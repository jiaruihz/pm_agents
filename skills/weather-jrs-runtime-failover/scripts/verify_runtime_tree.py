#!/usr/bin/env python3
"""Compare two stopped runtime trees without following symlinks.

Tiered mode hashes transaction-critical files, metadata-changed files, and a
deterministic bulk sample. Exact mode hashes every regular file.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any


DEFAULT_CRITICAL_GLOBS = (
    "*orders*.jsonl",
    "*fills*.jsonl",
    "*plans*.jsonl",
    "*signals*.jsonl",
    "*state*.json",
    "*pause*.json",
    "*cursor*.json",
    "*dedupe*",
    "*maker*",
    "*lifecycle*",
    "latest*.json",
    "*.db",
    "*.db-wal",
    "*.db-shm",
)


def hash_stable_file(path: Path) -> tuple[str, int]:
    before = path.lstat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.lstat()
    signature_before = (before.st_size, before.st_mtime_ns, before.st_ino)
    signature_after = (after.st_size, after.st_mtime_ns, after.st_ino)
    if signature_before != signature_after:
        raise RuntimeError(f"file changed while hashing: {path}")
    return digest.hexdigest(), after.st_size


def scan_tree(root: Path) -> tuple[dict[str, dict[str, Any]], int]:
    if root.is_symlink():
        raise ValueError(f"root must be a physical directory, not a symlink: {root}")
    if not root.is_dir():
        raise ValueError(f"root is not a directory: {root}")

    rows: dict[str, dict[str, Any]] = {}
    total_bytes = 0
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirnames.sort()
        filenames.sort()

        for name in dirnames:
            path = current_path / name
            rel = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                rows[rel] = {"type": "symlink", "target": os.readlink(path)}
            elif stat.S_ISDIR(mode):
                rows[rel] = {"type": "directory"}
            else:
                raise ValueError(f"unsupported directory entry: {path}")

        for name in filenames:
            path = current_path / name
            rel = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                rows[rel] = {"type": "symlink", "target": os.readlink(path)}
            elif stat.S_ISREG(mode):
                item_stat = path.lstat()
                rows[rel] = {
                    "type": "file",
                    "size": item_stat.st_size,
                    "mtime_ns": item_stat.st_mtime_ns,
                }
                total_bytes += item_stat.st_size
            else:
                raise ValueError(f"unsupported file entry: {path}")
    return rows, total_bytes


def comparable_metadata(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: value for key, value in row.items() if key != "mtime_ns"}


def matches_critical(rel: str, patterns: tuple[str, ...]) -> bool:
    name = Path(rel).name
    return any(fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(name, pattern) for pattern in patterns)


def deterministic_sample(paths: list[str], count: int) -> set[str]:
    if count <= 0:
        return set()
    ranked = sorted(paths, key=lambda value: (hashlib.sha256(value.encode()).digest(), value))
    return set(ranked[:count])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify two stopped runtime directory trees."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument(
        "--mode",
        choices=("tiered", "exact"),
        default="tiered",
        help="tiered hashes critical/changed/sampled files; exact hashes every file",
    )
    parser.add_argument(
        "--critical-glob",
        action="append",
        default=None,
        help="Additional or replacement critical glob; repeat as needed",
    )
    parser.add_argument(
        "--bulk-sample-count",
        type=int,
        default=32,
        help="Deterministic bulk files to hash in tiered mode",
    )
    parser.add_argument("--max-mismatches", type=int, default=20)
    args = parser.parse_args()

    try:
        source_manifest, source_bytes = scan_tree(args.source)
        target_manifest, target_bytes = scan_tree(args.target)
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"equal": False, "error": str(exc)}, ensure_ascii=False))
        return 2

    all_paths = sorted(set(source_manifest) | set(target_manifest))
    mismatches: list[dict[str, Any]] = [
        {
            "path": path,
            "reason": "metadata",
            "source": comparable_metadata(source_manifest.get(path)),
            "target": comparable_metadata(target_manifest.get(path)),
        }
        for path in all_paths
        if comparable_metadata(source_manifest.get(path))
        != comparable_metadata(target_manifest.get(path))
    ]

    common_files = [
        path
        for path in all_paths
        if source_manifest.get(path, {}).get("type") == "file"
        and target_manifest.get(path, {}).get("type") == "file"
        and source_manifest[path]["size"] == target_manifest[path]["size"]
    ]
    patterns = tuple(args.critical_glob or DEFAULT_CRITICAL_GLOBS)
    critical_paths = {path for path in common_files if matches_critical(path, patterns)}
    changed_metadata_paths = {
        path
        for path in common_files
        if source_manifest[path]["mtime_ns"] != target_manifest[path]["mtime_ns"]
    }
    bulk_paths = sorted(set(common_files) - critical_paths - changed_metadata_paths)
    sampled_bulk_paths = (
        set(bulk_paths)
        if args.mode == "exact"
        else deterministic_sample(bulk_paths, args.bulk_sample_count)
    )
    hash_paths = (
        set(common_files)
        if args.mode == "exact"
        else critical_paths | changed_metadata_paths | sampled_bulk_paths
    )

    try:
        for path in sorted(hash_paths):
            source_sha256, _ = hash_stable_file(args.source / path)
            target_sha256, _ = hash_stable_file(args.target / path)
            if source_sha256 != target_sha256:
                mismatches.append(
                    {
                        "path": path,
                        "reason": "sha256",
                        "source_sha256": source_sha256,
                        "target_sha256": target_sha256,
                    }
                )
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"equal": False, "error": str(exc)}, ensure_ascii=False))
        return 2

    mismatch_count = len(mismatches)
    result = {
        "equal": not mismatches,
        "mode": args.mode,
        "source": str(args.source.absolute()),
        "target": str(args.target.absolute()),
        "source_entries": len(source_manifest),
        "target_entries": len(target_manifest),
        "source_bytes": source_bytes,
        "target_bytes": target_bytes,
        "critical_file_count": len(critical_paths),
        "metadata_changed_file_count": len(changed_metadata_paths),
        "sampled_bulk_file_count": len(sampled_bulk_paths),
        "hashed_file_count": len(hash_paths),
        "bulk_file_count": len(bulk_paths),
        "mismatch_count": mismatch_count,
        "mismatches": mismatches[: max(0, args.max_mismatches)],
        "mismatches_truncated": mismatch_count > max(0, args.max_mismatches),
        "requires_exact": args.mode == "tiered" and bool(mismatches),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["equal"] else 1


if __name__ == "__main__":
    sys.exit(main())
