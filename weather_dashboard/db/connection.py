import sqlite3
import os
from pathlib import Path

DB_PATH = os.environ.get(
    "WEATHER_DASHBOARD_DB",
    str(Path(__file__).parent.parent.parent / "runtime" / "weather_dashboard.db")
)

def apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA wal_autocheckpoint = 1000")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA mmap_size = 268435456")

def get_conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    apply_pragmas(conn)
    return conn