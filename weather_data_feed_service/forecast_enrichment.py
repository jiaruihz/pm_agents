"""Forecast enrichment producer for shadow/research feature capture."""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from weather_data_feed import city_local_date, load_city_configs, parse_now_utc
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
from weather_data_feed_service.io_utils import write_latest_and_daily_jsonl


DEFAULT_OUTPUT_DIR = DEFAULT_RUNTIME_ROOT / "output" / "forecast_enrichment"


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

    target_multi_model = {}
    if isinstance(multi_model.payload, dict):
        target_multi_model = (multi_model.payload.get("daily") or {}).get(target_date) or {}

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
        "open_meteo_multi_model": {
            "result": _compact_result(multi_model),
            "target_date": target_multi_model,
            "daily_dates": multi_model.payload.get("daily_dates", []) if isinstance(multi_model.payload, dict) else [],
            "model_metadata": multi_model.payload.get("model_metadata", {}) if isinstance(multi_model.payload, dict) else {},
            "hourly_values_hash_by_model": multi_model.payload.get("hourly_values_hash_by_model", {}) if isinstance(multi_model.payload, dict) else {},
        },
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
    rows = list(payload.get("records") or [])
    write_latest_and_daily_jsonl(
        output_dir=output_dir,
        latest_payload=payload,
        rows=rows,
        jsonl_name="forecast_enrichment.jsonl",
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
