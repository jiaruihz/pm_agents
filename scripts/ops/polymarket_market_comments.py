#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.rule_lawyer.services.comment_intel import collect_market_comments
from src.strategies.rule_lawyer.services.market_resolver import resolve_market
from src.strategies.rule_lawyer.services.reporting import write_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch and score Polymarket market comments.")
    parser.add_argument("--target-market", required=True, help="Polymarket market URL, slug, or condition id")
    parser.add_argument("--comment-limit", type=int, default=30)
    parser.add_argument("--mode", choices=["top_only", "top_and_newest"], default="top_and_newest")
    parser.add_argument("--out-file", default="runtime/market_comments.json")
    parser.add_argument("--raw-file", default="", help="Optional raw comment dump path")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    market = resolve_market(args.target_market)
    result = collect_market_comments(market=market, comment_limit=args.comment_limit, mode=args.mode)
    out_file = Path(args.out_file)
    write_json(out_file, result)
    if args.raw_file:
        Path(args.raw_file).parent.mkdir(parents=True, exist_ok=True)
        Path(args.raw_file).write_text(json.dumps(result.get("raw_comments", []), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"market: {market.slug or market.market_id}")
    print(f"comment status: {result.get('comment_status', '')}")
    print(f"top commentary: {len(result.get('top_commentary', []))}")
    print(f"summary json: {out_file}")


if __name__ == "__main__":
    main()
