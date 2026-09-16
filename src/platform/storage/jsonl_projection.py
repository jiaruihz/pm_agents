"""Incremental compact projections over append-only JSONL journals."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence, Set
from contextlib import closing
from src.platform.storage.bounded import DiskRows, DiskSet
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Mapping


PROJECTOR_SCHEMA_VERSION = "jsonl_sqlite_projection_v1"


class _SnapshotRows(Sequence):
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def __len__(self):
        return len(self._rows)

    def __getitem__(self, index):
        value = self._rows[index]
        return _SnapshotRows(value) if isinstance(index, slice) else value


class _SnapshotIdentities(Set):
    def __init__(self, identities):
        self._identities = identities

    def __iter__(self):
        return iter(self._identities)

    def __len__(self):
        return len(self._identities)

    def __contains__(self, value):
        return value in self._identities

    @classmethod
    def _from_iterable(cls, values):
        return cls(DiskSet(values))


@dataclass(frozen=True)
class ProjectionSnapshot:
    rows: Sequence
    identities: Set[str]
    indexed_offset: int


class JsonlSQLiteProjection:
    """Materialize a small, durable projection and only parse appended bytes."""

    def __init__(
        self,
        *,
        journal_path: Path,
        database_path: Path,
        namespace: str,
        projection_version: str,
    ) -> None:
        self.journal_path = Path(journal_path)
        self.database_path = Path(database_path)
        self.namespace = namespace
        self.projection_version = projection_version

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA cache_size=-256")
        connection.execute("PRAGMA temp_store=FILE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS projection_metadata (
                namespace TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY(namespace, key)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS projection_rows (
                namespace TEXT NOT NULL,
                identity TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY(namespace, identity)
            )
            """
        )
        return connection

    def _meta(self, connection: sqlite3.Connection, key: str) -> str | None:
        row = connection.execute(
            "SELECT value FROM projection_metadata WHERE namespace=? AND key=?",
            (self.namespace, key),
        ).fetchone()
        return str(row[0]) if row else None

    def _set_meta(self, connection: sqlite3.Connection, key: str, value: Any) -> None:
        connection.execute(
            "INSERT INTO projection_metadata(namespace,key,value) VALUES(?,?,?) "
            "ON CONFLICT(namespace,key) DO UPDATE SET value=excluded.value",
            (self.namespace, key, str(value)),
        )

    def _reset(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "DELETE FROM projection_rows WHERE namespace=?", (self.namespace,)
        )
        connection.execute(
            "DELETE FROM projection_metadata WHERE namespace=?", (self.namespace,)
        )

    def sync(
        self,
        *,
        identity: Callable[[Mapping[str, Any]], str | None] | None,
        project: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> ProjectionSnapshot:
        stat = self.journal_path.stat() if self.journal_path.exists() else None
        with closing(self._connect()) as connection, connection:
            contract = {
                "schema_version": PROJECTOR_SCHEMA_VERSION,
                "projection_version": self.projection_version,
                "journal_path": str(self.journal_path),
            }
            if any(
                self._meta(connection, key) not in (None, value)
                for key, value in contract.items()
            ):
                self._reset(connection)
            for key, value in contract.items():
                self._set_meta(connection, key, value)

            stored_offset = int(self._meta(connection, "indexed_offset") or 0)
            if stat is None:
                self._reset(connection)
                for key, value in contract.items():
                    self._set_meta(connection, key, value)
                stored_offset = 0
            elif (
                self._meta(connection, "source_device") != str(stat.st_dev)
                or self._meta(connection, "source_inode") != str(stat.st_ino)
                or stat.st_size < stored_offset
            ):
                self._reset(connection)
                for key, value in contract.items():
                    self._set_meta(connection, key, value)
                self._set_meta(connection, "source_device", stat.st_dev)
                self._set_meta(connection, "source_inode", stat.st_ino)
                stored_offset = 0

            indexed_offset = stored_offset
            if stat is not None:
                with self.journal_path.open("rb") as handle:
                    handle.seek(stored_offset)
                    while True:
                        start = handle.tell()
                        line = handle.readline()
                        if not line:
                            break
                        if not line.endswith(b"\n"):
                            handle.seek(start)
                            break
                        indexed_offset = handle.tell()
                        try:
                            payload = json.loads(line)
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            continue
                        if not isinstance(payload, dict):
                            continue
                        row_identity = identity(payload) if identity is not None else f"offset:{start:020d}"
                        if not row_identity:
                            continue
                        compact = dict(project(payload))
                        connection.execute(
                            "INSERT INTO projection_rows(namespace,identity,payload_json) "
                            "VALUES(?,?,?) ON CONFLICT(namespace,identity) DO UPDATE "
                            "SET payload_json=excluded.payload_json",
                            (
                                self.namespace,
                                row_identity,
                                json.dumps(compact, ensure_ascii=False, sort_keys=True),
                            ),
                        )
                self._set_meta(connection, "indexed_offset", indexed_offset)

            rows = DiskRows()
            identities = DiskSet()
            for row_identity, payload in connection.execute(
                "SELECT identity,payload_json FROM projection_rows "
                "WHERE namespace=? ORDER BY identity",
                (self.namespace,),
            ):
                rows.append(json.loads(payload))
                identities.add(str(row_identity))
        return ProjectionSnapshot(rows=_SnapshotRows(rows), identities=_SnapshotIdentities(identities), indexed_offset=indexed_offset)
