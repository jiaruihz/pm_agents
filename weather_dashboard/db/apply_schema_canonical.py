from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import re

from weather_dashboard.db.connection import get_conn
from weather_dashboard.db.first_seen_schema import apply_first_seen_schema


SCHEMA_VERSION = 21


def _create_table_sql(schema_text: str, table: str) -> str:
    match = re.search(
        rf"CREATE TABLE IF NOT EXISTS {re.escape(table)}\s*\(.*?\n\);",
        schema_text,
        flags=re.DOTALL,
    )
    if match is None:
        raise RuntimeError(f"canonical schema missing CREATE TABLE for {table}")
    return match.group(0)


def _ensure_runtime_registry_enum_contract(
    conn: sqlite3.Connection, schema_text: str
) -> None:
    """Migrate the derived runtime registry when manifest enums expand."""

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='weather_strategy_runtime_registry'"
    ).fetchone()
    if row is None or all(
        marker in str(row[0])
        for marker in ("tiny_live_probe", "zero_notional_pre_live", "legacy_read_only")
    ):
        return

    conn.row_factory = sqlite3.Row
    registry_rows = [dict(item) for item in conn.execute(
        "SELECT * FROM weather_strategy_runtime_registry"
    )]
    artifact_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='weather_strategy_runtime_artifacts'"
    ).fetchone()
    artifact_rows = (
        [dict(item) for item in conn.execute("SELECT * FROM weather_strategy_runtime_artifacts")]
        if artifact_exists
        else []
    )

    if artifact_exists:
        conn.execute("DROP TABLE weather_strategy_runtime_artifacts")
    conn.execute("DROP TABLE weather_strategy_runtime_registry")
    conn.execute(_create_table_sql(schema_text, "weather_strategy_runtime_registry"))
    if registry_rows:
        columns = list(registry_rows[0])
        conn.executemany(
            f"INSERT INTO weather_strategy_runtime_registry ({','.join(columns)}) "
            f"VALUES ({','.join(['?'] * len(columns))})",
            [[item[column] for column in columns] for item in registry_rows],
        )
    conn.execute(_create_table_sql(schema_text, "weather_strategy_runtime_artifacts"))
    if artifact_rows:
        columns = list(artifact_rows[0])
        conn.executemany(
            f"INSERT INTO weather_strategy_runtime_artifacts ({','.join(columns)}) "
            f"VALUES ({','.join(['?'] * len(columns))})",
            [[item[column] for column in columns] for item in artifact_rows],
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_weather_runtime_status "
        "ON weather_strategy_runtime_registry(lifecycle_status, health_status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_weather_runtime_family "
        "ON weather_strategy_runtime_registry(family)"
    )


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


def _ensure_fill_fee_lineage_columns(conn: sqlite3.Connection) -> None:
    """Add fee lineage columns without changing the shared schema-v15 boundary."""
    if "fills" not in {
        str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }:
        return
    existing = _column_names(conn, "fills")
    additions = {
        "fee_source": "TEXT NOT NULL DEFAULT 'legacy_unknown'",
        "fee_rate": "REAL",
        "fee_metadata_json": "TEXT",
        "transaction_hash": "TEXT",
    }
    for column, decl in additions.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE fills ADD COLUMN {column} {decl}")


def _ensure_signal_clock_lineage_columns(conn: sqlite3.Connection) -> None:
    """Add causal-clock lineage without rewriting immutable signal rows."""

    existing_tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "signals" in existing_tables:
        existing = _column_names(conn, "signals")
        additions = {
            "snapshot_clock_basis": "TEXT",
            "snapshot_lineage_status": "TEXT",
            "snapshot_source_ref": "TEXT",
        }
        for column, declaration in additions.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE signals ADD COLUMN {column} {declaration}")

    # fact_trades is a materialized derivative created by the ETL rather than
    # schema_canonical.sql. Additive migration lets bounded refresh publish the
    # corrected rows without a table-wide rebuild.
    if "fact_trades" in existing_tables:
        existing = _column_names(conn, "fact_trades")
        additions = {
            "original_snapshot_ts_utc": "TEXT",
            "signal_clock_basis": "TEXT",
            "signal_clock_evidence_class": "TEXT",
            "signal_clock_lineage_status": "TEXT",
            "signal_clock_source_ref": "TEXT",
            "execution_evidence_link_id": "TEXT",
            "execution_book_snapshot_id": "TEXT",
            "execution_book_observed_at_utc": "TEXT",
            "execution_book_age_ms": "REAL",
            "execution_quote_side": "TEXT",
            "execution_quote_price": "REAL",
            "fill_vs_quote_slippage": "REAL",
            "execution_evidence_status": "TEXT",
            "execution_evidence_source_ref": "TEXT",
        }
        for column, declaration in additions.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE fact_trades ADD COLUMN {column} {declaration}")


def _ensure_execution_profile_columns(conn: sqlite3.Connection) -> None:
    """Add normalized execution-module identity to canonical plans."""

    if "plans" not in {
        str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }:
        return
    existing = _column_names(conn, "plans")
    additions = {
        "execution_profile": "TEXT",
        "order_lifecycle_policy": "TEXT",
        "child_order_role": "TEXT",
        "comparison_group_id": "TEXT",
        "maker_only": "INTEGER",
    }
    for column, declaration in additions.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE plans ADD COLUMN {column} {declaration}")


def _ensure_tmax_v2_lineage_columns(conn: sqlite3.Connection) -> None:
    """Add V2 metadata columns while immutable v15 rows remain untouched."""

    additions_by_table = {
        "tmax_v2_ladder_snapshots": {
            "market_unit": "TEXT",
            "settlement_source_class": "TEXT",
            "market_timezone": "TEXT",
            "market_utc_offset_seconds": "INTEGER",
            "market_metadata_source_json": "TEXT",
            "market_metadata_missing_reason": "TEXT",
        },
        "tmax_v2_ladder_rung_quotes": {
            "question": "TEXT",
        },
        "tmax_v2_forecast_captures": {
            "normalized_hourly_curve_json": "TEXT",
            "forecast_timezone": "TEXT",
            "forecast_utc_offset_seconds": "INTEGER",
            "available_at_basis": "TEXT",
            "forecast_first_seen_at_utc": "TEXT",
            "forecast_first_seen_basis": "TEXT",
            "forecast_first_seen_source": "TEXT",
            "curve_time_lineage_status": "TEXT",
            "curve_time_missing_reason": "TEXT",
        },
        "tmax_v2_observation_event_lineage": {
            "station_id": "TEXT",
            "icao": "TEXT",
            "feed_identity": "TEXT",
            "identity_missing_reason": "TEXT",
        },
    }
    existing_tables = {
        str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for table, additions in additions_by_table.items():
        if table not in existing_tables:
            continue
        existing_columns = _column_names(conn, table)
        for column, declaration in additions.items():
            if column not in existing_columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def apply_schema_canonical(conn: sqlite3.Connection) -> None:
    """Apply the canonical weather dashboard schema."""
    schema_path = Path(__file__).parent / "schema_canonical.sql"
    schema_text = schema_path.read_text(encoding="utf-8")
    conn.executescript(schema_text)
    _ensure_runtime_registry_enum_contract(conn, schema_text)
    _ensure_order_payload_column(conn)
    _ensure_strategy_config_columns(conn)
    _ensure_strategy_def_columns(conn)
    _ensure_strategy_instance_columns(conn)
    _ensure_strategy_instance_runtime_columns(conn)
    _ensure_fill_fee_lineage_columns(conn)
    _ensure_signal_clock_lineage_columns(conn)
    _ensure_execution_profile_columns(conn)
    _ensure_tmax_v2_lineage_columns(conn)
    apply_first_seen_schema(conn)

    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    if row[0] is None or int(row[0]) < SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at_utc, description) VALUES (?, ?, ?)",
            (
                SCHEMA_VERSION,
                datetime.now(timezone.utc).isoformat(),
                "canonical lineage, signal clocks, fee, execution, runtime registry, and replay-case contracts",
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
