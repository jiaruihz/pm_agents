"""Durable append-only fee corrections for immutable CLOB fills."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FEE_ADJUSTMENT_PATH = (
    ROOT / "runtime" / "weather_edge_v1" / "clob_fill_fee_adjustments.jsonl"
)


def ensure_fee_adjustment_schema(conn: sqlite3.Connection) -> None:
    """Create the durable append-only fee evidence layer when absent."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS fill_fee_adjustments (
            adjustment_id TEXT PRIMARY KEY,
            fill_id TEXT NOT NULL,
            fee_delta_usd REAL NOT NULL,
            fee_source TEXT NOT NULL,
            fee_evidence_class TEXT NOT NULL,
            transaction_hash TEXT,
            fee_rate REAL,
            market_fee_metadata_json TEXT NOT NULL DEFAULT '{}',
            evidence_json TEXT NOT NULL DEFAULT '{}',
            source_path TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,
            FOREIGN KEY (fill_id) REFERENCES fills(fill_id)
        );
        CREATE INDEX IF NOT EXISTS idx_fill_fee_adjustments_fill_id
            ON fill_fee_adjustments(fill_id);
        CREATE TRIGGER IF NOT EXISTS trg_fill_fee_adjustments_no_update
        BEFORE UPDATE ON fill_fee_adjustments
        BEGIN
            SELECT RAISE(ABORT, 'fill_fee_adjustments is append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS trg_fill_fee_adjustments_no_delete
        BEFORE DELETE ON fill_fee_adjustments
        BEGIN
            SELECT RAISE(ABORT, 'fill_fee_adjustments is append-only');
        END;
        """
    )


def iter_fee_adjustments(
    path: str | Path = DEFAULT_FEE_ADJUSTMENT_PATH,
) -> Iterable[dict[str, Any]]:
    journal_path = Path(path)
    if not journal_path.exists():
        return
    with journal_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def append_fee_adjustment(
    row: dict[str, Any],
    path: str | Path = DEFAULT_FEE_ADJUSTMENT_PATH,
) -> bool:
    """Append one adjustment once, keyed by deterministic adjustment_id."""
    adjustment_id = str(row["adjustment_id"])
    if any(str(existing.get("adjustment_id")) == adjustment_id for existing in iter_fee_adjustments(path)):
        return False
    journal_path = Path(path)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    with journal_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
    return True


def import_fee_adjustments(
    conn: sqlite3.Connection,
    path: str | Path = DEFAULT_FEE_ADJUSTMENT_PATH,
) -> int:
    ensure_fee_adjustment_schema(conn)
    inserted = 0
    for row in iter_fee_adjustments(path):
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO fill_fee_adjustments (
                adjustment_id, fill_id, fee_delta_usd, fee_source,
                fee_evidence_class, transaction_hash, fee_rate,
                market_fee_metadata_json, evidence_json, source_path,
                created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["adjustment_id"],
                row["fill_id"],
                float(row["fee_delta_usd"]),
                row["fee_source"],
                row["fee_evidence_class"],
                row.get("transaction_hash"),
                row.get("fee_rate"),
                json.dumps(row.get("market_fee_metadata") or {}, sort_keys=True),
                json.dumps(row.get("evidence") or {}, sort_keys=True),
                str(path),
                row["created_at_utc"],
            ),
        )
        inserted += conn.total_changes - before
    conn.commit()
    return inserted
