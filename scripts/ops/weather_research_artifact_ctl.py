#!/usr/bin/env python3
"""Inventory and evacuate bulky generated research artifacts to canonical JRS.

The archive is content-addressed.  A run manifest maps the former repository
path to its SHA-256 object, so cleanup is reversible without keeping duplicate
machine outputs under ``docs/``.
"""

from __future__ import annotations

import argparse
import ast
import gzip
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
READ_CALL_PREFIXES = ("read", "load", "open", "glob", "iterdir", "exists", "stat")
ARTIFACT_MANIFEST_SCHEMA = "pm_agents_research_artifact_manifest_v1"
ARTIFACT_TOMBSTONE_SCHEMA = "pm_agents_research_artifact_tombstone_v1"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def git_file_at_revision(revision: str, relative: str) -> bytes:
    try:
        return subprocess.check_output(
            ["git", "show", f"{revision}:{relative}"],
            cwd=ROOT,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"file is absent from code revision {revision}: {relative}") from exc


def archived_artifact_rows(artifact_root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load the generated-artifact path map and reject ambiguous history."""
    root = artifact_root or load_production_spec().research_artifact_root
    rows: dict[str, dict[str, Any]] = {}
    tombstones: list[tuple[Path, dict[str, Any]]] = []
    for manifest in sorted((root / "manifests").glob("*.json")):
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if payload.get("schema_version") == ARTIFACT_TOMBSTONE_SCHEMA:
            tombstones.append((manifest, payload))
            continue
        if payload.get("schema_version") != ARTIFACT_MANIFEST_SCHEMA:
            continue
        if payload.get("snapshot_kind") == "dirty_worktree":
            continue
        for raw in payload.get("files", []):
            if not raw.get("object_path") or not raw.get("sha256"):
                continue
            row = dict(raw)
            row["manifest_path"] = str(manifest)
            previous = rows.get(row["path"])
            if previous and previous["sha256"] != row["sha256"]:
                raise RuntimeError(
                    f"ambiguous archived revisions for {row['path']}: "
                    f"{previous['manifest_path']} vs {manifest}"
                )
            rows[row["path"]] = row
    for manifest, payload in tombstones:
        for tombstone in payload.get("tombstones", []):
            previous = rows.get(tombstone["path"])
            if previous is not None and previous["sha256"] != tombstone["sha256"]:
                raise RuntimeError(
                    f"tombstone hash mismatch for {tombstone['path']}: {manifest}"
                )
            rows.pop(tombstone["path"], None)
    return rows


def artifact_tombstones(artifact_root: Path) -> dict[str, dict[str, Any]]:
    tombstones: dict[str, dict[str, Any]] = {}
    for manifest in sorted((artifact_root / "manifests").glob("*.json")):
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if payload.get("schema_version") != ARTIFACT_TOMBSTONE_SCHEMA:
            continue
        for row in payload.get("tombstones", []):
            tombstones[row["path"]] = {**row, "manifest_path": str(manifest)}
    return tombstones


def _gzip_error(path: Path) -> str | None:
    try:
        with gzip.open(path, "rb") as handle:
            while handle.read(8 * 1024 * 1024):
                pass
    except (EOFError, OSError) as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def prune_corrupt(
    selected_paths: set[str],
    *,
    run_id: str,
    apply: bool,
    artifact_root: Path | None = None,
) -> dict[str, Any]:
    """Tombstone proven-corrupt archive representations and reclaim unique objects."""
    if not selected_paths:
        raise ValueError("prune-corrupt requires at least one --path")
    root = artifact_root or load_production_spec().research_artifact_root
    rows = archived_artifact_rows(root)
    missing = sorted(selected_paths - rows.keys())
    if missing:
        raise ValueError(f"paths absent from active archive: {missing}")

    tombstones: list[dict[str, Any]] = []
    for relative in sorted(selected_paths):
        row = rows[relative]
        source = Path(row["object_path"])
        if not source.exists() or sha256_file(source) != row["sha256"]:
            raise RuntimeError(f"archive object missing or hash-corrupt: {source}")
        if not relative.endswith(".gz"):
            raise ValueError(f"automatic corruption proof only supports gzip: {relative}")
        error = _gzip_error(source)
        if error is None:
            raise ValueError(f"refusing to prune valid gzip artifact: {relative}")
        tombstones.append(
            {
                "path": relative,
                "sha256": row["sha256"],
                "object_path": str(source),
                "size_bytes": int(row["size_bytes"]),
                "reason": "gzip_integrity_failure",
                "integrity_error": error,
            }
        )

    selected_hashes = {row["sha256"] for row in tombstones}
    remaining_hashes = {
        row["sha256"] for path, row in rows.items() if path not in selected_paths
    }
    payload: dict[str, Any] = {
        "schema_version": ARTIFACT_TOMBSTONE_SCHEMA,
        "run_id": run_id,
        "generated_at_utc": utc_now(),
        "artifact_root": str(root),
        "applied": False,
        "tombstones": tombstones,
        "reclaimable_bytes": sum(
            row["size_bytes"]
            for row in tombstones
            if row["sha256"] not in remaining_hashes
        ),
    }
    if not apply:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return payload

    manifest = root / "manifests" / f"{run_id}.json"
    if manifest.exists():
        raise RuntimeError(f"refusing to overwrite tombstone manifest: {manifest}")
    payload["applied"] = True
    payload["tombstoned_at_utc"] = utc_now()
    write_json_atomic(manifest, payload)
    deleted_bytes = 0
    for row in tombstones:
        if row["sha256"] in remaining_hashes:
            continue
        Path(row["object_path"]).unlink()
        deleted_bytes += row["size_bytes"]
    payload["deleted_object_bytes"] = deleted_bytes
    payload["deleted_at_utc"] = utc_now()
    write_json_atomic(manifest, payload)
    print(json.dumps({**payload, "manifest": str(manifest)}, ensure_ascii=False, sort_keys=True))
    return payload


def prune_reproduced(
    selected_paths: set[str],
    *,
    run_id: str,
    producer: str,
    reproduced_root: Path,
    code_revision: str,
    runtime_inputs: tuple[Path, ...] = (),
    additional_inputs: frozenset[str] = frozenset(),
    apply: bool,
    artifact_root: Path | None = None,
) -> dict[str, Any]:
    """Tombstone derived outputs proven identical in a clean replay."""
    if not selected_paths:
        raise ValueError("prune-reproduced requires at least one --path")
    producer_path = ROOT / producer
    if not producer_path.is_file() or producer_path.suffix != ".py":
        raise ValueError(f"producer must be a Python file: {producer}")
    if not reproduced_root.is_dir():
        raise ValueError(f"reproduced root is not a directory: {reproduced_root}")
    if not code_revision:
        raise ValueError("prune-reproduced requires --code-revision")
    reproduced_producer = reproduced_root / producer
    if not reproduced_producer.is_file():
        raise ValueError(f"producer is missing from reproduced root: {reproduced_producer}")
    revision_source = git_file_at_revision(code_revision, producer)
    if reproduced_producer.read_bytes() != revision_source:
        raise ValueError(
            f"reproduced producer does not match code revision {code_revision}: {producer}"
        )

    root = artifact_root or load_production_spec().research_artifact_root
    rows = archived_artifact_rows(root)
    missing = sorted(selected_paths - rows.keys())
    if missing:
        raise ValueError(f"paths absent from active archive: {missing}")

    source_paths = sorted((ROOT / "scripts").glob("**/*.py"))
    source_paths += sorted((ROOT / "src").glob("**/*.py"))
    dependencies = discover_archived_dependencies(source_paths, rows)
    consumers = {
        relative: sorted(
            script
            for script, paths in dependencies.items()
            if relative in paths and script != producer
        )
        for relative in selected_paths
    }
    blocked = {path: scripts for path, scripts in consumers.items() if scripts}
    if blocked:
        raise ValueError(f"reproduced outputs still have downstream consumers: {blocked}")

    producer_plan, producer_inputs = dependency_plan({producer})
    producer_input_rows = {
        row["path"]: row
        for row in producer_inputs
        if row["path"] not in selected_paths
    }
    unknown_inputs = sorted(additional_inputs - rows.keys())
    if unknown_inputs:
        raise ValueError(f"proof inputs absent from active archive: {unknown_inputs}")
    for relative in additional_inputs:
        if relative not in selected_paths:
            producer_input_rows[relative] = rows[relative]
    runtime_proof: list[dict[str, Any]] = []
    for runtime_input in runtime_inputs:
        if runtime_input.is_file():
            runtime_proof.append(
                {
                    "path": str(runtime_input.resolve()),
                    "kind": "file",
                    "size_bytes": runtime_input.stat().st_size,
                    "sha256": sha256_file(runtime_input),
                }
            )
            continue
        if not runtime_input.is_dir():
            raise ValueError(f"runtime input does not exist: {runtime_input}")
        inventory_digest = hashlib.sha256()
        file_count = 0
        size_bytes = 0
        latest_mtime_ns = 0
        for child in sorted(path for path in runtime_input.rglob("*") if path.is_file()):
            stat = child.stat()
            relative = str(child.relative_to(runtime_input))
            inventory_digest.update(
                f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode()
            )
            file_count += 1
            size_bytes += stat.st_size
            latest_mtime_ns = max(latest_mtime_ns, stat.st_mtime_ns)
        runtime_proof.append(
            {
                "path": str(runtime_input.resolve()),
                "kind": "directory_inventory",
                "file_count": file_count,
                "size_bytes": size_bytes,
                "latest_mtime_ns": latest_mtime_ns,
                "path_size_mtime_sha256": inventory_digest.hexdigest(),
            }
        )
    tombstones: list[dict[str, Any]] = []
    for relative in sorted(selected_paths):
        row = rows[relative]
        archived = Path(row["object_path"])
        reproduced = reproduced_root / relative
        if not archived.exists() or sha256_file(archived) != row["sha256"]:
            raise RuntimeError(f"archive object missing or hash-corrupt: {archived}")
        if not reproduced.is_file():
            raise ValueError(f"reproduced output is missing: {reproduced}")
        reproduced_sha = sha256_file(reproduced)
        if reproduced_sha != row["sha256"]:
            raise ValueError(
                f"reproduced output hash mismatch for {relative}: "
                f"{reproduced_sha} != {row['sha256']}"
            )
        tombstones.append(
            {
                "path": relative,
                "sha256": row["sha256"],
                "object_path": str(archived),
                "size_bytes": int(row["size_bytes"]),
                "reason": "exact_clean_reproduction",
                "reproduced_path": str(reproduced),
            }
        )

    remaining_hashes = {
        row["sha256"] for path, row in rows.items() if path not in selected_paths
    }
    payload: dict[str, Any] = {
        "schema_version": ARTIFACT_TOMBSTONE_SCHEMA,
        "run_id": run_id,
        "generated_at_utc": utc_now(),
        "artifact_root": str(root),
        "applied": False,
        "tombstones": tombstones,
        "reproduction_proof": {
            "producer": producer,
            "code_revision": code_revision,
            "replay_command": f"python {producer}",
            "input_plan": producer_plan,
            "input_hashes": {
                path: row["sha256"] for path, row in sorted(producer_input_rows.items())
            },
            "runtime_inputs": runtime_proof,
            "match": "sha256_exact",
        },
        "reclaimable_bytes": sum(
            row["size_bytes"]
            for row in tombstones
            if row["sha256"] not in remaining_hashes
        ),
    }
    if not apply:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return payload

    manifest = root / "manifests" / f"{run_id}.json"
    if manifest.exists():
        raise RuntimeError(f"refusing to overwrite tombstone manifest: {manifest}")
    payload["applied"] = True
    payload["tombstoned_at_utc"] = utc_now()
    write_json_atomic(manifest, payload)
    deleted_bytes = 0
    for row in tombstones:
        if row["sha256"] in remaining_hashes:
            continue
        Path(row["object_path"]).unlink()
        deleted_bytes += row["size_bytes"]
    payload["deleted_object_bytes"] = deleted_bytes
    payload["deleted_at_utc"] = utc_now()
    write_json_atomic(manifest, payload)
    print(json.dumps({**payload, "manifest": str(manifest)}, ensure_ascii=False, sort_keys=True))
    return payload


def _static_path(node: ast.AST, environment: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return environment.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _static_path(node.left, environment)
        right = _static_path(node.right, environment)
        if left and right:
            if "docs/" in right:
                return right
            return f"{left.rstrip('/')}/{right.lstrip('/')}"
        return left or right
    if isinstance(node, ast.Call) and node.args:
        name = ""
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name in {"Path", "str", "resolve"}:
            return _static_path(node.args[0], environment)
    return None


def _python_path_environment(tree: ast.AST) -> dict[str, str]:
    environment: dict[str, str] = {}
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
    ]
    for _ in range(4):
        changed = False
        for node in assignments:
            value = _static_path(node.value, environment)
            if not value:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and environment.get(target.id) != value:
                    environment[target.id] = value
                    changed = True
        if not changed:
            break
    return environment


def discover_archived_dependencies(
    source_paths: list[Path],
    archive_rows: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Find archived generated inputs read by Python scripts, including split Path joins."""
    dependencies: dict[str, list[str]] = {}
    archived_paths = tuple(sorted(archive_rows))
    for source in source_paths:
        try:
            tree = ast.parse(source.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        environment = _python_path_environment(tree)
        required: set[str] = set()

        def add_archived_path(value: str) -> None:
            if "docs/analysis/" not in value or "/generated/" not in value:
                return
            relative = value[value.index("docs/analysis/") :]
            if relative in archive_rows:
                required.add(relative)
            prefix = relative.rstrip("/") + "/"
            required.update(path for path in archived_paths if path.startswith(prefix))

        # argparse defaults are read indirectly through ``args.<name>`` and do
        # not appear at the eventual pandas/open call.  Treat static non-output
        # constants as dependencies as well.
        for name, value in environment.items():
            if any(marker in name.upper() for marker in ("OUTPUT", "OUT_DIR", "DESTINATION")):
                continue
            add_archived_path(value)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute):
                function = node.func.attr
                candidates = [node.func.value, *node.args[:1]]
            elif isinstance(node.func, ast.Name):
                function = node.func.id
                candidates = list(node.args[:1])
            else:
                continue
            if not function.startswith(READ_CALL_PREFIXES):
                continue
            for candidate in candidates:
                value = _static_path(candidate, environment)
                if value:
                    add_archived_path(value)
        if required:
            dependencies[str(source.relative_to(ROOT))] = sorted(required)
    return dependencies


def dependency_plan(selected_scripts: set[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    archive_rows = archived_artifact_rows()
    if selected_scripts:
        source_paths = []
        for relative in sorted(selected_scripts):
            source = ROOT / relative
            if not source.is_file() or source.suffix != ".py":
                raise ValueError(f"dependency script must be a Python file: {relative}")
            source_paths.append(source)
    else:
        source_paths = sorted((ROOT / "scripts").glob("**/*.py"))
        source_paths += sorted((ROOT / "src").glob("**/*.py"))
    dependencies = discover_archived_dependencies(source_paths, archive_rows)
    required_paths = sorted({path for paths in dependencies.values() for path in paths})
    rows = [archive_rows[path] for path in required_paths]
    missing = [path for path in required_paths if not (ROOT / path).exists()]
    plan = {
        "affected_script_count": len(dependencies),
        "archived_file_count": len(rows),
        "archived_bytes": sum(int(row["size_bytes"]) for row in rows),
        "missing_in_worktree_count": len(missing),
        "scripts": dependencies,
    }
    return plan, rows


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
        "schema_version": ARTIFACT_MANIFEST_SCHEMA,
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
    if payload.get("schema_version") != ARTIFACT_MANIFEST_SCHEMA:
        raise ValueError(f"unsupported artifact manifest: {manifest_path}")
    tombstones = artifact_tombstones(manifest_path.parent.parent)
    requested_tombstones = sorted(selected_paths.intersection(tombstones))
    if requested_tombstones:
        raise ValueError(f"paths were intentionally tombstoned: {requested_tombstones}")
    rows = [
        row
        for row in payload.get("files", [])
        if row["path"] not in tombstones
        and (not selected_paths or row["path"] in selected_paths)
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

    restored = restore_rows(rows)
    plan["applied"] = True
    plan["restored_file_count"] = restored
    print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
    return plan


def restore_rows(rows: list[dict[str, Any]]) -> int:
    """Restore already-validated manifest rows and return the created count."""
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
    return restored


def restore_dependencies(selected_scripts: set[str], *, apply: bool) -> dict[str, Any]:
    if not selected_scripts:
        raise ValueError("restore-dependencies requires at least one --script")
    plan, rows = dependency_plan(selected_scripts)
    payload = {
        **plan,
        "selected_scripts": sorted(selected_scripts),
        "applied": False,
    }
    if not apply:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return payload
    payload["restored_file_count"] = restore_rows(rows)
    payload["applied"] = True
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return payload


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
        "schema_version": ARTIFACT_MANIFEST_SCHEMA,
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
        "command",
        choices=(
            "inventory",
            "archive",
            "restore",
            "dependencies",
            "restore-dependencies",
            "snapshot-worktree",
            "prune-corrupt",
            "prune-reproduced",
        ),
    )
    parser.add_argument(
        "--run-id",
        default=f"pm_agents_generated_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="execute archive/removal or restore; otherwise print a read-only plan",
    )
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--producer")
    parser.add_argument("--reproduced-root", type=Path)
    parser.add_argument("--code-revision")
    parser.add_argument(
        "--runtime-input",
        action="append",
        default=[],
        type=Path,
        help="mutable runtime input to inventory in the replay proof; repeat as needed",
    )
    parser.add_argument(
        "--proof-input",
        action="append",
        default=[],
        help="additional archived input used through an imported helper; repeat as needed",
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="repository-relative path to restore; repeat as needed",
    )
    parser.add_argument(
        "--script",
        action="append",
        default=[],
        help="repository-relative Python script for dependency audit/restore",
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
    if args.command == "dependencies":
        plan, _ = dependency_plan(set(args.script))
        print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "restore-dependencies":
        restore_dependencies(set(args.script), apply=args.apply)
        return 0
    if args.command == "snapshot-worktree":
        snapshot_worktree(args.run_id, apply=args.apply)
        return 0
    if args.command == "prune-corrupt":
        prune_corrupt(
            set(args.path),
            run_id=args.run_id,
            apply=args.apply,
        )
        return 0
    if args.command == "prune-reproduced":
        if not args.producer or args.reproduced_root is None or not args.code_revision:
            raise SystemExit(
                "prune-reproduced requires --producer, --reproduced-root, and --code-revision"
            )
        prune_reproduced(
            set(args.path),
            run_id=args.run_id,
            producer=args.producer,
            reproduced_root=args.reproduced_root,
            code_revision=args.code_revision,
            runtime_inputs=tuple(args.runtime_input),
            additional_inputs=frozenset(args.proof_input),
            apply=args.apply,
        )
        return 0
    archive(args.run_id, apply=args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
