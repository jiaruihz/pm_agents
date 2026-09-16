"""Canonical city/date/bracket settlement outcome helpers.

`settlements` is keyed for trade joins, primarily by condition_id.  Raw
pm_history is keyed by city, target_date, and bracket.  This module materializes
that source grain so basket and market-structure research can reuse the same
settlement denominator without reading pm_history ad hoc.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any



CREATE_SQL = """
CREATE TABLE IF NOT EXISTS settlement_outcomes (
    settlement_outcome_id TEXT PRIMARY KEY,
    source_system TEXT NOT NULL CHECK (source_system IN ('pm_history','polymarket_api','manual_backfill')),
    source_path TEXT,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    bracket TEXT NOT NULL,
    unit TEXT,
    condition_id TEXT,
    market_id TEXT,
    token_id TEXT,
    raw_final_price REAL,
    final_price REAL NOT NULL,
    settlement_status TEXT NOT NULL CHECK (settlement_status IN ('settled','missing_event','missing_bracket')),
    question TEXT,
    payload TEXT,
    source_payload_hash TEXT,
    source_file_mtime_utc TEXT,
    first_seen_at_utc TEXT,
    available_at_utc TEXT,
    pit_lineage_class TEXT,
    producer_build_id TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_settlement_outcomes_city_bracket
    ON settlement_outcomes(source_system, city, target_date, bracket);
CREATE INDEX IF NOT EXISTS idx_settlement_outcomes_condition
    ON settlement_outcomes(condition_id);
CREATE INDEX IF NOT EXISTS idx_settlement_outcomes_date_city
    ON settlement_outcomes(target_date, city);
CREATE TRIGGER IF NOT EXISTS settlement_outcomes_canonical_before_update
BEFORE UPDATE ON settlement_outcomes
BEGIN
    SELECT RAISE(ABORT, 'settlement_outcomes is append-only');
END;
CREATE TRIGGER IF NOT EXISTS settlement_outcomes_canonical_before_delete
BEFORE DELETE ON settlement_outcomes
BEGIN
    SELECT RAISE(ABORT, 'settlement_outcomes is append-only');
END;
"""


OUTCOME_COLUMNS = (
    "settlement_outcome_id",
    "source_system",
    "source_path",
    "city",
    "target_date",
    "bracket",
    "unit",
    "condition_id",
    "market_id",
    "token_id",
    "raw_final_price",
    "final_price",
    "settlement_status",
    "question",
    "payload",
    "source_payload_hash",
    "source_file_mtime_utc",
    "first_seen_at_utc",
    "available_at_utc",
    "pit_lineage_class",
    "producer_build_id",
)


def ensure_settlement_outcomes_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(CREATE_SQL)
    existing = {str(row[1]) for row in conn.execute("PRAGMA table_info(settlement_outcomes)")}
    additions = {
        "source_payload_hash": "TEXT",
        "source_file_mtime_utc": "TEXT",
        "first_seen_at_utc": "TEXT",
        "available_at_utc": "TEXT",
        "pit_lineage_class": "TEXT",
        "producer_build_id": "TEXT",
    }
    for column, column_type in additions.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE settlement_outcomes ADD COLUMN {column} {column_type}")


def make_settlement_outcome_id(source_system: str, city: str, target_date: str, bracket: str) -> str:
    raw = f"settlement_outcome|{source_system}|{city}|{target_date}|{bracket}"
    return hashlib.sha256(raw.encode()).hexdigest()


def normalize_final_price(final_price: Any) -> float | None:
    try:
        value = float(final_price)
    except (TypeError, ValueError):
        return None
    if value >= 0.99:
        return 1.0
    if value <= 0.01:
        return 0.0
    return None


def stored_final_price(final_price: Any) -> float:
    normalized = normalize_final_price(final_price)
    if normalized is not None:
        return normalized
    return float(final_price)


def settlement_status(final_price: Any) -> str:
    if normalize_final_price(final_price) is not None:
        return "settled"
    return "missing_bracket"


def parse_pm_history_filename(name: str) -> tuple[str, str] | None:
    base = name[:-5] if name.endswith(".json") else name
    if base.startswith("prices_") or "_2026-" not in base:
        return None
    if len(base) < 11 or base[-11] != "_":
        return None
    return base[:-11], base[-10:]


def outcome_from_pm_history_bracket(
    *,
    source_path: Path,
    city: str,
    target_date: str,
    unit: str | None,
    bracket: dict[str, Any],
    condition_id: str | None,
    market_id: str | None,
    source_payload_hash: str | None = None,
    source_file_mtime_utc: str | None = None,
    available_at_utc: str | None = None,
    producer_build: str | None = None,
) -> dict[str, Any] | None:
    label = str(bracket.get("label") or "").strip()
    if not label or bracket.get("final_price") is None:
        return None
    raw_price = float(bracket["final_price"])
    payload = {
        key: bracket.get(key)
        for key in ("closed", "condition_id", "market_id", "tokens")
        if key in bracket
    }
    return {
        "settlement_outcome_id": make_settlement_outcome_id("pm_history", city, target_date, label),
        "source_system": "pm_history",
        "source_path": str(source_path),
        "city": city,
        "target_date": target_date,
        "bracket": label,
        "unit": unit,
        "condition_id": condition_id,
        "market_id": market_id,
        "token_id": str(bracket.get("token_id") or "") or None,
        "raw_final_price": raw_price,
        "final_price": stored_final_price(raw_price),
        "settlement_status": settlement_status(raw_price),
        "question": bracket.get("question"),
        "payload": json.dumps(payload, sort_keys=True) if payload else None,
        "source_payload_hash": source_payload_hash,
        "source_file_mtime_utc": source_file_mtime_utc,
        # pm_history is an archive/rebuild source. Its filesystem mtime must
        # never be promoted to the original collector first-seen clock.
        "first_seen_at_utc": None,
        "available_at_utc": available_at_utc,
        "pit_lineage_class": "late_backfill_first_seen_unknown",
        "producer_build_id": producer_build,
    }


def insert_settlement_outcome(conn: sqlite3.Connection, row: dict[str, Any]) -> int:
    placeholders = ", ".join("?" for _ in OUTCOME_COLUMNS)
    columns = ", ".join(OUTCOME_COLUMNS)
    cur = conn.execute(
        f"INSERT OR IGNORE INTO settlement_outcomes ({columns}) VALUES ({placeholders})",
        tuple(row.get(column) for column in OUTCOME_COLUMNS),
    )
    return int(cur.rowcount or 0)


def has_settlement_outcomes(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='settlement_outcomes'"
    ).fetchone()
    return row is not None


def load_settlement_outcomes(
    conn: sqlite3.Connection,
    *,
    source_system: str = "pm_history",
) -> dict[tuple[str, str, str], dict[str, Any]]:
    if not has_settlement_outcomes(conn):
        return {}
    rows = conn.execute(
        """
        SELECT source_system, source_path, city, target_date, bracket,
               condition_id, market_id, token_id, final_price,
               raw_final_price, settlement_status
        FROM settlement_outcomes
        WHERE source_system = ?
        """,
        (source_system,),
    ).fetchall()
    return {
        (str(row["city"]), str(row["target_date"]), str(row["bracket"])): {
            **dict(row),
            "settlement_source": "settlement_outcomes_city_bracket",
        }
        for row in rows
    }
