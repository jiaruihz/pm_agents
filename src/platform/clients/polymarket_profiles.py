from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


WEB_BASE = "https://polymarket.com"
NEXT_DATA_RE = re.compile(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(?P<payload>.*?)</script>', re.DOTALL)


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


class PolymarketProfilesClient:
    def __init__(self, web_base: str = WEB_BASE) -> None:
        self.web_base = web_base.rstrip("/")
        self.session = _build_session()

    def fetch_profile_page(self, username: str, timeout_sec: float = 20.0) -> Dict[str, Any]:
        resp = self.session.get(
            f"{self.web_base}/profile/{quote('@' + username.lstrip('@'))}",
            headers={"Accept": "text/html", "User-Agent": "pm-agent-research/1.0"},
            timeout=timeout_sec,
            allow_redirects=True,
        )
        resp.raise_for_status()
        match = NEXT_DATA_RE.search(resp.text)
        if not match:
            raise RuntimeError("cannot find __NEXT_DATA__")
        return json.loads(match.group("payload"))

    def fetch_profile_user_data(self, address: str, timeout_sec: float = 20.0) -> Dict[str, Any]:
        resp = self.session.get(
            f"{self.web_base}/api/profile/userData",
            params={"address": address},
            headers={"Accept": "application/json", "User-Agent": "pm-agent-research/1.0"},
            timeout=timeout_sec,
        )
        resp.raise_for_status()
        payload = json.loads(resp.text)
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def find_query_data(queries: List[Dict[str, Any]], query_key_head: str) -> Optional[Any]:
        for row in queries:
            state = row.get("state") if isinstance(row, dict) else None
            qk = row.get("queryKey") if isinstance(row, dict) else None
            if not isinstance(state, dict) or not isinstance(qk, list) or not qk:
                continue
            if str(qk[0]) != query_key_head:
                continue
            return state.get("data")
        return None
