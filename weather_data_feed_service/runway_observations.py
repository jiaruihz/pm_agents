"""Runway observation producer for airport microclimate research."""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from weather_data_feed import city_local_date, parse_now_utc
from weather_data_feed.runway_sources import (
    RunwayFetchResult,
    RunwayFetchSettings,
    fetch_amos_runway,
    fetch_amsc_awos_runway,
    stable_hash,
    supported_runway_cities,
)
from weather_data_feed_service.cli import DEFAULT_RUNTIME_ROOT
from weather_data_feed_service.io_utils import write_latest_and_daily_jsonl


DEFAULT_OUTPUT_DIR = DEFAULT_RUNTIME_ROOT / "output" / "runway_observations"
CITY_TIMEZONES = {
    "Beijing": "Asia/Shanghai",
    "Shanghai": "Asia/Shanghai",
    "Guangzhou": "Asia/Shanghai",
    "Chengdu": "Asia/Shanghai",
    "Chongqing": "Asia/Shanghai",
    "Wuhan": "Asia/Shanghai",
    "Qingdao": "Asia/Shanghai",
    "Seoul": "Asia/Seoul",
    "Busan": "Asia/Seoul",
}


def _failed_result(source_key: str, city: str, exc: BaseException) -> RunwayFetchResult:
    now = datetime.now(timezone.utc).isoformat()
    return RunwayFetchResult(
        source_key=source_key,
        status="fetch_failed",
        fetched_at_utc=now,
        latency_ms=0.0,
        records=(),
        error=f"{type(exc).__name__}: {exc}",
        metadata={"city": city},
    )


def _target_date(city: str, now_utc: datetime) -> str:
    timezone_name = CITY_TIMEZONES.get(city, "UTC")
    if city in CITY_TIMEZONES:
        return now_utc.astimezone(ZoneInfo(timezone_name)).date().isoformat()
    return city_local_date(city, now_utc).isoformat()


def _fetch_job(source: str, city: str, now_utc: datetime, settings: RunwayFetchSettings) -> RunwayFetchResult:
    target_date = _target_date(city, now_utc)
    try:
        if source == "amsc_awos":
            return fetch_amsc_awos_runway(city, settings=settings, target_date=target_date)
        if source == "amos":
            return fetch_amos_runway(city, settings=settings, target_date=target_date)
    except Exception as exc:  # noqa: BLE001
        return _failed_result(source, city, exc)
    return _failed_result(source, city, ValueError(f"unsupported runway source: {source}"))


def _row_with_hash(row: dict[str, Any], result: RunwayFetchResult) -> dict[str, Any]:
    out = dict(row)
    out.setdefault("source", result.source_key)
    out.setdefault("source_status", result.status)
    out.setdefault("fetched_at_utc", result.fetched_at_utc)
    out.setdefault("source_fetch_latency_sec", round(result.latency_ms / 1000.0, 3))
    out["payload_hash"] = stable_hash(
        {
            "source": out.get("source"),
            "city": out.get("city"),
            "station": out.get("station"),
            "runway": out.get("runway"),
            "observation_time_utc": out.get("observation_time_utc"),
            "point_temp_c": out.get("point_temp_c"),
            "tdz_temp_c": out.get("tdz_temp_c"),
            "mid_temp_c": out.get("mid_temp_c"),
            "end_temp_c": out.get("end_temp_c"),
            "raw_payload_hash": out.get("raw_payload_hash"),
        }
    )
    return out


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    now_utc = parse_now_utc(args.now_utc) if args.now_utc else datetime.now(timezone.utc)
    sources = tuple(args.sources or ["amsc_awos", "amos"])
    only_cities = set(args.cities or [])
    jobs: list[tuple[str, str]] = []
    for source in sources:
        for city in supported_runway_cities(source):
            if only_cities and city not in only_cities:
                continue
            jobs.append((source, city))
    settings = RunwayFetchSettings(timeout_sec=args.timeout_sec)
    results: list[RunwayFetchResult] = []
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
    rows = sorted(
        rows,
        key=lambda row: (
            str(row.get("city") or ""),
            str(row.get("source") or ""),
            str(row.get("station") or ""),
            str(row.get("runway") or ""),
        ),
    )
    source_statuses = {
        f"{result.source_key}:{result.metadata.get('city') or (result.records[0].get('city') if result.records else '')}": result.status
        for result in results
    }
    source_errors = {
        f"{result.source_key}:{result.metadata.get('city') or (result.records[0].get('city') if result.records else '')}": result.error
        for result in results
        if result.error
    }
    summary = {
        "status": "ok",
        "schema_version": "weather_runway_observations_payload_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "producer": "weather_data_feed_service.runway_observations",
        "rows": len(rows),
        "ok_sources": sum(1 for result in results if result.status == "ok"),
        "empty_sources": sum(1 for result in results if result.status == "empty"),
        "failed_sources": sum(1 for result in results if result.status in {"fetch_failed", "auth_failed", "source_error"}),
        "sources": list(sources),
        "cities": sorted({city for _source, city in jobs}),
        "source_statuses": source_statuses,
        "source_errors": source_errors,
    }
    return {**summary, "records": rows}


def write_outputs(payload: dict[str, Any], output_dir: Path) -> None:
    rows = list(payload.get("records") or [])
    write_latest_and_daily_jsonl(
        output_dir=output_dir,
        latest_payload=payload,
        rows=rows,
        jsonl_name="runway_observations.jsonl",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build runway-level airport observation rows.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--now-utc", default=None)
    parser.add_argument("--cities", nargs="*", default=None)
    parser.add_argument("--sources", nargs="*", default=["amsc_awos", "amos"])
    parser.add_argument("--timeout-sec", type=float, default=8.0)
    parser.add_argument("--max-workers", type=int, default=4)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_payload(args)
    write_outputs(payload, Path(args.output_dir))
    print(json.dumps({k: v for k, v in payload.items() if k != "records"}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
