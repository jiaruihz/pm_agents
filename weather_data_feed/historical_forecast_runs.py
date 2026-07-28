"""Historical Open-Meteo single-run reconstruction primitives.

The Single Runs API exposes a model initialization, not its public first-seen
timestamp.  Historical research therefore selects a deliberately older run
before calling this module and records that reconstruction rule explicitly.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx

from weather_data_feed.forecast_sources import (
    OPEN_METEO_SINGLE_RUN_API,
    stable_hash,
)


DEFAULT_GLOBAL_SINGLE_RUN_MODELS: tuple[str, ...] = (
    "ecmwf_ifs025",
    "ecmwf_aifs025_single",
    "gfs_global",
    "icon_seamless",
    "jma_seamless",
)


class ModelRunUnavailable(RuntimeError):
    """The archive does not contain the exact requested initialization."""


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def conservative_available_run(
    decision_time_utc: str,
    *,
    availability_lag_hours: int = 12,
    cycle_hours: int = 6,
) -> str:
    """Choose a common run conservatively older than the decision timestamp."""

    if availability_lag_hours < 0 or cycle_hours <= 0 or 24 % cycle_hours:
        raise ValueError("invalid availability lag or cycle")
    candidate = parse_utc(decision_time_utc) - timedelta(
        hours=availability_lag_hours
    )
    floored_hour = candidate.hour - candidate.hour % cycle_hours
    run = candidate.replace(
        hour=floored_hour, minute=0, second=0, microsecond=0
    )
    return run.strftime("%Y-%m-%dT%H:00")


def _request_key(
    *,
    run: str,
    locations: Iterable[dict[str, Any]],
    models: Iterable[str],
    forecast_days: int,
) -> str:
    return stable_hash(
        {
            "run": run,
            "locations": [
                {
                    "city": str(row["city"]),
                    "latitude": float(row["latitude"]),
                    "longitude": float(row["longitude"]),
                }
                for row in locations
            ],
            "models": list(models),
            "forecast_days": forecast_days,
        }
    )


def fetch_single_run_batch(
    locations: list[dict[str, Any]],
    *,
    run: str,
    models: tuple[str, ...] = DEFAULT_GLOBAL_SINGLE_RUN_MODELS,
    forecast_days: int = 4,
    cache_dir: Path | None = None,
    timeout_sec: float = 45.0,
    max_attempts: int = 6,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch one run for multiple coordinates, with immutable raw caching."""

    if not locations:
        return [], {"cache_hit": False, "request_key": "", "raw_hash": ""}
    request_key = _request_key(
        run=run,
        locations=locations,
        models=models,
        forecast_days=forecast_days,
    )
    cache_path = (
        cache_dir / f"{run.replace(':', '')}_{request_key}.json"
        if cache_dir is not None
        else None
    )
    cache_hit = bool(cache_path and cache_path.exists())
    if cache_hit:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        params = {
            "latitude": ",".join(
                f"{float(row['latitude']):.6f}" for row in locations
            ),
            "longitude": ",".join(
                f"{float(row['longitude']):.6f}" for row in locations
            ),
            "models": ",".join(models),
            "hourly": "temperature_2m",
            "run": run,
            "forecast_days": str(forecast_days),
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
        }
        last_error: Exception | None = None
        for attempt in range(max_attempts):
            try:
                response = httpx.get(
                    OPEN_METEO_SINGLE_RUN_API,
                    params=params,
                    timeout=timeout_sec,
                    trust_env=False,
                    headers={
                        "User-Agent": "pm-agent-weather-single-run-backfill/1.0"
                    },
                )
                response.raise_for_status()
                if not response.content.strip():
                    raise RuntimeError("empty HTTP 200 response")
                if "modelRunUnavailable" in response.text:
                    raise ModelRunUnavailable(response.text.strip())
                data = response.json()
                break
            except ModelRunUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt + 1 >= max_attempts:
                    raise RuntimeError(
                        f"single-run fetch failed run={run}: {exc}"
                    ) from exc
                time.sleep(2**attempt)
        else:  # pragma: no cover
            raise RuntimeError(str(last_error))
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )

    responses = data if isinstance(data, list) else [data]
    if len(responses) != len(locations):
        raise RuntimeError(
            f"single-run response count mismatch: {len(responses)} "
            f"!= {len(locations)}"
        )
    return responses, {
        "cache_hit": cache_hit,
        "request_key": request_key,
        "raw_hash": stable_hash(data),
        "run": run,
        "models": list(models),
    }


def daily_max_rows(
    payload: dict[str, Any],
    *,
    city: str,
    target_date: str,
    run: str,
    decision_time_utc: str,
    models: tuple[str, ...] = DEFAULT_GLOBAL_SINGLE_RUN_MODELS,
) -> list[dict[str, Any]]:
    """Normalize model-specific target-local-day maxima from one response."""

    hourly = payload.get("hourly") or {}
    times = [str(value) for value in hourly.get("time") or []]
    target_indices = [
        index for index, value in enumerate(times) if value[:10] == target_date
    ]
    rows: list[dict[str, Any]] = []
    for model in models:
        values = hourly.get(f"temperature_2m_{model}") or []
        valid = [
            float(values[index])
            for index in target_indices
            if index < len(values) and values[index] is not None
        ]
        if not valid:
            continue
        rows.append(
            {
                "city": city,
                "target_date": target_date,
                "decision_time_utc": decision_time_utc,
                "requested_run_utc": f"{run}:00Z",
                "model_key": model,
                "forecast_max_f": max(valid),
                "hour_count": len(valid),
                "timezone": payload.get("timezone"),
                "latitude": payload.get("latitude"),
                "longitude": payload.get("longitude"),
                "lineage_status": (
                    "single_run_reconstructed_conservative_12h_lag"
                ),
            }
        )
    return rows
