#!/usr/bin/env python3
"""Recover CLOB fills from a previous clob_fill_sync log.

This is a repair tool for the case where a DB rebuild removed live fills and
the only local evidence is an older clob_fill_sync log. It matches the logged
execution_id prefix back to the canonical orders table, inserts the fill, and
writes the same fill to the persistent CLOB fill cache.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from weather_dashboard.ingest.clob_fill_cache import append_cached_fill


LINE_RE = re.compile(
    r"^(?P<ts>\S+) .*Recorded fill .* execution_id=(?P<prefix>[0-9a-f]+)\.\.\. "
    r"shares=(?P<shares>[0-9.]+) price=(?P<price>[0-9.]+)"
)


def make_fill_id(execution_id: str, order_id: str) -> str:
    raw = f"{execution_id}|clob_fill|{order_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default="runtime/weather.db")
    parser.add_argument("--log-path", default="runtime/_dashboard_logs/clob_fill_sync.log")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    inserted = 0
    skipped = 0
    ambiguous = 0

    for line in Path(args.log_path).read_text(encoding="utf-8").splitlines():
        match = LINE_RE.search(line)
        if not match:
            continue
        prefix = match.group("prefix")
        candidates = conn.execute(
            """
            SELECT
                o.execution_id,
                COALESCE(json_extract(o.exchange_response, '$.place.orderID'), o.order_id) AS clob_order_id
            FROM orders o
            WHERE o.venue='polymarket_clob'
              AND o.status='submitted'
              AND o.execution_id LIKE ? || '%'
            """,
            (prefix,),
        ).fetchall()
        if len(candidates) != 1:
            ambiguous += 1
            continue
        row = candidates[0]
        execution_id = row["execution_id"]
        order_id = row["clob_order_id"]
        fill_id = make_fill_id(execution_id, order_id)
        filled_at_utc = match.group("ts")
        filled_shares = float(match.group("shares"))
        filled_price = float(match.group("price"))
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO fills
                (fill_id, execution_id, order_id, filled_shares, filled_price,
                 fees_usd, status, filled_at_utc, created_at_utc)
            VALUES (?, ?, ?, ?, ?, 0.0, 'filled', ?, ?)
            """,
            (
                fill_id,
                execution_id,
                order_id,
                filled_shares,
                filled_price,
                filled_at_utc,
                filled_at_utc,
            ),
        )
        if conn.total_changes > before:
            inserted += 1
            append_cached_fill(
                {
                    "fill_id": fill_id,
                    "execution_id": execution_id,
                    "order_id": order_id,
                    "filled_shares": filled_shares,
                    "filled_price": filled_price,
                    "fees_usd": 0.0,
                    "filled_at_utc": filled_at_utc,
                    "created_at_utc": filled_at_utc,
                }
            )
        else:
            skipped += 1
    conn.commit()
    print({"inserted": inserted, "skipped_existing": skipped, "ambiguous_or_missing": ambiguous})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
