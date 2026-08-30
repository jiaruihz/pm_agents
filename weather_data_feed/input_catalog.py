"""Deterministic discovery of PIT JSONL inputs without target-date path guesses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import hashlib
import os
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from weather_clock_contract import parse_utc_or_none


UTC = timezone.utc


def parse_utc(value: Any) -> datetime | None:
    return parse_utc_or_none(value)


@dataclass(frozen=True)
class CatalogRow:
    row: dict[str, Any]
    physical_path: str
    physical_line: int
    physical_record_index: int | None = None


class InputCatalogError(ValueError):
    """A journal cannot be trusted unless every scanned record is accounted for."""


@dataclass(frozen=True)
class InputCatalogAudit:
    scanned: int
    valid: int
    quarantined: int


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _snapshot_hash(path: Path, *, device: int, inode: int, size: int) -> str:
    digest = hashlib.sha256()
    remaining = size
    with path.open("rb") as handle:
        stat = os.fstat(handle.fileno())
        if stat.st_dev != device or stat.st_ino != inode or stat.st_size < size:
            raise InputCatalogError("JSONL source rotated or truncated during audit")
        while remaining:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                raise InputCatalogError("JSONL source truncated during audit")
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def _physical_line(path: Path, byte_offset: int) -> int:
    line_number = 1
    remaining = byte_offset
    with path.open("rb") as handle:
        while remaining:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                raise InputCatalogError("JSONL source truncated while locating bad row")
            line_number += chunk.count(b"\n")
            remaining -= len(chunk)
    return line_number


def _quarantine_record(
    *,
    path: Path,
    line_number: int,
    byte_offset: int,
    raw_line: bytes,
    reason: str,
    file_hash: str,
    snapshot_size: int,
) -> dict[str, Any]:
    line_payload = raw_line.rstrip(b"\r\n")
    return {
        "schema_version": "weather_jsonl_quarantine_v1",
        "reason": reason,
        "physical_path": str(path),
        "physical_line": line_number,
        "physical_byte_offset": byte_offset,
        "source_snapshot_size_bytes": snapshot_size,
        "source_snapshot_sha256": file_hash,
        # Compatibility name retained for existing quarantine readers.  For a
        # growing journal it hashes the bounded snapshot prefix, not later appends.
        "file_sha256": file_hash,
        "line_sha256": _digest(line_payload),
        "file_line_sha256": _digest(
            f"{file_hash}:{line_number}:{byte_offset}:".encode() + line_payload
        ),
    }


def _emit_quarantine(record: dict[str, Any], quarantine_path: Path | None) -> None:
    if quarantine_path is None:
        raise InputCatalogError(json.dumps(record, sort_keys=True))
    quarantine_path.parent.mkdir(parents=True, exist_ok=True)
    with quarantine_path.open("a", encoding="utf-8") as quarantined:
        quarantined.write(json.dumps(record, sort_keys=True) + "\n")


def _reverse_raw_lines(
    path: Path, *, block_size: int
) -> Iterable[tuple[bytes, int, int, int, int]]:
    """Yield line identity newest-first with bounded memory."""

    if block_size <= 0:
        raise ValueError("reverse JSONL block_size must be positive")
    with path.open("rb") as handle:
        stat = os.fstat(handle.fileno())
        position = stat.st_size
        remainder = b""
        while position > 0:
            read_size = min(block_size, position)
            position -= read_size
            handle.seek(position)
            chunk = handle.read(read_size) + remainder
            parts = chunk.split(b"\n")
            remainder = parts[0]
            offset = position + len(parts[0]) + 1
            completed: list[tuple[bytes, int]] = []
            for line in parts[1:]:
                completed.append((line, offset))
                offset += len(line) + 1
            for line, byte_offset in reversed(completed):
                if line:
                    yield line, byte_offset, stat.st_dev, stat.st_ino, stat.st_size
        if remainder:
            yield remainder, 0, stat.st_dev, stat.st_ino, stat.st_size


class JsonlInputCatalog:
    """Resolve inputs from an as-of clock or discovered files, never target_date.

    Day-sharded journals are located from the configured producer shard clock.
    Flat or nested archives are discovered first and filtered by row semantics.
    `target_date` is accepted only as a row predicate.
    """

    def __init__(
        self,
        *,
        day_shard_lookback_days: int = 2,
        day_shard_lookahead_days: int = 1,
        physical_shard_timezone: str = "UTC",
    ):
        if day_shard_lookback_days < 0 or day_shard_lookahead_days < 0:
            raise ValueError("day shard windows must be non-negative")
        self.day_shard_lookback_days = day_shard_lookback_days
        self.day_shard_lookahead_days = day_shard_lookahead_days
        self.physical_shard_timezone = ZoneInfo(physical_shard_timezone)

    def _physical_dates(self, as_of: datetime) -> list[str]:
        physical_date = as_of.astimezone(self.physical_shard_timezone).date()
        return [
            (physical_date + timedelta(days=offset)).isoformat()
            for offset in range(
                -self.day_shard_lookback_days,
                self.day_shard_lookahead_days + 1,
            )
        ]

    @staticmethod
    def _iter_path(path: Path, *, quarantine_path: Path | None = None) -> Iterable[CatalogRow]:
        """Read JSONL strictly, or explicitly quarantine every invalid physical line.

        Silent replacement of invalid UTF-8 and silent JSON skips make a PIT
        denominator unknowable.  Callers that need to continue must provide a
        durable quarantine journal; its records bind the source file and line.
        """
        if not path.is_file():
            return
        with path.open("rb") as handle:
            source_stat = os.fstat(handle.fileno())
            snapshot_size = source_stat.st_size
            file_hash: str | None = None
            byte_offset = 0
            line_number = 0
            while byte_offset < snapshot_size:
                raw_line = handle.readline(snapshot_size - byte_offset)
                if not raw_line:
                    break
                line_number += 1
                failure: str | None = None
                try:
                    line = raw_line.decode("utf-8")
                except UnicodeDecodeError as exc:
                    row = None
                    failure = f"invalid_utf8:{exc.start}"
                else:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        row = None
                        failure = f"invalid_json:{exc.msg}"
                if isinstance(row, dict):
                    yield CatalogRow(row=row, physical_path=str(path), physical_line=line_number)
                elif failure is None:
                    failure = "json_value_is_not_object"
                if failure is not None:
                    if file_hash is None:
                        file_hash = _snapshot_hash(
                            path,
                            device=source_stat.st_dev,
                            inode=source_stat.st_ino,
                            size=snapshot_size,
                        )
                    _emit_quarantine(
                        _quarantine_record(
                            path=path,
                            line_number=line_number,
                            byte_offset=byte_offset,
                            raw_line=raw_line,
                            reason=failure,
                            file_hash=file_hash,
                            snapshot_size=snapshot_size,
                        ),
                        quarantine_path,
                    )
                byte_offset += len(raw_line)

    @classmethod
    def audit_path(
        cls, path: Path, *, quarantine_path: Path | None = None
    ) -> tuple[list[CatalogRow], InputCatalogAudit]:
        """Return rows with the conservation identity ``scanned=valid+quarantine``."""
        before = path.stat()
        rows: list[CatalogRow] = []
        quarantined_before = 0
        if quarantine_path is not None and quarantine_path.exists():
            quarantined_before = len(quarantine_path.read_text(encoding="utf-8").splitlines())
        rows.extend(cls._iter_path(path, quarantine_path=quarantine_path))
        quarantined_after = quarantined_before
        if quarantine_path is not None and quarantine_path.exists():
            quarantined_after = len(quarantine_path.read_text(encoding="utf-8").splitlines())
        quarantined = quarantined_after - quarantined_before
        after = path.stat()
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise InputCatalogError("JSONL source changed during full audit")
        return rows, InputCatalogAudit(
            scanned=len(rows) + quarantined, valid=len(rows), quarantined=quarantined
        )

    @staticmethod
    def iter_path_reverse(
        path: Path, *, block_size: int = 1024 * 1024, quarantine_path: Path | None = None
    ) -> Iterable[CatalogRow]:
        """Read an append-only JSONL journal from newest to oldest."""

        if not path.is_file():
            return
        before = path.stat()
        expected_snapshot = (before.st_dev, before.st_ino, before.st_size)
        expected_path_identity = (*expected_snapshot, before.st_mtime_ns)
        file_hash: str | None = None
        try:
            for raw_line, byte_offset, device, inode, snapshot_size in _reverse_raw_lines(
                path, block_size=block_size
            ):
                if (device, inode, snapshot_size) != expected_snapshot:
                    raise InputCatalogError(
                        "JSONL source changed before reverse scan opened"
                    )
                try:
                    line = raw_line.decode("utf-8")
                    row = json.loads(line)
                except UnicodeDecodeError as exc:
                    row = None
                    failure = f"invalid_utf8:{exc.start}"
                except json.JSONDecodeError as exc:
                    row = None
                    failure = f"invalid_json:{exc.msg}"
                else:
                    failure = (
                        None if isinstance(row, dict) else "json_value_is_not_object"
                    )
                if isinstance(row, dict):
                    # Avoid a full prefix scan for the hot path.  Exact physical
                    # line is calculated only when a row needs quarantine.
                    yield CatalogRow(
                        row=row, physical_path=str(path), physical_line=-1
                    )
                    continue
                if file_hash is None:
                    file_hash = _snapshot_hash(
                        path,
                        device=device,
                        inode=inode,
                        size=snapshot_size,
                    )
                line_number = _physical_line(path, byte_offset)
                _emit_quarantine(
                    _quarantine_record(
                        path=path,
                        line_number=line_number,
                        byte_offset=byte_offset,
                        raw_line=raw_line,
                        reason=str(failure),
                        file_hash=file_hash,
                        snapshot_size=snapshot_size,
                    ),
                    quarantine_path,
                )
        finally:
            try:
                after = path.stat()
            except OSError as exc:
                raise InputCatalogError(
                    "JSONL source disappeared during reverse scan"
                ) from exc
            if (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ) != expected_path_identity:
                raise InputCatalogError("JSONL source changed during reverse scan")

    def day_shard_paths(
        self, root: Path, *, filename: str, as_of: datetime
    ) -> list[Path]:
        paths = []
        for physical_date in self._physical_dates(as_of):
            candidate = root / physical_date / filename
            if candidate.is_file():
                paths.append(candidate)
        return paths

    def day_shard_glob_paths(
        self, root: Path, *, pattern: str, as_of: datetime
    ) -> list[Path]:
        paths: list[Path] = []
        for physical_date in self._physical_dates(as_of):
            paths.extend(sorted((root / physical_date).glob(pattern)))
        return paths

    def rows_from_day_shards(
        self,
        root: Path,
        *,
        filename: str,
        as_of: datetime,
        predicate: Callable[[dict[str, Any]], bool],
    ) -> list[CatalogRow]:
        return [
            catalog_row
            for path in self.day_shard_paths(root, filename=filename, as_of=as_of)
            for catalog_row in self._iter_path(path)
            if predicate(catalog_row.row)
        ]

    def rows_from_discovered_files(
        self,
        root: Path,
        *,
        pattern: str,
        predicate: Callable[[dict[str, Any]], bool],
    ) -> list[CatalogRow]:
        paths = [root] if root.is_file() else sorted(root.glob(pattern))
        return [
            catalog_row
            for path in paths
            for catalog_row in self._iter_path(path)
            if predicate(catalog_row.row)
        ]

    def rows_from_day_shard_files(
        self,
        root: Path,
        *,
        pattern: str,
        as_of: datetime,
        predicate: Callable[[dict[str, Any]], bool],
    ) -> list[CatalogRow]:
        return [
            catalog_row
            for path in self.day_shard_glob_paths(root, pattern=pattern, as_of=as_of)
            for catalog_row in self._iter_path(path)
            if predicate(catalog_row.row)
        ]

    def latest_from_snapshot_files(
        self,
        root: Path,
        *,
        pattern: str,
        as_of: datetime,
        available_field: str,
        predicate: Callable[[dict[str, Any]], bool],
    ) -> CatalogRow | None:
        """Walk immutable snapshots newest-first and stop at the latest PIT row."""

        as_of_utc = as_of.astimezone(UTC)
        paths = self.day_shard_glob_paths(root, pattern=pattern, as_of=as_of)
        for path in sorted(paths, reverse=True):
            candidates = []
            for catalog_row in self._iter_path(path):
                if not predicate(catalog_row.row):
                    continue
                available = parse_utc(catalog_row.row.get(available_field))
                if available is not None and available <= as_of_utc:
                    candidates.append((available, catalog_row.physical_line, catalog_row))
            if candidates:
                return max(candidates, key=lambda item: item[:2])[2]
        return None

    def latest_from_day_shard_journals(
        self,
        root: Path,
        *,
        filename: str,
        as_of: datetime,
        available_field: str,
        predicate: Callable[[dict[str, Any]], bool],
    ) -> CatalogRow | None:
        as_of_utc = as_of.astimezone(UTC)
        for path in reversed(self.day_shard_paths(root, filename=filename, as_of=as_of)):
            for catalog_row in self.iter_path_reverse(path):
                if not predicate(catalog_row.row):
                    continue
                available = parse_utc(catalog_row.row.get(available_field))
                if available is not None and available <= as_of_utc:
                    return catalog_row
        return None

    def latest_from_discovered_journals(
        self,
        root: Path,
        *,
        pattern: str,
        as_of: datetime,
        available_field: str,
        predicate: Callable[[dict[str, Any]], bool],
    ) -> CatalogRow | None:
        as_of_utc = as_of.astimezone(UTC)
        paths = [root] if root.is_file() else sorted(root.glob(pattern), reverse=True)
        for path in paths:
            for catalog_row in self.iter_path_reverse(path):
                if not predicate(catalog_row.row):
                    continue
                available = parse_utc(catalog_row.row.get(available_field))
                if available is not None and available <= as_of_utc:
                    return catalog_row
        return None

    @staticmethod
    def latest_as_of(
        rows: Iterable[CatalogRow], *, as_of: datetime, available_field: str
    ) -> CatalogRow | None:
        as_of_utc = as_of.astimezone(UTC)
        candidates = []
        for catalog_row in rows:
            available = parse_utc(catalog_row.row.get(available_field))
            if available is not None and available <= as_of_utc:
                candidates.append((available, catalog_row.physical_path, catalog_row.physical_line, catalog_row))
        return max(candidates, default=(None, None, None, None), key=lambda item: item[:3])[3]
