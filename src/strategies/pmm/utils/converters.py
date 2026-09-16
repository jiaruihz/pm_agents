"""Unified type conversion and data normalization utilities.

Single source of truth — replaces duplicate _to_float / _safe_float / _normalize_levels
scattered across tick_loop, order_manager, paper_broker, recorder, scenario_validator, market_ws.
"""
from __future__ import annotations

from typing import Any, List, Tuple


def to_float(value: Any, default: float = 0.0) -> float:
    """Safe float conversion with fallback default."""
    try:
        return float(value)
    except Exception:
        return default


def to_int(value: Any, default: int = 0) -> int:
    """Safe int conversion with fallback default."""
    try:
        return int(value)
    except Exception:
        return default


def normalize_levels(levels: Any) -> List[Tuple[float, float]]:
    """Normalize orderbook levels to [(price, size), ...] format.

    Accepts:
      - [{"price": ..., "size": ...}, ...]
      - [[price, size], ...]
      - [(price, size), ...]
    """
    out: List[Tuple[float, float]] = []
    if not levels:
        return out
    for level in levels:
        if isinstance(level, dict):
            price = to_float(level.get("price"), 0.0)
            size = to_float(level.get("size"), 0.0)
        elif isinstance(level, (list, tuple)) and len(level) >= 2:
            price = to_float(level[0], 0.0)
            size = to_float(level[1], 0.0)
        else:
            continue
        if price > 0 and size > 0:
            out.append((price, size))
    return out


def best_level(levels: Any, is_bid: bool) -> Tuple[float, float]:
    """Pick the best price level from raw levels.

    For bids: highest price. For asks: lowest price.
    """
    normalized = normalize_levels(levels)
    if not normalized:
        return 0.0, 0.0
    if is_bid:
        return max(normalized, key=lambda x: x[0])
    return min(normalized, key=lambda x: x[0])
