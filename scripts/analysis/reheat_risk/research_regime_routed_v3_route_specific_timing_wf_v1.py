#!/usr/bin/env python3
"""Route-specific entry timing and nested walk-forward for regime-routed v3.

This is a causal-timing research script: it builds all routeable hourly rows,
selects at most one row per city-date using pre-declared route-specific timing
rules, then evaluates candidate menus with nested walk-forward.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
ANALYSIS_DIR = ROOT / "scripts/analysis/reheat_risk"
if str(ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import research_regime_routed_expression_router_v3 as router_v3  # noqa: E402
import research_regime_routed_expression_router_v3_live_like_entry_v1 as live_entry_v1  # noqa: E402
import research_regime_routed_no_expression_v1 as expression_v1  # noqa: E402
import research_regime_routed_no_mechanism_split_v2 as mechanism_v2  # noqa: E402
from src.strategies.weather_edge_v1.tools.regime_routed_temperature_context import (  # noqa: E402
    temperature_context_multiplier,
)


OUT_DIR = ROOT / "docs/analysis/2026-06/generated/regime_routed_v3_route_specific_timing_wf_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_SELECTED = OUT_DIR / "selected_candidate_rows.csv"
OUT_STATIC = OUT_DIR / "candidate_static_summary.csv"
OUT_WF = OUT_DIR / "walk_forward_summary.csv"
OUT_DECISIONS = OUT_DIR / "walk_forward_decisions.csv"
OUT_RECENT = OUT_DIR / "recent_decisions.csv"
OUT_ATTRIBUTION = OUT_DIR / "frozen_failure_price_feature_attribution.csv"
OUT_PROMISING_DAILY = OUT_DIR / "promising_shadow_candidate_daily.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-29-regime-routed-v3-route-specific-timing-wf-v1.md"

FORWARD_START = "2026-06-21"
SETTLED_END = "2026-06-26"
STAKE_USD = 5.0
MIN_VAL_DATES = 5
MIN_VAL_ROWS = 20
TRAILING_DATES = 10

NOON_PRIORITY = {12: 0, 11: 1, 13: 2, 10: 3, 14: 4}
LATE_PRIORITY = {13: 0, 14: 1, 12: 2, 11: 3, 10: 4}
MIDDAY_PRIORITY = {12: 0, 13: 1, 11: 2, 14: 3, 10: 4}

ROUTE_SETS = {
    "all_v3": None,
    "core_no": {"fresh_runway_current_no", "capped_d2_no", "false_fade_reheat_current_no"},
    "no_pullback_yes": {
        "fresh_runway_current_no",
        "capped_d2_no",
        "false_fade_reheat_current_no",
        "cheap_stale_tail_current_no",
    },
    "fresh_capped": {"fresh_runway_current_no", "capped_d2_no"},
    "fresh_only": {"fresh_runway_current_no"},
    "capped_only": {"capped_d2_no"},
}

ENTRY_POLICIES = [
    "global_first_eligible",
    "global_fixed_noon_priority",
    "route_clock_physics_v1",
    "route_confirmed_momentum_v1",
    "route_price_disciplined_v1",
]

SIZING_POLICIES = ["full_size", "row_risk_soft_v1", "temp_context_row_soft_v1"]
PROMISING_SHADOW_CANDIDATE = "route_price_disciplined_v1__no_pullback_yes__row_risk_soft_v1"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def finite(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        val = float(value)
        return val if math.isfinite(val) else None
    return value


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
    return f"${val:+.2f}"


def num(frame: pd.DataFrame, col: str, default: float = np.nan) -> pd.Series:
    if col not in frame.columns:
        return pd.Series(default, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[col], errors="coerce")


def build_all_routed_candidates() -> pd.DataFrame:
    states = expression_v1.load_states()
    base = live_entry_v1.base_candidates(states, require_payoff=False)
    labeled = mechanism_v2.add_mechanism_labels(base)
    routed = router_v3.build_v3_details(labeled)
    routed["target_date"] = routed["target_date"].astype(str)
    routed["decision_hour_num"] = num(routed, "decision_hour_local")
    routed["peak_delta"] = num(routed, "forecast_peak_delta_hours_local")
    routed["minutes_since_high"] = num(routed, "minutes_since_running_max")
    routed["trend_1h"] = num(routed, "temp_trend_1h_f")
    routed["trend_3h"] = num(routed, "temp_trend_3h_f")
    routed["router_ask"] = num(routed, "router_ask")
    return routed.reset_index(drop=True)


def route_row_allowed(frame: pd.DataFrame, policy: str) -> pd.Series:
    route = frame["router_route"].astype(str)
    hour = num(frame, "decision_hour_local")
    peak_delta = num(frame, "forecast_peak_delta_hours_local")
    minutes_since = num(frame, "minutes_since_running_max")
    trend_1h = num(frame, "temp_trend_1h_f")
    trend_3h = num(frame, "temp_trend_3h_f")
    ask = num(frame, "router_ask")
    forecast_gap = num(frame, "forecast_gap_to_running_native")

    allowed = pd.Series(True, index=frame.index)
    if policy in {"global_first_eligible", "global_fixed_noon_priority"}:
        return allowed

    if policy == "route_clock_physics_v1":
        fresh = route.eq("fresh_runway_current_no")
        false = route.eq("false_fade_reheat_current_no")
        capped = route.eq("capped_d2_no")
        cheap = route.eq("cheap_stale_tail_current_no")
        pullback = route.eq("pullback_uncertain_current_high_yes")
        allowed.loc[fresh] = peak_delta.loc[fresh].le(0.0) & hour.loc[fresh].le(13)
        allowed.loc[false] = hour.loc[false].ge(12) & trend_1h.loc[false].ge(0.0)
        allowed.loc[capped] = hour.loc[capped].between(11, 14)
        allowed.loc[cheap] = hour.loc[cheap].ge(13) & ask.loc[cheap].le(0.40)
        allowed.loc[pullback] = hour.loc[pullback].ge(13)
        return allowed.fillna(False)

    if policy == "route_confirmed_momentum_v1":
        fresh = route.eq("fresh_runway_current_no")
        false = route.eq("false_fade_reheat_current_no")
        capped = route.eq("capped_d2_no")
        cheap = route.eq("cheap_stale_tail_current_no")
        pullback = route.eq("pullback_uncertain_current_high_yes")
        allowed.loc[fresh] = (
            peak_delta.loc[fresh].le(0.0)
            & minutes_since.loc[fresh].le(75)
            & trend_1h.loc[fresh].ge(0.5)
            & trend_3h.loc[fresh].ge(2.0)
        )
        allowed.loc[false] = hour.loc[false].ge(13) & trend_1h.loc[false].ge(0.0)
        allowed.loc[capped] = hour.loc[capped].between(12, 14) & forecast_gap.loc[capped].le(1.75)
        allowed.loc[cheap] = hour.loc[cheap].ge(13) & ask.loc[cheap].le(0.35) & minutes_since.loc[cheap].ge(90)
        allowed.loc[pullback] = hour.loc[pullback].ge(13) & trend_1h.loc[pullback].le(0.5)
        return allowed.fillna(False)

    if policy == "route_price_disciplined_v1":
        caps = {
            "fresh_runway_current_no": 0.55,
            "capped_d2_no": 0.62,
            "false_fade_reheat_current_no": 0.65,
            "cheap_stale_tail_current_no": 0.40,
            "pullback_uncertain_current_high_yes": 0.65,
        }
        cap = route.map(caps).astype(float)
        return ask.le(cap).fillna(False)

    raise ValueError(policy)


def entry_policy_rank(frame: pd.DataFrame, policy: str) -> pd.DataFrame:
    out = frame.copy()
    route = out["router_route"].astype(str)
    hour = num(out, "decision_hour_local")
    peak_delta = num(out, "forecast_peak_delta_hours_local")
    minutes_since = num(out, "minutes_since_running_max")
    trend_1h = num(out, "temp_trend_1h_f")

    if policy == "global_first_eligible":
        out["entry_rank_1"] = hour
        out["entry_rank_2"] = 0
        out["entry_rank_3"] = 0
        return out
    if policy == "global_fixed_noon_priority":
        out["entry_rank_1"] = hour.map(NOON_PRIORITY).fillna(99)
        out["entry_rank_2"] = hour
        out["entry_rank_3"] = 0
        return out

    out["entry_rank_1"] = 50.0
    out["entry_rank_2"] = 50.0
    out["entry_rank_3"] = hour.fillna(99).astype(float)

    fresh = route.eq("fresh_runway_current_no")
    capped = route.eq("capped_d2_no")
    false = route.eq("false_fade_reheat_current_no")
    cheap = route.eq("cheap_stale_tail_current_no")
    pullback = route.eq("pullback_uncertain_current_high_yes")

    if policy in {"route_clock_physics_v1", "route_confirmed_momentum_v1"}:
        out.loc[fresh, "entry_rank_1"] = np.select(
            [
                peak_delta.loc[fresh].le(-2.0),
                peak_delta.loc[fresh].le(0.0),
                peak_delta.loc[fresh].le(1.0),
            ],
            [0, 1, 3],
            default=5,
        )
        out.loc[fresh, "entry_rank_2"] = hour.loc[fresh]
        out.loc[capped, "entry_rank_1"] = 1
        out.loc[capped, "entry_rank_2"] = hour.loc[capped].map(MIDDAY_PRIORITY).fillna(99)
        out.loc[false, "entry_rank_1"] = 2
        out.loc[false, "entry_rank_2"] = hour.loc[false].map(LATE_PRIORITY).fillna(99)
        out.loc[cheap, "entry_rank_1"] = 4
        out.loc[cheap, "entry_rank_2"] = hour.loc[cheap].map(LATE_PRIORITY).fillna(99)
        out.loc[pullback, "entry_rank_1"] = 3
        out.loc[pullback, "entry_rank_2"] = hour.loc[pullback].map(LATE_PRIORITY).fillna(99)
        if policy == "route_confirmed_momentum_v1":
            out.loc[fresh, "entry_rank_3"] = -trend_1h.loc[fresh].fillna(-99)
            out.loc[false, "entry_rank_3"] = -trend_1h.loc[false].fillna(-99)
        return out

    if policy == "route_price_disciplined_v1":
        out["entry_rank_1"] = hour
        out["entry_rank_2"] = 0
        out["entry_rank_3"] = 0
        return out

    raise ValueError(policy)


def select_one_per_city_day(frame: pd.DataFrame, *, entry_policy: str, route_set: str) -> pd.DataFrame:
    allowed_routes = ROUTE_SETS[route_set]
    work = frame.copy()
    if allowed_routes is not None:
        work = work[work["router_route"].isin(allowed_routes)].copy()
    if work.empty:
        return work
    work = work[route_row_allowed(work, entry_policy)].copy()
    if work.empty:
        return work
    work = entry_policy_rank(work, entry_policy)
    selected = (
        work.sort_values(["target_date", "city", "entry_rank_1", "entry_rank_2", "entry_rank_3", "decision_hour_num"])
        .drop_duplicates(["target_date", "city"], keep="first")
        .reset_index(drop=True)
    )
    selected["entry_policy"] = entry_policy
    selected["route_set"] = route_set
    return selected


def add_row_sizing(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    ask = num(out, "router_ask")
    route = out["router_route"].astype(str)
    peak_delta = num(out, "forecast_peak_delta_hours_local")
    minutes_since = num(out, "minutes_since_running_max")
    trend_1h = num(out, "temp_trend_1h_f")
    wind = num(out, "wind_speed_kt")
    humidity = num(out, "relative_humidity_pct")

    route_mult = route.map(
        {
            "fresh_runway_current_no": 0.80,
            "capped_d2_no": 0.45,
            "false_fade_reheat_current_no": 0.65,
            "cheap_stale_tail_current_no": 0.30,
            "pullback_uncertain_current_high_yes": 0.25,
        }
    ).fillna(0.50)
    price_risk = ((ask - 0.45) / 0.25).clip(0, 1).fillna(0)
    price_mult = (1.0 - 0.55 * price_risk).clip(0.35, 1.0)
    peak_mult = pd.Series(1.0, index=out.index)
    current_no = route.isin(
        ["fresh_runway_current_no", "false_fade_reheat_current_no", "cheap_stale_tail_current_no"]
    )
    peak_mult.loc[current_no] = np.select(
        [
            peak_delta.loc[current_no].le(-2.0),
            peak_delta.loc[current_no].le(0.0),
            peak_delta.loc[current_no].le(1.0),
        ],
        [1.0, 0.80, 0.50],
        default=0.25,
    )
    fresh = route.eq("fresh_runway_current_no")
    freshness_mult = pd.Series(1.0, index=out.index)
    freshness_mult.loc[fresh] = np.select(
        [
            minutes_since.loc[fresh].le(45),
            minutes_since.loc[fresh].le(90),
        ],
        [1.0, 0.70],
        default=0.40,
    )
    momentum_mult = pd.Series(1.0, index=out.index)
    momentum_routes = route.isin(["fresh_runway_current_no", "false_fade_reheat_current_no"])
    momentum_mult.loc[momentum_routes] = np.select(
        [
            trend_1h.loc[momentum_routes].ge(0.5),
            trend_1h.loc[momentum_routes].ge(0.0),
        ],
        [1.0, 0.80],
        default=0.50,
    )
    weather_mult = (
        1.0
        - 0.10 * out["city_family"].astype(str).eq("humid_low_latitude").astype(float)
        - 0.08 * wind.ge(15).fillna(False).astype(float)
        - 0.06 * humidity.ge(70).fillna(False).astype(float)
        - 0.06 * out["moisture_cloud_regime"].astype(str).str.contains("convective|humid|cloud", case=False, na=False).astype(float)
    ).clip(0.65, 1.0)
    row_weight = (route_mult * price_mult * peak_mult * freshness_mult * momentum_mult * weather_mult).clip(0.05, 1.0)
    out["row_risk_soft_v1"] = row_weight

    temp_mult = out.apply(lambda row: temperature_context_multiplier(row.to_dict(), strength="light"), axis=1)
    out["temp_context_row_soft_v1"] = (out["row_risk_soft_v1"] * pd.to_numeric(temp_mult, errors="coerce").fillna(1.0)).clip(0.03, 1.0)
    out["full_size"] = 1.0
    return out


def build_candidate_menu(all_routed: pd.DataFrame) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for entry_policy in ENTRY_POLICIES:
        for route_set in ROUTE_SETS:
            selected = select_one_per_city_day(all_routed, entry_policy=entry_policy, route_set=route_set)
            if selected.empty:
                continue
            selected = add_row_sizing(selected)
            for sizing_policy in SIZING_POLICIES:
                out = selected.copy()
                weight = num(out, sizing_policy, default=1.0).fillna(1.0).clip(lower=0)
                out["sizing_policy"] = sizing_policy
                out["candidate_id"] = entry_policy + "__" + route_set + "__" + sizing_policy
                out["candidate_cost_usd"] = num(out, "router_cost_usd").fillna(STAKE_USD) * weight
                out["candidate_pnl_usd"] = num(out, "router_pnl_usd") * weight
                out["candidate_weight"] = weight
                frames.append(out)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def summarize_group(group: pd.DataFrame, **labels: Any) -> dict[str, Any]:
    clean = group[group["router_payoff"].notna()].copy()
    cost = float(clean["candidate_cost_usd"].sum())
    pnl = float(clean["candidate_pnl_usd"].sum())
    return {
        **labels,
        "rows": int(len(clean)),
        "dates": int(clean["target_date"].nunique()) if len(clean) else 0,
        "cities": int(clean["city"].nunique()) if len(clean) else 0,
        "wins": int(pd.to_numeric(clean["router_payoff"], errors="coerce").sum()) if len(clean) else 0,
        "win_rate": float(pd.to_numeric(clean["router_payoff"], errors="coerce").mean()) if len(clean) else None,
        "avg_ask": float(pd.to_numeric(clean["router_ask"], errors="coerce").mean()) if len(clean) else None,
        "avg_weight": float(pd.to_numeric(clean["candidate_weight"], errors="coerce").mean()) if len(clean) else None,
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": pnl / cost if cost else None,
    }


def date_bootstrap_roi(frame: pd.DataFrame, *, n: int = 5000, seed: int = 20260629) -> tuple[float | None, float | None]:
    clean = frame[frame["router_payoff"].notna()].copy()
    if clean.empty or clean["target_date"].nunique() < 3:
        return None, None
    daily = clean.groupby("target_date", as_index=False).agg(
        cost=("candidate_cost_usd", "sum"),
        pnl=("candidate_pnl_usd", "sum"),
    )
    rng = np.random.default_rng(seed)
    idx = np.arange(len(daily))
    costs = daily["cost"].to_numpy(dtype=float)
    pnls = daily["pnl"].to_numpy(dtype=float)
    rois: list[float] = []
    for _ in range(n):
        sample = rng.choice(idx, size=len(idx), replace=True)
        cost = float(costs[sample].sum())
        if cost:
            rois.append(float(pnls[sample].sum() / cost))
    if not rois:
        return None, None
    low, high = np.quantile(rois, [0.025, 0.975])
    return float(low), float(high)


def static_summary(candidates: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    settled = candidates[candidates["router_payoff"].notna()].copy()
    for candidate_id, group in settled.groupby("candidate_id", dropna=False):
        first = group.iloc[0]
        for window, mask in [
            ("all", pd.Series(True, index=group.index)),
            (f"forward_{FORWARD_START}_plus", group["target_date"].ge(FORWARD_START)),
            (f"forward_{FORWARD_START}_to_{SETTLED_END}", group["target_date"].between(FORWARD_START, SETTLED_END)),
        ]:
            g = group[mask]
            rows.append(
                summarize_group(
                    g,
                    candidate_id=candidate_id,
                    entry_policy=first["entry_policy"],
                    route_set=first["route_set"],
                    sizing_policy=first["sizing_policy"],
                    window=window,
                )
            )
    return pd.DataFrame(rows).sort_values(["window", "roi", "rows"], ascending=[True, False, False])


def validation_dates(all_dates: list[str], idx: int, selector: str) -> list[str]:
    prior = all_dates[:idx]
    if selector == "expanding_prior":
        return prior
    if selector == "trailing_10_dates":
        return prior[-TRAILING_DATES:]
    raise ValueError(selector)


def run_nested_walk_forward(candidates: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    settled = candidates[candidates["router_payoff"].notna()].copy()
    all_dates = sorted(settled["target_date"].unique().tolist())
    decisions: list[dict[str, Any]] = []
    menus = {
        "route_timing_full_only": settled[settled["sizing_policy"].eq("full_size")].copy(),
        "route_timing_with_row_soft": settled.copy(),
        "route_timing_no_daily_soft": settled[settled["sizing_policy"].isin(SIZING_POLICIES)].copy(),
    }
    for menu_name, menu_df in menus.items():
        for val_selector in ["expanding_prior", "trailing_10_dates"]:
            for idx, test_date in enumerate(all_dates):
                val_dates = validation_dates(all_dates, idx, val_selector)
                if len(val_dates) < MIN_VAL_DATES:
                    continue
                val = menu_df[menu_df["target_date"].isin(val_dates)]
                test = menu_df[menu_df["target_date"].eq(test_date)]
                if val.empty or test.empty:
                    continue
                val_summary = (
                    val.groupby("candidate_id", as_index=False)
                    .agg(
                        val_rows=("city", "size"),
                        val_dates=("target_date", "nunique"),
                        val_cost_usd=("candidate_cost_usd", "sum"),
                        val_pnl_usd=("candidate_pnl_usd", "sum"),
                    )
                )
                val_summary = val_summary[
                    val_summary["val_rows"].ge(MIN_VAL_ROWS)
                    & val_summary["val_dates"].ge(MIN_VAL_DATES)
                    & val_summary["val_cost_usd"].gt(0)
                ].copy()
                if val_summary.empty:
                    continue
                val_summary["val_roi"] = val_summary["val_pnl_usd"] / val_summary["val_cost_usd"]
                chosen = val_summary.sort_values(
                    ["val_roi", "val_dates", "val_rows", "candidate_id"],
                    ascending=[False, False, False, True],
                ).iloc[0]
                candidate_id = str(chosen["candidate_id"])
                test_group = test[test["candidate_id"].eq(candidate_id)]
                if test_group.empty:
                    continue
                first = test_group.iloc[0]
                test_cost = float(test_group["candidate_cost_usd"].sum())
                test_pnl = float(test_group["candidate_pnl_usd"].sum())
                decisions.append(
                    {
                        "menu": menu_name,
                        "validation_selector": val_selector,
                        "test_date": test_date,
                        "candidate_id": candidate_id,
                        "entry_policy": first["entry_policy"],
                        "route_set": first["route_set"],
                        "sizing_policy": first["sizing_policy"],
                        "val_rows": int(chosen["val_rows"]),
                        "val_dates": int(chosen["val_dates"]),
                        "val_roi": float(chosen["val_roi"]),
                        "test_rows": int(len(test_group)),
                        "test_cost_usd": test_cost,
                        "test_pnl_usd": test_pnl,
                        "test_roi": test_pnl / test_cost if test_cost else None,
                        "test_wins": int(pd.to_numeric(test_group["router_payoff"], errors="coerce").sum()),
                        "test_win_rate": float(pd.to_numeric(test_group["router_payoff"], errors="coerce").mean()),
                    }
                )
    decisions_df = pd.DataFrame(decisions)
    summary_rows: list[dict[str, Any]] = []
    if decisions_df.empty:
        return pd.DataFrame(), decisions_df
    decisions_df["window"] = np.where(decisions_df["test_date"].ge(FORWARD_START), f"forward_{FORWARD_START}_plus", "all_walk_forward")
    for (menu, selector, window), group in decisions_df.groupby(["menu", "validation_selector", "window"], dropna=False):
        cost = float(group["test_cost_usd"].sum())
        pnl = float(group["test_pnl_usd"].sum())
        summary_rows.append(
            {
                "menu": menu,
                "validation_selector": selector,
                "window": window,
                "test_days": int(group["test_date"].nunique()),
                "test_rows": int(group["test_rows"].sum()),
                "cost_usd": cost,
                "pnl_usd": pnl,
                "roi": pnl / cost if cost else None,
                "avg_val_roi": float(group["val_roi"].mean()) if len(group) else None,
                "chosen_candidates": ";".join(
                    f"{k}:{v}" for k, v in group["candidate_id"].value_counts().sort_index().items()
                ),
                "chosen_entry_policies": ";".join(
                    f"{k}:{v}" for k, v in group["entry_policy"].value_counts().sort_index().items()
                ),
                "chosen_route_sets": ";".join(
                    f"{k}:{v}" for k, v in group["route_set"].value_counts().sort_index().items()
                ),
                "chosen_sizing": ";".join(
                    f"{k}:{v}" for k, v in group["sizing_policy"].value_counts().sort_index().items()
                ),
            }
        )
    return pd.DataFrame(summary_rows), decisions_df


def frozen_attribution(candidates: pd.DataFrame) -> pd.DataFrame:
    baseline_id = "global_fixed_noon_priority__no_pullback_yes__full_size"
    base = candidates[candidates["candidate_id"].eq(baseline_id) & candidates["router_payoff"].notna()].copy()
    rows: list[dict[str, Any]] = []
    train = base[base["target_date"].lt(FORWARD_START)].copy()
    fwd = base[base["target_date"].ge(FORWARD_START)].copy()
    for route, fgroup in fwd.groupby("router_route", dropna=False):
        tgroup = train[train["router_route"].eq(route)].copy()
        if tgroup.empty:
            continue
        train_median_ask = float(tgroup["router_ask"].median())
        actual_cost = float(fgroup["router_cost_usd"].sum())
        actual_pnl = float(fgroup["router_pnl_usd"].sum())
        cf_pnl_at_train_ask = float((pd.to_numeric(fgroup["router_payoff"], errors="coerce") * (STAKE_USD / train_median_ask) - STAKE_USD).sum())
        rows.append(
            {
                "router_route": route,
                "train_rows": int(len(tgroup)),
                "forward_rows": int(len(fgroup)),
                "train_avg_ask": float(tgroup["router_ask"].mean()),
                "forward_avg_ask": float(fgroup["router_ask"].mean()),
                "train_win_rate": float(tgroup["router_payoff"].mean()),
                "forward_win_rate": float(fgroup["router_payoff"].mean()),
                "train_roi": float(tgroup["router_pnl_usd"].sum() / tgroup["router_cost_usd"].sum()),
                "forward_roi_actual": actual_pnl / actual_cost if actual_cost else None,
                "forward_roi_at_train_median_ask": cf_pnl_at_train_ask / actual_cost if actual_cost else None,
                "price_effect_roi": (actual_pnl - cf_pnl_at_train_ask) / actual_cost if actual_cost else None,
                "train_forecast_gap_mean": float(num(tgroup, "forecast_gap_to_running_native").mean()),
                "forward_forecast_gap_mean": float(num(fgroup, "forecast_gap_to_running_native").mean()),
                "train_peak_delta_mean": float(num(tgroup, "forecast_peak_delta_hours_local").mean()),
                "forward_peak_delta_mean": float(num(fgroup, "forecast_peak_delta_hours_local").mean()),
                "train_minutes_since_high_mean": float(num(tgroup, "minutes_since_running_max").mean()),
                "forward_minutes_since_high_mean": float(num(fgroup, "minutes_since_running_max").mean()),
                "train_forecast_error_mean_realized": float(num(tgroup, "forecast_error_native").mean()),
                "forward_forecast_error_mean_realized": float(num(fgroup, "forecast_error_native").mean()),
            }
        )
    return pd.DataFrame(rows).sort_values("forward_roi_actual")


def promising_daily(candidates: pd.DataFrame) -> pd.DataFrame:
    cand = candidates[
        candidates["candidate_id"].eq(PROMISING_SHADOW_CANDIDATE)
        & candidates["router_payoff"].notna()
    ].copy()
    if cand.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for date, group in cand.groupby("target_date", sort=True):
        cost = float(group["candidate_cost_usd"].sum())
        pnl = float(group["candidate_pnl_usd"].sum())
        rows.append(
            {
                "target_date": date,
                "rows": int(len(group)),
                "wins": int(pd.to_numeric(group["router_payoff"], errors="coerce").sum()),
                "win_rate": float(pd.to_numeric(group["router_payoff"], errors="coerce").mean()),
                "avg_ask": float(pd.to_numeric(group["router_ask"], errors="coerce").mean()),
                "avg_weight": float(pd.to_numeric(group["candidate_weight"], errors="coerce").mean()),
                "cost_usd": cost,
                "pnl_usd": pnl,
                "roi": pnl / cost if cost else None,
                "route_mix": ",".join(
                    f"{k}:{v}" for k, v in group["router_route"].value_counts().sort_index().items()
                ),
            }
        )
    return pd.DataFrame(rows)


def render_md(
    payload: dict[str, Any],
    static: pd.DataFrame,
    wf: pd.DataFrame,
    decisions: pd.DataFrame,
    attribution: pd.DataFrame,
    promising: pd.DataFrame,
) -> str:
    def table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
        shown = df if limit is None else df.head(limit)
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        for _, row in shown.iterrows():
            vals = []
            for col in cols:
                val = row.get(col)
                if col.endswith("roi") or col.endswith("rate") or col in {"avg_val_roi"} or "effect" in col:
                    vals.append(pct(val))
                elif col.endswith("usd"):
                    vals.append(money(val))
                elif isinstance(val, float):
                    vals.append(f"{val:.3f}" if math.isfinite(val) else "NA")
                else:
                    vals.append("" if pd.isna(val) else str(val))
            lines.append("| " + " | ".join(vals) + " |")
        return "\n".join(lines)

    recent = decisions[decisions["test_date"].astype(str).ge(FORWARD_START)].copy() if not decisions.empty else pd.DataFrame()
    static_forward = static[static["window"].eq(f"forward_{FORWARD_START}_plus")].sort_values("roi", ascending=False)
    static_all = static[static["window"].eq("all")].sort_values("roi", ascending=False)
    promising_recent = promising[promising["target_date"].astype(str).ge(FORWARD_START)].copy() if not promising.empty else promising

    return "\n".join(
        [
            "# Regime-Routed V3 Route-Specific Timing WF V1",
            "",
            "## Conclusion",
            "",
            (
                "Route-specific entry timing improves the diagnostic surface but does not validate a live strategy. "
                "The best static route/timing rows can look strong, but nested walk-forward remains selection-sensitive "
                "and the 6/21+ window is still fragile."
            ),
            "",
            f"Verdict: `{payload['verdict']}`，live_ready=`False`。",
            "",
            "## Data Snapshot",
            "",
            f"- Synced/rebuilt at: `{payload['generated_at_utc']}`",
            f"- All routed hourly candidates: `{payload['all_routed_rows']}` rows, `{payload['all_routed_date_min']}`..`{payload['all_routed_date_max']}`",
            f"- Unique routed hourly rows with payoff: `{payload['unique_routed_settled_rows']}` rows through `{payload['settled_end']}`",
            f"- Candidate-policy settled rows used for menu evaluation: `{payload['settled_candidate_policy_rows']}` rows",
            "- 6/27+ rows are kept only as unresolved candidate telemetry, not as PnL.",
            "",
            "## Static Candidate Leaders",
            "",
            table(
                static_all,
                ["candidate_id", "rows", "dates", "cities", "win_rate", "avg_ask", "avg_weight", "pnl_usd", "roi"],
                limit=16,
            ),
            "",
            "## Forward 6/21+ Static Leaders",
            "",
            table(
                static_forward,
                ["candidate_id", "rows", "dates", "cities", "win_rate", "avg_ask", "avg_weight", "pnl_usd", "roi"],
                limit=16,
            ),
            "",
            "## Promising Shadow Candidate",
            "",
            f"Candidate: `{PROMISING_SHADOW_CANDIDATE}`.",
            "",
            (
                f"- All settled ROI: {pct(payload['promising_shadow_candidate']['all_roi'])} "
                f"(date-bootstrap CI {pct(payload['promising_shadow_candidate']['all_ci_low'])}..{pct(payload['promising_shadow_candidate']['all_ci_high'])})"
            ),
            (
                f"- 6/21+ ROI: {pct(payload['promising_shadow_candidate']['forward_roi'])} "
                f"(date-bootstrap CI {pct(payload['promising_shadow_candidate']['forward_ci_low'])}..{pct(payload['promising_shadow_candidate']['forward_ci_high'])})"
            ),
            "- This is a shadow candidate, not live approval: forward has only 6 settled dates and still includes a small -100% day.",
            "",
            table(
                promising_recent,
                ["target_date", "rows", "wins", "win_rate", "avg_ask", "avg_weight", "cost_usd", "pnl_usd", "roi", "route_mix"],
            ),
            "",
            "## Nested Walk-Forward",
            "",
            table(
                wf.sort_values(["menu", "validation_selector", "window"]) if not wf.empty else wf,
                [
                    "menu",
                    "validation_selector",
                    "window",
                    "test_days",
                    "test_rows",
                    "pnl_usd",
                    "roi",
                    "avg_val_roi",
                    "chosen_entry_policies",
                    "chosen_route_sets",
                    "chosen_sizing",
                ],
            ),
            "",
            "## Recent Nested Decisions",
            "",
            table(
                recent.sort_values(["menu", "validation_selector", "test_date"]) if not recent.empty else recent,
                ["menu", "validation_selector", "test_date", "candidate_id", "val_roi", "test_rows", "test_pnl_usd", "test_roi"],
                limit=40,
            ),
            "",
            "## Frozen Failure Attribution",
            "",
            table(
                attribution,
                [
                    "router_route",
                    "train_rows",
                    "forward_rows",
                    "train_win_rate",
                    "forward_win_rate",
                    "train_avg_ask",
                    "forward_avg_ask",
                    "train_roi",
                    "forward_roi_actual",
                    "forward_roi_at_train_median_ask",
                    "price_effect_roi",
                    "train_forecast_error_mean_realized",
                    "forward_forecast_error_mean_realized",
                ],
            ),
            "",
            "## Interpretation",
            "",
            "- 这次不是用更多 hard gate 追坏例子，而是把 route 的入场时点变成可事前声明的候选，然后用 nested WF 选择。",
            "- `price_effect_roi` 是把 forward 的同一批输赢按训练窗中位 ask 重新计价；它只解释价格变贵的贡献，不是 live 规则。",
            "- `forecast_error_mean_realized` 是事后解释字段，不能用于下单，但它说明 6/21+ 的主要坏因是 forecast overestimate/regime shift，而不是单纯少等一小时。",
            "- 若要继续推进，下一步应把 `fresh_runway_current_no` 和 `capped_d2_no` 分开建概率/EV 校准，而不是让一个 router 同时处理两个 payoff 机制。",
        ]
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_routed = build_all_routed_candidates()
    candidates = build_candidate_menu(all_routed)
    settled = candidates[candidates["router_payoff"].notna()].copy()
    static = static_summary(candidates)
    wf, decisions = run_nested_walk_forward(candidates)
    attribution = frozen_attribution(candidates)
    promising = promising_daily(candidates)

    candidates.to_csv(OUT_SELECTED, index=False)
    static.to_csv(OUT_STATIC, index=False)
    wf.to_csv(OUT_WF, index=False)
    decisions.to_csv(OUT_DECISIONS, index=False)
    recent = decisions[decisions["test_date"].astype(str).ge(FORWARD_START)].copy() if not decisions.empty else decisions
    recent.to_csv(OUT_RECENT, index=False)
    attribution.to_csv(OUT_ATTRIBUTION, index=False)
    promising.to_csv(OUT_PROMISING_DAILY, index=False)

    promising_rows = candidates[
        candidates["candidate_id"].eq(PROMISING_SHADOW_CANDIDATE)
        & candidates["router_payoff"].notna()
    ].copy()
    promising_forward = promising_rows[promising_rows["target_date"].ge(FORWARD_START)].copy()
    p_all = summarize_group(promising_rows, candidate_id=PROMISING_SHADOW_CANDIDATE, window="all")
    p_fwd = summarize_group(promising_forward, candidate_id=PROMISING_SHADOW_CANDIDATE, window=f"forward_{FORWARD_START}_plus")
    all_low, all_high = date_bootstrap_roi(promising_rows)
    fwd_low, fwd_high = date_bootstrap_roi(promising_forward)

    payload = {
        "generated_at_utc": now_utc(),
        "inputs": {
            "atlas_rows": str(expression_v1.ATLAS_ROWS.relative_to(ROOT)),
            "base_builder": "research_regime_routed_expression_router_v3_live_like_entry_v1.base_candidates",
        },
        "all_routed_rows": int(len(all_routed)),
        "all_routed_date_min": str(all_routed["target_date"].min()),
        "all_routed_date_max": str(all_routed["target_date"].max()),
        "selected_candidate_rows": int(len(candidates)),
        "unique_routed_settled_rows": int(all_routed["router_payoff"].notna().sum()),
        "settled_candidate_policy_rows": int(len(settled)),
        "settled_end": SETTLED_END,
        "entry_policies": ENTRY_POLICIES,
        "route_sets": {k: sorted(v) if v is not None else None for k, v in ROUTE_SETS.items()},
        "sizing_policies": SIZING_POLICIES,
        "verdict": "inconclusive_shadow_only",
        "top_static_all": finite(static[static["window"].eq("all")].sort_values("roi", ascending=False).head(10).to_dict("records")),
        "top_static_forward": finite(static[static["window"].eq(f"forward_{FORWARD_START}_plus")].sort_values("roi", ascending=False).head(10).to_dict("records")),
        "walk_forward_summary": finite(wf.to_dict("records")),
        "attribution": finite(attribution.to_dict("records")),
        "promising_shadow_candidate": finite(
            {
                "candidate_id": PROMISING_SHADOW_CANDIDATE,
                "all_roi": p_all["roi"],
                "all_rows": p_all["rows"],
                "all_dates": p_all["dates"],
                "all_ci_low": all_low,
                "all_ci_high": all_high,
                "forward_roi": p_fwd["roi"],
                "forward_rows": p_fwd["rows"],
                "forward_dates": p_fwd["dates"],
                "forward_ci_low": fwd_low,
                "forward_ci_high": fwd_high,
            }
        ),
    }
    OUT_JSON.write_text(json.dumps(finite(payload), indent=2), encoding="utf-8")
    OUT_MD.write_text(render_md(payload, static, wf, decisions, attribution, promising) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
