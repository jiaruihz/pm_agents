#!/usr/bin/env python3
"""Seal replay rows and chronological train/validation/test/frozen splits."""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.production import load_production_spec  # noqa: E402
from weather_data_feed.input_catalog import JsonlInputCatalog  # noqa: E402
from weather_model_evaluation import (  # noqa: E402
    DataSourceSnapshot,
    DatasetManifest,
    ReplayRunIdentity,
    canonical_db_identity,
    stable_json,
)
from weather_model_evaluation.dataset_manifest import sha256_file  # noqa: E402


def _allowed_output(path: Path) -> Path:
    resolved = path.resolve()
    runtime_root = (ROOT / "runtime").resolve()
    research_runtime = (runtime_root / "research").resolve()
    if resolved.is_relative_to(runtime_root) and not resolved.is_relative_to(
        research_runtime
    ):
        raise ValueError("dataset output may not use the repository runtime root")
    allowed = [
        Path(tempfile.gettempdir()).resolve(),
        research_runtime,
        (ROOT / "research_outputs").resolve(),
    ]
    try:
        allowed.append(load_production_spec().research_artifact_root.resolve(strict=False))
    except (OSError, ValueError):
        pass
    if not any(resolved.is_relative_to(root) for root in allowed):
        raise ValueError("dataset output must use a temp or registered research root")
    return resolved


def _file_identity(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha256_file(resolved),
    }


def _read_stable_jsonl(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    before = _file_identity(path)
    rows = [item.row for item in JsonlInputCatalog._iter_path(Path(before["path"]))]
    after = _file_identity(path)
    if before != after:
        raise RuntimeError(f"JSONL source changed while sealing: {path}")
    return rows, after


def _write_content_addressed(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() == payload:
            return path
        raise FileExistsError(f"conflicting sealed artifact already exists: {path}")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise
        path.chmod(0o444)
        return path
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _partition(
    rows: list[dict[str, Any]],
    *,
    train_end: date,
    validation_end: date,
    test_end: date,
) -> dict[str, list[dict[str, Any]]]:
    if not train_end < validation_end < test_end:
        raise ValueError("split boundaries must be train < validation < test")
    splits = {name: [] for name in ("train", "validation", "test", "frozen_forward")}
    for row in rows:
        try:
            target = date.fromisoformat(str(row["target_date"]))
        except (KeyError, ValueError) as exc:
            raise ValueError("every dataset row requires ISO target_date") from exc
        split = (
            "train"
            if target <= train_end
            else "validation"
            if target <= validation_end
            else "test"
            if target <= test_end
            else "frozen_forward"
        )
        splits[split].append(row)
    empty = [name for name, members in splits.items() if not members]
    if empty:
        raise ValueError(f"chronological split contains no rows: {empty}")
    return splits


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows-jsonl", required=True, type=Path)
    parser.add_argument(
        "--source-config",
        required=True,
        type=Path,
        help="JSON with sources[{source_id,path,available_at_field,cutoff_utc}]",
    )
    parser.add_argument("--db-snapshot", required=True, type=Path)
    parser.add_argument("--db-schema-version", required=True)
    parser.add_argument("--catalog-build-id", required=True)
    parser.add_argument("--code-sha", required=True)
    parser.add_argument("--measurement-namespace", choices=("tmax", "tmin"), required=True)
    parser.add_argument("--label-value-field", required=True)
    parser.add_argument("--label-available-at-field", required=True)
    parser.add_argument("--cutoff-utc", required=True)
    parser.add_argument("--train-end", required=True, type=date.fromisoformat)
    parser.add_argument("--validation-end", required=True, type=date.fromisoformat)
    parser.add_argument("--test-end", required=True, type=date.fromisoformat)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = _allowed_output(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows, rows_input_identity = _read_stable_jsonl(args.rows_jsonl)
    if not rows:
        raise ValueError("dataset rows are empty")
    rows = sorted(rows, key=lambda row: str(row.get("row_id") or row.get("row_identity") or ""))
    splits = _partition(
        rows,
        train_end=args.train_end,
        validation_end=args.validation_end,
        test_end=args.test_end,
    )

    source_config = json.loads(args.source_config.read_text(encoding="utf-8"))
    source_rows = source_config.get("sources") if isinstance(source_config, dict) else None
    if not isinstance(source_rows, list) or not source_rows:
        raise ValueError("source config requires a non-empty sources list")
    snapshots: list[DataSourceSnapshot] = []
    source_file_identities: list[dict[str, Any]] = []
    for source in source_rows:
        if not isinstance(source, dict):
            raise ValueError("each source config entry must be an object")
        source_path = Path(str(source["path"])).resolve(strict=True)
        materialized_source, physical_identity = _read_stable_jsonl(source_path)
        source_file_identities.append(physical_identity)
        snapshots.append(
            DataSourceSnapshot.from_rows(
                str(source["source_id"]),
                materialized_source,
                available_at_field=str(source["available_at_field"]),
                cutoff_utc=str(source["cutoff_utc"]),
                source_path=source_path,
            )
        )

    if Path(str(args.db_snapshot) + "-wal").exists() or Path(
        str(args.db_snapshot) + "-shm"
    ).exists():
        raise ValueError("db-snapshot must be checkpointed and have no WAL/SHM sidecars")
    db_before = canonical_db_identity(
        args.db_snapshot, schema_version=args.db_schema_version
    )
    manifest = DatasetManifest.build(
        rows=rows,
        db_path=args.db_snapshot,
        db_schema_version=args.db_schema_version,
        catalog_build_id=args.catalog_build_id,
        code_sha=args.code_sha,
        source_snapshots=snapshots,
        measurement_namespace=args.measurement_namespace,
        label_value_field=args.label_value_field,
        label_available_at_field=args.label_available_at_field,
        cutoff_utc=args.cutoff_utc,
        splits=splits,
    )
    db_after = canonical_db_identity(
        args.db_snapshot, schema_version=args.db_schema_version
    )
    if db_before != db_after:
        raise RuntimeError("canonical DB snapshot changed while sealing the dataset")

    rows_payload = "".join(stable_json(row) + "\n" for row in rows).encode("utf-8")
    rows_file_hash = hashlib.sha256(rows_payload).hexdigest()
    rows_path = output_dir / f"dataset_rows.{rows_file_hash}.jsonl"
    _write_content_addressed(rows_path, rows_payload)
    manifest_path = output_dir / f"dataset_manifest.{manifest.dataset_hash}.json"
    manifest_payload = (
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _write_content_addressed(manifest_path, manifest_payload)

    identity = ReplayRunIdentity(
        manifest.dataset_hash,
        manifest.split_manifest_hash,
        args.catalog_build_id,
        args.code_sha,
        tuple(snapshot.content_sha256 for snapshot in snapshots),
    )
    seal = {
        "schema_version": "weather_replay_dataset_seal_v1",
        "dataset_manifest_path": str(manifest_path),
        "dataset_manifest_sha256": sha256_file(manifest_path),
        "dataset_rows_path": str(rows_path),
        "dataset_rows_file_sha256": rows_file_hash,
        "dataset_rows_logical_sha256": manifest.rows_hash,
        "dataset_rows_input_snapshot": rows_input_identity,
        "source_file_snapshots": source_file_identities,
        "canonical_db_snapshot": db_after,
        "split_counts": {name: len(members) for name, members in splits.items()},
        "replay_identity": identity.to_dict(),
    }
    seal_hash = hashlib.sha256(
        json.dumps(seal, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    seal_path = output_dir / f"dataset_seal.{seal_hash}.json"
    _write_content_addressed(
        seal_path,
        (json.dumps(seal, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )
    print(json.dumps({"dataset_seal_path": str(seal_path), **seal}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
