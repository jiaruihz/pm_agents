from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional


@dataclass(frozen=True)
class StoreResult:
    inserted: int
    skipped: int


class NewsStore:
    """
    Minimal local store with de-duplication.

    - Uses SQLite UNIQUE constraint on item id.
    - Optionally mirrors inserted rows to a JSONL file for easy tail/grep.
    """

    def __init__(self, sqlite_path: str, jsonl_path: Optional[str] = None) -> None:
        self.sqlite_path = sqlite_path
        self.jsonl_path = jsonl_path

        os.makedirs(os.path.dirname(sqlite_path) or ".", exist_ok=True)
        if jsonl_path:
            os.makedirs(os.path.dirname(jsonl_path) or ".", exist_ok=True)

        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS news_items (
                    id TEXT PRIMARY KEY,
                    source TEXT,
                    source_type TEXT,
                    query TEXT,
                    title TEXT,
                    url TEXT,
                    published_at TEXT,
                    description TEXT,
                    fetched_at INTEGER,
                    raw_json TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_news_items_query_time ON news_items(query, fetched_at)"
            )
            conn.commit()

    def insert_many(
        self,
        items: Iterable[Dict[str, Any]],
        *,
        source_type: str,
    ) -> StoreResult:
        now = int(time.time())
        inserted = 0
        skipped = 0
        jsonl_rows: list[Dict[str, Any]] = []

        with self._connect() as conn:
            for item in items:
                item_id = str(item.get("id") or "").strip()
                if not item_id:
                    skipped += 1
                    continue
                row = (
                    item_id,
                    item.get("source"),
                    source_type,
                    item.get("query"),
                    item.get("title"),
                    item.get("url"),
                    item.get("published_at"),
                    item.get("description"),
                    now,
                    json.dumps(item.get("raw") or item, ensure_ascii=True),
                )
                try:
                    conn.execute(
                        """
                        INSERT INTO news_items (
                            id, source, source_type, query, title, url,
                            published_at, description, fetched_at, raw_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        row,
                    )
                    inserted += 1
                    jsonl_rows.append(
                        {
                            "id": item_id,
                            "source": item.get("source"),
                            "source_type": source_type,
                            "query": item.get("query"),
                            "title": item.get("title"),
                            "url": item.get("url"),
                            "published_at": item.get("published_at"),
                            "description": item.get("description"),
                            "fetched_at": now,
                        }
                    )
                except sqlite3.IntegrityError:
                    skipped += 1
            conn.commit()

        if self.jsonl_path and jsonl_rows:
            with open(self.jsonl_path, "a", encoding="utf-8") as f:
                for row in jsonl_rows:
                    f.write(json.dumps(row, ensure_ascii=True) + "\n")

        return StoreResult(inserted=inserted, skipped=skipped)

