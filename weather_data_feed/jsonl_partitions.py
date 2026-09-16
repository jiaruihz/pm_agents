"""Resolve append-only JSONL families without double-reading compatibility aggregates."""

from __future__ import annotations

from datetime import date, timedelta
from collections import deque
from pathlib import Path
from typing import Iterable, Iterator


def dated_jsonl_paths(
    path: Path,
    *,
    filename: str,
    dates: Iterable[str] | None = None,
    neighbor_days: int = 0,
    allow_missing: bool = False,
) -> tuple[Path, ...]:
    """Return explicit file input or exact ``YYYY-MM-DD/filename`` shards.

    Root-level aggregate files and sibling JSONL datasets are deliberately not
    selected when ``path`` is a directory.
    """
    if Path(filename).name != filename:
        raise ValueError(f"filename must be a basename: {filename}")
    if path.is_file():
        return (path,)
    if not path.is_dir():
        if allow_missing:
            return ()
        raise FileNotFoundError(f"required raw path does not exist: {path}")

    if dates is None:
        files = tuple(sorted(path.glob(f"????-??-??/{filename}")))
    else:
        radius = max(0, int(neighbor_days))
        days = {
            date.fromisoformat(value) + timedelta(days=offset)
            for value in dates
            for offset in range(-radius, radius + 1)
        }
        files = tuple(
            candidate
            for day in sorted(days)
            if (candidate := path / day.isoformat() / filename).is_file()
        )
    if not files and not allow_missing:
        raise FileNotFoundError(
            f"no dated {filename} partitions under required path: {path}"
        )
    return files


def jsonl_family_paths(
    path: Path,
    *,
    allow_missing: bool = False,
) -> tuple[Path, ...]:
    """Resolve one semantic journal path to its file or dated shards.

    Runtime contracts historically name journals as ``root/name.jsonl``.  A
    partitioned journal keeps that semantic path while storing physical rows
    at ``root/YYYY-MM-DD/name.jsonl``.  When both layouts exist during a
    maintenance cutover, the explicit file wins so callers never double-read
    the same prefix.
    """

    candidate = Path(path)
    if candidate.is_file():
        return (candidate,)
    files = dated_jsonl_paths(
        candidate.parent,
        filename=candidate.name,
        allow_missing=True,
    )
    if files:
        return files
    if allow_missing:
        return ()
    raise FileNotFoundError(f"required JSONL family does not exist: {candidate}")


def iter_jsonl_lines(paths: Iterable[Path]) -> Iterator[str]:
    """Stream complete lines from an already-resolved ordered path set."""
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            yield from handle


def recent_jsonl_lines(paths: Iterable[Path], *, max_lines: int) -> tuple[str, ...]:
    """Return the newest complete lines without reading older partitions.

    Files must be ordered oldest to newest.  Only as many newest shards as
    needed to satisfy ``max_lines`` are scanned.
    """

    limit = max(0, int(max_lines))
    if limit == 0:
        return ()
    remaining = limit
    chunks: list[tuple[str, ...]] = []
    for path in reversed(tuple(paths)):
        with path.open(encoding="utf-8") as handle:
            lines = tuple(deque(handle, maxlen=remaining))
        if lines:
            chunks.append(lines)
            remaining -= len(lines)
        if remaining <= 0:
            break
    return tuple(line for chunk in reversed(chunks) for line in chunk)
