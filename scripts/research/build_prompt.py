#!/usr/bin/env python
"""Build a final decision prompt for a single market."""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.domains.research.pipeline import get_market_details
from src.domains.research.db import get_connection


def _format_list(values: Optional[List[Any]]) -> str:
    if not values:
        return "N/A"
    return ", ".join([str(v) for v in values])


def _pretty_json(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def _format_time_window(value: Dict[str, Any]) -> str:
    if not isinstance(value, dict):
        return "N/A"
    start = value.get("start_at_utc") or "N/A"
    end = value.get("end_at_utc") or "N/A"
    tz = value.get("timezone_source") or "N/A"
    return f"start={start}, end={end}, tz={tz}"


def _format_token_summary(tokens: List[Dict[str, Any]]) -> str:
    if not tokens:
        return "N/A"
    lines: List[str] = []
    for tok in tokens:
        price = tok.get("latest_price") or {}
        metrics = tok.get("metrics") or {}
        lines.append(f"- token_id: {tok.get('token_id')}")
        lines.append(
            "  - price: "
            f"mid={price.get('mid')}, bid={price.get('best_bid')}, ask={price.get('best_ask')}, "
            f"spread={price.get('spread')}, spread_pct_mid={price.get('spread_pct_mid')}"
        )
        lines.append(
            "  - depth: "
            f"depth_1pct_bid={metrics.get('depth_1pct_bid')}, depth_1pct_ask={metrics.get('depth_1pct_ask')}, "
            f"depth_2pct_bid={metrics.get('depth_2pct_bid')}, depth_2pct_ask={metrics.get('depth_2pct_ask')}"
        )
    return "\n".join(lines)


def _format_events_summary(events: List[Dict[str, Any]]) -> str:
    if not events:
        return "N/A"
    lines: List[str] = []
    for ev in events:
        title = ev.get("title") or "N/A"
        ticker = ev.get("ticker")
        tags = ev.get("tags") or []
        suffix = f" ({ticker})" if ticker else ""
        lines.append(f"- {title}{suffix} | tags={_format_list(tags)}")
    return "\n".join(lines)


def _short_text(value: Optional[str], max_chars: int = 800) -> Tuple[str, bool]:
    if not value:
        return "", False
    cleaned = " ".join(str(value).split())
    if len(cleaned) <= max_chars:
        return cleaned, False
    return cleaned[:max_chars].rstrip() + "...", True


def _compact_market(market: Dict[str, Any]) -> Dict[str, Any]:
    desc, desc_trunc = _short_text(market.get("description"))
    rules, rules_trunc = _short_text(market.get("rules"))
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
        "description": desc,
        "rules": rules,
        "description_truncated": desc_trunc,
        "rules_truncated": rules_trunc,
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
        "analysis": {
            **analysis,
            "parsed_json": parsed_json,
        }
        if analysis
        else {},
        "evidence": detail.get("evidence"),
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


def _load_detail_from_file(path: Path, market_id: Optional[str]) -> Dict[str, Any]:
    obj = json.loads(path.read_text())
    items = obj.get("items") or []
    if not items:
        raise SystemExit("Input JSON contains no items.")
    if market_id:
        for item in items:
            if str(item.get("market", {}).get("market_id")) == market_id:
                return item
        raise SystemExit(f"market_id {market_id} not found in input JSON.")
    if len(items) > 1:
        raise SystemExit("Multiple items in input JSON; please provide --market-id.")
    return items[0]


def _load_config(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise SystemExit(f"Config not found: {cfg_path}")
    try:
        return json.loads(cfg_path.read_text())
    except Exception as exc:
        raise SystemExit(f"Invalid config JSON: {exc}") from exc


def _apply_config(args: argparse.Namespace, config: Dict[str, Any]) -> argparse.Namespace:
    def _value(name: str, default: Any) -> Any:
        current = getattr(args, name)
        if current is not None:
            return current
        return config.get(name, default)

    args.statuses = _value("statuses", "READY_TO_SEARCH,PARSED")
    args.limit = _value("limit", 0)
    args.output_dir = _value("output_dir", "output/prompts_all")
    args.require_price = _value("require_price", False)
    args.price_min = _value("price_min", 0.05)
    args.price_max = _value("price_max", 0.95)
    args.require_active = _value("require_active", False)
    args.require_valid = _value("require_valid", False)
    return args


def _safe_slug(value: Optional[str], max_len: int = 80) -> str:
    if not value:
        return ""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip().lower())
    cleaned = cleaned.strip("_")
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip("_")
    return cleaned


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _get_latest_price(conn, token_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT token_id, fetched_at_utc, mid, best_bid, best_ask, spread, spread_pct_mid "
        "FROM prices WHERE token_id=? ORDER BY fetched_at_utc DESC LIMIT 1",
        (token_id,),
    ).fetchone()
    return dict(row) if row else None


def _has_valid_price(
    conn,
    token_ids: List[str],
    price_min: float,
    price_max: float,
) -> bool:
    for tid in token_ids:
        row = _get_latest_price(conn, str(tid))
        if not row:
            continue
        best_bid = row.get("best_bid")
        best_ask = row.get("best_ask")
        if best_bid is None or best_ask is None:
            continue
        mid = row.get("mid")
        if mid is None:
            mid = (best_bid + best_ask) / 2
        if mid is None:
            continue
        if price_min < mid < price_max:
            return True
    return False


def _fetch_market_ids(
    statuses: List[str],
    limit: int,
    price_min: float,
    price_max: float,
    require_active: bool,
    require_valid: bool,
    require_price: bool,
) -> List[Tuple[str, Optional[str]]]:
    placeholders = ", ".join(["?"] * len(statuses))
    sql = f"""
    SELECT market_id, slug, clob_token_ids_json, end_at_utc, active, resolved
    FROM markets
    WHERE status IN ({placeholders})
    ORDER BY status_updated_at DESC
    """
    now = datetime.now(timezone.utc)
    results: List[Tuple[str, Optional[str]]] = []
    with get_connection() as conn:
        rows = conn.execute(sql, statuses).fetchall()
        for row in rows:
            if require_active and (row["active"] != 1 or row["resolved"] == 1):
                continue
            if require_valid:
                end_at = _parse_datetime(row["end_at_utc"])
                if not end_at or end_at <= now:
                    continue
            try:
                token_ids = json.loads(row["clob_token_ids_json"] or "[]")
            except Exception:
                token_ids = []
            if require_price and not _has_valid_price(conn, token_ids, price_min, price_max):
                continue
            results.append((row["market_id"], row["slug"]))
            if limit > 0 and len(results) >= limit:
                break
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Build final prompts for markets.")
    parser.add_argument("--market-id", help="Target market_id")
    parser.add_argument("--batch", action="store_true", help="Generate prompts for multiple markets")
    parser.add_argument(
        "--statuses",
        default=None,
        help="Comma-separated market statuses for batch mode",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit number of markets in batch mode")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for batch mode",
    )
    parser.add_argument("--require-price", action="store_true", help="Require bestBid/bestAsk to exist")
    parser.add_argument("--price-min", type=float, default=None, help="Min mid price (exclusive)")
    parser.add_argument("--price-max", type=float, default=None, help="Max mid price (exclusive)")
    parser.add_argument("--require-active", action="store_true", help="Require market active & unresolved")
    parser.add_argument("--require-valid", action="store_true", help="Require end_at_utc in the future")
    parser.add_argument("--config", help="Optional JSON config for batch mode")
    parser.add_argument(
        "--template",
        default="scripts/research/prompt_template.md",
        help="Prompt template path",
    )
    parser.add_argument(
        "--input",
        help="Optional market_detail.json path (if not provided, read from DB)",
    )
    parser.add_argument("--output", help="Output prompt path")
    args = parser.parse_args()
    config = _load_config(args.config)
    if config:
        args = _apply_config(args, config)

    template_path = Path(args.template)
    if not template_path.exists():
        raise SystemExit(f"Template not found: {template_path}")
    template_text = template_path.read_text()

    if args.batch:
        statuses = [s.strip() for s in args.statuses.split(",") if s.strip()]
        market_rows = _fetch_market_ids(
            statuses,
            args.limit,
            price_min=args.price_min,
            price_max=args.price_max,
            require_active=args.require_active,
            require_valid=args.require_valid,
            require_price=args.require_price,
        )
        if not market_rows:
            raise SystemExit("No markets found for the requested statuses.")
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        written = 0
        for market_id, slug in market_rows:
            details = get_market_details([market_id], include_orderbooks=True)
            if not details:
                continue
            detail = details[0]
            market_payload = _build_market_payload(detail)
            market_json = _build_market_payload_for_prompt(market_payload)
            market_url = str((detail.get("market") or {}).get("market_url") or "")
            rendered = _render_template(template_text, market_json, market_url)

            safe_slug = _safe_slug(slug) or "market"
            filename = f"prompt_{market_id}_{safe_slug}.md"
            output_path = output_dir / filename
            output_path.write_text(rendered, encoding="utf-8")
            written += 1

        print(str(output_dir))
        print(f"generated={written}")
        return 0

    if args.input:
        detail = _load_detail_from_file(Path(args.input), args.market_id)
    else:
        if not args.market_id:
            raise SystemExit("--market-id is required when --input is not provided.")
        details = get_market_details([args.market_id], include_orderbooks=True)
        if not details:
            raise SystemExit(f"market_id not found: {args.market_id}")
        detail = details[0]

    market_payload = _build_market_payload(detail)
    market_json = _build_market_payload_for_prompt(market_payload)
    market_url = str((detail.get("market") or {}).get("market_url") or "")
    rendered = _render_template(template_text, market_json, market_url)

    market_id = detail.get("market", {}).get("market_id") or "unknown"
    output_path = Path(args.output) if args.output else Path("output") / f"prompt_{market_id}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    print(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
