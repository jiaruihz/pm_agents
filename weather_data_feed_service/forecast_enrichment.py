"""Forecast enrichment producer for shadow/research feature capture."""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean, median, quantiles
from typing import Any
from zoneinfo import ZoneInfo

from weather_data_feed import city_local_date, load_city_configs, parse_now_utc
from weather_data_feed.assigned_forecast_models import assigned_model_family
from weather_data_feed.information_events import build_information_event
from weather_data_feed.historical_forecast_runs import (
    DEFAULT_GLOBAL_SINGLE_RUN_MODELS,
    conservative_available_run,
    daily_max_rows,
    fetch_single_run_batch,
)
from weather_data_feed.models import CityConfig
from weather_data_feed.forecast_sources import (
    ForecastFetchResult,
    ForecastFetchSettings,
    build_taf_signal,
    build_vertical_profile_signal,
    fetch_aviationweather_taf,
    fetch_open_meteo_multi_model,
    fetch_open_meteo_weather_context,
    stable_hash,
    target_day_hourly_summary,
)
from weather_data_feed.forecast_run_contract import (
    EXACT_SINGLE_RUN_ENDPOINT,
    build_forecast_row,
    run_lineage_evidence,
    stable_content_hash,
    summarize_forecast_batch,
)
from weather_data_feed.source_lineage import (
    build_source_capture_lineage,
    capture_batch_id,
    parse_utc,
    producer_build_id,
    utc_text,
)
from weather_data_feed_service.cli import DEFAULT_RUNTIME_ROOT
from weather_data_feed_service.legacy_weather_predict.city_pools import FULL_CITY_CONFIGS
from weather_data_feed_service.io_utils import (
    append_jsonl,
    read_json,
    write_json,
    write_latest_and_daily_jsonl,
)


DEFAULT_OUTPUT_DIR = DEFAULT_RUNTIME_ROOT / "output" / "forecast_enrichment"
DEFAULT_OPEN_METEO_REFRESH_SEC = int(
    os.environ.get("WEATHER_DATA_FEED_FORECAST_ENRICHMENT_OPEN_METEO_REFRESH_SEC", "21600")
)


def _parse_cache_utc(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _taf_valid_time(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        return str(value)


def _annotate_taf_information_events(rows: list[dict[str, Any]], output_dir: Path) -> list[dict[str, Any]]:
    state_path = output_dir / "taf_information_event_state.json"
    state = read_json(state_path, {})
    first_seen = dict(state.get("first_seen_by_id") or {})
    latest_by_content = dict(state.get("latest_by_content") or {})
    available = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    out: list[dict[str, Any]] = []
    for raw in rows:
        row, taf = dict(raw), dict(raw.get("taf") or {})
        result, source_payload = dict(taf.get("result") or {}), dict(taf.get("payload") or {})
        if result.get("status") != "ok" or not source_payload.get("raw_taf"):
            taf["information_event_status"] = "not_material_missing_or_failed_taf"
        else:
            issued = source_payload.get("issue_time") or source_payload.get("issue_time_utc")
            valid_from = source_payload.get("valid_time_from")
            valid_to = source_payload.get("valid_time_to")
            # A bulletin correction may keep the same issue time, or issue a
            # nearby AMD/COR timestamp while retaining the validity window.
            # Group revisions by station and validity, not by the raw payload.
            content_key = "|".join(
                str(v or "")
                for v in (row.get("city"), row.get("station"), valid_from, valid_to)
            )
            detected = str(result.get("fetched_at_utc") or available)
            common = dict(
                event_kind="taf",
                source="aviationweather_taf",
                city=str(row.get("city") or ""),
                station_id=str(row.get("station") or "") or None,
                provider_item_id=str(issued or "") or None,
                content_key=content_key,
                normalized_payload={"raw_taf": source_payload["raw_taf"]},
                issued_at_utc=issued,
                valid_from_utc=_taf_valid_time(valid_from),
                valid_to_utc=_taf_valid_time(valid_to),
                detected_at_utc=detected,
                available_at_utc=available,
                pit_lineage_class="collector_exact",
                raw_source_path=str(output_dir / "forecast_enrichment.jsonl"),
            )
            provisional = build_information_event(
                **common,
                event_role="new_content",
                first_seen_at_utc=detected,
            )
            event_id = str(provisional["information_event_id"])
            previous_event_id = latest_by_content.get(content_key)
            event_role = "revision" if previous_event_id and previous_event_id != event_id else "new_content"
            taf["information_event"] = build_information_event(
                **common,
                event_role=event_role,
                revision_of_event_id=previous_event_id if event_role == "revision" else None,
                first_seen_at_utc=first_seen.setdefault(event_id, detected),
            )
            taf["information_event_status"] = "material"
            latest_by_content[content_key] = event_id
        row["taf"] = taf
        out.append(row)
    write_json(
        state_path,
        {
            "first_seen_by_id": first_seen,
            "latest_by_content": latest_by_content,
        },
    )
    return out


def city_coordinates(city: str) -> dict[str, Any]:
    cfg = FULL_CITY_CONFIGS.get(city) or {}
    return {
        "lat": cfg.get("lat"),
        "lon": cfg.get("lon"),
        "legacy_icao": cfg.get("icao", ""),
        "region": cfg.get("region", ""),
        "slug": cfg.get("slug", ""),
    }


def _compact_result(result: ForecastFetchResult) -> dict[str, Any]:
    return {
        "source_key": result.source_key,
        "status": result.status,
        "fetched_at_utc": result.fetched_at_utc,
        "latency_ms": result.latency_ms,
        "error": result.error,
        "metadata": result.metadata,
    }


def _failed_result(source_key: str, exc: BaseException) -> ForecastFetchResult:
    now = datetime.now(timezone.utc).isoformat()
    return ForecastFetchResult(
        source_key=source_key,
        status="fetch_failed",
        fetched_at_utc=now,
        latency_ms=0.0,
        error=f"{type(exc).__name__}: {exc}",
    )


def _reusable_open_meteo_payload(
    previous: dict[str, Any] | None,
    *,
    city: str,
    target_date: str,
    now_utc: datetime,
    max_age_sec: int,
) -> dict[str, Any] | None:
    if not isinstance(previous, dict):
        return None
    if previous.get("city") != city or previous.get("target_date") != target_date:
        return None
    blocks: dict[str, dict[str, Any]] = {}
    ages: list[float] = []
    for key in ("open_meteo_multi_model", "open_meteo_weather_context"):
        block = previous.get(key)
        result = block.get("result") if isinstance(block, dict) else None
        fetched = _parse_cache_utc(result.get("fetched_at_utc")) if isinstance(result, dict) else None
        if not isinstance(block, dict) or not isinstance(result, dict) or result.get("status") != "ok" or fetched is None:
            return None
        age = (now_utc - fetched).total_seconds()
        if age < 0 or age > max_age_sec:
            return None
        blocks[key] = dict(block)
        ages.append(age)
    return {
        **blocks,
        "max_age_sec": round(max(ages), 3),
        "refresh_contract_sec": int(max_age_sec),
    }


def load_reusable_open_meteo_rows(
    output_dir: Path,
    *,
    now_utc: datetime,
    max_age_sec: int,
    tail_rows: int = 5000,
) -> dict[str, dict[str, Any]]:
    """Recover the latest successful Open-Meteo evidence per city.

    ``latest.json`` may itself be a 429/fetch-failed cycle. The append-only
    journal is therefore the durable cache owner; falling back only to latest
    would keep hammering the provider after the first rate-limit response.
    """

    candidates: list[dict[str, Any]] = []
    latest = read_json(output_dir / "latest.json", {})
    candidates.extend(
        row for row in latest.get("records", []) if isinstance(row, dict)
    )
    journal = output_dir / "forecast_enrichment.jsonl"
    if journal.exists():
        try:
            lines = journal.read_text(encoding="utf-8").splitlines()[-max(1, tail_rows) :]
        except OSError:
            lines = []
        for line in reversed(lines):
            try:
                row = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(row, dict):
                candidates.append(row)

    by_city: dict[str, dict[str, Any]] = {}
    for row in candidates:
        city = str(row.get("city") or "")
        target_date = str(row.get("target_date") or "")
        if not city or city in by_city or not target_date:
            continue
        if _reusable_open_meteo_payload(
            row,
            city=city,
            target_date=target_date,
            now_utc=now_utc,
            max_age_sec=max_age_sec,
        ) is not None:
            by_city[city] = row
    return by_city


def _compact_multi_model_payload(
    result: ForecastFetchResult, target_date: str
) -> dict[str, Any]:
    payload = result.payload if isinstance(result.payload, dict) else {}
    daily = payload.get("daily") or {}
    return {
        "result": _compact_result(result),
        "target_date": daily.get(target_date) or {},
        # Keep every requested date. D-1 research must recover exactly what was
        # visible before the target day, without refetching revised history.
        "daily": daily,
        "daily_dates": payload.get("daily_dates", []),
        "model_metadata": payload.get("model_metadata", {}),
        "hourly_values_hash_by_model": payload.get(
            "hourly_values_hash_by_model", {}
        ),
    }


def multi_model_forecast_versions(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten one capture into immutable city-target-model version rows."""
    multi = row.get("open_meteo_multi_model")
    if not isinstance(multi, dict):
        return []
    result = multi.get("result")
    if not isinstance(result, dict) or result.get("status") != "ok":
        return []
    daily = multi.get("daily")
    if not isinstance(daily, dict):
        return []
    metadata = (
        multi.get("model_metadata")
        if isinstance(multi.get("model_metadata"), dict)
        else {}
    )
    result_metadata = (
        result.get("metadata")
        if isinstance(result.get("metadata"), dict)
        else {}
    )
    captured_at = str(row.get("snapshot_ts_utc") or "")
    available_at = str(
        result_metadata.get("source_fetch_end_utc")
        or result.get("fetched_at_utc")
        or captured_at
    )
    local_date_text = str(row.get("target_date") or "")
    try:
        local_date = date.fromisoformat(local_date_text)
    except ValueError:
        local_date = None
    versions: list[dict[str, Any]] = []
    for forecast_target_date, target_payload in sorted(daily.items()):
        if not isinstance(target_payload, dict):
            continue
        models = target_payload.get("models")
        if not isinstance(models, dict):
            continue
        try:
            target_day = date.fromisoformat(str(forecast_target_date))
        except ValueError:
            target_day = None
        horizon_days = (
            (target_day - local_date).days
            if target_day is not None and local_date is not None
            else None
        )
        for model_label, raw_value in sorted(models.items()):
            try:
                forecast_max_f = float(raw_value)
            except (TypeError, ValueError):
                continue
            model_meta = (
                metadata.get(model_label)
                if isinstance(metadata.get(model_label), dict)
                else {}
            )
            version_hash = stable_hash(
                {
                    "city": row.get("city"),
                    "forecast_target_date": forecast_target_date,
                    "model_label": model_label,
                    "forecast_max_f": forecast_max_f,
                }
            )
            versions.append(
                {
                    "schema_version": "weather_forecast_model_version_v1",
                    "producer": "weather_data_feed_service.forecast_enrichment",
                    "capture_id": stable_hash(
                        {
                            "city": row.get("city"),
                            "captured_at_utc": captured_at,
                            "available_at_utc": available_at,
                            "forecast_target_date": forecast_target_date,
                            "model_label": model_label,
                        }
                    ),
                    "forecast_version_hash": version_hash,
                    "city": row.get("city"),
                    "station": row.get("station"),
                    "timezone_name": row.get("timezone_name"),
                    "market_unit": row.get("unit"),
                    "forecast_target_date": str(forecast_target_date),
                    "forecast_horizon_days_local": horizon_days,
                    "model_label": str(model_label),
                    "model_key": model_meta.get("open_meteo_model"),
                    "provider": model_meta.get("provider"),
                    "tier": model_meta.get("tier"),
                    "resolution_km": model_meta.get("resolution_km"),
                    "forecast_max_f": forecast_max_f,
                    "captured_at_utc": captured_at,
                    "available_at_utc": available_at,
                    "source_fetch_start_utc": result_metadata.get(
                        "source_fetch_start_utc"
                    ),
                    "source_fetch_end_utc": result_metadata.get(
                        "source_fetch_end_utc"
                    ),
                    "source_raw_payload_hash": result_metadata.get(
                        "raw_payload_hash"
                    ),
                    "forecast_run_at_utc": None,
                    "forecast_run_lineage_status": (
                        "provider_run_unavailable_collector_versioned"
                    ),
                }
            )
    return versions


def materialize_run_contract_capture(
    versions: list[dict[str, Any]],
    previous_state: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Materialize strict rows for both generic and exact Single Runs captures."""
    state = dict(previous_state or {})
    first_seen_by_content = dict(state.get("first_seen_by_content") or {})
    latest_run_by_sequence = dict(state.get("latest_run_by_sequence") or {})
    latest_content_by_run = dict(state.get("latest_content_by_run") or {})
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for version in versions:
        batch_capture_id = str(version.get("batch_capture_id") or stable_content_hash({
            "producer": "weather_data_feed_service.forecast_enrichment",
            "captured_at_utc": version.get("captured_at_utc"),
        }))
        groups.setdefault(
            (
                batch_capture_id,
                str(version.get("city") or ""),
                str(version.get("forecast_target_date") or ""),
                str(version.get("forecast_run_at_utc") or "run_unknown"),
            ),
            [],
        ).append(version)

    rows: list[dict[str, Any]] = []
    batches: list[dict[str, Any]] = []
    for (batch_capture_id, _, _, run_identity), group in sorted(groups.items()):
        expected_model_keys = sorted(
            {
                str(row.get("model_key") or row.get("model_label") or "")
                for row in group
                if row.get("model_key") or row.get("model_label")
            }
        )
        contract_rows: list[dict[str, Any]] = []
        for version in group:
            model_key = str(version.get("model_key") or version.get("model_label") or "")
            available_at = str(version.get("available_at_utc") or version.get("captured_at_utc") or "")
            content_identity = stable_content_hash(
                {
                    "model_key": model_key,
                    "city": version.get("city"),
                    "target_date": version.get("forecast_target_date"),
                    "forecast_max_f": version.get("forecast_max_f"),
                    "raw_payload_hash": version.get("source_raw_payload_hash"),
                }
            )
            first_seen = str(first_seen_by_content.setdefault(content_identity, available_at))
            evidence = run_lineage_evidence(
                request_run_at_utc=version.get("forecast_run_at_utc"),
                request_endpoint=version.get("single_run_request_endpoint"),
                request_succeeded=bool(version.get("single_run_request_succeeded")),
                fallback_applied=bool(version.get("single_run_fallback_applied")),
                raw_payload_hash=str(version.get("source_raw_payload_hash") or "") or None,
                request_hash=str(version.get("single_run_request_hash") or "") or None,
            )
            sequence_key = "|".join(
                [str(version.get("city") or ""), str(version.get("forecast_target_date") or ""), model_key]
            )
            content_run_key = f"{sequence_key}|{run_identity}"
            previous_run = latest_run_by_sequence.get(sequence_key)
            previous_content = latest_content_by_run.get(content_run_key)
            contract_rows.append(
                build_forecast_row(
                    model_key=model_key,
                    city=str(version.get("city") or ""),
                    target_date=str(version.get("forecast_target_date") or ""),
                    forecast_max_f=float(version["forecast_max_f"]),
                    source_fetched_at_utc=available_at,
                    detected_at_utc=available_at,
                    first_seen_at_utc=first_seen,
                    available_at_utc=available_at,
                    raw_payload_hash=str(version.get("source_raw_payload_hash") or version.get("forecast_version_hash") or ""),
                    producer_build_identity=str(version.get("producer_build_id") or "unavailable"),
                    capture_id=str(version.get("capture_id") or ""),
                    batch_capture_id=batch_capture_id,
                    horizon_days_local=(
                        int(version["forecast_horizon_days_local"])
                        if version.get("forecast_horizon_days_local") is not None
                        else None
                    ),
                    forecast_run_at_utc=evidence["forecast_run_at_utc"],
                    forecast_run_evidence=evidence["forecast_run_evidence"],
                    forecast_run_lineage_status=evidence["forecast_run_lineage_status"],
                    lineage_blocker=evidence["blocker"],
                    assigned_model=(
                        str(version.get("assigned_model_family") or "")
                        and str(version.get("assigned_model_family") or "") in model_key.lower()
                    ),
                    previous_run_ts=(previous_run or {}).get("forecast_run_at_utc"),
                    previous_run_forecast_max_f=(previous_run or {}).get("forecast_max_f"),
                    previous_content_hash=(previous_content or {}).get("content_hash"),
                    previous_content_forecast_max_f=(previous_content or {}).get("forecast_max_f"),
                    revision_of_content_id=(previous_content or {}).get("capture_id"),
                )
            )
            built = contract_rows[-1]
            latest_content_by_run[content_run_key] = {
                "content_hash": built["content_hash"],
                "forecast_max_f": built["forecast_max_f"],
                "capture_id": built["capture_id"],
            }
            if built.get("forecast_run_at_utc"):
                current_run = parse_utc(built["forecast_run_at_utc"])
                prior_run = parse_utc((previous_run or {}).get("forecast_run_at_utc"))
                if prior_run is None or (current_run is not None and current_run >= prior_run):
                    latest_run_by_sequence[sequence_key] = {
                        "forecast_run_at_utc": built["forecast_run_at_utc"],
                        "forecast_max_f": built["forecast_max_f"],
                        "capture_id": built["capture_id"],
                    }
        rows.extend(contract_rows)
        batches.append(
            summarize_forecast_batch(
                contract_rows,
                expected_model_keys=expected_model_keys,
            )
        )
    return rows, batches, {
        "schema_version": "weather_forecast_run_contract_state_v2",
        "first_seen_by_content": first_seen_by_content,
        "latest_run_by_sequence": latest_run_by_sequence,
        "latest_content_by_run": latest_content_by_run,
    }


def run_aware_forecast_versions(
    *,
    city_rows: list[dict[str, Any]],
    responses: list[dict[str, Any]],
    run: str,
    models: tuple[str, ...],
    forecast_days: int,
    captured_at_utc: str,
    fetch_metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    """Normalize an explicitly requested Single Runs response.

    The requested and returned initialization is a real model-run identity.
    Its collector first-seen clock remains separate from that provider clock.
    """
    if len(city_rows) != len(responses):
        raise ValueError("single-run city/response count mismatch")
    run_at = parse_utc(run)
    if run_at is None:
        raise ValueError("single-run run must be ISO-8601")
    run_at_utc = utc_text(run_at)
    detected_at = str(fetch_metadata.get("source_fetch_end_utc") or captured_at_utc)
    raw_hash = str(fetch_metadata.get("raw_hash") or "")
    out: list[dict[str, Any]] = []
    for city_row, response in zip(city_rows, responses):
        local_date = date.fromisoformat(str(city_row["target_date"]))
        for offset in range(max(1, forecast_days)):
            forecast_target_date = (local_date.fromordinal(local_date.toordinal() + offset)).isoformat()
            maxima = daily_max_rows(
                response,
                city=str(city_row["city"]),
                target_date=forecast_target_date,
                run=run,
                decision_time_utc=captured_at_utc,
                models=models,
            )
            for maximum in maxima:
                model_key = str(maximum["model_key"])
                value = float(maximum["forecast_max_f"])
                version_hash = stable_hash(
                    {
                        "city": city_row["city"],
                        "forecast_target_date": forecast_target_date,
                        "model_key": model_key,
                        "forecast_run_at_utc": run_at_utc,
                        "forecast_max_f": value,
                    }
                )
                capture_id = stable_hash(
                    {
                        "city": city_row["city"],
                        "forecast_target_date": forecast_target_date,
                        "model_key": model_key,
                        "forecast_run_at_utc": run_at_utc,
                        "captured_at_utc": captured_at_utc,
                        "raw_payload_hash": raw_hash,
                    }
                )
                out.append(
                    {
                        "schema_version": "weather_forecast_model_version_v2",
                        "producer": "weather_data_feed_service.forecast_enrichment.single_runs",
                        "capture_id": capture_id,
                        "forecast_version_hash": version_hash,
                        "city": city_row["city"],
                        "station": city_row.get("station"),
                        "timezone_name": city_row.get("timezone_name"),
                        "market_unit": city_row.get("unit"),
                        "forecast_target_date": forecast_target_date,
                        "forecast_horizon_days_local": offset,
                        "model_label": model_key,
                        "model_key": model_key,
                        "assigned_model_family": city_row.get("assigned_model_family"),
                        "forecast_max_f": value,
                        "forecast_hour_count": maximum.get("hour_count"),
                        "captured_at_utc": captured_at_utc,
                        "available_at_utc": detected_at,
                        "source_fetch_start_utc": fetch_metadata.get("source_fetch_start_utc"),
                        "source_fetch_end_utc": fetch_metadata.get("source_fetch_end_utc"),
                        "source_raw_payload_hash": raw_hash,
                        "forecast_run_at_utc": run_at_utc,
                        "forecast_run_lineage_status": "explicit_single_run_requested_and_returned",
                        "source_delivery_class": fetch_metadata.get("source_delivery_class"),
                        "single_run_request_endpoint": EXACT_SINGLE_RUN_ENDPOINT,
                        "single_run_request_hash": fetch_metadata.get("request_key"),
                        "single_run_request_succeeded": True,
                        "single_run_fallback_applied": False,
                    }
                )
    return out


def _version_sequence_key(row: dict[str, Any]) -> str:
    return "|".join(
        str(row.get(field) or "")
        for field in ("city", "forecast_target_date", "model_key")
    )


def enrich_forecast_version_lineage(
    versions: list[dict[str, Any]],
    *,
    state: dict[str, Any],
    batch_id: str,
    producer_build: str | None,
    ingested_at_utc: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Persist first-seen, content revision, and provider-run revision separately."""
    first_seen = dict(state.get("first_seen_by_identity") or {})
    latest_content = dict(state.get("latest_content_by_sequence") or {})
    latest_run = dict(state.get("latest_provider_run_by_sequence") or {})
    out: list[dict[str, Any]] = []
    for source in sorted(
        versions,
        key=lambda row: (
            str(row.get("city") or ""),
            str(row.get("forecast_target_date") or ""),
            str(row.get("model_key") or ""),
            str(row.get("forecast_run_at_utc") or ""),
        ),
    ):
        row = dict(source)
        sequence_key = _version_sequence_key(row)
        run_at = str(row.get("forecast_run_at_utc") or "")
        content_sequence_key = f"{sequence_key}|{run_at or 'run_unknown'}"
        identity_key = "|".join(
            [sequence_key, run_at or "run_unknown", str(row.get("forecast_version_hash") or "")]
        )
        detected_at = str(row.get("source_fetch_end_utc") or row.get("available_at_utc") or ingested_at_utc)
        observed_first_seen = str(first_seen.setdefault(identity_key, detected_at))
        row["forecast_first_seen_at_utc"] = observed_first_seen

        previous_content = latest_content.get(content_sequence_key)
        row["previous_content_version_hash"] = None
        row["previous_content_capture_id"] = None
        row["content_revision_f"] = None
        if previous_content and previous_content.get("forecast_version_hash") != row.get("forecast_version_hash"):
            row["previous_content_version_hash"] = previous_content.get("forecast_version_hash")
            row["previous_content_capture_id"] = previous_content.get("capture_id")
            try:
                row["content_revision_f"] = round(
                    float(row["forecast_max_f"]) - float(previous_content["forecast_max_f"]), 6
                )
            except (KeyError, TypeError, ValueError):
                pass
        latest_content[content_sequence_key] = {
            "forecast_version_hash": row.get("forecast_version_hash"),
            "capture_id": row.get("capture_id"),
            "forecast_max_f": row.get("forecast_max_f"),
        }

        row["previous_run_ts"] = None
        row["previous_run_forecast_max_f"] = None
        row["run_to_run_revision_f"] = None
        if run_at:
            previous_run = latest_run.get(sequence_key)
            current_run_dt = parse_utc(run_at)
            previous_run_dt = parse_utc((previous_run or {}).get("forecast_run_at_utc"))
            if previous_run and previous_run_dt and current_run_dt and previous_run_dt < current_run_dt:
                row["previous_run_ts"] = previous_run.get("forecast_run_at_utc")
                row["previous_run_forecast_max_f"] = previous_run.get("forecast_max_f")
                row["run_to_run_revision_f"] = round(
                    float(row["forecast_max_f"]) - float(previous_run["forecast_max_f"]), 6
                )
            if not previous_run_dt or (current_run_dt and current_run_dt >= previous_run_dt):
                latest_run[sequence_key] = {
                    "forecast_run_at_utc": run_at,
                    "forecast_max_f": row.get("forecast_max_f"),
                    "capture_id": row.get("capture_id"),
                }

        lineage = build_source_capture_lineage(
            producer=str(row.get("producer") or "weather_data_feed_service.forecast_enrichment"),
            producer_build=producer_build,
            capture_id=str(row["capture_id"]),
            batch_capture_id=batch_id,
            raw_payload_hash=str(row.get("source_raw_payload_hash") or "") or None,
            source_event_ts_utc=run_at or None,
            source_fetch_start_utc=row.get("source_fetch_start_utc"),
            source_fetch_end_utc=row.get("source_fetch_end_utc"),
            detected_at_utc=detected_at,
            first_seen_at_utc=observed_first_seen,
            available_at_utc=str(row.get("available_at_utc") or detected_at),
            ingested_at_utc=ingested_at_utc,
            lineage_status=str(row.get("forecast_run_lineage_status") or "unknown"),
        )
        row.update(lineage)
        if run_at:
            target_day = datetime.fromisoformat(str(row["forecast_target_date"])).replace(
                tzinfo=ZoneInfo(str(row.get("timezone_name") or "UTC"))
            ).astimezone(timezone.utc)
            run_dt = parse_utc(run_at)
            captured_dt = parse_utc(row["captured_at_utc"])
            assert run_dt is not None and captured_dt is not None
            row["forecast_target_lead_hours"] = round((target_day - run_dt).total_seconds() / 3600.0, 6)
            row["forecast_target_lead_basis"] = "provider_run_to_target_local_day_start"
            row["forecast_run_age_hours_at_capture"] = round((captured_dt - run_dt).total_seconds() / 3600.0, 6)
        else:
            row["forecast_target_lead_hours"] = None
            row["forecast_target_lead_basis"] = None
            row["forecast_run_age_hours_at_capture"] = None
        out.append(row)
    new_state = {
        "schema_version": "weather_forecast_version_lineage_state_v1",
        "first_seen_by_identity": first_seen,
        "latest_content_by_sequence": latest_content,
        "latest_provider_run_by_sequence": latest_run,
    }
    return out, new_state


def forecast_batch_summaries(
    versions: list[dict[str, Any]],
    *,
    batch_id: str,
    expected_model_keys: set[str],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in versions:
        groups.setdefault(
            (
                str(row.get("city") or ""),
                str(row.get("forecast_target_date") or ""),
                str(row.get("forecast_run_at_utc") or "run_unknown_live_endpoint"),
            ),
            [],
        ).append(row)
    summaries: list[dict[str, Any]] = []
    for (city, target_date, run_at), rows in sorted(groups.items()):
        values_by_model = {
            str(row.get("model_key") or row.get("model_label") or ""): float(row["forecast_max_f"])
            for row in rows
            if row.get("forecast_max_f") is not None
        }
        values = sorted(values_by_model.values())
        if not values:
            continue
        if len(values) >= 2:
            q25, _, q75 = quantiles(values, n=4, method="inclusive")
        else:
            q25 = q75 = values[0]
        assigned_family = str(rows[0].get("assigned_model_family") or "")
        assigned_candidates = [
            (key, value)
            for key, value in values_by_model.items()
            if assigned_family and assigned_family in key.lower()
        ]
        assigned_key, assigned_value = assigned_candidates[0] if assigned_candidates else (None, None)
        center = mean(values)
        summaries.append(
            {
                "schema_version": "weather_forecast_batch_summary_v1",
                "batch_capture_id": batch_id,
                "city": city,
                "forecast_target_date": target_date,
                "forecast_run_at_utc": None if run_at == "run_unknown_live_endpoint" else run_at,
                "forecast_run_lineage_status": rows[0].get("forecast_run_lineage_status"),
                "captured_at_utc": rows[0].get("captured_at_utc"),
                "available_at_utc": max(str(row.get("available_at_utc") or "") for row in rows),
                "model_values_f": dict(sorted(values_by_model.items())),
                "model_keys_present": sorted(values_by_model),
                "model_keys_missing": sorted(expected_model_keys - set(values_by_model)),
                "model_count": len(values_by_model),
                "mean_f": round(center, 6),
                "median_f": round(median(values), 6),
                "q25_f": round(q25, 6),
                "q75_f": round(q75, 6),
                "min_f": min(values),
                "max_f": max(values),
                "spread_f": round(max(values) - min(values), 6),
                "iqr_f": round(q75 - q25, 6),
                "assigned_model_family": assigned_family or None,
                "assigned_model_key": assigned_key,
                "assigned_model_forecast_max_f": assigned_value,
                "assigned_minus_consensus_f": None if assigned_value is None else round(assigned_value - center, 6),
            }
        )
    return summaries


def fetch_city_forecast_enrichment(
    cfg: CityConfig,
    now_utc: datetime,
    *,
    settings: ForecastFetchSettings,
    forecast_days: int,
    include_taf: bool = True,
    previous: dict[str, Any] | None = None,
    open_meteo_refresh_sec: int = DEFAULT_OPEN_METEO_REFRESH_SEC,
) -> dict[str, Any]:
    target_date = city_local_date(cfg.city, now_utc).isoformat()
    coords = city_coordinates(cfg.city)
    lat = coords.get("lat")
    lon = coords.get("lon")
    base = {
        "schema_version": "weather_forecast_enrichment_v1",
        "producer": "weather_data_feed_service.forecast_enrichment",
        "city": cfg.city,
        "target_date": target_date,
        "snapshot_ts_utc": now_utc.isoformat(),
        "timezone_name": cfg.timezone_name,
        "unit": cfg.unit,
        "station": cfg.official_icao,
        "latitude": lat,
        "longitude": lon,
        "region": coords.get("region", ""),
        "slug": coords.get("slug", ""),
        "assigned_model_family": assigned_model_family(cfg.city),
    }
    if lat is None or lon is None:
        return {
            **base,
            "status": "missing_coordinates",
            "error": "city missing lat/lon in legacy city config",
            "payload_hash": stable_hash(base),
        }

    reusable = _reusable_open_meteo_payload(
        previous,
        city=cfg.city,
        target_date=target_date,
        now_utc=now_utc,
        max_age_sec=open_meteo_refresh_sec,
    )
    temperature_unit = "fahrenheit"
    if reusable is None:
        try:
            multi_model = fetch_open_meteo_multi_model(
                float(lat),
                float(lon),
                forecast_days=forecast_days,
                temperature_unit=temperature_unit,
                settings=settings,
            )
        except Exception as exc:  # noqa: BLE001
            multi_model = _failed_result("open_meteo_multi_model", exc)

        try:
            context = fetch_open_meteo_weather_context(
                float(lat),
                float(lon),
                forecast_days=forecast_days,
                temperature_unit=temperature_unit,
                settings=settings,
            )
        except Exception as exc:  # noqa: BLE001
            context = _failed_result("open_meteo_weather_context", exc)
        multi_model_payload = _compact_multi_model_payload(multi_model, target_date)
        context_payload = {
            "result": _compact_result(context),
            "timezone": context.payload.get("timezone") if isinstance(context.payload, dict) else None,
            "timezone_abbreviation": context.payload.get("timezone_abbreviation") if isinstance(context.payload, dict) else None,
            "utc_offset_seconds": context.payload.get("utc_offset_seconds") if isinstance(context.payload, dict) else None,
            "daily": context.payload.get("daily", {}) if isinstance(context.payload, dict) else {},
            "hourly": context.payload.get("hourly", {}) if isinstance(context.payload, dict) else {},
        }
    else:
        multi_model_payload = reusable["open_meteo_multi_model"]
        context_payload = reusable["open_meteo_weather_context"]

    multi_status = str((multi_model_payload.get("result") or {}).get("status") or "missing")
    context_status = str((context_payload.get("result") or {}).get("status") or "missing")
    context_hourly = context_payload.get("hourly") if isinstance(context_payload, dict) else {}
    hourly_summary = target_day_hourly_summary(context_hourly or {}, target_date)
    local_now = now_utc.astimezone(ZoneInfo(cfg.timezone_name))
    first_peak_hour = hourly_summary.get("first_peak_hour_local")
    last_peak_hour = hourly_summary.get("last_peak_hour_local")
    if first_peak_hour is None:
        first_peak_hour = local_now.hour
    if last_peak_hour is None:
        last_peak_hour = first_peak_hour

    vertical_signal = build_vertical_profile_signal(
        context_hourly or {},
        target_date=target_date,
        local_hour=local_now.hour,
        first_peak_hour=int(first_peak_hour),
        last_peak_hour=int(last_peak_hour),
    )

    taf_result: ForecastFetchResult | None = None
    taf_signal: dict[str, Any] = {"available": False, "status": "disabled"}
    if include_taf and cfg.official_icao:
        try:
            taf_result = fetch_aviationweather_taf(cfg.official_icao, settings=settings)
            taf_signal = build_taf_signal(
                taf_result.payload,
                target_date=target_date,
                utc_offset_seconds=int(
                    context_payload.get("utc_offset_seconds")
                    or local_now.utcoffset().total_seconds()
                ),
                first_peak_hour=int(first_peak_hour),
                last_peak_hour=int(last_peak_hour),
            )
        except Exception as exc:  # noqa: BLE001
            taf_result = _failed_result("aviationweather_taf", exc)
            taf_signal = {"available": False, "status": "fetch_failed", "error": taf_result.error}

    source_statuses = {
        "open_meteo_multi_model": multi_status,
        "open_meteo_weather_context": context_status,
        "aviationweather_taf": taf_result.status if taf_result else "disabled",
    }
    ok_sources = sum(1 for value in source_statuses.values() if value == "ok")
    status = "ok" if ok_sources >= 2 else ("partial" if ok_sources else "failed")
    row = {
        **base,
        "status": status,
        "source_statuses": source_statuses,
        "open_meteo_multi_model": multi_model_payload,
        "open_meteo_weather_context": {
            **context_payload,
            "target_day_hourly": hourly_summary,
        },
        "open_meteo_reuse": {
            "reused": reusable is not None,
            "max_age_sec": reusable.get("max_age_sec") if reusable else 0,
            "refresh_contract_sec": int(open_meteo_refresh_sec),
        },
        "vertical_profile_signal": vertical_signal,
        "taf": {
            "result": _compact_result(taf_result) if taf_result else None,
            "payload": taf_result.payload if taf_result else {},
            "signal": taf_signal,
        },
    }
    row["payload_hash"] = stable_hash(
        {
            "city": row["city"],
            "target_date": row["target_date"],
            "snapshot_ts_utc": row["snapshot_ts_utc"],
            "source_statuses": source_statuses,
            "multi_model": row["open_meteo_multi_model"],
            "hourly_context_hash": stable_hash(row["open_meteo_weather_context"].get("hourly", {})),
            "vertical_profile_signal": vertical_signal,
            "taf_signal": taf_signal,
        }
    )
    return row


def collect_run_aware_forecasts(
    rows: list[dict[str, Any]],
    *,
    now_utc: datetime,
    output_dir: Path,
    forecast_days: int,
    models: tuple[str, ...],
    availability_lag_hours: int,
    batch_size: int,
    timeout_sec: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Capture one explicit provider initialization across the city universe."""
    run = conservative_available_run(
        now_utc.isoformat(), availability_lag_hours=availability_lag_hours
    )
    eligible = [row for row in rows if row.get("latitude") is not None and row.get("longitude") is not None]
    versions: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    raw_hashes: list[str] = []
    for start in range(0, len(eligible), max(1, batch_size)):
        chunk = eligible[start : start + max(1, batch_size)]
        locations = [
            {"city": row["city"], "latitude": row["latitude"], "longitude": row["longitude"]}
            for row in chunk
        ]
        try:
            fetch_started_at = datetime.now(timezone.utc).isoformat()
            responses, metadata = fetch_single_run_batch(
                locations,
                run=run,
                models=models,
                forecast_days=forecast_days,
                cache_dir=output_dir / "single_run_raw",
                timeout_sec=max(10.0, timeout_sec),
            )
            metadata.setdefault("source_fetch_start_utc", fetch_started_at)
            metadata.setdefault("source_fetch_end_utc", datetime.now(timezone.utc).isoformat())
            metadata.setdefault(
                "source_delivery_class",
                "immutable_cache_replay" if metadata.get("cache_hit") else "live_single_run_api_fetch",
            )
            raw_hashes.append(str(metadata.get("raw_hash") or ""))
            versions.extend(
                run_aware_forecast_versions(
                    city_rows=chunk,
                    responses=responses,
                    run=run,
                    models=models,
                    forecast_days=forecast_days,
                    captured_at_utc=now_utc.isoformat(),
                    fetch_metadata=metadata,
                )
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(
                {
                    "cities": [str(row.get("city") or "") for row in chunk],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return versions, {
        "schema_version": "weather_forecast_single_run_capture_summary_v1",
        "status": "ok" if versions and not errors else ("partial" if versions else "failed"),
        "forecast_run_at_utc": utc_text(parse_utc(run)),
        "models_requested": list(models),
        "availability_lag_hours": availability_lag_hours,
        "city_count_requested": len(eligible),
        "version_rows": len(versions),
        "raw_payload_hashes": sorted(value for value in raw_hashes if value),
        "errors": errors,
    }


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    now_utc = parse_now_utc(args.now_utc) if args.now_utc else datetime.now(timezone.utc)
    configs = load_city_configs(
        include_station_diff=args.include_station_diff,
        only_cities=set(args.cities or []) or None,
    )
    settings = ForecastFetchSettings(
        timeout_sec=args.timeout_sec,
        proxy_candidates=(os.environ.get("WEATHER_DATA_FEED_WEATHER_PROXY") or None, None)
        if os.environ.get("WEATHER_DATA_FEED_WEATHER_PROXY")
        else (None,),
    )
    previous_by_city = load_reusable_open_meteo_rows(
        Path(args.output_dir),
        now_utc=now_utc,
        max_age_sec=args.open_meteo_refresh_sec,
    )
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.max_workers)) as executor:
        futures = {
            executor.submit(
                fetch_city_forecast_enrichment,
                cfg,
                now_utc,
                settings=settings,
                forecast_days=args.forecast_days,
                include_taf=not args.no_taf,
                previous=previous_by_city.get(cfg.city),
                open_meteo_refresh_sec=args.open_meteo_refresh_sec,
            ): cfg
            for cfg in configs
        }
        for future in as_completed(futures):
            rows.append(future.result())
    rows = sorted(rows, key=lambda row: str(row.get("city") or ""))
    run_aware_versions: list[dict[str, Any]] = []
    single_run_summary: dict[str, Any] = {
        "schema_version": "weather_forecast_single_run_capture_summary_v1",
        "status": "disabled",
    }
    if not args.no_single_runs:
        run_aware_versions, single_run_summary = collect_run_aware_forecasts(
            rows,
            now_utc=now_utc,
            output_dir=Path(args.output_dir),
            forecast_days=args.forecast_days,
            models=tuple(args.single_run_models),
            availability_lag_hours=args.single_run_availability_lag_hours,
            batch_size=args.single_run_batch_size,
            timeout_sec=args.single_run_timeout_sec,
        )
    summary = {
        "status": "ok",
        "schema_version": "weather_forecast_enrichment_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "producer": "weather_data_feed_service.forecast_enrichment",
        "rows": len(rows),
        "ok": sum(1 for row in rows if row.get("status") == "ok"),
        "partial": sum(1 for row in rows if row.get("status") == "partial"),
        "failed": sum(1 for row in rows if row.get("status") == "failed"),
        "missing_coordinates": sum(1 for row in rows if row.get("status") == "missing_coordinates"),
        "cities": len(configs),
        "forecast_days": args.forecast_days,
        "include_taf": not args.no_taf,
        "open_meteo_reused": sum(
            1 for row in rows if (row.get("open_meteo_reuse") or {}).get("reused")
        ),
        "open_meteo_refresh_sec": args.open_meteo_refresh_sec,
    }
    return {
        **summary,
        "records": rows,
        "run_aware_forecast_versions": run_aware_versions,
        "single_run_capture_summary": single_run_summary,
    }


def write_outputs(payload: dict[str, Any], output_dir: Path) -> None:
    rows = _annotate_taf_information_events(list(payload.get("records") or []), output_dir)
    write_latest_and_daily_jsonl(
        output_dir=output_dir,
        latest_payload=payload,
        rows=rows,
        jsonl_name="forecast_enrichment.jsonl",
    )
    endpoint_versions = [
        version
        for row in rows
        if isinstance(row, dict)
        for version in multi_model_forecast_versions(row)
    ]
    versions = endpoint_versions + [
        dict(row)
        for row in payload.get("run_aware_forecast_versions") or []
        if isinstance(row, dict)
    ]
    ingested_at = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    build_id, build_basis = producer_build_id()
    raw_hashes = [str(row.get("source_raw_payload_hash") or "") for row in versions]
    batch_id = capture_batch_id(
        producer="weather_data_feed_service.forecast_enrichment",
        captured_at_utc=str(payload.get("generated_at_utc") or ingested_at),
        scope={
            "cities": sorted({str(row.get("city") or "") for row in versions}),
            "forecast_target_dates": sorted({str(row.get("forecast_target_date") or "") for row in versions}),
        },
        raw_payload_hashes=raw_hashes,
    )
    state_path = output_dir / "forecast_version_lineage_state.json"
    versions, lineage_state = enrich_forecast_version_lineage(
        versions,
        state=read_json(state_path, {}),
        batch_id=batch_id,
        producer_build=build_id,
        ingested_at_utc=ingested_at,
    )
    write_json(state_path, lineage_state)
    # Physical partitions follow the collector capture boundary.  A reused
    # provider version may have an older available_at_utc, so deriving the
    # shard from the first version silently writes current captures into a
    # previous day's file.
    capture_day = record_day
    append_jsonl(
        output_dir / capture_day / "forecast_versions.jsonl", versions
    )
    contract_state_path = output_dir / "forecast_run_contract_state.json"
    contract_rows, contract_batches, contract_state = materialize_run_contract_capture(
        versions,
        read_json(contract_state_path, {}),
    )
    append_jsonl(output_dir / capture_day / "forecast_run_rows_v2.jsonl", contract_rows)
    append_jsonl(output_dir / capture_day / "forecast_batches_v2.jsonl", contract_batches)
    write_json(contract_state_path, contract_state)
    expected_model_keys = {
        str(row.get("model_key") or row.get("model_label") or "")
        for row in versions
        if row.get("model_key") or row.get("model_label")
    }
    summaries = forecast_batch_summaries(
        versions,
        batch_id=batch_id,
        expected_model_keys=expected_model_keys,
    )
    append_jsonl(output_dir / capture_day / "forecast_batch_summaries.jsonl", summaries)
    target_dates = sorted(
        {
            str(row.get("forecast_target_date"))
            for row in versions
            if row.get("forecast_target_date")
        }
    )
    write_json(
        output_dir / "latest_versions.json",
        {
            "schema_version": "weather_forecast_model_version_batch_v2",
            "producer": "weather_data_feed_service.forecast_enrichment",
            "producer_build_id": build_id,
            "producer_build_id_basis": build_basis,
            "batch_capture_id": batch_id,
            "generated_at_utc": payload.get("generated_at_utc"),
            "capture_rows": len(versions),
            "endpoint_version_rows": len(endpoint_versions),
            "run_aware_version_rows": len(versions) - len(endpoint_versions),
            "cities": len(
                {str(row.get("city")) for row in versions if row.get("city")}
            ),
            "forecast_target_dates": target_dates,
            "models": sorted(
                {
                    str(row.get("model_label"))
                    for row in versions
                    if row.get("model_label")
                }
            ),
            "records": versions,
            "batch_summaries": summaries,
            "single_run_capture_summary": payload.get("single_run_capture_summary"),
        },
    )
    write_json(
        output_dir / "latest_run_contract_v2.json",
        {
            "schema_version": "weather_forecast_run_contract_capture_v2",
            "producer": "weather_data_feed_service.forecast_enrichment",
            "generated_at_utc": payload.get("generated_at_utc"),
            "forecast_rows": len(contract_rows),
            "forecast_batches": len(contract_batches),
            "real_run_identified_rows": sum(
                row.get("forecast_run_lineage_status") == "identified"
                for row in contract_rows
            ),
            "records": contract_rows,
            "batches": contract_batches,
        },
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build forecast enrichment rows for shadow/research feature capture.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--now-utc", default=None)
    parser.add_argument("--cities", nargs="*", default=None)
    parser.add_argument("--include-station-diff", action="store_true")
    parser.add_argument("--forecast-days", type=int, default=3)
    parser.add_argument("--timeout-sec", type=float, default=8.0)
    parser.add_argument("--max-workers", type=int, default=6)
    parser.add_argument(
        "--open-meteo-refresh-sec",
        type=int,
        default=DEFAULT_OPEN_METEO_REFRESH_SEC,
    )
    parser.add_argument("--no-taf", action="store_true")
    parser.add_argument("--no-single-runs", action="store_true")
    parser.add_argument(
        "--single-run-models",
        nargs="+",
        default=list(DEFAULT_GLOBAL_SINGLE_RUN_MODELS),
    )
    parser.add_argument("--single-run-availability-lag-hours", type=int, default=6)
    parser.add_argument("--single-run-batch-size", type=int, default=10)
    parser.add_argument("--single-run-timeout-sec", type=float, default=45.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_payload(args)
    write_outputs(payload, Path(args.output_dir))
    print(
        json.dumps(
            {
                k: v
                for k, v in payload.items()
                if k not in {"records", "run_aware_forecast_versions"}
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
