import sqlite3
import pytest
from weather_dashboard.db.connection import apply_pragmas

@pytest.fixture
def tmp_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_pragmas(conn)
    yield conn
    conn.close()

@pytest.fixture
def tmp_db_with_schema(tmp_db):
    from weather_dashboard.db.apply_schema import apply_schema
    apply_schema(tmp_db)
    return tmp_db