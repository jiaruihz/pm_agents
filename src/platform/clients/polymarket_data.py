from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


DATA_API_BASE = "https://data-api.polymarket.com"


def _build_session() -> requests.Session:
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


class PolymarketDataClient:
    def __init__(self, base_url: str = DATA_API_BASE) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = _build_session()

    def _get_json(self, path: str, params: Optional[Dict[str, Any]] = None, timeout_sec: float = 20.0) -> Any:
        resp = self.session.get(
            f"{self.base_url}{path}",
            params={k: v for k, v in (params or {}).items() if v is not None and str(v) != ""},
            headers={"Accept": "application/json", "User-Agent": "pm-agent-research/1.0"},
            timeout=timeout_sec,
            allow_redirects=True,
        )
        resp.raise_for_status()
        return json.loads(resp.text)

    def get_json_safe(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        default: Any = None,
        timeout_sec: float = 20.0,
    ) -> Any:
        try:
            return self._get_json(path, params=params, timeout_sec=timeout_sec)
        except Exception:
            return default

    def get_user_trades(self, user: str, limit: int, offset: int = 0) -> List[Dict[str, Any]]:
        rows = self.get_json_safe("/trades", params={"user": user, "limit": limit, "offset": offset}, default=[])
        return [x for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []

    def iter_user_trades(self, user: str, page_size: int = 200, max_rows: int = 1000) -> Iterator[List[Dict[str, Any]]]:
        offset = 0
        total = 0
        while total < max(1, int(max_rows)):
            limit = min(max(1, int(page_size)), max_rows - total)
            rows = self.get_user_trades(user=user, limit=limit, offset=offset)
            if not rows:
                break
            yield rows
            total += len(rows)
            if len(rows) < limit:
                break
            offset += len(rows)

    def get_user_positions(self, user: str, limit: int = 200, offset: int = 0) -> List[Dict[str, Any]]:
        rows = self.get_json_safe(
            "/positions",
            params={"user": user, "limit": limit, "offset": offset},
            default=[],
            timeout_sec=30.0,
        )
        return [x for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []

    def iter_user_positions(self, user: str, page_size: int = 200, max_rows: int = 5000) -> Iterator[List[Dict[str, Any]]]:
        offset = 0
        total = 0
        while total < max(1, int(max_rows)):
            limit = min(max(1, int(page_size)), max_rows - total)
            rows = self.get_user_positions(user=user, limit=limit, offset=offset)
            if not rows and offset == 0:
                rows = self.get_json_safe("/positions", params={"user": user}, default=[], timeout_sec=30.0)
                rows = [x for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []
            if not rows:
                break
            yield rows
            total += len(rows)
            if len(rows) < limit:
                break
            offset += len(rows)

    def get_user_closed_positions(
        self,
        user: str,
        limit: int = 50,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_direction: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rows = self.get_json_safe(
            "/closed-positions",
            params={
                "user": user,
                "limit": limit,
                "offset": offset,
                "sortBy": sort_by,
                "sortDirection": sort_direction,
            },
            default=[],
            timeout_sec=30.0,
        )
        return [x for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []

    def iter_user_closed_positions(
        self,
        user: str,
        page_size: int = 50,
        max_rows: int = 5000,
        sort_by: Optional[str] = None,
        sort_direction: Optional[str] = None,
    ) -> Iterator[List[Dict[str, Any]]]:
        offset = 0
        total = 0
        while total < max(1, int(max_rows)):
            limit = min(max(1, int(page_size)), max_rows - total)
            rows = self.get_user_closed_positions(
                user=user,
                limit=limit,
                offset=offset,
                sort_by=sort_by,
                sort_direction=sort_direction,
            )
            if not rows and offset == 0:
                rows = self.get_json_safe("/closed-positions", params={"user": user}, default=[], timeout_sec=30.0)
                rows = [x for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []
            if not rows:
                break
            yield rows
            total += len(rows)
            if len(rows) < limit:
                break
            offset += len(rows)

    def get_market_holders(self, condition_id: str, limit: int = 40) -> List[Dict[str, Any]]:
        rows = self.get_json_safe("/holders", params={"market": condition_id, "limit": limit}, default=[])
        return [x for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []

    def get_market_trades(self, condition_id: str, limit: int, offset: int = 0) -> List[Dict[str, Any]]:
        rows = self.get_json_safe("/trades", params={"market": condition_id, "limit": limit, "offset": offset}, default=[])
        return [x for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []
