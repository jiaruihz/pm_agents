"""Forecast enrichment producer for shadow/research feature capture."""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from weather_data_feed import city_local_date, load_city_configs, parse_now_utc
from weather_data_feed.information_events import build_information_event
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
from weather_data_feed_service.cli import DEFAULT_RUNTIME_ROOT
from weather_data_feed_service.legacy_weather_predict.city_pools import FULL_CITY_CONFIGS
from weather_data_feed_service.io_utils import (
    append_jsonl,
    read_json,
    write_json,
    write_latest_and_daily_jsonl,
)


DEFAULT_OUTPUT_DIR = DEFAULT_RUNTIME_ROOT / "output" / "forecast_enrichment"


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


def fetch_city_forecast_enrichment(
    cfg: CityConfig,
    now_utc: datetime,
    *,
    settings: ForecastFetchSettings,
    forecast_days: int,
    include_taf: bool = True,
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
    }
    if lat is None or lon is None:
        return {
            **base,
            "status": "missing_coordinates",
            "error": "city missing lat/lon in legacy city config",
            "payload_hash": stable_hash(base),
        }

    temperature_unit = "fahrenheit"
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

    context_hourly = context.payload.get("hourly") if isinstance(context.payload, dict) else {}
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
                utc_offset_seconds=int(context.payload.get("utc_offset_seconds") or local_now.utcoffset().total_seconds()),
                first_peak_hour=int(first_peak_hour),
                last_peak_hour=int(last_peak_hour),
            )
        except Exception as exc:  # noqa: BLE001
            taf_result = _failed_result("aviationweather_taf", exc)
            taf_signal = {"available": False, "status": "fetch_failed", "error": taf_result.error}

    source_statuses = {
        "open_meteo_multi_model": multi_model.status,
        "open_meteo_weather_context": context.status,
        "aviationweather_taf": taf_result.status if taf_result else "disabled",
    }
    ok_sources = sum(1 for value in source_statuses.values() if value == "ok")
    status = "ok" if ok_sources >= 2 else ("partial" if ok_sources else "failed")
    row = {
        **base,
        "status": status,
        "source_statuses": source_statuses,
        "open_meteo_multi_model": _compact_multi_model_payload(
            multi_model, target_date
        ),
        "open_meteo_weather_context": {
            "result": _compact_result(context),
            "target_day_hourly": hourly_summary,
            "daily": context.payload.get("daily", {}) if isinstance(context.payload, dict) else {},
            "hourly": context.payload.get("hourly", {}) if isinstance(context.payload, dict) else {},
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
            ): cfg
            for cfg in configs
        }
        for future in as_completed(futures):
            rows.append(future.result())
    rows = sorted(rows, key=lambda row: str(row.get("city") or ""))
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
    }
    return {**summary, "records": rows}


def write_outputs(payload: dict[str, Any], output_dir: Path) -> None:
    rows = _annotate_taf_information_events(list(payload.get("records") or []), output_dir)
    write_latest_and_daily_jsonl(
        output_dir=output_dir,
        latest_payload=payload,
        rows=rows,
        jsonl_name="forecast_enrichment.jsonl",
    )
    versions = [
        version
        for row in rows
        if isinstance(row, dict)
        for version in multi_model_forecast_versions(row)
    ]
    capture_day = (
        str(versions[0].get("available_at_utc") or "")[:10]
        if versions
        else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    )
    append_jsonl(output_dir / "forecast_versions.jsonl", versions)
    append_jsonl(
        output_dir / capture_day / "forecast_versions.jsonl", versions
    )
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
            "schema_version": "weather_forecast_model_version_batch_v1",
            "producer": "weather_data_feed_service.forecast_enrichment",
            "generated_at_utc": payload.get("generated_at_utc"),
            "capture_rows": len(versions),
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
    parser.add_argument("--no-taf", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_payload(args)
    write_outputs(payload, Path(args.output_dir))
    print(json.dumps({k: v for k, v in payload.items() if k != "records"}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
