#!/usr/bin/env python3
"""Provision operational CLOB journals into an immutable refresh release."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


RUNTIME_RELATIVE_ROOT = Path("runtime/weather_edge_v1")
FILL_CACHE_NAME = "clob_fills.jsonl"
ADJUSTMENT_JOURNAL_NAMES = (
    "clob_fill_fee_adjustments.jsonl",
    "clob_fill_price_adjustments.jsonl",
    "clob_fill_timestamp_adjustments.jsonl",
    "clob_fill_validity_adjustments.jsonl",
)


def _bind(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not target.is_symlink():
        raise RuntimeError(f"release runtime binding path is not a symlink: {target}")

    temporary = target.with_name(f".{target.name}.binding-{os.getpid()}")
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(source)
    os.replace(temporary, target)
    if not target.is_symlink() or target.readlink() != source:
        raise RuntimeError(f"release runtime binding target mismatch: {target}")


def provision_canonical_refresh_bindings(
    operational_project_dir: Path,
    refresh_project_dir: Path,
) -> list[dict[str, str]]:
    operational_runtime = operational_project_dir / RUNTIME_RELATIVE_ROOT
    refresh_runtime = refresh_project_dir / RUNTIME_RELATIVE_ROOT
    fill_cache = operational_runtime / FILL_CACHE_NAME
    if not fill_cache.is_file():
        raise FileNotFoundError(f"canonical fill cache missing: {fill_cache}")

    rows: list[dict[str, str]] = []
    for name in (FILL_CACHE_NAME, *ADJUSTMENT_JOURNAL_NAMES):
        source = operational_runtime / name
        target = refresh_runtime / name
        _bind(source, target)
        rows.append({"name": name, "source": str(source), "target": str(target)})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operational-project-dir", type=Path, required=True)
    parser.add_argument("--refresh-project-dir", type=Path, required=True)
    args = parser.parse_args()
    rows = provision_canonical_refresh_bindings(
        args.operational_project_dir.resolve(),
        args.refresh_project_dir.resolve(),
    )
    print(json.dumps({"bindings": rows}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
