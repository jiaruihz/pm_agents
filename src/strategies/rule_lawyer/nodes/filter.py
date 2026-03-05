"""Filter node: coarse filtering to protect LLM budget."""

from datetime import datetime, timezone
import json
from typing import Any, Dict, Iterable, List, Optional

from ..storage import (
    get_events_by_ids,
    get_latest_price,
    get_market_events_from_raw,
    get_markets_for_filter,
    update_market_statuses,
)
from .constants import (
    DEFAULT_IGNORE_CATEGORIES,
    DEFAULT_LIQUIDITY_THRESHOLD,
    DEFAULT_MIN_DESCRIPTION_LEN,
    DEFAULT_MIN_QUESTION_LEN,
    DEFAULT_MIN_RULES_LEN,
    DEFAULT_PRICE_MAX,
    DEFAULT_PRICE_MIN,
    STATUS_EXPIRED,
    STATUS_IGNORED,
    STATUS_NEW,
    STATUS_READY_TO_PARSE,
)


class FilterNode:
    def __init__(
        self,
        liquidity_threshold: float = DEFAULT_LIQUIDITY_THRESHOLD,
        price_min: float = DEFAULT_PRICE_MIN,
        price_max: float = DEFAULT_PRICE_MAX,
        min_question_len: int = DEFAULT_MIN_QUESTION_LEN,
        min_rules_len: int = DEFAULT_MIN_RULES_LEN,
        min_description_len: int = DEFAULT_MIN_DESCRIPTION_LEN,
        require_active: bool = True,
        require_unresolved: bool = True,
        require_token_ids: bool = True,
        require_best_bid_ask: bool = False,
        check_end_date: bool = True,
        ignore_categories: Optional[Iterable[str]] = None,
        limit: Optional[int] = 1000,
        metadata_only: bool = False,
        scan_all: bool = False,
    ) -> None:
        self.liquidity_threshold = liquidity_threshold
        self.price_min = price_min
        self.price_max = price_max
        self.min_question_len = min_question_len
        self.min_rules_len = min_rules_len
        self.min_description_len = min_description_len
        self.require_active = require_active
        self.require_unresolved = require_unresolved
        self.require_token_ids = require_token_ids
        self.require_best_bid_ask = require_best_bid_ask
        self.check_end_date = check_end_date
        self.ignore_categories = set(ignore_categories or DEFAULT_IGNORE_CATEGORIES)
        if limit is not None and limit <= 0:
            limit = None
        self.limit = limit
        self.metadata_only = metadata_only
        self.scan_all = scan_all
        self._ignore_categories_lower = {c.strip().lower() for c in self.ignore_categories}

    def _parse_iso(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            if value.endswith("Z"):
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            return datetime.fromisoformat(value)
        except Exception:
            return None

    def _get_primary_token_id(self, clob_token_ids_json: str) -> Optional[str]:
        try:
            token_ids = json.loads(clob_token_ids_json or "[]")
            if isinstance(token_ids, str):
                token_ids = json.loads(token_ids)
            if isinstance(token_ids, list) and token_ids:
                return str(token_ids[0])
        except Exception:
            return None
        return None

    def _get_mid_price(self, token_id: Optional[str]) -> Optional[float]:
        if not token_id:
            return None
        row = get_latest_price(token_id)
        if not row:
            return None
        mid = row.get("mid")
        if mid is None and row.get("best_bid") is not None and row.get("best_ask") is not None:
            mid = (row["best_bid"] + row["best_ask"]) / 2
        return mid

    def _has_best_bid_ask(self, token_id: Optional[str]) -> Optional[float]:
        if not token_id:
            return None
        row = get_latest_price(token_id)
        if not row:
            return None
        if row.get("best_bid") is None or row.get("best_ask") is None:
            return None
        mid = row.get("mid")
        if mid is None:
            mid = (row["best_bid"] + row["best_ask"]) / 2
        return mid

    def _get_outcome_price(self, market: Dict[str, Any]) -> Optional[float]:
        try:
            outcomes = json.loads(market.get("outcomes_json") or "[]")
            prices = json.loads(market.get("outcome_prices_json") or "[]")
        except Exception:
            return None
        if not isinstance(outcomes, list) or not isinstance(prices, list):
            return None
        if len(prices) == 2 and len(outcomes) == 2:
            for idx, name in enumerate(outcomes):
                if str(name).strip().lower() in {"yes", "y"}:
                    try:
                        return float(prices[idx])
                    except Exception:
                        return None
            try:
                return float(prices[0])
            except Exception:
                return None
        return None

    def execute(self) -> Dict[str, Any]:
        statuses = None if self.scan_all else [STATUS_NEW]
        markets = get_markets_for_filter(statuses=statuses, limit=self.limit)
        ignored: List[str] = []
        ready: List[str] = []
        expired: List[str] = []
        ignored_reasons: Dict[str, int] = {}

        def _ignore(market_id: str, reason: str) -> None:
            ignored.append(market_id)
            ignored_reasons[reason] = ignored_reasons.get(reason, 0) + 1

        def _expire(market_id: str) -> None:
            expired.append(market_id)
            ignored_reasons["expired"] = ignored_reasons.get("expired", 0) + 1

        event_ids_set = set()
        market_events: Dict[str, List[str]] = {}
        raw_event_cache = get_market_events_from_raw([m["market_id"] for m in markets])
        for m in markets:
            try:
                event_ids = json.loads(m.get("event_ids_json") or "[]")
                if isinstance(event_ids, list):
                    ids = [str(eid) for eid in event_ids if eid]
                else:
                    ids = []
            except Exception:
                ids = []
            if not ids:
                raw_events = raw_event_cache.get(m["market_id"]) or {}
                ids = raw_events.get("event_ids") or []
            if ids:
                market_events[m["market_id"]] = ids
                event_ids_set.update(ids)
        events_map = get_events_by_ids(list(event_ids_set)) if event_ids_set else {}

        for m in markets:
            market_id = m["market_id"]
            if self.require_active and m.get("active") != 1:
                _ignore(market_id, "inactive")
                continue
            if self.require_unresolved and m.get("resolved") == 1:
                _ignore(market_id, "resolved")
                continue
            if self.check_end_date:
                end_at = self._parse_iso(m.get("end_at_utc"))
                if end_at and end_at <= datetime.now(timezone.utc):
                    _expire(market_id)
                    continue
            question = (m.get("question") or "").strip()
            rules = (m.get("rules") or "").strip()
            description = (m.get("description") or "").strip()
            if self.min_question_len > 0 and len(question) < self.min_question_len:
                _ignore(market_id, "question_short")
                continue
            if self.min_rules_len > 0 or self.min_description_len > 0:
                if len(rules) < self.min_rules_len and len(description) < self.min_description_len:
                    _ignore(market_id, "missing_rules")
                    continue
            category = (m.get("category") or "").strip()
            if category and category in self.ignore_categories:
                _ignore(market_id, "category_ignored")
                continue
            event_ids = market_events.get(market_id, [])
            if event_ids and events_map:
                event_tags = []
                for eid in event_ids:
                    ev = events_map.get(eid)
                    if not ev:
                        continue
                    try:
                        tags = json.loads(ev.get("tags_json") or "[]")
                        if isinstance(tags, list):
                            event_tags.extend([str(t).lower() for t in tags if t])
                    except Exception:
                        continue
                if any(tag in self._ignore_categories_lower for tag in event_tags):
                    _ignore(market_id, "event_tag_ignored")
                    continue
            liquidity = m.get("liquidity") or 0
            if liquidity < self.liquidity_threshold:
                _ignore(market_id, "low_liquidity")
                continue
            token_id = self._get_primary_token_id(m.get("clob_token_ids_json") or "")
            if self.require_token_ids and not token_id:
                _ignore(market_id, "missing_token")
                continue
            mid_price = None
            if not self.metadata_only:
                if self.require_best_bid_ask:
                    mid_price = self._has_best_bid_ask(token_id)
                    if mid_price is None:
                        _ignore(market_id, "missing_best_bid_ask")
                        continue
                else:
                    mid_price = self._get_outcome_price(m)
                    if mid_price is None:
                        mid_price = self._get_mid_price(token_id)
            else:
                mid_price = self._get_outcome_price(m)
            if mid_price is not None and (mid_price < self.price_min or mid_price > self.price_max):
                _ignore(market_id, "price_extreme")
                continue
            ready.append(market_id)

        ignored_count = update_market_statuses(ignored, STATUS_IGNORED)
        expired_count = update_market_statuses(expired, STATUS_EXPIRED)
        ready_count = update_market_statuses(ready, STATUS_READY_TO_PARSE)
        return {
            "scanned": len(markets),
            "ignored": ignored_count,
            "expired": expired_count,
            "ready_to_parse": ready_count,
            "ignored_reasons": ignored_reasons,
        }

    def evaluate_market(self, market: Dict[str, Any]) -> Dict[str, Any]:
        market_id = market.get("market_id")
        if not market_id:
            return {"market_id": None, "passed": False, "reason": "missing_market_id"}

        raw_event_cache = get_market_events_from_raw([market_id])
        try:
            event_ids = json.loads(market.get("event_ids_json") or "[]")
            if isinstance(event_ids, list):
                ids = [str(eid) for eid in event_ids if eid]
            else:
                ids = []
        except Exception:
            ids = []
        if not ids:
            raw_events = raw_event_cache.get(market_id) or {}
            ids = raw_events.get("event_ids") or []
        event_ids = ids
        events_map = get_events_by_ids(event_ids) if event_ids else {}

        def _result(passed: bool, reason: Optional[str] = None, status: Optional[str] = None) -> Dict[str, Any]:
            return {
                "market_id": market_id,
                "passed": passed,
                "reason": reason,
                "status": status,
            }

        if self.require_active and market.get("active") != 1:
            update_market_statuses([market_id], STATUS_IGNORED)
            return _result(False, "inactive", STATUS_IGNORED)
        if self.require_unresolved and market.get("resolved") == 1:
            update_market_statuses([market_id], STATUS_IGNORED)
            return _result(False, "resolved", STATUS_IGNORED)
        if self.check_end_date:
            end_at = self._parse_iso(market.get("end_at_utc"))
            if end_at and end_at <= datetime.now(timezone.utc):
                update_market_statuses([market_id], STATUS_EXPIRED)
                return _result(False, "expired", STATUS_EXPIRED)
        question = (market.get("question") or "").strip()
        rules = (market.get("rules") or "").strip()
        description = (market.get("description") or "").strip()
        if self.min_question_len > 0 and len(question) < self.min_question_len:
            update_market_statuses([market_id], STATUS_IGNORED)
            return _result(False, "question_short", STATUS_IGNORED)
        if self.min_rules_len > 0 or self.min_description_len > 0:
            if len(rules) < self.min_rules_len and len(description) < self.min_description_len:
                update_market_statuses([market_id], STATUS_IGNORED)
                return _result(False, "missing_rules", STATUS_IGNORED)
        category = (market.get("category") or "").strip()
        if category and category in self.ignore_categories:
            update_market_statuses([market_id], STATUS_IGNORED)
            return _result(False, "category_ignored", STATUS_IGNORED)
        if event_ids and events_map:
            event_tags = []
            for eid in event_ids:
                ev = events_map.get(eid)
                if not ev:
                    continue
                try:
                    tags = json.loads(ev.get("tags_json") or "[]")
                    if isinstance(tags, list):
                        event_tags.extend([str(t).lower() for t in tags if t])
                except Exception:
                    continue
            if any(tag in self._ignore_categories_lower for tag in event_tags):
                update_market_statuses([market_id], STATUS_IGNORED)
                return _result(False, "event_tag_ignored", STATUS_IGNORED)
        liquidity = market.get("liquidity") or 0
        if liquidity < self.liquidity_threshold:
            update_market_statuses([market_id], STATUS_IGNORED)
            return _result(False, "low_liquidity", STATUS_IGNORED)
        token_id = self._get_primary_token_id(market.get("clob_token_ids_json") or "")
        if self.require_token_ids and not token_id:
            update_market_statuses([market_id], STATUS_IGNORED)
            return _result(False, "missing_token", STATUS_IGNORED)
        mid_price = None
        if not self.metadata_only:
            if self.require_best_bid_ask:
                mid_price = self._has_best_bid_ask(token_id)
                if mid_price is None:
                    update_market_statuses([market_id], STATUS_IGNORED)
                    return _result(False, "missing_best_bid_ask", STATUS_IGNORED)
            else:
                mid_price = self._get_outcome_price(market)
                if mid_price is None:
                    mid_price = self._get_mid_price(token_id)
        else:
            mid_price = self._get_outcome_price(market)
        if mid_price is not None and (mid_price < self.price_min or mid_price > self.price_max):
            update_market_statuses([market_id], STATUS_IGNORED)
            return _result(False, "price_extreme", STATUS_IGNORED)

        update_market_statuses([market_id], STATUS_READY_TO_PARSE)
        return _result(True, None, STATUS_READY_TO_PARSE)
