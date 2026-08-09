"""Resolve append-only JSONL families without double-reading compatibility aggregates."""

from __future__ import annotations

from datetime import date, timedelta
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


def iter_jsonl_lines(paths: Iterable[Path]) -> Iterator[str]:
    """Stream complete lines from an already-resolved ordered path set."""
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            yield from handle
