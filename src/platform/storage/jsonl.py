"""Crash-aware append-only JSONL writer."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping


def append_jsonl_row(path: Path, row: Mapping[str, Any]) -> None:
    """Append one complete row, refusing to compound an interrupted tail."""

    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(dict(row), sort_keys=True) + "\n").encode()
    with path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        if size:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b"\n":
                raise RuntimeError(f"append-only JSONL has partial trailing row: {path}")
        handle.seek(0, os.SEEK_END)
        written = handle.write(encoded)
        if written != len(encoded):
            raise RuntimeError(f"short JSONL append: {path}: {written}/{len(encoded)}")
        handle.flush()


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    """Replace a mutable JSON view without exposing a partial document."""

    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def rewrite_jsonl_atomic(path: Path, rows: list[Mapping[str, Any]]) -> None:
    """Atomically replace a derived JSONL view; append-only journals use append_jsonl_row."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w") as handle:
            for row in rows:
                handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
