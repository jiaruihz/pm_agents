"""Shared market geometry primitives for weather exact-bracket markets."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any


NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Bracket:
    raw: str
    low: float | None
    high: float | None


def parse_bracket(value: Any) -> Bracket | None:
    if value is None:
        return None
    raw = str(value).replace("°", "").strip()
    if not raw:
        return None
    if raw.endswith("+"):
        try:
            return Bracket(raw=raw, low=float(raw[:-1]), high=None)
        except ValueError:
            return None
    if "-" in raw:
        left, right = raw.split("-", 1)
        try:
            return Bracket(raw=raw, low=float(left), high=float(right))
        except ValueError:
            return None
    try:
        val = float(raw)
    except ValueError:
        return None
    return Bracket(raw=raw, low=val, high=val)


def bracket_contains(bracket: Bracket, value: float) -> bool:
    if bracket.low is not None and value < bracket.low:
        return False
    if bracket.high is not None and value > bracket.high:
        return False
    return True


def parse_bracket_bounds(bracket: Any) -> tuple[float | None, float | None]:
    """Legacy low-price YES bracket bounds parser.

    A single number, including labels such as ``36+``, returns ``(x, x)`` to
    preserve the existing tail-telemetry contract.
    """
    text = "" if bracket is None else str(bracket).strip().replace("−", "-")
    nums = [float(x) for x in NUM_RE.findall(text)]
    if not nums:
        return (None, None)
    if len(nums) == 1:
        return (nums[0], nums[0])
    return (min(nums[0], nums[1]), max(nums[0], nums[1]))


def to_float(value: Any, default: float = math.nan) -> float:
    try:
        if value is None:
            return default
        out = float(value)
        if not math.isfinite(out):
            return default
        return out
    except Exception:
        return default


def bracket_distance_features(row: dict[str, Any]) -> dict[str, Any]:
    low, high = parse_bracket_bounds(row.get("bracket"))
    forecast_native = to_float(row.get("forecast_max_native"))
    out: dict[str, Any] = {
        "bracket_low_native": low,
        "bracket_high_native": high,
        "bracket_distance_available": False,
        "forecast_to_bracket_low_native": None,
        "forecast_above_bracket_high_native": None,
        "forecast_inside_bracket_bounds": None,
    }
    if low is None or high is None or not math.isfinite(forecast_native):
        return out
    out.update(
        {
            "bracket_distance_available": True,
            "forecast_to_bracket_low_native": round(low - forecast_native, 6),
            "forecast_above_bracket_high_native": round(forecast_native - high, 6),
            "forecast_inside_bracket_bounds": bool(low <= forecast_native <= high),
        }
    )
    return out
