"""Gamma API client for markets/events discovery."""

import json
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx

from src.models import Event, Market

from ..config import get_settings
from .http import HttpClient


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_utc_iso(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        return datetime.fromisoformat(value).astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def _normalize_json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
            return [parsed] if parsed else []
        except Exception:
            return [value] if value else []
    return []


def _as_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


async def fetch_paginated(endpoint: str, params: Dict[str, Any], max_pages: Optional[int] = None) -> List[Dict[str, Any]]:
    client = HttpClient()
    results: List[Dict[str, Any]] = []
    page = 0
    limit = params.get("limit", 100)
    try:
        while True:
            if max_pages is not None and page >= max_pages:
                break
            current_params = dict(params)
            current_params["offset"] = page * limit
            data = await client.get_json(endpoint, params=current_params)
            items = data if isinstance(data, list) else data.get("markets") or data.get("events") or []
            if not items:
                break
            results.extend(items)
            if len(items) < limit:
                break
            page += 1
    finally:
        await client.aclose()
    return results


async def iter_paginated(
    endpoint: str,
    params: Dict[str, Any],
    max_pages: Optional[int] = None,
    start_offset: int = 0,
) -> AsyncIterator[Tuple[List[Dict[str, Any]], int]]:
    client = HttpClient()
    page = 0
    limit = params.get("limit", 100)
    offset = start_offset
    try:
        while True:
            if max_pages is not None and page >= max_pages:
                break
            current_params = dict(params)
            current_params["offset"] = offset
            data = await client.get_json(endpoint, params=current_params)
            items = data if isinstance(data, list) else data.get("markets") or data.get("events") or []
            if not items:
                break
            yield items, offset
            if len(items) < limit:
                break
            page += 1
            offset += limit
    finally:
        await client.aclose()


def normalize_market(raw: Dict[str, Any]) -> Market:
    end_at = raw.get("endDate") or raw.get("endDateISO") or raw.get("endTime")
    updated_at = raw.get("updatedAt") or raw.get("updated_at")
    clob_ids = raw.get("clobTokenIds") or raw.get("clob_token_ids") or raw.get("clob_token_ids_json")
    outcomes = raw.get("outcomes")
    outcome_prices = raw.get("outcomePrices")
    events = raw.get("events") or []
    clob_token_ids = [str(x) for x in _normalize_json_list(clob_ids) if x is not None and str(x)]
    outcome_list = _normalize_json_list(outcomes)
    outcome_price_list = _normalize_json_list(outcome_prices)
    event_ids: List[str] = []
    event_slugs: List[str] = []
    event_titles: List[str] = []
    event_tickers: List[str] = []
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            eid = event.get("id") or event.get("eventId")
            if eid is not None:
                event_ids.append(str(eid))
            slug = event.get("slug")
            if slug:
                event_slugs.append(str(slug))
            title = event.get("title")
            if title:
                event_titles.append(str(title))
            ticker = event.get("ticker")
            if ticker:
                event_tickers.append(str(ticker))
    return Market(
        market_id=_as_optional_str(raw.get("id")) or _as_optional_str(raw.get("marketId")),
        slug=_as_optional_str(raw.get("slug")),
        question=_as_optional_str(raw.get("question")),
        description=_as_optional_str(raw.get("description")),
        rules=_as_optional_str(raw.get("rules")),
        category=_as_optional_str(raw.get("category")),
        active=bool(raw.get("active", True)),
        resolved=bool(raw.get("resolved", False)),
        end_at_utc=_to_utc_iso(end_at),
        volume=raw.get("volume"),
        liquidity=raw.get("liquidity"),
        outcomes=outcome_list,
        outcome_prices=outcome_price_list,
        clob_token_ids=clob_token_ids,
        event_ids=event_ids,
        event_slugs=event_slugs,
        event_titles=event_titles,
        event_tickers=event_tickers,
        updated_at_utc=_to_utc_iso(updated_at),
        last_synced_at_utc=_now_utc_iso(),
    )


def normalize_event(raw: Dict[str, Any]) -> Event:
    updated_at = raw.get("updatedAt") or raw.get("updated_at")
    start_at = raw.get("startDate") or raw.get("startDateIso")
    end_at = raw.get("endDate") or raw.get("endDateIso")
    tags = raw.get("tags") or []
    tag_slugs: List[str] = []
    if isinstance(tags, list):
        for tag in tags:
            if not isinstance(tag, dict):
                continue
            slug = tag.get("slug") or tag.get("label")
            if slug:
                tag_slugs.append(str(slug).lower())
    return Event(
        event_id=_as_optional_str(raw.get("id")) or _as_optional_str(raw.get("eventId")),
        slug=_as_optional_str(raw.get("slug")),
        title=_as_optional_str(raw.get("title")),
        description=_as_optional_str(raw.get("description")),
        ticker=_as_optional_str(raw.get("ticker")),
        tags=tag_slugs,
        active=bool(raw.get("active", True)),
        closed=bool(raw.get("closed", False)),
        start_at_utc=_to_utc_iso(start_at),
        end_at_utc=_to_utc_iso(end_at),
        volume=raw.get("volume"),
        liquidity=raw.get("liquidity"),
        updated_at_utc=_to_utc_iso(updated_at),
        last_synced_at_utc=_now_utc_iso(),
    )


async def fetch_markets(
    active: bool, page_size: int, pages: Optional[int] = None, closed: Optional[bool] = None
) -> List[Dict[str, Any]]:
    settings = get_settings()
    endpoint = f"{settings.gamma_base_url}/markets"
    params = {"active": str(active).lower(), "limit": page_size}
    if closed is not None:
        params["closed"] = str(closed).lower()
    return await fetch_paginated(endpoint, params=params, max_pages=pages)


async def fetch_events(
    active: bool, page_size: int, pages: Optional[int] = None, closed: Optional[bool] = None
) -> List[Dict[str, Any]]:
    settings = get_settings()
    endpoint = f"{settings.gamma_base_url}/events"
    params = {"active": str(active).lower(), "limit": page_size}
    if closed is not None:
        params["closed"] = str(closed).lower()
    return await fetch_paginated(endpoint, params=params, max_pages=pages)


def _extract_market_payload(data: Any) -> Optional[Dict[str, Any]]:
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict):
        if isinstance(data.get("markets"), list) and data["markets"]:
            return data["markets"][0]
        if isinstance(data.get("market"), dict):
            return data["market"]
        return data
    return None


async def fetch_market_by_id_or_slug(
    market_id: Optional[str] = None,
    slug: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    settings = get_settings()
    client = HttpClient()
    base = settings.gamma_base_url.rstrip("/")
    candidates: List[Tuple[str, Optional[Dict[str, Any]]]] = []
    if market_id:
        candidates.extend(
            [
                (f"{base}/markets/{market_id}", None),
                (f"{base}/market/{market_id}", None),
                (f"{base}/markets", {"id": market_id}),
                (f"{base}/markets", {"market_id": market_id}),
                (f"{base}/markets", {"marketId": market_id}),
            ]
        )
    if slug:
        candidates.extend(
            [
                (f"{base}/markets", {"slug": slug}),
                (f"{base}/markets", {"market_slug": slug}),
            ]
        )
    try:
        for url, params in candidates:
            try:
                payload = await client.get_json(url, params=params)
            except httpx.HTTPStatusError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    continue
                continue
            except httpx.RequestError:
                continue
            market = _extract_market_payload(payload)
            if market:
                return market
    finally:
        await client.aclose()
    return None
