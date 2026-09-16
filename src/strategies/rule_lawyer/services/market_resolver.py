from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urlparse

from src.platform.clients.polymarket_gamma import PolymarketGammaClient
from src.strategies.rule_lawyer.models_research import MarketTarget, ResolvedEvent, ResolvedMarket
from src.strategies.rule_lawyer.services.common import CONDITION_RE, normalize_json_list, to_float


def _extract_slug_from_url(url: str) -> str:
    parsed = urlparse(url.strip())
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return ""
    if "event" in parts:
        idx = parts.index("event")
        if idx + 2 < len(parts):
            return parts[idx + 2]
        if idx + 1 < len(parts):
            return parts[idx + 1]
    if "market" in parts:
        idx = parts.index("market")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return parts[-1]


def _extract_url_kind(url: str) -> str:
    parsed = urlparse(url.strip())
    parts = [p for p in parsed.path.split("/") if p]
    if "event" in parts:
        idx = parts.index("event")
        return "market" if idx + 2 < len(parts) else "event"
    if "market" in parts:
        return "market"
    return "unknown"


def resolve_market_target(target: str) -> MarketTarget:
    text = (target or "").strip()
    if text.startswith("http://") or text.startswith("https://"):
        url_kind = _extract_url_kind(text)
        return MarketTarget(raw_input=text, kind=url_kind if url_kind != "unknown" else "url", url=text, slug=_extract_slug_from_url(text))
    if CONDITION_RE.match(text):
        return MarketTarget(raw_input=text, kind="condition_id", condition_id=text)
    return MarketTarget(raw_input=text, kind="slug", slug=text)


def _build_resolved_market(raw_market: Dict[str, Any], market_target: Optional[MarketTarget] = None, raw_event: Optional[Dict[str, Any]] = None) -> ResolvedMarket:
    raw_event = raw_event if isinstance(raw_event, dict) else {}
    outcomes = [str(x) for x in normalize_json_list(raw_market.get("outcomes")) if str(x)]
    token_ids = [str(x) for x in normalize_json_list(raw_market.get("clobTokenIds")) if str(x)]
    outcome_prices = [to_float(x, 0.0) for x in normalize_json_list(raw_market.get("outcomePrices"))]
    slug = str(raw_market.get("slug") or (market_target.slug if market_target else "") or "").strip()
    market_url = f"https://polymarket.com/market/{slug}" if slug else (market_target.url if market_target else "")
    return ResolvedMarket(
        market_id=str(raw_market.get("id") or ""),
        event_id=str(raw_event.get("id") or raw_market.get("eventId") or raw_market.get("event_id") or ""),
        slug=slug,
        condition_id=str(raw_market.get("conditionId") or (market_target.condition_id if market_target else "") or ""),
        question=str(raw_market.get("question") or ""),
        description=str(raw_market.get("description") or ""),
        rules=str(raw_market.get("rules") or ""),
        category=str(raw_market.get("category") or ""),
        outcomes=outcomes,
        token_ids=token_ids,
        outcome_prices=outcome_prices,
        volume=to_float(raw_market.get("volumeNum"), to_float(raw_market.get("volume"), 0.0)),
        liquidity=to_float(raw_market.get("liquidityNum"), to_float(raw_market.get("liquidity"), 0.0)),
        active=bool(raw_market.get("active", True)),
        closed=bool(raw_market.get("closed", False)),
        end_date=str(raw_market.get("endDate") or ""),
        market_url=market_url,
        event_title=str(raw_event.get("title") or ""),
        event_description=str(raw_event.get("description") or ""),
        raw_market=raw_market,
        raw_event=raw_event,
    )


def _resolve_raw_market(market_target: MarketTarget) -> Dict[str, Any]:
    gamma_client = PolymarketGammaClient()
    raw_market: Optional[Dict[str, Any]] = None
    raw_event: Optional[Dict[str, Any]] = None
    if market_target.condition_id:
        raw_market = gamma_client.fetch_market_by_condition_id(market_target.condition_id)
    elif market_target.slug:
        raw_market = gamma_client.fetch_market_by_id_or_slug(slug=market_target.slug)
        if not raw_market:
            raw_event = gamma_client.fetch_event_by_id_or_slug(slug=market_target.slug)
            if raw_event and isinstance(raw_event.get("markets"), list) and raw_event["markets"]:
                raw_market = raw_event["markets"][0]
    if raw_market and not raw_event:
        events = raw_market.get("events")
        if isinstance(events, list) and events and isinstance(events[0], dict):
            raw_event = events[0]
        else:
            event_id = raw_market.get("eventId") or raw_market.get("event_id")
            if event_id:
                raw_event = gamma_client.fetch_event_by_id_or_slug(event_id=str(event_id))
    if not raw_market:
        raise RuntimeError(f"cannot resolve market for target={market_target.raw_input}")
    return {"market": raw_market, "event": raw_event or {}}


def resolve_market(target: str) -> ResolvedMarket:
    market_target = resolve_market_target(target)
    payload = _resolve_raw_market(market_target)
    raw_market = payload["market"]
    raw_event = payload["event"] if isinstance(payload.get("event"), dict) else {}
    return _build_resolved_market(raw_market=raw_market, market_target=market_target, raw_event=raw_event)


def resolve_event(target: str) -> ResolvedEvent:
    market_target = resolve_market_target(target)
    gamma_client = PolymarketGammaClient()
    raw_event: Optional[Dict[str, Any]] = None
    if market_target.kind == "event":
        raw_event = gamma_client.fetch_event_by_id_or_slug(slug=market_target.slug)
    elif market_target.kind == "slug":
        raw_event = gamma_client.fetch_event_by_id_or_slug(slug=market_target.slug)
    elif market_target.kind == "condition_id":
        raw_market = gamma_client.fetch_market_by_condition_id(market_target.condition_id)
        if raw_market:
            event_id = str(raw_market.get("eventId") or raw_market.get("event_id") or "")
            if event_id:
                raw_event = gamma_client.fetch_event_by_id_or_slug(event_id=event_id)
    if not raw_event:
        raw_market = resolve_market(target)
        raw_event = raw_market.raw_event
    if not raw_event:
        raise RuntimeError(f"cannot resolve event for target={target}")
    raw_markets = raw_event.get("markets") if isinstance(raw_event.get("markets"), list) else []
    markets = [_build_resolved_market(row, raw_event=raw_event).to_dict() for row in raw_markets if isinstance(row, dict)]
    slug = str(raw_event.get("slug") or market_target.slug or "").strip()
    return ResolvedEvent(
        event_id=str(raw_event.get("id") or raw_event.get("eventId") or raw_event.get("event_id") or ""),
        slug=slug,
        title=str(raw_event.get("title") or ""),
        description=str(raw_event.get("description") or ""),
        ticker=str(raw_event.get("ticker") or ""),
        start_date=str(raw_event.get("startDate") or raw_event.get("creationDate") or ""),
        end_date=str(raw_event.get("endDate") or ""),
        active=bool(raw_event.get("active", True)),
        closed=bool(raw_event.get("closed", False)),
        volume=to_float(raw_event.get("volume"), 0.0),
        liquidity=to_float(raw_event.get("liquidity"), 0.0),
        event_url=f"https://polymarket.com/event/{slug}" if slug else market_target.url,
        markets=markets,
        raw_event=raw_event,
    )
