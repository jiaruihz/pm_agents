#!/usr/bin/env python3
"""Manage PMM strategy packs (skill-like strategy folders)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.domains.pmm.strategy_packs.registry import STRATEGY_PACKS, strategy_pack_map


def _cmd_list() -> int:
    for pack in STRATEGY_PACKS:
        print(f"{pack.key}\t{pack.pack_dir}\t{pack.description}")
    return 0


def _cmd_show(key: str) -> int:
    pack = strategy_pack_map().get(key)
    if pack is None:
        print(f"strategy pack not found: {key}", file=sys.stderr)
        return 2
    payload = {
        "key": pack.key,
        "name": pack.name,
        "strategy_module": pack.strategy_module,
        "runner_module": pack.runner_module,
        "pack_dir": pack.pack_dir,
        "description": pack.description,
        "required_files": {k: str(v) for k, v in pack.required_files().items()},
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _cmd_validate() -> int:
    ok = True
    for pack in STRATEGY_PACKS:
        for label, path in pack.required_files().items():
            full_path = ROOT / path
            if not full_path.exists():
                ok = False
                print(f"[missing] {pack.key}:{label} -> {path}")
    if ok:
        print("strategy packs validation passed")
        return 0
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="PMM strategy packs manager")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list all packs")

    show = sub.add_parser("show", help="show one pack detail")
    show.add_argument("--key", required=True, help="strategy key")

    sub.add_parser("validate", help="validate required files")

    args = parser.parse_args()
    if args.cmd == "list":
        return _cmd_list()
    if args.cmd == "show":
        return _cmd_show(args.key)
    if args.cmd == "validate":
        return _cmd_validate()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
