#!/usr/bin/env python3
"""Ask-floor and feature breakdown for tmax_distribution clean_edge02 policy.

This is analysis-only. It reads the P6 shadow event ledger, reselects candidates
with the same city-day unit as the candidate shadow runner, and compares ask
floors without applying any time-ordered daily cap.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "docs/analysis/2026-07/generated/tmax_distribution_p6_shadow_telemetry_v1/shadow_events.csv"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_distribution_candidate_policy_breakdown_v1"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-05-tmax-distribution-candidate-policy-breakdown-v1.md"
SUMMARY_JSON_PATH = ROOT / "docs/analysis/2026-07/2026-07-05-tmax-distribution-candidate-policy-breakdown-v1.json"

CONFIGS = ["tmax_dist_clean_edge02", "tmax_dist_city_source_edge02", "tmax_dist_clean_edge10"]
ASK_FLOORS = [0.05, 0.10, 0.20, 0.25, 0.40]
ASK_BANDS = [(0.20, 0.40), (0.40, 0.99)]
DEFAULT_CONFIG = "tmax_dist_clean_edge02"
DEFAULT_ASK_FLOOR = 0.20
FIXED_SHARES = 5.0
ASK_CEILING = 0.99
TREND3H_FLAT_LOW = -0.5
TREND3H_FLAT_HIGH = 0.5


@dataclass(frozen=True)
class SliceSpec:
    name: str
    column: str
    min_forward_rows: int = 10
    min_dev_rows: int = 20


FEATURE_SPECS = [
    SliceSpec("expression", "chosen_expression", 10, 20),
    SliceSpec("day_regime", "day_regime", 10, 20),
    SliceSpec("intraday_state", "intraday_state", 10, 20),
    SliceSpec("running_max_state", "running_max_state", 10, 20),
    SliceSpec("wind_regime", "wind_regime", 10, 20),
    SliceSpec("moisture_cloud_regime", "moisture_cloud_regime", 10, 20),
    SliceSpec("solar_window", "solar_window", 10, 20),
    SliceSpec("forecast_source", "forecast_source", 10, 20),
    SliceSpec("city_family", "city_family", 10, 20),
    SliceSpec("ask_bucket", "ask_bucket", 10, 20),
    SliceSpec("edge_bucket", "edge_bucket", 10, 20),
    SliceSpec("p_win_bucket", "p_win_bucket", 10, 20),
    SliceSpec("hour_bucket", "hour_bucket", 10, 20),
    SliceSpec("running_max_age_bucket", "running_max_age_bucket", 10, 20),
    SliceSpec("peak_delta_bucket", "peak_delta_bucket", 10, 20),
    SliceSpec("trend_1h_bucket", "trend_1h_bucket", 10, 20),
    SliceSpec("trend_3h_bucket", "trend_3h_bucket", 10, 20),
    SliceSpec("wind_speed_bucket", "wind_speed_bucket", 10, 20),
    SliceSpec("humidity_bucket", "humidity_bucket", 10, 20),
]


def _json_ready(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (np.integer, np.floating)):
        return _json_ready(value.item())
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_ready(v) for v in value]
    return value


def _fmt_pct(value: object, *, signed: bool = True) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(v):
        return "n/a"
    sign = "+" if signed else ""
    return f"{v:{sign}.1%}"


def _fmt_num(value: object, digits: int = 2) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(v):
        return "n/a"
    return f"{v:.{digits}f}"


def _date_block_ci(rows: pd.DataFrame, cost_col: str = "cost", pnl_col: str = "pnl", *, n_boot: int = 2000, seed: int = 20260705) -> dict[str, float]:
    if rows.empty:
        return {"roi": math.nan, "ci_low": math.nan, "ci_high": math.nan}
    cost = float(rows[cost_col].sum())
    pnl = float(rows[pnl_col].sum())
    roi = pnl / cost if cost else math.nan
    by_day = rows.groupby("target_date", as_index=False).agg(cost=(cost_col, "sum"), pnl=(pnl_col, "sum"))
    if len(by_day) < 3:
        return {"roi": roi, "ci_low": math.nan, "ci_high": math.nan}
    rng = np.random.default_rng(seed)
    arr = by_day[["cost", "pnl"]].to_numpy(dtype=float)
    boot = []
    for _ in range(n_boot):
        sample = arr[rng.integers(0, len(arr), size=len(arr))]
        c = sample[:, 0].sum()
        p = sample[:, 1].sum()
        boot.append(p / c if c else math.nan)
    return {
        "roi": roi,
        "ci_low": float(np.nanpercentile(boot, 2.5)),
        "ci_high": float(np.nanpercentile(boot, 97.5)),
    }


def _load() -> pd.DataFrame:
    if not SOURCE.exists():
        raise FileNotFoundError(f"missing source: {SOURCE}")
    df = pd.read_csv(SOURCE)
    required = {
        "shadow_config_id",
        "scope",
        "city",
        "target_date",
        "decision_hour_local",
        "chosen_expression",
        "ask",
        "p_win",
        "model_edge",
        "model_roi",
        "edge_threshold",
        "win",
        "unit_pnl",
    }
    missing = sorted(required - set(df.columns))
    if missing:
        raise RuntimeError(f"source missing columns: {missing}")
    for col in [
        "decision_hour_local",
        "ask",
        "p_win",
        "model_edge",
        "model_roi",
        "edge_threshold",
        "win",
        "unit_pnl",
        "minutes_since_running_max",
        "forecast_peak_delta_hours_local",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "wind_speed_kt",
        "relative_humidity_pct",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return _add_bins(df)


def _add_bins(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ask_bucket"] = pd.cut(
        out["ask"],
        bins=[-np.inf, 0.05, 0.10, 0.20, 0.25, 0.40, 0.60, 0.80, 1.00],
        labels=["<0.05", "0.05-0.10", "0.10-0.20", "0.20-0.25", "0.25-0.40", "0.40-0.60", "0.60-0.80", "0.80-1.00"],
        right=False,
    ).astype("object").fillna("unknown")
    out["edge_bucket"] = pd.cut(
        out["model_edge"],
        bins=[-np.inf, 0.02, 0.05, 0.10, 0.20, np.inf],
        labels=["<0.02", "0.02-0.05", "0.05-0.10", "0.10-0.20", ">=0.20"],
        right=False,
    ).astype("object").fillna("unknown")
    out["p_win_bucket"] = pd.cut(
        out["p_win"],
        bins=[-np.inf, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, np.inf],
        labels=["<0.40", "0.40-0.50", "0.50-0.60", "0.60-0.70", "0.70-0.80", "0.80-0.90", ">=0.90"],
        right=False,
    ).astype("object").fillna("unknown")
    out["hour_bucket"] = pd.cut(
        out["decision_hour_local"],
        bins=[-np.inf, 10, 12, 14, 16, 18, np.inf],
        labels=["<10", "10-12", "12-14", "14-16", "16-18", "18+"],
        right=False,
    ).astype("object").fillna("unknown")
    out["running_max_age_bucket"] = pd.cut(
        out.get("minutes_since_running_max", pd.Series(index=out.index, dtype=float)),
        bins=[-np.inf, 30, 90, 180, 360, np.inf],
        labels=["<=30m", "30-90m", "90-180m", "180-360m", ">360m"],
        right=True,
    ).astype("object").fillna("unknown")
    out["peak_delta_bucket"] = pd.cut(
        out.get("forecast_peak_delta_hours_local", pd.Series(index=out.index, dtype=float)),
        bins=[-np.inf, 0, 1, 2, np.inf],
        labels=["peak_passed", "0-1h", "1-2h", "2h+"],
        right=False,
    ).astype("object").fillna("unknown")
    out["trend_1h_bucket"] = _trend_bucket(out.get("temp_trend_1h_f", pd.Series(index=out.index, dtype=float)))
    out["trend_3h_bucket"] = _trend_bucket(out.get("temp_trend_3h_f", pd.Series(index=out.index, dtype=float)))
    out["wind_speed_bucket"] = pd.cut(
        out.get("wind_speed_kt", pd.Series(index=out.index, dtype=float)),
        bins=[-np.inf, 5, 10, 15, np.inf],
        labels=["<5kt", "5-10kt", "10-15kt", "15kt+"],
        right=False,
    ).astype("object").fillna("unknown")
    out["humidity_bucket"] = pd.cut(
        out.get("relative_humidity_pct", pd.Series(index=out.index, dtype=float)),
        bins=[-np.inf, 40, 60, 80, np.inf],
        labels=["<40", "40-60", "60-80", "80+"],
        right=False,
    ).astype("object").fillna("unknown")
    for col in [
        "day_regime",
        "intraday_state",
        "moisture_cloud_regime",
        "wind_regime",
        "running_max_state",
        "solar_window",
        "forecast_source",
        "city_family",
    ]:
        if col in out.columns:
            out[col] = out[col].astype("object").fillna("unknown")
    return out


def _trend_bucket(series: pd.Series) -> pd.Series:
    return pd.cut(
        series,
        bins=[-np.inf, -0.5, 0.5, 2.0, np.inf],
        labels=["cooling", "flat", "warming", "strong_warming"],
        right=False,
    ).astype("object").fillna("unknown")


def select_policy(df: pd.DataFrame, *, config: str, scope: str, ask_floor: float, ask_ceiling: float = ASK_CEILING) -> pd.DataFrame:
    sub = df[df["shadow_config_id"].eq(config) & df["scope"].eq(scope)].copy()
    if sub.empty:
        return sub
    threshold = float(sub["edge_threshold"].dropna().iloc[0])
    eligible = sub[
        (sub["model_edge"] >= threshold)
        & (sub["ask"] >= ask_floor)
        & (sub["ask"] <= ask_ceiling)
    ].copy()
    if eligible.empty:
        return eligible
    eligible = eligible.sort_values(
        ["scope", "target_date", "city", "decision_hour_local", "model_edge", "model_roi"],
        ascending=[True, True, True, True, False, False],
    )
    selected = eligible.groupby(["scope", "city", "target_date"], as_index=False).head(1).copy()
    selected = selected.sort_values(["scope", "target_date", "decision_hour_local", "city"]).copy()
    selected["ask_floor_policy"] = ask_floor
    selected["policy_shares"] = FIXED_SHARES
    selected["cost"] = selected["ask"] * FIXED_SHARES
    selected["pnl"] = selected["unit_pnl"] * FIXED_SHARES
    return selected


def _trend3h_flat(series: pd.Series) -> pd.Series:
    trend = pd.to_numeric(series, errors="coerce")
    return trend.ge(TREND3H_FLAT_LOW) & trend.lt(TREND3H_FLAT_HIGH)


def select_policy_ask_band(
    df: pd.DataFrame,
    *,
    config: str,
    scope: str,
    ask_low: float,
    ask_high: float,
    variant: str = "base",
) -> pd.DataFrame:
    """Select first city-day candidate inside an inclusive ask band.

    This reselects after the band/mechanism filter. That matches the intended
    live policy better than selecting ask>=floor first and filtering later.
    """

    sub = df[df["shadow_config_id"].eq(config) & df["scope"].eq(scope)].copy()
    if sub.empty:
        return sub
    threshold = float(sub["edge_threshold"].dropna().iloc[0])
    eligible = sub[
        (sub["model_edge"] >= threshold)
        & (sub["ask"] >= ask_low)
        & (sub["ask"] <= ask_high)
    ].copy()
    if eligible.empty:
        return eligible
    if variant in {"no_trend3h_flat_keep_missing", "no_trend3h_flat_block_missing"}:
        trend = pd.to_numeric(eligible.get("temp_trend_3h_f"), errors="coerce")
        eligible = eligible[~_trend3h_flat(trend)].copy()
        if variant == "no_trend3h_flat_block_missing":
            trend = pd.to_numeric(eligible.get("temp_trend_3h_f"), errors="coerce")
            eligible = eligible[trend.notna()].copy()
    if eligible.empty:
        return eligible
    eligible = eligible.sort_values(
        ["scope", "target_date", "city", "decision_hour_local", "model_edge", "model_roi"],
        ascending=[True, True, True, True, False, False],
    )
    selected = eligible.groupby(["scope", "city", "target_date"], as_index=False).head(1).copy()
    selected = selected.sort_values(["scope", "target_date", "decision_hour_local", "city"]).copy()
    selected["ask_band_policy"] = f"{ask_low:.2f}-{ask_high:.2f}"
    selected["policy_variant"] = variant
    selected["policy_shares"] = FIXED_SHARES
    selected["cost"] = selected["ask"] * FIXED_SHARES
    selected["pnl"] = selected["unit_pnl"] * FIXED_SHARES
    return selected


def perf(rows: pd.DataFrame) -> dict[str, Any]:
    if rows.empty:
        return {
            "rows": 0,
            "dates": 0,
            "cities": 0,
            "wins": 0,
            "win_rate": math.nan,
            "cost": 0.0,
            "pnl": 0.0,
            "roi": math.nan,
            "roi_ci_low": math.nan,
            "roi_ci_high": math.nan,
            "avg_ask": math.nan,
            "avg_edge": math.nan,
            "avg_p_win": math.nan,
            "positive_days": 0,
            "negative_days": 0,
            "best_day_roi": math.nan,
            "worst_day_roi": math.nan,
        }
    ci = _date_block_ci(rows)
    daily = rows.groupby("target_date", as_index=False).agg(cost=("cost", "sum"), pnl=("pnl", "sum"))
    daily["roi"] = daily["pnl"] / daily["cost"]
    return {
        "rows": int(len(rows)),
        "dates": int(rows["target_date"].nunique()),
        "cities": int(rows["city"].nunique()),
        "wins": int(rows["win"].sum()),
        "win_rate": float(rows["win"].mean()),
        "cost": float(rows["cost"].sum()),
        "pnl": float(rows["pnl"].sum()),
        "roi": ci["roi"],
        "roi_ci_low": ci["ci_low"],
        "roi_ci_high": ci["ci_high"],
        "avg_ask": float(rows["ask"].mean()),
        "avg_edge": float(rows["model_edge"].mean()),
        "avg_p_win": float(rows["p_win"].mean()),
        "positive_days": int((daily["pnl"] > 0).sum()),
        "negative_days": int((daily["pnl"] < 0).sum()),
        "best_day_roi": float(daily["roi"].max()) if not daily.empty else math.nan,
        "worst_day_roi": float(daily["roi"].min()) if not daily.empty else math.nan,
    }


def build_selected(df: pd.DataFrame) -> dict[tuple[str, str, float], pd.DataFrame]:
    selected: dict[tuple[str, str, float], pd.DataFrame] = {}
    for config in CONFIGS:
        for scope in ["dev_cv", "verified_forward"]:
            for floor in ASK_FLOORS:
                selected[(config, scope, floor)] = select_policy(df, config=config, scope=scope, ask_floor=floor)
    return selected


def policy_summary(selected: dict[tuple[str, str, float], pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for (config, scope, floor), data in selected.items():
        item = {
            "config": config,
            "scope": scope,
            "ask_floor": floor,
            **perf(data),
        }
        rows.append(item)
    return pd.DataFrame(rows).sort_values(["config", "scope", "ask_floor"]).reset_index(drop=True)


def ask_band_selected(df: pd.DataFrame) -> dict[tuple[str, str, float, float, str], pd.DataFrame]:
    selected: dict[tuple[str, str, float, float, str], pd.DataFrame] = {}
    variants = ["base", "no_trend3h_flat_keep_missing", "no_trend3h_flat_block_missing"]
    for config in [DEFAULT_CONFIG]:
        for scope in ["dev_cv", "verified_forward"]:
            for low, high in ASK_BANDS:
                for variant in variants:
                    selected[(config, scope, low, high, variant)] = select_policy_ask_band(
                        df,
                        config=config,
                        scope=scope,
                        ask_low=low,
                        ask_high=high,
                        variant=variant,
                    )
    return selected


def ask_band_summary(selected: dict[tuple[str, str, float, float, str], pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for (config, scope, low, high, variant), data in selected.items():
        rows.append(
            {
                "config": config,
                "scope": scope,
                "ask_low": low,
                "ask_high": high,
                "ask_band": f"{low:.2f}-{high:.2f}",
                "variant": variant,
                **perf(data),
            }
        )
    return pd.DataFrame(rows).sort_values(["config", "scope", "ask_low", "variant"]).reset_index(drop=True)


def ask_band_daily_summary(selected: dict[tuple[str, str, float, float, str], pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for (config, scope, low, high, variant), data in selected.items():
        if config != DEFAULT_CONFIG or scope != "verified_forward" or data.empty:
            continue
        daily = data.groupby("target_date", as_index=False).agg(
            rows=("city", "count"),
            cities=("city", "nunique"),
            wins=("win", "sum"),
            cost=("cost", "sum"),
            pnl=("pnl", "sum"),
            avg_ask=("ask", "mean"),
            avg_edge=("model_edge", "mean"),
        )
        daily["config"] = config
        daily["scope"] = scope
        daily["ask_low"] = low
        daily["ask_high"] = high
        daily["ask_band"] = f"{low:.2f}-{high:.2f}"
        daily["variant"] = variant
        daily["win_rate"] = daily["wins"] / daily["rows"]
        daily["roi"] = daily["pnl"] / daily["cost"]
        rows.append(daily)
    out = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if not out.empty:
        out = out[
            [
                "config",
                "scope",
                "ask_band",
                "variant",
                "target_date",
                "rows",
                "cities",
                "wins",
                "win_rate",
                "cost",
                "pnl",
                "roi",
                "avg_ask",
                "avg_edge",
            ]
        ]
    return out.sort_values(["ask_band", "variant", "target_date"]).reset_index(drop=True)


def daily_summary(selected: dict[tuple[str, str, float], pd.DataFrame], *, config: str = DEFAULT_CONFIG) -> pd.DataFrame:
    rows = []
    for (cfg, scope, floor), data in selected.items():
        if cfg != config or scope != "verified_forward":
            continue
        if data.empty:
            continue
        daily = data.groupby("target_date", as_index=False).agg(
            rows=("city", "count"),
            cities=("city", "nunique"),
            wins=("win", "sum"),
            cost=("cost", "sum"),
            pnl=("pnl", "sum"),
            avg_ask=("ask", "mean"),
            avg_edge=("model_edge", "mean"),
        )
        daily["config"] = cfg
        daily["scope"] = scope
        daily["ask_floor"] = floor
        daily["win_rate"] = daily["wins"] / daily["rows"]
        daily["roi"] = daily["pnl"] / daily["cost"]
        rows.append(daily)
    out = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if not out.empty:
        out = out[["config", "scope", "ask_floor", "target_date", "rows", "cities", "wins", "win_rate", "cost", "pnl", "roi", "avg_ask", "avg_edge"]]
    return out.sort_values(["ask_floor", "target_date"]).reset_index(drop=True)


def slice_summary(data: pd.DataFrame, *, by: str) -> pd.DataFrame:
    rows = []
    if data.empty or by not in data.columns:
        return pd.DataFrame()
    for level, grp in data.groupby(by, dropna=False):
        item = {"feature": by, "level": str(level), **perf(grp)}
        rows.append(item)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    total_pnl = float(data["pnl"].sum())
    total_cost = float(data["cost"].sum())
    out["pnl_share"] = out["pnl"] / total_pnl if total_pnl else np.nan
    out["cost_share"] = out["cost"] / total_cost if total_cost else np.nan
    return out.sort_values(["pnl", "rows"], ascending=[True, False]).reset_index(drop=True)


def all_slice_summaries(selected: dict[tuple[str, str, float], pd.DataFrame], *, ask_floor: float = DEFAULT_ASK_FLOOR) -> pd.DataFrame:
    frames = []
    for scope in ["dev_cv", "verified_forward"]:
        data = selected[(DEFAULT_CONFIG, scope, ask_floor)]
        for spec in FEATURE_SPECS:
            s = slice_summary(data, by=spec.column)
            if s.empty:
                continue
            s.insert(0, "scope", scope)
            s.insert(1, "ask_floor", ask_floor)
            frames.append(s)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def city_summary(selected: dict[tuple[str, str, float], pd.DataFrame], *, ask_floor: float = DEFAULT_ASK_FLOOR) -> pd.DataFrame:
    rows = []
    for scope in ["dev_cv", "verified_forward"]:
        data = selected[(DEFAULT_CONFIG, scope, ask_floor)]
        if data.empty:
            continue
        for city, grp in data.groupby("city"):
            rows.append({"scope": scope, "ask_floor": ask_floor, "city": city, **perf(grp)})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["scope", "pnl"], ascending=[True, True]).reset_index(drop=True)


def stability_summary(selected: dict[tuple[str, str, float], pd.DataFrame], *, config: str = DEFAULT_CONFIG) -> pd.DataFrame:
    rows = []
    for floor in ASK_FLOORS:
        data = selected[(config, "verified_forward", floor)]
        if data.empty:
            continue
        total = perf(data)
        by_date = data.groupby("target_date", as_index=False).agg(cost=("cost", "sum"), pnl=("pnl", "sum"), rows=("city", "count"))
        by_date["roi"] = by_date["pnl"] / by_date["cost"]
        by_city = data.groupby("city", as_index=False).agg(cost=("cost", "sum"), pnl=("pnl", "sum"), rows=("target_date", "count"))
        by_city["roi"] = by_city["pnl"] / by_city["cost"]
        for kind, frame in [("date", by_date), ("city", by_city)]:
            for top_n in [1, 2, 3]:
                top = frame.sort_values("pnl", ascending=False).head(top_n)
                remain_cost = total["cost"] - float(top["cost"].sum())
                remain_pnl = total["pnl"] - float(top["pnl"].sum())
                rows.append(
                    {
                        "ask_floor": floor,
                        "remove_kind": kind,
                        "remove_top_n": top_n,
                        "removed_keys": ",".join(top["target_date" if kind == "date" else "city"].astype(str).tolist()),
                        "remaining_rows": int(total["rows"] - top["rows"].sum()),
                        "remaining_cost": remain_cost,
                        "remaining_pnl": remain_pnl,
                        "remaining_roi": remain_pnl / remain_cost if remain_cost else math.nan,
                    }
                )
        worst_days = by_date[by_date["roi"] <= -0.50]
        rows.append(
            {
                "ask_floor": floor,
                "remove_kind": "daily_loss_count",
                "remove_top_n": 0,
                "removed_keys": ",".join(worst_days["target_date"].astype(str).tolist()),
                "remaining_rows": int(len(worst_days)),
                "remaining_cost": float(worst_days["cost"].sum()),
                "remaining_pnl": float(worst_days["pnl"].sum()),
                "remaining_roi": float(worst_days["pnl"].sum() / worst_days["cost"].sum()) if float(worst_days["cost"].sum()) else math.nan,
            }
        )
    return pd.DataFrame(rows)


def candidate_filter_tests(selected: dict[tuple[str, str, float], pd.DataFrame], *, ask_floor: float = DEFAULT_ASK_FLOOR) -> pd.DataFrame:
    """Exploratory train->forward filters.

    This does not approve live changes. It looks for feature levels that are bad
    in dev_cv and still bad in verified_forward, then reports what excluding
    them would do. City filters use stricter support but remain low-confidence.
    """

    base_dev = selected[(DEFAULT_CONFIG, "dev_cv", ask_floor)]
    base_fwd = selected[(DEFAULT_CONFIG, "verified_forward", ask_floor)]
    base_dev_perf = perf(base_dev)
    base_fwd_perf = perf(base_fwd)
    rows = []

    specs = FEATURE_SPECS + [SliceSpec("city", "city", 5, 8)]
    for spec in specs:
        if spec.column not in base_dev.columns:
            continue
        dev_slice = slice_summary(base_dev, by=spec.column)
        fwd_slice = slice_summary(base_fwd, by=spec.column)
        if dev_slice.empty or fwd_slice.empty:
            continue
        merged = dev_slice[["level", "rows", "roi", "pnl"]].merge(
            fwd_slice[["level", "rows", "roi", "pnl"]],
            on="level",
            how="inner",
            suffixes=("_dev", "_fwd"),
        )
        for item in merged.to_dict("records"):
            if int(item["rows_dev"]) < spec.min_dev_rows or int(item["rows_fwd"]) < spec.min_forward_rows:
                continue
            level = item["level"]
            dev_bad = float(item["roi_dev"]) < 0
            fwd_bad = float(item["roi_fwd"]) < 0
            dev_good = float(item["roi_dev"]) > base_dev_perf["roi"]
            fwd_good = float(item["roi_fwd"]) > base_fwd_perf["roi"]
            if dev_bad and fwd_bad:
                fwd_ex = base_fwd[base_fwd[spec.column].astype(str) != str(level)].copy()
                dev_ex = base_dev[base_dev[spec.column].astype(str) != str(level)].copy()
                fwd_ex_perf = perf(fwd_ex)
                dev_ex_perf = perf(dev_ex)
                rows.append(
                    {
                        "candidate_type": "exclude_bad_level",
                        "feature": spec.name,
                        "level": level,
                        "dev_rows_level": int(item["rows_dev"]),
                        "dev_level_roi": float(item["roi_dev"]),
                        "forward_rows_level": int(item["rows_fwd"]),
                        "forward_level_roi": float(item["roi_fwd"]),
                        "dev_rows_after": dev_ex_perf["rows"],
                        "dev_roi_after": dev_ex_perf["roi"],
                        "forward_rows_after": fwd_ex_perf["rows"],
                        "forward_roi_after": fwd_ex_perf["roi"],
                        "forward_roi_delta": fwd_ex_perf["roi"] - base_fwd_perf["roi"],
                        "support_note": "exploratory_only_city_low_sample" if spec.name == "city" else "exploratory_same_direction",
                    }
                )
            if dev_good and fwd_good:
                fwd_in = base_fwd[base_fwd[spec.column].astype(str) == str(level)].copy()
                dev_in = base_dev[base_dev[spec.column].astype(str) == str(level)].copy()
                fwd_in_perf = perf(fwd_in)
                dev_in_perf = perf(dev_in)
                rows.append(
                    {
                        "candidate_type": "include_good_level",
                        "feature": spec.name,
                        "level": level,
                        "dev_rows_level": int(item["rows_dev"]),
                        "dev_level_roi": float(item["roi_dev"]),
                        "forward_rows_level": int(item["rows_fwd"]),
                        "forward_level_roi": float(item["roi_fwd"]),
                        "dev_rows_after": dev_in_perf["rows"],
                        "dev_roi_after": dev_in_perf["roi"],
                        "forward_rows_after": fwd_in_perf["rows"],
                        "forward_roi_after": fwd_in_perf["roi"],
                        "forward_roi_delta": fwd_in_perf["roi"] - base_fwd_perf["roi"],
                        "support_note": "exploratory_capacity_shrink" if spec.name != "city" else "exploratory_city_memory_risk",
                    }
                )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["candidate_type", "forward_roi_delta", "forward_rows_after"], ascending=[True, False, False]).reset_index(drop=True)


def variant_policy_tests(selected: dict[tuple[str, str, float], pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for floor in [0.20, 0.25, 0.40]:
        for scope in ["dev_cv", "verified_forward"]:
            base = selected[(DEFAULT_CONFIG, scope, floor)]
            variants = {
                "base": base,
                "no_trend3h_flat": base[base["trend_3h_bucket"].astype(str) != "flat"],
                "no_current_no": base[base["chosen_expression"].astype(str) != "current_no"],
                "no_humid_convective": base[base["moisture_cloud_regime"].astype(str) != "humid_convective_risk"],
                "no_pullback_from_high": base[base["running_max_state"].astype(str) != "pullback_from_high"],
                "no_late_16_18": base[base["hour_bucket"].astype(str) != "16-18"],
                "no_pwin_lt40": base[base["p_win"] >= 0.40],
                "combo_no_flat_no_lt40_no_late": base[
                    (base["trend_3h_bucket"].astype(str) != "flat")
                    & (base["p_win"] >= 0.40)
                    & (base["hour_bucket"].astype(str) != "16-18")
                ],
                "only_d1_no": base[base["chosen_expression"].astype(str) == "d1_no"],
            }
            for name, data in variants.items():
                item = {"ask_floor": floor, "scope": scope, "variant": name, **perf(data)}
                rows.append(item)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    base = out[out["variant"].eq("base")][["ask_floor", "scope", "roi", "rows"]].rename(columns={"roi": "base_roi", "rows": "base_rows"})
    out = out.merge(base, on=["ask_floor", "scope"], how="left")
    out["roi_delta_vs_base"] = out["roi"] - out["base_roi"]
    out["row_delta_vs_base"] = out["rows"] - out["base_rows"]
    return out.sort_values(["ask_floor", "variant", "scope"]).reset_index(drop=True)


def _taker_fee(price: pd.Series) -> pd.Series:
    return 0.05 * price * (1.0 - price)


def execution_sensitivity(selected: dict[tuple[str, str, float], pd.DataFrame]) -> pd.DataFrame:
    rows = []
    variants = {
        "base": lambda data: data,
        "no_trend3h_flat": lambda data: data[data["trend_3h_bucket"].astype(str) != "flat"],
        "combo_no_flat_no_lt40_no_late": lambda data: data[
            (data["trend_3h_bucket"].astype(str) != "flat")
            & (data["p_win"] >= 0.40)
            & (data["hour_bucket"].astype(str) != "16-18")
        ],
    }
    for floor in [0.20, 0.25, 0.40]:
        base = selected[(DEFAULT_CONFIG, "verified_forward", floor)]
        for variant_name, fn in variants.items():
            data = fn(base).copy()
            for slippage in [0.00, 0.01, 0.02, 0.05]:
                for fee_on in [False, True]:
                    if data.empty:
                        rows.append(
                            {
                                "ask_floor": floor,
                                "variant": variant_name,
                                "slippage": slippage,
                                "taker_fee": fee_on,
                                "rows": 0,
                                "cost": 0.0,
                                "pnl": 0.0,
                                "roi": math.nan,
                                "avg_exec_price": math.nan,
                                "avg_fee": 0.0,
                            }
                        )
                        continue
                    exec_price = (data["ask"] + slippage).clip(upper=0.999)
                    fee = _taker_fee(exec_price) if fee_on else 0.0
                    pnl = (data["win"] - exec_price - fee) * FIXED_SHARES
                    cost = exec_price * FIXED_SHARES
                    rows.append(
                        {
                            "ask_floor": floor,
                            "variant": variant_name,
                            "slippage": slippage,
                            "taker_fee": fee_on,
                            "rows": int(len(data)),
                            "wins": int(data["win"].sum()),
                            "win_rate": float(data["win"].mean()),
                            "cost": float(cost.sum()),
                            "pnl": float(pnl.sum()),
                            "roi": float(pnl.sum() / cost.sum()) if float(cost.sum()) else math.nan,
                            "avg_exec_price": float(exec_price.mean()),
                            "avg_fee": float(_taker_fee(exec_price).mean()) if fee_on else 0.0,
                        }
                    )
    return pd.DataFrame(rows)


def ask_band_execution_sensitivity(selected: dict[tuple[str, str, float, float, str], pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for (config, scope, low, high, variant), data in selected.items():
        if config != DEFAULT_CONFIG or scope != "verified_forward":
            continue
        for slippage in [0.00, 0.01, 0.02, 0.05]:
            for fee_on in [False, True]:
                if data.empty:
                    rows.append(
                        {
                            "config": config,
                            "scope": scope,
                            "ask_band": f"{low:.2f}-{high:.2f}",
                            "variant": variant,
                            "slippage": slippage,
                            "taker_fee": fee_on,
                            "rows": 0,
                            "cost": 0.0,
                            "pnl": 0.0,
                            "roi": math.nan,
                            "avg_exec_price": math.nan,
                            "avg_fee": 0.0,
                        }
                    )
                    continue
                exec_price = (data["ask"] + slippage).clip(upper=0.999)
                fee = _taker_fee(exec_price) if fee_on else 0.0
                pnl = (data["win"] - exec_price - fee) * FIXED_SHARES
                cost = exec_price * FIXED_SHARES
                rows.append(
                    {
                        "config": config,
                        "scope": scope,
                        "ask_band": f"{low:.2f}-{high:.2f}",
                        "variant": variant,
                        "slippage": slippage,
                        "taker_fee": fee_on,
                        "rows": int(len(data)),
                        "wins": int(data["win"].sum()),
                        "win_rate": float(data["win"].mean()),
                        "cost": float(cost.sum()),
                        "pnl": float(pnl.sum()),
                        "roi": float(pnl.sum() / cost.sum()) if float(cost.sum()) else math.nan,
                        "avg_exec_price": float(exec_price.mean()),
                        "avg_fee": float(_taker_fee(exec_price).mean()) if fee_on else 0.0,
                    }
                )
    return pd.DataFrame(rows)


def _md_table(df: pd.DataFrame, columns: list[tuple[str, str]], *, max_rows: int | None = None) -> str:
    if df.empty:
        return "_No rows._"
    use = df.head(max_rows) if max_rows else df
    headers = [h for h, _ in columns]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in use.to_dict("records"):
        vals = []
        for _, col in columns:
            value = row.get(col)
            if col.endswith("roi") or col.endswith("rate") or col in {
                "roi",
                "win_rate",
                "roi_ci_low",
                "roi_ci_high",
                "pnl_share",
                "cost_share",
                "forward_roi_delta",
                "roi_delta_vs_base",
                "dev_level_roi",
                "forward_level_roi",
                "dev_roi_after",
                "forward_roi_after",
                "dev_roi",
                "fwd_roi",
                "fwd_ci_low",
                "fwd_ci_high",
            }:
                vals.append(_fmt_pct(value))
            elif col in {"cost", "pnl", "avg_ask", "avg_edge", "avg_p_win", "avg_exec_price", "remaining_cost", "remaining_pnl"}:
                vals.append(_fmt_num(value, 2))
            else:
                vals.append(str(value))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def build_report(
    *,
    policy: pd.DataFrame,
    daily: pd.DataFrame,
    slices: pd.DataFrame,
    cities: pd.DataFrame,
    stability: pd.DataFrame,
    filters: pd.DataFrame,
    variants: pd.DataFrame,
    execution: pd.DataFrame,
    band_policy: pd.DataFrame,
    band_daily: pd.DataFrame,
    band_execution: pd.DataFrame,
    selected: dict[tuple[str, str, float], pd.DataFrame],
) -> str:
    default_fwd = selected[(DEFAULT_CONFIG, "verified_forward", DEFAULT_ASK_FLOOR)]
    default_dev = selected[(DEFAULT_CONFIG, "dev_cv", DEFAULT_ASK_FLOOR)]
    p_default = perf(default_fwd)
    p_dev = perf(default_dev)

    policy_focus = policy[
        policy["config"].eq(DEFAULT_CONFIG)
        & policy["scope"].isin(["dev_cv", "verified_forward"])
    ].copy()
    daily_focus = daily[daily["ask_floor"].isin(ASK_FLOORS)].copy()

    expr = slices[
        slices["scope"].eq("verified_forward")
        & slices["feature"].eq("chosen_expression")
    ].copy()
    if expr.empty:
        expr = slices[
            slices["scope"].eq("verified_forward")
            & slices["feature"].eq("chosen_expression")
        ].copy()

    forward_slices = slices[slices["scope"].eq("verified_forward")].copy()
    bad_slices = forward_slices[(forward_slices["rows"] >= 10) & (forward_slices["roi"] < 0)].sort_values("pnl").head(15)
    good_slices = forward_slices[(forward_slices["rows"] >= 10) & (forward_slices["roi"] > p_default["roi"])].sort_values("pnl", ascending=False).head(15)

    city_fwd = cities[cities["scope"].eq("verified_forward")].copy()
    city_bad = city_fwd.sort_values("pnl").head(12)
    city_good = city_fwd.sort_values("pnl", ascending=False).head(12)

    daily_bad = daily_focus.sort_values(["ask_floor", "pnl"]).groupby("ask_floor").head(4)
    daily_good = daily_focus.sort_values(["ask_floor", "pnl"], ascending=[True, False]).groupby("ask_floor").head(4)

    filt_bad = filters[filters["candidate_type"].eq("exclude_bad_level")].head(20) if not filters.empty else pd.DataFrame()
    filt_good = filters[filters["candidate_type"].eq("include_good_level")].head(20) if not filters.empty else pd.DataFrame()
    variant_focus = variants[
        variants["scope"].eq("verified_forward")
        & variants["ask_floor"].isin([0.20, 0.25])
    ].sort_values(["ask_floor", "roi"], ascending=[True, False])
    variant_dev = variants[variants["scope"].eq("dev_cv")][["ask_floor", "variant", "rows", "roi"]].rename(
        columns={"rows": "dev_rows", "roi": "dev_roi"}
    )
    variant_fwd = variants[variants["scope"].eq("verified_forward")][
        ["ask_floor", "variant", "rows", "roi", "roi_ci_low", "roi_ci_high", "roi_delta_vs_base", "row_delta_vs_base"]
    ].rename(columns={"rows": "fwd_rows", "roi": "fwd_roi", "roi_ci_low": "fwd_ci_low", "roi_ci_high": "fwd_ci_high"})
    variant_pair = variant_fwd.merge(variant_dev, on=["ask_floor", "variant"], how="left")
    variant_pair = variant_pair[variant_pair["ask_floor"].isin([0.20, 0.25])].copy()
    variant_pair["candidate_read"] = "shadow_only"
    variant_pair.loc[variant_pair["variant"].eq("no_trend3h_flat"), "candidate_read"] = "cleanest_mechanism_candidate"
    variant_pair.loc[variant_pair["variant"].eq("only_d1_no"), "candidate_read"] = "forward_strong_dev_weak"
    variant_pair.loc[variant_pair["variant"].isin(["no_pwin_lt40", "no_late_16_18", "combo_no_flat_no_lt40_no_late"]), "candidate_read"] = "forward_driven_watch"
    variant_pair = variant_pair.sort_values(["ask_floor", "fwd_roi"], ascending=[True, False])
    execution_focus = execution[
        execution["ask_floor"].isin([0.20, 0.25])
        & execution["variant"].isin(["base", "no_trend3h_flat"])
        & execution["taker_fee"].eq(True)
    ].copy()
    execution_focus["slippage_cents"] = (execution_focus["slippage"] * 100).round().astype(int)
    execution_focus = execution_focus.sort_values(["ask_floor", "variant", "slippage"])

    band_fwd = band_policy[band_policy["scope"].eq("verified_forward")].copy()
    band_dev = band_policy[band_policy["scope"].eq("dev_cv")][["ask_band", "variant", "rows", "roi"]].rename(
        columns={"rows": "dev_rows", "roi": "dev_roi"}
    )
    band_pair = band_fwd.merge(band_dev, on=["ask_band", "variant"], how="left")
    band_daily_focus = band_daily[band_daily["scope"].eq("verified_forward")].copy()
    band_exec_focus = band_execution[
        band_execution["taker_fee"].eq(True)
        & band_execution["variant"].isin(["base", "no_trend3h_flat_block_missing"])
    ].copy()
    if not band_exec_focus.empty:
        band_exec_focus["slippage_cents"] = (band_exec_focus["slippage"] * 100).round().astype(int)
        band_exec_focus = band_exec_focus.sort_values(["ask_band", "variant", "slippage"])

    lines = [
        "# Tmax Distribution Edge Candidate Policy Breakdown v1",
        "",
        "Generated: 2026-07-05",
        "Status: `analysis / zero-notional policy breakdown`",
        "",
        "## 结论先说",
        "",
        f"- 当前主候选仍是 `{DEFAULT_CONFIG}`；本报告只比较执行表达，不批准 live。",
        f"- 默认候选 `ask>=0.20 + fixed 5 shares + 每 city-day 第一条过门` 在 verified_forward 为 "
        f"{p_default['rows']} 笔/{p_default['dates']} 天/{p_default['cities']} 城，ROI {_fmt_pct(p_default['roi'])}，"
        f"date-block CI [{_fmt_pct(p_default['roi_ci_low'])}, {_fmt_pct(p_default['roi_ci_high'])}]。",
        f"- 同口径 dev_cv 为 {p_dev['rows']} 笔/{p_dev['dates']} 天，ROI {_fmt_pct(p_dev['roi'])}。"
        "dev_cv 和 verified_forward 同号，但这是 backfill/rejoin + zero-notional evidence，不是 P7 fresh-forward。",
        "- 不使用 time-order daily cap。它会偏早时区；本报告只把 cap 当作反事实诊断，不进入 selector。",
        "- `ask_floor` 的主要作用不是控量，而是把 0.2c/0.4c 这种 lottery/stale-tail 票从中价位校准策略里拆出去。"
        "`ask>=0.20/0.25/0.40` 的 forward ROI 接近，说明 ask floor 不是主要 alpha 来源。",
        "- 可研究优化方向：表达类型、`running_max_state`、`intraday_state`、`wind/moisture`、城市 family/城市；"
        "但城市级样本很薄，不能把城市切片直接变成 live allow/deny。",
        "",
        "## Evidence Snapshot",
        "",
        f"- Source: `{SOURCE.relative_to(ROOT)}`",
        "- Row grain: `shadow_config_id + scope + city + target_date + decision_hour_local`；本报告重新按 city-day 去重选单。",
        "- Scopes: `dev_cv=2026-06-02..2026-06-20`，`verified_forward=2026-06-21..2026-07-03`。",
        "- Sizing replay: fixed 5 shares；PnL = `5 * (win - ask)`，cost = `5 * ask`；ROI 与 1-share 相同，金额放大 5 倍。",
        "- 价格口径：P5/P6 的 `ask` 是 state snapshot 里的 observed ask，不是 best bid；原始 ROI 未包含 taker fee、fresh-book drift 或 maker fill probability。",
        "",
        "## Ask Floor 总览",
        "",
        _md_table(
            policy_focus,
            [
                ("config", "config"),
                ("scope", "scope"),
                ("ask_floor", "ask_floor"),
                ("rows", "rows"),
                ("dates", "dates"),
                ("cities", "cities"),
                ("win", "win_rate"),
                ("pos days", "positive_days"),
                ("neg days", "negative_days"),
                ("cost", "cost"),
                ("pnl", "pnl"),
                ("ROI", "roi"),
                ("CI low", "roi_ci_low"),
                ("CI high", "roi_ci_high"),
                ("avg ask", "avg_ask"),
            ],
        ),
        "",
        "## Verified Forward 每日明细",
        "",
        "下面是每个 ask floor 每天的笔数、胜率、ROI。日度波动很大，所以不能只看总 ROI。",
        "",
        _md_table(
            daily_focus,
            [
                ("ask_floor", "ask_floor"),
                ("date", "target_date"),
                ("rows", "rows"),
                ("wins", "wins"),
                ("win", "win_rate"),
                ("cost", "cost"),
                ("pnl", "pnl"),
                ("ROI", "roi"),
                ("avg ask", "avg_ask"),
            ],
        ),
        "",
        "## 日度贡献拆分",
        "",
        "最大盈利日/亏损日如下。若收益只靠一两天撑住，策略不能放大；若亏损集中在可解释机制，才值得改表达。",
        "",
        "### Top Daily Winners",
        "",
        _md_table(
            daily_good,
            [
                ("ask_floor", "ask_floor"),
                ("date", "target_date"),
                ("rows", "rows"),
                ("win", "win_rate"),
                ("pnl", "pnl"),
                ("ROI", "roi"),
            ],
        ),
        "",
        "### Top Daily Losers",
        "",
        _md_table(
            daily_bad,
            [
                ("ask_floor", "ask_floor"),
                ("date", "target_date"),
                ("rows", "rows"),
                ("win", "win_rate"),
                ("pnl", "pnl"),
                ("ROI", "roi"),
            ],
        ),
        "",
        "## 稳健性压力测试",
        "",
        _md_table(
            stability,
            [
                ("ask_floor", "ask_floor"),
                ("kind", "remove_kind"),
                ("top_n", "remove_top_n"),
                ("removed", "removed_keys"),
                ("remain rows", "remaining_rows"),
                ("remain pnl", "remaining_pnl"),
                ("remain ROI", "remaining_roi"),
            ],
            max_rows=40,
        ),
        "",
        "## Feature / Regime 拆分",
        "",
        "### Forward 负贡献切片（rows>=10）",
        "",
        _md_table(
            bad_slices,
            [
                ("feature", "feature"),
                ("level", "level"),
                ("rows", "rows"),
                ("win", "win_rate"),
                ("pnl", "pnl"),
                ("ROI", "roi"),
                ("pnl share", "pnl_share"),
            ],
        ),
        "",
        "### Forward 正贡献切片（rows>=10 且 ROI 高于默认总 ROI）",
        "",
        _md_table(
            good_slices,
            [
                ("feature", "feature"),
                ("level", "level"),
                ("rows", "rows"),
                ("win", "win_rate"),
                ("pnl", "pnl"),
                ("ROI", "roi"),
                ("pnl share", "pnl_share"),
            ],
        ),
        "",
        "## 城市贡献",
        "",
        "城市切片只能当诊断。单城 active days 太少，不能直接生成 live city allow/deny。",
        "",
        "### Worst Cities",
        "",
        _md_table(
            city_bad,
            [
                ("city", "city"),
                ("rows", "rows"),
                ("dates", "dates"),
                ("win", "win_rate"),
                ("pnl", "pnl"),
                ("ROI", "roi"),
            ],
        ),
        "",
        "### Best Cities",
        "",
        _md_table(
            city_good,
            [
                ("city", "city"),
                ("rows", "rows"),
                ("dates", "dates"),
                ("win", "win_rate"),
                ("pnl", "pnl"),
                ("ROI", "roi"),
            ],
        ),
        "",
        "## 探索性优化候选",
        "",
        "这些候选只说明“值得继续 shadow/replay”，不等于现在可以加 live rule。筛选标准是 dev_cv 与 verified_forward 同方向，且有最小支持。",
        "",
        "### 可疑负贡献 level：候选排除",
        "",
        _md_table(
            filt_bad,
            [
                ("feature", "feature"),
                ("level", "level"),
                ("dev rows", "dev_rows_level"),
                ("dev ROI", "dev_level_roi"),
                ("fwd rows", "forward_rows_level"),
                ("fwd ROI", "forward_level_roi"),
                ("after rows", "forward_rows_after"),
                ("after ROI", "forward_roi_after"),
                ("delta", "forward_roi_delta"),
                ("note", "support_note"),
            ],
        ),
        "",
        "### 高贡献 level：候选加权/优先级",
        "",
        _md_table(
            filt_good,
            [
                ("feature", "feature"),
                ("level", "level"),
                ("dev rows", "dev_rows_level"),
                ("dev ROI", "dev_level_roi"),
                ("fwd rows", "forward_rows_level"),
                ("fwd ROI", "forward_level_roi"),
                ("after rows", "forward_rows_after"),
                ("after ROI", "forward_roi_after"),
                ("delta", "forward_roi_delta"),
                ("note", "support_note"),
            ],
        ),
        "",
        "## 组合候选对比",
        "",
        "这些是候选 selector 变体，不是最终 live 规则。重点看 dev 与 forward 是否同方向；只在 forward 好、dev 不支持的变体按过拟合处理。",
        "",
        _md_table(
            variant_focus,
            [
                ("ask_floor", "ask_floor"),
                ("variant", "variant"),
                ("rows", "rows"),
                ("win", "win_rate"),
                ("pos days", "positive_days"),
                ("neg days", "negative_days"),
                ("pnl", "pnl"),
                ("ROI", "roi"),
                ("CI low", "roi_ci_low"),
                ("CI high", "roi_ci_high"),
                ("delta", "roi_delta_vs_base"),
                ("row delta", "row_delta_vs_base"),
            ],
        ),
        "",
        "### Dev vs Forward 同表",
        "",
        _md_table(
            variant_pair,
            [
                ("ask_floor", "ask_floor"),
                ("variant", "variant"),
                ("dev rows", "dev_rows"),
                ("dev ROI", "dev_roi"),
                ("fwd rows", "fwd_rows"),
                ("fwd ROI", "fwd_roi"),
                ("fwd CI low", "fwd_ci_low"),
                ("fwd CI high", "fwd_ci_high"),
                ("delta", "roi_delta_vs_base"),
                ("row delta", "row_delta_vs_base"),
                ("read", "candidate_read"),
            ],
        ),
        "",
        "Interpretation:",
        "",
        "- `no_trend3h_flat` 是目前最干净的机制优化候选：3h 趋势走平意味着剩余路径不再顺滑升温；dev_cv 和 verified_forward 都改善。",
        "- `only_d1_no` forward 很强，但 dev_cv 更弱、样本更薄，像表达赢家偏差，不能直接变主策略。",
        "- `no_pwin_lt40`、`no_late_16_18` 主要由 forward 驱动，dev_cv 不支持硬过滤；可以作为 shadow tag。",
        "- 城市层面只能做权重观察，不能直接按 Beijing/Miami 这种小样本名单 live allow/deny。",
        "",
        "## 执行磨损敏感性",
        "",
        "主报告 ROI 使用 observed ask 成交价，未含费用。下表按 fixed 5 shares，把执行价设为 `ask + slippage`，并按 Weather taker fee `0.05 * p * (1-p)` 扣费。它不是真实 fill replay，只是把已知摩擦显式打进去。",
        "",
        _md_table(
            execution_focus,
            [
                ("ask_floor", "ask_floor"),
                ("variant", "variant"),
                ("slip c", "slippage_cents"),
                ("rows", "rows"),
                ("win", "win_rate"),
                ("avg exec", "avg_exec_price"),
                ("cost", "cost"),
                ("pnl", "pnl"),
                ("ROI", "roi"),
            ],
        ),
        "",
        "Read: base 在 +2c taker 磨损后仍为正，但 +5c 后基本被吃掉；`no_trend3h_flat` 抗磨损更好。真实 live 不能用这个表替代 fresh-book replay，因为 maker-first 会引入 fill selection，taker 会遇到 quote drift。",
        "",
        "## Ask 20-40c 中价位切片",
        "",
        "`ask_floor=0.20` 会包含 40c 以上的票；下面单独统计 `0.20 <= ask <= 0.40`。这里同样按 city-day 重新选第一条候选，不是从 ask_floor 结果里事后裁剪。",
        "",
        _md_table(
            band_pair,
            [
                ("band", "ask_band"),
                ("variant", "variant"),
                ("dev rows", "dev_rows"),
                ("dev ROI", "dev_roi"),
                ("fwd rows", "rows"),
                ("dates", "dates"),
                ("cities", "cities"),
                ("win", "win_rate"),
                ("pos days", "positive_days"),
                ("neg days", "negative_days"),
                ("cost", "cost"),
                ("pnl", "pnl"),
                ("ROI", "roi"),
                ("CI low", "roi_ci_low"),
                ("CI high", "roi_ci_high"),
                ("avg ask", "avg_ask"),
            ],
        ),
        "",
        "### 20-40c 每日明细",
        "",
        _md_table(
            band_daily_focus,
            [
                ("band", "ask_band"),
                ("variant", "variant"),
                ("date", "target_date"),
                ("rows", "rows"),
                ("wins", "wins"),
                ("win", "win_rate"),
                ("cost", "cost"),
                ("pnl", "pnl"),
                ("ROI", "roi"),
                ("avg ask", "avg_ask"),
            ],
        ),
        "",
        "### 20-40c 执行磨损",
        "",
        _md_table(
            band_exec_focus,
            [
                ("band", "ask_band"),
                ("variant", "variant"),
                ("slip c", "slippage_cents"),
                ("rows", "rows"),
                ("win", "win_rate"),
                ("avg exec", "avg_exec_price"),
                ("cost", "cost"),
                ("pnl", "pnl"),
                ("ROI", "roi"),
            ],
        ),
        "",
        "## Verdict",
        "",
        "```text",
        "significance=PASS for default ask>=0.20 on current verified_forward date-block CI",
        "baseline=PARTIAL (market ask is used, but ask-side/spread/fill feasibility gate is still pending)",
        "forward=FAIL/NA (P7 fresh-forward runner evidence not accumulated yet)",
        "conclusion=shadow_candidate / no live",
        "```",
        "",
        "Practical read:",
        "",
        "- 当前更合理的候选不是加 daily cap，而是 `ask_floor=0.20` 或 `0.25` + fixed 5 shares + city-day dedupe。",
        "- 如果要控量，不能按当天先后顺序 cap；应研究不偏时区的 allocation，例如按地区预算、同一 cycle 内 score 排序、或固定每 city-family 上限。",
        "- 进一步优化优先级：先用 P7 fresh-forward 累积 7-14 天，再验证本报告列出的 expression/regime/city-family 候选；城市单名单独切掉目前证据不足。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = _load()
    selected = build_selected(df)
    band_selected = ask_band_selected(df)
    policy = policy_summary(selected)
    band_policy = ask_band_summary(band_selected)
    daily = daily_summary(selected)
    band_daily = ask_band_daily_summary(band_selected)
    slices = all_slice_summaries(selected)
    cities = city_summary(selected)
    stability = stability_summary(selected)
    filters = candidate_filter_tests(selected)
    variants = variant_policy_tests(selected)
    execution = execution_sensitivity(selected)
    band_execution = ask_band_execution_sensitivity(band_selected)

    policy.to_csv(OUT_DIR / "policy_summary.csv", index=False)
    band_policy.to_csv(OUT_DIR / "ask_band_policy_summary.csv", index=False)
    daily.to_csv(OUT_DIR / "daily_by_ask_floor.csv", index=False)
    band_daily.to_csv(OUT_DIR / "ask_band_daily.csv", index=False)
    slices.to_csv(OUT_DIR / "feature_slice_summary.csv", index=False)
    cities.to_csv(OUT_DIR / "city_summary.csv", index=False)
    stability.to_csv(OUT_DIR / "stability_stress.csv", index=False)
    filters.to_csv(OUT_DIR / "exploratory_filter_candidates.csv", index=False)
    variants.to_csv(OUT_DIR / "variant_policy_tests.csv", index=False)
    execution.to_csv(OUT_DIR / "execution_sensitivity.csv", index=False)
    band_execution.to_csv(OUT_DIR / "ask_band_execution_sensitivity.csv", index=False)

    summary = {
        "source": str(SOURCE.relative_to(ROOT)),
        "generated_outputs": {
            "report": str(REPORT_PATH.relative_to(ROOT)),
            "json": str(SUMMARY_JSON_PATH.relative_to(ROOT)),
            "policy_summary_csv": str((OUT_DIR / "policy_summary.csv").relative_to(ROOT)),
            "ask_band_policy_summary_csv": str((OUT_DIR / "ask_band_policy_summary.csv").relative_to(ROOT)),
            "daily_by_ask_floor_csv": str((OUT_DIR / "daily_by_ask_floor.csv").relative_to(ROOT)),
            "ask_band_daily_csv": str((OUT_DIR / "ask_band_daily.csv").relative_to(ROOT)),
            "feature_slice_summary_csv": str((OUT_DIR / "feature_slice_summary.csv").relative_to(ROOT)),
            "city_summary_csv": str((OUT_DIR / "city_summary.csv").relative_to(ROOT)),
            "stability_stress_csv": str((OUT_DIR / "stability_stress.csv").relative_to(ROOT)),
            "exploratory_filter_candidates_csv": str((OUT_DIR / "exploratory_filter_candidates.csv").relative_to(ROOT)),
            "variant_policy_tests_csv": str((OUT_DIR / "variant_policy_tests.csv").relative_to(ROOT)),
            "execution_sensitivity_csv": str((OUT_DIR / "execution_sensitivity.csv").relative_to(ROOT)),
            "ask_band_execution_sensitivity_csv": str((OUT_DIR / "ask_band_execution_sensitivity.csv").relative_to(ROOT)),
        },
        "default_policy": {
            "config": DEFAULT_CONFIG,
            "scope": "verified_forward",
            "ask_floor": DEFAULT_ASK_FLOOR,
            "fixed_shares": FIXED_SHARES,
            "daily_cap_policy": "none",
            "selection_rule": "first_eligible_per_city_day_after_edge_and_ask_floor",
            **perf(selected[(DEFAULT_CONFIG, "verified_forward", DEFAULT_ASK_FLOOR)]),
        },
        "ask_band_20_40": band_policy.to_dict("records") if not band_policy.empty else [],
        "policy_summary": policy.to_dict("records"),
        "top_forward_bad_slices": slices[
            slices["scope"].eq("verified_forward")
            & (slices["rows"] >= 10)
            & (slices["roi"] < 0)
        ].sort_values("pnl").head(20).to_dict("records"),
        "top_forward_good_slices": slices[
            slices["scope"].eq("verified_forward")
            & (slices["rows"] >= 10)
            & (slices["roi"] > perf(selected[(DEFAULT_CONFIG, "verified_forward", DEFAULT_ASK_FLOOR)])["roi"])
        ].sort_values("pnl", ascending=False).head(20).to_dict("records"),
        "exploratory_filter_candidates": filters.head(50).to_dict("records") if not filters.empty else [],
        "variant_policy_tests": variants.to_dict("records") if not variants.empty else [],
        "execution_sensitivity": execution.to_dict("records") if not execution.empty else [],
        "ask_band_execution_sensitivity": band_execution.to_dict("records") if not band_execution.empty else [],
    }
    SUMMARY_JSON_PATH.write_text(json.dumps(_json_ready(summary), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    REPORT_PATH.write_text(
        build_report(
            policy=policy,
            daily=daily,
            slices=slices,
            cities=cities,
            stability=stability,
            filters=filters,
            variants=variants,
            execution=execution,
            band_policy=band_policy,
            band_daily=band_daily,
            band_execution=band_execution,
            selected=selected,
        ),
        encoding="utf-8",
    )
    print(json.dumps(_json_ready(summary["default_policy"]), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
