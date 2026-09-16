#!/usr/bin/env python3
"""Incrementally materialize dispute-repricing JSONL into its canonical mart."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.rule_lawyer.canonical import materialize  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-root",
        type=Path,
        default=ROOT / "runtime/dispute_repricing/forward_v1",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=ROOT / "runtime/dispute_repricing/dispute.db",
    )
    args = parser.parse_args()
    print(json.dumps(materialize(args.source_root, args.db), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
