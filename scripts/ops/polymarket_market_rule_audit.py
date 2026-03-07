#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.workflows.research.market_rule_audit_workflow import run_market_rule_audit_workflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit a Polymarket market's rules and resolution risk.")
    parser.add_argument("--target-market", required=True, help="Polymarket market URL, slug, or condition id")
    parser.add_argument("--out-dir", default="", help="Output directory")
    parser.add_argument("--allow-rule-fallback", action="store_true", help="Allow heuristic fallback when LLM parsing is unavailable")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run_market_rule_audit_workflow(
        target_market=args.target_market,
        out_dir=args.out_dir,
        require_llm=not args.allow_rule_fallback,
    )
    summary = result["summary"]
    print(f"market: {summary.get('market', {}).get('slug') or summary.get('market', {}).get('market_id', '')}")
    print(f"rule status: {summary.get('rule_status', '')}")
    if summary.get("rule_status") == "ok":
        print(f"rule clarity: {float(summary.get('rule_clarity_score', 0.0)):.2f}")
        print(f"resolution risk: {float(summary.get('resolution_risk', 0.0)):.2f}")
    else:
        print(f"rule failure: {summary.get('rule_failure_reason', '')}")
    print(f"summary json: {result['summary_path']}")
    print(f"report md: {result['report_path']}")


if __name__ == "__main__":
    main()
