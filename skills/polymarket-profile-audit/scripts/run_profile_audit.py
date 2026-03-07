#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.workflows.research.profile_audit_workflow import run_profile_audit_workflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Skill wrapper for Polymarket profile audit.")
    parser.add_argument("--target", required=True, help="@username, profile url, or wallet")
    parser.add_argument("--fetch-all", action="store_true", help="paginate near-full history")
    parser.add_argument("--focus", default="all", choices=["accuracy", "pnl", "drawdown", "suspiciousness", "all"])
    parser.add_argument("--report-style", default="brief", choices=["brief", "analyst", "risk"])
    parser.add_argument("--out-file", default="", help="summary json path")
    parser.add_argument("--raw-dir", default="", help="raw payload dir")
    parser.add_argument("--max-trades", type=int, default=800, help="trade cap when not fetching all")
    parser.add_argument("--resolve-trade-outcomes", action="store_true", help="enable slower outcome inference")
    parser.add_argument("--max-resolve-markets", type=int, default=80, help="max markets to resolve")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run_profile_audit_workflow(
        target=args.target,
        fetch_all=args.fetch_all,
        max_trades=args.max_trades,
        resolve_trade_outcomes=args.resolve_trade_outcomes,
        max_resolve_markets=args.max_resolve_markets,
        focus=args.focus,
        report_style=args.report_style,
        out_file=args.out_file,
        raw_dir=args.raw_dir,
    )
    summary = result["summary"]
    profile = summary.get("profile", {})
    closed = summary.get("closed_position_score", {})
    pnl = summary.get("portfolio_pnl_analysis", {})
    print(f"target: {summary.get('target', '')}")
    print(f"profile: {profile.get('profile_url', '')}")
    print(f"wallet: {profile.get('proxy_wallet', '')}")
    print(
        "closed positions: "
        f"{closed.get('closed_positions_fetched', 0)} | "
        f"win rate: {float(closed.get('closed_position_win_rate', 0.0)):.2%}"
    )
    print(
        "portfolio pnl / drawdown: "
        f"{float(profile.get('portfolio_pnl', 0.0)):.4f} / {float(pnl.get('max_drawdown_abs', 0.0)):.4f}"
    )
    print(f"verdict: {summary.get('verdict', '')}")
    print(f"summary json: {result['summary_path']}")
    print(f"report md: {result['report_path']}")


if __name__ == "__main__":
    main()
