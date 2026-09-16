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
)
from weather_data_feed.models import CityConfig, ObservationRecord
from weather_data_feed.observation_sources import (
    FetchSettings,
    ObservationSourceRequest,
    fetch_observation_source,
    infer_cadence_min,
    normalize_source_name,
    observation_path_features,
)
from weather_data_feed.observation_sources.fetchers import parse_dt
from weather_data_feed.observation_sources.fetchers import one_hour_observation_changes
from weather_data_feed.information_events import canonical_json_hash
from weather_data_feed.physical_features import metar_physical_features
from weather_data_feed.source_lineage import producer_build_id
from weather_data_feed_service.cli import DEFAULT_RUNTIME_ROOT
from weather_data_feed_service.io_utils import write_latest_and_daily_jsonl


DEFAULT_OUTPUT_PATH = DEFAULT_RUNTIME_ROOT / "output" / "observations" / "latest.json"
FIRST_OBSERVATION_GRACE_MIN = 90
PRODUCER = "weather_data_feed_service.observations"
PRODUCER_BUILD_ID, PRODUCER_BUILD_ID_BASIS = producer_build_id(
    Path(__file__).resolve().parents[1]
)


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
            sources.extend(("aviationweather_cache_csv", "noaa_tgftp_station_txt"))
        sources.extend(cfg.fallback_sources)
    out: list[str] = []
    for source in sources:
        normalized = normalize_source_name(source)
        if normalized and normalized not in out:
            out.append(normalized)
    return out


def _fetch_result(cfg: CityConfig, target_date: str, settings: FetchSettings, sources: list[str]) -> tuple[str, Any]:
    last_error = ""
    stale_results: list[tuple[datetime, str, Any]] = []
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
            latest = max(
                (dt for record in result.records if (dt := parse_dt(record.obs_ts_utc)) is not None),
                default=None,
            )
            fetched_at = parse_dt(result.fetched_at_utc) or datetime.now(timezone.utc)
            if latest is not None:
                age_min = (fetched_at - latest).total_seconds() / 60.0
                if 0.0 <= age_min <= 120.0:
                    return source, result
                if age_min >= 0.0:
                    stale_results.append((latest, source, result))
            last_error = result.error or result.status
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
    if stale_results:
        _latest, source, result = max(stale_results, key=lambda item: item[0])
        return source, result
    raise RuntimeError(last_error or "no observation records")


def _local_day_elapsed_min(cfg: CityConfig, now_utc: datetime) -> int:
    local_now = now_utc.astimezone(ZoneInfo(cfg.timezone_name))
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return max(0, int((local_now - local_midnight).total_seconds() / 60.0))


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
        records = []
        for record in result.records:
            obs_dt = parse_dt(record.obs_ts_utc)
            ingest_dt = parse_dt(record.ingest_ts_utc)
            if obs_dt is None or obs_dt > fetched_at or (ingest_dt is not None and ingest_dt > fetched_at):
                continue
            records.append(record)
        records.sort(key=lambda record: parse_dt(record.obs_ts_utc) or datetime.min.replace(tzinfo=timezone.utc))
    except Exception as exc:  # noqa: BLE001
        local_day_elapsed_min = _local_day_elapsed_min(cfg, now_utc)
        awaiting_first_observation = (
            str(exc) in {"empty", "no observation records"}
            and local_day_elapsed_min <= FIRST_OBSERVATION_GRACE_MIN
        )
        return {
            "city": cfg.city,
            "target_date": target_date,
            "timezone_name": cfg.timezone_name,
            "unit": cfg.unit,
            "station": cfg.official_icao,
            "source": sources[0] if sources else "",
            "status": "awaiting_first_observation" if awaiting_first_observation else "fetch_failed",
            "error": f"{type(exc).__name__}: {exc}",
            "fetched_at_utc": fetched_at.isoformat(),
            "n_obs": 0,
            "source_chain": sources,
            "local_day_elapsed_min": local_day_elapsed_min,
            "first_observation_grace_min": FIRST_OBSERVATION_GRACE_MIN,
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
    running_min_c = min(temps) if temps else None
    running_hits = [
        parse_dt(record.obs_ts_utc)
        for record in records
        if running_max_c is not None and abs(float(record.temp_c) - running_max_c) < 1e-9
    ]
    running_hits = [dt for dt in running_hits if dt is not None]
    running_max_obs_utc = max(running_hits) if running_hits else None
    running_min_hits = [
        parse_dt(record.obs_ts_utc)
        for record in records
        if running_min_c is not None and abs(float(record.temp_c) - running_min_c) < 1e-9
    ]
    running_min_hits = [dt for dt in running_min_hits if dt is not None]
    running_min_obs_utc = max(running_min_hits) if running_min_hits else None
    current_temp_c = _float_or_none(latest.temp_c)
    age_min = None if latest_dt is None else round((fetched_at - latest_dt).total_seconds() / 60.0, 3)
    cadence_min = infer_cadence_min(records)
    tmpf_now = _temp_f(current_temp_c)
    # Keep the legacy fetch-anchored fields stable for existing consumers.
    legacy_tmpc_1h = _asof(records, fetched_at, 60.0, "temp_c")
    legacy_tmpc_3h = _asof(records, fetched_at, 180.0, "temp_c")
    legacy_dwpc_3h = _asof(records, fetched_at, 180.0, "dewpoint_c")
    legacy_relh_3h = _asof(records, fetched_at, 180.0, "relh")
    # New report-anchored fields express the meteorological path independently
    # from observation age.  New state features consume these explicit names.
    trend_anchor = latest_dt or fetched_at
    tmpc_1h = _asof(records, trend_anchor, 60.0, "temp_c")
    tmpc_3h = _asof(records, trend_anchor, 180.0, "temp_c")
    dwpc_now = _float_or_none(latest.dewpoint_c)
    dwpc_1h = _asof(records, trend_anchor, 60.0, "dewpoint_c")
    dwpc_3h = _asof(records, trend_anchor, 180.0, "dewpoint_c")
    relh_now = _float_or_none(latest.relh)
    relh_3h = _asof(records, trend_anchor, 180.0, "relh")
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
    path = observation_path_features(records, as_of_utc=fetched_at)
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
        **path,
        # Compatibility: this legacy clock is the last observation equal to
        # the running maximum.  Consumers that need plateau maturity must use
        # minutes_since_last_strict_new_high / minutes_since_first_running_max.
        "running_max_obs_utc": running_max_obs_utc.isoformat() if running_max_obs_utc else "",
        "minutes_since_running_max": None if running_max_obs_utc is None else round((fetched_at - running_max_obs_utc).total_seconds() / 60.0, 3),
        "running_min_obs_utc": running_min_obs_utc.isoformat() if running_min_obs_utc else "",
        "minutes_since_running_min": None if running_min_obs_utc is None else round((fetched_at - running_min_obs_utc).total_seconds() / 60.0, 3),
        "n_obs": len(records),
        "age_min": age_min,
        "cadence_min": cadence_min,
        "minutes_to_next_obs": None if age_min is None or cadence_min is None else round(cadence_min - age_min, 3),
        "current_temp_c": current_temp_c,
        "running_max_c": running_max_c,
        "running_min_c": running_min_c,
        "decline_c": None if current_temp_c is None or running_max_c is None else running_max_c - current_temp_c,
        "rebound_c": None if current_temp_c is None or running_min_c is None else current_temp_c - running_min_c,
        "tmpf_now": tmpf_now,
        "dwpf_now": _temp_f(dwpc_now),
        "dewpoint_depression_f": None if tmpf_now is None or dwpc_now is None else tmpf_now - _temp_f(dwpc_now),
        "relh_now": relh_now,
        "relative_humidity_pct": relh_now,
        "sknt_now": wind_kt,
        "wind_speed_kt": wind_kt,
        "pressure_hpa": physical["pressure_hpa"],
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
        "d_tmpf_1h": None if current_temp_c is None or legacy_tmpc_1h is None else (current_temp_c - legacy_tmpc_1h) * 9.0 / 5.0,
        "d_tmpf_3h": None if current_temp_c is None or legacy_tmpc_3h is None else (current_temp_c - legacy_tmpc_3h) * 9.0 / 5.0,
        "temp_trend_report_anchored_1h_f": None if current_temp_c is None or tmpc_1h is None else (current_temp_c - tmpc_1h) * 9.0 / 5.0,
        "temp_trend_report_anchored_3h_f": None if current_temp_c is None or tmpc_3h is None else (current_temp_c - tmpc_3h) * 9.0 / 5.0,
        "d_dwpf_1h": None if dwpc_now is None or dwpc_1h is None else (dwpc_now - dwpc_1h) * 9.0 / 5.0,
        "d_dwpf_3h": None if dwpc_now is None or legacy_dwpc_3h is None else (dwpc_now - legacy_dwpc_3h) * 9.0 / 5.0,
        "dewpoint_trend_report_anchored_1h_f": None if dwpc_now is None or dwpc_1h is None else (dwpc_now - dwpc_1h) * 9.0 / 5.0,
        "dewpoint_trend_report_anchored_3h_f": None if dwpc_now is None or dwpc_3h is None else (dwpc_now - dwpc_3h) * 9.0 / 5.0,
        "d_relh_3h": None if relh_now is None or legacy_relh_3h is None else relh_now - legacy_relh_3h,
        "relative_humidity_trend_report_anchored_3h_pct": None if relh_now is None or relh_3h is None else relh_now - relh_3h,
        "record_count": len(records),
        "estimated_cadence_min": cadence_min,
        "fetch_latency_ms": result.latency_ms,
        "source_chain": sources,
    }


def merge_previous_running_max(
    row: dict[str, Any],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    """Keep one station-day's running extrema monotone across source failover.

    ``aviationweather_cache_csv`` can contain only the latest METAR.  It is
    valid for the current observation but cannot reconstruct the day-to-date
    maximum, so retain a higher maximum already observed for the same station
    and local date.
    """

    if not previous or row.get("status") != "ok" or not _has_trusted_observation(previous):
        return row
    if str(row.get("target_date") or "") != str(previous.get("target_date") or ""):
        return row
    if str(row.get("station") or "") != str(previous.get("station") or ""):
        return row
    current_max = _float_or_none(row.get("running_max_c"))
    previous_max = _float_or_none(previous.get("running_max_c"))
    current_min = _float_or_none(row.get("running_min_c"))
    previous_min = _float_or_none(previous.get("running_min_c"))
    merge_max = (
        current_max is not None
        and previous_max is not None
        and current_max < previous_max
    )
    merge_min = (
        current_min is not None
        and previous_min is not None
        and current_min > previous_min
    )
    if not merge_max and not merge_min:
        return row

    out = dict(row)
    current_temp = _float_or_none(out.get("current_temp_c"))
    fetched_at = parse_dt(str(out.get("fetched_at_utc") or ""))
    if merge_max:
        out["running_max_c"] = previous_max
        out["decline_c"] = None if current_temp is None else previous_max - current_temp
        if previous.get("running_max_obs_utc") not in (None, ""):
            out["running_max_obs_utc"] = previous["running_max_obs_utc"]
        running_max_at = parse_dt(str(out.get("running_max_obs_utc") or ""))
        if fetched_at is not None and running_max_at is not None:
            out["minutes_since_running_max"] = round(
                (fetched_at - running_max_at).total_seconds() / 60.0, 3
            )
        out["history_continuity_status"] = "merged_previous_running_max"
        out["history_continuity_previous_running_max_c"] = previous_max
        out["history_continuity_raw_running_max_c"] = current_max
    if merge_min:
        out["running_min_c"] = previous_min
        out["rebound_c"] = None if current_temp is None else current_temp - previous_min
        if previous.get("running_min_obs_utc") not in (None, ""):
            out["running_min_obs_utc"] = previous["running_min_obs_utc"]
        running_min_at = parse_dt(str(out.get("running_min_obs_utc") or ""))
        if fetched_at is not None and running_min_at is not None:
            out["minutes_since_running_min"] = round(
                (fetched_at - running_min_at).total_seconds() / 60.0, 3
            )
        out["history_continuity_min_status"] = "merged_previous_running_min"
        out["history_continuity_previous_running_min_c"] = previous_min
        out["history_continuity_raw_running_min_c"] = current_min
        out.setdefault("history_continuity_status", "merged_previous_running_min")
    out["history_continuity_previous_source"] = str(previous.get("source") or "")
    return out


def _has_trusted_observation(row: dict[str, Any]) -> bool:
    """Return whether a cache row still carries a successful observation."""

    status = str(row.get("status") or "")
    last_success_status = str(row.get("last_success_status") or "")
    return status == "ok" or (
        status == "reused_after_fetch_error" and last_success_status == "ok"
    )


def build_cache(args: argparse.Namespace) -> dict[str, Any]:
    now_utc = parse_now_utc(args.now_utc) if args.now_utc else datetime.now(timezone.utc)
    output = Path(args.output)
    previous_records: dict[tuple[str, str], dict[str, Any]] = {}
    if output.exists():
        try:
            previous_records = index_observation_cache(load_observation_cache(output))
        except Exception:
            previous_records = {}
    additional_cities = set(getattr(args, "additional_cities", None) or [])
    configs = load_city_configs(
        include_station_diff=args.include_station_diff,
        only_cities=set(args.cities or []) or None,
        include_research_cities=bool(additional_cities),
        research_cities=additional_cities or None,
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
            if row.get("status") != "ok" and previous and _has_trusted_observation(previous):
                reused = dict(previous)
                reused_at = now_utc
                reused["status"] = "reused_after_fetch_error"
                reused["last_success_status"] = str(previous.get("last_success_status") or previous.get("status") or "")
                reused["cache_reused_after_fetch_status"] = row.get("status")
                reused["cache_reused_after_fetch_error"] = row.get("error")
                reused["cache_reused_at_utc"] = reused_at.isoformat()
                last_obs = parse_dt(str(previous.get("last_obs_utc") or ""))
                if last_obs is not None:
                    reused["age_min"] = round((reused_at - last_obs).total_seconds() / 60.0, 3)
                for field, timestamp_field in (
                    ("minutes_since_running_max", "running_max_obs_utc"),
                    ("minutes_since_running_min", "running_min_obs_utc"),
                    ("minutes_since_first_running_max", "first_running_max_obs_utc"),
                    ("minutes_since_last_running_max", "last_running_max_obs_utc"),
                    ("minutes_since_last_strict_new_high", "first_running_max_obs_utc"),
                ):
                    timestamp = parse_dt(str(previous.get(timestamp_field) or ""))
                    if timestamp is not None:
                        reused[field] = round((reused_at - timestamp).total_seconds() / 60.0, 3)
                rows.append(reused)
            else:
                rows.append(merge_previous_running_max(row, previous))
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
        "additional_cities": sorted(additional_cities),
        "running_max_continuity_merges": sum(
            1 for row in rows if row.get("history_continuity_status") == "merged_previous_running_max"
        ),
        "running_min_continuity_merges": sum(
            1
            for row in rows
            if row.get("history_continuity_min_status")
            == "merged_previous_running_min"
            or row.get("history_continuity_status")
            == "merged_previous_running_min"
        ),
    }
    return cache


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the fast weather observation cache.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--now-utc", default=None)
    parser.add_argument("--cities", nargs="*", default=None)
    parser.add_argument(
        "--additional-cities",
        nargs="*",
        default=None,
        help=(
            "Add source-resolved observation coverage without changing strategy "
            "live eligibility"
        ),
    )
    parser.add_argument("--include-station-diff", action="store_true")
    parser.add_argument("--include-fallback-sources", action="store_true")
    parser.add_argument("--timeout-sec", type=float, default=3.0)
    parser.add_argument("--max-workers", type=int, default=12)
    return parser


def observation_history_rows(cache: dict[str, Any]) -> list[dict[str, Any]]:
    """Add durable capture identity and clocks to observation-cache history."""

    generated_at_utc = str(cache.get("generated_at_utc") or "")
    rows = [dict(row) for row in cache.get("records") or [] if isinstance(row, dict)]
    batch_capture_id = canonical_json_hash(
        {
            "producer": PRODUCER,
            "generated_at_utc": generated_at_utc,
            "record_count": len(rows),
            "cities": sorted(str(row.get("city") or "") for row in rows),
        }
    )
    history_rows: list[dict[str, Any]] = []
    for row in rows:
        row["record_type"] = "weather_observation_cache_record"
        row["producer"] = PRODUCER
        row["producer_build_id"] = PRODUCER_BUILD_ID
        row["producer_build_id_basis"] = PRODUCER_BUILD_ID_BASIS
        row["batch_capture_id"] = batch_capture_id
        row["observation_cache_generated_at_utc"] = generated_at_utc
        row.setdefault("available_at_utc", generated_at_utc)
        row.setdefault("ingested_at_utc", generated_at_utc)
        row["observation_history_id"] = canonical_json_hash(
            {
                "batch_capture_id": batch_capture_id,
                "city": row.get("city"),
                "target_date": row.get("target_date"),
                "station": row.get("station"),
                "last_obs_utc": row.get("last_obs_utc"),
            }
        )
        history_rows.append(row)
    return history_rows


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cache = build_cache(args)
    output = Path(args.output)
    generated_at_utc = str(cache.get("generated_at_utc") or "")
    write_latest_and_daily_jsonl(
        output_dir=output.parent,
        latest_payload=cache,
        rows=observation_history_rows(cache),
        jsonl_name="observations.jsonl",
        day=generated_at_utc[:10] or None,
        write_aggregate=False,
    )
    print(json.dumps({"status": "ok", "output": str(output), **cache.get("summary", {})}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
