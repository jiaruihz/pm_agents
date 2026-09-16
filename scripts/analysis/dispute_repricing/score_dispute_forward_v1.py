#!/usr/bin/env python3
"""Score latest forward dispute snapshots with the frozen zero-notional policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.rule_lawyer.dispute_strategy import score_forward_snapshots  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--forward-root", default="runtime/dispute_repricing/forward_v1")
    parser.add_argument(
        "--model",
        default="runtime/dispute_repricing/dispute_case_panel_v1/market_plus_rules_model.joblib",
    )
    args = parser.parse_args()
    root = Path(args.forward_root)
    summary = score_forward_snapshots(
        root / "snapshots.jsonl",
        Path(args.model),
        root / "signals.jsonl",
    )
    (root / "scorecard.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
