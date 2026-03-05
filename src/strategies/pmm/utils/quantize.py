from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING, ROUND_HALF_UP
from typing import Dict, Optional


def _decimals_from_tick(tick: Decimal) -> int:
    # tick like 0.001 -> decimals 3, 0.01 -> 2
    exp = -tick.as_tuple().exponent
    return max(0, int(exp))


def quantize_to_tick(value: float, tick: float, mode: str = "nearest") -> float:
    """Quantize a price to a fixed tick size.

    mode:
    - nearest: round to nearest tick (HALF_UP)
    - floor: round down to tick
    - ceil: round up to tick
    """
    if tick <= 0:
        return float(value)
    d_tick = Decimal(str(tick))
    d_val = Decimal(str(value))
    if d_tick <= 0:
        return float(value)

    m = (mode or "nearest").strip().lower()
    if m == "floor":
        rounding = ROUND_FLOOR
    elif m == "ceil":
        rounding = ROUND_CEILING
    else:
        rounding = ROUND_HALF_UP

    steps = (d_val / d_tick).to_integral_value(rounding=rounding)
    out = steps * d_tick
    decimals = _decimals_from_tick(d_tick)
    # Convert back to float, but round to tick-decimals to avoid 0.419999999999 noise.
    return round(float(out), decimals)


def quantize_quote_pair(
    bid: float,
    ask: float,
    tick: float,
    mode: str,
) -> tuple[float, float]:
    """Quantize a bid/ask pair, ensuring they don't cross."""
    if tick <= 0:
        return bid, ask
    qb = quantize_to_tick(bid, tick=tick, mode=mode)
    qa = quantize_to_tick(ask, tick=tick, mode=mode)
    # Keep within probability bounds and avoid crossing.
    qb = max(0.0001, min(0.9998, qb))
    qa = max(0.0002, min(0.9999, qa))
    if qb >= qa:
        # Force a 1-tick gap if needed.
        qa = min(0.9999, quantize_to_tick(qb + tick, tick=tick, mode="ceil"))
        if qb >= qa:
            qa = min(0.9999, qb + 0.0001)
    return qb, qa


def quantize_price_dict(values: Dict[str, float], tick: float, mode: str) -> Dict[str, float]:
    """Quantize all prices in a {token_id: price} dict."""
    if tick <= 0:
        return dict(values)
    return {k: quantize_to_tick(v, tick=tick, mode=mode) for k, v in values.items()}


def quantize_quote_dict(
    values: Dict[str, Dict[str, float]],
    tick: float,
    mode: str,
) -> Dict[str, Dict[str, float]]:
    """Quantize all bid/ask pairs in a {token_id: {bid, ask}} dict."""
    if tick <= 0:
        return {k: dict(v) for k, v in values.items()}
    out: Dict[str, Dict[str, float]] = {}
    for token_id, q in values.items():
        bid = float(q.get("bid", 0.0))
        ask = float(q.get("ask", 0.0))
        qb, qa = quantize_quote_pair(bid, ask, tick=tick, mode=mode)
        out[token_id] = {"bid": qb, "ask": qa}
    return out


