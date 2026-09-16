"""
Copy Trade Thematic Scanner

针对非伊朗主题（crypto/tech/economics/culture）进行主题扫描，
从活跃市场中发现高质量地址，入库到 research.db。

Usage:
    python3 scripts/copy_trade/copy_trade_thematic_scan.py
    python3 scripts/copy_trade/copy_trade_thematic_scan.py --themes crypto,tech
    python3 scripts/copy_trade/copy_trade_thematic_scan.py --market-limit 30 --min-holders 3
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
DEFAULT_DB = "runtime/db/research.db"

# 主题搜索配置: (搜索关键词, 主题标签, 排除关键词)
THEME_CONFIGS: list[tuple[str, str, list[str]]] = [
    ("bitcoin", "crypto", ["weather", "temperature"]),
    ("ethereum", "crypto", ["weather", "temperature"]),
    ("solana", "crypto", ["weather", "temperature"]),
    ("OpenAI", "tech", []),
    ("Nvidia", "tech", []),
    ("Tesla", "tech", []),
    ("SpaceX", "tech", []),
    ("Fed interest rate", "economics", []),
    ("CPI inflation", "economics", []),
    ("recession", "economics", []),
    ("tariff", "economics", []),
    ("Oscar", "culture", []),
    ("Grammy", "culture", []),
]

IRAN_RE = re.compile(r"\biran|iranian|tehran|khamenei|pahlavi|hormuz|kharg\b", re.I)
SPORTS_RE = re.compile(
    r"\b(nba|nfl|mlb|nhl|epl|uefa|fifa|ufc|tennis|soccer|football|basketball|baseball|hockey|cricket|golf|fight|vs\.?|over [0-9]|under [0-9]|spread)\b",
    re.I,
)
ELECTION_RE = re.compile(
    r"\btrump|biden|president|election|senate|house|governor|mayor|primary|democrat|republican|gop\b",
    re.I,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_id(*parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


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
        headers={"Accept": "application/json", "User-Agent": "pm-agent-copytrade-thematic/1.0"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def upsert_wallet(conn: sqlite3.Connection, wallet: str, display_name: str | None, tags: list[str], traits: dict[str, Any]) -> None:
    now = utc_now()
    existing = conn.execute(
        "SELECT tags_json, traits_json, display_name FROM copy_trade_wallets WHERE wallet_address = ?", (wallet,)
    ).fetchone()
    if existing:
        old_tags = set(json.loads(existing["tags_json"] or "[]"))
        old_traits = json.loads(existing["traits_json"] or "{}")
        old_traits.update({k: v for k, v in traits.items() if v is not None})
        merged_tags = sorted(old_tags | set(tags))
        conn.execute(
            """
            UPDATE copy_trade_wallets
            SET display_name = COALESCE(NULLIF(?, ''), display_name),
                tags_json = ?,
                traits_json = ?,
                last_seen_at = ?
            WHERE wallet_address = ?
            """,
            (display_name or "", json_dumps(merged_tags), json_dumps(old_traits), now, wallet),
        )
    else:
        conn.execute(
            """
            INSERT INTO copy_trade_wallets
              (wallet_address, display_name, status, tags_json, traits_json, first_seen_at, last_seen_at)
            VALUES (?, ?, 'candidate', ?, ?, ?, ?)
            """,
            (wallet, display_name, json_dumps(sorted(set(tags))), json_dumps(traits), now, now),
        )


def insert_discovery(
    conn: sqlite3.Connection,
    run_id: str,
    wallet: str,
    method: str,
    source_ref: str,
    evidence: dict[str, Any],
    strength: float,
    tags: list[str],
) -> None:
    discovery_id = stable_id(run_id, wallet, method, source_ref)
    conn.execute(
        """
        INSERT OR IGNORE INTO copy_trade_wallet_discoveries
          (discovery_id, run_id, wallet_address, method, source_ref, evidence_json, discovered_at, strength, tags_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (discovery_id, run_id, wallet, method, source_ref, json_dumps(evidence), utc_now(), strength, json_dumps(tags)),
    )


def search_markets(session: requests.Session, keyword: str, limit: int) -> list[dict[str, Any]]:
    """Gamma API 搜索市场"""
    try:
        markets = get_json(
            session,
            GAMMA_API,
            "/markets",
            {"limit": limit, "closed": "false", "active": "true", "order": "volume24hr", "ascending": "false"},
        )
    except Exception as exc:
        print(f"  [WARN] markets fetch failed: {exc}")
        return []

    if not isinstance(markets, list):
        return []

    keyword_lower = keyword.lower()
    matched = []
    for m in markets:
        if not isinstance(m, dict):
            continue
        text = " ".join(str(m.get(k) or "") for k in ("question", "slug", "description")).lower()
        if keyword_lower in text:
            matched.append(m)

    return matched


def is_iran_polluted(market: dict[str, Any]) -> bool:
    text = " ".join(str(market.get(k) or "") for k in ("question", "slug", "description")).lower()
    return bool(IRAN_RE.search(text))


def is_sports(market: dict[str, Any]) -> bool:
    text = " ".join(str(market.get(k) or "") for k in ("question", "slug", "description")).lower()
    return bool(SPORTS_RE.search(text))


def scan_theme(
    session: requests.Session,
    conn: sqlite3.Connection,
    run_id: str,
    keyword: str,
    theme_tag: str,
    exclude_keywords: list[str],
    market_limit: int,
    min_holders: int,
) -> dict[str, Any]:
    """扫描单个主题"""
    print(f"\n  Scanning theme '{theme_tag}' with keyword '{keyword}'...")

    markets = search_markets(session, keyword, market_limit * 3)
    print(f"    -> {len(markets)} markets matched keyword")

    # 过滤排除的市场
    filtered = []
    for m in markets:
        if is_iran_polluted(m):
            continue
        if is_sports(m):
            continue
        text = " ".join(str(m.get(k) or "") for k in ("question", "slug", "description")).lower()
        if any(excl in text for excl in exclude_keywords):
            continue
        filtered.append(m)

    filtered = filtered[:market_limit]
    print(f"    -> {len(filtered)} markets after filter (no Iran, no sports)")

    wallets: dict[str, dict[str, Any]] = {}
    for m in filtered:
        condition_id = str(m.get("conditionId") or "").strip()
        if not condition_id:
            continue

        # holders
        try:
            holders_payload = get_json(session, DATA_API, "/holders", {"market": condition_id, "limit": 50})
        except Exception:
            holders_payload = []

        holder_rows: list[dict[str, Any]] = []
        if isinstance(holders_payload, list):
            for token_block in holders_payload:
                if isinstance(token_block, dict) and isinstance(token_block.get("holders"), list):
                    holder_rows.extend(token_block["holders"])

        for holder in holder_rows:
            addr = norm_addr(holder.get("proxyWallet"))
            if not addr:
                continue
            if addr not in wallets:
                wallets[addr] = {
                    "wallet": addr,
                    "name": str(holder.get("name") or holder.get("pseudonym") or "").strip(),
                    "sources": [],
                    "total_amount": 0.0,
                }
            amount = num(holder.get("amount"))
            wallets[addr]["sources"].append({
                "type": "holder",
                "market": m.get("slug"),
                "condition_id": condition_id,
                "amount": amount,
            })
            wallets[addr]["total_amount"] += amount

        # trades
        try:
            trades = get_json(session, DATA_API, "/trades", {"market": condition_id, "limit": 100, "offset": 0})
        except Exception:
            trades = []

        if isinstance(trades, list):
            for trade in trades:
                addr = norm_addr(trade.get("proxyWallet"))
                if not addr:
                    continue
                if addr not in wallets:
                    wallets[addr] = {
                        "wallet": addr,
                        "name": str(trade.get("name") or trade.get("pseudonym") or "").strip(),
                        "sources": [],
                        "total_amount": 0.0,
                    }
                size = num(trade.get("size"))
                price = num(trade.get("price"))
                wallets[addr]["sources"].append({
                    "type": "trade",
                    "market": m.get("slug"),
                    "condition_id": condition_id,
                    "size": size,
                    "price": price,
                    "notional": size * price,
                })

        time.sleep(0.05)

    # 入库：只保留持有量足够大的地址
    inserted = 0
    for addr, data in wallets.items():
        if data["total_amount"] < min_holders:
            continue

        tags = [theme_tag, "thematic_scan"]
        if data["total_amount"] >= 50:
            tags.append("large_holder")

        upsert_wallet(
            conn,
            addr,
            data["name"] or None,
            tags,
            {"thematic_total_amount": data["total_amount"], "discovery_theme": theme_tag},
        )

        for src in data["sources"]:
            insert_discovery(
                conn,
                run_id,
                addr,
                f"thematic_{src['type']}",
                f"{theme_tag}:{src['condition_id']}",
                {"market_slug": src["market"], **src},
                strength=min(3.0, 1.0 + data["total_amount"] / 20),
                tags=tags,
            )

        inserted += 1

    conn.commit()
    print(f"    -> {inserted} wallets inserted (min_amount={min_holders})")

    return {
        "theme": theme_tag,
        "keyword": keyword,
        "markets_found": len(filtered),
        "wallets_discovered": len(wallets),
        "wallets_inserted": inserted,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy-trade thematic scanner")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--themes", default="crypto,tech,economics,culture", help="Comma-separated themes")
    parser.add_argument("--market-limit", type=int, default=20, help="Max markets per theme keyword")
    parser.add_argument("--min-holders", type=int, default=5, help="Min holder amount to keep wallet")
    args = parser.parse_args()

    selected_themes = set(args.themes.split(","))
    configs = [c for c in THEME_CONFIGS if c[1] in selected_themes]

    session = build_session()
    conn = connect(args.db)

    run_id = f"thematic_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    conn.execute(
        """
        INSERT INTO copy_trade_discovery_runs
          (run_id, run_type, run_name, started_at, status, config_json)
        VALUES (?, 'thematic_scan', ?, ?, 'running', ?)
        """,
        (run_id, run_id, utc_now(), json_dumps({"themes": args.themes, "market_limit": args.market_limit})),
    )
    conn.commit()

    print(f"Thematic scan starting: {run_id}")
    print(f"Themes: {args.themes}")
    print(f"Market limit per keyword: {args.market_limit}")

    results: list[dict[str, Any]] = []
    for keyword, theme_tag, exclude_kw in configs:
        result = scan_theme(session, conn, run_id, keyword, theme_tag, exclude_kw, args.market_limit, args.min_holders)
        results.append(result)
        time.sleep(0.2)

    total_wallets = conn.execute("SELECT COUNT(*) AS n FROM copy_trade_wallets").fetchone()["n"]
    summary = {
        "themes_scanned": len(results),
        "total_wallets": total_wallets,
        "theme_results": results,
    }

    conn.execute(
        "UPDATE copy_trade_discovery_runs SET finished_at = ?, status = 'completed', summary_json = ? WHERE run_id = ?",
        (utc_now(), json_dumps(summary), run_id),
    )
    conn.commit()

    print(f"\nDone. Total wallets in DB: {total_wallets}")
    for r in results:
        print(f"  {r['theme']}: {r['wallets_inserted']} wallets from {r['markets_found']} markets")


if __name__ == "__main__":
    main()
