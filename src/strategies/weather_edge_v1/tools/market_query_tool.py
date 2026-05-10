from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.platform.clients import clob as clob_client
from src.platform.clients.polymarket_gamma import PolymarketGammaClient
from src.strategies.rule_lawyer.services.common import normalize_json_list, to_float
from src.strategies.rule_lawyer.services.market_resolver import resolve_event, resolve_market, resolve_market_target


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_levels(level_rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, float]]]:
    bids: List[Dict[str, float]] = []
    asks: List[Dict[str, float]] = []
    for row in level_rows:
        if not isinstance(row, dict):
            continue
        item = {
            "level": int(row.get("level") or 0),
            "price": to_float(row.get("price"), 0.0),
            "size": to_float(row.get("size"), 0.0),
        }
        side = _safe_str(row.get("side")).lower()
        if side == "bid":
            bids.append(item)
        elif side == "ask":
            asks.append(item)
    return {"bids": bids, "asks": asks}


async def _fetch_orderbook_rows(token_ids: List[str], top_n: int) -> Dict[str, Dict[str, Any]]:
    if not token_ids:
        return {}
    results = await clob_client.enrich_token_batch(token_ids, top_n=top_n, archive_books=False)
    payload: Dict[str, Dict[str, Any]] = {}
    for token_id, result in zip(token_ids, results):
        price_row, level_rows, archive_path = result
        levels = _normalize_levels(level_rows)
        price_row = dict(price_row or {})
        payload[str(token_id)] = {
            "token_id": str(token_id),
            "best_bid": price_row.get("best_bid"),
            "best_ask": price_row.get("best_ask"),
            "mid": price_row.get("mid"),
            "spread": price_row.get("spread"),
            "spread_pct_mid": price_row.get("spread_pct_mid"),
            "archive_path": archive_path,
            "bids": levels["bids"],
            "asks": levels["asks"],
        }
    return payload


def _token_entries(
    raw_market: Dict[str, Any],
    orderbooks: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    outcomes = [str(x) for x in normalize_json_list(raw_market.get("outcomes")) if str(x)]
    outcome_prices = [to_float(x, 0.0) for x in normalize_json_list(raw_market.get("outcomePrices"))]
    token_ids = [str(x) for x in normalize_json_list(raw_market.get("clobTokenIds")) if str(x)]
    out: List[Dict[str, Any]] = []
    for idx, token_id in enumerate(token_ids):
        out.append(
            {
                "token_id": token_id,
                "outcome": outcomes[idx] if idx < len(outcomes) else "",
                "outcome_price": outcome_prices[idx] if idx < len(outcome_prices) else None,
                "orderbook": orderbooks.get(token_id, {}),
            }
        )
    return out


def _normalize_market_row(
    raw_market: Dict[str, Any],
    *,
    selected_market_id: str,
    selected_market_slug: str,
    orderbooks: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    token_ids = [str(x) for x in normalize_json_list(raw_market.get("clobTokenIds")) if str(x)]
    prices = [to_float(x, 0.0) for x in normalize_json_list(raw_market.get("outcomePrices"))]
    outcomes = [str(x) for x in normalize_json_list(raw_market.get("outcomes")) if str(x)]
    market_id = _safe_str(raw_market.get("id") or raw_market.get("marketId") or raw_market.get("market_id"))
    slug = _safe_str(raw_market.get("slug"))
    return {
        "market_id": market_id,
        "slug": slug,
        "question": _safe_str(raw_market.get("question")),
        "description": _safe_str(raw_market.get("description")),
        "resolution_source": _safe_str(raw_market.get("resolutionSource")),
        "end_date": _safe_str(raw_market.get("endDate")),
        "active": _safe_bool(raw_market.get("active"), True),
        "closed": _safe_bool(raw_market.get("closed"), False),
        "best_bid": raw_market.get("bestBid"),
        "best_ask": raw_market.get("bestAsk"),
        "last_trade_price": raw_market.get("lastTradePrice"),
        "spread": raw_market.get("spread"),
        "volume": raw_market.get("volumeNum", raw_market.get("volume")),
        "liquidity": raw_market.get("liquidityNum", raw_market.get("liquidity")),
        "outcomes": outcomes,
        "outcome_prices": prices,
        "token_ids": token_ids,
        "selected": bool((selected_market_id and market_id == selected_market_id) or (selected_market_slug and slug == selected_market_slug)),
        "tokens": _token_entries(raw_market, orderbooks),
    }


def _resolve_event_payload(target_market: str) -> Tuple[Dict[str, Any], str, str]:
    market_target = resolve_market_target(target_market)
    selected_market_id = ""
    selected_market_slug = ""
    if market_target.kind in {"event", "slug"}:
        event = resolve_event(target_market)
        return event.raw_event, selected_market_id, selected_market_slug

    resolved_market = resolve_market(target_market)
    selected_market_id = resolved_market.market_id
    selected_market_slug = resolved_market.slug
    raw_event = dict(resolved_market.raw_event or {})
    raw_markets = raw_event.get("markets")
    if isinstance(raw_markets, list) and raw_markets:
        return raw_event, selected_market_id, selected_market_slug

    event_target = resolved_market.event_id or selected_market_slug
    if not event_target:
        raise RuntimeError(f"cannot resolve event payload for target={target_market}")
    gamma_client = PolymarketGammaClient()
    raw_event = gamma_client.fetch_event_by_id_or_slug(event_id=resolved_market.event_id, slug=_safe_str(raw_event.get("slug")))
    if not raw_event and selected_market_slug:
        event = resolve_event(selected_market_slug)
        raw_event = event.raw_event
    if not isinstance(raw_event, dict) or not raw_event:
        raise RuntimeError(f"cannot resolve event payload for target={target_market}")
    return raw_event, selected_market_id, selected_market_slug


def build_market_snapshot(
    *,
    target_market: str,
    include_orderbook: bool = True,
    orderbook_top_n: int = 10,
) -> Dict[str, Any]:
    raw_event, selected_market_id, selected_market_slug = _resolve_event_payload(target_market)
    raw_markets = raw_event.get("markets") if isinstance(raw_event.get("markets"), list) else []
    token_ids: List[str] = []
    for row in raw_markets:
        if not isinstance(row, dict):
            continue
        token_ids.extend(str(x) for x in normalize_json_list(row.get("clobTokenIds")) if str(x))
    unique_token_ids = list(dict.fromkeys(token_ids))
    orderbooks: Dict[str, Dict[str, Any]] = {}
    if include_orderbook and unique_token_ids:
        orderbooks = asyncio.run(_fetch_orderbook_rows(unique_token_ids, max(1, int(orderbook_top_n))))

    event_slug = _safe_str(raw_event.get("slug"))
    event_payload = {
        "event_id": _safe_str(raw_event.get("id") or raw_event.get("eventId") or raw_event.get("event_id")),
        "slug": event_slug,
        "title": _safe_str(raw_event.get("title")),
        "description": _safe_str(raw_event.get("description")),
        "start_date": _safe_str(raw_event.get("startDate") or raw_event.get("creationDate")),
        "end_date": _safe_str(raw_event.get("endDate")),
        "active": _safe_bool(raw_event.get("active"), True),
        "closed": _safe_bool(raw_event.get("closed"), False),
        "volume": raw_event.get("volume"),
        "liquidity": raw_event.get("liquidity"),
        "event_url": f"https://polymarket.com/event/{event_slug}" if event_slug else "",
    }

    markets = [
        _normalize_market_row(
            row,
            selected_market_id=selected_market_id,
            selected_market_slug=selected_market_slug,
            orderbooks=orderbooks,
        )
        for row in raw_markets
        if isinstance(row, dict)
    ]

    return {
        "target": {
            "raw_input": target_market,
            "selected_market_id": selected_market_id,
            "selected_market_slug": selected_market_slug,
        },
        "event": event_payload,
        "markets": markets,
        "meta": {
            "fetched_at_utc": _utc_now_iso(),
            "source": "internal_gamma_clob_clients",
            "include_orderbook": bool(include_orderbook),
            "orderbook_top_n": max(1, int(orderbook_top_n)),
        },
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description="Fetch weather-market snapshots using internal Gamma/CLOB clients.")
    parser.add_argument("--target-market", required=True, help="Polymarket event URL, market URL, slug, or condition id")
    parser.add_argument("--include-orderbook", default="true", help="Whether to fetch token orderbooks (true/false)")
    parser.add_argument("--orderbook-top-n", type=int, default=10)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    payload = build_market_snapshot(
        target_market=args.target_market,
        include_orderbook=_safe_bool(args.include_orderbook, True),
        orderbook_top_n=args.orderbook_top_n,
    )
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.out.strip():
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
        print(str(out_path))
        return 0
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
