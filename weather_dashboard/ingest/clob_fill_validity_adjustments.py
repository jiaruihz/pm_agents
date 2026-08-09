"""Append-only exclusions for recovered CLOB fills disproved by exchange evidence."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VALIDITY_ADJUSTMENT_PATH = (
    ROOT / "runtime" / "weather_edge_v1" / "clob_fill_validity_adjustments.jsonl"
)


def append_validity_adjustment(row: dict[str, Any], path: str | Path) -> bool:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    adjustment_id = str(row["adjustment_id"])
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line).get("adjustment_id") == adjustment_id:
                return False
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return True


def import_validity_adjustments(
    conn: sqlite3.Connection,
    path: str | Path = DEFAULT_VALIDITY_ADJUSTMENT_PATH,
) -> int:
    target = Path(path)
    if not target.exists():
        return 0
    inserted = 0
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO fill_validity_adjustments (
              adjustment_id, fill_id, effective_status, reason,
              evidence_json, source_path, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, strftime('%Y-%m-%dT%H:%M:%SZ','now')))
            """,
            (
                row["adjustment_id"],
                row["fill_id"],
                row["effective_status"],
                row["reason"],
                json.dumps(row.get("evidence") or {}, sort_keys=True),
                row.get("source_path") or str(target),
                row.get("created_at_utc"),
            ),
        )
        inserted += conn.total_changes - before
    conn.commit()
    return inserted
