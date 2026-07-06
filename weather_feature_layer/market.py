"""Shared market geometry primitives for weather exact-bracket markets."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd


NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
SETTLEMENT_NUM_RE = re.compile(r"(?<!\d)-?\d+(?:\.\d+)?")


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


def settlement_interval(bracket: Any) -> tuple[float, float] | None:
    """Exact-bracket settlement interval used by Tmax local distribution work."""
    text = "" if bracket is None else str(bracket).strip().replace("−", "-")
    nums = [float(x) for x in SETTLEMENT_NUM_RE.findall(text)]
    if not nums:
        return None
    lower_text = text.lower()
    if "+" in lower_text or "above" in lower_text:
        return (nums[0] - 0.5, math.inf)
    if "below" in lower_text or "or less" in lower_text:
        return (-math.inf, nums[0] + 0.5)
    if len(nums) >= 2:
        lo, hi = min(nums[0], nums[1]), max(nums[0], nums[1])
        return (lo - 0.5, hi + 0.5)
    value = nums[0]
    return (value - 0.5, value + 0.5)


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


def _finite_float(value: Any) -> float | None:
    out = to_float(value)
    if not math.isfinite(out):
        return None
    return out


def _safe_upper(interval: tuple[float, float] | None) -> float | None:
    if interval is None:
        return None
    hi = interval[1]
    if math.isinf(hi):
        return None
    return hi


def _safe_mid(interval: tuple[float, float] | None) -> float | None:
    if interval is None:
        return None
    lo, hi = interval
    if math.isinf(lo) or math.isinf(hi):
        return None
    return (lo + hi) / 2.0


def _delta(left: Any, right: Any) -> float | None:
    left_float = _finite_float(left)
    right_float = _finite_float(right)
    if left_float is None or right_float is None:
        return None
    return left_float - right_float


def _frac(value: Any) -> float | None:
    out = _finite_float(value)
    if out is None:
        return None
    return out - math.floor(out)


def _share(value: Any, lo: float | None, hi: float | None) -> float | None:
    out = _finite_float(value)
    if out is None or lo is None or hi is None or not math.isfinite(hi) or hi <= lo:
        return None
    return (out - lo) / (hi - lo)


def _dist_to_upper_share(value: Any, lo: float | None, hi: float | None) -> float | None:
    out = _finite_float(value)
    if out is None or lo is None or hi is None or not math.isfinite(hi) or hi <= lo:
        return None
    return (hi - out) / (hi - lo)


def _hour_bucket(value: Any) -> str:
    hour = _finite_float(value)
    if hour is None:
        return "hour_unknown"
    if hour <= 11:
        return "10_11"
    if hour <= 13:
        return "12_13"
    if hour <= 16:
        return "14_16"
    return "17_21"


def _market_quote_features(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    aliases = {
        "current_yes": ("current_yes",),
        "current_no": ("current_no", "current_bracket_no"),
        "d1_no": ("d1_no",),
        "d2_no": ("d2_no",),
    }
    for leg, prefixes in aliases.items():
        ask = None
        bid = None
        ask_size = None
        bid_size = None
        for prefix in prefixes:
            if ask is None and f"{prefix}_ask" in row:
                ask = row.get(f"{prefix}_ask")
            if bid is None and f"{prefix}_bid" in row:
                bid = row.get(f"{prefix}_bid")
            if ask_size is None and f"{prefix}_ask_size" in row:
                ask_size = row.get(f"{prefix}_ask_size")
            if bid_size is None and f"{prefix}_bid_size" in row:
                bid_size = row.get(f"{prefix}_bid_size")
        ask_float = _finite_float(ask)
        bid_float = _finite_float(bid)
        out[f"{leg}_best_ask"] = ask_float
        out[f"{leg}_best_bid"] = bid_float
        out[f"{leg}_ask_size"] = _finite_float(ask_size)
        out[f"{leg}_bid_size"] = _finite_float(bid_size)
        out[f"{leg}_spread"] = None if ask_float is None or bid_float is None else ask_float - bid_float
        out[f"{leg}_mid"] = None if ask_float is None or bid_float is None else (ask_float + bid_float) / 2.0
    return out


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


def add_market_geometry_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add shared exact-bracket geometry and symmetric bid/ask book-state fields."""
    rows: list[dict[str, Any]] = []
    for item in df.to_dict("records"):
        row = dict(item)
        intervals = {
            "current": settlement_interval(row.get("current_bracket")),
            "d1": settlement_interval(row.get("d1_no_bracket")),
            "d2": settlement_interval(row.get("d2_no_bracket")),
        }
        for leg, interval in intervals.items():
            lo: float | None = None
            hi: float | None = None
            if interval is not None:
                lo, hi = interval
            row[f"{leg}_bracket_low_native"] = lo
            row[f"{leg}_bracket_high_native"] = hi
            row[f"{leg}_bracket_mid_native"] = _safe_mid(interval)

        hour = _finite_float(row.get("decision_hour_local"))
        row["hour_bucket"] = _hour_bucket(row.get("decision_hour_local"))
        row["decision_hour_sin"] = None
        row["decision_hour_cos"] = None
        if hour is not None:
            row["decision_hour_sin"] = math.sin(2.0 * math.pi * hour / 24.0)
            row["decision_hour_cos"] = math.cos(2.0 * math.pi * hour / 24.0)

        peak_delta = _finite_float(row.get("forecast_peak_delta_hours_local"))
        row["forecast_peak_delta_abs"] = abs(peak_delta) if peak_delta is not None else None

        current_iv = intervals["current"]
        d1_iv = intervals["d1"]
        d2_iv = intervals["d2"]
        current_upper = _safe_upper(current_iv)
        d1_upper = _safe_upper(d1_iv)
        d2_upper = _safe_upper(d2_iv)
        current_mid = _safe_mid(current_iv)
        d1_mid = _safe_mid(d1_iv)
        d2_mid = _safe_mid(d2_iv)

        row["forecast_minus_running_native"] = _delta(row.get("forecast_max_native"), row.get("running_native"))
        row["forecast_minus_current_native"] = _delta(row.get("forecast_max_native"), row.get("current_native"))
        row["running_minus_current_native"] = _delta(row.get("running_native"), row.get("current_native"))
        row["forecast_to_current_upper_native"] = _delta(row.get("forecast_max_native"), current_upper)
        row["forecast_to_d1_upper_native"] = _delta(row.get("forecast_max_native"), d1_upper)
        row["forecast_to_d2_upper_native"] = _delta(row.get("forecast_max_native"), d2_upper)
        row["forecast_to_current_mid_native"] = _delta(row.get("forecast_max_native"), current_mid)
        row["forecast_to_d1_mid_native"] = _delta(row.get("forecast_max_native"), d1_mid)
        row["forecast_to_d2_mid_native"] = _delta(row.get("forecast_max_native"), d2_mid)
        row["running_to_current_upper_native"] = _delta(row.get("running_native"), current_upper)
        row["current_to_current_upper_native"] = _delta(row.get("current_native"), current_upper)
        row["running_position_in_current_native"] = _delta(row.get("running_native"), current_mid)
        row["current_position_in_current_native"] = _delta(row.get("current_native"), current_mid)

        current_lo: float | None = None
        current_hi: float | None = None
        if current_iv is not None:
            current_lo, current_hi = current_iv
        row["current_bracket_width_native"] = (
            current_hi - current_lo
            if current_lo is not None and current_hi is not None and math.isfinite(current_hi)
            else None
        )
        row["current_frac_in_current_bracket"] = _share(row.get("current_native"), current_lo, current_hi)
        row["running_frac_in_current_bracket"] = _share(row.get("running_native"), current_lo, current_hi)
        row["forecast_frac_in_current_bracket"] = _share(row.get("forecast_max_native"), current_lo, current_hi)
        row["current_native_frac"] = _frac(row.get("current_native"))
        row["running_native_frac"] = _frac(row.get("running_native"))
        row["forecast_native_frac"] = _frac(row.get("forecast_max_native"))
        row["current_dist_to_upper_share"] = _dist_to_upper_share(row.get("current_native"), current_lo, current_hi)
        row["running_dist_to_upper_share"] = _dist_to_upper_share(row.get("running_native"), current_lo, current_hi)
        row["forecast_dist_to_upper_share"] = _dist_to_upper_share(row.get("forecast_max_native"), current_lo, current_hi)

        row.update(_market_quote_features(row))
        rows.append(row)
    return pd.DataFrame(rows, columns=list(rows[0].keys()) if rows else list(df.columns))
