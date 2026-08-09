#!/usr/bin/env python3
"""Bound plain runtime logs without touching raw/canonical JSONL evidence."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.tmp")
    pending.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    pending.replace(path)


def compact_log(path: Path, *, max_bytes: int, retain_bytes: int) -> dict[str, Any] | None:
    if path.suffix != ".log" or not path.is_file() or path.is_symlink():
        return None
    before = path.stat().st_size
    if before <= max_bytes:
        return None

    with path.open("r+b", buffering=0) as handle:
        start = max(0, before - retain_bytes)
        handle.seek(start)
        tail = handle.read()
        if start and b"\n" in tail:
            tail = tail.split(b"\n", 1)[1]
        handle.seek(0)
        handle.write(tail)
        handle.truncate()
        os.fsync(handle.fileno())
    return {"path": str(path), "before_bytes": before, "after_bytes": len(tail)}


def maintain(roots: list[Path], *, max_bytes: int, retain_bytes: int) -> dict[str, Any]:
    compacted: list[dict[str, Any]] = []
    scanned = 0
    errors: list[dict[str, str]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.log"):
            scanned += 1
            try:
                result = compact_log(path, max_bytes=max_bytes, retain_bytes=retain_bytes)
                if result:
                    compacted.append(result)
            except OSError as exc:
                errors.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
    return {
        "status": "ok" if not errors else "warning",
        "generated_at_utc": utc_now(),
        "roots": [str(root) for root in roots],
        "max_bytes": max_bytes,
        "retain_bytes": retain_bytes,
        "scanned_logs": scanned,
        "compacted_logs": compacted,
        "reclaimed_bytes": sum(row["before_bytes"] - row["after_bytes"] for row in compacted),
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", required=True)
    parser.add_argument("--health-path", required=True)
    parser.add_argument("--max-mib", type=int, default=64)
    parser.add_argument("--retain-mib", type=int, default=8)
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.max_mib <= 0 or args.retain_mib < 0 or args.retain_mib >= args.max_mib:
        parser.error("require 0 <= retain-mib < max-mib")

    roots = [Path(value).resolve() for value in args.root]
    while True:
        summary = maintain(
            roots,
            max_bytes=args.max_mib * 1024 * 1024,
            retain_bytes=args.retain_mib * 1024 * 1024,
        )
        atomic_write_json(Path(args.health_path), summary)
        if args.once:
            return
        time.sleep(max(30, args.interval_seconds))


if __name__ == "__main__":
    main()
