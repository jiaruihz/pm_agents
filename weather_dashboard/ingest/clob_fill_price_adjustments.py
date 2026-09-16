"""Durable append-only price corrections for immutable CLOB fills."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRICE_ADJUSTMENT_PATH = (
    ROOT / "runtime" / "weather_edge_v1" / "clob_fill_price_adjustments.jsonl"
)


def iter_price_adjustments(
    path: str | Path = DEFAULT_PRICE_ADJUSTMENT_PATH,
) -> Iterable[dict[str, Any]]:
    journal_path = Path(path)
    if not journal_path.exists():
        return
    with journal_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def import_price_adjustments(
    conn: sqlite3.Connection,
    path: str | Path = DEFAULT_PRICE_ADJUSTMENT_PATH,
) -> int:
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "fill_price_adjustments" not in tables:
        return 0
    inserted = 0
    for row in iter_price_adjustments(path):
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO fill_price_adjustments (
                adjustment_id, fill_id, corrected_filled_price, price_source,
                price_evidence_class, evidence_json, source_path, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["adjustment_id"],
                row["fill_id"],
                float(row["corrected_filled_price"]),
                row["price_source"],
                row["price_evidence_class"],
                json.dumps(row.get("evidence") or {}, sort_keys=True),
                str(path),
                row["created_at_utc"],
            ),
        )
        inserted += conn.total_changes - before
    conn.commit()
    return inserted
