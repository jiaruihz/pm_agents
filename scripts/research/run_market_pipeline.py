#!/usr/bin/env python3
"""Run end-to-end pipeline steps for a single market_id."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.domains.research.config import get_settings
from src.domains.research.db import init_db, get_connection
from src.domains.research.pipeline import get_market_details
from src.domains.research.storage import get_markets_by_ids, save_orderbook_levels, save_prices
from src.domains.research.clients.clob import enrich_token_batch
from src.domains.research.parser import parse_market_with_llm, save_market_rule_parses_records


def _load_market(market_id: Optional[str], slug: Optional[str]) -> Dict[str, Any]:
    if market_id:
        rows = get_markets_by_ids([market_id])
        return rows[0] if rows else {}
    if slug:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT market_id, slug, question, description, rules, category, end_at_utc, volume, liquidity, "
                "clob_token_ids_json, outcomes_json, outcome_prices_json, event_ids_json, event_slugs_json, "
                "event_titles_json, event_tickers_json, active, resolved, status, status_updated_at, last_synced_at_utc "
                "FROM markets WHERE slug=? ORDER BY last_synced_at_utc DESC LIMIT 1",
                (slug,),
            ).fetchone()
            return dict(row) if row else {}
    return {}


def _parse_event_slug(value: str) -> Optional[str]:
    if not value:
        return None
    if value.startswith("http"):
        try:
            from urllib.parse import urlparse
        except Exception:
            return None
        parsed = urlparse(value)
        parts = [p for p in parsed.path.split("/") if p]
        if "event" in parts:
            idx = parts.index("event")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        return None
    return value


def _find_markets_by_event_slug(event_slug: str) -> List[Dict[str, Any]]:
    if not event_slug:
        return []
    like = f"%\"{event_slug}\"%"
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT market_id, slug, question, end_at_utc, volume, liquidity, event_slugs_json, last_synced_at_utc "
            "FROM markets WHERE event_slugs_json LIKE ? ORDER BY last_synced_at_utc DESC",
            (like,),
        ).fetchall()
    results: List[Dict[str, Any]] = []
    for row in rows:
        try:
            slugs = json.loads(row["event_slugs_json"] or "[]")
        except Exception:
            slugs = []
        if event_slug in slugs:
            results.append(dict(row))
    return results


def _select_market_from_event(markets: List[Dict[str, Any]], pick: Optional[int], slug: Optional[str]) -> Optional[Dict[str, Any]]:
    if not markets:
        return None
    if slug:
        for m in markets:
            if m.get("slug") == slug:
                return m
        return None
    if pick is not None:
        if 0 <= pick < len(markets):
            return markets[pick]
        return None
    return markets[0]


def _parse_token_ids(market: Dict[str, Any]) -> List[str]:
    raw = market.get("clob_token_ids_json") or "[]"
    try:
        data = json.loads(raw)
        return [str(t) for t in data if t]
    except Exception:
        return []


def _pretty_json(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def _short_text(value: Optional[str], max_chars: int = 800) -> Dict[str, Any]:
    if not value:
        return {"text": "", "truncated": False}
    cleaned = " ".join(str(value).split())
    if len(cleaned) <= max_chars:
        return {"text": cleaned, "truncated": False}
    return {"text": cleaned[:max_chars].rstrip() + "...", "truncated": True}


def _compact_market(market: Dict[str, Any]) -> Dict[str, Any]:
    desc = _short_text(market.get("description"))
    rules = _short_text(market.get("rules"))
    return {
        "market_id": market.get("market_id"),
        "slug": market.get("slug"),
        "question": market.get("question"),
        "category": market.get("category"),
        "status": market.get("status"),
        "active": market.get("active"),
        "resolved": market.get("resolved"),
        "end_at_utc": market.get("end_at_utc"),
        "volume": market.get("volume"),
        "liquidity": market.get("liquidity"),
        "market_url": market.get("market_url"),
        "description": desc["text"],
        "rules": rules["text"],
        "description_truncated": desc["truncated"],
        "rules_truncated": rules["truncated"],
    }


def _build_market_payload(detail: Dict[str, Any]) -> Dict[str, Any]:
    market = _compact_market(detail.get("market") or {})
    analysis = detail.get("analysis") or {}
    parsed = detail.get("parsed") or {}
    outcomes = parsed.get("outcomes") or []
    outcome_prices = parsed.get("outcome_prices") or []
    token_ids = parsed.get("token_ids") or []
    parsed_json = {}
    if analysis.get("parsed_json"):
        try:
            parsed_json = json.loads(analysis.get("parsed_json") or "{}")
        except Exception:
            parsed_json = {}

    tokens = detail.get("tokens") or []
    options_summary: List[Dict[str, Any]] = []
    options_detail: List[Dict[str, Any]] = []
    for idx, tok in enumerate(tokens):
        token_id = str(token_ids[idx]) if idx < len(token_ids) else str(tok.get("token_id"))
        outcome_label = outcomes[idx] if idx < len(outcomes) else None
        outcome_price = outcome_prices[idx] if idx < len(outcome_prices) else None
        latest_price = tok.get("latest_price") or {}
        metrics = tok.get("metrics") or {}
        levels = tok.get("orderbook_levels") or []
        bids = [lvl for lvl in levels if lvl.get("side") == "bid"]
        asks = [lvl for lvl in levels if lvl.get("side") == "ask"]
        bids.sort(key=lambda x: x.get("level") or 0)
        asks.sort(key=lambda x: x.get("level") or 0)
        options_summary.append(
            {
                "token_id": token_id,
                "outcome_label": outcome_label,
                "outcome_price": outcome_price,
                "best_bid": latest_price.get("best_bid"),
                "best_ask": latest_price.get("best_ask"),
                "mid": latest_price.get("mid"),
                "spread": latest_price.get("spread"),
                "spread_pct_mid": latest_price.get("spread_pct_mid"),
                "depth_1pct_bid": metrics.get("depth_1pct_bid"),
                "depth_1pct_ask": metrics.get("depth_1pct_ask"),
                "depth_2pct_bid": metrics.get("depth_2pct_bid"),
                "depth_2pct_ask": metrics.get("depth_2pct_ask"),
            }
        )
        options_detail.append(
            {
                "token_id": token_id,
                "outcome_label": outcome_label,
                "orderbook_levels_top": {
                    "bid": bids[:3],
                    "ask": asks[:3],
                }
                if levels
                else None,
            }
        )

    return {
        "market": market,
        "outcomes": outcomes,
        "outcome_prices": outcome_prices,
        "options_summary": options_summary,
        "options_detail": options_detail,
        "events": detail.get("events") or [],
        "analysis": {**analysis, "parsed_json": parsed_json} if analysis else {},
        "evidence": detail.get("evidence"),
        "scores": detail.get("scores"),
    }


def _build_market_payload_for_prompt(payload: Dict[str, Any]) -> str:
    guide_lines = [
        "FIELD_GUIDE:",
        "- market: 市场基础信息（描述/规则已精简，必要时以 URL 为准）",
        "- outcomes: 该市场的可选结果列表（如 YES/NO 或候选人）",
        "- outcome_prices: Gamma 提供的结果价格列表（粗筛用）",
        "- options_summary: 每个选项对应的 token 摘要（label + 价格/点差/深度）",
        "- options_detail: 每个选项的订单簿前 3 档（bid/ask）",
        "- events: 关联事件信息（标题、ticker、tags）",
        "- analysis: LLM 规则解析结果（结构化规则、分数、歧义提示）",
        "- evidence: 外部证据搜索结果（如果已跑调查节点）",
        "- scores: 量化评分结果（如果已跑风控节点）",
        "",
        "DATA:",
    ]
    payload_json = _pretty_json(payload)
    return "\n".join(guide_lines) + "\n" + payload_json


def _render_template(template_text: str, market_json: str, market_url: str) -> str:
    rendered = template_text
    tokens = [
        "<<<MARKET_JSON>>>>",
        "<<<MARKET_JSON>>>",
        "<<<MARKET_JSON>>",
        "{{MARKET_JSON}}",
        "{MARKET_JSON}",
    ]
    for token in tokens:
        rendered = rendered.replace(f"MARKET_JSON: {token}", "MARKET_JSON:\n" + market_json)
    for token in tokens:
        rendered = rendered.replace(token, market_json)

    rendered = rendered.replace("{{market_url}}", market_url)
    rendered = rendered.replace("{market_url}", market_url)
    if market_url and "market_url:" in rendered and "{market_url" not in template_text:
        lines = rendered.splitlines()
        for idx, line in enumerate(lines):
            if line.strip().startswith("market_url:"):
                if line.strip() == "market_url:":
                    lines[idx] = line + " " + market_url
                break
        rendered = "\n".join(lines)
    return rendered


async def _enrich_tokens(token_ids: List[str], top_n: int, archive_books: bool) -> Dict[str, int]:
    if not token_ids:
        return {"prices": 0, "levels": 0}
    results = await enrich_token_batch(token_ids, top_n=top_n, archive_books=archive_books)
    price_rows = [p for p, _, _ in results if p]
    level_rows = [lvl for _, levels, _ in results for lvl in levels]
    saved_prices = save_prices(price_rows) if price_rows else 0
    saved_levels = save_orderbook_levels(level_rows) if level_rows else 0
    return {"prices": saved_prices, "levels": saved_levels}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run full pipeline for one market_id.")
    parser.add_argument("market_id", nargs="?", help="Market ID from markets table")
    parser.add_argument("--slug", default="", help="Market slug from URL")
    parser.add_argument("--event-url", default="", help="Event URL (polymarket.com/event/...)")
    parser.add_argument("--event-slug", default="", help="Event slug (from /event/<slug>)")
    parser.add_argument("--pick", type=int, default=None, help="Index of market under the event (0-based)")
    parser.add_argument("--list-only", action="store_true", help="List markets under event and exit")
    parser.add_argument("--no-enrich", action="store_true", help="Skip fetching prices/orderbooks")
    parser.add_argument("--no-parse", action="store_true", help="Skip LLM parse")
    parser.add_argument("--no-prompt", action="store_true", help="Skip prompt build")
    parser.add_argument("--template", default="scripts/prompt_template.md", help="Prompt template path")
    parser.add_argument("--output", default="", help="Output prompt path (default output/prompt_<id>.md)")
    parser.add_argument("--top-n", type=int, default=0, help="Orderbook levels to store (default from config)")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification (proxy MITM)")
    parser.add_argument("--proxy", default="", help="Proxy URL (e.g. http://127.0.0.1:7897)")
    args = parser.parse_args()

    if args.insecure:
        os.environ["PYTHONHTTPSVERIFY"] = "0"
        os.environ["CURL_CA_BUNDLE"] = ""
    if args.proxy:
        os.environ.setdefault("HTTP_PROXY", args.proxy)
        os.environ.setdefault("HTTPS_PROXY", args.proxy)
        os.environ.setdefault("http_proxy", args.proxy)
        os.environ.setdefault("https_proxy", args.proxy)
        os.environ.setdefault("ALL_PROXY", args.proxy)
        os.environ.setdefault("all_proxy", args.proxy)

    init_db()
    event_slug = _parse_event_slug(args.event_url) or (args.event_slug or "")
    if event_slug:
        markets = _find_markets_by_event_slug(event_slug)
        if not markets:
            print(f"event slug not found in DB: {event_slug}")
            return 1
        if args.list_only:
            for idx, m in enumerate(markets):
                print(f"[{idx}] market_id={m.get('market_id')} slug={m.get('slug')} question={m.get('question')}")
            return 0
        selected = _select_market_from_event(markets, args.pick, args.slug or None)
        if not selected:
            print("market not found under event; use --list-only to inspect options.")
            return 1
        market = _load_market(selected.get("market_id"), None)
    else:
        market = _load_market(args.market_id, args.slug or None)
    if not market:
        if args.slug:
            print(f"market slug not found: {args.slug}")
        else:
            print(f"market_id not found: {args.market_id}")
        return 1

    settings = get_settings()
    if not args.no_enrich:
        token_ids = _parse_token_ids(market)
        top_n = args.top_n or settings.orderbook_top_n
        enrich_result = asyncio.run(_enrich_tokens(token_ids, top_n=top_n, archive_books=settings.archive_books))
        print(f"enrich: prices={enrich_result['prices']} levels={enrich_result['levels']}")

    if not args.no_parse:
        parsed = asyncio.run(parse_market_with_llm(market))
        if not parsed:
            print("LLM parse failed or invalid JSON.")
            return 2
        save_market_rule_parses_records(market["market_id"], parsed, parsed.dict())
        print("parse: ok")

    if not args.no_prompt:
        details = get_market_details([market["market_id"]], include_orderbooks=True, include_analysis=True, include_evidence=True, include_scores=True)
        if not details:
            print("prompt: market details missing")
            return 3
        detail = details[0]
        payload = _build_market_payload(detail)
        market_url = detail.get("market", {}).get("market_url") or ""
        template_path = Path(args.template)
        template_text = template_path.read_text()
        market_json = _build_market_payload_for_prompt(payload)
        rendered = _render_template(template_text, market_json, market_url)
        output_path = Path(args.output) if args.output else Path(f"output/prompt_{market['market_id']}.md")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered)
        print(f"prompt: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
