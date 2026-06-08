#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.copy_trade_wallet_research import (
    analyze_rows,
    get_open_positions,
    iter_closed_positions,
    norm_addr,
    num,
)


DEFAULT_EVENT_SLUGS = [
    "what-kind-of-product-will-openai-announce-in-2026",
    "which-company-has-second-best-ai-model-end-of-june",
    "which-company-has-best-ai-model-end-of-june",
    "who-will-close-warner-bros-acquisition",
    "will-anthropic-or-openai-ipo-first",
    "time-person-of-the-year-2026",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_session() -> requests.Session:
    session = requests.Session()
    session.mount(
        "https://",
        HTTPAdapter(
            max_retries=Retry(
                total=4,
                connect=4,
                read=4,
                backoff_factor=0.5,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=frozenset(["GET"]),
            )
        ),
    )
    session.headers.update({"Accept": "application/json", "User-Agent": "pm-agent-rule-edge-wallet-research/1.0"})
    return session


def json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def get_event(session: requests.Session, slug: str) -> dict[str, Any] | None:
    response = session.get("https://gamma-api.polymarket.com/events", params={"slug": slug}, timeout=25)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, list) and payload:
        return payload[0]
    return None


def get_holders(session: requests.Session, condition_id: str, holders_per_market: int) -> list[dict[str, Any]]:
    response = session.get(
        "https://data-api.polymarket.com/holders",
        params={"market": condition_id, "limit": holders_per_market},
        timeout=25,
    )
    response.raise_for_status()
    payload = response.json()
    holders: list[dict[str, Any]] = []
    if isinstance(payload, list):
        for block in payload:
            if isinstance(block, dict) and isinstance(block.get("holders"), list):
                token = block.get("token")
                for holder in block["holders"]:
                    if isinstance(holder, dict):
                        row = dict(holder)
                        row["_token"] = token
                        holders.append(row)
    return holders


def collect_wallets(
    session: requests.Session,
    event_slugs: list[str],
    min_price: float,
    max_price: float,
    min_market_liquidity: float,
    holders_per_market: int,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    markets: list[dict[str, Any]] = []
    wallets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "name": "",
            "source_markets": [],
            "total_holder_amount": 0.0,
            "max_holder_amount": 0.0,
            "mid_price_holder_amount": 0.0,
        }
    )
    for slug in event_slugs:
        event = get_event(session, slug)
        if not event:
            continue
        for market in event.get("markets") or []:
            prices = []
            for price in json_list(market.get("outcomePrices")):
                try:
                    prices.append(float(price))
                except Exception:
                    pass
            if not prices:
                continue
            yes_price = prices[0]
            no_price = prices[1] if len(prices) > 1 else 1.0 - yes_price
            if not (min_price <= yes_price <= max_price or min_price <= no_price <= max_price):
                continue
            liquidity = num(market.get("liquidityClob") or market.get("liquidity"))
            if liquidity < min_market_liquidity:
                continue
            condition_id = str(market.get("conditionId") or "")
            if not condition_id:
                continue
            outcomes = json_list(market.get("outcomes"))
            market_row = {
                "event": event.get("title") or "",
                "event_slug": slug,
                "question": market.get("question") or "",
                "condition_id": condition_id,
                "prices": prices,
                "liquidity": liquidity,
                "volume24hr": num(market.get("volume24hr")),
                "link": "https://polymarket.com/event/" + slug,
            }
            markets.append(market_row)
            try:
                holders = get_holders(session, condition_id, holders_per_market)
            except Exception as exc:
                market_row["holders_error"] = str(exc)
                holders = []
            for holder in holders:
                wallet = norm_addr(holder.get("proxyWallet"))
                if not wallet:
                    continue
                amount = num(holder.get("amount"))
                name = str(holder.get("name") or holder.get("pseudonym") or "").strip()
                wallets[wallet]["name"] = name or wallets[wallet]["name"]
                wallets[wallet]["total_holder_amount"] += amount
                wallets[wallet]["max_holder_amount"] = max(wallets[wallet]["max_holder_amount"], amount)
                wallets[wallet]["mid_price_holder_amount"] += amount
                outcome_index = holder.get("outcomeIndex")
                outcome_label = None
                try:
                    idx = int(outcome_index)
                    if 0 <= idx < len(outcomes):
                        outcome_label = outcomes[idx]
                except Exception:
                    pass
                wallets[wallet]["source_markets"].append(
                    {
                        "event": market_row["event"],
                        "question": market_row["question"],
                        "outcome_index": outcome_index,
                        "outcome": outcome_label,
                        "asset": holder.get("asset") or holder.get("_token"),
                        "amount": round(amount, 2),
                        "prices": prices,
                        "link": market_row["link"],
                    }
                )
            time.sleep(0.04)
        time.sleep(0.08)
    return markets, wallets


def review_wallets(
    session: requests.Session,
    wallets: dict[str, dict[str, Any]],
    max_wallets: int,
    max_closed_rows: int,
) -> list[dict[str, Any]]:
    ranked_wallets = sorted(
        wallets.items(),
        key=lambda item: (
            len(item[1]["source_markets"]),
            item[1]["mid_price_holder_amount"],
            item[1]["total_holder_amount"],
        ),
        reverse=True,
    )
    reviewed: list[dict[str, Any]] = []
    for wallet, source in ranked_wallets[:max_wallets]:
        try:
            closed_rows = iter_closed_positions(session, wallet, max_closed_rows)
            open_rows = get_open_positions(session, wallet)
            score, verdict, metrics, reasons, summary = analyze_rows(wallet, closed_rows, open_rows, max_closed_rows)
            reviewed.append(
                {
                    "wallet": wallet,
                    "name": source["name"],
                    "source_market_count": len(source["source_markets"]),
                    "total_holder_amount": round(source["total_holder_amount"], 2),
                    "max_holder_amount": round(source["max_holder_amount"], 2),
                    "score": score,
                    "verdict": verdict,
                    "summary": summary,
                    "metrics": metrics,
                    "reasons": reasons,
                    "source_markets": sorted(source["source_markets"], key=lambda row: -row["amount"])[:10],
                }
            )
        except Exception as exc:
            reviewed.append(
                {
                    "wallet": wallet,
                    "name": source["name"],
                    "source_market_count": len(source["source_markets"]),
                    "total_holder_amount": round(source["total_holder_amount"], 2),
                    "error": str(exc),
                    "source_markets": sorted(source["source_markets"], key=lambda row: -row["amount"])[:10],
                }
            )
        time.sleep(0.08)
    return reviewed


def sort_reviews(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        reviews,
        key=lambda row: (
            1 if row.get("verdict") in {"paper_candidate", "watch"} else 0,
            row.get("score", -999),
            row.get("source_market_count", 0),
            row.get("total_holder_amount", 0),
        ),
        reverse=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Research smart-wallet candidates from rule-edge Polymarket markets.")
    parser.add_argument("--out", required=True, help="Output JSON path")
    parser.add_argument("--event-slug", action="append", default=[], help="Event slug to scan; repeatable")
    parser.add_argument("--min-price", type=float, default=0.08)
    parser.add_argument("--max-price", type=float, default=0.88)
    parser.add_argument("--min-market-liquidity", type=float, default=500.0)
    parser.add_argument("--holders-per-market", type=int, default=30)
    parser.add_argument("--max-wallets", type=int, default=45)
    parser.add_argument("--max-closed-rows", type=int, default=500)
    args = parser.parse_args()

    session = build_session()
    event_slugs = args.event_slug or DEFAULT_EVENT_SLUGS
    markets, wallets = collect_wallets(
        session=session,
        event_slugs=event_slugs,
        min_price=args.min_price,
        max_price=args.max_price,
        min_market_liquidity=args.min_market_liquidity,
        holders_per_market=args.holders_per_market,
    )
    reviewed = review_wallets(
        session=session,
        wallets=wallets,
        max_wallets=args.max_wallets,
        max_closed_rows=args.max_closed_rows,
    )
    output = {
        "generated_at_utc": utc_now(),
        "config": {
            "event_slugs": event_slugs,
            "price_range": [args.min_price, args.max_price],
            "min_market_liquidity": args.min_market_liquidity,
            "holders_per_market": args.holders_per_market,
            "max_wallets_reviewed": args.max_wallets,
            "max_closed_rows": args.max_closed_rows,
        },
        "markets_count": len(markets),
        "wallets_found": len(wallets),
        "reviewed_count": len(reviewed),
        "markets": sorted(markets, key=lambda row: (-row["liquidity"], row["question"])),
        "top_reviewed": sort_reviews(reviewed),
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")
    print(f"markets={len(markets)} wallets_found={len(wallets)} reviewed={len(reviewed)}")


if __name__ == "__main__":
    main()
