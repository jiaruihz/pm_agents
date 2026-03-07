#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.workflows.research.market_analysis_workflow import run_market_analysis_workflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run complete Polymarket market analysis with rule gate and market intel.")
    parser.add_argument("--target-market", required=True, help="Polymarket market URL, slug, or condition id")
    parser.add_argument("--comment-limit", type=int, default=30)
    parser.add_argument("--comment-mode", choices=["top_only", "top_and_newest"], default="top_and_newest")
    parser.add_argument("--holders-depth", type=int, default=40)
    parser.add_argument("--top-wallets", type=int, default=8)
    parser.add_argument("--wallet-score-mode", choices=["pnl_proxy", "resolved_trades"], default="pnl_proxy")
    parser.add_argument("--profile-audit-mode", choices=["normal", "full"], default="normal")
    parser.add_argument("--out-dir", default="")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run_market_analysis_workflow(
        target_market=args.target_market,
        comment_limit=args.comment_limit,
        comment_mode=args.comment_mode,
        holders_depth=args.holders_depth,
        top_wallets=args.top_wallets,
        wallet_score_mode=args.wallet_score_mode,
        profile_audit_mode=args.profile_audit_mode,
        out_dir=args.out_dir,
    )
    summary = result["summary"]
    print(f"market: {summary.get('market', {}).get('slug') or summary.get('market', {}).get('market_id', '')}")
    print(f"verdict: {summary.get('verdict', '')}")
    print(f"confidence: {float(summary.get('confidence', 0.0)):.2f}")
    print(f"summary json: {result['summary_path']}")
    print(f"report md: {result['report_path']}")


if __name__ == "__main__":
    main()
