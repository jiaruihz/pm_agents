#!/usr/bin/env python3
"""Provision declared runtime inputs into an immutable refresh release."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


RUNTIME_RELATIVE_ROOT = Path("runtime/weather_edge_v1")
CANONICAL_DB_RELATIVE_PATH = Path("runtime/weather.db")
FILL_CACHE_NAME = "clob_fills.jsonl"
ADJUSTMENT_JOURNAL_NAMES = (
    "clob_fill_fee_adjustments.jsonl",
    "clob_fill_price_adjustments.jsonl",
    "clob_fill_timestamp_adjustments.jsonl",
    "clob_fill_validity_adjustments.jsonl",
)


def _bind(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.binding-{os.getpid()}")
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(source)
    os.replace(temporary, target)
    if not target.is_symlink() or target.readlink() != source:
        raise RuntimeError(f"release runtime binding target mismatch: {target}")


def _validate_target(target: Path) -> None:
    if os.path.lexists(target) and not target.is_symlink():
        raise RuntimeError(f"release runtime binding path is not a symlink: {target}")


def provision_canonical_refresh_bindings(
    operational_project_dir: Path,
    refresh_project_dir: Path,
    canonical_db_path: Path,
) -> list[dict[str, str]]:
    operational_runtime = operational_project_dir / RUNTIME_RELATIVE_ROOT
    refresh_runtime = refresh_project_dir / RUNTIME_RELATIVE_ROOT
    fill_cache = operational_runtime / FILL_CACHE_NAME
    if not fill_cache.is_file():
        raise FileNotFoundError(f"canonical fill cache missing: {fill_cache}")
    if not canonical_db_path.is_file():
        raise FileNotFoundError(f"canonical database missing: {canonical_db_path}")

    binding_paths = [
        (
            "canonical_db",
            canonical_db_path,
            refresh_project_dir / CANONICAL_DB_RELATIVE_PATH,
        ),
        *(
            (name, operational_runtime / name, refresh_runtime / name)
            for name in (FILL_CACHE_NAME, *ADJUSTMENT_JOURNAL_NAMES)
        ),
    ]
    # Refuse every known target conflict before writing any binding. This keeps
    # validation failures from leaving a partially provisioned release.
    for _, _, target in binding_paths:
        _validate_target(target)

    canonical_target = refresh_project_dir / CANONICAL_DB_RELATIVE_PATH
    _bind(canonical_db_path, canonical_target)
    rows: list[dict[str, str]] = [
        {
            "name": "canonical_db",
            "source": str(canonical_db_path),
            "target": str(canonical_target),
        }
    ]
    for name, source, target in binding_paths[1:]:
        _bind(source, target)
        rows.append({"name": name, "source": str(source), "target": str(target)})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operational-project-dir", type=Path, required=True)
    parser.add_argument("--refresh-project-dir", type=Path, required=True)
    parser.add_argument("--canonical-db", type=Path, required=True)
    args = parser.parse_args()
    rows = provision_canonical_refresh_bindings(
        args.operational_project_dir.resolve(),
        args.refresh_project_dir.resolve(),
        args.canonical_db.resolve(),
    )
    print(json.dumps({"bindings": rows}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
