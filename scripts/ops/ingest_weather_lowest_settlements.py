#!/usr/bin/env python3
"""Ingest Tmin (lowest-temperature) settlements from pm_history_lowest cache.

Why a separate path
-------------------
The existing `pm_history_settlements` ingest was built for Tmax only:
`settlement_outcome_id` is keyed by (source_system, city, target_date, bracket)
with no extreme dimension, and condition/market binding goes through the
(city, target_date, bracket) `signals` lookup — ambiguous once Tmax and Tmin
ladders coexist for the same city-date. Reusing it would either collide IDs or
bind a Tmin settlement to a Tmax market.

This ingest reads the `pm_history_lowest` cache written by
`backfill_weather_pm_history.py --extreme min` (whose files embed
condition_id/market_id per bracket from the Gamma event) and writes:

- `settlement_outcomes` rows under source_system='polymarket_api' (an unused
  namespace in that table today), so Tmax 'pm_history' rows and consumers
  keyed on (city, date, bracket) are unaffected;
- `settlements` rows keyed by the natural (target_date, condition_id,
  market_id, bracket) id only when the bracket carries condition+market.

Idempotent: INSERT OR IGNORE on both tables.

Run:
    python -m scripts.ops.ingest_weather_lowest_settlements \
        --db-path /Volumes/jrs/pm_agents/runtime/weather.db [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for entry in (str(ROOT / "src"), str(ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)
from src.strategies.weather_edge_v1.ids import make_settlement_id
from weather_dashboard.ingest.settlement_outcomes import (
    OUTCOME_COLUMNS,
    ensure_settlement_outcomes_schema,
    insert_settlement_outcome,
    parse_pm_history_filename,
    settlement_status,
    stored_final_price,
)

ROOT_PATH = Path(__file__).resolve().parents[2]
DEFAULT_PMH_DIR = ROOT_PATH / "runtime/weather_edge_v1/market_data/cache/pm_history_lowest"
SOURCE_SYSTEM = "polymarket_api"


def _outcome_row(source_path: Path, city: str, target_date: str, doc: dict, bracket: dict) -> dict:
    raw_price = float(bracket["final_price"])
    payload = {"closed": bracket.get("closed"), "extreme_kind": "min"}
    row = {
        "settlement_outcome_id": None,  # filled by caller-side helper contract below
        "source_system": SOURCE_SYSTEM,
        "source_path": str(source_path),
        "city": city,
        "target_date": target_date,
        "bracket": str(bracket.get("label") or "").strip(),
        "unit": doc.get("unit"),
        "condition_id": bracket.get("condition_id"),
        "market_id": bracket.get("market_id"),
        "token_id": str(bracket.get("token_id") or "") or None,
        "raw_final_price": raw_price,
        "final_price": stored_final_price(raw_price),
        "settlement_status": settlement_status(raw_price),
        "question": bracket.get("question"),
        "payload": json.dumps(payload, sort_keys=True),
        "source_payload_hash": None,
        "source_file_mtime_utc": None,
        "first_seen_at_utc": None,
        "available_at_utc": None,
        "pit_lineage_class": "late_backfill_first_seen_unknown",
        "producer_build_id": "ingest_weather_lowest_settlements_v1",
    }
    import hashlib

    raw_id = f"settlement_outcome|{SOURCE_SYSTEM}|{city}|{target_date}|{row['bracket']}|lowest"
    row["settlement_outcome_id"] = hashlib.sha256(raw_id.encode()).hexdigest()
    return row


def ingest(pmh_dir: Path, conn: sqlite3.Connection, dry_run: bool) -> dict:
    ensure_settlement_outcomes_schema(conn)
    stats = {"files": 0, "outcome_inserted": 0, "settlement_inserted": 0, "skipped_open": 0}

    for path in sorted(pmh_dir.glob("*.json")):
        parsed_name = parse_pm_history_filename(path.name)
        if parsed_name is None:
            continue
        city, target_date = parsed_name
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(doc, dict) or not doc.get("brackets"):
            continue
        stats["files"] += 1

        for bracket in doc["brackets"]:
            label = str(bracket.get("label") or "").strip()
            if not label or bracket.get("final_price") is None:
                continue
            if not bracket.get("closed"):
                stats["skipped_open"] += 1
                continue

            row = _outcome_row(path, city, target_date, doc, bracket)
            if not dry_run:
                stats["outcome_inserted"] += insert_settlement_outcome(conn, row)

            condition_id = bracket.get("condition_id")
            market_id = bracket.get("market_id")
            if condition_id and market_id:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO settlements (
                        settlement_id, target_date, condition_id, market_id,
                        bracket, token_id, final_price, settlement_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        make_settlement_id(
                            target_date=target_date,
                            condition_id=condition_id,
                            market_id=market_id,
                            bracket=label,
                        ),
                        target_date,
                        condition_id,
                        market_id,
                        label,
                        row["token_id"],
                        row["final_price"],
                        row["settlement_status"],
                    ),
                )
                if not dry_run:
                    stats["settlement_inserted"] += int(cur.rowcount or 0)

    if not dry_run:
        conn.commit()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default=str(ROOT / "runtime/weather.db"))
    parser.add_argument("--pmh-dir", default=str(DEFAULT_PMH_DIR))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    try:
        stats = ingest(Path(args.pmh_dir), conn, args.dry_run)
    finally:
        conn.close()
    print(json.dumps({"pmh_dir": args.pmh_dir, "dry_run": args.dry_run, "stats": stats}, indent=2))


if __name__ == "__main__":
    main()
