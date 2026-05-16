"""FastAPI dependency: per-request SQLite connection."""

import os
import sqlite3
from typing import Generator

from fastapi import HTTPException

from weather_dashboard.db.connection import get_conn

DB_PATH = os.environ.get("WEATHER_DB_PATH", "runtime/weather.db")


def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = get_conn(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()
