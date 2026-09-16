#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.workflows.research.market_rule_audit_workflow import run_market_rule_audit_workflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Skill wrapper for Polymarket market rule audit.")
    parser.add_argument("--target-market", required=True, help="Polymarket market URL, slug, or condition id")
    parser.add_argument("--out-dir", default="")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run_market_rule_audit_workflow(target_market=args.target_market, out_dir=args.out_dir)
    summary = result["summary"]
    print(f"market: {summary.get('market', {}).get('slug') or summary.get('market', {}).get('market_id', '')}")
    print(f"rule clarity: {float(summary.get('rule_clarity_score', 0.0)):.2f}")
    print(f"resolution risk: {float(summary.get('resolution_risk', 0.0)):.2f}")
    print(f"summary json: {result['summary_path']}")
    print(f"report md: {result['report_path']}")


if __name__ == "__main__":
    main()
