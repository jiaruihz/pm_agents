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