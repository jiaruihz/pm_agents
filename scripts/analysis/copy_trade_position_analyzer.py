"""
Copy Trade Position Analyzer

拉取 watch/paper 钱包的当前持仓明细，获取市场结算规则，
分析持仓集中度、主题分布、浮盈浮亏，输出是否值得关注的判断。

Usage:
    python3 scripts/analysis/copy_trade_position_analyzer.py
    python3 scripts/analysis/copy_trade_position_analyzer.py --wallets 0xabc...,0xdef...
    python3 scripts/analysis/copy_trade_position_analyzer.py --status paper --top 10
"""

from __future__ import annotations

import argparse
import json
import math
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
OUT_DIR = Path("runtime/copy_trade")

# 主题正则（与 research 脚本保持一致）
SPORTS_RE = re.compile(
    r"\b(nba|nfl|mlb|nhl|epl|uefa|fifa|ufc|tennis|soccer|football|basketball|baseball|hockey|cricket|golf|fight|vs\.?|over [0-9]|under [0-9]|spread)\b",
    re.I,
)
WEATHER_RE = re.compile(r"\btemperature|weather|rain|snow|hurricane|tornado|degrees|°f|highest-temperature\b", re.I)
ELECTION_RE = re.compile(
    r"\btrump|biden|president|election|senate|house|governor|mayor|primary|democrat|republican|gop|electoral college|popular vote\b",
    re.I,
)
GEOPOL_RE = re.compile(r"\bukraine|russia|iran|israel|gaza|china|taiwan|nato|war|ceasefire|missile|nuclear|tariff\b", re.I)
CRYPTO_RE = re.compile(r"\bbitcoin|btc|ethereum|eth|solana|sol|crypto|token|xrp|doge|stablecoin|defi\b", re.I)
TECH_RE = re.compile(r"\bai|openai|nvidia|tesla|apple|google|meta|microsoft|spacex|starship|ipo\b", re.I)
ECON_RE = re.compile(r"\bfed|rate|inflation|cpi|gdp|recession|unemployment|tariff|treasury\b", re.I)
CULTURE_RE = re.compile(r"\boscar|grammy|movie|album|song|celebrity|taylor|kendrick|rihanna|gta\b", re.I)


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
        headers={"Accept": "application/json", "User-Agent": "pm-agent-copytrade-research/1.0"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


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
    return conn


def get_watch_wallets(conn: sqlite3.Connection, status: str | None, limit: int) -> list[dict[str, Any]]:
    """获取需要监控的钱包列表（去重，取最新 review）"""
    where_clauses = ["w.status IN ('watch', 'paper')", "r.verdict IN ('watch', 'paper_candidate')"]
    params: list[Any] = []
    if status:
        where_clauses = ["w.status = ?", "r.verdict IN ('watch', 'paper_candidate')"]
        params = [status]

    rows = conn.execute(
        f"""
        SELECT
          w.wallet_address,
          w.display_name,
          w.status,
          r.score,
          r.verdict,
          r.metrics_json,
          r.summary,
          r.reviewed_at
        FROM copy_trade_wallets w
        JOIN copy_trade_wallet_reviews r ON r.wallet_address = w.wallet_address
        WHERE {' AND '.join(where_clauses)}
        ORDER BY r.reviewed_at DESC
        """,
        params,
    ).fetchall()

    seen: set[str] = set()
    wallets: list[dict[str, Any]] = []
    for row in rows:
        addr = row["wallet_address"]
        if addr in seen:
            continue
        seen.add(addr)
        metrics = json.loads(row["metrics_json"] or "{}")
        wallets.append({
            "wallet_address": addr,
            "display_name": row["display_name"] or "",
            "status": row["status"],
            "score": row["score"],
            "verdict": row["verdict"],
            "summary": row["summary"],
            "reviewed_at": row["reviewed_at"],
            "metrics": metrics,
        })
        if len(wallets) >= limit:
            break
    return wallets


def fetch_positions(session: requests.Session, wallet: str) -> list[dict[str, Any]]:
    """拉取钱包当前持仓"""
    try:
        rows = get_json(session, DATA_API, "/positions", {"user": wallet, "limit": 200, "offset": 0}, timeout=30)
    except Exception as exc:
        print(f"  [ERROR] positions fetch failed for {wallet}: {exc}")
        return []
    return [row for row in rows if isinstance(row, dict)]


def fetch_market_meta(session: requests.Session, condition_id: str, cache: dict[str, Any]) -> dict[str, Any]:
    """获取市场元数据（带缓存）"""
    if condition_id in cache:
        return cache[condition_id]

    try:
        markets = get_json(
            session,
            GAMMA_API,
            "/markets",
            {"conditionId": condition_id, "limit": 10, "active": "true", "closed": "false"},
            timeout=15,
        )
    except Exception:
        markets = []

    meta: dict[str, Any] = {"condition_id": condition_id, "found": False}
    if isinstance(markets, list) and markets:
        m = markets[0]
        if isinstance(m, dict):
            meta = {
                "condition_id": condition_id,
                "found": True,
                "slug": m.get("slug"),
                "question": m.get("question"),
                "description": m.get("description"),
                "resolution_source": m.get("resolutionSource") or m.get("resolution_source"),
                "end_date": m.get("endDate") or m.get("end_date"),
                "category": m.get("category"),
                "volume24hr": num(m.get("volume24hr") or m.get("volume24hrClob")),
                "liquidity": num(m.get("liquidity") or m.get("liquidityClob")),
                "outcomes": [o.get("name") or o.get("outcome") for o in m.get("outcomes", []) if isinstance(o, dict)],
                "outcome_prices": [num(o.get("price")) for o in m.get("outcomes", []) if isinstance(o, dict)],
                "closed": m.get("closed"),
                "active": m.get("active"),
            }

    cache[condition_id] = meta
    return meta


def analyze_position(position: dict[str, Any], market_meta: dict[str, Any]) -> dict[str, Any]:
    """分析单个持仓"""
    title = str(position.get("title") or position.get("question") or market_meta.get("question") or "")
    topic = topic_for_text(title.lower())
    size = num(position.get("size"))
    avg_price = num(position.get("avgPrice") or position.get("avg_price"))
    cur_price = num(position.get("curPrice") or position.get("cur_price"))
    initial_value = num(position.get("initialValue"))
    current_value = num(position.get("currentValue"))
    cash_pnl = num(position.get("cashPnl") or position.get("realizedPnl"))
    percent_pnl = num(position.get("percentPnl"))
    outcome = str(position.get("outcome") or "")

    # 如果 API 没给 percentPnl，自己算
    if percent_pnl == 0 and initial_value > 0:
        percent_pnl = (current_value - initial_value) / initial_value

    # 结算规则清晰度判断
    resolution_source = market_meta.get("resolution_source") or ""
    description = market_meta.get("description") or ""
    has_clear_source = bool(resolution_source and len(resolution_source) > 10)
    has_description = bool(description and len(description) > 20)

    rule_clarity = "clear" if (has_clear_source and has_description) else "partial" if has_description else "unclear"

    # 是否适合跟单判断
    concerns: list[str] = []
    if topic == "sports":
        concerns.append("sports_market")
    if topic == "politics" and ELECTION_RE.search(title.lower()):
        concerns.append("election_polluted")
    if not market_meta.get("found"):
        concerns.append("market_not_found")
    if rule_clarity == "unclear":
        concerns.append("unclear_rules")
    if cur_price > 0.90:
        concerns.append("price_too_high")
    if cur_price < 0.05:
        concerns.append("price_too_low")
    if market_meta.get("liquidity", 0) < 1000:
        concerns.append("low_liquidity")
    if percent_pnl < -0.5:
        concerns.append("deep_drawdown")

    follow_recommendation = "caution" if concerns else "ok"
    if {"sports_market", "election_polluted", "unclear_rules", "market_not_found"} & set(concerns):
        follow_recommendation = "avoid"

    return {
        "title": title,
        "topic": topic,
        "outcome": outcome,
        "size": size,
        "avg_price": round(avg_price, 4),
        "cur_price": round(cur_price, 4),
        "initial_value": round(initial_value, 2),
        "current_value": round(current_value, 2),
        "cash_pnl": round(cash_pnl, 2),
        "percent_pnl": round(percent_pnl, 4),
        "condition_id": market_meta.get("condition_id"),
        "slug": market_meta.get("slug"),
        "resolution_source": resolution_source,
        "end_date": market_meta.get("end_date"),
        "volume24hr": market_meta.get("volume24hr"),
        "liquidity": market_meta.get("liquidity"),
        "rule_clarity": rule_clarity,
        "concerns": concerns,
        "follow_recommendation": follow_recommendation,
    }


def analyze_wallet_positions(
    wallet: dict[str, Any],
    positions: list[dict[str, Any]],
    market_cache: dict[str, Any],
) -> dict[str, Any]:
    """分析钱包全部持仓"""
    if not positions:
        return {
            "wallet": wallet["wallet_address"],
            "name": wallet["display_name"],
            "status": wallet["status"],
            "score": wallet["score"],
            "verdict": wallet["verdict"],
            "position_count": 0,
            "total_initial": 0,
            "total_current": 0,
            "total_pnl": 0,
            "avg_roi": 0,
            "topic_breakdown": {},
            "positions": [],
            "recommendation": "caution",
            "ok_count": 0,
            "caution_count": 0,
            "avoid_count": 0,
            "summary": "无活跃持仓",
        }

    topic_values: dict[str, float] = defaultdict(float)
    topic_pnls: dict[str, float] = defaultdict(float)
    analyzed: list[dict[str, Any]] = []
    total_initial = 0.0
    total_current = 0.0

    for pos in positions:
        condition_id = str(pos.get("conditionId") or pos.get("condition_id") or "").strip()
        meta = fetch_market_meta(session, condition_id, market_cache) if condition_id else {"found": False}
        analyzed_pos = analyze_position(pos, meta)
        analyzed.append(analyzed_pos)
        topic_values[analyzed_pos["topic"]] += analyzed_pos["initial_value"]
        topic_pnls[analyzed_pos["topic"]] += analyzed_pos["cash_pnl"]
        total_initial += analyzed_pos["initial_value"]
        total_current += analyzed_pos["current_value"]

    total_pnl = total_current - total_initial
    avg_roi = total_pnl / total_initial if total_initial > 0 else 0.0

    # 集中度
    topic_breakdown = {}
    for topic, value in sorted(topic_values.items(), key=lambda x: -x[1]):
        pct = value / total_initial if total_initial > 0 else 0.0
        topic_breakdown[topic] = {
            "initial_value": round(value, 2),
            "pct_of_portfolio": round(pct, 3),
            "pnl": round(topic_pnls[topic], 2),
        }

    # 钱包级推荐
    avoid_count = sum(1 for p in analyzed if p["follow_recommendation"] == "avoid")
    caution_count = sum(1 for p in analyzed if p["follow_recommendation"] == "caution")
    ok_count = sum(1 for p in analyzed if p["follow_recommendation"] == "ok")

    if avoid_count >= len(analyzed) * 0.5:
        wallet_recommendation = "avoid"
    elif ok_count >= len(analyzed) * 0.5:
        wallet_recommendation = "ok"
    else:
        wallet_recommendation = "caution"

    # 如果整体 deep drawdown，降权
    if avg_roi < -0.3:
        wallet_recommendation = "caution" if wallet_recommendation == "ok" else "avoid"

    return {
        "wallet": wallet["wallet_address"],
        "name": wallet["display_name"],
        "status": wallet["status"],
        "score": wallet["score"],
        "verdict": wallet["verdict"],
        "position_count": len(positions),
        "total_initial": round(total_initial, 2),
        "total_current": round(total_current, 2),
        "total_pnl": round(total_pnl, 2),
        "avg_roi": round(avg_roi, 4),
        "topic_breakdown": topic_breakdown,
        "recommendation": wallet_recommendation,
        "ok_count": ok_count,
        "caution_count": caution_count,
        "avoid_count": avoid_count,
        "positions": analyzed,
    }


def generate_report(results: list[dict[str, Any]], stamp: str) -> str:
    """生成 Markdown 报告"""
    lines: list[str] = []
    lines.append(f"# Copy Trade Position Analysis - {stamp}")
    lines.append("")
    lines.append("> 自动拉取 watch/paper 钱包的当前持仓，分析主题集中度、浮盈浮亏、结算规则清晰度。")
    lines.append("")

    # 汇总表
    lines.append("## 钱包汇总")
    lines.append("")
    lines.append("| 钱包 | 名称 | 状态 | 评分 | 持仓数 | 总投入 | 当前值 | 浮盈亏 | ROI | 推荐 | 可跟 | 谨慎 | 回避 |")
    lines.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in results:
        rec = r["recommendation"]
        rec_emoji = "✅" if rec == "ok" else "⚠️" if rec == "caution" else "❌"
        lines.append(
            f"| {r['wallet'][:12]}... | {r['name'] or '-'} | {r['status']} | {r['score']:.1f} | "
            f"{r['position_count']} | ${r['total_initial']:,.0f} | ${r['total_current']:,.0f} | "
            f"${r['total_pnl']:,.0f} | {r['avg_roi']:.1%} | {rec_emoji} {rec} | "
            f"{r['ok_count']} | {r['caution_count']} | {r['avoid_count']} |"
        )
    lines.append("")

    # 每个钱包详情
    for r in results:
        if not r["positions"]:
            continue
        lines.append(f"## {r['name'] or r['wallet'][:16]}... ({r['wallet']})")
        lines.append("")
        lines.append(f"- **状态**: {r['status']} | **评分**: {r['score']:.1f} | **推荐**: {r['recommendation']}")
        lines.append(f"- **持仓**: {r['position_count']} 个 | **总投入**: ${r['total_initial']:,.0f} | **ROI**: {r['avg_roi']:.1%}")
        lines.append("")

        # 主题分布
        if r["topic_breakdown"]:
            lines.append("**主题分布**:")
            for topic, data in r["topic_breakdown"].items():
                lines.append(f"- {topic}: {data['pct_of_portfolio']:.1%} (${data['initial_value']:,.0f}), PnL ${data['pnl']:,.0f}")
            lines.append("")

        # 持仓明细
        lines.append("| 市场 | 主题 | 方向 | 投入 | 当前值 | 盈亏 | ROI | 现价 | 清晰度 | 推荐 | 备注 |")
        lines.append("|---|---|---|---:|---:|---:|---:|---:|---|---|---|")
        for pos in sorted(r["positions"], key=lambda x: -x["initial_value"]):
            concerns = ", ".join(pos["concerns"]) if pos["concerns"] else "-"
            rec = "✅" if pos["follow_recommendation"] == "ok" else "⚠️" if pos["follow_recommendation"] == "caution" else "❌"
            title_short = pos["title"][:50] if pos["title"] else "-"
            lines.append(
                f"| {title_short} | {pos['topic']} | {pos['outcome']} | "
                f"${pos['initial_value']:,.0f} | ${pos['current_value']:,.0f} | ${pos['cash_pnl']:,.0f} | "
                f"{pos['percent_pnl']:.1%} | {pos['cur_price']:.2f} | {pos['rule_clarity']} | {rec} | {concerns} |"
            )
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy-trade position analyzer")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--status", choices=["watch", "paper"], help="Filter by wallet status")
    parser.add_argument("--top", type=int, default=15, help="Max wallets to analyze")
    parser.add_argument("--max-positions-per-wallet", type=int, default=50, help="Max positions to analyze per wallet (by value)")
    parser.add_argument("--wallets", help="Comma-separated wallet addresses (override DB selection)")
    args = parser.parse_args()

    global session
    session = build_session()
    market_cache: dict[str, Any] = {}

    conn = connect(args.db)

    if args.wallets:
        wallet_addrs = [norm_addr(w) for w in args.wallets.split(",") if norm_addr(w)]
        wallets = []
        for addr in wallet_addrs:
            row = conn.execute(
                """
                SELECT w.wallet_address, w.display_name, w.status,
                       r.score, r.verdict, r.metrics_json, r.summary, r.reviewed_at
                FROM copy_trade_wallets w
                LEFT JOIN copy_trade_wallet_reviews r ON r.wallet_address = w.wallet_address
                WHERE w.wallet_address = ?
                ORDER BY r.reviewed_at DESC LIMIT 1
                """,
                (addr,),
            ).fetchone()
            if row:
                metrics = json.loads(row["metrics_json"] or "{}")
                wallets.append({
                    "wallet_address": row["wallet_address"],
                    "display_name": row["display_name"] or "",
                    "status": row["status"],
                    "score": row["score"] or 0,
                    "verdict": row["verdict"] or "",
                    "summary": row["summary"] or "",
                    "reviewed_at": row["reviewed_at"] or "",
                    "metrics": metrics,
                })
    else:
        wallets = get_watch_wallets(conn, args.status, args.top)

    print(f"Analyzing {len(wallets)} wallets...", flush=True)

    results: list[dict[str, Any]] = []
    for i, wallet in enumerate(wallets, 1):
        print(f"\n[{i}/{len(wallets)}] {wallet['wallet_address'][:16]}... ({wallet['display_name'] or 'unnamed'})", flush=True)
        positions = fetch_positions(session, wallet["wallet_address"])

        # Sort by initial value desc and limit
        positions_sorted = sorted(
            positions,
            key=lambda p: num(p.get("initialValue") or p.get("initial_value") or 0),
            reverse=True
        )
        if args.max_positions_per_wallet > 0:
            positions_sorted = positions_sorted[:args.max_positions_per_wallet]

        print(f"  -> fetched {len(positions)} positions, analyzing top {len(positions_sorted)}", flush=True)

        result = analyze_wallet_positions(wallet, positions_sorted, market_cache)
        results.append(result)

        print(f"  -> total=${result['total_initial']:,.0f}, roi={result['avg_roi']:.1%}, rec={result['recommendation']}", flush=True)
        time.sleep(0.2)

    # 输出
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # JSON
    json_path = OUT_DIR / f"position_analysis_{stamp}.json"
    json_path.write_text(json.dumps({
        "generated_at": utc_now(),
        "wallets_analyzed": len(results),
        "markets_cached": len(market_cache),
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "position_analysis_latest.json").write_text(
        json_path.read_text(encoding="utf-8"), encoding="utf-8"
    )

    # Markdown
    md = generate_report(results, stamp)
    md_path = OUT_DIR / f"position_analysis_{stamp}.md"
    md_path.write_text(md, encoding="utf-8")
    (OUT_DIR / "position_analysis_latest.md").write_text(md, encoding="utf-8")

    print(f"\nDone. Output:")
    print(f"  {json_path}")
    print(f"  {md_path}")


if __name__ == "__main__":
    main()
