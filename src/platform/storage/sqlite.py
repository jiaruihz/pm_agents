"""SQLite utility helpers shared by repository domains."""

import sqlite3
from pathlib import Path


def ensure_parent_dir(db_path: str) -> None:
    path = Path(db_path)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)


def connect_sqlite(db_path: str) -> sqlite3.Connection:
    ensure_parent_dir(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
    except sqlite3.OperationalError:
        try:
            conn.execute("PRAGMA journal_mode = DELETE;")
        except sqlite3.OperationalError:
            pass
    return conn

