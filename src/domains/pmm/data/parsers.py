"""Data parsing utilities for API responses.

Extracted from tick_loop.py for independent testing and reuse.
"""
from __future__ import annotations

import ast
from typing import Any, Dict, List, Optional, Tuple

from src.domains.pmm.utils.converters import to_float, to_int


def mid_from_market(market: Dict[str, Any]) -> Optional[float]:
    """Parse fallback mid from `/market/{token_id}` payload when orderbook is unavailable."""
    raw = market.get("outcome_prices")
    if raw is None:
        return None
    try:
        if isinstance(raw, list):
            return float(raw[0])
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, list) and parsed:
            return float(parsed[0])
    except Exception:
        return None
    return None


def parse_partition(value: Any) -> List[int]:
    """Parse a partition list from a raw API value, defaulting to [1, 2]."""
    if not isinstance(value, list):
        return [1, 2]
    out: List[int] = []
    for item in value:
        i = to_int(item, 0)
        if i > 0:
            out.append(i)
    return out if len(out) >= 2 else [1, 2]


def parse_account_state(
    balance_raw: Any,
    open_orders_raw: Any,
    positions_raw: Any,
    token_ids: List[str],
) -> Tuple[float, List[Dict[str, Any]], Dict[str, float]]:
    """Parse raw account state into (usdc_balance, open_orders, positions) tuple."""
    balance = balance_raw if isinstance(balance_raw, dict) else {}
    usdc_balance = to_float(balance.get("usdc_balance"), 0.0)
    open_orders = open_orders_raw if isinstance(open_orders_raw, list) else []

    if isinstance(positions_raw, dict):
        positions = {tid: to_float(positions_raw.get(tid), 0.0) for tid in token_ids}
    else:
        positions = {tid: 0.0 for tid in token_ids}
    return usdc_balance, open_orders, positions


def pending_credit_total(pending_items: List[Dict[str, float]], now_ts: float) -> float:
    """Keep merge-pending credits alive until TTL expires and return current credit total."""
    alive: List[Dict[str, float]] = []
    total = 0.0
    for item in pending_items:
        expire_at = to_float(item.get("expire_at"), 0.0)
        amount = max(0.0, to_float(item.get("amount"), 0.0))
        if expire_at <= 0 or now_ts <= expire_at:
            alive.append({"amount": amount, "expire_at": expire_at})
            total += amount
    pending_items[:] = alive
    return total
