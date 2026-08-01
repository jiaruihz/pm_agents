"""Deterministic discovery of PIT JSONL inputs without target-date path guesses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo


UTC = timezone.utc


def parse_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class CatalogRow:
    row: dict[str, Any]
    physical_path: str
    physical_line: int


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
    def _iter_path(path: Path) -> Iterable[CatalogRow]:
        if not path.is_file():
            return
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle, 1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield CatalogRow(row=row, physical_path=str(path), physical_line=line_number)

    @staticmethod
    def iter_path_reverse(path: Path, *, block_size: int = 1024 * 1024) -> Iterable[CatalogRow]:
        """Read an append-only JSONL journal from newest to oldest."""

        if not path.is_file():
            return
        with path.open("rb") as handle:
            handle.seek(0, 2)
            position = handle.tell()
            remainder = b""
            while position > 0:
                read_size = min(block_size, position)
                position -= read_size
                handle.seek(position)
                chunk = handle.read(read_size) + remainder
                lines = chunk.split(b"\n")
                remainder = lines[0]
                for line in reversed(lines[1:]):
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if isinstance(row, dict):
                        yield CatalogRow(
                            row=row,
                            physical_path=str(path),
                            physical_line=-1,
                        )
            if remainder:
                try:
                    row = json.loads(remainder)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return
                if isinstance(row, dict):
                    yield CatalogRow(row=row, physical_path=str(path), physical_line=1)

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
