"""Durable byte-range indexes for append-only JSONL journals.

The raw journal remains the source of truth. The SQLite sidecar stores only
byte offsets, so hot-path consumers seek to one logical key without reparsing
the cumulative prefix or rewriting a cumulative JSON index on every append.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any


INDEX_SCHEMA_VERSION = "jsonl_field_range_index_v1"


class JsonlFieldRangeIndex:
    def __init__(self, journal_path: Path, index_path: Path, field_name: str) -> None:
        self.journal_path = Path(journal_path)
        self.index_path = Path(index_path)
        self.field_name = field_name
        self.indexed_offset = 0

    @classmethod
    def load(
        cls, journal_path: Path, index_path: Path, field_name: str
    ) -> "JsonlFieldRangeIndex":
        index = cls(journal_path, index_path, field_name)
        with index._connect() as connection:
            index._ensure_contract(connection)
            index.indexed_offset = int(index._meta(connection, "indexed_offset") or 0)
        return index

    def _connect(self) -> sqlite3.Connection:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.index_path, timeout=5.0)
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ranges (
                field_value TEXT NOT NULL,
                start_offset INTEGER NOT NULL,
                end_offset INTEGER NOT NULL,
                PRIMARY KEY (field_value, start_offset)
            )
            """
        )
        return connection

    @staticmethod
    def _meta(connection: sqlite3.Connection, key: str) -> str | None:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key = ?", (key,)
        ).fetchone()
        return str(row[0]) if row else None

    @staticmethod
    def _set_meta(connection: sqlite3.Connection, key: str, value: Any) -> None:
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )

    def _ensure_contract(self, connection: sqlite3.Connection) -> None:
        expected = {
            "schema_version": INDEX_SCHEMA_VERSION,
            "journal_path": str(self.journal_path),
            "field_name": self.field_name,
        }
        mismatch = any(
            self._meta(connection, key) not in (None, value)
            for key, value in expected.items()
        )
        if mismatch:
            connection.execute("DELETE FROM ranges")
            connection.execute("DELETE FROM metadata")
        for key, value in expected.items():
            self._set_meta(connection, key, value)

    def _reset(self, connection: sqlite3.Connection, *, device: int, inode: int) -> None:
        connection.execute("DELETE FROM ranges")
        self.indexed_offset = 0
        self._set_meta(connection, "source_device", device)
        self._set_meta(connection, "source_inode", inode)
        self._set_meta(connection, "indexed_offset", 0)

    def update(self) -> None:
        if not self.journal_path.exists():
            return
        stat = self.journal_path.stat()
        with self._connect() as connection:
            self._ensure_contract(connection)
            stored_device = self._meta(connection, "source_device")
            stored_inode = self._meta(connection, "source_inode")
            stored_offset = int(self._meta(connection, "indexed_offset") or 0)
            if (
                stored_device != str(stat.st_dev)
                or stored_inode != str(stat.st_ino)
                or stat.st_size < stored_offset
            ):
                self._reset(connection, device=stat.st_dev, inode=stat.st_ino)
            else:
                self.indexed_offset = stored_offset

            with self.journal_path.open("rb") as handle:
                handle.seek(self.indexed_offset)
                while True:
                    start = handle.tell()
                    line = handle.readline()
                    if not line:
                        break
                    if not line.endswith(b"\n"):
                        handle.seek(start)
                        break
                    end = handle.tell()
                    self.indexed_offset = end
                    try:
                        payload = json.loads(line)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if not isinstance(payload, dict):
                        continue
                    key = str(payload.get(self.field_name) or "")
                    if not key:
                        continue
                    prior = connection.execute(
                        "SELECT start_offset, end_offset FROM ranges "
                        "WHERE field_value = ? ORDER BY start_offset DESC LIMIT 1",
                        (key,),
                    ).fetchone()
                    if prior and int(prior[1]) == start:
                        connection.execute(
                            "UPDATE ranges SET end_offset = ? "
                            "WHERE field_value = ? AND start_offset = ?",
                            (end, key, int(prior[0])),
                        )
                    else:
                        connection.execute(
                            "INSERT INTO ranges(field_value, start_offset, end_offset) "
                            "VALUES(?, ?, ?)",
                            (key, start, end),
                        )
            self._set_meta(connection, "indexed_offset", self.indexed_offset)

    def rows_for(self, key: str) -> list[dict[str, Any]]:
        self.update()
        if not self.journal_path.exists():
            return []
        with self._connect() as connection:
            spans = connection.execute(
                "SELECT start_offset, end_offset FROM ranges "
                "WHERE field_value = ? ORDER BY start_offset",
                (str(key),),
            ).fetchall()
        rows: list[dict[str, Any]] = []
        with self.journal_path.open("rb") as handle:
            for start, end in spans:
                handle.seek(int(start))
                while handle.tell() < int(end):
                    line = handle.readline()
                    if not line or not line.endswith(b"\n"):
                        raise RuntimeError(
                            f"indexed JSONL range became incomplete: {self.journal_path}"
                        )
                    try:
                        payload = json.loads(line)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if (
                        isinstance(payload, dict)
                        and str(payload.get(self.field_name) or "") == str(key)
                    ):
                        rows.append(payload)
        return rows
