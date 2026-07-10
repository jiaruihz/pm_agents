"""Persistent cache for recovered Polymarket CLOB fills.

The canonical DB is rebuildable, but historical CLOB fills are not present in
live order JSONL files. Keep each recovered fill as JSONL so a DB rebuild can
replay known fills before querying external APIs for new ones.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_PATH = ROOT / "runtime" / "weather_edge_v1" / "clob_fills.jsonl"


def iter_cached_fills(path: str | Path = DEFAULT_CACHE_PATH) -> Iterable[dict[str, Any]]:
    cache_path = Path(path)
    if not cache_path.exists():
        return
    with cache_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def append_cached_fill(row: dict[str, Any], path: str | Path = DEFAULT_CACHE_PATH) -> None:
    cache_path = Path(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def import_cached_fills(conn: sqlite3.Connection, path: str | Path = DEFAULT_CACHE_PATH) -> int:
    inserted = 0
    seen_physical_keys: set[tuple[str, str, float, float]] = set()
    for row in iter_cached_fills(path):
        physical_key = (
            str(row.get("order_id") or ""),
            str(row.get("filled_at_utc") or ""),
            round(float(row["filled_shares"]), 6),
            round(float(row["filled_price"]), 6),
        )
        if physical_key in seen_physical_keys:
            continue
        seen_physical_keys.add(physical_key)
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO fills
                (fill_id, execution_id, order_id, filled_shares, filled_price,
                 fees_usd, status, filled_at_utc, created_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, 'filled', ?, ?)
            """,
            (
                row["fill_id"],
                row["execution_id"],
                row["order_id"],
                float(row["filled_shares"]),
                float(row["filled_price"]),
                float(row.get("fees_usd") or 0.0),
                row.get("filled_at_utc"),
                row.get("created_at_utc") or row.get("filled_at_utc"),
            ),
        )
        inserted += conn.total_changes - before
    conn.commit()
    return inserted
