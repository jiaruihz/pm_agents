#!/usr/bin/env python3
"""Collect a complete public activity history for an external weather wallet.

The Polymarket Data API exposes at most 5,500 rows through offset pagination.
This collector avoids that ceiling by querying bounded UTC windows and
recursively splitting any saturated window.  It stores resumable daily raw
checkpoints, then derives a deduplicated weather-only activity file and fetches
the related position and Gamma event metadata needed for whole-ladder replay.

This is an external public-data snapshot.  It is not part of the project's
canonical fact_trades lineage and cannot recover private signals, unfilled
orders, or the original order-post timestamp.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Iterable

import requests

from research_external_wallet_strategy_v1 import (
    DATA_API,
    _activity_key,
    _event_slug,
    _is_weather,
)
from research_wallet_event_portfolios_v1 import GAMMA_API


USER_AGENT = "pm-agent-external-wallet-history-collector/1.0"
TRANSIENT_STATUS = {403, 408, 425, 429, 500, 502, 503, 504}
ACTIVITY_PAGE_SIZE = 500
ACTIVITY_MAX_ROWS = 5_500
_THREAD_LOCAL = threading.local()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def session() -> requests.Session:
    current = getattr(_THREAD_LOCAL, "session", None)
    if current is None:
        current = requests.Session()
        current.headers.update({"Accept": "application/json", "User-Agent": USER_AGENT})
        _THREAD_LOCAL.session = current
    return current


def get_list(
    base_url: str,
    path: str,
    params: dict[str, Any],
    *,
    attempts: int = 8,
) -> list[dict[str, Any]]:
    last_error = ""
    for attempt in range(attempts):
        try:
            response = session().get(
                f"{base_url}{path}",
                params=params,
                timeout=(10, 45),
            )
            if response.status_code == 200:
                payload = response.json()
                if not isinstance(payload, list):
                    raise RuntimeError(f"expected list from {path}, got {type(payload).__name__}")
                return [row for row in payload if isinstance(row, dict)]
            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            if response.status_code not in TRANSIENT_STATUS:
                response.raise_for_status()
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt + 1 < attempts:
            retry_after = 0.6 * (attempt + 1)
            time.sleep(min(retry_after, 6.0))
    raise RuntimeError(f"request exhausted retries: {path} params={params}: {last_error}")


def json_bytes(row: dict[str, Any]) -> bytes:
    return (
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def atomic_write_jsonl_gz(path: Path, rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    count = 0
    digest = hashlib.sha256()
    with temporary.open("wb") as raw_handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_handle, mtime=0) as handle:
            for row in rows:
                encoded = json_bytes(row)
                digest.update(encoded)
                handle.write(encoded)
                count += 1
    os.replace(temporary, path)
    return {
        "rows": count,
        "uncompressed_sha256": digest.hexdigest(),
        "compressed_bytes": path.stat().st_size,
    }


def read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def activity_rows(
    wallet: str,
    start_ts: int,
    end_ts: int,
    *,
    depth: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Fetch one half-open time window, splitting when offset pagination saturates."""
    rows: list[dict[str, Any]] = []
    pages = 0
    for offset in range(0, ACTIVITY_MAX_ROWS, ACTIVITY_PAGE_SIZE):
        page = get_list(
            DATA_API,
            "/activity",
            {
                "user": wallet,
                "start": start_ts,
                "end": end_ts,
                "limit": ACTIVITY_PAGE_SIZE,
                "offset": offset,
                "sortDirection": "ASC",
            },
        )
        pages += 1
        rows.extend(page)
        if len(page) < ACTIVITY_PAGE_SIZE:
            break

    if len(rows) < ACTIVITY_MAX_ROWS:
        return rows, {
            "requests": pages,
            "split_nodes": 0,
            "leaf_windows": 1,
            "max_split_depth": depth,
        }

    if end_ts - start_ts <= 1:
        raise RuntimeError(
            f"activity exceeds {ACTIVITY_MAX_ROWS} rows inside one second: "
            f"wallet={wallet} second={start_ts}"
        )
    midpoint = (start_ts + end_ts) // 2
    left, left_stats = activity_rows(wallet, start_ts, midpoint, depth=depth + 1)
    right, right_stats = activity_rows(wallet, midpoint, end_ts, depth=depth + 1)
    return left + right, {
        "requests": pages + left_stats["requests"] + right_stats["requests"],
        "split_nodes": 1 + left_stats["split_nodes"] + right_stats["split_nodes"],
        "leaf_windows": left_stats["leaf_windows"] + right_stats["leaf_windows"],
        "max_split_depth": max(
            depth,
            left_stats["max_split_depth"],
            right_stats["max_split_depth"],
        ),
    }


def earliest_activity_timestamp(wallet: str) -> int:
    rows = get_list(
        DATA_API,
        "/activity",
        {
            "user": wallet,
            "limit": 1,
            "offset": 0,
            "sortDirection": "ASC",
        },
    )
    if not rows:
        raise RuntimeError(f"wallet has no public activity: {wallet}")
    timestamp = int(rows[0].get("timestamp") or 0)
    if timestamp <= 0:
        raise RuntimeError(f"earliest activity has no valid timestamp: {rows[0]}")
    return timestamp


def day_checkpoint_paths(output: Path, day_start: datetime) -> tuple[Path, Path]:
    stem = day_start.date().isoformat()
    directory = output / "daily_activity"
    return directory / f"{stem}.jsonl.gz", directory / f"{stem}.meta.json"


def collect_day(
    wallet: str,
    output: Path,
    day_start: datetime,
    final_end: datetime,
) -> dict[str, Any]:
    data_path, meta_path = day_checkpoint_paths(output, day_start)
    day_end = min(day_start + timedelta(days=1), final_end)
    if data_path.exists() and meta_path.exists():
        saved = json.loads(meta_path.read_text(encoding="utf-8"))
        if (
            saved.get("complete")
            and saved.get("wallet") == wallet
            and saved.get("window_start_utc") == day_start.isoformat()
            and saved.get("window_end_utc") == day_end.isoformat()
        ):
            return {**saved, "resumed": True}

    rows, stats = activity_rows(
        wallet,
        int(day_start.timestamp()),
        int(day_end.timestamp()),
    )
    deduplicated = list({_activity_key(row): row for row in rows}.values())
    deduplicated.sort(
        key=lambda row: (
            int(row.get("timestamp") or 0),
            str(row.get("transactionHash") or ""),
            str(row.get("type") or ""),
        )
    )
    file_stats = atomic_write_jsonl_gz(data_path, deduplicated)
    weather_count = sum(_is_weather(row) for row in deduplicated)
    meta = {
        "complete": True,
        "wallet": wallet,
        "window_start_utc": day_start.isoformat(),
        "window_end_utc": day_end.isoformat(),
        "api_rows_before_dedupe": len(rows),
        "raw_rows": len(deduplicated),
        "duplicate_rows_removed": len(rows) - len(deduplicated),
        "weather_rows": weather_count,
        "requests": stats["requests"],
        "split_nodes": stats["split_nodes"],
        "leaf_windows": stats["leaf_windows"],
        "max_split_depth": stats["max_split_depth"],
        "file": str(data_path.relative_to(output)),
        **file_stats,
    }
    atomic_write_json(meta_path, meta)
    return {**meta, "resumed": False}


def collect_days(
    wallet: str,
    output: Path,
    start: datetime,
    end: datetime,
    *,
    workers: int,
) -> list[dict[str, Any]]:
    days: list[datetime] = []
    cursor = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    while cursor < end:
        days.append(cursor)
        cursor += timedelta(days=1)

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(collect_day, wallet, output, day, end): day for day in days
        }
        completed = 0
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            completed += 1
            if completed == 1 or completed % 10 == 0 or completed == len(days):
                raw_rows = sum(int(row["raw_rows"]) for row in results)
                weather_rows = sum(int(row["weather_rows"]) for row in results)
                print(
                    f"activity days {completed}/{len(days)}; "
                    f"raw={raw_rows:,}; weather={weather_rows:,}",
                    flush=True,
                )
    return sorted(results, key=lambda row: row["window_start_utc"])


def collect_positions_by_market(
    wallet: str,
    conditions: list[str],
    *,
    workers: int,
) -> tuple[list[dict[str, Any]], int]:
    """Fetch positions by condition batches, avoiding the endpoint's 10k offset cap."""

    batches = [conditions[start : start + 60] for start in range(0, len(conditions), 60)]

    def fetch(batch: list[str]) -> list[dict[str, Any]]:
        rows = get_list(
            DATA_API,
            "/positions",
            {
                "user": wallet,
                "market": ",".join(batch),
                "limit": 500,
                "offset": 0,
            },
        )
        if len(rows) >= 500:
            if len(batch) == 1:
                raise RuntimeError(f"one condition returned >=500 positions: {batch[0]}")
            midpoint = len(batch) // 2
            return fetch(batch[:midpoint]) + fetch(batch[midpoint:])
        return rows

    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch, batch): batch for batch in batches}
        completed = 0
        for future in as_completed(futures):
            rows.extend(future.result())
            completed += 1
            if completed == 1 or completed % 50 == 0 or completed == len(batches):
                print(
                    f"positions market batches {completed}/{len(batches)}; rows={len(rows):,}",
                    flush=True,
                )
    deduplicated = {
        (
            str(row.get("conditionId") or ""),
            str(row.get("asset") or ""),
            str(row.get("outcome") or ""),
        ): row
        for row in rows
    }
    return list(deduplicated.values()), len(batches)


def collect_closed_positions(wallet: str) -> list[dict[str, Any]]:
    """Fetch weather closed positions; this endpoint is discovery evidence only."""
    rows: list[dict[str, Any]] = []
    for offset in range(0, 10_000, 50):
        page = get_list(
            DATA_API,
            "/closed-positions",
            {
                "user": wallet,
                "title": "highest temperature",
                "limit": 50,
                "offset": offset,
            },
        )
        rows.extend(page)
        if len(page) < 50:
            return [row for row in rows if _is_weather(row)]
    raise RuntimeError("closed-positions reached local 10,000-row safety limit")


def safe_slug(slug: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", slug)
    return cleaned[:220]


def fetch_event_checkpoint(output: Path, slug: str) -> dict[str, Any]:
    path = output / "event_metadata_by_slug" / f"{safe_slug(slug)}.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("event_slug") == slug and "metadata" in payload:
            return payload

    rows = get_list(GAMMA_API, "/events", {"slug": slug})
    payload = {
        "event_slug": slug,
        "fetched_at_utc": utc_now().isoformat(),
        "metadata": rows[0] if rows else None,
    }
    atomic_write_json(path, payload)
    return payload


def collect_event_metadata(
    output: Path,
    slugs: list[str],
    *,
    workers: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_event_checkpoint, output, slug): slug for slug in slugs
        }
        completed = 0
        for future in as_completed(futures):
            rows.append(future.result())
            completed += 1
            if completed == 1 or completed % 100 == 0 or completed == len(slugs):
                missing = sum(row.get("metadata") is None for row in rows)
                print(
                    f"event metadata {completed}/{len(slugs)}; missing={missing}",
                    flush=True,
                )
    return sorted(rows, key=lambda row: row["event_slug"])


def leaderboard(wallet: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for period in ("ALL", "MONTH", "WEEK", "DAY"):
        rows = get_list(
            DATA_API,
            "/v1/leaderboard",
            {
                "category": "WEATHER",
                "timePeriod": period,
                "orderBy": "PNL",
                "user": wallet,
                "limit": 1,
            },
        )
        result[period.lower()] = rows[0] if rows else None
    return result


def file_record(path: Path, output: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return {
        "path": str(path.relative_to(output)),
        "bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }


def event_slug_set(rows: Iterable[dict[str, Any]]) -> set[str]:
    return {slug for row in rows if (slug := _event_slug(row))}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--start",
        help="UTC ISO date/datetime. Default: earliest public wallet activity.",
    )
    parser.add_argument(
        "--end",
        help="UTC ISO date/datetime. Default: collection start time.",
    )
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    wallet = args.wallet.lower()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    snapshot_start = utc_now()
    earliest_ts = earliest_activity_timestamp(wallet)
    earliest = datetime.fromtimestamp(earliest_ts, timezone.utc)
    start = (
        datetime.fromisoformat(args.start).astimezone(timezone.utc)
        if args.start
        else earliest
    )
    end = (
        datetime.fromisoformat(args.end).astimezone(timezone.utc)
        if args.end
        else snapshot_start
    )
    if start >= end:
        raise ValueError(f"start must be before end: {start=} {end=}")

    print(
        f"wallet={wallet}; earliest={earliest.isoformat()}; "
        f"collect={start.isoformat()}..{end.isoformat()}",
        flush=True,
    )
    day_meta = collect_days(
        wallet,
        output,
        start,
        end,
        workers=max(1, args.workers),
    )

    all_rows: dict[tuple[Any, ...], dict[str, Any]] = {}
    for meta in day_meta:
        data_path = output / str(meta["file"])
        for row in read_jsonl_gz(data_path):
            all_rows[_activity_key(row)] = row
    activity = sorted(
        all_rows.values(),
        key=lambda row: (
            int(row.get("timestamp") or 0),
            str(row.get("transactionHash") or ""),
            str(row.get("type") or ""),
        ),
    )
    weather_activity = [row for row in activity if _is_weather(row)]
    weather_activity_path = output / "weather_activity.jsonl.gz"
    weather_activity_stats = atomic_write_jsonl_gz(
        weather_activity_path,
        weather_activity,
    )
    print(
        f"combined activity raw={len(activity):,}; weather={len(weather_activity):,}",
        flush=True,
    )

    conditions = sorted(
        {
            str(row.get("conditionId"))
            for row in weather_activity
            if row.get("conditionId")
        }
    )
    positions_weather, position_batches = collect_positions_by_market(
        wallet,
        conditions,
        workers=max(1, args.workers),
    )
    positions_weather = [row for row in positions_weather if _is_weather(row)]
    positions_path = output / "weather_open_positions.jsonl.gz"
    positions_stats = atomic_write_jsonl_gz(positions_path, positions_weather)

    closed_positions = collect_closed_positions(wallet)
    closed_path = output / "weather_closed_positions.jsonl.gz"
    closed_stats = atomic_write_jsonl_gz(closed_path, closed_positions)
    print(
        f"positions open={len(positions_weather):,}; closed={len(closed_positions):,}",
        flush=True,
    )

    slugs = sorted(
        event_slug_set(weather_activity)
        | event_slug_set(positions_weather)
        | event_slug_set(closed_positions)
    )
    metadata_wrappers = collect_event_metadata(
        output,
        slugs,
        workers=max(1, min(args.workers, 16)),
    )
    metadata_path = output / "event_metadata.jsonl.gz"
    metadata_stats = atomic_write_jsonl_gz(metadata_path, metadata_wrappers)

    leaderboard_path = output / "leaderboard.json"
    atomic_write_json(
        leaderboard_path,
        {
            "snapshot_utc": utc_now().isoformat(),
            "wallet": wallet,
            "weather": leaderboard(wallet),
        },
    )

    activity_types = Counter(str(row.get("type") or "UNKNOWN") for row in weather_activity)
    sides = Counter(str(row.get("side") or "NONE") for row in weather_activity)
    transactions = {
        str(row.get("transactionHash"))
        for row in weather_activity
        if row.get("transactionHash")
    }
    missing_slugs = [
        row["event_slug"] for row in metadata_wrappers if row.get("metadata") is None
    ]
    metadata_market_count = sum(
        len(row["metadata"].get("markets") or [])
        for row in metadata_wrappers
        if isinstance(row.get("metadata"), dict)
    )
    files = [
        file_record(weather_activity_path, output),
        file_record(positions_path, output),
        file_record(closed_path, output),
        file_record(metadata_path, output),
        file_record(leaderboard_path, output),
    ]
    manifest = {
        "schema_version": "external_wallet_weather_history_v1",
        "snapshot_started_utc": snapshot_start.isoformat(),
        "snapshot_completed_utc": utc_now().isoformat(),
        "wallet": wallet,
        "coverage": {
            "earliest_public_wallet_activity_utc": earliest.isoformat(),
            "query_start_utc": day_meta[0]["window_start_utc"],
            "query_end_utc": end.isoformat(),
            "utc_day_windows": len(day_meta),
            "complete_day_windows": sum(bool(row.get("complete")) for row in day_meta),
            "resumed_day_windows": sum(bool(row.get("resumed")) for row in day_meta),
            "api_requests": sum(int(row["requests"]) for row in day_meta),
            "recursive_split_nodes": sum(int(row["split_nodes"]) for row in day_meta),
            "maximum_split_depth": max(
                (int(row["max_split_depth"]) for row in day_meta),
                default=0,
            ),
            "raw_activity_rows_before_cross_day_dedupe": sum(
                int(row["raw_rows"]) for row in day_meta
            ),
            "raw_activity_rows_after_cross_day_dedupe": len(activity),
            "weather_activity_rows": len(weather_activity),
            "weather_events": len(event_slug_set(weather_activity)),
            "weather_conditions": len(conditions),
            "weather_transactions": len(transactions),
            "weather_activity_types": dict(sorted(activity_types.items())),
            "weather_activity_sides": dict(sorted(sides.items())),
        },
        "supporting_endpoints": {
            "open_positions_market_batches": position_batches,
            "open_weather_position_rows": len(positions_weather),
            "closed_weather_position_rows": len(closed_positions),
            "event_slugs_union": len(slugs),
            "event_metadata_rows": len(metadata_wrappers),
            "event_metadata_missing": len(missing_slugs),
            "event_metadata_missing_slugs": missing_slugs,
            "gamma_market_rows": metadata_market_count,
        },
        "artifacts": files,
        "artifact_row_stats": {
            "weather_activity": weather_activity_stats,
            "weather_open_positions": positions_stats,
            "weather_closed_positions": closed_stats,
            "event_metadata": metadata_stats,
        },
        "daily_checkpoint_directory": "daily_activity",
        "event_metadata_checkpoint_directory": "event_metadata_by_slug",
        "completeness_notes": [
            "Every activity leaf window returned fewer than 5,500 rows; saturated windows were recursively split.",
            "Daily raw checkpoints retain all public activity rows, including non-weather activity; weather_activity is the client-side title-filtered derivative.",
            "Adjacent API windows may share boundary rows; activity identity deduplication is applied per day and globally.",
            "Closed positions are supporting discovery evidence only and are not used as a PnL source.",
            "Public APIs do not expose private signals, unfilled/cancelled orders, maker intent, or original order-post timestamps.",
        ],
    }
    manifest_path = output / "manifest.json"
    atomic_write_json(manifest_path, manifest)
    print(
        f"complete: events={len(slugs):,}; metadata_missing={len(missing_slugs)}; "
        f"manifest={manifest_path}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
