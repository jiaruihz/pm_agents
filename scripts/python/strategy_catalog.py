#!/usr/bin/env python3
"""Manage global strategy catalog under src/strategies."""

from __future__ import annotations

import argparse
import json
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.strategies.registry import load_strategy_catalog


def _cmd_list() -> int:
    for item in load_strategy_catalog():
        print(f"{item.strategy_key}\t{item.runner_module}\t{item.description}")
    return 0


def _cmd_show(key: str) -> int:
    for item in load_strategy_catalog():
        if item.strategy_key != key:
            continue
        print(json.dumps(item.model_dump(), ensure_ascii=False, indent=2))
        return 0
    print(f"strategy not found: {key}", file=sys.stderr)
    return 2


def _cmd_validate() -> int:
    rows = load_strategy_catalog()
    seen: set[str] = set()
    ok = True
    for item in rows:
        if item.strategy_key in seen:
            ok = False
            print(f"duplicate strategy key: {item.strategy_key}")
            continue
        seen.add(item.strategy_key)
    if ok:
        print("strategy catalog validation passed")
        return 0
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Global strategy catalog manager")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list all strategies")

    show = sub.add_parser("show", help="show one strategy")
    show.add_argument("--key", required=True, help="strategy key")

    sub.add_parser("validate", help="validate manifests")

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
