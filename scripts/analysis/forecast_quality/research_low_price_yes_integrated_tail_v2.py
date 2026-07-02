#!/usr/bin/env python3
"""Integrated low-price YES tail v2 research.

This script intentionally does not optimize a new live gate.  It takes the
current v1 no-dust low-price YES denominator and asks whether the research
layers we already built add stable information on the same rows:

- source-aware price bands
- as-of station-bias / p_cal telemetry
- later intraday METAR/regime labels when the same tail bracket appears

The output is a shadow design, not live approval.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
PCAL_DETAILS = ROOT / "docs/analysis/2026-07/generated/low_price_yes_tail_pcal_v1/details.csv"
METAR_DETAILS = ROOT / "docs/analysis/2026-07/generated/low_price_yes_lottery_metar_regime_v2/details.csv"
P2_SUMMARY = ROOT / "docs/analysis/2026-07/generated/tmax_distribution_p2_ev_shadow_v1/deduped_best_expression_summary.csv"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/low_price_yes_integrated_tail_v2"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-02-low-price-yes-integrated-tail-v2.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-02-low-price-yes-integrated-tail-v2.json"

HOLDOUT_START = "2026-06-21"
FORWARD_START = "2026-06-27"
RECENT_START = "2026-06-08"
RNG_SEED = 20260702


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: Any) -> str:
    try:
        x = float(value)
    except Exception:
        return ""
    if not math.isfinite(x):
        return ""
    return f"{100 * x:+.1f}%"


def money(value: Any) -> str:
    try:
        x = float(value)
    except Exception:
        return ""
    if not math.isfinite(x):
        return ""
    return f"${x:+.2f}"


def md_table(df: pd.DataFrame, cols: list[tuple[str, str]], max_rows: int = 40) -> str:
    if df.empty:
        return "_No rows._"
    pct_cols = {
        "win_rate",
        "roi",
        "roi_ci_low",
        "roi_ci_high",
        "top_trade_removed_roi",
        "top3_removed_roi",
        "top5_removed_roi",
        "top10_removed_roi",
        "selected_roi",
        "complement_roi",
        "roi_delta_vs_complement",
    }
    money_cols = {"cost", "pnl", "max_daily_loss", "pnl_per_active_day"}
    int_cols = {"rows", "dates", "cities", "wins", "losing_days", "roi_le_minus_50_days", "selected_rows", "complement_rows"}
    lines = ["| " + " | ".join(label for _, label in cols) + " |"]
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for _, row in df.head(max_rows).iterrows():
        vals: list[str] = []
        for key, _label in cols:
            val = row.get(key)
            if key in pct_cols or key.endswith("_roi") or "roi_ci" in key:
                vals.append(pct(val))
            elif key in money_cols or key.endswith("_pnl") or key.endswith("_loss"):
                vals.append(money(val))
            elif key in int_cols and pd.notna(val):
                vals.append(str(int(val)))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}" if math.isfinite(val) else "")
            else:
                vals.append("" if val is None or pd.isna(val) else str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def block_bootstrap_ci(daily: pd.DataFrame, n_boot: int = 5000) -> tuple[float | None, float | None]:
    if len(daily) < 3:
        return (None, None)
    rng = np.random.default_rng(RNG_SEED)
    costs = daily["cost"].to_numpy(float)
    pnls = daily["pnl"].to_numpy(float)
    vals: list[float] = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(daily), len(daily))
        cost = float(costs[idx].sum())
        if cost > 0:
            vals.append(float(pnls[idx].sum() / cost))
    lo, hi = np.quantile(vals, [0.025, 0.975])
    return (float(lo), float(hi))


def add_fixed_one_dollar_pnl(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ask"] = pd.to_numeric(out["ask"], errors="coerce")
    out["payoff"] = pd.to_numeric(out["payoff"], errors="coerce")
    out["cost"] = 1.0
    out["shares"] = out["cost"] / out["ask"]
    out["pnl"] = out["payoff"] * out["shares"] - out["cost"]
    out["win"] = out["payoff"].eq(1.0).astype(int)
    return out


def load_base() -> pd.DataFrame:
    pcal = pd.read_csv(PCAL_DETAILS, low_memory=False)
    base = pcal[pcal["strategy"].eq("v1_edge20_baseline_all")].copy()
    base = base.drop_duplicates("candidate_id").reset_index(drop=True)
    keep_cols = [
        "candidate_id",
        "condition_id",
        "market_id",
        "city",
        "target_date",
        "bracket",
        "forecast_source",
        "forecast_peak_source",
        "forecast_max_native",
        "forecast_peak_delta_hours_local",
        "forecast_max_in_bracket",
        "forecast_max_above_bracket_f",
        "forecast_max_below_bracket_f",
        "model_p_yes",
        "market_yes_price",
        "edge",
        "ask",
        "first_seen_ts_utc",
        "decision_snapshot_ts_utc",
        "payoff",
        "period",
        "clob_closed",
        "clob_yes_price",
        "forecast_model",
        "decision_hour_utc",
        "bias_n",
        "bias_mean",
        "bias_p50",
        "bias_p90",
        "hot_tail_pct",
        "hot_tail2_pct",
        "cold_tail_pct",
        "bias_mae",
        "p_cal",
        "p_cal_ev",
        "pcal_model",
    ]
    base = base[[c for c in keep_cols if c in base.columns]].copy()
    base = base.rename(columns={"p_cal": "p_cal_no_city", "p_cal_ev": "p_cal_no_city_ev"})

    city_diag = pcal[pcal["strategy"].eq("v1_edge20_v1_edge20_city_diag_ev_ge_-0.2")].copy()
    city_diag = city_diag.drop_duplicates("candidate_id")
    city_diag = city_diag[["candidate_id", "p_cal", "p_cal_ev"]].rename(
        columns={"p_cal": "p_cal_city_diag", "p_cal_ev": "p_cal_city_diag_ev"}
    )
    out = base.merge(city_diag, on="candidate_id", how="left", validate="one_to_one")

    numeric = [
        "ask",
        "payoff",
        "model_p_yes",
        "market_yes_price",
        "edge",
        "forecast_max_native",
        "forecast_peak_delta_hours_local",
        "forecast_max_in_bracket",
        "forecast_max_above_bracket_f",
        "forecast_max_below_bracket_f",
        "bias_n",
        "bias_mean",
        "bias_p50",
        "bias_p90",
        "hot_tail_pct",
        "hot_tail2_pct",
        "cold_tail_pct",
        "bias_mae",
        "p_cal_no_city",
        "p_cal_no_city_ev",
        "p_cal_city_diag",
        "p_cal_city_diag_ev",
    ]
    for col in numeric:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out["target_date"] = out["target_date"].astype(str)
    out["bracket"] = out["bracket"].astype(str)
    out["source_lc"] = out["forecast_source"].fillna("").astype(str).str.lower()
    out["is_gfs"] = out["source_lc"].str.contains("gfs")
    out["is_ecmwf"] = out["source_lc"].str.contains("ecmwf")
    out = add_fixed_one_dollar_pnl(out)
    return out


def load_metar_tags() -> pd.DataFrame:
    if not METAR_DETAILS.exists():
        return pd.DataFrame()
    metar = pd.read_csv(METAR_DETAILS, low_memory=False)
    metar = metar[metar["rule"].eq("all_low_price_05_20")].copy()
    if metar.empty:
        return metar
    metar["target_date"] = metar["target_date"].astype(str)
    metar["bracket"] = metar["lottery_yes_bracket"].astype(str)
    metar["metar_join_key"] = metar["city"].astype(str) + "|" + metar["target_date"] + "|" + metar["bracket"]
    metar = metar.sort_values(["city", "target_date", "bracket", "decision_snapshot_sort"])
    metar = metar.drop_duplicates("metar_join_key")
    cols = [
        "metar_join_key",
        "decision_hour_local",
        "decision_snapshot_ts_utc",
        "day_regime",
        "intraday_state",
        "running_max_state",
        "moisture_cloud_regime",
        "wind_regime",
        "solar_window",
        "city_family",
        "forecast_gap_to_running_native",
        "gfs_gap_to_running_native",
        "ecmwf_gap_to_running_native",
        "remaining_heat_native",
        "future_break_any",
        "capped_day",
        "forecast_error_native",
        "relative_humidity_pct",
        "dewpoint_depression_f",
        "wind_speed_kt",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "minutes_since_running_max",
        "regime_score",
        "score_open_or_marginal",
        "score_forecast_gap_ge1",
        "score_late_morning",
        "score_fade_or_fresh_high",
        "score_humid_convective",
        "score_light_wind",
        "score_not_capped_busted",
    ]
    out = metar[[c for c in cols if c in metar.columns]].copy()
    out = out.rename(
        columns={
            "decision_hour_local": "metar_decision_hour_local",
            "decision_snapshot_ts_utc": "metar_decision_snapshot_ts_utc",
        }
    )
    for col in [
        "forecast_gap_to_running_native",
        "gfs_gap_to_running_native",
        "ecmwf_gap_to_running_native",
        "remaining_heat_native",
        "forecast_error_native",
        "relative_humidity_pct",
        "dewpoint_depression_f",
        "wind_speed_kt",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "minutes_since_running_max",
        "regime_score",
    ]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def attach_tags(base: pd.DataFrame) -> pd.DataFrame:
    out = base.copy()
    metar = load_metar_tags()
    out["metar_join_key"] = out["city"].astype(str) + "|" + out["target_date"] + "|" + out["bracket"].astype(str)
    if not metar.empty:
        out = out.merge(metar, on="metar_join_key", how="left", validate="many_to_one")
    out["metar_joined"] = out.get("metar_decision_snapshot_ts_utc", pd.Series("", index=out.index)).notna()

    train = out[(out["period"].eq("historical")) & (out["target_date"] < HOLDOUT_START)].copy()
    bias_p90_q66 = float(train["bias_p90"].quantile(2 / 3))
    hot_tail_q66 = float(train["hot_tail_pct"].quantile(2 / 3))
    bias_mean_q66 = float(train["bias_mean"].quantile(2 / 3))

    out["source_aware_v3"] = (out["is_gfs"] & out["ask"].between(0.05, 0.15)) | (
        out["is_ecmwf"] & out["ask"].between(0.10, 0.20)
    )
    out["source_aware_wide"] = (out["is_gfs"] & out["ask"].between(0.05, 0.15)) | (
        out["is_ecmwf"] & out["ask"].between(0.08, 0.20)
    )
    out["pcal_no_city_ev_ge_0_2"] = out["p_cal_no_city_ev"].ge(0.2)
    out["pcal_no_city_ev_ge_0_5"] = out["p_cal_no_city_ev"].ge(0.5)
    out["pcal_city_diag_ev_ge_0_2"] = out["p_cal_city_diag_ev"].ge(0.2)
    out["pcal_city_diag_ev_ge_0_5"] = out["p_cal_city_diag_ev"].ge(0.5)
    out["station_bias_p90_high"] = out["bias_p90"].ge(bias_p90_q66)
    out["station_hot_tail_high"] = out["hot_tail_pct"].ge(hot_tail_q66)
    out["station_bias_mean_high"] = out["bias_mean"].ge(bias_mean_q66)
    out["metar_regime_score_ge_4"] = pd.to_numeric(out.get("regime_score"), errors="coerce").ge(4)
    out["metar_regime_score_ge_5"] = pd.to_numeric(out.get("regime_score"), errors="coerce").ge(5)
    out["metar_open_late_lightwind"] = (
        out.get("day_regime", pd.Series("", index=out.index)).astype(str).eq("day_open_runway")
        & out.get("solar_window", pd.Series("", index=out.index)).astype(str).eq("late_morning")
        & out.get("wind_regime", pd.Series("", index=out.index)).astype(str).eq("light_wind")
    )
    out["metar_not_capped_busted"] = ~out.get("day_regime", pd.Series("", index=out.index)).astype(str).isin(
        ["day_forecast_capped", "day_forecast_busted"]
    )

    out["score_source_station_no_city"] = (
        out["source_aware_v3"].astype(int)
        + out["station_bias_p90_high"].astype(int)
        + out["station_hot_tail_high"].astype(int)
        + out["pcal_no_city_ev_ge_0_2"].astype(int)
    )
    out["score_source_station_citydiag"] = (
        out["source_aware_v3"].astype(int)
        + out["station_bias_p90_high"].astype(int)
        + out["station_hot_tail_high"].astype(int)
        + out["pcal_city_diag_ev_ge_0_5"].astype(int)
    )
    out["score_integrated_with_metar"] = (
        out["score_source_station_no_city"]
        + out["metar_regime_score_ge_4"].astype(int)
        + out["metar_not_capped_busted"].astype(int)
    )

    out.attrs["thresholds"] = {
        "train_bias_p90_q66": bias_p90_q66,
        "train_hot_tail_pct_q66": hot_tail_q66,
        "train_bias_mean_q66": bias_mean_q66,
    }
    return out


def strategy_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "baseline_v1_all": pd.Series(True, index=df.index),
        "source_aware_v3": df["source_aware_v3"],
        "station_bias_p90_high": df["station_bias_p90_high"],
        "station_hot_tail_high": df["station_hot_tail_high"],
        "pcal_no_city_ev_ge_0_2": df["pcal_no_city_ev_ge_0_2"],
        "pcal_no_city_ev_ge_0_5": df["pcal_no_city_ev_ge_0_5"],
        "pcal_city_diag_ev_ge_0_2_diag_only": df["pcal_city_diag_ev_ge_0_2"],
        "pcal_city_diag_ev_ge_0_5_diag_only": df["pcal_city_diag_ev_ge_0_5"],
        "source_station_no_city_score_ge_2": df["score_source_station_no_city"].ge(2),
        "source_station_no_city_score_ge_3": df["score_source_station_no_city"].ge(3),
        "source_station_citydiag_score_ge_3_diag_only": df["score_source_station_citydiag"].ge(3),
        "source_station_citydiag_score_ge_4_diag_only": df["score_source_station_citydiag"].ge(4),
        "metar_joined_source_aware": df["source_aware_v3"] & df["metar_joined"],
        "metar_score_ge_5": df["metar_regime_score_ge_5"],
        "source_aware_and_metar_score_ge_4": df["source_aware_v3"] & df["metar_regime_score_ge_4"],
        "integrated_with_metar_score_ge_4": df["score_integrated_with_metar"].ge(4),
        "integrated_with_metar_score_ge_5": df["score_integrated_with_metar"].ge(5),
        "metar_open_late_lightwind": df["metar_open_late_lightwind"],
    }


def period_frames(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    historical = df[df["period"].eq("historical")]
    return {
        "train_pre_2026_06_21": historical[historical["target_date"] < HOLDOUT_START],
        "holdout_2026_06_21_26": historical[historical["target_date"] >= HOLDOUT_START],
        "recent_2026_06_08_plus": historical[historical["target_date"] >= RECENT_START],
        "forward_2026_06_27_30": df[df["period"].eq("forward")],
        "full_with_forward": df,
    }


def summarize(g: pd.DataFrame, strategy: str, period: str) -> dict[str, Any]:
    out: dict[str, Any] = {"strategy": strategy, "period": period}
    if g.empty:
        out.update({"rows": 0, "dates": 0, "cities": 0})
        return out
    daily = g.groupby("target_date", as_index=False).agg(
        rows=("pnl", "size"),
        wins=("win", "sum"),
        cost=("cost", "sum"),
        pnl=("pnl", "sum"),
    )
    daily["roi"] = daily["pnl"] / daily["cost"]
    ci_low, ci_high = block_bootstrap_ci(daily)
    top_removed = g.sort_values("pnl", ascending=False).iloc[1:]
    top3_removed = g.sort_values("pnl", ascending=False).iloc[3:]
    top5_removed = g.sort_values("pnl", ascending=False).iloc[5:]
    top10_removed = g.sort_values("pnl", ascending=False).iloc[10:]

    def roi_for(frame: pd.DataFrame) -> float | None:
        cost = float(frame["cost"].sum())
        if cost <= 0:
            return None
        return float(frame["pnl"].sum() / cost)

    out.update(
        {
            "rows": int(len(g)),
            "dates": int(g["target_date"].nunique()),
            "cities": int(g["city"].nunique()),
            "wins": int(g["win"].sum()),
            "win_rate": float(g["win"].mean()),
            "avg_ask": float(g["ask"].mean()),
            "avg_edge": float(g["edge"].mean()),
            "avg_pcal_no_city_ev": float(g["p_cal_no_city_ev"].mean()),
            "avg_pcal_city_diag_ev": float(g["p_cal_city_diag_ev"].mean()),
            "metar_join_rate": float(g["metar_joined"].mean()),
            "cost": float(g["cost"].sum()),
            "pnl": float(g["pnl"].sum()),
            "roi": roi_for(g),
            "roi_ci_low": ci_low,
            "roi_ci_high": ci_high,
            "top_trade_removed_roi": roi_for(top_removed),
            "top3_removed_roi": roi_for(top3_removed),
            "top5_removed_roi": roi_for(top5_removed),
            "top10_removed_roi": roi_for(top10_removed),
            "losing_days": int((daily["pnl"] < 0).sum()),
            "roi_le_minus_50_days": int((daily["roi"] <= -0.50).sum()),
            "max_daily_loss": float(daily["pnl"].min()),
            "pnl_per_active_day": float(g["pnl"].sum() / g["target_date"].nunique()),
        }
    )
    return out


def evaluate(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summary_rows: list[dict[str, Any]] = []
    selected_rows: list[pd.DataFrame] = []
    daily_rows: list[pd.DataFrame] = []
    complement_rows: list[dict[str, Any]] = []
    masks = strategy_masks(df)
    for strategy, mask in masks.items():
        selected = df[mask.fillna(False)].copy()
        selected["strategy"] = strategy
        if not selected.empty:
            selected_rows.append(selected)
        for period, period_df in period_frames(df).items():
            sub = selected[selected.index.isin(period_df.index)].copy()
            summary_rows.append(summarize(sub, strategy, period))
            comp = period_df[~period_df.index.isin(sub.index)].copy()
            if len(sub) and len(comp):
                selected_roi = float(sub["pnl"].sum() / sub["cost"].sum())
                comp_roi = float(comp["pnl"].sum() / comp["cost"].sum())
                complement_rows.append(
                    {
                        "strategy": strategy,
                        "period": period,
                        "selected_rows": int(len(sub)),
                        "selected_roi": selected_roi,
                        "complement_rows": int(len(comp)),
                        "complement_roi": comp_roi,
                        "roi_delta_vs_complement": selected_roi - comp_roi,
                    }
                )
            if not sub.empty:
                d = sub.groupby("target_date", as_index=False).agg(
                    rows=("pnl", "size"), wins=("win", "sum"), cost=("cost", "sum"), pnl=("pnl", "sum")
                )
                d["roi"] = d["pnl"] / d["cost"]
                d["strategy"] = strategy
                d["period"] = period
                daily_rows.append(d)
    return (
        pd.DataFrame(summary_rows),
        pd.concat(selected_rows, ignore_index=True, sort=False) if selected_rows else pd.DataFrame(),
        pd.concat(daily_rows, ignore_index=True, sort=False) if daily_rows else pd.DataFrame(),
        pd.DataFrame(complement_rows),
    )


def contribution(selected: pd.DataFrame, strategies: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    dims = ["city", "forecast_source", "forecast_model", "day_regime", "intraday_state", "wind_regime", "moisture_cloud_regime"]
    for strategy in strategies:
        sub = selected[selected["strategy"].eq(strategy)].copy()
        if sub.empty:
            continue
        for dim in dims:
            if dim not in sub.columns:
                continue
            for level, g in sub.groupby(dim, dropna=False):
                if len(g) < 3:
                    continue
                rows.append(
                    {
                        "strategy": strategy,
                        "dimension": dim,
                        "level": str(level),
                        "rows": int(len(g)),
                        "dates": int(g["target_date"].nunique()),
                        "cities": int(g["city"].nunique()),
                        "win_rate": float(g["win"].mean()),
                        "avg_ask": float(g["ask"].mean()),
                        "pnl": float(g["pnl"].sum()),
                        "roi": float(g["pnl"].sum() / g["cost"].sum()),
                    }
                )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["strategy", "dimension", "pnl"], ascending=[True, True, False])


def p2_reference() -> pd.DataFrame:
    if not P2_SUMMARY.exists():
        return pd.DataFrame()
    df = pd.read_csv(P2_SUMMARY)
    keep = df[
        df["scope"].eq("expanding_forward")
        & df["method"].isin(["fusion_city_blend", "fusion_numeric_blend", "fusion_context_blend"])
        & df["edge_threshold"].eq(0.02)
    ].copy()
    return keep.sort_values("roi", ascending=False)


def render_report(
    rows: pd.DataFrame,
    summary: pd.DataFrame,
    daily: pd.DataFrame,
    complement: pd.DataFrame,
    contrib: pd.DataFrame,
    p2: pd.DataFrame,
    thresholds: dict[str, float],
    data_snapshot: dict[str, Any],
) -> str:
    focus = [
        "baseline_v1_all",
        "source_aware_v3",
        "source_station_no_city_score_ge_2",
        "source_station_no_city_score_ge_3",
        "source_station_citydiag_score_ge_3_diag_only",
        "source_station_citydiag_score_ge_4_diag_only",
        "source_aware_and_metar_score_ge_4",
        "integrated_with_metar_score_ge_4",
        "metar_open_late_lightwind",
    ]
    summary_focus = summary[
        summary["strategy"].isin(focus)
        & summary["period"].isin(["train_pre_2026_06_21", "holdout_2026_06_21_26", "forward_2026_06_27_30"])
    ].copy()
    summary_focus["strategy"] = pd.Categorical(summary_focus["strategy"], categories=focus, ordered=True)
    summary_focus = summary_focus.sort_values(["strategy", "period"])

    full_rank = summary[summary["period"].eq("full_with_forward")].copy().sort_values("roi", ascending=False)
    holdout_comp = complement[complement["period"].eq("holdout_2026_06_21_26")].copy().sort_values(
        "roi_delta_vs_complement", ascending=False
    )
    forward_comp = complement[complement["period"].eq("forward_2026_06_27_30")].copy().sort_values(
        "roi_delta_vs_complement", ascending=False
    )
    daily_focus = daily[daily["strategy"].isin(["baseline_v1_all", "source_aware_v3", "source_station_no_city_score_ge_2"])].copy()
    daily_focus = daily_focus[daily_focus["period"].isin(["forward_2026_06_27_30", "holdout_2026_06_21_26"])]

    metar_join = rows["metar_joined"].mean() if len(rows) else 0.0
    lines = [
        "# Low-Price YES Integrated Tail v2",
        "",
        f"Generated: {now_utc()}",
        "",
        "## Verdict",
        "",
        "`shadow_candidate` for telemetry only; no live selector/size change.",
        "",
        "The core finding is boring but useful: source-aware v3 remains the best broad "
        "forecast-tail sleeve, while METAR/regime confirmation still does not improve "
        "the same denominator.  Station-bias and p_cal are valuable as forward tags, "
        "but city-diagnostic p_cal is too likely to be city/source memory to promote.",
        "",
        "Recommended V2 shadow record:",
        "",
        "```text",
        "base candidate: V1 no-dust low-price YES, edge>=0.20, ask 0.05..0.20",
        "shadow tags: source_aware_v3, station_bias_p90_high, station_hot_tail_high,",
        "             p_cal_no_city/city_diag EV, live METAR observation summary,",
        "             simplified METAR regime score and blocker/executable status",
        "shadow decision: diagnostic only; no order routing, no size-up",
        "```",
        "",
        "```text",
        "significance=PARTIAL (source-aware historical CI passes; integrated no-city CI does not)",
        "baseline=PARTIAL (source-aware improves headline; no-city integrated not stable vs complement)",
        "forward=PARTIAL/LOW_N (6/27..6/30 only 19 baseline rows)",
        "conclusion=shadow_candidate; keep V1 tiny live, add V2 shadow telemetry only",
        "```",
        "",
        "## Data Snapshot",
        "",
        f"- Sync/rebuild: {data_snapshot.get('sync_rebuild')}",
        f"- Fact signal candidates: {data_snapshot.get('fact_signal_candidates')}",
        f"- Fact trades: {data_snapshot.get('fact_trades')}",
        f"- Base denominator rows: {len(rows)}; dates {rows['target_date'].min()}..{rows['target_date'].max()}; cities {rows['city'].nunique()}.",
        f"- METAR/regime same-bracket join coverage: {metar_join:.1%}. Missing METAR join means no same city-date-bracket intraday low-price tail row in v2 atlas.",
        f"- Station thresholds are train-only q66: bias_p90>={thresholds['train_bias_p90_q66']:.3f}, hot_tail_pct>={thresholds['train_hot_tail_pct_q66']:.3f}.",
        "",
        "## Main A/B",
        "",
        md_table(
            summary_focus,
            [
                ("strategy", "strategy"),
                ("period", "period"),
                ("rows", "rows"),
                ("dates", "dates"),
                ("cities", "cities"),
                ("win_rate", "win"),
                ("avg_ask", "avg ask"),
                ("roi", "ROI"),
                ("roi_ci_low", "CI low"),
                ("roi_ci_high", "CI high"),
                ("top5_removed_roi", "top5 removed"),
                ("losing_days", "losing days"),
                ("roi_le_minus_50_days", "<= -50% days"),
                ("max_daily_loss", "max daily loss"),
                ("metar_join_rate", "METAR join"),
            ],
            max_rows=80,
        ),
        "",
        "## Full-Window Rank",
        "",
        md_table(
            full_rank,
            [
                ("strategy", "strategy"),
                ("rows", "rows"),
                ("dates", "dates"),
                ("cities", "cities"),
                ("win_rate", "win"),
                ("avg_ask", "avg ask"),
                ("roi", "ROI"),
                ("roi_ci_low", "CI low"),
                ("roi_ci_high", "CI high"),
                ("top5_removed_roi", "top5 removed"),
                ("pnl_per_active_day", "$/active day"),
            ],
            max_rows=25,
        ),
        "",
        "## Complement Check",
        "",
        "A selector is interesting only if selected rows beat the rows it leaves behind on the same denominator.  "
        "This is where no-city p_cal and station-bias tags still look unstable.",
        "",
        "### Holdout 2026-06-21..26",
        "",
        md_table(
            holdout_comp,
            [
                ("strategy", "strategy"),
                ("selected_rows", "selected"),
                ("selected_roi", "selected ROI"),
                ("complement_rows", "complement"),
                ("complement_roi", "complement ROI"),
                ("roi_delta_vs_complement", "delta"),
            ],
            max_rows=25,
        ),
        "",
        "### Forward 2026-06-27..30",
        "",
        md_table(
            forward_comp,
            [
                ("strategy", "strategy"),
                ("selected_rows", "selected"),
                ("selected_roi", "selected ROI"),
                ("complement_rows", "complement"),
                ("complement_roi", "complement ROI"),
                ("roi_delta_vs_complement", "delta"),
            ],
            max_rows=25,
        ),
        "",
        "## Daily Distribution",
        "",
        md_table(
            daily_focus.sort_values(["strategy", "period", "target_date"]),
            [
                ("strategy", "strategy"),
                ("period", "period"),
                ("target_date", "date"),
                ("rows", "rows"),
                ("wins", "wins"),
                ("cost", "cost"),
                ("pnl", "PnL"),
                ("roi", "ROI"),
            ],
            max_rows=80,
        ),
        "",
        "## P2 Expression Selector Context",
        "",
        "The Tmax distribution P2 branch is not the same denominator as low-price YES.  It is the broader expression selector line "
        "for current YES/current NO/d1 NO/d2 NO.  It should be shadowed as expression telemetry, not merged into this low-price live sleeve yet.",
        "",
        md_table(
            p2,
            [
                ("method", "method"),
                ("selected_rows", "rows"),
                ("dates", "dates"),
                ("cities", "cities"),
                ("roi", "ROI"),
                ("roi_ci_low", "CI low"),
                ("roi_ci_high", "CI high"),
                ("current_yes_rows", "YES"),
                ("current_no_rows", "current NO"),
                ("d1_no_rows", "d1 NO"),
                ("d2_no_rows", "d2 NO"),
            ],
            max_rows=20,
        ),
        "",
        "## Contribution Slices",
        "",
        md_table(
            contrib,
            [
                ("strategy", "strategy"),
                ("dimension", "dimension"),
                ("level", "level"),
                ("rows", "rows"),
                ("dates", "dates"),
                ("win_rate", "win"),
                ("avg_ask", "avg ask"),
                ("pnl", "PnL"),
                ("roi", "ROI"),
            ],
            max_rows=80,
        ),
        "",
        "## Interpretation",
        "",
        "- V1 is still mostly a forecast/model/market mispricing sleeve, not an observation-led METAR strategy.",
        "- METAR/regime is useful forward telemetry because it tells us whether a ticket had physical runway, but historical same-denominator evidence does not justify waiting for METAR before entry.",
        "- `source_aware_v3` is the broadest useful tag; it is still not clean alpha because forecast source is partially city/source-policy coupled.",
        "- Station-bias tags are mechanism-plausible.  The high p90/hot-tail rows catch convex winners, but complement and forward checks are noisy.",
        "- City-diagnostic p_cal looks strongest numerically.  That is exactly why it stays diagnostic-only until fresh forward rows prove it is not city memory.",
        "- P2 distribution EV is the right architecture for a future expression selector, but it needs executable replay and more forward dates before it can replace or route V1.",
        "",
        "## Shadow Spec",
        "",
        "Implement `low_price_yes_integrated_tail_shadow_v2` as a zero-notional journal over current V1 candidates.  Each row should record:",
        "",
        "- V1 candidate fields: city/date/bracket/source/ask/model_p/edge/snapshot.",
        "- source tags: `source_aware_v3`, `source_aware_wide`.",
        "- station tags: `bias_p90_asof`, `hot_tail_pct_asof`, `p_cal_no_city`, `p_cal_city_diag`, EV fields.",
        "- live observation tags: observation cache status, current/running temp, forecast-to-running gap, humidity/wind/trend/minutes-since-high.",
        "- simplified METAR/regime score tags for forward analysis.",
        "- execution status: token id available, fresh book ask/depth, blocked reason.",
        "",
        "This shadow should not submit orders and should not alter the existing `$1` V1 live sleeve.",
        "",
        "## Artifacts",
        "",
        f"- Enriched rows: `{rel(OUT_DIR / 'enriched_rows.csv')}`",
        f"- Strategy summary: `{rel(OUT_DIR / 'strategy_summary.csv')}`",
        f"- Daily summary: `{rel(OUT_DIR / 'daily_summary.csv')}`",
        f"- Complement check: `{rel(OUT_DIR / 'complement_check.csv')}`",
        f"- Contribution slices: `{rel(OUT_DIR / 'contribution_slices.csv')}`",
        f"- JSON: `{rel(OUT_JSON)}`",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = load_base()
    rows = attach_tags(base)
    thresholds = rows.attrs.get("thresholds", {})
    summary, selected, daily, complement = evaluate(rows)
    contrib = contribution(
        selected,
        [
            "source_aware_v3",
            "source_station_no_city_score_ge_2",
            "source_station_citydiag_score_ge_3_diag_only",
            "source_aware_and_metar_score_ge_4",
        ],
    )
    p2 = p2_reference()

    rows.to_csv(OUT_DIR / "enriched_rows.csv", index=False)
    summary.to_csv(OUT_DIR / "strategy_summary.csv", index=False)
    daily.to_csv(OUT_DIR / "daily_summary.csv", index=False)
    complement.to_csv(OUT_DIR / "complement_check.csv", index=False)
    contrib.to_csv(OUT_DIR / "contribution_slices.csv", index=False)
    p2.to_csv(OUT_DIR / "p2_expression_context.csv", index=False)

    data_snapshot = {
        "sync_rebuild": "scripts/ops/sync_weather_remote.sh + scripts/weather_dashboard/run_stack.sh completed on 2026-07-02",
        "fact_signal_candidates": "42652 rows, event_date 2026-05-05..2026-07-04, fact_built_at_utc 2026-07-02T15:12:46Z",
        "fact_trades": "4414 rows, target_date 2026-05-06..2026-07-01, fact_built_at_utc 2026-07-02T15:12:24Z",
    }
    payload = {
        "generated_at_utc": now_utc(),
        "verdict": "shadow_candidate",
        "data_snapshot": data_snapshot,
        "thresholds": thresholds,
        "funnel": {
            "base_rows": int(len(rows)),
            "base_dates": int(rows["target_date"].nunique()),
            "base_cities": int(rows["city"].nunique()),
            "metar_joined_rows": int(rows["metar_joined"].sum()),
            "metar_join_rate": float(rows["metar_joined"].mean()),
        },
        "artifacts": {
            "enriched_rows": rel(OUT_DIR / "enriched_rows.csv"),
            "strategy_summary": rel(OUT_DIR / "strategy_summary.csv"),
            "daily_summary": rel(OUT_DIR / "daily_summary.csv"),
            "complement_check": rel(OUT_DIR / "complement_check.csv"),
            "contribution_slices": rel(OUT_DIR / "contribution_slices.csv"),
            "p2_expression_context": rel(OUT_DIR / "p2_expression_context.csv"),
            "report": rel(OUT_MD),
        },
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_report(rows, summary, daily, complement, contrib, p2, thresholds, data_snapshot), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
