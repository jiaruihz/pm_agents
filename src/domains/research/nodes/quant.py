"""Quant node: compute edge and select candidates."""

import json
from typing import Any, Dict, List

from ..metrics import compute_metrics
from ..storage import get_market_rule_parse, get_latest_orderbook, get_latest_price, get_markets_by_status
from .constants import DEFAULT_EDGE_THRESHOLD, STATUS_INVESTIGATED, STATUS_PARSED


class QuantNode:
    def __init__(self, edge_threshold: float = DEFAULT_EDGE_THRESHOLD, limit: int = 500) -> None:
        self.edge_threshold = edge_threshold
        self.limit = limit

    def _get_primary_token_id(self, clob_token_ids_json: str) -> str:
        try:
            token_ids = json.loads(clob_token_ids_json or "[]")
            if isinstance(token_ids, str):
                token_ids = json.loads(token_ids)
            if isinstance(token_ids, list) and token_ids:
                return str(token_ids[0])
        except Exception:
            return ""
        return ""

    def evaluate(self) -> List[Dict[str, Any]]:
        markets = get_markets_by_status([STATUS_INVESTIGATED, STATUS_PARSED], limit=self.limit)
        candidates: List[Dict[str, Any]] = []
        for m in markets:
            token_id = self._get_primary_token_id(m.get("clob_token_ids_json") or "")
            if not token_id:
                continue
            price_row = get_latest_price(token_id)
            if not price_row or price_row.get("mid") is None:
                continue
            levels = get_latest_orderbook(token_id)
            metrics = compute_metrics(price_row, levels)
            analysis = get_market_rule_parse(m["market_id"], "v1") or {}
            alpha_score = analysis.get("alpha_score")
            if alpha_score is None:
                continue
            my_prob = alpha_score / 100.0
            edge = my_prob - float(price_row["mid"])
            if edge <= self.edge_threshold:
                continue
            candidates.append(
                {
                    "market_id": m["market_id"],
                    "slug": m.get("slug"),
                    "category": m.get("category"),
                    "end_at_utc": m.get("end_at_utc"),
                    "edge": edge,
                    "alpha_score": alpha_score,
                    "mid": price_row.get("mid"),
                    "best_bid": price_row.get("best_bid"),
                    "best_ask": price_row.get("best_ask"),
                    "spread": metrics.get("spread"),
                    "liquidity": m.get("liquidity"),
                    "volume": m.get("volume"),
                }
            )
        return candidates
