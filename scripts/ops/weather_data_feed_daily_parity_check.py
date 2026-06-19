#!/usr/bin/env python3
"""Compare old and new weather daily-pipeline products.

This is a read-only Phase 3 migration check. It summarizes file counts, sizes,
latest mtimes, and lightweight row counts for the daily cache/output trees.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OLD_ROOT = ROOT.parent / "weather-predict"
DEFAULT_NEW_RUNTIME_ROOT = ROOT / "weather_data_feed_service" / "runtime"
DEFAULT_RELATIVE_DIRS = (
    "cache/pm_history",
    "cache/gfs_daily",
    "cache/wu_obs",
    "output/research",
)


def file_digest(path: Path, *, limit_bytes: int = 2_000_000) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        remaining = limit_bytes
        while remaining > 0:
            chunk = fh.read(min(65536, remaining))
            if not chunk:
                break
            h.update(chunk)
            remaining -= len(chunk)
    return h.hexdigest()


def estimate_rows(path: Path) -> int | None:
    suffix = path.suffix.lower()
    try:
        if suffix in {".json", ".jsonl"}:
            raw = path.read_text(encoding="utf-8")
            if suffix == ".jsonl":
                return sum(1 for line in raw.splitlines() if line.strip())
            payload = json.loads(raw)
            if isinstance(payload, list):
                return len(payload)
            if isinstance(payload, dict):
                for key in ("records", "brackets", "prices", "hourly"):
                    value = payload.get(key)
                    if isinstance(value, list):
                        return len(value)
                    if isinstance(value, dict):
                        return len(value)
                return len(payload)
        if suffix == ".csv":
            with path.open(newline="", encoding="utf-8") as fh:
                return max(0, sum(1 for _row in csv.reader(fh)) - 1)
    except Exception:
        return None
    return None


def summarize_dir(root: Path) -> dict[str, Any]:
    if not root.exists():
        return {"exists": False, "file_count": 0, "total_bytes": 0}
    files = sorted(path for path in root.rglob("*") if path.is_file())
    suffix_counts = Counter(path.suffix.lower() or "<none>" for path in files)
    latest = max((path.stat().st_mtime for path in files), default=0.0)
    sample_files = files[-20:]
    return {
        "exists": True,
        "file_count": len(files),
        "total_bytes": sum(path.stat().st_size for path in files),
        "latest_mtime": latest,
        "suffix_counts": dict(sorted(suffix_counts.items())),
        "sample": [
            {
                "path": str(path.relative_to(root)),
                "bytes": path.stat().st_size,
                "rows": estimate_rows(path),
                "sha256_prefix": file_digest(path)[:16],
            }
            for path in sample_files
        ],
    }


def compare(old_base: Path, new_base: Path, relative_dirs: list[str]) -> dict[str, Any]:
    sections: dict[str, Any] = {}
    status = "ok"
    for rel in relative_dirs:
        old = summarize_dir(old_base / rel)
        new = summarize_dir(new_base / rel)
        file_delta = int(new.get("file_count", 0)) - int(old.get("file_count", 0))
        byte_delta = int(new.get("total_bytes", 0)) - int(old.get("total_bytes", 0))
        section_status = "ok"
        if old.get("exists") and not new.get("exists"):
            section_status = "fail"
        elif abs(file_delta) > max(5, int(old.get("file_count", 0)) * 0.05):
            section_status = "warn"
        if section_status == "fail":
            status = "fail"
        elif section_status == "warn" and status == "ok":
            status = "warn"
        sections[rel] = {
            "status": section_status,
            "old": old,
            "new": new,
            "file_count_delta": file_delta,
            "total_bytes_delta": byte_delta,
        }
    return {
        "status": status,
        "old_base": str(old_base),
        "new_base": str(new_base),
        "sections": sections,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-root", default=str(DEFAULT_OLD_ROOT), help="Old weather-predict root.")
    parser.add_argument(
        "--new-root",
        default=str(DEFAULT_NEW_RUNTIME_ROOT),
        help="New service runtime root containing cache/ and output/.",
    )
    parser.add_argument("--relative-dir", action="append", default=[], help="Relative cache/output dir to compare.")
    args = parser.parse_args()

    rels = args.relative_dir or list(DEFAULT_RELATIVE_DIRS)
    report = compare(Path(args.old_root), Path(args.new_root), rels)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] in {"ok", "warn"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

