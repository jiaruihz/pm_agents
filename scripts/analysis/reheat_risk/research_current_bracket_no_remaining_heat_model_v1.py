#!/usr/bin/env python3
"""First-principles remaining-heat model for current-bracket NO.

The trade does not pay because the afternoon peak is later; it pays when the
future day max crosses the current bracket upper bound.  This script therefore
models:

    remaining_heat_f = final_max - decision_running_max

and trades only when the modeled probability

    P(remaining_heat_f > required_gap_f)

clears price/edge thresholds.  This is intended to replace the previous
"afternoon peak / still warming" proxy with a payoff-aligned mechanism.
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
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import brier_score_loss, mean_absolute_error, mean_squared_error, r2_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import research_current_bracket_no_preferred_model_payoff_v1 as pref  # noqa: E402
import research_current_bracket_no_source_policy_ab_v1 as source_ab  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_bracket_no_remaining_heat_model_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_VARIANTS = OUT_DIR / "variant_summary.csv"
OUT_DAILY = OUT_DIR / "daily_variant_summary.csv"
OUT_SELECTED = OUT_DIR / "selected_trade_rows.csv"
OUT_FORWARD = OUT_DIR / "forward_validation_and_shadow.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-24-current-bracket-no-remaining-heat-model-v1.md"

SEED = 20260624
STAKE_USD = pref.STAKE_USD
BOOTSTRAP_REPS = 3000

MECH_NUM_FEATURES = [
    "required_gap_f",
    "required_rate_to_peak_fph",
    "hours_until_forecast_peak",
    "forecast_remaining_heat_f",
    "forecast_surplus_f",
    "forecast_over_required_ratio",
    "current_temp_f_equiv",
    "running_temp_f_equiv",
    "distance_into_bracket_f",
    "decline_f",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "temp_trend_3h_per_hour_f",
    "slope_accel_fph",
    "minutes_since_running_max",
    "relative_humidity_pct",
    "dewpoint_depression_f",
    "wind_speed_kt",
    "sky_cover_code",
    "decision_hour_local",
    "available_model_gap_spread_f",
    "gfs_minus_ecmwf_gap_f",
]
MECH_CAT_FEATURES = ["city", "unit", "calibration_best_model"]


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
        return float(value) if math.isfinite(float(value)) else None
    return value


def pct(value: Any) -> str:
    try:
        fval = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(fval):
        return "NA"
    return f"{100.0 * fval:+.1f}%"


def money(value: Any) -> str:
    try:
        fval = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(fval):
        return "NA"
    return f"${fval:+,.2f}"


def native_delta_to_f(df: pd.DataFrame, values: pd.Series | np.ndarray) -> pd.Series:
    vals = pd.to_numeric(pd.Series(values, index=df.index), errors="coerce")
    is_c = df["unit"].astype(str).str.upper().eq("C")
    return vals.where(~is_c, vals * 9.0 / 5.0)


def native_temp_to_f(df: pd.DataFrame, values: pd.Series | np.ndarray) -> pd.Series:
    vals = pd.to_numeric(pd.Series(values, index=df.index), errors="coerce")
    is_c = df["unit"].astype(str).str.upper().eq("C")
    return vals.where(~is_c, vals * 9.0 / 5.0 + 32.0)


def norm_sf(x: pd.Series | np.ndarray) -> np.ndarray:
    arr = np.asarray(x, dtype=float)
    return 0.5 * np.vectorize(math.erfc)(arr / math.sqrt(2.0))


def add_mechanism_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    unit = out["unit"].astype(str).str.upper()
    noise_native = np.where(unit.eq("F"), 0.5, 0.25)
    running = pd.to_numeric(out["running_native"], errors="coerce")
    current = pd.to_numeric(out["current_native"], errors="coerce")
    bracket_upper = pd.to_numeric(out["bracket_upper"], errors="coerce")
    final_max = pd.to_numeric(out["final_max_native"], errors="coerce")
    forecast_max = pd.to_numeric(out["forecast_max_native"], errors="coerce")
    gfs_gap = pd.to_numeric(out.get("gfs_gap_to_bracket_upper_native"), errors="coerce")
    ecmwf_gap = pd.to_numeric(out.get("ecmwf_gap_to_bracket_upper_native"), errors="coerce")

    required_gap_native = bracket_upper + noise_native - running
    forecast_remaining_native = forecast_max - running
    forecast_surplus_native = forecast_max - (bracket_upper + noise_native)
    out["future_delta_to_daymax_f"] = native_delta_to_f(out, final_max - running)
    out["required_gap_f"] = native_delta_to_f(out, required_gap_native).clip(lower=0.0)
    out["forecast_remaining_heat_f"] = native_delta_to_f(out, forecast_remaining_native)
    out["forecast_surplus_f"] = native_delta_to_f(out, forecast_surplus_native)
    out["current_temp_f_equiv"] = native_temp_to_f(out, current)
    out["running_temp_f_equiv"] = native_temp_to_f(out, running)
    out["distance_into_bracket_f"] = native_delta_to_f(out, pd.to_numeric(out["distance_into_bracket_native"], errors="coerce"))
    out["decline_f"] = native_delta_to_f(out, pd.to_numeric(out["decline_native"], errors="coerce"))
    out["available_model_gap_spread_f"] = native_delta_to_f(out, (gfs_gap - ecmwf_gap).abs())
    out["gfs_minus_ecmwf_gap_f"] = native_delta_to_f(out, gfs_gap - ecmwf_gap)

    decision_hour = pd.to_numeric(out["decision_hour_local"], errors="coerce")
    peak_hour = pd.to_numeric(out["forecast_peak_hour_local"], errors="coerce")
    out["hours_until_forecast_peak"] = (peak_hour - decision_hour).clip(lower=0.25)
    out["required_rate_to_peak_fph"] = out["required_gap_f"] / out["hours_until_forecast_peak"]
    denom = out["required_gap_f"].where(out["required_gap_f"].abs().gt(0.1), 0.1)
    out["forecast_over_required_ratio"] = out["forecast_remaining_heat_f"] / denom
    out["temp_trend_3h_per_hour_f"] = pd.to_numeric(out["temp_trend_3h_f"], errors="coerce") / 3.0
    out["slope_accel_fph"] = pd.to_numeric(out["temp_trend_1h_f"], errors="coerce") - out["temp_trend_3h_per_hour_f"]
    out["cross_upper_margin_label"] = (out["future_delta_to_daymax_f"] > out["required_gap_f"]).astype(int)
    out["stake_cost_usd"] = STAKE_USD
    out["stake_shares"] = STAKE_USD / pd.to_numeric(out["no_ask"], errors="coerce")
    out["stake_profit_usd"] = out["label_no_wins"] * out["stake_shares"] - STAKE_USD
    return out


def common_source_mask(df: pd.DataFrame) -> pd.Series:
    return (
        pd.to_numeric(df["forecast_max_native"], errors="coerce").notna()
        & pd.to_numeric(df["forecast_peak_hour_local"], errors="coerce").notna()
        & source_ab.effective_gfs_available(df)
        & pd.to_numeric(df["final_max_native"], errors="coerce").notna()
        & pd.to_numeric(df["running_native"], errors="coerce").notna()
        & pd.to_numeric(df["bracket_upper"], errors="coerce").notna()
    )


def trade_base_mask(df: pd.DataFrame) -> pd.Series:
    return (
        common_source_mask(df)
        & df["no_ask"].between(0.10, 0.35)
        & pd.to_numeric(df["depth5_notional"], errors="coerce").ge(STAKE_USD)
    )


def load_historical_forced_gfs() -> tuple[pd.DataFrame, dict[str, Any]]:
    base, _forecast_rows, forecast_stats = pref.load_dataset()
    frame = source_ab.prepare_policy_frame(base, "forced_gfs", common_source_mask(base))
    frame = add_mechanism_features(frame)
    for col in MECH_NUM_FEATURES:
        if col not in frame.columns:
            frame[col] = np.nan
    for col in MECH_CAT_FEATURES:
        if col not in frame.columns:
            frame[col] = ""
    stats = {
        "raw_rows": int(len(base)),
        "mechanism_rows": int(len(frame)),
        "trade_base_rows": int(trade_base_mask(frame).sum()),
        "date_min": str(base["target_date"].min()),
        "date_max": str(base["target_date"].max()),
        "forecast_stats": forecast_stats,
    }
    return frame.reset_index(drop=True), stats


def load_forward_forced_gfs() -> tuple[pd.DataFrame, dict[str, Any]]:
    forward, stats = pref.load_forward_dataset()
    if forward.empty:
        return forward, {"missing": True, "source_stats": stats}
    frame = source_ab.prepare_policy_frame(forward, "forced_gfs", common_source_mask(forward))
    frame = add_mechanism_features(frame)
    for col in MECH_NUM_FEATURES:
        if col not in frame.columns:
            frame[col] = np.nan
    for col in MECH_CAT_FEATURES:
        if col not in frame.columns:
            frame[col] = ""
    return frame.reset_index(drop=True), {"source_stats": stats, "mechanism_rows": int(len(frame))}


def split_date_for(df: pd.DataFrame) -> str:
    dates = sorted(df["target_date"].dropna().astype(str).unique())
    return dates[max(1, int(len(dates) * 0.70)) - 1]


def build_regressor() -> Pipeline:
    pre = ColumnTransformer(
        [
            ("num", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler())]), MECH_NUM_FEATURES),
            (
                "cat",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=5, sparse_output=False)),
                    ]
                ),
                MECH_CAT_FEATURES,
            ),
        ]
    )
    reg = HistGradientBoostingRegressor(
        max_iter=250,
        learning_rate=0.04,
        max_leaf_nodes=10,
        min_samples_leaf=18,
        l2_regularization=0.05,
        random_state=SEED,
    )
    return Pipeline([("pre", pre), ("reg", reg)])


def add_predictions(frame: pd.DataFrame, train_mask: pd.Series) -> tuple[pd.DataFrame, Pipeline, dict[str, Any]]:
    out = frame.copy()
    train = out[train_mask].copy()
    model = build_regressor()
    model.fit(train[MECH_NUM_FEATURES + MECH_CAT_FEATURES], train["future_delta_to_daymax_f"])
    train_pred = model.predict(train[MECH_NUM_FEATURES + MECH_CAT_FEATURES])
    resid = train["future_delta_to_daymax_f"].to_numpy(dtype=float) - train_pred
    sigma = max(float(np.nanstd(resid, ddof=1)), 0.25)
    out["pred_remaining_heat_f"] = model.predict(out[MECH_NUM_FEATURES + MECH_CAT_FEATURES])
    out["remaining_heat_sigma_f"] = sigma
    out["p_cross_upper"] = norm_sf((out["required_gap_f"] - out["pred_remaining_heat_f"]) / sigma)
    out["mechanism_edge"] = out["p_cross_upper"] - pd.to_numeric(out["no_ask"], errors="coerce")
    out["p_no_win"] = out["p_cross_upper"]
    out["p_up_margin"] = out["p_cross_upper"]
    out["edge_no_win"] = out["mechanism_edge"]
    out["edge_up_margin"] = out["mechanism_edge"]
    return out, model, {"residual_sigma_f": sigma}


def model_metrics(frame: pd.DataFrame, period_mask: pd.Series) -> dict[str, Any]:
    d = frame[period_mask].copy()
    y = pd.to_numeric(d["future_delta_to_daymax_f"], errors="coerce")
    pred = pd.to_numeric(d["pred_remaining_heat_f"], errors="coerce")
    label = d["cross_upper_margin_label"].astype(int)
    p = pd.to_numeric(d["p_cross_upper"], errors="coerce")
    out = {
        "rows": int(len(d)),
        "active_dates": int(d["target_date"].nunique()) if not d.empty else 0,
        "future_delta_mean_f": float(y.mean()) if not d.empty else None,
        "mae_f": float(mean_absolute_error(y, pred)) if len(d) else None,
        "rmse_f": float(math.sqrt(mean_squared_error(y, pred))) if len(d) else None,
        "r2": float(r2_score(y, pred)) if len(d) > 1 else None,
        "cross_rate": float(label.mean()) if len(d) else None,
        "cross_auc": float(roc_auc_score(label, p)) if label.nunique() > 1 else None,
        "cross_brier": float(brier_score_loss(label, p)) if label.nunique() > 1 else None,
    }
    return out


def roi_of(frame: pd.DataFrame) -> float | None:
    if frame.empty:
        return None
    cost = float(frame["stake_cost_usd"].sum())
    return float(frame["stake_profit_usd"].sum()) / cost if cost else None


def select_first(frame: pd.DataFrame) -> pd.DataFrame:
    return pref.select_first(frame.copy())


def select_top_per_date(frame: pd.DataFrame, per_date: int, score_col: str) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    out = frame.sort_values(["target_date", score_col, "no_ask"], ascending=[True, False, True]).copy()
    out["_date_rank"] = out.groupby("target_date").cumcount() + 1
    return out[out["_date_rank"].le(per_date)].drop(columns=["_date_rank"]).reset_index(drop=True)


def grouped_profit_cost(frame: pd.DataFrame) -> dict[str, tuple[float, float]]:
    if frame.empty:
        return {}
    g = frame.groupby("target_date").agg(profit=("stake_profit_usd", "sum"), cost=("stake_cost_usd", "sum"))
    return {str(idx): (float(row.profit), float(row.cost)) for idx, row in g.iterrows()}


def block_bootstrap_roi(frame: pd.DataFrame) -> dict[str, Any]:
    by_date = grouped_profit_cost(frame)
    dates = sorted(by_date)
    if len(dates) < 3:
        return {"ci_low": None, "ci_high": None, "active_dates": len(dates), "reps": 0}
    rng = np.random.default_rng(SEED)
    vals = []
    for _ in range(BOOTSTRAP_REPS):
        draw = rng.choice(dates, size=len(dates), replace=True)
        profit = sum(by_date[d][0] for d in draw)
        cost = sum(by_date[d][1] for d in draw)
        vals.append(profit / cost)
    return {
        "ci_low": float(np.quantile(vals, 0.025)),
        "ci_high": float(np.quantile(vals, 0.975)),
        "active_dates": len(dates),
        "reps": len(vals),
    }


def summarize_variant(name: str, raw: pd.DataFrame, baseline: pd.DataFrame | None = None) -> dict[str, Any]:
    selected = select_first(raw)
    if selected.empty:
        return {"variant": name, "raw_signals": int(len(raw)), "selected_trades": 0}
    profit = float(selected["stake_profit_usd"].sum())
    cost = float(selected["stake_cost_usd"].sum())
    ci = block_bootstrap_roi(selected)
    all_loss_dates = (
        selected.groupby("target_date")["label_no_wins"].sum().reset_index().query("label_no_wins == 0")["target_date"].astype(str).tolist()
    )
    out = {
        "variant": name,
        "raw_signals": int(len(raw)),
        "selected_trades": int(len(selected)),
        "active_dates": int(selected["target_date"].nunique()),
        "cities": int(selected["city"].nunique()),
        "avg_no_ask": float(selected["no_ask"].mean()),
        "win_rate": float(selected["label_no_wins"].mean()),
        "avg_p_cross": float(selected["p_cross_upper"].mean()),
        "avg_required_gap_f": float(selected["required_gap_f"].mean()),
        "avg_pred_remaining_heat_f": float(selected["pred_remaining_heat_f"].mean()),
        "avg_actual_remaining_heat_f": float(selected["future_delta_to_daymax_f"].mean()),
        "avg_actual_margin_f": float((selected["future_delta_to_daymax_f"] - selected["required_gap_f"]).mean()),
        "cost_usd": cost,
        "profit_usd": profit,
        "roi": profit / cost if cost else None,
        "roi_ci_low": ci["ci_low"],
        "roi_ci_high": ci["ci_high"],
        "bootstrap_active_dates": ci["active_dates"],
        "holdout_selected_trades": int(selected[selected["period_split"].eq("holdout")].shape[0]),
        "holdout_roi": roi_of(selected[selected["period_split"].eq("holdout")]),
        "train_roi": roi_of(selected[selected["period_split"].eq("train")]),
        "selected_all_loss_days": int(len(all_loss_dates)),
        "selected_all_loss_dates": ",".join(all_loss_dates),
    }
    if baseline is not None and not baseline.empty:
        out["baseline_trades"] = int(len(baseline))
        out["baseline_roi"] = roi_of(baseline)
        out["excess_roi_vs_baseline"] = out["roi"] - out["baseline_roi"] if out["roi"] is not None and out["baseline_roi"] is not None else None
    return out


def variant_raws(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    tb = frame[frame["trade_base_mechanism"]].copy()
    return {
        "baseline_trade_base": tb,
        "remaining_heat_p35_ev05": tb[tb["p_cross_upper"].ge(0.35) & tb["mechanism_edge"].ge(0.05)].copy(),
        "remaining_heat_p40_ev10": tb[tb["p_cross_upper"].ge(0.40) & tb["mechanism_edge"].ge(0.10)].copy(),
        "remaining_heat_p45_ev10": tb[tb["p_cross_upper"].ge(0.45) & tb["mechanism_edge"].ge(0.10)].copy(),
        "remaining_heat_p40_ev10_max2_day": select_top_per_date(
            tb[tb["p_cross_upper"].ge(0.40) & tb["mechanism_edge"].ge(0.10)].copy(), 2, "mechanism_edge"
        ),
    }


def daily_summary(variant: str, raw: pd.DataFrame) -> pd.DataFrame:
    selected = select_first(raw)
    if selected.empty:
        return pd.DataFrame()
    out = (
        selected.groupby("target_date")
        .agg(
            trades=("city", "size"),
            wins=("label_no_wins", "sum"),
            cost_usd=("stake_cost_usd", "sum"),
            profit_usd=("stake_profit_usd", "sum"),
            avg_p_cross=("p_cross_upper", "mean"),
            avg_required_gap_f=("required_gap_f", "mean"),
            avg_pred_remaining_heat_f=("pred_remaining_heat_f", "mean"),
            avg_actual_remaining_heat_f=("future_delta_to_daymax_f", "mean"),
            avg_margin_f=("future_delta_to_daymax_f", lambda s: float((s - selected.loc[s.index, "required_gap_f"]).mean())),
        )
        .reset_index()
    )
    out["variant"] = variant
    out["roi"] = out["profit_usd"] / out["cost_usd"]
    out["win_rate"] = out["wins"] / out["trades"]
    out["loss_cities"] = out["target_date"].map(
        selected[selected["label_no_wins"].eq(0)].groupby("target_date")["city"].apply(lambda x: ",".join(sorted(x.astype(str))))
    )
    return out


def evaluate_forward(model: Pipeline, sigma: float, split_date: str) -> tuple[pd.DataFrame, list[dict[str, Any]], dict[str, Any]]:
    frame, stats = load_forward_forced_gfs()
    if frame.empty:
        return frame, [], stats
    frame["pred_remaining_heat_f"] = model.predict(frame[MECH_NUM_FEATURES + MECH_CAT_FEATURES])
    frame["remaining_heat_sigma_f"] = sigma
    frame["p_cross_upper"] = norm_sf((frame["required_gap_f"] - frame["pred_remaining_heat_f"]) / sigma)
    frame["mechanism_edge"] = frame["p_cross_upper"] - pd.to_numeric(frame["no_ask"], errors="coerce")
    frame["p_no_win"] = frame["p_cross_upper"]
    frame["p_up_margin"] = frame["p_cross_upper"]
    frame["edge_no_win"] = frame["mechanism_edge"]
    frame["edge_up_margin"] = frame["mechanism_edge"]
    frame["trade_base_mechanism"] = trade_base_mask(frame)
    rows = []
    selected_parts = []
    for name, raw in variant_raws(frame).items():
        selected = select_first(raw)
        if selected.empty:
            rows.append({"variant": name, "selected_trades": 0})
            continue
        settled = selected[selected["label_no_wins"].notna()].copy()
        profit = float(settled["stake_profit_usd"].sum()) if not settled.empty else 0.0
        cost = float(settled["stake_cost_usd"].sum()) if not settled.empty else 0.0
        rows.append(
            {
                "variant": name,
                "selected_trades": int(len(selected)),
                "active_dates": int(selected["target_date"].nunique()),
                "settled_trades": int(len(settled)),
                "open_shadow_trades": int(selected["label_no_wins"].isna().sum()),
                "settled_win_rate": None if settled.empty else float(settled["label_no_wins"].mean()),
                "settled_profit_usd": profit,
                "settled_roi": profit / cost if cost else None,
                "dates": ",".join(sorted(selected["target_date"].astype(str).unique())),
            }
        )
        selected = selected.copy()
        selected["variant"] = name
        selected_parts.append(selected)
    selected_all = pd.concat(selected_parts, ignore_index=True) if selected_parts else pd.DataFrame()
    return selected_all, rows, stats


def render_md(payload: dict[str, Any], variants: pd.DataFrame, daily: pd.DataFrame, forward_rows: pd.DataFrame) -> str:
    def table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
        shown = df if limit is None else df.head(limit)
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        for _, row in shown.iterrows():
            vals = []
            for col in cols:
                val = row.get(col)
                if col.endswith("roi") or col.endswith("rate") or col in {"win_rate", "roi_ci_low", "roi_ci_high", "holdout_roi"}:
                    vals.append(pct(val))
                elif col.endswith("usd"):
                    vals.append(money(val))
                elif isinstance(val, float):
                    vals.append(f"{val:.2f}")
                else:
                    vals.append("" if pd.isna(val) else str(val))
            lines.append("| " + " | ".join(vals) + " |")
        return "\n".join(lines)

    all_loss = daily[daily["wins"].eq(0)].sort_values(["variant", "target_date"])
    lines = [
        "# Current-Bracket NO Remaining-Heat Model V1",
        "",
        "## 结论",
        "",
        "这版把信号机制改成 payoff 对齐：不再预测“午后 peak”或简单升温，而是预测从 decision running max 到日最高温的"
        "`remaining_heat`，再和穿过当前 bracket upper 所需的 `required_gap` 比较。",
        "",
        f"Verdict: `{payload['verdict']['status']}`，live_ready=`{payload['verdict']['live_ready']}`。",
        "",
        "## 数据层",
        "",
        f"- Generated at UTC: `{payload['generated_at_utc']}`",
        f"- Sync/rebuild: `{payload['data_refresh_note']}`",
        f"- Raw rows: `{payload['dataset']['raw_rows']}`",
        f"- Mechanism rows: `{payload['dataset']['mechanism_rows']}`",
        f"- Trade-base rows: `{payload['dataset']['trade_base_rows']}`",
        f"- Date range: `{payload['dataset']['date_min']}`..`{payload['dataset']['date_max']}`",
        f"- Split date: `{payload['dataset']['split_date']}`",
        "",
        "## Model Diagnostics",
        "",
        table(pd.DataFrame(payload["model_metrics"]), ["period", "rows", "active_dates", "mae_f", "rmse_f", "r2", "cross_rate", "cross_auc", "cross_brier"]),
        "",
        "## Trade Variants",
        "",
        table(
            variants,
            [
                "variant",
                "selected_trades",
                "active_dates",
                "cities",
                "win_rate",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
                "holdout_roi",
                "avg_required_gap_f",
                "avg_pred_remaining_heat_f",
                "avg_actual_remaining_heat_f",
                "avg_actual_margin_f",
                "selected_all_loss_days",
            ],
        ),
        "",
        "## All-Loss Dates",
        "",
        table(
            all_loss,
            ["variant", "target_date", "trades", "wins", "roi", "avg_p_cross", "avg_required_gap_f", "avg_pred_remaining_heat_f", "avg_actual_remaining_heat_f", "avg_margin_f", "loss_cities"],
            limit=60,
        ),
        "",
        "## Forward 6/21..6/23",
        "",
        table(
            forward_rows,
            ["variant", "selected_trades", "settled_trades", "open_shadow_trades", "settled_win_rate", "settled_roi", "settled_profit_usd", "dates"],
        ),
        "",
        "## 读法",
        "",
        "1. 这是机制重写，不是新增 gate：分数本身就是 `P(remaining_heat > required_gap)`。",
        "2. 如果全错日仍集中，说明我们缺的是 remaining-heat 特征，而不是再补一条日期过滤。",
        "3. 这版仍使用 forced-GFS 做主对照，原因是上一轮 source-policy A/B 显示 source route 不是根因，且非 GFS 缺 true PIT。",
        "",
        "## Files",
        "",
        f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
        f"- Variants: `{OUT_VARIANTS.relative_to(ROOT)}`",
        f"- Daily: `{OUT_DAILY.relative_to(ROOT)}`",
        f"- Selected rows: `{OUT_SELECTED.relative_to(ROOT)}`",
        f"- Forward: `{OUT_FORWARD.relative_to(ROOT)}`",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frame, stats = load_historical_forced_gfs()
    split_date = split_date_for(frame)
    frame["period_split"] = np.where(frame["target_date"].astype(str).le(split_date), "train", "holdout")
    frame["trade_base_mechanism"] = trade_base_mask(frame)

    train_mask = frame["period_split"].eq("train")
    frame, model, residual = add_predictions(frame, train_mask)
    metrics = [
        {"period": "train", **model_metrics(frame, frame["period_split"].eq("train"))},
        {"period": "holdout", **model_metrics(frame, frame["period_split"].eq("holdout"))},
        {"period": "trade_base_holdout", **model_metrics(frame, frame["period_split"].eq("holdout") & frame["trade_base_mechanism"])},
    ]

    variants_raw = variant_raws(frame)
    baseline = select_first(variants_raw["baseline_trade_base"])
    variants = pd.DataFrame(
        [summarize_variant(name, raw, None if name == "baseline_trade_base" else baseline) for name, raw in variants_raw.items()]
    )
    variants.to_csv(OUT_VARIANTS, index=False)

    daily_parts = []
    selected_parts = []
    for name, raw in variants_raw.items():
        dsum = daily_summary(name, raw)
        if not dsum.empty:
            daily_parts.append(dsum)
        selected = select_first(raw)
        if not selected.empty:
            selected = selected.copy()
            selected["variant"] = name
            selected_parts.append(selected)
    daily = pd.concat(daily_parts, ignore_index=True) if daily_parts else pd.DataFrame()
    selected_all = pd.concat(selected_parts, ignore_index=True) if selected_parts else pd.DataFrame()
    daily.to_csv(OUT_DAILY, index=False)
    selected_all.to_csv(OUT_SELECTED, index=False)

    forward_selected, forward_summary, forward_stats = evaluate_forward(model, residual["residual_sigma_f"], split_date)
    pd.DataFrame(forward_summary).to_csv(OUT_FORWARD, index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "current_bracket_no_remaining_heat_model_v1",
        "data_refresh_note": "sync_weather_remote.sh completed; run_stack rebuilt facts and CLOB gate passed, then exited non-clean only because FE port 5174 stayed busy.",
        "dataset": {**stats, "split_date": split_date},
        "residual": residual,
        "model_metrics": finite_or_none(metrics),
        "variant_summary": finite_or_none(variants.to_dict(orient="records")),
        "forward_summary": finite_or_none(forward_summary),
        "forward_stats": finite_or_none(forward_stats),
        "outputs": {
            "summary_json": str(OUT_JSON.relative_to(ROOT)),
            "variant_summary_csv": str(OUT_VARIANTS.relative_to(ROOT)),
            "daily_variant_summary_csv": str(OUT_DAILY.relative_to(ROOT)),
            "selected_trade_rows_csv": str(OUT_SELECTED.relative_to(ROOT)),
            "forward_validation_csv": str(OUT_FORWARD.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "status": "mechanism_rewrite_shadow_only",
            "live_ready": False,
            "reason": "Remaining-heat formulation aligns with payoff, but must pass holdout and forward before live consideration.",
        },
    }
    OUT_JSON.write_text(json.dumps(finite_or_none(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(payload, variants, daily, pd.DataFrame(forward_summary)), encoding="utf-8")
    print(json.dumps(payload["verdict"], indent=2, ensure_ascii=False))
    print(OUT_MD.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
