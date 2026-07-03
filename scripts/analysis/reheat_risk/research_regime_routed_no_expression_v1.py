#!/usr/bin/env python3
"""Regime-routed NO expression test.

This tests the user's proposed routing:
- open/marginal runway -> buy current-bracket NO near noon;
- forecast-capped -> buy a higher-temperature NO near noon.

It also records a PIT-boundary audit for the regime labels.  This is a
research replay, not a live rule.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[3]
ATLAS_ROWS = ROOT / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_expression_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_VARIANTS = OUT_DIR / "variant_summary.csv"
OUT_DAILY = OUT_DIR / "daily_variant_summary.csv"
OUT_DETAILS = OUT_DIR / "selected_trade_details.csv"
OUT_ROUTE = OUT_DIR / "route_leg_summary.csv"
OUT_CITY = OUT_DIR / "city_summary_main_candidate.csv"
OUT_RECENT = OUT_DIR / "recent_model_predictions.csv"
OUT_SOFT = OUT_DIR / "soft_weight_summary.csv"
OUT_SOFT_DAILY = OUT_DIR / "soft_weight_daily_summary.csv"
OUT_AUDIT = OUT_DIR / "pit_regime_feature_audit.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-25-regime-routed-no-expression-v1.md"
RECENT_FEATURE_ROWS = [
    ROOT / "docs/analysis/2026-06/generated/current_bracket_no_20260624_feature_factory/reheat_feature_rows.csv",
]

STAKE_USD = 5.0
ASK_MIN = 0.10
ASK_MAX = 0.35
DECISION_HOURS = {10, 11, 12, 13, 14}
ASK_CAPS = {
    "strict35": 0.35,
    "relaxed50": 0.50,
    "relaxed70": 0.70,
}
SELECTORS = ("near_noon", "first_eligible", "best_ask")
BASELINE_VARIANT = "baseline_current_no_strict35_near_noon"
OPTIMISTIC_MAIN_CANDIDATE = "routed_capped_d1_no_relaxed50_best_ask"
MAIN_CANDIDATE = "routed_capped_d1_no_relaxed50_first_eligible"
OPTIMISTIC_BALANCED_SOFT_CANDIDATE = "routed_capped_d2_no_relaxed70_best_ask"
BALANCED_SOFT_CANDIDATE = "routed_capped_d2_no_relaxed70_first_eligible"
BALANCED_SOFT_POLICY = "soft_balanced"
RECENT_START = "2026-06-21"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"{100.0 * val:+.1f}%"


def money(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"${val:+,.2f}"


def finite_or_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: finite_or_none(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite_or_none(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    return value


def load_states() -> pd.DataFrame:
    df = pd.read_csv(ATLAS_ROWS, low_memory=False)
    df["target_date"] = df["target_date"].astype(str)
    df["decision_hour_local"] = pd.to_numeric(df["decision_hour_local"], errors="coerce")
    return df


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


def date_bootstrap_roi(selected: pd.DataFrame, n: int = 5000, seed: int = 20260625) -> tuple[float | None, float | None]:
    clean = selected[selected["payoff"].notna()].copy()
    dates = sorted(clean["target_date"].astype(str).unique().tolist())
    if len(dates) < 3:
        return None, None
    daily = {
        date: (
            float(group["stake_cost_usd"].sum()),
            float(group["stake_profit_usd"].sum()),
        )
        for date, group in clean.groupby("target_date")
    }
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n):
        draw = rng.choice(dates, size=len(dates), replace=True)
        cost = sum(daily[str(d)][0] for d in draw)
        profit = sum(daily[str(d)][1] for d in draw)
        if cost:
            vals.append(profit / cost)
    if not vals:
        return None, None
    return float(np.quantile(vals, 0.025)), float(np.quantile(vals, 0.975))


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
    # Live receives only the current snapshot's candidates, not the full target
    # day's eventual cross-section. Keep replay sizing row-local so replay and
    # live share the same information boundary.
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


def weighted_bootstrap_roi(selected: pd.DataFrame, weight_col: str, n: int = 5000, seed: int = 20260625) -> tuple[float | None, float | None]:
    clean = selected[selected["payoff"].notna()].copy()
    dates = sorted(clean["target_date"].astype(str).unique().tolist())
    if len(dates) < 3 or weight_col not in clean:
        return None, None
    clean["weighted_cost_usd"] = clean["stake_cost_usd"] * pd.to_numeric(clean[weight_col], errors="coerce").fillna(1.0)
    clean["weighted_profit_usd"] = clean["stake_profit_usd"] * pd.to_numeric(clean[weight_col], errors="coerce").fillna(1.0)
    daily = clean.groupby("target_date").agg(cost=("weighted_cost_usd", "sum"), profit=("weighted_profit_usd", "sum"))
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n):
        draw = rng.choice(dates, size=len(dates), replace=True)
        cost = float(daily.loc[draw, "cost"].sum())
        profit = float(daily.loc[draw, "profit"].sum())
        if cost:
            vals.append(profit / cost)
    if not vals:
        return None, None
    return float(np.quantile(vals, 0.025)), float(np.quantile(vals, 0.975))


def weighted_daily_summary(variant: str, selected: pd.DataFrame, weight_col: str) -> pd.DataFrame:
    if selected.empty:
        return pd.DataFrame()
    frame = add_soft_weights(selected)
    weights = pd.to_numeric(frame[weight_col], errors="coerce").fillna(1.0)
    frame["weighted_cost_usd"] = frame["stake_cost_usd"] * weights
    frame["weighted_profit_usd"] = frame["stake_profit_usd"] * weights
    rows = []
    for date, group in frame.groupby("target_date"):
        cost = float(group["weighted_cost_usd"].sum())
        profit = float(group["weighted_profit_usd"].sum())
        rows.append(
            {
                "target_date": date,
                "variant": variant,
                "weight_policy": weight_col,
                "trades": int(len(group)),
                "cities": int(group["city"].nunique()),
                "wins": int(group["payoff"].sum()),
                "win_rate": float(group["payoff"].mean()),
                "weighted_cost_usd": cost,
                "weighted_profit_usd": profit,
                "weighted_roi": profit / cost if cost else np.nan,
                "avg_weight": float(weights.loc[group.index].mean()),
                "day_risk": float(group["day_risk"].mean()),
                "regime_mix": ",".join(f"{k}:{v}" for k, v in group["day_regime"].value_counts().sort_index().items()),
                "loss_cities": ",".join(group.loc[group["payoff"].eq(0), "city"].astype(str).sort_values().tolist()),
            }
        )
    return pd.DataFrame(rows).sort_values(["target_date", "variant", "weight_policy"]).reset_index(drop=True)


def summarize_soft_policy(variant: str, selected: pd.DataFrame, weight_col: str) -> dict[str, Any]:
    frame = add_soft_weights(selected)
    if frame.empty:
        return {"variant": variant, "weight_policy": weight_col, "trades": 0}
    weights = pd.to_numeric(frame[weight_col], errors="coerce").fillna(1.0)
    weighted_cost = float((frame["stake_cost_usd"] * weights).sum())
    weighted_profit = float((frame["stake_profit_usd"] * weights).sum())
    daily = weighted_daily_summary(variant, frame, weight_col)
    ci_low, ci_high = weighted_bootstrap_roi(frame, weight_col)
    return {
        "variant": variant,
        "weight_policy": weight_col,
        "trades": int(len(frame)),
        "active_dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "wins": int(frame["payoff"].sum()),
        "win_rate": float(frame["payoff"].mean()),
        "avg_ask": float(frame["ask"].mean()),
        "avg_weight": float(weights.mean()),
        "notional_retained": weighted_cost / float(frame["stake_cost_usd"].sum()) if len(frame) else None,
        "weighted_cost_usd": weighted_cost,
        "weighted_profit_usd": weighted_profit,
        "weighted_roi": weighted_profit / weighted_cost if weighted_cost else None,
        "weighted_roi_ci_low": ci_low,
        "weighted_roi_ci_high": ci_high,
        "roi_le_minus50_days": int(daily["weighted_roi"].le(-0.50).sum()) if not daily.empty else 0,
        "roi_eq_minus100_days": int(daily["weighted_roi"].le(-0.999999).sum()) if not daily.empty else 0,
        "loss_ge_10usd_days": int(daily["weighted_profit_usd"].le(-10.0).sum()) if not daily.empty else 0,
        "worst_day_profit_usd": float(daily["weighted_profit_usd"].min()) if not daily.empty else None,
        "worst_day_roi": float(daily["weighted_roi"].min()) if not daily.empty else None,
    }


def summarize_variant(name: str, selected: pd.DataFrame, baseline: pd.DataFrame | None = None) -> dict[str, Any]:
    if selected.empty:
        return {
            "variant": name,
            "selected_trades": 0,
            "active_dates": 0,
            "cities": 0,
            "wins": 0,
            "win_rate": None,
            "cost_usd": 0.0,
            "profit_usd": 0.0,
            "roi": None,
        }
    cost = float(selected["stake_cost_usd"].sum())
    profit = float(selected["stake_profit_usd"].sum())
    row = {
        "variant": name,
        "selected_trades": int(len(selected)),
        "active_dates": int(selected["target_date"].nunique()),
        "cities": int(selected["city"].nunique()),
        "wins": int(selected["payoff"].sum()),
        "win_rate": float(selected["payoff"].mean()),
        "avg_ask": float(selected["ask"].mean()),
        "cost_usd": cost,
        "profit_usd": profit,
        "roi": profit / cost if cost else None,
        "roi_ci_low": None,
        "roi_ci_high": None,
        "open_runway_trades": int(selected["day_regime"].eq("day_open_runway").sum()),
        "marginal_runway_trades": int(selected["day_regime"].eq("day_marginal_runway").sum()),
        "forecast_capped_trades": int(selected["day_regime"].eq("day_forecast_capped").sum()),
        "roi_le_minus50_days": int(daily_summary(name, selected)["roi"].le(-0.50).sum()),
        "roi_eq_minus100_days": int(daily_summary(name, selected)["roi"].le(-0.999999).sum()),
    }
    row["roi_ci_low"], row["roi_ci_high"] = date_bootstrap_roi(selected)
    if baseline is not None and not baseline.empty:
        bcost = float(baseline["stake_cost_usd"].sum())
        bprofit = float(baseline["stake_profit_usd"].sum())
        row["baseline_roi"] = bprofit / bcost if bcost else None
        row["excess_roi_vs_baseline"] = row["roi"] - row["baseline_roi"] if row["roi"] is not None else None
    return row


def summarize_route_legs(variant: str, selected: pd.DataFrame) -> pd.DataFrame:
    if selected.empty:
        return pd.DataFrame()
    rows = []
    for keys, group in selected.groupby(["route_leg", "day_regime", "expression"], dropna=False):
        cost = float(group["stake_cost_usd"].sum())
        profit = float(group["stake_profit_usd"].sum())
        rows.append(
            {
                "variant": variant,
                "route_leg": keys[0],
                "day_regime": keys[1],
                "expression": keys[2],
                "trades": int(len(group)),
                "active_dates": int(group["target_date"].nunique()),
                "cities": int(group["city"].nunique()),
                "wins": int(group["payoff"].sum()),
                "win_rate": float(group["payoff"].mean()),
                "avg_ask": float(group["ask"].mean()),
                "profit_usd": profit,
                "cost_usd": cost,
                "roi": profit / cost if cost else np.nan,
            }
        )
    return pd.DataFrame(rows).sort_values(["variant", "route_leg", "day_regime"]).reset_index(drop=True)


def summarize_cities(variant: str, selected: pd.DataFrame) -> pd.DataFrame:
    if selected.empty:
        return pd.DataFrame()
    rows = []
    for city, group in selected.groupby("city"):
        cost = float(group["stake_cost_usd"].sum())
        profit = float(group["stake_profit_usd"].sum())
        rows.append(
            {
                "variant": variant,
                "city": city,
                "trades": int(len(group)),
                "active_dates": int(group["target_date"].nunique()),
                "wins": int(group["payoff"].sum()),
                "win_rate": float(group["payoff"].mean()),
                "avg_ask": float(group["ask"].mean()),
                "profit_usd": profit,
                "cost_usd": cost,
                "roi": profit / cost if cost else np.nan,
                "route_mix": ",".join(f"{k}:{v}" for k, v in group["route_leg"].value_counts().sort_index().items()),
                "loss_dates": ",".join(group.loc[group["payoff"].eq(0), "target_date"].astype(str).sort_values().tolist()),
            }
        )
    return pd.DataFrame(rows).sort_values(["profit_usd", "trades"], ascending=[True, False]).reset_index(drop=True)


def recent_predictions(states: pd.DataFrame) -> pd.DataFrame:
    selected = variant_frame(states, MAIN_CANDIDATE, ASK_CAPS["relaxed50"], "first_eligible", require_payoff=False)
    if selected.empty:
        return selected
    keep = [
        "target_date",
        "city",
        "decision_hour_local",
        "route_leg",
        "day_regime",
        "expression",
        "ask",
        "ask_size",
        "ask_notional",
        "payoff",
        "stake_profit_usd",
        "current_bracket",
        "d1_no_bracket",
        "d2_no_bracket",
        "current_native",
        "running_native",
        "forecast_source",
        "forecast_max_native",
        "forecast_peak_hour_local",
        "intraday_state",
        "moisture_cloud_regime",
        "wind_regime",
        "running_max_state",
        "city_family",
        "decision_snapshot_ts_utc",
    ]
    out = selected[selected["target_date"].astype(str).ge(RECENT_START)].copy()
    out = add_recent_market_metadata(out)
    out = add_gamma_settlement_check(out)
    out["resolved_payoff"] = out["payoff"]
    out.loc[out["resolved_payoff"].isna(), "resolved_payoff"] = out.loc[
        out["resolved_payoff"].isna(), "gamma_no_payoff"
    ]
    out["resolved_profit_usd"] = np.where(
        out["resolved_payoff"].notna(),
        out["resolved_payoff"] * (STAKE_USD / out["ask"]) - STAKE_USD,
        np.nan,
    )
    out["settlement_known"] = out["resolved_payoff"].notna()
    out["model_action"] = np.where(
        out["route_leg"].eq("capped_d1_no"),
        "buy d1 NO",
        "buy current-bracket NO",
    )
    extra = [
        "settlement_known",
        "resolved_payoff",
        "resolved_profit_usd",
        "gamma_check_status",
        "gamma_closed",
        "gamma_yes_price",
        "gamma_no_price",
        "condition_id",
        "market_id",
        "token_id",
        "model_action",
    ]
    return out[[c for c in keep + extra if c in out.columns]].sort_values(["target_date", "city", "decision_hour_local"])


def load_recent_feature_rows() -> pd.DataFrame:
    frames = []
    for path in RECENT_FEATURE_ROWS:
        if path.exists():
            frames.append(pd.read_csv(path, low_memory=False))
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out["target_date"] = out["target_date"].astype(str)
    out["decision_hour_local"] = pd.to_numeric(out["decision_hour_local"], errors="coerce")
    return out


def add_recent_market_metadata(recent: pd.DataFrame) -> pd.DataFrame:
    out = recent.copy()
    for col in ["condition_id", "market_id", "token_id"]:
        out[col] = pd.Series([None] * len(out), index=out.index, dtype="object")
    features = load_recent_feature_rows()
    if features.empty or out.empty:
        return out
    no_rows = features[features["outcome"].astype(str).str.lower().eq("no")].copy()
    for idx, row in out[out["target_date"].astype(str).ge("2026-06-24")].iterrows():
        bracket_col = "d1_no_bracket" if row.get("expression") == "d1_no" else "current_bracket"
        bracket = str(row.get(bracket_col) or "")
        match = no_rows[
            no_rows["city"].astype(str).eq(str(row.get("city")))
            & no_rows["target_date"].astype(str).eq(str(row.get("target_date")))
            & pd.to_numeric(no_rows["decision_hour_local"], errors="coerce").eq(float(row.get("decision_hour_local")))
            & no_rows["bracket"].astype(str).eq(bracket)
        ].head(1)
        if match.empty:
            continue
        for col in ["condition_id", "market_id", "token_id"]:
            out.loc[idx, col] = match.iloc[0].get(col)
    return out


def fetch_gamma_market(market_id: Any) -> dict[str, Any] | None:
    if pd.isna(market_id):
        return None
    url = f"https://gamma-api.polymarket.com/markets/{int(float(market_id))}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception:
        return None


def add_gamma_settlement_check(recent: pd.DataFrame) -> pd.DataFrame:
    out = recent.copy()
    out["gamma_check_status"] = pd.Series([None] * len(out), index=out.index, dtype="object")
    out["gamma_closed"] = pd.Series([None] * len(out), index=out.index, dtype="object")
    for col in ["gamma_yes_price", "gamma_no_price", "gamma_no_payoff"]:
        out[col] = np.nan
    for idx, row in out[out["payoff"].isna() & out["market_id"].notna()].iterrows():
        data = fetch_gamma_market(row.get("market_id"))
        if not data:
            out.loc[idx, "gamma_check_status"] = "fetch_failed"
            continue
        out.loc[idx, "gamma_closed"] = bool(data.get("closed"))
        prices_raw = data.get("outcomePrices")
        try:
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
            yes_price = float(prices[0])
            no_price = float(prices[1])
        except Exception:
            out.loc[idx, "gamma_check_status"] = "price_parse_failed"
            continue
        out.loc[idx, "gamma_yes_price"] = yes_price
        out.loc[idx, "gamma_no_price"] = no_price
        if bool(data.get("closed")) and (yes_price >= 0.999 or no_price >= 0.999):
            out.loc[idx, "gamma_check_status"] = "closed_binary"
            out.loc[idx, "gamma_no_payoff"] = 1.0 if no_price >= 0.999 else 0.0
        else:
            out.loc[idx, "gamma_check_status"] = "not_binary_closed"
    return out


def daily_summary(variant: str, selected: pd.DataFrame) -> pd.DataFrame:
    if selected.empty:
        return pd.DataFrame()
    rows = []
    for date, group in selected.groupby("target_date"):
        cost = float(group["stake_cost_usd"].sum())
        profit = float(group["stake_profit_usd"].sum())
        rows.append(
            {
                "target_date": date,
                "variant": variant,
                "trades": int(len(group)),
                "cities": int(group["city"].nunique()),
                "wins": int(group["payoff"].sum()),
                "win_rate": float(group["payoff"].mean()),
                "profit_usd": profit,
                "cost_usd": cost,
                "roi": profit / cost if cost else np.nan,
                "regime_mix": ",".join(f"{k}:{v}" for k, v in group["day_regime"].value_counts().sort_index().items()),
                "loss_cities": ",".join(group.loc[group["payoff"].eq(0), "city"].astype(str).sort_values().tolist()),
            }
        )
    return pd.DataFrame(rows).sort_values(["target_date", "variant"]).reset_index(drop=True)


def pit_feature_audit() -> pd.DataFrame:
    rows = [
        {
            "regime_label": "day_regime",
            "live_inputs": "forecast_max_native,running_native,unit",
            "feature_timing": "forecast chosen as-of decision snapshot; running max to decision hour only",
            "uses_future_observation": False,
            "uses_settlement": False,
            "notes": "implemented via forecast_gap_to_running_native; safe if forecast source is PIT",
        },
        {
            "regime_label": "intraday_state",
            "live_inputs": "temp_trend_1h_f,temp_trend_3h_f,decline_native,minutes_since_running_max,decision_hour_local,unit",
            "feature_timing": "IEM/METAR as-of decision snapshot with 90 minute tolerance; trend looks backward 1h/3h",
            "uses_future_observation": False,
            "uses_settlement": False,
            "notes": "safe for live only after same as-of observation cache is available before the decision",
        },
        {
            "regime_label": "moisture_cloud_regime",
            "live_inputs": "relative_humidity_pct,sky_cover_code,dewpoint_depression_f",
            "feature_timing": "IEM/METAR as-of decision snapshot with 90 minute tolerance",
            "uses_future_observation": False,
            "uses_settlement": False,
            "notes": "safe if live observation feed has these fields; otherwise must be missing/unknown, not backfilled",
        },
        {
            "regime_label": "wind_regime",
            "live_inputs": "wind_speed_kt",
            "feature_timing": "IEM/METAR as-of decision snapshot with 90 minute tolerance",
            "uses_future_observation": False,
            "uses_settlement": False,
            "notes": "safe if live observation feed has wind before the decision",
        },
        {
            "regime_label": "running_max_state",
            "live_inputs": "minutes_since_running_max,decline_native,unit",
            "feature_timing": "current/running max only from observations up to decision snapshot",
            "uses_future_observation": False,
            "uses_settlement": False,
            "notes": "safe; depends on observation freshness and cadence",
        },
        {
            "regime_label": "realized_context",
            "live_inputs": "final_max_native,final_winning_bracket,current_bracket_held,d1_hit,d2_hit,target_hit",
            "feature_timing": "known only after target date settlement/final observations",
            "uses_future_observation": True,
            "uses_settlement": True,
            "notes": "not allowed for live regime identification; payoff/calibration only",
        },
    ]
    return pd.DataFrame(rows)


def markdown_table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
    shown = df if limit is None else df.head(limit)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in shown.iterrows():
        vals = []
        for col in cols:
            val = row.get(col)
            if (
                col in {
                    "roi",
                    "weighted_roi",
                    "worst_day_roi",
                    "baseline_roi",
                    "excess_roi_vs_baseline",
                    "roi_ci_low",
                    "roi_ci_high",
                    "weighted_roi_ci_low",
                    "weighted_roi_ci_high",
                    "notional_retained",
                }
                or col.endswith("rate")
            ):
                vals.append(pct(val))
            elif col.endswith("usd"):
                vals.append(money(val))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}" if math.isfinite(val) else "NA")
            else:
                vals.append("" if pd.isna(val) else str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def render_md(
    payload: dict[str, Any],
    variants: pd.DataFrame,
    daily: pd.DataFrame,
    route: pd.DataFrame,
    cities: pd.DataFrame,
    recent: pd.DataFrame,
    soft: pd.DataFrame,
    soft_daily: pd.DataFrame,
    audit: pd.DataFrame,
) -> str:
    main_daily = daily[daily["variant"].eq(MAIN_CANDIDATE)].copy() if not daily.empty else daily
    worst = main_daily.sort_values("roi").head(12) if not main_daily.empty else main_daily
    recent_daily = (
        recent.groupby(["target_date", "settlement_known"], as_index=False)
        .agg(
            predicted_trades=("city", "size"),
            cities=("city", "nunique"),
            avg_ask=("ask", "mean"),
            known_wins=("resolved_payoff", "sum"),
            known_profit_usd=("resolved_profit_usd", "sum"),
        )
        if not recent.empty
        else pd.DataFrame()
    )
    balanced_daily = (
        soft_daily[
            soft_daily["variant"].eq(BALANCED_SOFT_CANDIDATE)
            & soft_daily["weight_policy"].eq(BALANCED_SOFT_POLICY)
        ].copy()
        if not soft_daily.empty
        else soft_daily
    )
    balanced_worst = balanced_daily.sort_values("weighted_profit_usd").head(12) if not balanced_daily.empty else balanced_daily
    balanced_records = payload.get("balanced_soft_candidate") or []
    if balanced_records:
        balanced = balanced_records[0]
        balanced_trades = balanced.get("selected_trades", balanced.get("trades", 0))
        balanced_line = (
            "raw 候选里 `routed_capped_d1_no_relaxed50_best_ask` 的 ROI 最高但日内 tail 偏薄；"
            f"当前更平衡的 shadow 候选是 `{BALANCED_SOFT_CANDIDATE} + {BALANCED_SOFT_POLICY}`："
            f"保留 {int(balanced_trades)} 笔 / "
            f"{int(balanced.get('active_dates', 0))} 天 / "
            f"{int(balanced.get('cities', 0))} 城，"
            f"胜率 {pct(balanced.get('win_rate'))}，"
            f"weighted ROI {pct(balanced.get('weighted_roi'))}，"
            f"date-block CI [{pct(balanced.get('weighted_roi_ci_low'))}, {pct(balanced.get('weighted_roi_ci_high'))}]；"
            "仍是 shadow 候选，不是 live 规则。"
        )
    else:
        balanced_line = (
            f"当前更平衡的 shadow 候选是 `{BALANCED_SOFT_CANDIDATE} + {BALANCED_SOFT_POLICY}`，"
            "但本次没有生成可用 summary；不是 live 规则。"
        )
    return "\n".join(
        [
            "# Regime-Routed NO Expression V1",
            "",
            "## 结论",
            "",
            "把 regime 当成表达式路由器这个方向可以测，但不能只看 strict 口径。这里并排比较 A strict、B relaxed ask cap、C first-eligible live-like timing、D best-ask optimistic timing；主结论只看 first-eligible，best-ask 仅作上界对照。本报告把已结算 ROI 和最近模型候选分开，避免未结算日期被静默过滤。",
            "",
            f"Verdict: `{payload['verdict']['status']}`，live_ready=`{payload['verdict']['live_ready']}`。",
            "",
            balanced_line,
            "",
            "## Variant Summary",
            "",
            markdown_table(
                variants,
                [
                    "variant",
                    "selected_trades",
                    "active_dates",
                    "cities",
                    "win_rate",
                    "avg_ask",
                    "profit_usd",
                    "roi",
                    "roi_ci_low",
                    "roi_ci_high",
                    "baseline_roi",
                    "excess_roi_vs_baseline",
                    "roi_le_minus50_days",
                    "roi_eq_minus100_days",
                    "open_runway_trades",
                    "marginal_runway_trades",
                    "forecast_capped_trades",
                ],
            ),
            "",
            "## Main Candidate Daily",
            "",
            markdown_table(
                main_daily,
                ["target_date", "trades", "cities", "wins", "win_rate", "profit_usd", "roi", "regime_mix", "loss_cities"],
            ),
            "",
            "## Main Candidate Worst Days",
            "",
            markdown_table(
                worst,
                ["target_date", "trades", "wins", "win_rate", "profit_usd", "roi", "regime_mix", "loss_cities"],
            ),
            "",
            "## Route-Leg Breakdown",
            "",
            markdown_table(
                route,
                ["route_leg", "day_regime", "expression", "trades", "active_dates", "cities", "win_rate", "avg_ask", "profit_usd", "roi"],
            ),
            "",
            "## Soft Weight Overlay",
            "",
            "soft weight 只改 notional，不筛单。固定机制权重：`route_multiplier × price_multiplier × weather_multiplier × day_multiplier`；`day_multiplier` 已改为 row-local/live-like，不使用当天完整截面、payoff、final max 或 settlement 训练。",
            "",
            markdown_table(
                soft,
                [
                    "variant",
                    "weight_policy",
                    "trades",
                    "active_dates",
                    "cities",
                    "win_rate",
                    "avg_ask",
                    "notional_retained",
                    "weighted_profit_usd",
                    "weighted_roi",
                    "weighted_roi_ci_low",
                    "weighted_roi_ci_high",
                    "roi_le_minus50_days",
                    "roi_eq_minus100_days",
                    "loss_ge_10usd_days",
                    "worst_day_profit_usd",
                ],
            ),
            "",
            "## Balanced Candidate Daily",
            "",
            markdown_table(
                balanced_daily,
                [
                    "target_date",
                    "trades",
                    "cities",
                    "wins",
                    "win_rate",
                    "avg_weight",
                    "weighted_profit_usd",
                    "weighted_roi",
                    "day_risk",
                    "regime_mix",
                    "loss_cities",
                ],
            ),
            "",
            "## Balanced Candidate Worst PnL Days",
            "",
            markdown_table(
                balanced_worst,
                [
                    "target_date",
                    "trades",
                    "wins",
                    "win_rate",
                    "avg_weight",
                    "weighted_profit_usd",
                    "weighted_roi",
                    "regime_mix",
                    "loss_cities",
                ],
            ),
            "",
            "## City Distribution",
            "",
            markdown_table(
                cities,
                ["city", "trades", "active_dates", "wins", "win_rate", "avg_ask", "profit_usd", "roi", "route_mix", "loss_dates"],
                limit=36,
            ),
            "",
            "## Recent Model Predictions",
            "",
            markdown_table(
                recent_daily,
                ["target_date", "settlement_known", "predicted_trades", "cities", "avg_ask", "known_wins", "known_profit_usd"],
            ),
            "",
            markdown_table(
                recent,
                [
                    "target_date",
                    "city",
                    "decision_hour_local",
                    "model_action",
                    "day_regime",
                    "expression",
                    "ask",
                    "settlement_known",
                    "resolved_payoff",
                    "resolved_profit_usd",
                    "gamma_check_status",
                    "current_bracket",
                    "d1_no_bracket",
                    "forecast_max_native",
                    "running_native",
                    "intraday_state",
                    "city_family",
                ],
                limit=80,
            ),
            "",
            "## PIT Boundary Audit",
            "",
            markdown_table(
                audit,
                ["regime_label", "live_inputs", "feature_timing", "uses_future_observation", "uses_settlement", "notes"],
            ),
            "",
            "## Interpretation",
            "",
            "1. `day_open_runway/day_marginal_runway` 的 current-bracket NO 是 PIT 可识别的表达式，不需要知道最终最高温。",
            "2. `day_forecast_capped` 买 higher NO 也可以 PIT 识别，但不能用当天之后的 final max 或 settlement 来挑 d1/d2；只能用当时盘口和 forecast ceiling margin。",
            "3. strict 口径样本少主要来自 ask band 和 capacity；relaxed 口径用于判断信号容量，不代表已经可以下真钱。",
            "4. `first_eligible` 是 live-like 主口径；`best_ask` 虽不用 payoff，但仍是日内后视上界，只能辅助判断 fixed near-noon 是否错过入场，不可作为 live 证据。",
            "5. 当前 atlas label 本身没有用 future/settlement；future 字段只在 payoff/calibration 层。代码已把 `add_pit_context` 和 `add_realized_context` 拆开，降低误用风险。",
            "6. live 里真正难点不是 regime 当天识别不到，而是观测 feed 延迟/缺字段时必须让 label 变 `unknown`，不能用当天事后补齐的 IEM cache 假装当时可见。",
            "7. 6/25 本轮没有生成可用 observed-state rows；不能报模型候选。等 live observed feed 落表后再跑同一脚本即可进入 recent predictions。",
            "",
            "## Files",
            "",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
            f"- Variant summary: `{OUT_VARIANTS.relative_to(ROOT)}`",
            f"- Daily summary: `{OUT_DAILY.relative_to(ROOT)}`",
            f"- Selected details: `{OUT_DETAILS.relative_to(ROOT)}`",
            f"- Route-leg summary: `{OUT_ROUTE.relative_to(ROOT)}`",
            f"- City summary: `{OUT_CITY.relative_to(ROOT)}`",
            f"- Recent predictions: `{OUT_RECENT.relative_to(ROOT)}`",
            f"- Soft weight summary: `{OUT_SOFT.relative_to(ROOT)}`",
            f"- Soft daily summary: `{OUT_SOFT_DAILY.relative_to(ROOT)}`",
            f"- PIT audit: `{OUT_AUDIT.relative_to(ROOT)}`",
            "",
        ]
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    states = load_states()
    settled_states = states[states["current_bracket_held"].notna()].copy()

    selected_by_variant = {
        BASELINE_VARIANT: variant_frame(settled_states, BASELINE_VARIANT, ASK_CAPS["strict35"], "near_noon")
    }
    for ask_label, ask_max in ASK_CAPS.items():
        for selector in SELECTORS:
            for route in ["routed_capped_d1_no", "routed_capped_d2_no"]:
                variant = f"{route}_{ask_label}_{selector}"
                selected_by_variant[variant] = variant_frame(settled_states, variant, ask_max, selector)

    baseline = selected_by_variant[BASELINE_VARIANT]
    variant_rows = []
    daily_frames = []
    details = []
    for variant, selected in selected_by_variant.items():
        variant_rows.append(summarize_variant(variant, selected, baseline if variant != BASELINE_VARIANT else None))
        daily_frames.append(daily_summary(variant, selected))
        if not selected.empty:
            details.append(selected.assign(variant=variant))

    variants = pd.DataFrame(variant_rows)
    daily = pd.concat(daily_frames, ignore_index=True) if daily_frames else pd.DataFrame()
    detail = pd.concat(details, ignore_index=True) if details else pd.DataFrame()
    main_selected = selected_by_variant.get(MAIN_CANDIDATE, pd.DataFrame())
    route = summarize_route_legs(MAIN_CANDIDATE, main_selected)
    cities = summarize_cities(MAIN_CANDIDATE, main_selected)
    recent = recent_predictions(states)
    soft_rows = []
    soft_daily_frames = []
    soft_variant_names = [
        "routed_capped_d1_no_relaxed50_first_eligible",
        "routed_capped_d2_no_relaxed50_first_eligible",
        "routed_capped_d1_no_relaxed70_first_eligible",
        "routed_capped_d2_no_relaxed70_first_eligible",
        "routed_capped_d1_no_relaxed50_best_ask",
        "routed_capped_d2_no_relaxed50_best_ask",
        "routed_capped_d1_no_relaxed70_best_ask",
        "routed_capped_d2_no_relaxed70_best_ask",
    ]
    for variant in soft_variant_names:
        selected = selected_by_variant.get(variant, pd.DataFrame())
        if selected.empty:
            continue
        for weight_policy in ["full_size", "soft_moderate", "soft_balanced"]:
            soft_rows.append(summarize_soft_policy(variant, selected, weight_policy))
            soft_daily_frames.append(weighted_daily_summary(variant, selected, weight_policy))
    soft = pd.DataFrame(soft_rows)
    soft_daily = pd.concat(soft_daily_frames, ignore_index=True) if soft_daily_frames else pd.DataFrame()
    audit = pit_feature_audit()

    variants.to_csv(OUT_VARIANTS, index=False)
    daily.to_csv(OUT_DAILY, index=False)
    detail.to_csv(OUT_DETAILS, index=False)
    route.to_csv(OUT_ROUTE, index=False)
    cities.to_csv(OUT_CITY, index=False)
    recent.to_csv(OUT_RECENT, index=False)
    soft.to_csv(OUT_SOFT, index=False)
    soft_daily.to_csv(OUT_SOFT_DAILY, index=False)
    audit.to_csv(OUT_AUDIT, index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "strategy": "regime_routed_no_expression_v1",
        "data_snapshot": {
            "atlas_state_rows": int(len(states)),
            "date_min": str(states["target_date"].min()) if len(states) else None,
            "date_max": str(states["target_date"].max()) if len(states) else None,
            "settled_state_rows": int(len(settled_states)),
            "settled_date_min": str(settled_states["target_date"].min()) if len(settled_states) else None,
            "settled_date_max": str(settled_states["target_date"].max()) if len(settled_states) else None,
            "cities": int(states["city"].nunique()) if len(states) else 0,
            "decision_hours": sorted(DECISION_HOURS),
            "ask_min": ASK_MIN,
            "ask_caps": ASK_CAPS,
            "selectors": list(SELECTORS),
            "baseline_variant": BASELINE_VARIANT,
            "stake_usd": STAKE_USD,
            "selection": "one trade per city-date; first_eligible is live-like primary; best_ask is optimistic upper-bound audit",
            "recent_prediction_start": RECENT_START,
            "main_candidate": MAIN_CANDIDATE,
            "optimistic_main_candidate": OPTIMISTIC_MAIN_CANDIDATE,
            "latest_unsettled_atlas_date": str(states.loc[states["current_bracket_held"].isna(), "target_date"].max())
            if states["current_bracket_held"].isna().any()
            else None,
            "balanced_soft_candidate": BALANCED_SOFT_CANDIDATE,
            "optimistic_balanced_soft_candidate": OPTIMISTIC_BALANCED_SOFT_CANDIDATE,
            "balanced_soft_policy": BALANCED_SOFT_POLICY,
        },
        "pit_boundary": {
            "regime_labels_use_future_observation": False,
            "regime_labels_use_settlement": False,
            "realized_columns_payoff_only": True,
            "live_requirement": "observation and forecast values must be as-of decision; missing live fields must remain unknown, not same-day backfilled",
        },
        "variants": finite_or_none(variants.to_dict(orient="records")),
        "main_candidate_route_legs": finite_or_none(route.to_dict(orient="records")),
        "main_candidate_city_summary": finite_or_none(cities.to_dict(orient="records")),
        "soft_weight_overlay": finite_or_none(soft.to_dict(orient="records")),
        "balanced_soft_candidate": finite_or_none(
            soft[
                soft["variant"].eq(BALANCED_SOFT_CANDIDATE)
                & soft["weight_policy"].eq(BALANCED_SOFT_POLICY)
            ].to_dict(orient="records")
        )
        if not soft.empty
        else [],
        "recent_predictions": {
            "rows": int(len(recent)),
            "dates": sorted(recent["target_date"].astype(str).unique().tolist()) if not recent.empty else [],
            "settlement_known_rows": int(recent["settlement_known"].sum()) if not recent.empty else 0,
            "unsettled_rows": int((~recent["settlement_known"]).sum()) if not recent.empty else 0,
            "path": str(OUT_RECENT.relative_to(ROOT)),
        },
        "outputs": {
            "json": str(OUT_JSON.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
            "variant_summary": str(OUT_VARIANTS.relative_to(ROOT)),
            "daily_summary": str(OUT_DAILY.relative_to(ROOT)),
            "selected_details": str(OUT_DETAILS.relative_to(ROOT)),
            "route_leg_summary": str(OUT_ROUTE.relative_to(ROOT)),
            "city_summary": str(OUT_CITY.relative_to(ROOT)),
            "recent_predictions": str(OUT_RECENT.relative_to(ROOT)),
            "soft_weight_summary": str(OUT_SOFT.relative_to(ROOT)),
            "soft_weight_daily_summary": str(OUT_SOFT_DAILY.relative_to(ROOT)),
            "pit_audit": str(OUT_AUDIT.relative_to(ROOT)),
        },
        "verdict": {
            "status": "balanced_shadow_candidate_but_not_live_ready",
            "live_ready": False,
            "reason": "Live-like first-eligible soft-routed expression has positive historical date-block CI after the PIT fixes, but it has not accumulated enough frozen forward/live evidence for live.",
        },
    }
    OUT_JSON.write_text(json.dumps(finite_or_none(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(payload, variants, daily, route, cities, recent, soft, soft_daily, audit), encoding="utf-8")
    print(json.dumps({"verdict": payload["verdict"], "variants": payload["variants"]}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
