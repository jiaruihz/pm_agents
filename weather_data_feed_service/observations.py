"""Fast observation cache producer for live weather strategies."""

from __future__ import annotations

import argparse
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from weather_data_feed import (
    build_observation_cache,
    city_local_date,
    index_observation_cache,
    load_city_configs,
    load_observation_cache,
    parse_now_utc,
    write_observation_cache,
)
from weather_data_feed.models import CityConfig, ObservationRecord
from weather_data_feed.observation_sources import (
    FetchSettings,
    ObservationSourceRequest,
    fetch_observation_source,
    infer_cadence_min,
    normalize_source_name,
)
from weather_data_feed.observation_sources.fetchers import parse_dt
from weather_data_feed.observation_sources.fetchers import one_hour_observation_changes
from weather_data_feed.physical_features import metar_physical_features

from weather_data_feed_service.cli import DEFAULT_RUNTIME_ROOT


DEFAULT_OUTPUT_PATH = DEFAULT_RUNTIME_ROOT / "output" / "observations" / "latest.json"


def _float_or_none(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _temp_f(temp_c: float | None) -> float | None:
    return None if temp_c is None else temp_c * 9.0 / 5.0 + 32.0


def _asof(records: list[ObservationRecord], now: datetime, minutes: float, attr: str) -> float | None:
    target = now.timestamp() - minutes * 60.0
    prior: list[tuple[datetime, ObservationRecord]] = []
    for record in records:
        dt = parse_dt(record.obs_ts_utc)
        if dt is not None and dt.timestamp() <= target:
            prior.append((dt, record))
    if not prior:
        return None
    return _float_or_none(getattr(sorted(prior, key=lambda item: item[0])[-1][1], attr))


def _source_chain(cfg: CityConfig, *, include_fallback_sources: bool) -> list[str]:
    sources = [cfg.live_observation_source]
    if include_fallback_sources:
        if normalize_source_name(cfg.live_observation_source) == "aviationweather_metar":
            sources.append("aviationweather_cache_csv")
        sources.extend(cfg.fallback_sources)
    out: list[str] = []
    for source in sources:
        normalized = normalize_source_name(source)
        if normalized and normalized not in out:
            out.append(normalized)
    return out


def _fetch_result(cfg: CityConfig, target_date: str, settings: FetchSettings, sources: list[str]) -> tuple[str, Any]:
    last_error = ""
    for source in sources:
        try:
            result = fetch_observation_source(
                ObservationSourceRequest(
                    city=cfg.city,
                    station_or_feed=cfg.official_icao,
                    target_date=target_date,
                    timezone_name=cfg.timezone_name,
                    source_key=source,
                    metadata={"recent_minutes": 360},
                ),
                settings=settings,
            )
            if result.records or result.status == "ok":
                return source, result
            last_error = result.error or result.status
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
    raise RuntimeError(last_error or "no observation records")


def observation_cache_row(
    cfg: CityConfig,
    now_utc: datetime,
    *,
    settings: FetchSettings,
    include_fallback_sources: bool = False,
) -> dict[str, Any]:
    target_date = city_local_date(cfg.city, now_utc).isoformat()
    sources = _source_chain(cfg, include_fallback_sources=include_fallback_sources)
    fetched_at = datetime.now(timezone.utc)
    try:
        source, result = _fetch_result(cfg, target_date, settings, sources)
        fetched_at = parse_dt(result.fetched_at_utc) or datetime.now(timezone.utc)
        records = list(result.records)
    except Exception as exc:  # noqa: BLE001
        return {
            "city": cfg.city,
            "target_date": target_date,
            "timezone_name": cfg.timezone_name,
            "unit": cfg.unit,
            "station": cfg.official_icao,
            "source": sources[0] if sources else "",
            "status": "fetch_failed",
            "error": f"{type(exc).__name__}: {exc}",
            "fetched_at_utc": fetched_at.isoformat(),
            "n_obs": 0,
            "source_chain": sources,
        }

    latest = records[-1] if records else None
    if latest is None:
        return {
            "city": cfg.city,
            "target_date": target_date,
            "timezone_name": cfg.timezone_name,
            "unit": cfg.unit,
            "station": cfg.official_icao,
            "source": source,
            "status": result.status or "empty",
            "error": result.error,
            "fetched_at_utc": fetched_at.isoformat(),
            "n_obs": 0,
            "source_chain": sources,
        }

    latest_dt = parse_dt(latest.obs_ts_utc)
    temps = [record.temp_c for record in records if _float_or_none(record.temp_c) is not None]
    running_max_c = max(temps) if temps else None
    running_hits = [
        parse_dt(record.obs_ts_utc)
        for record in records
        if running_max_c is not None and abs(float(record.temp_c) - running_max_c) < 1e-9
    ]
    running_hits = [dt for dt in running_hits if dt is not None]
    running_max_obs_utc = max(running_hits) if running_hits else None
    current_temp_c = _float_or_none(latest.temp_c)
    age_min = None if latest_dt is None else round((fetched_at - latest_dt).total_seconds() / 60.0, 3)
    cadence_min = infer_cadence_min(records)
    tmpf_now = _temp_f(current_temp_c)
    tmpc_1h = _asof(records, fetched_at, 60.0, "temp_c")
    tmpc_3h = _asof(records, fetched_at, 180.0, "temp_c")
    dwpc_now = _float_or_none(latest.dewpoint_c)
    dwpc_3h = _asof(records, fetched_at, 180.0, "dewpoint_c")
    relh_now = _float_or_none(latest.relh)
    relh_3h = _asof(records, fetched_at, 180.0, "relh")
    wind_kt = _float_or_none(latest.wind_kt)
    physical = metar_physical_features(
        latest.raw_text,
        latest.metadata.get("present_weather")
        or latest.metadata.get("present_weather_codes")
        or latest.metadata.get("wx_string"),
    )
    wind_dir_raw = latest.metadata.get("wind_dir_deg")
    if wind_dir_raw is None:
        wind_dir_raw = latest.metadata.get("metar_wind_dir_deg")
    wind_dir_deg = _float_or_none(wind_dir_raw)
    changes = one_hour_observation_changes(records)
    return {
        "city": cfg.city,
        "target_date": target_date,
        "timezone_name": cfg.timezone_name,
        "unit": cfg.unit,
        "station": cfg.official_icao,
        "source": result.source_key or source,
        "status": "ok" if current_temp_c is not None and running_max_c is not None else "missing_temp",
        "error": result.error,
        "fetched_at_utc": fetched_at.isoformat(),
        "last_obs_utc": latest.obs_ts_utc,
        "running_max_obs_utc": running_max_obs_utc.isoformat() if running_max_obs_utc else "",
        "minutes_since_running_max": None if running_max_obs_utc is None else round((fetched_at - running_max_obs_utc).total_seconds() / 60.0, 3),
        "n_obs": len(records),
        "age_min": age_min,
        "cadence_min": cadence_min,
        "minutes_to_next_obs": None if age_min is None or cadence_min is None else round(cadence_min - age_min, 3),
        "current_temp_c": current_temp_c,
        "running_max_c": running_max_c,
        "decline_c": None if current_temp_c is None or running_max_c is None else running_max_c - current_temp_c,
        "tmpf_now": tmpf_now,
        "dwpf_now": _temp_f(dwpc_now),
        "dewpoint_depression_f": None if tmpf_now is None or dwpc_now is None else tmpf_now - _temp_f(dwpc_now),
        "relh_now": relh_now,
        "relative_humidity_pct": relh_now,
        "sknt_now": wind_kt,
        "wind_speed_kt": wind_kt,
        "drct_now": wind_dir_deg,
        "wind_dir_deg": wind_dir_deg,
        "sky_code_now": latest.sky_code,
        "sky_cover_code": latest.sky_code,
        "raw_metar": latest.raw_text,
        "present_weather_codes": physical["present_weather_codes"],
        "precip_state": physical["precip_state"],
        "precip_intensity_code": physical["precip_intensity_code"],
        "precip_observed": physical["precip_observed"],
        "thunderstorm_observed": physical["thunderstorm_observed"],
        "freezing_precip_observed": physical["freezing_precip_observed"],
        "cloud_layer_count": physical["cloud_layer_count"],
        "lowest_cloud_base_ft_agl": physical["lowest_cloud_base_ft_agl"],
        "ceiling_ft_agl": physical["ceiling_ft_agl"],
        **changes,
        "d_tmpf_1h": None if current_temp_c is None or tmpc_1h is None else (current_temp_c - tmpc_1h) * 9.0 / 5.0,
        "d_tmpf_3h": None if current_temp_c is None or tmpc_3h is None else (current_temp_c - tmpc_3h) * 9.0 / 5.0,
        "d_dwpf_3h": None if dwpc_now is None or dwpc_3h is None else (dwpc_now - dwpc_3h) * 9.0 / 5.0,
        "d_relh_3h": None if relh_now is None or relh_3h is None else relh_now - relh_3h,
        "record_count": len(records),
        "estimated_cadence_min": cadence_min,
        "fetch_latency_ms": result.latency_ms,
        "source_chain": sources,
    }


def build_cache(args: argparse.Namespace) -> dict[str, Any]:
    now_utc = parse_now_utc(args.now_utc) if args.now_utc else datetime.now(timezone.utc)
    output = Path(args.output)
    previous_records: dict[tuple[str, str], dict[str, Any]] = {}
    if output.exists():
        try:
            previous_records = index_observation_cache(load_observation_cache(output))
        except Exception:
            previous_records = {}
    configs = load_city_configs(
        include_station_diff=args.include_station_diff,
        only_cities=set(args.cities or []) or None,
    )
    settings = FetchSettings(timeout_sec=args.timeout_sec, proxy_candidates=(None,))
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.max_workers)) as executor:
        futures = {
            executor.submit(
                observation_cache_row,
                cfg,
                now_utc,
                settings=settings,
                include_fallback_sources=args.include_fallback_sources,
            ): cfg
            for cfg in configs
        }
        for future in as_completed(futures):
            row = future.result()
            previous = previous_records.get((str(row.get("city") or ""), str(row.get("target_date") or "")))
            if row.get("status") != "ok" and previous and previous.get("status") == "ok":
                reused = dict(previous)
                reused["cache_reused_after_fetch_status"] = row.get("status")
                reused["cache_reused_after_fetch_error"] = row.get("error")
                reused["cache_reused_at_utc"] = datetime.now(timezone.utc).isoformat()
                rows.append(reused)
            else:
                rows.append(row)
    cache = build_observation_cache(
        sorted(rows, key=lambda row: str(row.get("city"))),
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    ok = sum(1 for row in rows if row.get("status") == "ok")
    cache["summary"] = {
        "cities": len(configs),
        "ok": ok,
        "non_ok": len(rows) - ok,
        "include_fallback_sources": bool(args.include_fallback_sources),
    }
    return cache


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the fast weather observation cache.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--now-utc", default=None)
    parser.add_argument("--cities", nargs="*", default=None)
    parser.add_argument("--include-station-diff", action="store_true")
    parser.add_argument("--include-fallback-sources", action="store_true")
    parser.add_argument("--timeout-sec", type=float, default=3.0)
    parser.add_argument("--max-workers", type=int, default=12)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cache = build_cache(args)
    output = Path(args.output)
    write_observation_cache(cache, output)
    print(json.dumps({"status": "ok", "output": str(output), **cache.get("summary", {})}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
