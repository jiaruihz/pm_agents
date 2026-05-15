from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.strategies.rule_lawyer.services.common import normalize_json_list


class PolymarketGammaClient:
    def __init__(self, base_url: str = "https://gamma-api.polymarket.com") -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = float(os.getenv("POLYMARKET_GAMMA_TIMEOUT_SEC", "4.0"))
        retries = int(os.getenv("POLYMARKET_GAMMA_RETRIES", "1"))
        self.session = requests.Session()
        self.session.mount(
            "https://",
            HTTPAdapter(
                max_retries=Retry(
                    total=retries,
                    connect=retries,
                    read=retries,
                    backoff_factor=0.4,
                    status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=frozenset(["GET"]),
                )
            ),
        )

    def _get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        resp = self.session.get(
            f"{self.base_url}{path}",
            params={k: v for k, v in (params or {}).items() if v is not None and str(v) != ""},
            headers={"Accept": "application/json", "User-Agent": "pm-agent-polymarket-gamma/1.0"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return json.loads(resp.text)

    @staticmethod
    def _extract_market(payload: Any) -> Optional[Dict[str, Any]]:
        if isinstance(payload, list):
            return payload[0] if payload else None
        if isinstance(payload, dict):
            if isinstance(payload.get("markets"), list) and payload["markets"]:
                return payload["markets"][0]
            if isinstance(payload.get("market"), dict):
                return payload["market"]
            return payload
        return None

    @staticmethod
    def _extract_event(payload: Any) -> Optional[Dict[str, Any]]:
        if isinstance(payload, list):
            return payload[0] if payload else None
        if isinstance(payload, dict):
            if isinstance(payload.get("events"), list) and payload["events"]:
                return payload["events"][0]
            if isinstance(payload.get("event"), dict):
                return payload["event"]
            return payload
        return None

    @staticmethod
    def _market_matches(market: Dict[str, Any], market_id: str = "", slug: str = "", condition_id: str = "") -> bool:
        if market_id:
            values = {
                str(market.get("id") or "").strip(),
                str(market.get("marketId") or "").strip(),
                str(market.get("market_id") or "").strip(),
            }
            if market_id in values:
                return True
        if slug and str(market.get("slug") or "").strip() == slug:
            return True
        if condition_id and str(market.get("conditionId") or "").strip() == condition_id:
            return True
        return False

    @staticmethod
    def _event_matches(event: Dict[str, Any], event_id: str = "", slug: str = "") -> bool:
        if event_id:
            values = {
                str(event.get("id") or "").strip(),
                str(event.get("eventId") or "").strip(),
                str(event.get("event_id") or "").strip(),
            }
            if event_id in values:
                return True
        if slug and str(event.get("slug") or "").strip() == slug:
            return True
        return False

    def fetch_market_by_id_or_slug(self, market_id: str = "", slug: str = "") -> Optional[Dict[str, Any]]:
        candidates: List[tuple[str, Optional[Dict[str, Any]]]] = []
        if market_id:
            candidates.extend(
                [
                    (f"/markets/{market_id}", None),
                    (f"/market/{market_id}", None),
                    ("/markets", {"id": market_id}),
                    ("/markets", {"market_id": market_id}),
                    ("/markets", {"marketId": market_id}),
                ]
            )
        if slug:
            candidates.extend(
                [
                    ("/markets", {"slug": slug}),
                    ("/markets", {"market_slug": slug}),
                ]
            )
        for path, params in candidates:
            try:
                payload = self._get_json(path, params=params)
            except Exception:
                continue
            if isinstance(payload, list):
                for row in payload:
                    if isinstance(row, dict) and self._market_matches(row, market_id=market_id, slug=slug):
                        return row
                continue
            market = self._extract_market(payload)
            if market and self._market_matches(market, market_id=market_id, slug=slug):
                return market
        return None

    def fetch_market_by_condition_id(self, condition_id: str) -> Optional[Dict[str, Any]]:
        for key in ("condition_id", "conditionId"):
            try:
                payload = self._get_json("/markets", params={key: condition_id, "limit": 1})
            except Exception:
                continue
            if isinstance(payload, list):
                for row in payload:
                    if isinstance(row, dict) and self._market_matches(row, condition_id=condition_id):
                        return row
                continue
            market = self._extract_market(payload)
            if market and self._market_matches(market, condition_id=condition_id):
                return market
        return None

    def fetch_event_by_id_or_slug(self, event_id: str = "", slug: str = "") -> Optional[Dict[str, Any]]:
        candidates: List[tuple[str, Optional[Dict[str, Any]]]] = []
        if event_id:
            candidates.extend(
                [
                    (f"/events/{event_id}", None),
                    ("/events", {"id": event_id}),
                    ("/events", {"event_id": event_id}),
                    ("/events", {"eventId": event_id}),
                ]
            )
        if slug:
            candidates.extend(
                [
                    ("/events", {"slug": slug}),
                    ("/events", {"event_slug": slug}),
                ]
            )
        for path, params in candidates:
            try:
                payload = self._get_json(path, params=params)
            except Exception:
                continue
            if isinstance(payload, list):
                for row in payload:
                    if isinstance(row, dict) and self._event_matches(row, event_id=event_id, slug=slug):
                        return row
                continue
            event = self._extract_event(payload)
            if event and self._event_matches(event, event_id=event_id, slug=slug):
                return event
        return None

    def parse_list_field(self, value: Any) -> List[Any]:
        return normalize_json_list(value)
