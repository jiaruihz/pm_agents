import sqlite3
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = (
    os.environ.get("WEATHER_DB_PATH")
    or os.environ.get("WEATHER_DASHBOARD_DB")
    or str(ROOT / "runtime" / "weather.db")
)

def apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA wal_autocheckpoint = 1000")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA mmap_size = 268435456")

def get_conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    apply_pragmas(conn)
    return conn


def get_readonly_conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Open an existing dashboard DB without creating or mutating it."""
    path = Path(db_path).resolve(strict=True)
    conn = sqlite3.connect(
        f"file:{path}?mode=ro",
        uri=True,
        check_same_thread=False,
        timeout=1.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn
