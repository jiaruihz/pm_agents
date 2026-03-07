from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


GAMMA_BASE = "https://gamma-api.polymarket.com"


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


class PolymarketCommentsClient:
    def __init__(self, base_url: str = GAMMA_BASE) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = _build_session()

    def fetch_comments(
        self,
        parent_entity_type: str,
        parent_entity_id: str,
        order: str = "reactionCount",
        limit: int = 10,
        offset: int = 0,
        holders_only: bool = False,
        timeout_sec: float = 20.0,
    ) -> List[Dict[str, Any]]:
        resp = self.session.get(
            f"{self.base_url}/comments",
            params={
                "get_positions": "true",
                "get_reports": "true",
                "parent_entity_type": parent_entity_type,
                "parent_entity_id": parent_entity_id,
                "ascending": "false",
                "holders_only": "true" if holders_only else "false",
                "order": order,
                "limit": str(limit),
                "offset": str(offset),
            },
            headers={"Accept": "application/json", "User-Agent": "pm-agent-research/1.0"},
            timeout=timeout_sec,
        )
        resp.raise_for_status()
        payload = json.loads(resp.text)
        return [x for x in payload if isinstance(x, dict)] if isinstance(payload, list) else []
