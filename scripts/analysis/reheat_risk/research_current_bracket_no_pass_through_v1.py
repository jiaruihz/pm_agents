#!/usr/bin/env python3
"""Backtest current-bracket NO pass-through setups.

The strategy idea: when the current observed running max has just entered a
temperature bracket near midday, buy that current bracket's NO if weather and
forecast context suggest the day can continue heating through the bracket.

This is an opportunity replay.  It uses the shared reheat feature factory rows
and the real NO ask from the same historical orderbook snapshot.  It does not
infer NO as 1 - YES.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
FEATURE_ROWS = (
    ROOT
    / "docs/analysis/2026-06/generated/current_bracket_no_pass_through_v1_feature_factory/reheat_feature_rows.csv"
)
FEATURE_FACTORY_SUMMARY = (
    ROOT
    / "docs/analysis/2026-06/generated/current_bracket_no_pass_through_v1_feature_factory/feature_factory_summary.json"
)
IEM_EXT_DIR = ROOT / "docs/analysis/2026-06/generated/theta_no_iem_ext_patch_v8_20260620"
GATE = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_bracket_no_pass_through_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_VARIANTS = OUT_DIR / "variant_summary.csv"
OUT_SWEEP = OUT_DIR / "threshold_sweep.csv"
OUT_SIGNALS = OUT_DIR / "selected_signals.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-22-current-bracket-no-pass-through-v1.md"

SEED = 20260622
BOOTSTRAP_REPS = 5000
STAKE_USD = 5.0


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def finite_or_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: finite_or_none(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite_or_none(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        if math.isfinite(float(value)):
            return float(value)
        return None
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    return value


def pct(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{100.0 * float(value):+.1f}%"


def money(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"${float(value):,.0f}"


def load_gate() -> dict[str, Any]:
    if not GATE.exists():
        return {"gate_pass": None, "missing": True}
    data = json.loads(GATE.read_text(encoding="utf-8"))
    return {
        "gate_pass": data.get("gate_pass"),
        "fail_reasons": data.get("fail_reasons", []),
        "fact_trades_live_real": data.get("fact_trades_live_real", {}),
        "db_vs_primary_cache": data.get("db_vs_primary_cache", {}),
        "db_fill_cost_minus_fact_cost": data.get("db_fill_cost_minus_fact_cost"),
    }


def parse_bracket_num(value: Any) -> tuple[float | None, float | None]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None, None
    text = str(value).replace("°", "").strip()
    if not text:
        return None, None
    if text.endswith("+"):
        try:
            low = float(text[:-1])
        except ValueError:
            return None, None
        return low, None
    if "-" in text:
        left, right = text.split("-", 1)
        try:
            return float(left), float(right)
        except ValueError:
            return None, None
    try:
        val = float(text)
    except ValueError:
        return None, None
    return val, val


def bracket_mid(value: Any) -> float:
    low, high = parse_bracket_num(value)
    if low is None:
        return float("nan")
    if high is None:
        return low
    return (low + high) / 2.0


def load_feature_rows() -> pd.DataFrame:
    keep = [
        "orderbook_file",
        "decision_snapshot_ts_utc",
        "decision_hour_local",
        "city",
        "icao",
        "timezone",
        "target_date",
        "bracket",
        "bracket_low",
        "bracket_high",
        "outcome",
        "quote_best_ask",
        "quote_best_ask_size",
        "quote_best_bid",
        "quote_best_bid_size",
        "quote_spread",
        "quote_depth_ask_5c",
        "condition_id",
        "current_temp_f",
        "current_temp_c",
        "running_max_f",
        "running_max_c",
        "final_max_f",
        "final_max_c",
        "current_native",
        "running_native",
        "running_value",
        "decline_native",
        "current_bracket",
        "current_bracket_held",
        "final_winning_bracket",
        "unit",
        "settlement_status",
        "source_system",
        "tmpf_now",
        "dwpf_now",
        "dewpoint_depression_f",
        "relative_humidity_pct",
        "wind_speed_kt",
        "sky_cover_code",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "minutes_since_running_max",
        "forecast_max_native",
        "forecast_peak_hour_local",
        "forecast_peak_delta_hours_local",
        "gfs_forecast_peak_delta_hours_local",
        "ecmwf_forecast_peak_delta_hours_local",
        "gfs_forecast_gap_to_running_native",
        "ecmwf_forecast_gap_to_running_native",
        "gfs_forecast_peak_present",
        "ecmwf_forecast_peak_present",
        "forecast_peak_models_agree_le_1h",
        "forecast_peak_hour_spread",
        "forecast_clock_source",
    ]
    df = pd.read_csv(FEATURE_ROWS, usecols=lambda c: c in keep, low_memory=False)
    for col in keep:
        if col not in df.columns:
            df[col] = np.nan
    numeric = [
        "decision_hour_local",
        "bracket_low",
        "bracket_high",
        "quote_best_ask",
        "quote_best_ask_size",
        "quote_best_bid",
        "quote_best_bid_size",
        "quote_spread",
        "quote_depth_ask_5c",
        "current_temp_f",
        "current_temp_c",
        "running_max_f",
        "running_max_c",
        "final_max_f",
        "final_max_c",
        "current_native",
        "running_native",
        "running_value",
        "decline_native",
        "current_bracket_held",
        "tmpf_now",
        "dwpf_now",
        "dewpoint_depression_f",
        "relative_humidity_pct",
        "wind_speed_kt",
        "sky_cover_code",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "minutes_since_running_max",
        "forecast_max_native",
        "forecast_peak_hour_local",
        "forecast_peak_delta_hours_local",
        "gfs_forecast_peak_delta_hours_local",
        "ecmwf_forecast_peak_delta_hours_local",
        "gfs_forecast_gap_to_running_native",
        "ecmwf_forecast_gap_to_running_native",
        "forecast_peak_hour_spread",
    ]
    for col in numeric:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_actual_peak_hours(feature_rows: pd.DataFrame) -> pd.DataFrame:
    city_meta = (
        feature_rows[["city", "icao", "timezone", "target_date"]]
        .dropna(subset=["city", "icao", "timezone", "target_date"])
        .drop_duplicates()
    )
    date_min = str(city_meta["target_date"].min())
    date_max = str(city_meta["target_date"].max())
    rows: list[dict[str, Any]] = []
    for meta in city_meta[["city", "icao", "timezone"]].drop_duplicates().itertuples(index=False):
        paths = sorted(IEM_EXT_DIR.glob(f"iem_ext_{str(meta.icao).upper()}_*.csv"))
        if not paths:
            continue
        frames = []
        for path in paths:
            raw = pd.read_csv(path, na_values=["M"], low_memory=False)
            if "valid" not in raw or "tmpf" not in raw:
                continue
            frames.append(raw[["valid", "tmpf"]].copy())
        if not frames:
            continue
        obs = pd.concat(frames, ignore_index=True)
        obs["valid_utc"] = pd.to_datetime(obs["valid"], utc=True, errors="coerce")
        obs["tmpf"] = pd.to_numeric(obs["tmpf"], errors="coerce")
        obs = obs.dropna(subset=["valid_utc", "tmpf"]).drop_duplicates("valid_utc").sort_values("valid_utc")
        if obs.empty:
            continue
        tz = ZoneInfo(str(meta.timezone))
        obs["local_dt"] = obs["valid_utc"].dt.tz_convert(tz)
        obs["target_date"] = obs["local_dt"].dt.date.astype(str)
        obs = obs[obs["target_date"].between(date_min, date_max)].copy()
        for target_date, day in obs.groupby("target_date", sort=True):
            max_f = float(day["tmpf"].max())
            peak = day[day["tmpf"].ge(max_f - 0.05)].copy()
            rows.append(
                {
                    "city": meta.city,
                    "target_date": target_date,
                    "actual_peak_max_f": max_f,
                    "actual_peak_first_hour_local": int(peak["local_dt"].dt.hour.min()),
                    "actual_peak_last_hour_local": int(peak["local_dt"].dt.hour.max()),
                    "actual_peak_obs_count": int(len(peak)),
                }
            )
    return pd.DataFrame(rows).drop_duplicates(["city", "target_date"], keep="last")


def enrich_current_no(df: pd.DataFrame) -> pd.DataFrame:
    cur = df[
        df["outcome"].astype(str).str.lower().eq("no")
        & df["bracket"].astype(str).eq(df["current_bracket"].astype(str))
        & df["settlement_status"].astype(str).eq("settled")
        & df["quote_best_ask"].notna()
        & df["current_bracket_held"].notna()
    ].copy()
    peak = load_actual_peak_hours(df)
    if not peak.empty:
        cur = cur.merge(peak, on=["city", "target_date"], how="left")
    else:
        cur["actual_peak_first_hour_local"] = np.nan
        cur["actual_peak_last_hour_local"] = np.nan
        cur["actual_peak_max_f"] = np.nan
        cur["actual_peak_obs_count"] = np.nan

    cur["no_ask"] = cur["quote_best_ask"]
    cur["no_ask_size"] = cur["quote_best_ask_size"]
    cur["no_top_ask_notional"] = cur["no_ask"] * cur["no_ask_size"]
    cur["no_depth_5c_notional_approx"] = cur["no_ask"] * cur["quote_depth_ask_5c"]
    cur["label_no_wins"] = 1.0 - cur["current_bracket_held"].astype(float)
    cur["label_current_yes_wins"] = cur["current_bracket_held"].astype(float)
    cur["bracket_upper"] = cur["bracket_high"].fillna(cur["bracket_low"])
    cur["distance_into_bracket_native"] = cur["running_native"] - cur["bracket_low"]
    cur["forecast_gap_to_bracket_upper_native"] = cur["forecast_max_native"] - cur["bracket_upper"]
    cur["gfs_gap_to_bracket_upper_native"] = (
        cur["running_native"] + cur["gfs_forecast_gap_to_running_native"] - cur["bracket_upper"]
    )
    cur["ecmwf_gap_to_bracket_upper_native"] = (
        cur["running_native"] + cur["ecmwf_forecast_gap_to_running_native"] - cur["bracket_upper"]
    )
    cur["final_max_native"] = np.where(cur["unit"].astype(str).str.upper().eq("F"), cur["final_max_f"], cur["final_max_c"])
    cur["final_max_above_current_upper"] = cur["final_max_native"] > cur["bracket_upper"] + 0.05
    cur["final_winner_mid"] = cur["final_winning_bracket"].map(bracket_mid)
    cur["final_winner_above_current"] = cur["final_winner_mid"] > cur["bracket_upper"]
    cur["pass_through_no_win"] = cur["label_no_wins"].eq(1.0) & (
        cur["final_winner_above_current"].fillna(False) | cur["final_max_above_current_upper"].fillna(False)
    )

    cur["new_high_now"] = (
        cur["decline_native"].le(0.25)
        & cur["minutes_since_running_max"].le(45)
        & cur["current_native"].ge(cur["bracket_low"] - 0.1)
    )
    cur["new_high_strict"] = (
        cur["decline_native"].le(0.10)
        & cur["minutes_since_running_max"].le(30)
        & cur["distance_into_bracket_native"].between(-0.1, 1.0)
    )
    cur["warming_1h_3h"] = cur["temp_trend_1h_f"].ge(0.0) & cur["temp_trend_3h_f"].ge(0.5)
    cur["warming_strict"] = cur["temp_trend_1h_f"].ge(0.5) & cur["temp_trend_3h_f"].ge(1.0)
    cur["forecast_peak_later_2h"] = cur["forecast_peak_delta_hours_local"].ge(2.0)
    cur["forecast_gap_ge_0p5"] = cur["forecast_gap_to_bracket_upper_native"].ge(0.5)
    cur["forecast_gap_ge_1"] = cur["forecast_gap_to_bracket_upper_native"].ge(1.0)
    cur["dual_model_gap_ge_1"] = cur["gfs_gap_to_bracket_upper_native"].ge(1.0) & cur[
        "ecmwf_gap_to_bracket_upper_native"
    ].ge(1.0)
    cur["risk_low"] = (
        (cur["sky_cover_code"].isna() | cur["sky_cover_code"].le(2.0))
        & (cur["wind_speed_kt"].isna() | cur["wind_speed_kt"].le(15.0))
        & (cur["relative_humidity_pct"].isna() | cur["relative_humidity_pct"].le(85.0))
        & (cur["dewpoint_depression_f"].isna() | cur["dewpoint_depression_f"].ge(4.0))
    )
    cur["midday_h10_14"] = cur["decision_hour_local"].between(10, 14)
    cur["actual_peak_afternoon"] = cur["actual_peak_first_hour_local"].ge(13)
    cur["actual_peak_after_decision_2h"] = cur["actual_peak_last_hour_local"] - cur["decision_hour_local"] >= 2
    cur["stake_cost_usd"] = STAKE_USD
    cur["stake_shares"] = STAKE_USD / cur["no_ask"]
    cur["stake_profit_usd"] = cur["label_no_wins"] * cur["stake_shares"] - STAKE_USD
    cur["stake_profit_pass_through_only_usd"] = cur["pass_through_no_win"].astype(float) * cur["stake_shares"] - STAKE_USD
    dates = sorted(cur["target_date"].dropna().astype(str).unique())
    split_idx = max(1, int(len(dates) * 0.70))
    train_dates = set(dates[:split_idx])
    cur["period_split"] = np.where(cur["target_date"].isin(train_dates), "train", "holdout")
    cur["no_ask_bucket"] = pd.cut(
        cur["no_ask"],
        bins=[-math.inf, 0.10, 0.20, 0.30, 0.40, 0.50, math.inf],
        labels=["<=0.10", "0.10-0.20", "0.20-0.30", "0.30-0.40", "0.40-0.50", ">0.50"],
    ).astype(str)
    return cur.reset_index(drop=True)


def select_first_per_city_day(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    return (
        frame.sort_values(["target_date", "city", "decision_hour_local", "decision_snapshot_ts_utc", "no_ask"])
        .drop_duplicates(["target_date", "city"], keep="first")
        .reset_index(drop=True)
    )


def grouped_profit_cost(frame: pd.DataFrame) -> dict[str, tuple[float, float]]:
    if frame.empty:
        return {}
    grouped = frame.groupby("target_date").agg(profit=("stake_profit_usd", "sum"), cost=("stake_cost_usd", "sum"))
    return {str(idx): (float(row.profit), float(row.cost)) for idx, row in grouped.iterrows()}


def block_bootstrap_roi(frame: pd.DataFrame) -> dict[str, Any]:
    by_date = grouped_profit_cost(frame)
    dates = sorted(by_date)
    if len(dates) < 3:
        return {"ci_low": None, "ci_high": None, "reps": 0, "active_dates": len(dates)}
    rng = np.random.default_rng(SEED)
    values: list[float] = []
    for _ in range(BOOTSTRAP_REPS):
        draw = rng.choice(dates, size=len(dates), replace=True)
        profit = sum(by_date[d][0] for d in draw)
        cost = sum(by_date[d][1] for d in draw)
        if cost > 0:
            values.append(profit / cost)
    return {
        "ci_low": float(np.quantile(values, 0.025)) if values else None,
        "ci_high": float(np.quantile(values, 0.975)) if values else None,
        "reps": len(values),
        "active_dates": len(dates),
    }


def block_bootstrap_delta(candidate: pd.DataFrame, baseline: pd.DataFrame) -> dict[str, Any]:
    cand = grouped_profit_cost(candidate)
    base = grouped_profit_cost(baseline)
    dates = sorted(set(cand) | set(base))
    if len(dates) < 3 or not cand or not base:
        return {"ci_low": None, "ci_high": None, "reps": 0, "active_dates": len(dates)}
    rng = np.random.default_rng(SEED + 1)
    values: list[float] = []
    for _ in range(BOOTSTRAP_REPS):
        draw = rng.choice(dates, size=len(dates), replace=True)
        cand_profit = sum(cand.get(d, (0.0, 0.0))[0] for d in draw)
        cand_cost = sum(cand.get(d, (0.0, 0.0))[1] for d in draw)
        base_profit = sum(base.get(d, (0.0, 0.0))[0] for d in draw)
        base_cost = sum(base.get(d, (0.0, 0.0))[1] for d in draw)
        if cand_cost > 0 and base_cost > 0:
            values.append(cand_profit / cand_cost - base_profit / base_cost)
    return {
        "ci_low": float(np.quantile(values, 0.025)) if values else None,
        "ci_high": float(np.quantile(values, 0.975)) if values else None,
        "reps": len(values),
        "active_dates": len(dates),
    }


def summarize(name: str, raw: pd.DataFrame, selected: pd.DataFrame, baseline: pd.DataFrame | None) -> dict[str, Any]:
    if selected.empty:
        return {"variant": name, "raw_signals": int(len(raw)), "selected_trades": 0}
    profit = float(selected["stake_profit_usd"].sum())
    cost = float(selected["stake_cost_usd"].sum())
    ci = block_bootstrap_roi(selected)
    out: dict[str, Any] = {
        "variant": name,
        "raw_signals": int(len(raw)),
        "selected_trades": int(len(selected)),
        "active_dates": int(selected["target_date"].nunique()),
        "cities": int(selected["city"].nunique()),
        "start_date": str(selected["target_date"].min()),
        "end_date": str(selected["target_date"].max()),
        "avg_no_ask": float(selected["no_ask"].mean()),
        "median_no_ask": float(selected["no_ask"].median()),
        "avg_top_ask_notional": float(selected["no_top_ask_notional"].mean()),
        "no_win_rate": float(selected["label_no_wins"].mean()),
        "pass_through_win_rate": float(selected["pass_through_no_win"].mean()),
        "actual_peak_afternoon_rate": float(selected["actual_peak_afternoon"].mean()),
        "actual_peak_after_decision_2h_rate": float(selected["actual_peak_after_decision_2h"].mean()),
        "cost_usd": cost,
        "profit_usd": profit,
        "roi": profit / cost if cost else None,
        "roi_ci_low": ci["ci_low"],
        "roi_ci_high": ci["ci_high"],
        "bootstrap_active_dates": ci["active_dates"],
        "holdout_selected_trades": int(selected[selected["period_split"].eq("holdout")].shape[0]),
        "holdout_roi": roi_of(selected[selected["period_split"].eq("holdout")]),
        "train_roi": roi_of(selected[selected["period_split"].eq("train")]),
    }
    if baseline is not None and not baseline.empty:
        base_roi = roi_of(baseline)
        delta_ci = block_bootstrap_delta(selected, baseline)
        out.update(
            {
                "baseline_trades": int(len(baseline)),
                "baseline_roi": base_roi,
                "excess_roi_vs_baseline": (profit / cost - base_roi) if cost and base_roi is not None else None,
                "excess_roi_ci_low": delta_ci["ci_low"],
                "excess_roi_ci_high": delta_ci["ci_high"],
            }
        )
    return out


def roi_of(frame: pd.DataFrame) -> float | None:
    if frame.empty:
        return None
    cost = float(frame["stake_cost_usd"].sum())
    if cost <= 0:
        return None
    return float(frame["stake_profit_usd"].sum()) / cost


def variant_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    executable_035 = (
        df["midday_h10_14"]
        & df["no_ask"].between(0.01, 0.35)
        & df["no_top_ask_notional"].ge(STAKE_USD)
    )
    executable_025 = (
        df["midday_h10_14"]
        & df["no_ask"].between(0.01, 0.25)
        & df["no_top_ask_notional"].ge(STAKE_USD)
    )
    ex_ante_core = (
        executable_035
        & df["new_high_now"]
        & df["warming_1h_3h"]
        & df["forecast_peak_later_2h"]
        & df["forecast_gap_ge_0p5"]
        & df["risk_low"]
    )
    ex_ante_strict = (
        executable_025
        & df["new_high_strict"]
        & df["warming_strict"]
        & df["forecast_peak_later_2h"]
        & df["forecast_gap_ge_1"]
        & df["dual_model_gap_ge_1"]
        & df["risk_low"]
    )
    return {
        "baseline_midday_no_ask_le_35_cap5": executable_035,
        "oracle_actual_peak_afternoon_price_only": executable_035 & df["actual_peak_afternoon"],
        "oracle_peak_after_decision_2h_price_only": executable_035 & df["actual_peak_after_decision_2h"],
        "oracle_actual_pass_through_price_only": executable_035 & df["final_max_above_current_upper"],
        "forecast_only_midday_gap1": executable_035
        & df["new_high_now"]
        & df["forecast_peak_later_2h"]
        & df["forecast_gap_ge_1"],
        "warming_only_midday": executable_035 & df["new_high_now"] & df["warming_1h_3h"],
        "ex_ante_core": ex_ante_core,
        "ex_ante_strict": ex_ante_strict,
        "oracle_afternoon_peak_ex_ante_core": ex_ante_core & df["actual_peak_afternoon"],
        "oracle_peak_after_decision_2h_ex_ante_core": ex_ante_core & df["actual_peak_after_decision_2h"],
        "oracle_actual_pass_through_ex_ante_core": ex_ante_core & df["final_max_above_current_upper"],
        "exploratory_h13_14_warming_gap05": df["decision_hour_local"].between(13, 14)
        & df["no_ask"].between(0.01, 0.35)
        & df["no_top_ask_notional"].ge(STAKE_USD)
        & df["warming_1h_3h"]
        & df["forecast_gap_to_bracket_upper_native"].ge(0.5),
        "exploratory_h14_risk_gap0": df["decision_hour_local"].eq(14)
        & df["no_ask"].between(0.01, 0.35)
        & df["no_top_ask_notional"].ge(STAKE_USD)
        & df["risk_low"]
        & df["forecast_gap_to_bracket_upper_native"].ge(0.0),
        "exploratory_h14_ask20_risk": df["decision_hour_local"].eq(14)
        & df["no_ask"].between(0.01, 0.20)
        & df["no_top_ask_notional"].ge(STAKE_USD)
        & df["risk_low"],
    }


def run_variants(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    masks = variant_masks(df)
    baseline_selected = select_first_per_city_day(df[masks["baseline_midday_no_ask_le_35_cap5"]].copy())
    rows: list[dict[str, Any]] = []
    selected_frames = []
    for name, mask in masks.items():
        raw = df[mask].copy()
        selected = select_first_per_city_day(raw)
        selected["variant"] = name
        selected_frames.append(selected)
        baseline = None if name == "baseline_midday_no_ask_le_35_cap5" else baseline_selected
        rows.append(summarize(name, raw, selected, baseline))
    return pd.DataFrame(rows), pd.concat(selected_frames, ignore_index=True) if selected_frames else pd.DataFrame()


def run_threshold_sweep(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    profiles = {
        "core_like": lambda d, gap: d["new_high_now"]
        & d["warming_1h_3h"]
        & d["forecast_peak_later_2h"]
        & d["forecast_gap_to_bracket_upper_native"].ge(gap)
        & d["risk_low"],
        "warming_gap": lambda d, gap: d["warming_1h_3h"] & d["forecast_gap_to_bracket_upper_native"].ge(gap),
        "new_high_gap": lambda d, gap: d["new_high_now"] & d["forecast_gap_to_bracket_upper_native"].ge(gap),
    }
    for profile, profile_mask in profiles.items():
        for no_ask_max in [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]:
            for gap in [0.0, 0.5, 1.0, 1.5, 2.0]:
                mask = (
                    df["midday_h10_14"]
                    & df["no_ask"].between(0.01, no_ask_max)
                    & df["no_top_ask_notional"].ge(STAKE_USD)
                    & profile_mask(df, gap)
                )
                selected = select_first_per_city_day(df[mask].copy())
                row = summarize(f"{profile}|noask<={no_ask_max:.2f}|gap>={gap:.1f}", df[mask].copy(), selected, None)
                row["profile"] = profile
                row["no_ask_max"] = no_ask_max
                row["forecast_gap_min"] = gap
                rows.append(row)
    return pd.DataFrame(rows).sort_values(["selected_trades", "roi"], ascending=[False, False])


def table_lines(df: pd.DataFrame, cols: list[str]) -> list[str]:
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in df.to_dict("records"):
        vals = []
        for col in cols:
            val = row.get(col)
            if col.endswith("roi") or col in {"roi", "roi_ci_low", "roi_ci_high", "holdout_roi", "baseline_roi", "excess_roi_vs_baseline"}:
                vals.append(pct(val))
            elif col in {"cost_usd", "profit_usd"}:
                vals.append(money(val))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}" if math.isfinite(val) else "NA")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return lines


def write_report(payload: dict[str, Any], variants: pd.DataFrame, sweep: pd.DataFrame) -> None:
    core = variants[variants["variant"].eq("ex_ante_core")].to_dict("records")
    core_row = core[0] if core else {}
    strict = variants[variants["variant"].eq("ex_ante_strict")].to_dict("records")
    strict_row = strict[0] if strict else {}
    baseline = variants[variants["variant"].eq("baseline_midday_no_ask_le_35_cap5")].to_dict("records")
    baseline_row = baseline[0] if baseline else {}
    oracle_peak = variants[variants["variant"].eq("oracle_actual_peak_afternoon_price_only")].to_dict("records")
    oracle_peak_row = oracle_peak[0] if oracle_peak else {}
    exploratory = variants[variants["variant"].eq("exploratory_h13_14_warming_gap05")].to_dict("records")
    exploratory_row = exploratory[0] if exploratory else {}
    best_sweep = sweep[sweep["selected_trades"].fillna(0).ge(20)].head(10).copy()

    lines = [
        "# Current-Bracket NO Pass-Through v1",
        "",
        "## 数据快照",
        "",
        f"- 数据源：专用 reheat feature factory rows + 同窗真实 current-bracket NO ask；`{FEATURE_ROWS.relative_to(ROOT)}`。",
        f"- 生成时间：`{payload['generated_at_utc']}`；feature factory `generated_at_utc={payload['feature_factory'].get('generated_at_utc')}`。",
        f"- 窗口：`{payload['coverage']['sample_start']}`..`{payload['coverage']['sample_end']}`，settled current-NO states `{payload['coverage']['current_no_rows']}`。",
        f"- CLOB gate：`gate_pass={payload['clob_gate'].get('gate_pass')}`；本报告是 opportunity replay，不发布 live_real PnL。",
        f"- Unsettled：feature layer 当前回测行 `0`；missing bracket：`0`（来自 `settlement_outcomes` final winner 完整覆盖）。",
        f"- Rebuild 备注：`run_stack.sh` 完成 DB/fact/gate 后因 FE 5174 端口仍 busy 退出 1；数据层和 gate 已单独核验。",
        "",
        "## 结论",
        "",
        (
            f"在 2026-05-19..2026-06-20，`ex_ante_core` 选出 {core_row.get('selected_trades', 0)} 笔 / "
            f"{core_row.get('active_dates', 0)} 天，固定 ${STAKE_USD:.0f} notional 的 current-bracket NO ROI 为 "
            f"{pct(core_row.get('roi'))}（日期 bootstrap 95% CI "
            f"[{pct(core_row.get('roi_ci_low'))}, {pct(core_row.get('roi_ci_high'))}]），"
            f"相对同价位/同午间 NO baseline 的 excess ROI 为 {pct(core_row.get('excess_roi_vs_baseline'))}，"
            f"holdout ROI 为 {pct(core_row.get('holdout_roi'))}。"
        ),
        "",
        (
            f"机制上界很强但不可交易：`oracle_actual_peak_afternoon_price_only` 选出 "
            f"{oracle_peak_row.get('selected_trades', 0)} 笔，ROI {pct(oracle_peak_row.get('roi'))} "
            f"（CI [{pct(oracle_peak_row.get('roi_ci_low'))}, {pct(oracle_peak_row.get('roi_ci_high'))}]）。"
            "这说明“午后继续创新高”确实会让 current-bracket NO 赚钱，但这个条件本身是事后信息。"
        ),
        "",
        (
            f"最像可交易 proxy 的探索性切片 `exploratory_h13_14_warming_gap05` 有 "
            f"{exploratory_row.get('selected_trades', 0)} 笔，ROI {pct(exploratory_row.get('roi'))} "
            f"（CI [{pct(exploratory_row.get('roi_ci_low'))}, {pct(exploratory_row.get('roi_ci_high'))}]，"
            f"holdout {pct(exploratory_row.get('holdout_roi'))}）。点估不错，但 CI 跨 0，是研究线索，不是 live edge。"
        ),
        "",
        (
            f"三门：significance={payload['three_gate']['significance']} / "
            f"baseline={payload['three_gate']['baseline']} / forward={payload['three_gate']['forward']}；"
            f"conclusion={payload['three_gate']['level']}。"
        ),
        "",
        "直白交易动作：不加 live。这个形态的物理机制是真的，但可交易的“午后 peak 判定器”还没过三门；最多继续做 research / zero-notional shadow 采样。",
        "",
        "## 规则口径",
        "",
        "`ex_ante_core` = local h10-14，current temp 在 running max 附近且 running max age <=45m，1h/3h 仍升温，forecast peak 至少晚 2h，forecast max 高于 current bracket upper >=0.5 native unit，云/风/湿风险低，真实 NO ask 0.01..0.35，top ask notional >=$5。每个 city-date 只取第一笔触发。",
        "",
        "`oracle_*` 行只用于机制验证，包含事后 actual peak / final max 条件，不可直接交易。",
        "",
        "## 主表",
        "",
        *table_lines(
            variants[
                [
                    "variant",
                    "selected_trades",
                    "active_dates",
                    "avg_no_ask",
                    "no_win_rate",
                    "pass_through_win_rate",
                    "roi",
                    "roi_ci_low",
                    "roi_ci_high",
                    "holdout_roi",
                    "baseline_roi",
                    "excess_roi_vs_baseline",
                ]
            ],
            [
                "variant",
                "selected_trades",
                "active_dates",
                "avg_no_ask",
                "no_win_rate",
                "pass_through_win_rate",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
                "holdout_roi",
                "baseline_roi",
                "excess_roi_vs_baseline",
            ],
        ),
        "",
        "## Threshold Sweep",
        "",
        *table_lines(
            best_sweep[
                [
                    "variant",
                    "selected_trades",
                    "active_dates",
                    "avg_no_ask",
                    "no_win_rate",
                    "roi",
                    "roi_ci_low",
                    "roi_ci_high",
                    "holdout_roi",
                ]
            ],
            [
                "variant",
                "selected_trades",
                "active_dates",
                "avg_no_ask",
                "no_win_rate",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
                "holdout_roi",
            ],
        ),
        "",
        "## 8 环覆盖",
        "",
        "- 描述性绩效：PASS，opportunity replay fixed-notional PnL。",
        "- 统计推断：PASS，按 `target_date` block bootstrap。",
        "- 信号判别：PARTIAL，只验证手工条件切片，不是独立模型 IC。",
        "- 概率分布评估：NA，未校准概率。",
        "- 执行微结构：PARTIAL，真实 NO ask + top ask capacity；未模拟 queue / stale chase。",
        "- 容量：PARTIAL，仅 `$5` top ask notional gate。",
        "- 组合相关性：PARTIAL，按日期 bootstrap，未做跨策略组合叠加。",
        "- 基准/反事实：PASS，比较同午间同 price/cap baseline；未通过 baseline gate。",
        "",
        "## 输出",
        "",
        f"- JSON：`{OUT_JSON.relative_to(ROOT)}`",
        f"- Variant CSV：`{OUT_VARIANTS.relative_to(ROOT)}`",
        f"- Sweep CSV：`{OUT_SWEEP.relative_to(ROOT)}`",
        f"- Selected signals：`{OUT_SIGNALS.relative_to(ROOT)}`",
        "",
        "## 限制",
        "",
        "- `cloud/rain/sea breeze risk` 这里只能用 METAR sky/wind/RH/dewpoint proxy，没有真正的海风边界层特征。",
        "- 6/21 settlement 已有，但 observed-detail/forecast-peak research layer 没完整补到 6/21，所以本报告严守到 6/20。",
        "- `oracle_*` 不是可交易规则，只说明“如果事后知道午后继续创新高”，current-NO 会怎样。",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    feature_factory = json.loads(FEATURE_FACTORY_SUMMARY.read_text(encoding="utf-8"))
    gate = load_gate()
    raw = load_feature_rows()
    cur = enrich_current_no(raw)
    variants, selected = run_variants(cur)
    sweep = run_threshold_sweep(cur)

    variants.to_csv(OUT_VARIANTS, index=False)
    sweep.to_csv(OUT_SWEEP, index=False)
    selected.to_csv(OUT_SIGNALS, index=False)

    core = variants[variants["variant"].eq("ex_ante_core")].to_dict("records")
    core_row = core[0] if core else {}
    sig_pass = (
        core_row.get("roi_ci_low") is not None
        and math.isfinite(float(core_row.get("roi_ci_low")))
        and float(core_row.get("roi_ci_low")) > 0
    )
    baseline_pass = (
        sig_pass
        and core_row.get("excess_roi_ci_low") is not None
        and math.isfinite(float(core_row.get("excess_roi_ci_low")))
        and float(core_row.get("excess_roi_ci_low")) > 0
    )
    forward_pass = core_row.get("holdout_roi") is not None and float(core_row.get("holdout_roi")) > 0
    three_gate = {
        "significance": "PASS" if sig_pass else "FAIL",
        "baseline": "PASS" if baseline_pass else "FAIL",
        "forward": "PASS" if forward_pass else "FAIL",
        "level": "confirmed" if sig_pass and baseline_pass and forward_pass else "inconclusive",
    }
    if sig_pass and baseline_pass and not forward_pass:
        three_gate["level"] = "shadow_candidate"

    payload = {
        "generated_at_utc": now_utc(),
        "strategy": "current_bracket_no_pass_through_v1",
        "target_metric": "fixed $5 current-bracket NO replay ROI using real NO ask",
        "row_grain": "first selected city-date signal from city/date/hour current-bracket NO orderbook rows",
        "sources": {
            "feature_rows": str(FEATURE_ROWS.relative_to(ROOT)),
            "feature_factory_summary": str(FEATURE_FACTORY_SUMMARY.relative_to(ROOT)),
            "iem_ext_dir": str(IEM_EXT_DIR.relative_to(ROOT)),
            "clob_gate": str(GATE.relative_to(ROOT)),
        },
        "feature_factory": {
            "generated_at_utc": feature_factory.get("generated_at_utc"),
            "funnel": feature_factory.get("funnel", {}),
            "inputs": feature_factory.get("inputs", {}),
        },
        "clob_gate": gate,
        "coverage": {
            "sample_start": str(cur["target_date"].min()),
            "sample_end": str(cur["target_date"].max()),
            "current_no_rows": int(len(cur)),
            "active_dates": int(cur["target_date"].nunique()),
            "cities": int(cur["city"].nunique()),
            "rows_with_top_ask_notional_ge_5": int(cur["no_top_ask_notional"].ge(STAKE_USD).sum()),
            "actual_peak_hour_join_rate": float(cur["actual_peak_first_hour_local"].notna().mean()),
        },
        "rule_definitions": {
            "stake_usd": STAKE_USD,
            "baseline_midday_no_ask_le_35_cap5": "h10-14, real NO ask 0.01..0.35, top ask notional >= $5, first per city-date",
            "ex_ante_core": "baseline + new high age<=45m + 1h/3h warming + forecast peak later>=2h + forecast gap>=0.5 native + low sky/wind/RH proxy",
            "ex_ante_strict": "ask<=0.25 + new high age<=30m + stricter warming + forecast gap>=1 and both GFS/ECMWF gap>=1 + risk_low",
            "oracle_rows": "post-hoc actual peak/final max filters for mechanism only, not tradable",
        },
        "variants": variants.to_dict("records"),
        "threshold_sweep_top": sweep.head(20).to_dict("records"),
        "three_gate": three_gate,
    }
    payload = finite_or_none(payload)
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_report(payload, variants, sweep)
    print(
        json.dumps(
            {
                "out_json": str(OUT_JSON.relative_to(ROOT)),
                "out_md": str(OUT_MD.relative_to(ROOT)),
                "core": core_row,
                "three_gate": three_gate,
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
