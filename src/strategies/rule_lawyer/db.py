"""SQLite helper utilities (PG-friendly schema)."""

import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, List

from . import schema
from .config import get_settings
from src.platform.storage.sqlite import connect_sqlite


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def get_connection() -> sqlite3.Connection:
    settings = get_settings()
    return connect_sqlite(settings.db_path)


def init_db() -> None:
    with get_connection() as conn:
        if _table_exists(conn, "analysis_cache") and not _table_exists(conn, "market_rule_parses"):
            conn.execute("ALTER TABLE analysis_cache RENAME TO market_rule_parses")
        conn.executescript(schema.CREATE_TABLES_SQL)
        _ensure_columns(
            conn,
            "markets",
            {
                "status": "TEXT",
                "status_updated_at": "TEXT",
                "outcomes_json": "TEXT",
                "outcome_prices_json": "TEXT",
                "event_ids_json": "TEXT",
                "event_slugs_json": "TEXT",
                "event_titles_json": "TEXT",
                "event_tickers_json": "TEXT",
            },
        )
        _ensure_columns(
            conn,
            "market_rule_parses",
            {
                "rule_score": "INTEGER",
                "rule_score_components_json": "TEXT",
            },
        )


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: Dict[str, str]) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, col_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}")


@contextmanager
def db_cursor() -> Iterator[sqlite3.Cursor]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    finally:
        cur.close()
        conn.close()


def upsert_many(table: str, rows: Iterable[Dict[str, Any]]) -> int:
    rows_list: List[Dict[str, Any]] = list(rows)
    if not rows_list:
        return 0
    sql = schema.UPSERT_STATEMENTS[table]
    with db_cursor() as cur:
        cur.executemany(sql, rows_list)
        return cur.rowcount
