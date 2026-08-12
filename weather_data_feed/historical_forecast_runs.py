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

SYNOPTIC_HOURLY_VARIABLES: tuple[str, ...] = (
    "temperature_2m",
    "dew_point_2m",
    "surface_pressure",
    "boundary_layer_height",
    "wind_speed_10m",
    "wind_direction_10m",
    "temperature_925hPa",
    "relative_humidity_925hPa",
    "wind_speed_925hPa",
    "wind_direction_925hPa",
    "geopotential_height_925hPa",
    "temperature_850hPa",
    "relative_humidity_850hPa",
    "wind_speed_850hPa",
    "wind_direction_850hPa",
    "geopotential_height_850hPa",
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
    source_fetch_start: datetime | None = None
    source_fetch_end: datetime | None = None
    if cache_hit:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        source_fetch_end = datetime.fromtimestamp(
            cache_path.stat().st_mtime, timezone.utc
        )
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
                source_fetch_start = datetime.now(timezone.utc)
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
                source_fetch_end = datetime.now(timezone.utc)
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
        "source_fetch_start_utc": (
            source_fetch_start.isoformat() if source_fetch_start else None
        ),
        "source_fetch_end_utc": source_fetch_end.isoformat() if source_fetch_end else None,
        "source_fetch_clock_status": (
            "response_complete"
            if not cache_hit and source_fetch_start and source_fetch_end
            else "cache_file_mtime_not_response_complete"
        ),
    }


def fetch_single_run_synoptic_batch(
    locations: list[dict[str, Any]],
    *,
    run: str,
    model: str,
    forecast_hours: int = 24,
    cache_dir: Path | None = None,
    timeout_sec: float = 90.0,
    max_attempts: int = 6,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch PIT-reconstructable multilevel fields for spatial advection.

    Locations may include several stencil points for the same city.  The
    response order is guaranteed to match the request order and is preserved
    in the immutable cache.
    """

    if not locations:
        return [], {"cache_hit": False, "request_key": "", "raw_hash": ""}
    if forecast_hours <= 0:
        raise ValueError("forecast_hours must be positive")
    request_key = stable_hash(
        {
            "purpose": "synoptic_advection_v1",
            "run": run,
            "locations": [
                {
                    "city": str(row["city"]),
                    "point": str(row.get("point") or "center"),
                    "latitude": float(row["latitude"]),
                    "longitude": float(row["longitude"]),
                }
                for row in locations
            ],
            "model": model,
            "forecast_hours": forecast_hours,
            "hourly": list(SYNOPTIC_HOURLY_VARIABLES),
        }
    )
    cache_path = (
        cache_dir
        / f"{run.replace(':', '')}_{model}_{request_key}.json"
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
            "models": model,
            "hourly": ",".join(SYNOPTIC_HOURLY_VARIABLES),
            "run": run,
            "forecast_hours": str(forecast_hours),
            "temperature_unit": "celsius",
            "wind_speed_unit": "ms",
            "timezone": "UTC",
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
                        "User-Agent": (
                            "pm-agent-weather-synoptic-backfill/1.0"
                        )
                    },
                )
                if (
                    "modelRunUnavailable" in response.text
                    or "requested model run is not available"
                    in response.text.lower()
                ):
                    raise ModelRunUnavailable(response.text.strip())
                response.raise_for_status()
                if not response.content.strip():
                    raise RuntimeError("empty HTTP 200 response")
                data = response.json()
                break
            except ModelRunUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt + 1 >= max_attempts:
                    raise RuntimeError(
                        "synoptic single-run fetch failed "
                        f"run={run} model={model}: {exc}"
                    ) from exc
                retry_after = 0.0
                if (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code == 429
                ):
                    try:
                        retry_after = float(
                            exc.response.headers.get("Retry-After") or 0.0
                        )
                    except ValueError:
                        retry_after = 0.0
                time.sleep(
                    max(retry_after, 5.0 * (2**attempt))
                    if retry_after or (
                        isinstance(exc, httpx.HTTPStatusError)
                        and exc.response.status_code == 429
                    )
                    else 2**attempt
                )
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
            f"synoptic response count mismatch: {len(responses)} "
            f"!= {len(locations)}"
        )
    return responses, {
        "cache_hit": cache_hit,
        "request_key": request_key,
        "raw_hash": stable_hash(data),
        "run": run,
        "model": model,
        "forecast_hours": forecast_hours,
        "hourly": list(SYNOPTIC_HOURLY_VARIABLES),
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
