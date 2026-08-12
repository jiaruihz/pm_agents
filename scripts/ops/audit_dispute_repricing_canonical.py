#!/usr/bin/env python3
"""Audit dispute canonical DB identity, freshness, and decision lineage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.rule_lawyer.canonical_audit import audit_canonical  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        type=Path,
        default=ROOT / "runtime/dispute_repricing/dispute.db",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "runtime/dispute_repricing/canonical_health.json",
    )
    args = parser.parse_args()
    report = audit_canonical(args.db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 1 if report["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
