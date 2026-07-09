from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from weather_dashboard.db.connection import get_conn


SCHEMA_VERSION = 7


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_order_payload_column(conn: sqlite3.Connection) -> None:
    if "orders" not in {
        str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }:
        return
    if "order_payload" not in _column_names(conn, "orders"):
        conn.execute("ALTER TABLE orders ADD COLUMN order_payload TEXT")


def apply_schema_canonical(conn: sqlite3.Connection) -> None:
    """Apply the canonical weather dashboard schema."""
    schema_path = Path(__file__).parent / "schema_canonical.sql"
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    _ensure_order_payload_column(conn)

    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    if row[0] is None or int(row[0]) < SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at_utc, description) VALUES (?, ?, ?)",
            (
                SCHEMA_VERSION,
                datetime.now(timezone.utc).isoformat(),
                "canonical tables + strategy runtime control plane (def/instance/control_log)",
            ),
        )
    conn.commit()


def init_db_canonical(db_path: str) -> None:
    """Create or open db_path, apply schema_canonical.sql, and close the connection."""
    Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn(db_path)
    try:
        apply_schema_canonical(conn)
    finally:
        conn.close()
