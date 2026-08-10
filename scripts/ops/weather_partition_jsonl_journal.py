#!/usr/bin/env python3
"""Split one chronological JSONL journal into byte-preserving daily shards.

The command is dry-run by default.  ``--apply --confirm-shard-write`` creates
new shards atomically and writes a manifest, but never deletes or truncates the
source journal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from contextlib import ExitStack
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def row_day(raw: bytes, *, timestamp_field: str) -> str:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("JSONL row is not an object")
    value = str(payload.get(timestamp_field) or "").strip()
    if len(value) < 10:
        raise ValueError(f"missing {timestamp_field}")
    day = value[:10]
    date.fromisoformat(day)
    return day


def inspect_source(source: Path, *, timestamp_field: str) -> dict[str, Any]:
    total_hash = hashlib.sha256()
    day_hashes: dict[str, Any] = {}
    day_rows: dict[str, int] = defaultdict(int)
    day_bytes: dict[str, int] = defaultdict(int)
    rows = parse_errors = date_regressions = 0
    previous_day = ""
    with source.open("rb") as handle:
        for raw in handle:
            rows += 1
            total_hash.update(raw)
            try:
                day = row_day(raw, timestamp_field=timestamp_field)
            except (json.JSONDecodeError, TypeError, ValueError):
                parse_errors += 1
                continue
            if previous_day and day < previous_day:
                date_regressions += 1
            previous_day = day
            day_rows[day] += 1
            day_bytes[day] += len(raw)
            day_hashes.setdefault(day, hashlib.sha256()).update(raw)
    return {
        "source": str(source),
        "source_bytes": source.stat().st_size,
        "source_sha256": total_hash.hexdigest(),
        "rows": rows,
        "parse_errors": parse_errors,
        "date_regressions": date_regressions,
        "first_day": min(day_rows, default=""),
        "last_day": max(day_rows, default=""),
        "day_count": len(day_rows),
        "days": {
            day: {
                "rows": day_rows[day],
                "bytes": day_bytes[day],
                "sha256": day_hashes[day].hexdigest(),
            }
            for day in sorted(day_rows)
        },
    }


def write_shards(
    source: Path,
    destination_root: Path,
    *,
    filename: str,
    timestamp_field: str,
    expected: dict[str, Any],
) -> dict[str, Any]:
    if Path(filename).name != filename:
        raise ValueError("filename must be a basename")
    if expected["parse_errors"] or expected["date_regressions"]:
        raise RuntimeError("source must be parse-clean and chronological before partitioning")

    destinations = {
        day: destination_root / day / filename
        for day in expected["days"]
    }
    existing = [str(path) for path in destinations.values() if path.exists()]
    if existing:
        raise FileExistsError(f"destination shards already exist: {existing[:5]}")

    temp_paths = {
        day: path.with_name(f".{path.name}.partition-{os.getpid()}.tmp")
        for day, path in destinations.items()
    }
    for path in temp_paths.values():
        if path.exists():
            raise FileExistsError(f"staging shard already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)

    handles: dict[str, BinaryIO] = {}
    try:
        with ExitStack() as stack:
            handles = {
                day: stack.enter_context(path.open("xb"))
                for day, path in temp_paths.items()
            }
            with source.open("rb") as handle:
                for raw in handle:
                    day = row_day(raw, timestamp_field=timestamp_field)
                    handles[day].write(raw)

        combined_hash = hashlib.sha256()
        shard_bytes = shard_rows = 0
        for day in sorted(temp_paths):
            path = temp_paths[day]
            raw = path.read_bytes()
            shard_bytes += len(raw)
            shard_rows += raw.count(b"\n")
            combined_hash.update(raw)
            if hashlib.sha256(raw).hexdigest() != expected["days"][day]["sha256"]:
                raise RuntimeError(f"staging shard hash mismatch: {day}")
        if shard_bytes != expected["source_bytes"] or combined_hash.hexdigest() != expected["source_sha256"]:
            raise RuntimeError("ordered shard bytes do not reproduce the source journal")

        for day in sorted(temp_paths):
            os.replace(temp_paths[day], destinations[day])
        return {
            "shard_count": len(destinations),
            "shard_bytes": shard_bytes,
            "shard_rows": shard_rows,
            "ordered_shards_sha256": combined_hash.hexdigest(),
            "destinations": [str(destinations[day]) for day in sorted(destinations)],
        }
    except Exception:
        for path in temp_paths.values():
            path.unlink(missing_ok=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination-root", type=Path, required=True)
    parser.add_argument("--filename", default="")
    parser.add_argument("--timestamp-field", default="ts_utc")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-shard-write", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = args.source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"source journal does not exist: {source}")
    filename = args.filename.strip() or source.name
    report = {
        "schema_version": "weather_jsonl_partition_manifest_v1",
        "generated_at_utc": iso_now(),
        "mode": "apply" if args.apply else "dry_run",
        "destination_root": str(args.destination_root.resolve()),
        "filename": filename,
        "timestamp_field": args.timestamp_field,
        **inspect_source(source, timestamp_field=args.timestamp_field),
    }
    if args.apply:
        if not args.confirm_shard_write:
            raise RuntimeError("--apply requires --confirm-shard-write")
        report["write"] = write_shards(
            source,
            args.destination_root.resolve(),
            filename=filename,
            timestamp_field=args.timestamp_field,
            expected=report,
        )
    manifest = args.manifest
    if args.apply and manifest is None:
        manifest = args.destination_root / f"{Path(filename).stem}.partition_manifest.json"
    if manifest is not None:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
