#!/usr/bin/env python3
"""Append complete raw market-book ladders to the Tmax V2 canonical tables."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from weather_data_feed.market_book_ladder_history import iter_market_book_ladders


def _hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _snapshot_rows(meta: dict[str, Any], records: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ordered = sorted(records, key=lambda row: (str(row["bracket"]), str(row["condition_id"])))
    signature_rows = [
        {"bracket": str(row["bracket"]), "condition_id": row["condition_id"], "market_id": row["market_id"]}
        for row in ordered
    ]
    payload_hash = _hash({"meta": meta, "records": ordered})
    snapshot_id = _hash(
        {"table": "tmax_v2_ladder_snapshots", "payload": payload_hash, "city": meta["city"], "target_date": meta["target_date"]}
    )
    first = ordered[0]
    snapshot = {
        "ladder_snapshot_id": snapshot_id,
        "source_system": meta["source_system"],
        "source_path": meta["source_path"],
        "source_snapshot_ts_utc": meta["source_snapshot_ts_utc"],
        "available_at_utc": meta["available_at_utc"],
        "source_payload_hash": payload_hash,
        "city": meta["city"],
        "target_date": meta["target_date"],
        "event_slug": meta["event_slug"],
        "event_identity": meta["event_identity"],
        "market_unit": first.get("unit"),
        "settlement_source_class": first.get("settlement_source_class"),
        "market_timezone": first.get("timezone_name"),
        "market_utc_offset_seconds": first.get("forecast_utc_offset_seconds"),
        "market_metadata_source_json": json.dumps(
            {"basis": "weather_data_feed.source_profiles", "raw_owner": "market_books/batches"}, sort_keys=True
        ),
        "market_metadata_missing_reason": None,
        "absolute_ladder_signature": _hash(signature_rows),
        "rung_count": len(ordered),
        "complete_rung_count": len(ordered),
        "completeness_status": "complete",
        "lineage_status": "pit_verified_capture",
    }
    rungs = []
    for row in ordered:
        bracket = str(row["bracket"])
        output = {
            "rung_quote_id": _hash({"table": "tmax_v2_ladder_rung_quotes", "snapshot": snapshot_id, "bracket": bracket}),
            "ladder_snapshot_id": snapshot_id,
            "absolute_bracket_identity": bracket,
            "condition_id": row.get("condition_id"),
            "market_id": row.get("market_id"),
            "question": None,
        }
        for side in ("yes", "no"):
            output.update(
                {
                    f"{side}_token_id": row.get(f"{side}_token_id"),
                    f"{side}_direct_bid": _number(row.get(f"{side}_best_bid")),
                    f"{side}_direct_ask": _number(row.get(f"{side}_best_ask")),
                    f"{side}_direct_bid_size": _number(row.get(f"{side}_bid_size")),
                    f"{side}_direct_ask_size": _number(row.get(f"{side}_ask_size")),
                    f"{side}_direct_depth_bid_5c": _number(row.get(f"{side}_depth_bid_5c")),
                    f"{side}_direct_depth_ask_5c": _number(row.get(f"{side}_depth_ask_5c")),
                    f"{side}_direct_depth_bid_10c": _number(row.get(f"{side}_depth_bid_10c")),
                    f"{side}_direct_depth_ask_10c": _number(row.get(f"{side}_depth_ask_10c")),
                    f"{side}_book_status": row.get(f"{side}_book_status"),
                    f"{side}_book_fetched_at_utc": row.get(f"{side}_book_fetched_at_utc"),
                }
            )
        output["source_record_hash"] = _hash(row)
        rungs.append(output)
    return snapshot, rungs


def _insert(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    columns = list(rows[0])
    marks = ",".join("?" for _ in columns)
    statement = f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({marks})"
    before = conn.total_changes
    conn.executemany(
        statement,
        ([row.get(column) for column in columns] for row in rows),
    )
    return int(conn.total_changes - before)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--market-book-batch-dir", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument(
        "--city",
        default="",
        help="Optional exact city filter for a bounded incremental materialization.",
    )
    parser.add_argument("--summary-json", default="")
    args = parser.parse_args()
    snapshots: list[dict[str, Any]] = []
    rungs: list[dict[str, Any]] = []
    for meta, records in iter_market_book_ladders(
        Path(args.market_book_batch_dir), str(args.start_date), str(args.end_date)
    ):
        if args.city and str(meta.get("city")) != str(args.city):
            continue
        snapshot, quotes = _snapshot_rows(meta, records)
        snapshots.append(snapshot)
        rungs.extend(quotes)
    conn = sqlite3.connect(args.db)
    try:
        apply_schema_canonical(conn)
        inserted = {
            "ladder_snapshots": _insert(conn, "tmax_v2_ladder_snapshots", snapshots),
            "ladder_rung_quotes": _insert(conn, "tmax_v2_ladder_rung_quotes", rungs),
        }
        conn.commit()
    finally:
        conn.close()
    summary = {
        "schema_version": "market_book_ladder_canonical_materialization_v1",
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "range": [args.start_date, args.end_date],
        "city": args.city or None,
        "source": str(Path(args.market_book_batch_dir).resolve()),
        "source_ladders": len(snapshots),
        "source_rungs": len(rungs),
        "inserted": inserted,
    }
    if args.summary_json:
        output = Path(args.summary_json)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
