#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


DEFAULT_KEYWORDS = (
    "acquisition|ipo|supreme|court|ban|approval|nobel|time person|oscar|grammy|"
    "warner|paramount|app store|tiktok|openai|anthropic|google|claude|gpt|gemini|"
    "model|leaderboard|release|announce"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Print selected rows from rule-edge discovery JSON.")
    parser.add_argument("json_path")
    parser.add_argument("--keywords", default=DEFAULT_KEYWORDS)
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--wallets", action="store_true", help="Print wallet source rows instead of markets.")
    args = parser.parse_args()

    data = json.loads(Path(args.json_path).read_text(encoding="utf-8"))
    pattern = re.compile(args.keywords, re.I)
    if args.wallets:
        for row in (data.get("wallet_sources") or [])[: args.limit]:
            print(
                f"{row.get('name') or '-'} {row.get('wallet')} "
                f"markets={row.get('source_market_count')} amount={row.get('total_holder_amount')}"
            )
            for source in (row.get("source_markets") or [])[:5]:
                print(
                    f"  {source.get('outcome')} amt={source.get('amount')} "
                    f"{source.get('question')}"
                )
        return

    rows = []
    for row in data.get("markets") or []:
        blob = " ".join(str(row.get(key) or "") for key in ("event", "question", "event_slug"))
        if pattern.search(blob):
            rows.append(row)

    for row in rows[: args.limit]:
        prices = row.get("prices") or []
        yes = prices[0] if prices else None
        print(
            f"{yes!s:>6} liq={row.get('liquidity', 0):>8} vol={row.get('volume', 0):>9} "
            f"{row.get('question')} :: {row.get('event_slug')}"
        )


if __name__ == "__main__":
    main()
