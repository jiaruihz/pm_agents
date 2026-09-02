#!/usr/bin/env python3
"""Classify read-only historical weather inputs into replay capability classes."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Iterable
import zlib


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


MANIFEST_SCHEMA_VERSION = "weather_historical_replay_manifest_v2"


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


def _seal_create_only(
    source: Path, destination: Path, *, expected_sha256: str
) -> Path:
    """Hard-link a completed temp file into its immutable content-addressed name."""

    if _sha256(source) != expected_sha256:
        raise ValueError("temporary artifact hash changed before sealing")
    if destination.exists():
        if _sha256(destination) != expected_sha256:
            raise FileExistsError(
                f"conflicting sealed artifact already exists: {destination}"
            )
        return destination
    try:
        os.link(source, destination)
    except FileExistsError:
        if _sha256(destination) != expected_sha256:
            raise
    destination.chmod(0o444)
    return destination


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


def _iter_gzip_jsonl(
    path: Path, *, source_hash: str, quarantine_path: Path
) -> Iterable[CatalogRow]:
    """Strictly scan a compressed JSONL archive without decompressing it to disk."""

    snapshot_size = path.stat().st_size
    try:
        with gzip.open(path, "rb") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                reason: str | None = None
                try:
                    value = json.loads(raw_line.decode("utf-8"))
                except UnicodeDecodeError as exc:
                    value = None
                    reason = f"invalid_utf8:{exc.start}"
                except json.JSONDecodeError as exc:
                    value = None
                    reason = f"invalid_json:{exc.msg}"
                if isinstance(value, dict):
                    yield CatalogRow(
                        row=value,
                        physical_path=str(path),
                        physical_line=line_number,
                    )
                    continue
                if reason is None:
                    reason = "json_value_is_not_object"
                line_payload = raw_line.rstrip(b"\r\n")
                record = {
                    "schema_version": "weather_jsonl_quarantine_v1",
                    "reason": reason,
                    "physical_path": str(path),
                    "physical_line": line_number,
                    "physical_byte_offset": None,
                    "source_snapshot_size_bytes": snapshot_size,
                    "source_snapshot_sha256": source_hash,
                    "file_sha256": source_hash,
                    "line_sha256": hashlib.sha256(line_payload).hexdigest(),
                    "file_line_sha256": hashlib.sha256(
                        f"{source_hash}:{line_number}:".encode() + line_payload
                    ).hexdigest(),
                    "compression": "gzip",
                }
                with quarantine_path.open("a", encoding="utf-8") as quarantined:
                    quarantined.write(json.dumps(record, sort_keys=True) + "\n")
    except (gzip.BadGzipFile, EOFError, zlib.error) as exc:
        _append_json_quarantine(
            path,
            quarantine_path=quarantine_path,
            source_hash=source_hash,
            reason=f"gzip_container_error:{type(exc).__name__}",
            record_index=None,
        )


def _read_registered_input(
    path: Path, *, quarantine_path: Path
) -> tuple[list[CatalogRow], InputCatalogAudit]:
    if path.suffix == ".jsonl":
        return JsonlInputCatalog.audit_path(path, quarantine_path=quarantine_path)
    if path.name.endswith(".jsonl.gz"):
        before = _line_count(quarantine_path)
        source_hash = _sha256(path)
        rows = list(
            _iter_gzip_jsonl(
                path, source_hash=source_hash, quarantine_path=quarantine_path
            )
        )
        quarantined = _line_count(quarantine_path) - before
        return rows, InputCatalogAudit(
            scanned=len(rows) + quarantined,
            valid=len(rows),
            quarantined=quarantined,
        )
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


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            count += chunk.count(b"\n")
    return count


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
    sources: list[dict[str, Any]] = []
    counters = {"quarantined": 0}
    adapter = HistoricalAdapter(
        allowed_roots=(registry.physical_path(args.product_id),),
        allow_source_only=True,
    )
    try:
        def iter_enriched_rows() -> Iterable[dict[str, Any]]:
            for path in paths:
                source_stat = path.stat()
                source_hash = _sha256(path)
                if path.suffix == ".jsonl":
                    quarantine_before = _line_count(quarantine_tmp)
                    catalog_rows: Iterable[CatalogRow] = JsonlInputCatalog._iter_path(
                        path, quarantine_path=quarantine_tmp
                    )
                    source_audit = None
                elif path.name.endswith(".jsonl.gz"):
                    quarantine_before = _line_count(quarantine_tmp)
                    catalog_rows = _iter_gzip_jsonl(
                        path,
                        source_hash=source_hash,
                        quarantine_path=quarantine_tmp,
                    )
                    source_audit = None
                else:
                    catalog_rows, source_audit = _read_registered_input(
                        path,
                        quarantine_path=quarantine_tmp,
                    )
                identity = (
                    source_stat.st_dev,
                    source_stat.st_ino,
                    source_stat.st_size,
                    source_stat.st_mtime_ns,
                )
                adapter.register_verified_source(
                    path,
                    source_hash,
                    device=source_stat.st_dev,
                    inode=source_stat.st_ino,
                    size_bytes=source_stat.st_size,
                    mtime_ns=source_stat.st_mtime_ns,
                )
                valid_rows = 0
                for catalog_row in catalog_rows:
                    valid_rows += 1
                    enriched = dict(catalog_row.row)
                    if "source_snapshot_hash" in enriched:
                        enriched["legacy_declared_source_snapshot_hash"] = enriched[
                            "source_snapshot_hash"
                        ]
                    if "physical_ref" in enriched:
                        enriched["legacy_declared_physical_ref"] = enriched[
                            "physical_ref"
                        ]
                    enriched["source_snapshot_hash"] = source_hash
                    physical_ref = {
                        "path": catalog_row.physical_path,
                        "device": source_stat.st_dev,
                        "inode": source_stat.st_ino,
                        "size_bytes": source_stat.st_size,
                        "mtime_ns": source_stat.st_mtime_ns,
                        "row_sha256": canonical_json_hash(catalog_row.row),
                    }
                    if catalog_row.physical_record_index is not None:
                        physical_ref["record_index"] = catalog_row.physical_record_index
                    else:
                        physical_ref["line"] = catalog_row.physical_line
                    enriched["physical_ref"] = physical_ref
                    yield enriched
                source_stat_after = path.stat()
                if identity != (
                    source_stat_after.st_dev,
                    source_stat_after.st_ino,
                    source_stat_after.st_size,
                    source_stat_after.st_mtime_ns,
                ):
                    raise RuntimeError(f"historical input changed during audit: {path}")
                if source_audit is None:
                    quarantined_rows = _line_count(quarantine_tmp) - quarantine_before
                    source_audit = InputCatalogAudit(
                        scanned=valid_rows + quarantined_rows,
                        valid=valid_rows,
                        quarantined=quarantined_rows,
                    )
                counters["quarantined"] += source_audit.quarantined
                sources.append(
                    {
                        "path": str(path),
                        "sha256": source_hash,
                        "device": source_stat.st_dev,
                        "inode": source_stat.st_ino,
                        "size_bytes": source_stat.st_size,
                        "mtime_ns": source_stat.st_mtime_ns,
                        "scanned": source_audit.scanned,
                        "valid": source_audit.valid,
                        "quarantined": source_audit.quarantined,
                    }
                )

        adapted_tmp: Path | None = None
        adapted_digest = hashlib.sha256()
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=output_dir,
            prefix=".adapted_rows.",
            suffix=".tmp",
            delete=False,
        ) as adapted_handle:
            adapted_tmp = Path(adapted_handle.name)

            def write_adapted(item: Any) -> None:
                payload = (
                    json.dumps(
                        {
                            **{
                                key: value
                                for key, value in asdict(item).items()
                                if key != "row"
                            },
                            "raw_row_hash": (item.physical_ref or {}).get(
                                "row_sha256"
                            ),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")
                adapted_handle.write(payload)
                adapted_digest.update(payload)

            adapted, audit = adapter.adapt_stream(
                iter_enriched_rows(),
                retain_rows=False,
                sink=write_adapted,
                spool_dir=output_dir,
            )
            if adapted:
                raise AssertionError("non-retaining adapter unexpectedly returned rows")
            adapted_handle.flush()
            os.fsync(adapted_handle.fileno())
        quarantined = counters["quarantined"]
        if quarantined and not args.allow_quarantine:
            raise ValueError(
                f"historical input contains {quarantined} quarantined physical rows"
            )
        adapted_hash = adapted_digest.hexdigest()
        adapted_path = output_dir / f"adapted_rows.{adapted_hash}.jsonl"
        _seal_create_only(
            adapted_tmp, adapted_path, expected_sha256=adapted_hash
        )
        adapted_tmp.unlink(missing_ok=True)

        quarantine_path = None
        quarantine_hash = None
        if quarantine_tmp.exists() and quarantine_tmp.stat().st_size:
            quarantine_hash = _sha256(quarantine_tmp)
            quarantine_path = output_dir / f"quarantine.{quarantine_hash}.jsonl"
            _seal_create_only(
                quarantine_tmp,
                quarantine_path,
                expected_sha256=quarantine_hash,
            )
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
            "adapted_rows_mode": "reference_only_physical_row",
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
        if "adapted_tmp" in locals() and adapted_tmp is not None:
            adapted_tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
