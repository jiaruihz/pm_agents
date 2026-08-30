#!/usr/bin/env python3
"""Classify read-only historical weather inputs into replay capability classes."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.data_products import production_data_product_registry  # noqa: E402
from src.strategies.runtime.production import load_production_spec  # noqa: E402
from weather_data_feed.historical_adapter import HistoricalAdapter  # noqa: E402
from weather_data_feed.information_events import canonical_json_hash  # noqa: E402
from weather_data_feed.input_catalog import (  # noqa: E402
    CatalogRow,
    InputCatalogAudit,
    JsonlInputCatalog,
)


MANIFEST_SCHEMA_VERSION = "weather_historical_replay_manifest_v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_create_only(path: Path, payload: bytes) -> Path:
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


def _discover(root: Path, pattern: str) -> list[Path]:
    resolved_root = root.resolve(strict=True)
    paths = [resolved_root] if resolved_root.is_file() else sorted(resolved_root.glob(pattern))
    files: list[Path] = []
    for path in paths:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
            raise ValueError(f"historical input escapes its registered root: {path}")
        files.append(resolved)
    if not files:
        raise ValueError(f"no historical inputs matched {pattern!r} below {resolved_root}")
    return files


def _append_json_quarantine(
    path: Path,
    *,
    quarantine_path: Path,
    source_hash: str,
    reason: str,
    record_index: int | None,
) -> None:
    record = {
        "schema_version": "weather_jsonl_quarantine_v1",
        "reason": reason,
        "physical_path": str(path),
        "physical_line": None,
        "physical_record_index": record_index,
        "physical_byte_offset": None,
        "source_snapshot_size_bytes": path.stat().st_size,
        "source_snapshot_sha256": source_hash,
        "file_sha256": source_hash,
        "line_sha256": None,
        "file_line_sha256": canonical_json_hash(
            {
                "file_sha256": source_hash,
                "record_index": record_index,
                "reason": reason,
            }
        ),
    }
    with quarantine_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def _read_registered_input(
    path: Path, *, quarantine_path: Path
) -> tuple[list[CatalogRow], InputCatalogAudit]:
    if path.suffix == ".jsonl":
        return JsonlInputCatalog.audit_path(path, quarantine_path=quarantine_path)
    if path.suffix != ".json":
        raise ValueError(f"unsupported historical input suffix: {path}")
    source_hash = _sha256(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _append_json_quarantine(
            path,
            quarantine_path=quarantine_path,
            source_hash=source_hash,
            reason=f"invalid_json_document:{type(exc).__name__}",
            record_index=None,
        )
        return [], InputCatalogAudit(scanned=1, valid=0, quarantined=1)
    values: list[Any]
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, dict) and isinstance(payload.get("records"), list):
        values = list(payload["records"])
    elif isinstance(payload, dict):
        values = [payload]
    else:
        values = [payload]
    rows: list[CatalogRow] = []
    quarantined = 0
    for index, value in enumerate(values):
        if isinstance(value, dict):
            rows.append(
                CatalogRow(
                    row=value,
                    physical_path=str(path),
                    physical_line=-1,
                    physical_record_index=index,
                )
            )
            continue
        quarantined += 1
        _append_json_quarantine(
            path,
            quarantine_path=quarantine_path,
            source_hash=source_hash,
            reason="json_record_is_not_object",
            record_index=index,
        )
    return rows, InputCatalogAudit(
        scanned=len(values), valid=len(rows), quarantined=quarantined
    )


def _jsonl_bytes(rows: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(
        (
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        for row in rows
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-id", required=True)
    parser.add_argument("--pattern", default="**/*.json*")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--allow-quarantine", action="store_true")
    parser.add_argument("--production-spec", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    spec = load_production_spec(args.production_spec)
    registry = production_data_product_registry(spec)
    product = registry.product(args.product_id)
    if product.data_class != "archive" or product.write_semantics != "read_only":
        raise ValueError("historical migration accepts only registered read-only archives")
    output_dir = args.output_dir.resolve()
    research_root = spec.research_artifact_root.resolve(strict=False)
    if not output_dir.is_relative_to(research_root):
        raise ValueError("historical replay manifests must live below research_artifact_root")
    output_dir.mkdir(parents=True, exist_ok=True)

    paths = _discover(registry.physical_path(args.product_id), args.pattern)
    quarantine_tmp = output_dir / f".quarantine.{os.getpid()}.jsonl"
    rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    quarantined = 0
    try:
        for path in paths:
            source_stat = path.stat()
            source_hash = _sha256(path)
            catalog_rows, audit = _read_registered_input(
                path,
                quarantine_path=quarantine_tmp,
            )
            source_stat_after = path.stat()
            if (
                source_stat.st_dev,
                source_stat.st_ino,
                source_stat.st_size,
                source_stat.st_mtime_ns,
            ) != (
                source_stat_after.st_dev,
                source_stat_after.st_ino,
                source_stat_after.st_size,
                source_stat_after.st_mtime_ns,
            ):
                raise RuntimeError(f"historical input changed during audit: {path}")
            quarantined += audit.quarantined
            sources.append(
                {
                    "path": str(path),
                    "sha256": source_hash,
                    "device": source_stat.st_dev,
                    "inode": source_stat.st_ino,
                    "size_bytes": source_stat.st_size,
                    "mtime_ns": source_stat.st_mtime_ns,
                    "scanned": audit.scanned,
                    "valid": audit.valid,
                    "quarantined": audit.quarantined,
                }
            )
            for catalog_row in catalog_rows:
                enriched = dict(catalog_row.row)
                if "source_snapshot_hash" in enriched:
                    enriched["legacy_declared_source_snapshot_hash"] = enriched[
                        "source_snapshot_hash"
                    ]
                if "physical_ref" in enriched:
                    enriched["legacy_declared_physical_ref"] = enriched["physical_ref"]
                enriched["source_snapshot_hash"] = source_hash
                physical_ref = {
                    "path": catalog_row.physical_path,
                    "device": source_stat.st_dev,
                    "inode": source_stat.st_ino,
                    "size_bytes": source_stat.st_size,
                    "mtime_ns": source_stat.st_mtime_ns,
                }
                if catalog_row.physical_record_index is not None:
                    physical_ref["record_index"] = catalog_row.physical_record_index
                else:
                    physical_ref["line"] = catalog_row.physical_line
                enriched["physical_ref"] = physical_ref
                rows.append(enriched)
        if quarantined and not args.allow_quarantine:
            raise ValueError(
                f"historical input contains {quarantined} quarantined physical rows"
            )
        adapted, audit = HistoricalAdapter(
            allowed_roots=(registry.physical_path(args.product_id),)
        ).adapt(rows)
        adapted_rows = [
            {
                **{key: value for key, value in asdict(item).items() if key != "row"},
                "raw_row_hash": canonical_json_hash(item.row),
                "row": item.row,
            }
            for item in adapted
        ]
        adapted_payload = _jsonl_bytes(adapted_rows)
        adapted_hash = hashlib.sha256(adapted_payload).hexdigest()
        adapted_path = output_dir / f"adapted_rows.{adapted_hash}.jsonl"
        _write_create_only(adapted_path, adapted_payload)

        quarantine_path = None
        quarantine_hash = None
        if quarantine_tmp.exists() and quarantine_tmp.stat().st_size:
            quarantine_hash = _sha256(quarantine_tmp)
            quarantine_path = output_dir / f"quarantine.{quarantine_hash}.jsonl"
            _write_create_only(quarantine_path, quarantine_tmp.read_bytes())
        manifest_body = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "product_id": args.product_id,
            "product_schema_version": product.schema_version,
            "product_cutover_state": product.cutover_state,
            "source_root": str(registry.physical_path(args.product_id).resolve()),
            "sources": sources,
            "source_count": len(sources),
            "scanned": audit.scanned + quarantined,
            "valid": audit.scanned,
            "quarantined": quarantined,
            "canonical": audit.canonical,
            "duplicate": audit.duplicate,
            "blocked": audit.blocked,
            "conservation_ok": (
                audit.scanned == audit.canonical + audit.duplicate + audit.blocked
                and audit.scanned + quarantined
                == audit.canonical + audit.duplicate + audit.blocked + quarantined
            ),
            "adapted_rows_path": str(adapted_path),
            "adapted_rows_sha256": adapted_hash,
            "quarantine_path": str(quarantine_path) if quarantine_path else None,
            "quarantine_sha256": quarantine_hash,
            "old_source_retention": "read_only_never_delete",
        }
        manifest_id = canonical_json_hash(manifest_body)
        manifest = {
            **manifest_body,
            "manifest_id": manifest_id,
        }
        manifest_path = output_dir / f"historical_replay_manifest.{manifest_id}.json"
        _write_create_only(
            manifest_path,
            (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
        )
        print(
            json.dumps(
                {"manifest_path": str(manifest_path), **manifest},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    finally:
        quarantine_tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
