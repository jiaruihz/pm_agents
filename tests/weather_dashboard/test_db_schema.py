import sqlite3
import pytest
from weather_dashboard.db.connection import get_conn, apply_pragmas

def test_get_conn_returns_sqlite_connection(tmp_db):
    assert isinstance(tmp_db, sqlite3.Connection)

def test_pragma_wal_mode(tmp_db):
    row = tmp_db.execute("PRAGMA journal_mode").fetchone()
    # in-memory DB uses 'memory' mode, not 'wal'
    assert row[0] in ("wal", "memory")

def test_pragma_foreign_keys(tmp_db):
    row = tmp_db.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1


def test_all_tables_exist(tmp_db_with_schema):
    tables = ['schema_version', 'universes', 'code_versions', 'strategy_config',
              'runs', 'signals', 'plans', 'orders', 'fills', 'settlements', 'ingestion_log']
    for t in tables:
        row = tmp_db_with_schema.execute(
            f"SELECT name FROM sqlite_master WHERE type='table' AND name='{t}'"
        ).fetchone()
        assert row is not None, f"Table {t} not found"

def test_fk_constraint_enforced(tmp_db_with_schema):
    # Insert nonexistent config_id -> should fail FK check
    with pytest.raises(sqlite3.IntegrityError):
        tmp_db_with_schema.execute("""
            INSERT INTO runs (run_id, config_id, execution_mode, state)
            VALUES ('r1', 'nonexistent_config', 'paper', 'paper')
        """)

def test_signals_append_only_trigger(tmp_db_with_schema):
    # Insert a signal then try to update it - should be blocked
    tmp_db_with_schema.execute("""
        INSERT INTO signals (signal_id, target_date, city, bracket, side)
        VALUES ('sig1', '2026-05-09', 'Tokyo', '23', 'YES')
    """)
    tmp_db_with_schema.commit()

    with pytest.raises(sqlite3.IntegrityError):
        tmp_db_with_schema.execute("UPDATE signals SET city='Osaka' WHERE signal_id='sig1'")

def test_schema_version_written(tmp_db_with_schema):
    row = tmp_db_with_schema.execute("SELECT version FROM schema_version").fetchone()
    assert row is not None
    assert row['version'] == 1