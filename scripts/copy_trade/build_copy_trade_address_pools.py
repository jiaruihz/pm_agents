from __future__ import annotations

import csv
import json
import math
import re
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

SPORTS_RE = re.compile(
    r"\b(nba|nfl|mlb|nhl|epl|uefa|fifa|ufc|tennis|soccer|football|basketball|baseball|hockey|cricket|golf|fight|vs\.?|over [0-9]|under [0-9]|spread)\b"
)
WEATHER_RE = re.compile(r"\btemperature|weather|rain|snow|hurricane|tornado|degrees|°f|highest-temperature\b")
ELECTION_RE = re.compile(
    r"\btrump|biden|president|election|senate|house|governor|mayor|primary|democrat|republican|gop\b"
)
GEOPOL_RE = re.compile(r"\bukraine|russia|iran|israel|gaza|china|taiwan|nato|war|ceasefire|missile|nuclear|tariff\b")
CRYPTO_RE = re.compile(r"\bbitcoin|btc|ethereum|eth|solana|sol|crypto|token|xrp|doge|stablecoin|defi\b")
TECH_RE = re.compile(r"\bai|openai|nvidia|tesla|apple|google|meta|microsoft|spacex|starship|ipo\b")
ECON_RE = re.compile(r"\bfed|rate|inflation|cpi|gdp|recession|unemployment|tariff|treasury\b")
CULTURE_RE = re.compile(r"\boscar|grammy|movie|album|song|celebrity|taylor|kendrick|rihanna|gta\b")


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


def get_json(session: requests.Session, base: str, path: str, params: dict[str, Any], timeout: float = 20.0) -> Any:
    resp = session.get(
        base + path,
        params={k: v for k, v in params.items() if v is not None and v != ""},
        headers={"Accept": "application/json", "User-Agent": "pm-agent-copytrade-research/1.0"},
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


def topic_for_text(text: str) -> str:
    if SPORTS_RE.search(text):
        return "sports"
    if WEATHER_RE.search(text):
        return "weather"
    if GEOPOL_RE.search(text):
        return "geopolitics"
    if ELECTION_RE.search(text):
        return "politics"
    if CRYPTO_RE.search(text):
        return "crypto"
    if ECON_RE.search(text):
        return "economics"
    if TECH_RE.search(text):
        return "tech"
    if CULTURE_RE.search(text):
        return "culture"
    return "other"


def market_text(market: dict[str, Any]) -> str:
    return " ".join(str(market.get(key) or "") for key in ("question", "slug", "description", "category")).lower()


def ensure(wallets: dict[str, dict[str, Any]], addr: str) -> dict[str, Any]:
    return wallets.setdefault(
        addr,
        {
            "wallet": addr,
            "names": Counter(),
            "leaderboard_sources": [],
            "market_sources": [],
            "source_counts": Counter(),
            "topics": Counter(),
            "best_rank": None,
            "max_pnl": None,
            "max_vol": None,
        },
    )


def add_name(row: dict[str, Any], name: Any) -> None:
    value = str(name or "").strip()
    if value:
        row["names"][value] += 1


def best_leaderboard_source(row: dict[str, Any]) -> dict[str, Any] | None:
    sources = row["leaderboard_sources"]
    if not sources:
        return None
    return sorted(sources, key=lambda item: (item.get("rank") or 10**9, -item.get("pnl", 0)))[0]


def primary_name(row: dict[str, Any]) -> str:
    return row["names"].most_common(1)[0][0] if row["names"] else ""


def quality_score(row: dict[str, Any]) -> float:
    lb_sources = len(row["leaderboard_sources"])
    market_sources = len(row["market_sources"])
    non_sports_topics = sum(value for key, value in row["topics"].items() if key not in {"sports", "weather"})
    sports_topics = row["topics"].get("sports", 0)
    pnl = max(0.0, row["max_pnl"] or 0.0)
    rank = row["best_rank"] or 9999
    score = 0.0
    score += min(35, lb_sources * 2.5)
    score += min(25, market_sources * 0.5)
    if pnl > 0:
        score += min(20, math.log10(pnl + 1) * 3)
    score += max(0, 15 - math.log10(rank + 1) * 5)
    score += min(15, non_sports_topics * 0.4)
    score -= min(30, sports_topics * 1.5)
    return round(score, 2)


def main() -> None:
    out_dir = Path("runtime/copy_trade")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    session = build_session()
    wallets: dict[str, dict[str, Any]] = {}
    categories = ["OVERALL", "POLITICS", "CRYPTO", "ECONOMICS", "TECH", "SPORTS"]
    periods = ["ALL", "MONTH", "WEEK"]
    leaderboard_rows = 0

    for category in categories:
        for period in periods:
            for offset in range(0, 500, 50):
                try:
                    rows = get_json(
                        session,
                        DATA_API,
                        "/v1/leaderboard",
                        {"category": category, "timePeriod": period, "limit": 50, "offset": offset},
                    )
                except Exception as exc:
                    print("leaderboard_error", category, period, offset, exc)
                    break
                if not isinstance(rows, list) or not rows:
                    break
                for item in rows:
                    addr = norm_addr(item.get("proxyWallet"))
                    if not addr:
                        continue
                    row = ensure(wallets, addr)
                    add_name(row, item.get("userName") or item.get("name"))
                    rank = int(num(item.get("rank")) or 0)
                    pnl = num(item.get("pnl"))
                    vol = num(item.get("vol"))
                    row["leaderboard_sources"].append(
                        {"category": category, "period": period, "rank": rank, "pnl": pnl, "vol": vol}
                    )
                    row["source_counts"][f"leaderboard:{category}:{period}"] += 1
                    row["topics"][category.lower()] += 1
                    row["best_rank"] = rank if row["best_rank"] is None else min(row["best_rank"], rank)
                    row["max_pnl"] = pnl if row["max_pnl"] is None else max(row["max_pnl"], pnl)
                    row["max_vol"] = vol if row["max_vol"] is None else max(row["max_vol"], vol)
                    leaderboard_rows += 1
                if len(rows) < 50:
                    break
                time.sleep(0.03)

    try:
        markets = get_json(
            session,
            GAMMA_API,
            "/markets",
            {"limit": 80, "closed": "false", "active": "true", "order": "volume24hr", "ascending": "false"},
        )
        markets = markets if isinstance(markets, list) else []
    except Exception as exc:
        print("markets_error", exc)
        markets = []

    market_rows: list[dict[str, Any]] = []
    for market in markets:
        if not isinstance(market, dict):
            continue
        condition_id = str(market.get("conditionId") or "").strip()
        if not condition_id:
            continue
        topic = topic_for_text(market_text(market))
        if topic == "weather":
            continue

        market_meta = {
            "conditionId": condition_id,
            "slug": market.get("slug"),
            "question": market.get("question"),
            "topic": topic,
            "volume24hr": num(market.get("volume24hr") or market.get("volume24hrClob")),
            "liquidity": num(market.get("liquidity") or market.get("liquidityClob")),
        }
        market_rows.append(market_meta)

        try:
            holders_payload = get_json(session, DATA_API, "/holders", {"market": condition_id, "limit": 50})
        except Exception as exc:
            print("holders_error", condition_id, exc)
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
            row = ensure(wallets, addr)
            add_name(row, holder.get("name") or holder.get("pseudonym"))
            row["market_sources"].append(
                {
                    "kind": "holder",
                    "topic": topic,
                    "conditionId": condition_id,
                    "slug": market.get("slug"),
                    "amount": num(holder.get("amount")),
                    "outcomeIndex": holder.get("outcomeIndex"),
                }
            )
            row["source_counts"]["market_holder"] += 1
            row["topics"][topic] += 1

        try:
            trades = get_json(session, DATA_API, "/trades", {"market": condition_id, "limit": 100, "offset": 0})
        except Exception as exc:
            print("trades_error", condition_id, exc)
            trades = []

        if isinstance(trades, list):
            for trade in trades:
                addr = norm_addr(trade.get("proxyWallet"))
                if not addr:
                    continue
                row = ensure(wallets, addr)
                add_name(row, trade.get("name") or trade.get("pseudonym"))
                size = num(trade.get("size"))
                price = num(trade.get("price"))
                row["market_sources"].append(
                    {
                        "kind": "trade",
                        "topic": topic,
                        "conditionId": condition_id,
                        "slug": market.get("slug"),
                        "side": trade.get("side"),
                        "size": size,
                        "price": price,
                        "notional": size * price,
                    }
                )
                row["source_counts"]["market_trade"] += 1
                row["topics"][topic] += 1
        time.sleep(0.05)

    pools: dict[str, list[str]] = defaultdict(list)
    summary_rows: list[dict[str, Any]] = []
    for addr, row in wallets.items():
        topics = row["topics"]
        all_non_sports = sum(
            1
            for source in row["leaderboard_sources"]
            if source["category"] != "SPORTS" and source["period"] == "ALL"
        )
        recent_non_sports = sum(
            1
            for source in row["leaderboard_sources"]
            if source["category"] != "SPORTS" and source["period"] in {"MONTH", "WEEK"}
        )
        market_non_sports = sum(1 for source in row["market_sources"] if source.get("topic") not in {"sports", "weather"})
        sportish = topics.get("sports", 0) >= max(3, sum(topics.values()) * 0.5)

        if all_non_sports >= 2 and not sportish:
            pools["core_non_sports_leaderboard"].append(addr)
        if recent_non_sports >= 2 and all_non_sports == 0 and not sportish:
            pools["emerging_recent_non_sports"].append(addr)
        if (topics.get("politics", 0) + topics.get("geopolitics", 0) + topics.get("economics", 0)) >= 2 and not sportish:
            pools["politics_macro_geopolitics"].append(addr)
        if (topics.get("crypto", 0) + topics.get("tech", 0)) >= 2 and not sportish:
            pools["crypto_tech"].append(addr)
        if market_non_sports >= 2 and not sportish:
            pools["active_hot_market_non_sports"].append(addr)
        if sportish:
            pools["sports_noise_or_latency"].append(addr)

        best = best_leaderboard_source(row) or {}
        summary_rows.append(
            {
                "wallet": addr,
                "name": primary_name(row),
                "score": quality_score(row),
                "best_rank": row["best_rank"] or "",
                "max_pnl": round(row["max_pnl"] or 0, 2),
                "max_vol": round(row["max_vol"] or 0, 2),
                "leaderboard_sources": len(row["leaderboard_sources"]),
                "market_sources": len(row["market_sources"]),
                "top_topics": ",".join(f"{key}:{value}" for key, value in topics.most_common(4)),
                "best_source": f"{best.get('category', '')}/{best.get('period', '')}/#{best.get('rank', '')}"
                if best
                else "",
            }
        )

    summary_rows.sort(key=lambda item: (-item["score"], -(item["leaderboard_sources"] + item["market_sources"]), item["wallet"]))
    score_by_addr = {row["wallet"]: row["score"] for row in summary_rows}
    for pool_name in list(pools):
        pools[pool_name] = sorted(set(pools[pool_name]), key=lambda wallet: (-score_by_addr.get(wallet, 0), wallet))

    shortlist: list[str] = []
    for summary in summary_rows:
        addr = summary["wallet"]
        row = wallets[addr]
        if addr in pools.get("sports_noise_or_latency", []):
            continue
        has_recent = any(
            source["period"] in {"MONTH", "WEEK"} and source["category"] != "SPORTS"
            for source in row["leaderboard_sources"]
        )
        has_active_market = any(source.get("topic") not in {"sports", "weather"} for source in row["market_sources"])
        if summary["score"] >= 20 and (has_recent or has_active_market):
            shortlist.append(addr)
        if len(shortlist) >= 80:
            break

    payload = {
        "generated_at_utc": stamp,
        "method": {
            "leaderboard_categories": categories,
            "leaderboard_periods": periods,
            "leaderboard_offsets": "0..450 step 50",
            "active_markets_source": "gamma /markets closed=false active=true order=volume24hr limit=80; weather markets skipped",
            "active_market_wallet_sources": "data-api /holders limit=50 and /trades limit=100 per market",
            "note": "Discovery-layer address pooling, not full closed-position quality validation.",
        },
        "counts": {
            "unique_wallets": len(wallets),
            "leaderboard_rows_seen": leaderboard_rows,
            "active_markets_scanned": len(market_rows),
            "summary_rows": len(summary_rows),
        },
        "markets_scanned": market_rows,
        "pools": dict(pools),
        "deep_scan_shortlist": shortlist,
        "wallets": [],
    }

    for summary in summary_rows:
        row = wallets[summary["wallet"]]
        payload["wallets"].append(
            {
                **summary,
                "names": dict(row["names"].most_common(5)),
                "topics": dict(row["topics"].most_common()),
                "leaderboard_sources": row["leaderboard_sources"][:30],
                "market_sources_sample": row["market_sources"][:20],
                "pool_membership": [pool_name for pool_name, addrs in pools.items() if summary["wallet"] in addrs],
            }
        )

    json_path = out_dir / f"address_pools_{stamp}.json"
    md_path = out_dir / f"address_pools_{stamp}.md"
    csv_path = out_dir / f"address_pools_{stamp}.csv"
    json_text = json.dumps(payload, ensure_ascii=False, indent=2)
    json_path.write_text(json_text, encoding="utf-8")
    (out_dir / "address_pools_latest.json").write_text(json_text, encoding="utf-8")

    if summary_rows:
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(summary_rows[0].keys()))
            writer.writeheader()
            writer.writerows(summary_rows)

    lines: list[str] = []
    lines.append(f"# Copy Trade Address Pools - {stamp}")
    lines.append("")
    lines.append("This is a discovery-layer pool, not a validated whale list. Run full closed-positions pagination before live/paper weighting.")
    lines.append("")
    lines.append("## Counts")
    lines.append("")
    for key, value in payload["counts"].items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("## Pools")
    lines.append("")
    ordered_pools = [
        "core_non_sports_leaderboard",
        "emerging_recent_non_sports",
        "politics_macro_geopolitics",
        "crypto_tech",
        "active_hot_market_non_sports",
        "sports_noise_or_latency",
    ]
    summary_by_addr = {row["wallet"]: row for row in summary_rows}
    for pool_name in ordered_pools:
        addrs = pools.get(pool_name, [])
        lines.append(f"### {pool_name} ({len(addrs)})")
        lines.append("")
        lines.append("| rank | score | name | wallet | best_source | topics |")
        lines.append("|---:|---:|---|---|---|---|")
        for idx, addr in enumerate(addrs[:40], 1):
            summary = summary_by_addr[addr]
            lines.append(
                f"| {idx} | {summary['score']} | {summary['name']} | {addr} | {summary['best_source']} | {summary['top_topics']} |"
            )
        lines.append("")
    lines.append("## Deep Scan Shortlist")
    lines.append("")
    lines.append("| rank | score | name | wallet | best_source | topics |")
    lines.append("|---:|---:|---|---|---|---|")
    for idx, addr in enumerate(shortlist[:80], 1):
        summary = summary_by_addr[addr]
        lines.append(
            f"| {idx} | {summary['score']} | {summary['name']} | {addr} | {summary['best_source']} | {summary['top_topics']} |"
        )
    lines.append("")
    lines.append("## Active Markets Scanned")
    lines.append("")
    lines.append("| topic | volume24h | liquidity | slug | question |")
    lines.append("|---|---:|---:|---|---|")
    for market in market_rows[:40]:
        question = str(market.get("question") or "").replace("|", "/")[:120]
        lines.append(
            f"| {market['topic']} | {market['volume24hr']:.0f} | {market['liquidity']:.0f} | {market.get('slug')} | {question} |"
        )
    lines.append("")
    md_text = "\n".join(lines)
    md_path.write_text(md_text, encoding="utf-8")
    (out_dir / "address_pools_latest.md").write_text(md_text, encoding="utf-8")

    print(json_path)
    print(md_path)
    print(csv_path)
    print(json.dumps(payload["counts"], ensure_ascii=False))
    for pool_name in ordered_pools:
        print(pool_name, len(pools.get(pool_name, [])))
    print("deep_scan_shortlist", len(shortlist))


if __name__ == "__main__":
    main()
