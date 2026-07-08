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


DEFAULT_OUTPUT_DIR = DEFAULT_RUNTIME_ROOT / "output" / "high_frequency_observations"


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


def _jobs(source_names: list[str], city_names: list[str] | None) -> list[tuple[str, str]]:
    registry = supported_high_frequency_sources()
    selected_sources = source_names or list(registry)
    selected_cities = set(city_names or [])
    jobs: list[tuple[str, str]] = []
    for source in selected_sources:
        for city in registry.get(source, {}):
            if selected_cities and city not in selected_cities:
                continue
            jobs.append((source, city))
    return jobs


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
    jobs = _jobs(args.sources or [], args.cities)
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
        "sources": args.sources or sorted(supported_high_frequency_sources()),
        "cities": sorted({city for _source, city in jobs}),
        "source_statuses": source_statuses,
        "source_errors": source_errors,
    }
    return {**summary, "records": rows}


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_payload(args)
    write_outputs(payload, Path(args.output_dir))
    print(json.dumps({k: v for k, v in payload.items() if k != "records"}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
