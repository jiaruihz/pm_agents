from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING, ROUND_HALF_UP
from typing import Optional


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

