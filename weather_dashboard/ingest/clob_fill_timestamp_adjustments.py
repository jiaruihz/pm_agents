"""Durable append-only timestamp corrections for immutable CLOB fills."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TIMESTAMP_ADJUSTMENT_PATH = (
    ROOT / "runtime" / "weather_edge_v1" / "clob_fill_timestamp_adjustments.jsonl"
)


def iter_timestamp_adjustments(
    path: str | Path = DEFAULT_TIMESTAMP_ADJUSTMENT_PATH,
) -> Iterable[dict[str, Any]]:
    journal_path = Path(path)
    if not journal_path.exists():
        return
    with journal_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def append_timestamp_adjustment(
    row: dict[str, Any],
    path: str | Path = DEFAULT_TIMESTAMP_ADJUSTMENT_PATH,
) -> bool:
    """Append one adjustment once, keyed by deterministic adjustment_id."""
    adjustment_id = str(row["adjustment_id"])
    if any(
        str(existing.get("adjustment_id")) == adjustment_id
        for existing in iter_timestamp_adjustments(path)
    ):
        return False
    journal_path = Path(path)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    with journal_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
    return True


def import_timestamp_adjustments(
    conn: sqlite3.Connection,
    path: str | Path = DEFAULT_TIMESTAMP_ADJUSTMENT_PATH,
) -> int:
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "fill_timestamp_adjustments" not in tables:
        return 0
    inserted = 0
    for row in iter_timestamp_adjustments(path):
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO fill_timestamp_adjustments (
                adjustment_id, fill_id, corrected_filled_at_utc, timestamp_source,
                timestamp_evidence_class, evidence_json, source_path, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["adjustment_id"],
                row["fill_id"],
                row["corrected_filled_at_utc"],
                row["timestamp_source"],
                row["timestamp_evidence_class"],
                json.dumps(row.get("evidence") or {}, sort_keys=True),
                str(path),
                row["created_at_utc"],
            ),
        )
        inserted += conn.total_changes - before
    conn.commit()
    return inserted
