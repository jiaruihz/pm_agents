#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.rule_lawyer.services.market_resolver import resolve_market
from src.strategies.rule_lawyer.services.smart_wallets import discover_market_wallets
from src.strategies.rule_lawyer.services.reporting import write_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pmm_find_smart_wallets",
        description="Discover high-signal Polymarket wallets for a single market.",
    )
    parser.add_argument("--target-market", default="", help="Polymarket market URL, slug, or condition id")
    parser.add_argument("--url", default="", help="Polymarket market URL")
    parser.add_argument("--slug", default="", help="Polymarket market slug")
    parser.add_argument("--condition-id", default="", help="Market condition id")
    parser.add_argument("--holders-depth", "--max-holders-per-token", dest="holders_depth", type=int, default=40)
    parser.add_argument("--max-market-trades", type=int, default=250)
    parser.add_argument("--max-candidate-wallets", type=int, default=50)
    parser.add_argument("--top-wallets", type=int, default=12)
    parser.add_argument("--score-mode", choices=["pnl_proxy", "resolved_trades"], default="pnl_proxy")
    parser.add_argument("--max-user-trades", type=int, default=300)
    parser.add_argument("--behavior-max-trades", type=int, default=120)
    parser.add_argument("--min-win-rate", type=float, default=0.55)
    parser.add_argument("--min-resolved-trades", type=int, default=6)
    parser.add_argument("--min-resolved-markets", type=int, default=4)
    parser.add_argument("--include-sells", action="store_true")
    parser.add_argument("--min-position-notional", type=float, default=10.0)
    parser.add_argument("--min-pnl-abs", type=float, default=0.2)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--out-file", default="runtime/smart_money_wallets.json")
    return parser


def resolve_target(args: argparse.Namespace) -> str:
    for value in (args.target_market, args.url, args.slug, args.condition_id):
        text = str(value or "").strip()
        if text:
            return text
    raise SystemExit("one of --target-market/--url/--slug/--condition-id is required")


def print_summary(payload: dict, out_file: Path) -> None:
    stats = payload.get("stats", {})
    wallets = payload.get("wallets", [])
    print(f"wallets selected: {len(wallets)}")
    print(f"holders wallets: {stats.get('holders_wallets', 0)}")
    print(f"trades wallets: {stats.get('trades_wallets', 0)}")
    if wallets:
        top = wallets[0]
        print(
            "top wallet: "
            f"{top.get('wallet', '')} | win_rate={float(top.get('win_rate', 0.0)):.2%} | score={float(top.get('score', 0.0)):.4f}"
        )
    print(f"summary json: {out_file}")


def main() -> None:
    args = build_parser().parse_args()
    market = resolve_market(resolve_target(args))
    payload = discover_market_wallets(
        market=market,
        holders_depth=args.holders_depth,
        max_market_trades=args.max_market_trades,
        max_candidate_wallets=args.max_candidate_wallets,
        top_wallets=args.top_wallets,
        score_mode=args.score_mode,
        max_user_trades=args.max_user_trades,
        behavior_max_trades=args.behavior_max_trades,
        min_win_rate=args.min_win_rate,
        min_resolved_trades=args.min_resolved_trades,
        min_resolved_markets=args.min_resolved_markets,
        include_sells=args.include_sells,
        min_position_notional=args.min_position_notional,
        min_pnl_abs=args.min_pnl_abs,
        page_size=args.page_size,
    )
    out_file = Path(args.out_file)
    write_json(out_file, payload)
    print_summary(payload, out_file)


if __name__ == "__main__":
    main()
