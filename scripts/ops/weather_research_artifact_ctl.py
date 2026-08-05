#!/usr/bin/env python3
"""Inventory and evacuate bulky generated research artifacts to canonical JRS.

The archive is content-addressed.  A run manifest maps the former repository
path to its SHA-256 object, so cleanup is reversible without keeping duplicate
machine outputs under ``docs/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ops.check_weather_docs import (
    ROOT,
    active_code_corpus,
    authoritative_link_targets,
    generated_artifact_repo_eligible,
    generated_artifact_required_in_worktree,
    git_tracked_files,
    hygiene_config,
)
from src.strategies.runtime.production import load_production_spec


GENERATED_ROOT = ROOT / "docs" / "analysis"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def generated_files() -> list[Path]:
    return sorted(
        path
        for path in GENERATED_ROOT.glob("**/generated/**/*")
        if path.is_file() and not path.is_symlink()
    )


def select_artifacts() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config = hygiene_config()
    threshold = int(config["untracked_artifact_archive_min_bytes"])
    tracked = git_tracked_files()
    corpus = active_code_corpus(tracked)
    authoritative_targets = authoritative_link_targets(tracked)
    rows: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    for path in generated_files():
        relative = str(path.relative_to(ROOT))
        is_tracked = relative in tracked
        size = path.stat().st_size
        eligible = generated_artifact_repo_eligible(
            relative,
            corpus=corpus,
            authoritative_targets=authoritative_targets,
        )
        required = generated_artifact_required_in_worktree(
            relative,
            corpus=corpus,
            authoritative_targets=authoritative_targets,
        )
        selected = (is_tracked and not eligible) or (
            not is_tracked and not required and size >= threshold
        )
        row = {
            "path": relative,
            "size_bytes": size,
            "git_tracked": is_tracked,
            "repo_eligible": eligible,
            "required_in_worktree": required,
            "selected": selected,
        }
        all_rows.append(row)
        if selected:
            rows.append(row)
    summary = {
        "all_file_count": len(all_rows),
        "all_bytes": sum(row["size_bytes"] for row in all_rows),
        "selected_file_count": len(rows),
        "selected_bytes": sum(row["size_bytes"] for row in rows),
        "selected_tracked_file_count": sum(row["git_tracked"] for row in rows),
        "selected_tracked_bytes": sum(
            row["size_bytes"] for row in rows if row["git_tracked"]
        ),
        "selected_untracked_file_count": sum(
            not row["git_tracked"] for row in rows
        ),
        "selected_untracked_bytes": sum(
            row["size_bytes"] for row in rows if not row["git_tracked"]
        ),
    }
    return rows, summary


def git_snapshot() -> dict[str, Any]:
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    status = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "-z"], cwd=ROOT
    ).decode("utf-8", errors="replace")
    return {
        "head": head,
        "dirty_entry_count": len([item for item in status.split("\0") if item]),
    }


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def store_object(source: Path, object_root: Path, digest: str) -> Path:
    destination = object_root / digest[:2] / digest
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.stat().st_size != source.stat().st_size:
            raise RuntimeError(f"content-address collision by size: {destination}")
        if sha256_file(destination) != digest:
            raise RuntimeError(f"corrupt content-address object: {destination}")
        return destination
    temporary = destination.with_name(f".{destination.name}.tmp.{os.getpid()}")
    shutil.copyfile(source, temporary)
    if sha256_file(temporary) != digest:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"copied object hash mismatch: {source}")
    temporary.replace(destination)
    return destination


def archive(run_id: str, *, apply: bool) -> dict[str, Any]:
    rows, summary = select_artifacts()
    spec = load_production_spec()
    artifact_root = spec.research_artifact_root
    payload: dict[str, Any] = {
        "schema_version": "pm_agents_research_artifact_manifest_v1",
        "run_id": run_id,
        "generated_at_utc": utc_now(),
        "repo_root": str(ROOT),
        "artifact_root": str(artifact_root),
        "git": git_snapshot(),
        "selection": summary,
        "applied": False,
        "files": rows,
    }
    if not apply:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return payload

    object_root = artifact_root / "objects"
    manifest_root = artifact_root / "manifests"
    prepared_path = manifest_root / f"{run_id}.prepared.json"
    final_path = manifest_root / f"{run_id}.json"
    archived_bytes = 0
    for index, row in enumerate(rows, start=1):
        source = ROOT / row["path"]
        before = source.stat()
        digest = sha256_file(source)
        destination = store_object(source, object_root, digest)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError(f"source changed during archive: {source}")
        row["sha256"] = digest
        row["object_path"] = str(destination)
        archived_bytes += before.st_size
        if index % 50 == 0 or archived_bytes // (1024**3) != (
            archived_bytes - before.st_size
        ) // (1024**3):
            print(
                f"archived {index}/{len(rows)} files "
                f"({archived_bytes / (1024**3):.2f} GiB)",
                flush=True,
            )

    payload["archived_at_utc"] = utc_now()
    write_json_atomic(prepared_path, payload)

    removed_bytes = 0
    for row in rows:
        source = ROOT / row["path"]
        if sha256_file(source) != row["sha256"]:
            raise RuntimeError(f"source changed before removal: {source}")
        source.unlink()
        row["removed_from_repo"] = True
        removed_bytes += row["size_bytes"]

    payload["applied"] = True
    payload["removed_at_utc"] = utc_now()
    payload["removed_bytes"] = removed_bytes
    write_json_atomic(final_path, payload)
    prepared_path.unlink(missing_ok=True)
    print(
        json.dumps(
            {
                "manifest": str(final_path),
                "removed_file_count": len(rows),
                "removed_bytes": removed_bytes,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return payload


def restore(
    manifest_path: Path,
    *,
    selected_paths: set[str],
    apply: bool,
) -> dict[str, Any]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "pm_agents_research_artifact_manifest_v1":
        raise ValueError(f"unsupported artifact manifest: {manifest_path}")
    rows = [
        row
        for row in payload.get("files", [])
        if not selected_paths or row["path"] in selected_paths
    ]
    found = {row["path"] for row in rows}
    missing = sorted(selected_paths - found)
    if missing:
        raise ValueError(f"paths absent from manifest: {missing}")
    plan = {
        "manifest": str(manifest_path),
        "restore_file_count": len(rows),
        "restore_bytes": sum(int(row["size_bytes"]) for row in rows),
        "paths": [row["path"] for row in rows],
        "applied": False,
    }
    if not apply:
        print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
        return plan

    restored = 0
    for row in rows:
        destination = ROOT / row["path"]
        if row.get("kind") == "symlink":
            target = str(row["link_target"])
            if destination.is_symlink() and os.readlink(destination) == target:
                continue
            if destination.exists() or destination.is_symlink():
                raise RuntimeError(f"refusing to overwrite different file: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(target)
            restored += 1
            continue
        source = Path(row["object_path"])
        digest = str(row["sha256"])
        if not source.exists() or sha256_file(source) != digest:
            raise RuntimeError(f"missing or corrupt archived object: {source}")
        if destination.exists():
            if sha256_file(destination) != digest:
                raise RuntimeError(f"refusing to overwrite different file: {destination}")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.tmp.{os.getpid()}")
        shutil.copyfile(source, temporary)
        if sha256_file(temporary) != digest:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(f"restored file hash mismatch: {destination}")
        temporary.replace(destination)
        restored += 1
    plan["applied"] = True
    plan["restored_file_count"] = restored
    print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
    return plan


def dirty_worktree_paths() -> list[Path]:
    commands = (
        ["git", "diff", "--name-only", "-z", "HEAD"],
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
    )
    relative_paths: set[str] = set()
    for command in commands:
        output = subprocess.check_output(command, cwd=ROOT).decode("utf-8")
        relative_paths.update(item for item in output.split("\0") if item)
    return sorted(ROOT / relative for relative in relative_paths)


def snapshot_worktree(run_id: str, *, apply: bool) -> dict[str, Any]:
    spec = load_production_spec()
    artifact_root = spec.research_artifact_root
    paths = dirty_worktree_paths()
    rows: list[dict[str, Any]] = []
    for path in paths:
        relative = str(path.relative_to(ROOT))
        if path.is_symlink():
            rows.append(
                {
                    "path": relative,
                    "kind": "symlink",
                    "link_target": os.readlink(path),
                    "size_bytes": path.lstat().st_size,
                }
            )
        elif path.is_file():
            rows.append(
                {
                    "path": relative,
                    "kind": "file",
                    "size_bytes": path.stat().st_size,
                }
            )
        else:
            rows.append({"path": relative, "kind": "missing", "size_bytes": 0})
    payload: dict[str, Any] = {
        "schema_version": "pm_agents_research_artifact_manifest_v1",
        "snapshot_kind": "dirty_worktree",
        "run_id": run_id,
        "generated_at_utc": utc_now(),
        "repo_root": str(ROOT),
        "artifact_root": str(artifact_root),
        "git": git_snapshot(),
        "applied": False,
        "files": rows,
        "selection": {
            "selected_file_count": len(rows),
            "selected_bytes": sum(row["size_bytes"] for row in rows),
        },
    }
    if not apply:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return payload

    object_root = artifact_root / "objects"
    for row in rows:
        if row["kind"] != "file":
            continue
        source = ROOT / row["path"]
        before = source.stat()
        digest = sha256_file(source)
        destination = store_object(source, object_root, digest)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError(f"source changed during snapshot: {source}")
        row["sha256"] = digest
        row["object_path"] = str(destination)
    payload["applied"] = True
    payload["snapshotted_at_utc"] = utc_now()
    manifest = artifact_root / "manifests" / f"{run_id}.json"
    write_json_atomic(manifest, payload)
    print(
        json.dumps(
            {
                "manifest": str(manifest),
                "snapshotted_file_count": len(rows),
                "snapshotted_bytes": payload["selection"]["selected_bytes"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=("inventory", "archive", "restore", "snapshot-worktree")
    )
    parser.add_argument(
        "--run-id",
        default=f"pm_agents_generated_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="archive, verify, and remove selected repository copies",
    )
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="repository-relative path to restore; repeat as needed",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "inventory":
        rows, summary = select_artifacts()
        print(
            json.dumps(
                {"selection": summary, "files": rows},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "restore":
        if args.manifest is None:
            raise SystemExit("restore requires --manifest")
        restore(
            args.manifest,
            selected_paths=set(args.path),
            apply=args.apply,
        )
        return 0
    if args.command == "snapshot-worktree":
        snapshot_worktree(args.run_id, apply=args.apply)
        return 0
    archive(args.run_id, apply=args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
