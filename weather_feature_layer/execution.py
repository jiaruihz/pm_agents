"""Shared execution-cost and orderbook-quality features.

These helpers describe market microstructure at decision time. They are not
weather alpha features; strategies should use them for sizing, maker/taker
choice, fillability, and replay diagnostics.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import pandas as pd


BOOK_STATE_MISSING = "missing"
BOOK_STATE_FEASIBLE = "feasible"
BOOK_STATE_THIN_WIDE = "thin_wide"

DEFAULT_FEASIBLE_SPREAD_MAX = 0.03
DEFAULT_FEASIBLE_DEPTH_ASK_5C_MIN = 25.0


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


def classify_book_state(
    spread: Any,
    depth_ask_5c: Any,
    *,
    feasible_spread_max: float = DEFAULT_FEASIBLE_SPREAD_MAX,
    feasible_depth_ask_5c_min: float = DEFAULT_FEASIBLE_DEPTH_ASK_5C_MIN,
) -> str:
    """Classify top-of-book execution quality using the HeadA v1 contract."""
    spread_float = to_float(spread)
    depth_float = to_float(depth_ask_5c)
    if not math.isfinite(spread_float) or not math.isfinite(depth_float):
        return BOOK_STATE_MISSING
    if spread_float <= feasible_spread_max and depth_float >= feasible_depth_ask_5c_min:
        return BOOK_STATE_FEASIBLE
    return BOOK_STATE_THIN_WIDE


def side_execution_features(
    row: Mapping[str, Any],
    *,
    side: str | None = None,
    feasible_spread_max: float = DEFAULT_FEASIBLE_SPREAD_MAX,
    feasible_depth_ask_5c_min: float = DEFAULT_FEASIBLE_DEPTH_ASK_5C_MIN,
) -> dict[str, Any]:
    """Return side-normalized execution features for BUY_YES or BUY_NO rows."""
    resolved_side = str(side or row.get("side") or "").upper()
    if resolved_side == "BUY_NO":
        spread = _first_float(row, "side_spread", "no_spread", "dec_no_spread")
        depth = _first_float(row, "side_depth_ask_5c", "no_depth_ask_5c", "dec_no_depth_ask_5c")
        ask = _first_float(row, "side_best_ask", "no_ask", "decision_entry_price")
    else:
        resolved_side = "BUY_YES"
        spread = _first_float(row, "side_spread", "yes_spread", "dec_yes_spread")
        depth = _first_float(row, "side_depth_ask_5c", "yes_depth_ask_5c", "dec_yes_depth_ask_5c")
        ask = _first_float(row, "side_best_ask", "ask", "yes_ask", "decision_entry_price")
    notional = None
    if ask is not None and depth is not None:
        notional = ask * depth
    return {
        "side": resolved_side,
        "side_spread": spread,
        "side_depth_ask_5c": depth,
        "side_fillable_notional_ask_5c": notional,
        "book_state_v1": classify_book_state(
            spread,
            depth,
            feasible_spread_max=feasible_spread_max,
            feasible_depth_ask_5c_min=feasible_depth_ask_5c_min,
        ),
    }


def add_side_execution_features(df: pd.DataFrame) -> pd.DataFrame:
    """Append side-normalized execution fields to candidate/order rows."""
    if df.empty:
        return df.copy()
    rows = []
    for row in df.to_dict("records"):
        item = dict(row)
        item.update(side_execution_features(item))
        rows.append(item)
    return pd.DataFrame(rows, columns=list(rows[0].keys()))


def summarize_city_execution_profile(df: pd.DataFrame, *, min_rows: int = 1) -> pd.DataFrame:
    """Summarize city-level spread/depth distributions from execution rows."""
    if df.empty:
        return pd.DataFrame(
            columns=[
                "city",
                "execution_profile_rows",
                "side_spread_p50",
                "side_spread_p90",
                "side_depth_ask_5c_p10",
                "side_depth_ask_5c_p50",
                "side_fillable_notional_ask_5c_p10",
                "side_fillable_notional_ask_5c_p50",
                "book_state_feasible_rate",
                "book_state_missing_rate",
                "book_state_thin_wide_rate",
            ]
        )
    frame = add_side_execution_features(df)
    frame["side_spread"] = pd.to_numeric(frame["side_spread"], errors="coerce")
    frame["side_depth_ask_5c"] = pd.to_numeric(frame["side_depth_ask_5c"], errors="coerce")
    frame["side_fillable_notional_ask_5c"] = pd.to_numeric(
        frame["side_fillable_notional_ask_5c"], errors="coerce"
    )
    rows: list[dict[str, Any]] = []
    for city, group in frame.groupby("city", dropna=False):
        if len(group) < min_rows:
            continue
        book_state = group["book_state_v1"].astype(str)
        rows.append(
            {
                "city": city,
                "execution_profile_rows": int(len(group)),
                "side_spread_p50": _quantile(group["side_spread"], 0.50),
                "side_spread_p90": _quantile(group["side_spread"], 0.90),
                "side_depth_ask_5c_p10": _quantile(group["side_depth_ask_5c"], 0.10),
                "side_depth_ask_5c_p50": _quantile(group["side_depth_ask_5c"], 0.50),
                "side_fillable_notional_ask_5c_p10": _quantile(
                    group["side_fillable_notional_ask_5c"], 0.10
                ),
                "side_fillable_notional_ask_5c_p50": _quantile(
                    group["side_fillable_notional_ask_5c"], 0.50
                ),
                "book_state_feasible_rate": float(book_state.eq(BOOK_STATE_FEASIBLE).mean()),
                "book_state_missing_rate": float(book_state.eq(BOOK_STATE_MISSING).mean()),
                "book_state_thin_wide_rate": float(book_state.eq(BOOK_STATE_THIN_WIDE).mean()),
            }
        )
    return pd.DataFrame(rows)


def _first_float(row: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = to_float(row.get(key))
        if math.isfinite(value):
            return value
    return None


def _quantile(series: pd.Series, q: float) -> float | None:
    cleaned = pd.to_numeric(series, errors="coerce").dropna()
    if cleaned.empty:
        return None
    return float(cleaned.quantile(q))
