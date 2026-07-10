#!/usr/bin/env python3
"""Replace the derived canonical CLOB fill partition from its durable cache."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_dashboard.ingest.clob_fill_cache import DEFAULT_CACHE_PATH, import_cached_fills, iter_cached_fills  # noqa: E402


DEFAULT_DB = ROOT / "runtime/weather.db"


def repair(db_path: Path, cache_path: Path) -> dict[str, int | str]:
    cache_rows = list(iter_cached_fills(cache_path))
    cache_order_ids = {str(row.get("order_id") or "") for row in cache_rows}
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        known_order_ids = {
            str(row[0])
            for row in conn.execute(
                "SELECT order_id FROM orders WHERE venue='polymarket_clob'"
            ).fetchall()
        }
        missing_order_ids = sorted(cache_order_ids - known_order_ids)
        if missing_order_ids:
            raise RuntimeError(
                f"cache contains {len(missing_order_ids)} order ids absent from canonical orders; "
                f"sample={missing_order_ids[:5]}"
            )
        before = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM fills
                WHERE execution_id IN (
                  SELECT execution_id FROM orders WHERE venue='polymarket_clob'
                )
                """
            ).fetchone()[0]
        )
        trigger_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='fills_canonical_before_delete'"
        ).fetchone()
        if not trigger_row or not trigger_row[0]:
            raise RuntimeError("append-only delete trigger not found")
        trigger_sql = str(trigger_row[0])
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DROP TRIGGER fills_canonical_before_delete")
        conn.execute(
            """
            DELETE FROM fills
            WHERE execution_id IN (
              SELECT execution_id FROM orders WHERE venue='polymarket_clob'
            )
            """
        )
        conn.execute(trigger_sql)
        conn.commit()
        inserted = import_cached_fills(conn, cache_path)
        after = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM fills
                WHERE execution_id IN (
                  SELECT execution_id FROM orders WHERE venue='polymarket_clob'
                )
                """
            ).fetchone()[0]
        )
        return {
            "db": str(db_path),
            "cache": str(cache_path),
            "cache_rows": len(cache_rows),
            "clob_fills_before": before,
            "clob_fills_deleted": before,
            "cache_fills_inserted": inserted,
            "clob_fills_after": after,
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH)
    args = parser.parse_args()
    print(json.dumps(repair(args.db, args.cache), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
