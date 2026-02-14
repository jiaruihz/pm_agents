"""Market microstructure signal functions.

Pure computation — no I/O, no side effects, fully testable.
Extracted from tick_loop.py to enable independent testing and reuse in replay_runner.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any, Dict, List

from src.domains.pmm.config import PMMConfig
from src.domains.pmm.utils.converters import normalize_levels, best_level


def weighted_mid(orderbook: Dict[str, Any]) -> float:
    """Weighted midpoint (micro-price proxy) to reduce toxic midpoint bias on thin books."""
    bid_price, bid_size = best_level(orderbook.get("bids"), is_bid=True)
    ask_price, ask_size = best_level(orderbook.get("asks"), is_bid=False)
    if bid_price <= 0 or ask_price <= 0:
        return 0.0
    denom = bid_size + ask_size
    if denom <= 0:
        return 0.0
    return (bid_price * ask_size + ask_price * bid_size) / denom


def fair_mid(orderbook: Dict[str, Any], mode: str) -> float:
    """Fair value mode switch. `weighted` first, fallback to normal midpoint."""
    from src.domains.pmm.data.orderbook import mid_price

    mode_value = (mode or "").strip().lower()
    if mode_value == "weighted":
        price = weighted_mid(orderbook)
        if price > 0:
            return price
    return mid_price(orderbook)


def depth_near_mid(orderbook: Dict[str, Any], delta: float) -> tuple[float, float]:
    """Compute bid/ask depth within `delta` of the midpoint."""
    from src.domains.pmm.data.orderbook import best_bid_ask

    top = best_bid_ask(orderbook)
    best_bid = top.get("best_bid", 0.0)
    best_ask = top.get("best_ask", 0.0)
    if best_bid <= 0 or best_ask <= 0:
        return 0.0, 0.0
    mid = (best_bid + best_ask) / 2.0
    bid_depth = 0.0
    ask_depth = 0.0
    for price, size in normalize_levels(orderbook.get("bids")):
        if price >= mid - delta:
            bid_depth += size
    for price, size in normalize_levels(orderbook.get("asks")):
        if price <= mid + delta:
            ask_depth += size
    return bid_depth, ask_depth


def order_flow_imbalance(orderbook: Dict[str, Any], delta: float) -> float:
    """Order flow imbalance in [-1, 1]: positive = bid pressure."""
    bid_depth, ask_depth = depth_near_mid(orderbook, delta)
    denom = bid_depth + ask_depth
    if denom <= 0:
        return 0.0
    return (bid_depth - ask_depth) / denom


def realized_vol(hist: deque[float]) -> float:
    """Realized volatility from a deque of midpoint prices."""
    if len(hist) < 3:
        return 0.0
    items = list(hist)
    returns: List[float] = []
    for prev, cur in zip(items[:-1], items[1:]):
        if prev <= 0:
            continue
        returns.append((cur - prev) / prev)
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((x - mean) ** 2 for x in returns) / len(returns)
    return math.sqrt(max(0.0, variance))


def momentum(hist: deque[float], min_points: int) -> float:
    """Simple momentum: (last - first) / first over the price history."""
    if len(hist) < max(2, min_points):
        return 0.0
    first = hist[0]
    last = hist[-1]
    if first <= 0:
        return 0.0
    return (last - first) / first


def required_spread(config: PMMConfig, rv: float, inventory_signal: float) -> float:
    """Minimum adaptive spread based on fees, target profit, volatility, and inventory."""
    return (
        config.fee_spread_floor
        + config.target_profit_spread
        + config.volatility_spread_coeff * max(0.0, rv)
        + config.inventory_risk_spread_coeff * abs(inventory_signal)
    )


def inventory_signal(
    token_id: str,
    token_ids: List[str],
    positions: Dict[str, float],
    max_position: float,
    sigmoid_k: float,
) -> float:
    """Non-linear inventory skew: near limits, quoting pressure increases faster."""
    current = positions.get(token_id, 0.0)
    if len(token_ids) >= 2:
        if token_id == token_ids[0]:
            net = current - positions.get(token_ids[1], 0.0)
        elif token_id == token_ids[1]:
            net = current - positions.get(token_ids[0], 0.0)
        else:
            net = current
    else:
        net = current
    denom = max(1.0, max_position)
    raw_signal = max(-1.0, min(1.0, net / denom))
    signal = (2.0 / (1.0 + math.exp(-sigmoid_k * raw_signal))) - 1.0
    return max(-1.0, min(1.0, signal))
