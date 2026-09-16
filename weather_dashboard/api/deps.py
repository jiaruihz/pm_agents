"""FastAPI dependency: per-request SQLite connection."""

import os
import sqlite3
from typing import Generator

from fastapi import HTTPException

from weather_dashboard.db.connection import get_readonly_conn

DB_PATH = os.environ.get("WEATHER_DB_PATH", "runtime/weather.db")


def get_db() -> Generator[sqlite3.Connection, None, None]:
    try:
        conn = get_readonly_conn(DB_PATH)
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="canonical database unavailable") from exc
    try:
        yield conn
    finally:
        conn.close()
