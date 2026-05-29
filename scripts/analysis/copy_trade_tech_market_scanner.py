"""
Copy Trade Tech/Business Market Scanner

搜索非政治类的科技/商业/文化市场，发现活跃的高质量地址。
排除: 政治、地缘、伊朗、体育、天气

Usage:
    python3 scripts/analysis/copy_trade_tech_market_scanner.py
    python3 scripts/analysis/copy_trade_tech_market_scanner.py --market-limit 30
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

# 排除词
POLITICS_RE = re.compile(
    r"\b(trump|biden|election|senate|house|governor|mayor|primary|democrat|republican|gop|president|vote|congress|parliament|chancellor)\b",
    re.I,
)
GEOPOL_RE = re.compile(r"\b(iran|iranian|tehran|khamenei|pahlavi|hormuz|kharg|ukraine|russia|israel|gaza|china|taiwan|nato|war|ceasefire|missile|nuclear)\b", re.I)
SPORTS_RE = re.compile(
    r"\b(nba|nfl|mlb|nhl|epl|uefa|fifa|ufc|tennis|soccer|football|basketball|baseball|hockey|cricket|golf|fight|vs\.?|over [0-9]|under [0-9]|spread)\b",
    re.I,
)
WEATHER_RE = re.compile(r"\btemperature|weather|rain|snow|hurricane|tornado|degrees|°f|highest-temperature\b", re.I)

# 科技/商业/文化关键词
TECH_BUSINESS_KEYWORDS = [
    "tesla", "spacex", "spacex ipo", "elon musk", "openai", "nvidia", "apple", "google", "microsoft",
    "meta", "facebook", "bitcoin", "ethereum", "crypto", "ai", "artificial intelligence",
    "ipo", "merger", "acquisition", "stock", "nasdaq", "dow jones", "s&p 500",
    "oscar", "grammy", "emmy", "nobel", "taylor swift", "kanye", "movie", "album",
    "gta", "game", "nintendo", "playstation", "xbox",
    "fed", "interest rate", "cpi", "inflation", "recession", "gdp", "unemployment",
]


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
        headers={"Accept": "application/json", "User-Agent": "pm-agent-copytrade-tech/1.0"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def is_political(text: str) -> bool:
    return bool(POLITICS_RE.search(text))


def is_geopolitical(text: str) -> bool:
    return bool(GEOPOL_RE.search(text))


def is_sports(text: str) -> bool:
    return bool(SPORTS_RE.search(text))


def is_weather(text: str) -> bool:
    return bool(WEATHER_RE.search(text))


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


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def fetch_active_markets(session: requests.Session, limit: int) -> list[dict[str, Any]]:
    """拉取活跃市场列表"""
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

    # 过滤非科技/商业/文化类市场
    filtered = []
    for m in markets:
        if not isinstance(m, dict):
            continue
        text = " ".join(str(m.get(k) or "") for k in ("question", "slug", "description")).lower()

        # 排除政治/地缘/体育/天气
        if is_political(text) or is_geopolitical(text) or is_sports(text) or is_weather(text):
            continue

        # 保留包含科技/商业/文化关键词的
        if any(kw in text for kw in TECH_BUSINESS_KEYWORDS):
            filtered.append(m)

    return filtered


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
    parser = argparse.ArgumentParser(description="Tech/Business market scanner")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--market-limit", type=int, default=100)
    parser.add_argument("--min-amount", type=float, default=10.0, help="Min holder amount to track")
    args = parser.parse_args()

    session = build_session()
    conn = connect(args.db)

    print(f"Fetching active markets (limit={args.market_limit})...")
    markets = fetch_active_markets(session, args.market_limit)
    print(f"Found {len(markets)} tech/business/culture markets")

    # 统计
    market_wallets: dict[str, list[dict[str, Any]]] = {}
    all_wallets: dict[str, dict[str, Any]] = defaultdict(lambda: {"name": "", "markets": [], "total_amount": 0.0})

    for i, m in enumerate(markets, 1):
        condition_id = str(m.get("conditionId") or "").strip()
        if not condition_id:
            continue

        question = m.get("question", "")[:80]
        print(f"\n[{i}/{len(markets)}] {question}...")

        holders = fetch_holders(session, condition_id)
        print(f"  -> {len(holders)} holder entries")

        market_wallets[condition_id] = []
        for h in holders:
            addr = norm_addr(h.get("proxyWallet"))
            if not addr:
                continue
            amount = num(h.get("amount"))
            if amount < args.min_amount:
                continue

            name = str(h.get("name") or h.get("pseudonym") or "").strip()
            entry = {
                "wallet": addr,
                "name": name,
                "amount": amount,
                "outcome_index": h.get("outcomeIndex"),
            }
            market_wallets[condition_id].append(entry)

            all_wallets[addr]["name"] = name or all_wallets[addr]["name"]
            all_wallets[addr]["markets"].append({
                "question": m.get("question"),
                "slug": m.get("slug"),
                "condition_id": condition_id,
                "amount": amount,
                "volume24hr": num(m.get("volume24hr") or m.get("volume24hrClob")),
                "liquidity": num(m.get("liquidity") or m.get("liquidityClob")),
            })
            all_wallets[addr]["total_amount"] += amount

        time.sleep(0.1)

    # 排序：按持仓总量
    sorted_wallets = sorted(all_wallets.items(), key=lambda x: -x[1]["total_amount"])

    print(f"\n\n{'='*60}")
    print(f"SUMMARY: {len(markets)} markets, {len(all_wallets)} unique wallets")
    print(f"{'='*60}")

    # Top 30 钱包
    print("\n## Top Wallets by Total Amount\n")
    print("| rank | wallet | name | markets | total_amount |")
    print("|---:|---|---|---:|---:|")
    for rank, (addr, data) in enumerate(sorted_wallets[:30], 1):
        name = data["name"] or "-"
        print(f"| {rank} | {addr} | {name} | {len(data['markets'])} | {data['total_amount']:,.0f} |")

    # 每个市场的Top持仓
    print("\n## Market Details\n")
    for m in markets[:15]:
        condition_id = str(m.get("conditionId") or "").strip()
        if not condition_id or condition_id not in market_wallets:
            continue

        question = m.get("question", "")[:60]
        volume = num(m.get("volume24hr") or m.get("volume24hrClob"))
        liquidity = num(m.get("liquidity") or m.get("liquidityClob"))

        wallets = sorted(market_wallets[condition_id], key=lambda x: -x["amount"])[:10]
        if not wallets:
            continue

        print(f"\n### {question}")
        print(f"volume24hr: ${volume:,.0f}, liquidity: ${liquidity:,.0f}")
        print("")
        print("| wallet | name | amount | outcome |")
        print("|---|---|---:|---|")
        for w in wallets:
            print(f"| {w['wallet'][:16]}... | {w['name'] or '-'} | {w['amount']:,.0f} | {w.get('outcome_index', '-')} |")

    # 检查哪些钱包已经在研究数据库中
    known_addrs = set()
    for row in conn.execute("SELECT wallet_address FROM copy_trade_wallets").fetchall():
        known_addrs.add(row["wallet_address"])

    new_wallets = [(addr, data) for addr, data in sorted_wallets if addr not in known_addrs]
    print(f"\n## New Wallets (not in research.db): {len(new_wallets)}")
    print("\n| wallet | name | markets | total_amount |")
    print("|---|---|---:|---:|")
    for addr, data in new_wallets[:20]:
        name = data["name"] or "-"
        print(f"| {addr} | {name} | {len(data['markets'])} | {data['total_amount']:,.0f} |")

    # 输出JSON
    out_dir = Path("runtime/copy_trade")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    output = {
        "generated_at": utc_now(),
        "markets_scanned": len(markets),
        "wallets_found": len(all_wallets),
        "new_wallets": len(new_wallets),
        "top_wallets": [
            {
                "wallet": addr,
                "name": data["name"],
                "total_amount": data["total_amount"],
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
            }
            for m in markets
        ],
    }

    json_path = out_dir / f"tech_market_scan_{stamp}.json"
    json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n\nOutput saved: {json_path}")


if __name__ == "__main__":
    main()
