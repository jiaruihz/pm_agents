"""High-frequency airport/reference observation producer for research capture."""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from weather_data_feed import parse_now_utc
from weather_data_feed.high_frequency_observation_sources import (
    HighFrequencyFetchResult,
    HighFrequencyFetchSettings,
    fetch_high_frequency_observation,
    stable_hash,
    supported_high_frequency_sources,
)
from weather_data_feed_service.cli import DEFAULT_RUNTIME_ROOT
from weather_data_feed_service.io_utils import write_latest_and_daily_jsonl
from weather_data_feed_service.scheduling import ActiveLocalWindow, filter_jobs_by_local_window, filter_jobs_by_min_interval


DEFAULT_OUTPUT_DIR = DEFAULT_RUNTIME_ROOT / "output" / "high_frequency_observations"
DEFAULT_DIRECT_SOURCES = [
    "amos_runway",
    "noaa_madis_hfmetar",
    "singapore_mss",
    "jma_amedas",
    "hko_obs",
    "cowin_obs",
    "fmi",
    "mgm",
    "ims_lod",
]
DEFAULT_SOURCE_MIN_INTERVAL_SEC = {
    "jma_amedas": 300.0,
    "noaa_madis_hfmetar": 300.0,
    "fmi": 300.0,
    "mgm": 300.0,
    "ims_lod": 300.0,
}


def _parse_minute_window(raw: str) -> tuple[float, float] | None:
    if "-" not in raw:
        return None
    start_raw, end_raw = raw.split("-", 1)
    try:
        start = float(start_raw)
        end = float(end_raw)
    except ValueError:
        return None
    if start < 0 or end < 0 or start >= 60 or end >= 60 or end < start:
        return None
    return start, end


def _failed_result(source: str, city: str, exc: BaseException) -> HighFrequencyFetchResult:
    now = datetime.now(timezone.utc).isoformat()
    return HighFrequencyFetchResult(
        source_key=source,
        city=city,
        status="fetch_failed",
        fetched_at_utc=now,
        latency_ms=0.0,
        error=f"{type(exc).__name__}: {exc}",
        metadata={"city": city},
    )


def _jobs(source_names: list[str], city_names: list[str] | None, now_utc: datetime, args: argparse.Namespace) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
    registry = supported_high_frequency_sources()
    selected_sources = source_names or list(DEFAULT_DIRECT_SOURCES)
    selected_cities = set(city_names or [])
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    for source in selected_sources:
        for city, meta in registry.get(source, {}).items():
            if selected_cities and city not in selected_cities:
                continue
            candidates.append((source, city, meta))
    return filter_jobs_by_local_window(
        candidates,
        now_utc=now_utc,
        window=ActiveLocalWindow(args.active_local_start_hour, args.active_local_end_hour),
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    tmp.replace(path)


def _parse_source_min_intervals(raw_items: list[str] | None) -> dict[str, float]:
    out = dict(DEFAULT_SOURCE_MIN_INTERVAL_SEC)
    for raw in raw_items or []:
        if "=" not in str(raw):
            continue
        source, value = str(raw).split("=", 1)
        try:
            out[source.strip()] = max(0.0, float(value))
        except ValueError:
            continue
    return out


def _parse_source_minute_window_intervals(raw_items: list[str] | None) -> dict[str, list[dict[str, Any]]]:
    """Parse source=minute_start-minute_end,...:interval_sec rules."""
    out: dict[str, list[dict[str, Any]]] = {}
    for raw in raw_items or []:
        item = str(raw).strip()
        if not item or "=" not in item or ":" not in item:
            continue
        source, rest = item.split("=", 1)
        windows_raw, interval_raw = rest.rsplit(":", 1)
        source = source.strip()
        try:
            interval_sec = max(0.0, float(interval_raw))
        except ValueError:
            continue
        windows = []
        for window_raw in windows_raw.split(","):
            parsed = _parse_minute_window(window_raw.strip())
            if parsed is None:
                continue
            windows.append(parsed)
        if not source or not windows:
            continue
        out.setdefault(source, []).append({"windows": windows, "interval_sec": interval_sec})
    return out


def _apply_active_minute_window_intervals(
    base_intervals: dict[str, float],
    rules: dict[str, list[dict[str, Any]]],
    *,
    now_utc: datetime,
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    intervals = dict(base_intervals)
    minute_value = now_utc.minute + now_utc.second / 60.0
    active: list[dict[str, Any]] = []
    for source, source_rules in rules.items():
        for rule in source_rules:
            interval_sec = float(rule.get("interval_sec") or 0.0)
            for start, end in rule.get("windows") or []:
                if start <= minute_value <= end:
                    previous = float(intervals.get(source, 0.0) or 0.0)
                    intervals[source] = interval_sec if previous <= 0 else min(previous, interval_sec)
                    active.append(
                        {
                            "source": source,
                            "minute_utc": round(minute_value, 3),
                            "window_start_minute": start,
                            "window_end_minute": end,
                            "base_interval_sec": previous,
                            "active_interval_sec": intervals[source],
                        }
                    )
                    break
    return intervals, active


def _previous_records_for_jobs(output_dir: Path, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted = {(str(row.get("source") or ""), str(row.get("city") or "")) for row in jobs}
    if not wanted:
        return []
    payload = _read_json(output_dir / "latest.json")
    records = []
    for row in payload.get("records") or []:
        key = (str(row.get("source") or ""), str(row.get("city") or ""))
        if key in wanted:
            records.append({**row, "source_status": row.get("source_status") or "cadence_preserved"})
    return records


def _fetch_job(source: str, city: str, now_utc: datetime, settings: HighFrequencyFetchSettings) -> HighFrequencyFetchResult:
    try:
        return fetch_high_frequency_observation(source, city, settings=settings, now_utc=now_utc)
    except Exception as exc:  # noqa: BLE001
        return _failed_result(source, city, exc)


def _row_with_hash(row: dict[str, Any], result: HighFrequencyFetchResult) -> dict[str, Any]:
    out = dict(row)
    out.setdefault("source", result.source_key)
    out.setdefault("source_status", result.status)
    out.setdefault("fetched_at_utc", result.fetched_at_utc)
    out.setdefault("source_fetch_latency_sec", round(result.latency_ms / 1000.0, 3))
    out["payload_hash"] = out.get("payload_hash") or stable_hash(
        {
            "source": out.get("source"),
            "city": out.get("city"),
            "station": out.get("station"),
            "observation_time_utc": out.get("observation_time_utc"),
            "temp_c": out.get("temp_c"),
            "raw_payload_hash": out.get("raw_payload_hash"),
        }
    )
    return out


def append_history_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest_by_key: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("source") or ""),
            str(row.get("city") or ""),
            str(row.get("station") or ""),
            str(row.get("icao") or ""),
            str(row.get("runway") or ""),
        )
        old = latest_by_key.get(key)
        if old is None or str(row.get("observation_time_utc") or "") >= str(old.get("observation_time_utc") or ""):
            latest_by_key[key] = row
    return sorted(
        latest_by_key.values(),
        key=lambda row: (
            str(row.get("city") or ""),
            str(row.get("source") or ""),
            str(row.get("station") or ""),
            str(row.get("observation_time_utc") or ""),
        ),
    )


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    now_utc = parse_now_utc(args.now_utc) if args.now_utc else datetime.now(timezone.utc)
    settings = HighFrequencyFetchSettings(timeout_sec=args.timeout_sec)
    active_jobs, skipped_jobs = _jobs(args.sources or [], args.cities, now_utc, args)
    state_path = Path(args.state_path) if args.state_path else Path(args.output_dir) / "state.json"
    state = _read_json(state_path)
    source_min_intervals = _parse_source_min_intervals(args.source_min_interval_sec)
    source_minute_window_intervals = _parse_source_minute_window_intervals(args.source_minute_window_min_interval_sec)
    effective_source_min_intervals, active_minute_window_overrides = _apply_active_minute_window_intervals(
        source_min_intervals,
        source_minute_window_intervals,
        now_utc=now_utc,
    )
    jobs, cadence_skipped_jobs = filter_jobs_by_min_interval(
        active_jobs,
        now_utc=now_utc,
        min_interval_by_source=effective_source_min_intervals,
        last_attempt_by_job=dict(state.get("last_attempt_by_job") or {}),
    )
    results: list[HighFrequencyFetchResult] = []
    with ThreadPoolExecutor(max_workers=max(1, args.max_workers)) as executor:
        futures = {
            executor.submit(_fetch_job, source, city, now_utc, settings): (source, city)
            for source, city in jobs
        }
        for future in as_completed(futures):
            results.append(future.result())

    rows: list[dict[str, Any]] = []
    for result in results:
        rows.extend(_row_with_hash(row, result) for row in result.records)
    preserved_rows = _previous_records_for_jobs(Path(args.output_dir), cadence_skipped_jobs)
    rows.extend(preserved_rows)
    rows = sorted(
        rows,
        key=lambda row: (
            str(row.get("city") or ""),
            str(row.get("source") or ""),
            str(row.get("station") or ""),
            str(row.get("observation_time_utc") or ""),
        ),
    )
    source_statuses = {f"{result.source_key}:{result.city}": result.status for result in results}
    source_errors = {f"{result.source_key}:{result.city}": result.error for result in results if result.error}
    summary = {
        "status": "ok",
        "schema_version": "weather_high_frequency_observations_payload_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "producer": "weather_data_feed_service.high_frequency_observations",
        "rows": len(rows),
        "ok_sources": sum(1 for result in results if result.status == "ok"),
        "empty_sources": sum(1 for result in results if result.status == "empty"),
        "non_ok_sources": sum(1 for result in results if result.status not in {"ok", "empty"}),
        "sources": args.sources or list(DEFAULT_DIRECT_SOURCES),
        "cities": sorted({str(row.get("city") or "") for row in rows if row.get("city")}),
        "active_job_cities": sorted({city for _source, city in active_jobs}),
        "fetch_attempt_cities": sorted({city for _source, city in jobs}),
        "active_local_window": ActiveLocalWindow(args.active_local_start_hour, args.active_local_end_hour).as_payload(),
        "skipped_inactive_jobs": skipped_jobs,
        "skipped_inactive_count": len(skipped_jobs),
        "source_min_interval_sec": source_min_intervals,
        "effective_source_min_interval_sec": effective_source_min_intervals,
        "source_minute_window_min_interval_sec": source_minute_window_intervals,
        "active_minute_window_overrides": active_minute_window_overrides,
        "skipped_cadence_jobs": cadence_skipped_jobs,
        "skipped_cadence_count": len(cadence_skipped_jobs),
        "cadence_preserved_rows": len(preserved_rows),
        "state_path": str(state_path),
        "source_statuses": source_statuses,
        "source_errors": source_errors,
    }
    return {**summary, "records": rows}


def update_state(payload: dict[str, Any], state_path: Path) -> None:
    now = str(payload.get("generated_at_utc") or datetime.now(timezone.utc).isoformat())
    state = _read_json(state_path)
    last = dict(state.get("last_attempt_by_job") or {})
    for key in payload.get("source_statuses") or {}:
        last[str(key)] = now
    _write_json(
        state_path,
        {
            "schema_version": "weather_high_frequency_observations_state_v1",
            "updated_at_utc": now,
            "last_attempt_by_job": last,
        },
    )


def write_outputs(payload: dict[str, Any], output_dir: Path) -> None:
    rows = list(payload.get("records") or [])
    append_rows = append_history_rows(rows)
    latest_payload = {**payload, "append_rows": len(append_rows)}
    write_latest_and_daily_jsonl(
        output_dir=output_dir,
        latest_payload=latest_payload,
        rows=append_rows,
        jsonl_name="high_frequency_observations.jsonl",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build airport/reference high-frequency observation rows.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--now-utc", default=None)
    parser.add_argument("--cities", nargs="*", default=None)
    parser.add_argument("--sources", nargs="*", default=None)
    parser.add_argument("--timeout-sec", type=float, default=8.0)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--active-local-start-hour", type=float, default=None)
    parser.add_argument("--active-local-end-hour", type=float, default=None)
    parser.add_argument("--source-min-interval-sec", action="append", default=[])
    parser.add_argument("--source-minute-window-min-interval-sec", action="append", default=[])
    parser.add_argument("--state-path", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_payload(args)
    write_outputs(payload, Path(args.output_dir))
    update_state(payload, Path(args.state_path) if args.state_path else Path(args.output_dir) / "state.json")
    print(json.dumps({k: v for k, v in payload.items() if k != "records"}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
