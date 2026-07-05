from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from weather_data_feed.city_family import CITY_FAMILY_ATLAS_V1 as CITY_FAMILY


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


def unit_step(row: pd.Series) -> float:
    return 1.0 if str(row.get("unit", "")).upper() == "F" else 0.5


def label_solar_window(hour: float) -> str:
    if pd.isna(hour):
        return "hour_missing"
    if hour <= 11:
        return "late_morning"
    if hour <= 14:
        return "solar_peak_window"
    if hour <= 17:
        return "afternoon_decay_window"
    return "evening_tail"


def label_day_space(row: pd.Series) -> str:
    gap = row.get("forecast_gap_to_running_native")
    if pd.isna(gap):
        return "day_space_unknown"
    step = unit_step(row)
    if gap >= 3 * step:
        return "day_open_runway"
    if gap >= step:
        return "day_marginal_runway"
    if gap >= -step:
        return "day_forecast_capped"
    return "day_forecast_busted"


def label_moisture_cloud(row: pd.Series) -> str:
    rh = row.get("relative_humidity_pct")
    sky = row.get("sky_cover_code")
    dew_dep = row.get("dewpoint_depression_f")
    if pd.isna(rh) and pd.isna(sky) and pd.isna(dew_dep):
        return "moisture_cloud_unknown"
    if pd.notna(rh) and rh >= 80 and pd.notna(sky) and sky >= 3:
        return "humid_overcast_suppression"
    if pd.notna(rh) and rh >= 75:
        return "humid_convective_risk"
    if pd.notna(sky) and sky >= 3:
        return "cloud_suppression"
    if pd.notna(dew_dep) and dew_dep >= 25:
        return "dry_heat_inertia"
    return "mixed_moisture"


def label_wind_noise(row: pd.Series) -> str:
    wind = row.get("wind_speed_kt")
    if pd.isna(wind):
        return "wind_unknown"
    if wind >= 18:
        return "windy_mixing_noise"
    if wind >= 10:
        return "moderate_wind"
    return "light_wind"


def label_running_state(row: pd.Series) -> str:
    mins = row.get("minutes_since_running_max")
    decline = row.get("decline_native")
    step = unit_step(row)
    if pd.isna(mins) or pd.isna(decline):
        return "running_max_clock_unknown"
    if decline <= 0.25 * step and mins <= 45:
        return "fresh_running_high"
    if decline <= 0.5 * step and mins <= 120:
        return "near_high_plateau"
    if decline <= 0.5 * step:
        return "stalled_high"
    if mins >= 120:
        return "mature_fade"
    return "pullback_from_high"


def label_intraday_state(row: pd.Series) -> str:
    trend1 = row.get("temp_trend_1h_f")
    trend3 = row.get("temp_trend_3h_f")
    decline = row.get("decline_native")
    mins = row.get("minutes_since_running_max")
    hour = row.get("decision_hour_local")
    step = unit_step(row)
    if pd.isna(trend1) and pd.isna(trend3):
        return "state_unknown"
    if pd.notna(trend1) and trend1 >= 1.0 and (pd.isna(decline) or decline <= step):
        return "active_warming"
    if pd.notna(decline) and decline <= 0.25 * step and pd.notna(mins) and mins <= 60:
        return "fresh_high"
    if pd.notna(decline) and decline <= 0.5 * step and pd.notna(mins) and mins > 60:
        return "plateau_near_high"
    if pd.notna(decline) and decline > 0.5 * step:
        if pd.notna(hour) and hour <= 14 and pd.notna(trend3) and trend3 > 0:
            return "false_fade_risk"
        if pd.notna(trend1) and trend1 > 0:
            return "reheating_after_dip"
        if pd.notna(mins) and mins >= 120:
            return "mature_fade"
        return "pullback_uncertain"
    if pd.notna(trend3) and trend3 <= 0:
        return "flat_or_cooling"
    return "slow_warming"


def add_regime_labels(states: pd.DataFrame) -> pd.DataFrame:
    out = states.copy()
    out["city_family"] = out["city"].map(CITY_FAMILY).fillna("other")
    out["solar_window"] = out["decision_hour_local"].apply(label_solar_window)
    out["day_regime"] = out.apply(label_day_space, axis=1)
    out["moisture_cloud_regime"] = out.apply(label_moisture_cloud, axis=1)
    out["wind_regime"] = out.apply(label_wind_noise, axis=1)
    out["running_max_state"] = out.apply(label_running_state, axis=1)
    out["intraday_state"] = out.apply(label_intraday_state, axis=1)
    out["composite_regime"] = (
        out["day_regime"].astype(str)
        + " | "
        + out["intraday_state"].astype(str)
        + " | "
        + out["moisture_cloud_regime"].astype(str)
    )
    return out


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
