from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from weather_feature_layer.market import Bracket, bracket_contains, parse_bracket
from weather_feature_layer.regimes import (
    add_regime_labels,
    label_day_space,
    label_intraday_state,
    label_moisture_cloud,
    label_running_state,
    label_solar_window,
    label_wind_noise,
    unit_step,
)


ROOT = Path(__file__).resolve().parents[4]
ATLAS_ROWS = ROOT / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv"

STAKE_USD = 5.0
ASK_MIN = 0.10
ASK_MAX = 0.35
DECISION_HOURS = {10, 11, 12, 13, 14}
ASK_CAPS = {
    "strict35": 0.35,
    "relaxed50": 0.50,
    "relaxed70": 0.70,
}
BASELINE_VARIANT = "baseline_current_no_strict35_near_noon"
OPTIMISTIC_MAIN_CANDIDATE = "routed_capped_d1_no_relaxed50_best_ask"
MAIN_CANDIDATE = "routed_capped_d1_no_relaxed50_first_eligible"
OPTIMISTIC_BALANCED_SOFT_CANDIDATE = "routed_capped_d2_no_relaxed70_best_ask"
BALANCED_SOFT_CANDIDATE = "routed_capped_d2_no_relaxed70_first_eligible"
BALANCED_SOFT_POLICY = "soft_balanced"


def expression_candidates(states: pd.DataFrame, expression: str) -> pd.DataFrame:
    ask_col = f"{expression}_ask"
    payoff_col = f"{expression}_payoff"
    size_col = "current_no_ask_size" if expression == "current_bracket_no" else f"{expression}_ask_size"
    out = states.copy()
    out["expression"] = expression
    out["ask"] = pd.to_numeric(out.get(ask_col), errors="coerce")
    out["payoff"] = pd.to_numeric(out.get(payoff_col), errors="coerce")
    out["ask_size"] = pd.to_numeric(out.get(size_col), errors="coerce")
    out["ask_notional"] = out["ask"] * out["ask_size"]
    out["stake_cost_usd"] = STAKE_USD
    out["stake_profit_usd"] = np.where(out["payoff"].notna(), out["payoff"] * (STAKE_USD / out["ask"]) - STAKE_USD, np.nan)
    return out


def select_one_per_city_day(frame: pd.DataFrame, selector: str) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    out = frame.copy()
    out["noon_distance"] = (pd.to_numeric(out["decision_hour_local"], errors="coerce") - 12).abs()
    if "decision_snapshot_ts_utc" not in out.columns:
        out["decision_snapshot_ts_utc"] = ""
    if selector == "near_noon":
        sort_cols = ["target_date", "city", "noon_distance", "decision_hour_local", "ask"]
    elif selector == "first_eligible":
        sort_cols = ["target_date", "city", "decision_hour_local", "decision_snapshot_ts_utc", "ask"]
    elif selector == "best_ask":
        sort_cols = ["target_date", "city", "ask", "noon_distance", "decision_hour_local"]
    else:
        raise ValueError(selector)
    return (
        out.sort_values(sort_cols)
        .drop_duplicates(["target_date", "city"], keep="first")
        .reset_index(drop=True)
    )


def apply_liquidity(frame: pd.DataFrame, ask_max: float, *, require_payoff: bool = True) -> pd.DataFrame:
    mask = (
        frame["decision_hour_local"].isin(DECISION_HOURS)
        & frame["ask"].between(ASK_MIN, ask_max)
        & pd.to_numeric(frame["ask_notional"], errors="coerce").ge(STAKE_USD)
    )
    if require_payoff:
        mask = mask & frame["payoff"].notna()
    return frame[mask].copy()


def routed_candidates(states: pd.DataFrame, capped_expression: str) -> pd.DataFrame:
    cur = expression_candidates(states, "current_bracket_no")
    cur = cur[cur["day_regime"].isin(["day_open_runway", "day_marginal_runway"])].copy()
    cur["route_leg"] = "runway_current_no"

    capped = expression_candidates(states, capped_expression)
    capped = capped[capped["day_regime"].eq("day_forecast_capped")].copy()
    capped["route_leg"] = f"capped_{capped_expression}"

    return pd.concat([cur, capped], ignore_index=True)


def variant_frame(
    states: pd.DataFrame,
    variant: str,
    ask_max: float = ASK_MAX,
    selector: str = "near_noon",
    *,
    require_payoff: bool = True,
) -> pd.DataFrame:
    if variant.startswith("baseline_current_no"):
        frame = expression_candidates(states, "current_bracket_no")
        frame = frame[frame["day_regime"].isin(["day_open_runway", "day_marginal_runway", "day_forecast_capped"])].copy()
        frame["route_leg"] = "baseline_current_no"
    elif "routed_capped_d1_no" in variant:
        frame = routed_candidates(states, "d1_no")
    elif "routed_capped_d2_no" in variant:
        frame = routed_candidates(states, "d2_no")
    else:
        raise ValueError(variant)
    selected = select_one_per_city_day(apply_liquidity(frame, ask_max, require_payoff=require_payoff), selector)
    selected["ask_max"] = ask_max
    selected["selector"] = selector
    return selected


def add_soft_weights(selected: pd.DataFrame) -> pd.DataFrame:
    out = selected.copy()
    if out.empty:
        return out
    if {"soft_balanced", "soft_moderate", "full_size"}.issubset(out.columns):
        return out
    out["is_capped_route"] = out["route_leg"].astype(str).str.startswith("capped")
    out["is_marginal_runway"] = out["day_regime"].eq("day_marginal_runway")
    out["is_open_runway"] = out["day_regime"].eq("day_open_runway")
    out["is_humid_family"] = out["city_family"].eq("humid_low_latitude")
    out["is_mature_fade"] = out["intraday_state"].eq("mature_fade")
    regime_cols = ["moisture_cloud_regime", "wind_regime", "running_max_state", "intraday_state"]
    out["is_unknown_weather"] = out[regime_cols].astype(str).apply(lambda row: any("unknown" in item for item in row), axis=1)
    out["high_ask_risk"] = ((pd.to_numeric(out["ask"], errors="coerce") - 0.35) / 0.35).clip(0, 1).fillna(0)
    if "decision_hour_local_float" in out.columns:
        decision_hour = pd.to_numeric(out["decision_hour_local_float"], errors="coerce")
    else:
        decision_hour = pd.Series(np.nan, index=out.index, dtype="float64")
    if "decision_hour_local" in out.columns:
        decision_hour_fallback = pd.to_numeric(out["decision_hour_local"], errors="coerce")
    else:
        decision_hour_fallback = pd.Series(np.nan, index=out.index, dtype="float64")
    decision_hour = decision_hour.fillna(decision_hour_fallback)
    peak_hour = pd.to_numeric(out.get("forecast_peak_hour_local"), errors="coerce")
    out["forecast_peak_delta_hours_local"] = decision_hour - peak_hour
    current_no_route = out["route_leg"].astype(str).eq("runway_current_no") | out["expression"].astype(str).eq(
        "current_bracket_no"
    )
    peak_delta = pd.to_numeric(out["forecast_peak_delta_hours_local"], errors="coerce")
    out["peak_clock_state"] = np.select(
        [
            ~current_no_route,
            peak_delta.isna(),
            peak_delta.le(-2.0),
            peak_delta.le(0.0),
            peak_delta.le(1.0),
        ],
        [
            "not_current_no_route",
            "unknown",
            "peak_2h_plus_ahead",
            "peak_0_to_2h_ahead",
            "peak_passed_0_to_1h",
        ],
        default="peak_passed_1h_plus",
    )
    out["peak_clock_multiplier"] = np.select(
        [
            ~current_no_route,
            peak_delta.isna(),
            peak_delta.le(-2.0),
            peak_delta.le(0.0),
            peak_delta.le(1.0),
        ],
        [
            1.00,
            0.70,
            1.00,
            0.70,
            0.45,
        ],
        default=0.20,
    )

    out["route_multiplier"] = np.select(
        [out["is_marginal_runway"], out["is_open_runway"], out["is_capped_route"]],
        [1.00, 0.70, 0.45],
        default=0.65,
    )
    out["price_multiplier"] = (1.0 - 0.55 * out["high_ask_risk"]).clip(0.35, 1.0)
    out["weather_multiplier"] = (
        1.0
        - 0.12 * out["is_humid_family"].astype(float)
        - 0.10 * out["is_mature_fade"].astype(float)
        - 0.08 * out["is_unknown_weather"].astype(float)
    ).clip(0.70, 1.0)
    out["day_risk"] = (
        0.35 * out["is_capped_route"].astype(float)
        + 0.15 * out["is_open_runway"].astype(float)
        + 0.15 * out["is_humid_family"].astype(float)
        + 0.15 * out["is_mature_fade"].astype(float)
        + 0.20 * out["high_ask_risk"].astype(float)
    ).clip(0, 1)
    out["day_multiplier"] = (1.0 - 0.50 * out["day_risk"]).clip(0.50, 1.0)
    daily = out.groupby("target_date").agg(
        replay_day_risk_mean=("day_risk", "mean"),
        replay_day_multiplier_mean=("day_multiplier", "mean"),
    )
    out = out.merge(daily, left_on="target_date", right_index=True, how="left")
    out["soft_moderate"] = (
        (0.25 + 0.75 * out["route_multiplier"])
        * (0.70 + 0.30 * out["price_multiplier"])
        * (0.80 + 0.20 * out["day_multiplier"])
        * (0.50 + 0.50 * out["peak_clock_multiplier"])
    )
    out["soft_route_price"] = out["route_multiplier"] * out["price_multiplier"]
    out["soft_balanced"] = (
        out["route_multiplier"]
        * out["price_multiplier"]
        * out["weather_multiplier"]
        * out["day_multiplier"]
        * out["peak_clock_multiplier"]
    )
    out["full_size"] = 1.0
    return out
