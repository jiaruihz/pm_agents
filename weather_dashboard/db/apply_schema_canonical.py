from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from weather_dashboard.db.connection import get_conn


SCHEMA_VERSION = 13


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_order_payload_column(conn: sqlite3.Connection) -> None:
    if "orders" not in {
        str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }:
        return
    if "order_payload" not in _column_names(conn, "orders"):
        conn.execute("ALTER TABLE orders ADD COLUMN order_payload TEXT")
    if "instance_id" not in _column_names(conn, "orders"):
        conn.execute("ALTER TABLE orders ADD COLUMN instance_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_instance_id ON orders(instance_id)")


def _ensure_strategy_config_columns(conn: sqlite3.Connection) -> None:
    """Add strategy ownership to config rows created before the management model."""
    if "strategy_config" not in {
        str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }:
        return
    if "strategy_key" not in _column_names(conn, "strategy_config"):
        conn.execute("ALTER TABLE strategy_config ADD COLUMN strategy_key TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_config_strategy_key ON strategy_config(strategy_key)")


def _ensure_strategy_def_columns(conn: sqlite3.Connection) -> None:
    """Additive columns for the metadata catalog (manifest-backed rows).

    CREATE TABLE IF NOT EXISTS never alters an existing strategy_def, so add the
    manifest-carrying columns here for DBs created before schema v8.
    """
    if "strategy_def" not in {
        str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }:
        return
    existing = _column_names(conn, "strategy_def")
    additions = {
        "strategy_name": "TEXT NOT NULL DEFAULT ''",
        "runner_module": "TEXT NOT NULL DEFAULT ''",
        "strategy_module": "TEXT NOT NULL DEFAULT ''",
        "meta_json": "TEXT NOT NULL DEFAULT '{}'",
        "def_source": "TEXT NOT NULL DEFAULT 'instance_family'",
        "portfolio_status": "TEXT NOT NULL DEFAULT 'unclassified'",
        "portfolio_note": "TEXT NOT NULL DEFAULT ''",
    }
    for column, decl in additions.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE strategy_def ADD COLUMN {column} {decl}")


def _ensure_strategy_instance_columns(conn: sqlite3.Connection) -> None:
    """Additive columns for the DB control-plane instance model."""
    if "strategy_instance" not in {
        str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }:
        return
    if "config_id" not in _column_names(conn, "strategy_instance"):
        conn.execute("ALTER TABLE strategy_instance ADD COLUMN config_id TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_strategy_instance_config_id "
        "ON strategy_instance(config_id)"
    )


def _ensure_strategy_instance_runtime_columns(conn: sqlite3.Connection) -> None:
    if "strategy_instance_runtime" not in {
        str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }:
        return
    existing = _column_names(conn, "strategy_instance_runtime")
    additions = {
        "paper_order_rows": "INTEGER NOT NULL DEFAULT 0",
        "telemetry_rows": "INTEGER NOT NULL DEFAULT 0",
        "fact_trade_rows": "INTEGER NOT NULL DEFAULT 0",
        "fact_live_real_rows": "INTEGER NOT NULL DEFAULT 0",
        "fact_cost_usd": "REAL",
        "first_target_date": "TEXT",
        "last_target_date": "TEXT",
        "latest_fill_ts_utc": "TEXT",
        "latest_summary_ts_utc": "TEXT",
        "latest_artifact_mtime_utc": "TEXT",
        "heartbeat_age_min": "REAL",
        "live_enabled": "INTEGER",
        "summary_path": "TEXT",
        "primary_journal_path": "TEXT",
    }
    for column, decl in additions.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE strategy_instance_runtime ADD COLUMN {column} {decl}")


def apply_schema_canonical(conn: sqlite3.Connection) -> None:
    """Apply the canonical weather dashboard schema."""
    schema_path = Path(__file__).parent / "schema_canonical.sql"
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    _ensure_order_payload_column(conn)
    _ensure_strategy_config_columns(conn)
    _ensure_strategy_def_columns(conn)
    _ensure_strategy_instance_columns(conn)
    _ensure_strategy_instance_runtime_columns(conn)

    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    if row[0] is None or int(row[0]) < SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at_utc, description) VALUES (?, ?, ?)",
            (
                SCHEMA_VERSION,
                datetime.now(timezone.utc).isoformat(),
                "strategy catalog portfolio status and ownership lineage",
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
