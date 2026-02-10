from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional


@dataclass(frozen=True)
class DecisionStoreResult:
    inserted: int
    skipped: int


class NewsDecisionStore:
    """
    Stores LLM decisions for news items (dedup by (item_id, market_key, model, prompt_version)).

    Rationale:
    - You will iterate on prompts and models. Keep decisions versioned.
    - Use this as a pipeline node boundary: ingest -> decisions -> downstream signals.
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
                CREATE TABLE IF NOT EXISTS news_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id TEXT NOT NULL,
                    market_key TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(item_id, market_key, model, prompt_version)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_news_decisions_item ON news_decisions(item_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_news_decisions_market_time ON news_decisions(market_key, created_at)"
            )
            conn.commit()

    def has_decision(self, item_id: str, market_key: str, model: str, prompt_version: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT 1 FROM news_decisions
                WHERE item_id = ? AND market_key = ? AND model = ? AND prompt_version = ?
                LIMIT 1
                """,
                (item_id, market_key, model, prompt_version),
            )
            return cur.fetchone() is not None

    def insert_many(
        self,
        decisions: Iterable[Dict[str, Any]],
        *,
        market_key: str,
        model: str,
        prompt_version: str,
    ) -> DecisionStoreResult:
        now = int(time.time())
        inserted = 0
        skipped = 0
        jsonl_rows: list[Dict[str, Any]] = []

        with self._connect() as conn:
            for d in decisions:
                item_id = str(d.get("item_id") or "").strip()
                if not item_id:
                    skipped += 1
                    continue
                payload = json.dumps(d, ensure_ascii=True)
                try:
                    conn.execute(
                        """
                        INSERT INTO news_decisions (
                            item_id, market_key, model, prompt_version, decision_json, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (item_id, market_key, model, prompt_version, payload, now),
                    )
                    inserted += 1
                    jsonl_rows.append(
                        {
                            "item_id": item_id,
                            "market_key": market_key,
                            "model": model,
                            "prompt_version": prompt_version,
                            "created_at": now,
                            "decision": d,
                        }
                    )
                except sqlite3.IntegrityError:
                    skipped += 1
            conn.commit()

        if self.jsonl_path and jsonl_rows:
            with open(self.jsonl_path, "a", encoding="utf-8") as f:
                for row in jsonl_rows:
                    f.write(json.dumps(row, ensure_ascii=True) + "\n")

        return DecisionStoreResult(inserted=inserted, skipped=skipped)

