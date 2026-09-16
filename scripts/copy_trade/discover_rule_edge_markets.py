#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXCLUDE_PATTERNS = [
    r"\bfed\b",
    r"fomc",
    r"interest rate",
    r"\bnba\b",
    r"\bnfl\b",
    r"\bnhl\b",
    r"\bmlb\b",
    r"\buefa\b",
    r"\bsoccer\b",
    r"\bcricket\b",
    r"\bweather\b",
    r"\brain\b",
    r"temperature",
    r"\bbitcoin\b",
    r"\bethereum\b",
    r"\bbtc\b",
    r"\beth\b",
    r"\bsolana\b",
    r"\bgold\b",
    r"\boil\b",
    r"stock price",
    r"market cap",
    r"largest company",
    r"election",
    r"president",
]

RULE_EDGE_PATTERNS = [
    r"according to",
    r"resolve",
    r"resolution",
    r"official",
    r"announce",
    r"unveil",
    r"release",
    r"acquisition",
    r"close",
    r"merger",
    r"ipo",
    r"spac",
    r"rank",
    r"leaderboard",
    r"benchmark",
    r"lmarena",
    r"model",
    r"court",
    r"settlement",
    r"law",
    r"rule",
    r"ban",
    r"approval",
    r"confirmed",
    r"person of the year",
    r"nobel",
    r"oscar",
    r"grammy",
]

THEME_PATTERNS = [
    r"\bopenai\b",
    r"\banthropic\b",
    r"\bgoogle\b",
    r"\bmeta\b",
    r"\bapple\b",
    r"\btesla\b",
    r"\bwarner\b",
    r"\bparamount\b",
    r"\bai\b",
    r"\bchatgpt\b",
    r"hardware",
    r"device",
    r"product",
    r"company",
    r"award",
    r"ceo",
    r"app store",
    r"supreme court",
]


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
    session.headers.update({"Accept": "application/json", "User-Agent": "pm-agent-rule-edge-discovery/1.0"})
    return session


def text_blob(event: dict[str, Any], market: dict[str, Any] | None = None) -> str:
    parts = [
        event.get("title"),
        event.get("slug"),
        event.get("description"),
        event.get("resolutionSource"),
        event.get("category"),
    ]
    if market:
        parts.extend(
            [
                market.get("question"),
                market.get("slug"),
                market.get("description"),
                market.get("resolutionSource"),
                market.get("rules"),
            ]
        )
    return " ".join(str(part or "") for part in parts).lower()


def count_matches(patterns: list[str], text: str) -> int:
    return sum(1 for pattern in patterns if re.search(pattern, text, flags=re.I))


def should_exclude(text: str) -> bool:
    return count_matches(EXCLUDE_PATTERNS, text) > 0


def prices(market: dict[str, Any]) -> list[float]:
    out = []
    for price in json_list(market.get("outcomePrices")):
        try:
            out.append(float(price))
        except Exception:
            pass
    return out


def num(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def fetch_events(session: requests.Session, pages: int, limit: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(pages):
        params = {
            "active": "true",
            "closed": "false",
            "archived": "false",
            "limit": limit,
            "offset": page * limit,
            "order": "volume24hr",
            "ascending": "false",
        }
        response = session.get("https://gamma-api.polymarket.com/events", params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list) or not payload:
            break
        for event in payload:
            if not isinstance(event, dict):
                continue
            slug = str(event.get("slug") or "")
            if slug and slug not in seen:
                seen.add(slug)
                events.append(event)
        time.sleep(0.1)
    return events


def discover(
    events: list[dict[str, Any]],
    min_price: float,
    max_price: float,
    min_liquidity: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        event_text = text_blob(event)
        if should_exclude(event_text):
            continue
        event_rule_score = count_matches(RULE_EDGE_PATTERNS, event_text)
        event_theme_score = count_matches(THEME_PATTERNS, event_text)
        for market in event.get("markets") or []:
            if not isinstance(market, dict):
                continue
            blob = text_blob(event, market)
            if should_exclude(blob):
                continue
            px = prices(market)
            if not px:
                continue
            yes = px[0]
            no = px[1] if len(px) > 1 else 1.0 - yes
            if not (min_price <= yes <= max_price or min_price <= no <= max_price):
                continue
            liquidity = num(market.get("liquidityClob") or market.get("liquidity"))
            if liquidity < min_liquidity:
                continue
            rule_score = event_rule_score + count_matches(RULE_EDGE_PATTERNS, blob)
            theme_score = event_theme_score + count_matches(THEME_PATTERNS, blob)
            if rule_score == 0 or theme_score == 0:
                continue
            volume = num(market.get("volume") or event.get("volume"))
            volume24 = num(market.get("volume24hr") or event.get("volume24hr"))
            rows.append(
                {
                    "score": round(rule_score * 2.0 + theme_score * 1.2 + min(volume, 250_000) / 100_000 + min(liquidity, 25_000) / 25_000, 3),
                    "rule_score": rule_score,
                    "theme_score": theme_score,
                    "event": event.get("title") or "",
                    "event_slug": event.get("slug") or "",
                    "question": market.get("question") or "",
                    "condition_id": market.get("conditionId") or "",
                    "prices": px,
                    "liquidity": round(liquidity, 2),
                    "volume": round(volume, 2),
                    "volume24hr": round(volume24, 2),
                    "end_date": market.get("endDate") or event.get("endDate"),
                    "link": "https://polymarket.com/event/" + str(event.get("slug") or ""),
                }
            )
    return sorted(rows, key=lambda row: (row["score"], row["liquidity"], row["volume"]), reverse=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover active rule-edge Polymarket candidates.")
    parser.add_argument("--out", required=True)
    parser.add_argument("--pages", type=int, default=12)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--min-price", type=float, default=0.08)
    parser.add_argument("--max-price", type=float, default=0.88)
    parser.add_argument("--min-liquidity", type=float, default=500.0)
    args = parser.parse_args()

    session = build_session()
    events = fetch_events(session, args.pages, args.limit)
    rows = discover(events, args.min_price, args.max_price, args.min_liquidity)
    output = {
        "generated_at_utc": utc_now(),
        "config": {
            "pages": args.pages,
            "limit": args.limit,
            "price_range": [args.min_price, args.max_price],
            "min_liquidity": args.min_liquidity,
        },
        "events_scanned": len(events),
        "markets_found": len(rows),
        "markets": rows,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")
    print(f"events_scanned={len(events)} markets_found={len(rows)}")
    for row in rows[:30]:
        print(f"{row['score']:5.1f} {row['prices'][0]:.3f} liq={row['liquidity']:.0f} vol={row['volume']:.0f} {row['question']} :: {row['event_slug']}")


if __name__ == "__main__":
    main()
