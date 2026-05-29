"""
Copy Trade Event-Driven Scanner — 真正的事件驱动型市场扫描

排除: crypto价格预测、政治选举、地缘冲突、体育
保留: 公司事件(M&A/IPO/产品)、法律监管、名人文化、科技里程碑

Usage:
    python3 scripts/analysis/copy_trade_event_driven_scanner.py
    python3 scripts/analysis/copy_trade_event_driven_scanner.py --market-limit 200
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
DEFAULT_DB = "runtime/db/research.db"

# 严格排除词
EXCLUDE_RE = re.compile(
    r"\b(bitcoin|btc|ethereum|eth|solana|sol|crypto price|crypto above|crypto below|"
    r"bitcoin hit|bitcoin dip|bitcoin reach|price of bitcoin|price of ethereum|"
    r"trump|biden|election|president|senate|house|governor|mayor|primary|democrat|republican|gop|vote|"
    r"iran|iranian|tehran|khamenei|pahlavi|hormuz|kharg|"
    r"ukraine|russia|israel|gaza|china|taiwan|nato|war|ceasefire|missile|nuclear|"
    r"nba|nfl|mlb|nhl|epl|uefa|fifa|ufc|tennis|soccer|football|basketball|baseball|hockey|cricket|golf|fight|"
    r"vs\.?\s|over [0-9]+|under [0-9]+|spread|"
    r"temperature|weather|rain|snow|hurricane|tornado|degrees|°f|highest-temperature)\b",
    re.I,
)

# 事件驱动型关键词（正向匹配）
EVENT_KEYWORDS = [
    # 公司事件
    "merger", "acquisition", "acquire", "merge", "ipo", "public", "listing", "ticker",
    "buyout", "takeover", "spinoff", "divest", "antitrust", "monopoly",
    # 产品/技术
    "launch", "release", "product", "iphone", "model", "ship", "delivery",
    "fda", "approval", "clinical trial", "drug", "vaccine",
    "ai model", "gpt", "llm", "chatgpt", "gemini", "claude",
    # 法律/监管
    "lawsuit", "sue", "settlement", "verdict", "court", "judge", "ruling",
    "sec", "fine", "sanction", "ban", "regulate",
    # 名人/文化
    "oscar", "grammy", "emmy", "nobel", "taylor swift", "album", "tour",
    "movie", "film", "box office", "release date",
    # 科技里程碑
    "starship", "mars", "moon", "landing", "orbit", "space",
    "tesla", "spacex", "apple", "google", "microsoft", "meta", "nvidia",
    # 宏观经济事件（非价格）
    "recession", "gdp contraction", "bank failure", "default", "shutdown",
]

# 价格相关排除
PRICE_RE = re.compile(r"\$[0-9,]+|above \$|below \$|hit \$|reach \$|dip to \$|between \$", re.I)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_session() -> requests.Session:
    session = requests.Session()
    session.mount(
        "https://",
        HTTPAdapter(
            max_retries=Retry(
                total=3,
                connect=3,
                read=3,
                backoff_factor=0.4,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=frozenset(["GET"]),
            )
        ),
    )
    return session


def get_json(session: requests.Session, base: str, path: str, params: dict[str, Any], timeout: float = 25.0) -> Any:
    resp = session.get(
        base + path,
        params={k: v for k, v in params.items() if v is not None and v != ""},
        headers={"Accept": "application/json", "User-Agent": "pm-agent-copytrade-event/1.0"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def norm_addr(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text.startswith("0x") and len(text) == 42 else ""


def num(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def is_event_driven(market: dict[str, Any]) -> tuple[bool, str]:
    """
    判断市场是否是事件驱动型。
    返回: (是否匹配, 匹配原因)
    """
    text = " ".join(str(market.get(k) or "") for k in ("question", "slug", "description")).lower()

    # 先排除
    if EXCLUDE_RE.search(text):
        return False, "excluded"

    # 排除纯价格预测
    if PRICE_RE.search(text) and ("bitcoin" in text or "btc" in text or "ethereum" in text or "eth" in text):
        return False, "crypto_price"

    # 正向匹配事件关键词
    matched_kw = []
    for kw in EVENT_KEYWORDS:
        if kw.lower() in text:
            matched_kw.append(kw)

    if matched_kw:
        return True, f"matched: {matched_kw[:3]}"

    return False, "no_event_keywords"


def fetch_active_markets(session: requests.Session, limit: int) -> list[dict[str, Any]]:
    try:
        markets = get_json(
            session,
            GAMMA_API,
            "/markets",
            {"limit": limit, "closed": "false", "active": "true", "order": "volume24hr", "ascending": "false"},
        )
    except Exception as exc:
        print(f"[ERROR] markets fetch failed: {exc}")
        return []

    if not isinstance(markets, list):
        return []

    event_markets = []
    for m in markets:
        if not isinstance(m, dict):
            continue
        is_event, reason = is_event_driven(m)
        if is_event:
            m["_match_reason"] = reason
            event_markets.append(m)

    return event_markets


def fetch_holders(session: requests.Session, condition_id: str) -> list[dict[str, Any]]:
    try:
        payload = get_json(session, DATA_API, "/holders", {"market": condition_id, "limit": 50})
    except Exception:
        return []

    holders = []
    if isinstance(payload, list):
        for block in payload:
            if isinstance(block, dict) and isinstance(block.get("holders"), list):
                holders.extend(block["holders"])
    return holders


def main() -> None:
    parser = argparse.ArgumentParser(description="Event-driven market scanner")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--market-limit", type=int, default=200)
    parser.add_argument("--min-amount", type=float, default=50.0)
    args = parser.parse_args()

    session = build_session()

    print(f"Fetching markets (limit={args.market_limit})...")
    markets = fetch_active_markets(session, args.market_limit)
    print(f"Found {len(markets)} event-driven markets")

    if not markets:
        print("No event-driven markets found. Try increasing --market-limit.")
        return

    # 打印找到的市场
    print("\n## Event-Driven Markets\n")
    print("| # | question | volume24hr | liquidity | match_reason |")
    print("|---|---|---|---|---|")
    for i, m in enumerate(markets[:20], 1):
        q = m.get("question", "")[:50]
        vol = num(m.get("volume24hr") or m.get("volume24hrClob"))
        liq = num(m.get("liquidity") or m.get("liquidityClob"))
        print(f"| {i} | {q} | ${vol:,.0f} | ${liq:,.0f} | {m['_match_reason']} |")

    # 收集持仓者
    all_wallets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"name": "", "markets": [], "total_amount": 0.0, "max_single_position": 0.0}
    )

    for i, m in enumerate(markets, 1):
        condition_id = str(m.get("conditionId") or "").strip()
        if not condition_id:
            continue

        question = m.get("question", "")[:60]
        print(f"\n[{i}/{len(markets)}] {question}...")

        holders = fetch_holders(session, condition_id)
        print(f"  -> {len(holders)} holders")

        for h in holders:
            addr = norm_addr(h.get("proxyWallet"))
            if not addr:
                continue
            amount = num(h.get("amount"))
            if amount < args.min_amount:
                continue

            name = str(h.get("name") or h.get("pseudonym") or "").strip()
            all_wallets[addr]["name"] = name or all_wallets[addr]["name"]
            all_wallets[addr]["markets"].append({
                "question": m.get("question"),
                "slug": m.get("slug"),
                "condition_id": condition_id,
                "amount": amount,
                "volume24hr": num(m.get("volume24hr") or m.get("volume24hrClob")),
                "liquidity": num(m.get("liquidity") or m.get("liquidityClob")),
                "match_reason": m["_match_reason"],
            })
            all_wallets[addr]["total_amount"] += amount
            all_wallets[addr]["max_single_position"] = max(
                all_wallets[addr]["max_single_position"], amount
            )

        time.sleep(0.1)

    # 排序
    sorted_wallets = sorted(all_wallets.items(), key=lambda x: -x[1]["total_amount"])

    print(f"\n\n{'='*60}")
    print(f"SUMMARY: {len(markets)} markets, {len(all_wallets)} unique wallets")
    print(f"{'='*60}")

    # Top 钱包
    print("\n## Top Wallets by Total Amount\n")
    print("| rank | wallet | name | markets | total | max_single |")
    print("|---:|---|---|---:|---:|---:|")
    for rank, (addr, data) in enumerate(sorted_wallets[:30], 1):
        name = data["name"] or "-"
        print(f"| {rank} | {addr[:16]}... | {name} | {len(data['markets'])} | {data['total_amount']:,.0f} | {data['max_single_position']:,.0f} |")

    # 按市场分类的Top持仓
    print("\n## Top Positions by Market\n")
    for m in markets[:10]:
        condition_id = str(m.get("conditionId") or "").strip()
        if not condition_id:
            continue

        q = m.get("question", "")[:50]
        market_wallets = []
        for addr, data in all_wallets.items():
            for pos in data["markets"]:
                if pos["condition_id"] == condition_id:
                    market_wallets.append({
                        "wallet": addr,
                        "name": data["name"],
                        "amount": pos["amount"],
                    })

        if not market_wallets:
            continue

        market_wallets.sort(key=lambda x: -x["amount"])
        print(f"\n### {q}")
        print("| wallet | name | amount |")
        print("|---|---|---:|")
        for w in market_wallets[:8]:
            print(f"| {w['wallet'][:16]}... | {w['name'] or '-'} | {w['amount']:,.0f} |")

    # 输出JSON
    out_dir = Path("runtime/copy_trade")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    output = {
        "generated_at": utc_now(),
        "markets_scanned": len(markets),
        "wallets_found": len(all_wallets),
        "top_wallets": [
            {
                "wallet": addr,
                "name": data["name"],
                "total_amount": data["total_amount"],
                "max_single_position": data["max_single_position"],
                "market_count": len(data["markets"]),
                "markets": data["markets"],
            }
            for addr, data in sorted_wallets[:50]
        ],
        "markets": [
            {
                "condition_id": m.get("conditionId"),
                "slug": m.get("slug"),
                "question": m.get("question"),
                "volume24hr": num(m.get("volume24hr") or m.get("volume24hrClob")),
                "liquidity": num(m.get("liquidity") or m.get("liquidityClob")),
                "match_reason": m.get("_match_reason"),
            }
            for m in markets
        ],
    }

    json_path = out_dir / f"event_driven_scan_{stamp}.json"
    json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n\nOutput saved: {json_path}")


if __name__ == "__main__":
    main()
