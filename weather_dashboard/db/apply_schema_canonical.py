from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from weather_dashboard.db.connection import get_conn


SCHEMA_VERSION = 2


def apply_schema_canonical(conn: sqlite3.Connection) -> None:
    """Apply the canonical weather dashboard schema."""
    schema_path = Path(__file__).parent / "schema_canonical.sql"
    conn.executescript(schema_path.read_text(encoding="utf-8"))

    row = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()
    if row[0] == 0:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at_utc, description) VALUES (?, ?, ?)",
            (
                SCHEMA_VERSION,
                datetime.now(timezone.utc).isoformat(),
                "canonical weather lineage schema",
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
