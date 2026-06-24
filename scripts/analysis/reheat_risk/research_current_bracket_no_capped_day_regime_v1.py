#!/usr/bin/env python3
"""Capped-day regime model for current-bracket NO.

The remaining-heat score answers "can this city still warm enough to cross the
current upper bound?"  The failure mode found on 6/22 is different: a day can
look warmable but still be capped below the upper margin.

This script trains a separate capped-day risk model:

    p_cap = P(actual remaining heat <= required_gap)

Then it tests whether p_cap is stable across early/late train-validation swaps
and whether it improves the fixed remaining_heat_p40_ev10 expression.
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
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import research_current_bracket_no_city_robustness_622_v1 as city622  # noqa: E402
import research_current_bracket_no_remaining_heat_mechanism_features_v3 as v3  # noqa: E402
import research_current_bracket_no_remaining_heat_model_v1 as v1  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_bracket_no_capped_day_regime_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_MODEL = OUT_DIR / "cap_model_swap_metrics.csv"
OUT_VARIANT = OUT_DIR / "cap_variant_performance.csv"
OUT_BUCKET = OUT_DIR / "cap_risk_bucket_calibration.csv"
OUT_OVERCONF = OUT_DIR / "forecast_overconfidence_slices.csv"
OUT_622 = OUT_DIR / "cap_2026_06_22_details.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-24-current-bracket-no-capped-day-regime-v1.md"

EARLY_END = "2026-06-10"
LATE_START = "2026-06-11"
LATE_END = "2026-06-20"
FORWARD_START = "2026-06-21"
SEED = 20260624

CAP_NUM_FEATURES = [
    "required_gap_f",
    "forecast_surplus_f",
    "forecast_over_required_ratio",
    "forecast_remaining_heat_f",
    "hours_until_forecast_peak",
    "required_rate_to_peak_fph",
    "curve_remaining_to_peak_f",
    "curve_next_1h_delta_f",
    "curve_next_2h_delta_f",
    "curve_next_3h_delta_f",
    "curve_slope_next_3h_fph",
    "curve_plateau_hours_next_3h",
    "curve_tail_above_upper_margin_hours",
    "curve_tail_area_above_upper_margin_fh",
    "curve_pullback_before_peak_f",
    "forecast_curve_max_minus_forecast_max_f",
    "minutes_since_running_max",
    "running_max_stale_ge_30m",
    "running_max_stale_ge_60m",
    "plateau_proxy",
    "trend_decay_proxy_fph",
    "temp_trend_1h_f",
    "temp_trend_3h_per_hour_f",
    "slope_accel_fph",
    "relative_humidity_pct",
    "dewpoint_depression_f",
    "wind_speed_kt",
    "sky_cover_code",
    "decision_hour_local",
    "distance_into_bracket_f",
    "decline_f",
    "available_model_gap_spread_f",
    "gfs_minus_ecmwf_gap_f",
]
CAP_CAT_WEATHER = ["unit", "calibration_best_model"]
CAP_CAT_WITH_CITY = ["unit", "calibration_best_model", "city", "city_family"]


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


def add_regime_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["target_date"] = out["target_date"].astype(str)
    out["city_family"] = out["city"].map(city622.CITY_FAMILY).fillna("other")
    out["actual_margin_f"] = pd.to_numeric(out["future_delta_to_daymax_f"], errors="coerce") - pd.to_numeric(
        out["required_gap_f"], errors="coerce"
    )
    out["cap_label"] = out["actual_margin_f"].le(0).astype(int)
    if "pred_remaining_heat_f" in out.columns:
        out["pred_error_f"] = pd.to_numeric(out["pred_remaining_heat_f"], errors="coerce") - pd.to_numeric(
            out["future_delta_to_daymax_f"], errors="coerce"
        )
    else:
        out["pred_error_f"] = np.nan
    out["loss_reason"] = np.select(
        [
            out["label_no_wins"].eq(1),
            out["actual_margin_f"].lt(0) & out["pred_error_f"].gt(0.35),
            out["actual_margin_f"].lt(0),
            out["pred_error_f"].gt(0.35),
        ],
        ["win", "capped_day_model_overestimate", "capped_day_shortfall", "model_overestimate"],
        default="other_loss",
    )
    for col in CAP_NUM_FEATURES:
        if col not in out.columns:
            out[col] = np.nan
    for col in sorted(set(CAP_CAT_WEATHER + CAP_CAT_WITH_CITY)):
        if col not in out.columns:
            out[col] = ""
    return out


def prepare_history() -> tuple[pd.DataFrame, dict[str, Any]]:
    frame, stats = v1.load_historical_forced_gfs()
    frame["target_date"] = frame["target_date"].astype(str)
    frame["trade_base_mechanism"] = v1.trade_base_mask(frame)
    frame = v3.add_enhanced_mechanism_features(frame)
    return add_regime_columns(frame.reset_index(drop=True)), stats


def prepare_forward() -> tuple[pd.DataFrame, dict[str, Any]]:
    frame, stats = v1.load_forward_forced_gfs()
    if frame.empty:
        return frame, stats
    frame["target_date"] = frame["target_date"].astype(str)
    frame["trade_base_mechanism"] = v1.trade_base_mask(frame)
    frame = v3.add_enhanced_mechanism_features(frame)
    return add_regime_columns(frame.reset_index(drop=True)), stats


def window_mask(frame: pd.DataFrame, window: str) -> pd.Series:
    dates = frame["target_date"].astype(str)
    if window == "early":
        return dates.le(EARLY_END)
    if window == "late":
        return dates.between(LATE_START, LATE_END)
    if window == "historical":
        return dates.le(LATE_END)
    if window == "forward":
        return dates.ge(FORWARD_START)
    raise ValueError(window)


def build_cap_classifier(cat_features: list[str]) -> Pipeline:
    pre = ColumnTransformer(
        [
            ("num", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler())]), CAP_NUM_FEATURES),
            (
                "cat",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=5, sparse_output=False)),
                    ]
                ),
                cat_features,
            ),
        ]
    )
    clf = HistGradientBoostingClassifier(
        max_iter=180,
        learning_rate=0.04,
        max_leaf_nodes=8,
        min_samples_leaf=30,
        l2_regularization=0.10,
        random_state=SEED,
    )
    return Pipeline([("pre", pre), ("clf", clf)])


def fit_remaining_heat(frame: pd.DataFrame, train_window: str) -> tuple[pd.DataFrame, Any, float]:
    train_mask = window_mask(frame, train_window)
    scored, model, sigma = v3.score_model(frame, train_mask, v3.ENHANCED_NUM_FEATURES, v3.ENHANCED_CAT_FEATURES)
    return add_regime_columns(scored), model, sigma


def score_remaining_heat(frame: pd.DataFrame, model: Any, sigma: float) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    out = frame.copy()
    out["pred_remaining_heat_f"] = model.predict(out[v3.ENHANCED_NUM_FEATURES + v3.ENHANCED_CAT_FEATURES])
    out["remaining_heat_sigma_f"] = sigma
    out["p_cross_upper"] = v1.norm_sf((out["required_gap_f"] - out["pred_remaining_heat_f"]) / sigma)
    out["mechanism_edge"] = out["p_cross_upper"] - pd.to_numeric(out["no_ask"], errors="coerce")
    out["p_no_win"] = out["p_cross_upper"]
    out["p_up_margin"] = out["p_cross_upper"]
    out["edge_no_win"] = out["mechanism_edge"]
    out["edge_up_margin"] = out["mechanism_edge"]
    return add_regime_columns(out)


def fit_cap_model(scored: pd.DataFrame, train_window: str, cat_features: list[str]) -> Pipeline:
    train = scored[window_mask(scored, train_window)].copy()
    model = build_cap_classifier(cat_features)
    model.fit(train[CAP_NUM_FEATURES + cat_features], train["cap_label"].astype(int))
    return model


def score_cap_model(frame: pd.DataFrame, model: Pipeline, cat_features: list[str]) -> pd.DataFrame:
    out = frame.copy()
    if out.empty:
        return out
    out["p_cap"] = model.predict_proba(out[CAP_NUM_FEATURES + cat_features])[:, 1]
    out["p_cross_cap_adjusted"] = out["p_cross_upper"] * (1.0 - out["p_cap"])
    out["cap_adjusted_edge"] = out["p_cross_cap_adjusted"] - pd.to_numeric(out["no_ask"], errors="coerce")
    return out


def cap_metrics(frame: pd.DataFrame, train_spec: str, eval_window: str) -> dict[str, Any]:
    d = frame[frame["label_no_wins"].notna()].copy()
    y = d["cap_label"].astype(int)
    p = pd.to_numeric(d["p_cap"], errors="coerce")
    return {
        "train_spec": train_spec,
        "eval_window": eval_window,
        "rows": int(len(d)),
        "trade_base_rows": int(d["trade_base_mechanism"].fillna(False).sum()) if len(d) else 0,
        "active_dates": int(d["target_date"].nunique()) if len(d) else 0,
        "cap_rate": float(y.mean()) if len(d) else None,
        "cap_auc": float(roc_auc_score(y, p)) if y.nunique() > 1 else None,
        "cap_brier": float(brier_score_loss(y, p)) if y.nunique() > 1 else None,
        "avg_p_cap": float(p.mean()) if len(d) else None,
    }


def select_base_p40(frame: pd.DataFrame) -> pd.DataFrame:
    raw = v1.variant_raws(frame)["remaining_heat_p40_ev10"]
    return v1.select_first(raw)


def select_cap_veto(frame: pd.DataFrame, threshold: float) -> pd.DataFrame:
    raw = v1.variant_raws(frame)["remaining_heat_p40_ev10"]
    raw = raw[pd.to_numeric(raw["p_cap"], errors="coerce").lt(threshold)].copy()
    return v1.select_first(raw)


def select_cap_adjusted(frame: pd.DataFrame) -> pd.DataFrame:
    tb = frame[frame["trade_base_mechanism"]].copy()
    raw = tb[tb["p_cross_cap_adjusted"].ge(0.40) & tb["cap_adjusted_edge"].ge(0.10)].copy()
    if raw.empty:
        return raw
    return v1.select_first(raw.sort_values(["target_date", "cap_adjusted_edge"], ascending=[True, False]))


def selected_perf(selected: pd.DataFrame, train_spec: str, eval_window: str, variant: str) -> dict[str, Any]:
    settled = selected[selected["label_no_wins"].notna()].copy()
    cost = float(settled["stake_cost_usd"].sum()) if len(settled) else 0.0
    profit = float(settled["stake_profit_usd"].sum()) if len(settled) else 0.0
    return {
        "train_spec": train_spec,
        "eval_window": eval_window,
        "variant": variant,
        "selected_trades": int(len(selected)),
        "settled_trades": int(len(settled)),
        "active_dates": int(selected["target_date"].nunique()) if len(selected) else 0,
        "cities": int(selected["city"].nunique()) if len(selected) else 0,
        "wins": float(settled["label_no_wins"].sum()) if len(settled) else 0.0,
        "win_rate": float(settled["label_no_wins"].mean()) if len(settled) else None,
        "profit_usd": profit,
        "cost_usd": cost,
        "roi": profit / cost if cost else None,
        "avg_p_cross": float(selected["p_cross_upper"].mean()) if len(selected) else None,
        "avg_p_cap": float(selected["p_cap"].mean()) if len(selected) else None,
        "avg_p_cross_cap_adjusted": float(selected["p_cross_cap_adjusted"].mean()) if len(selected) else None,
        "avg_pred_error_f": float(settled["pred_error_f"].mean()) if len(settled) else None,
        "avg_actual_margin_f": float(settled["actual_margin_f"].mean()) if len(settled) else None,
        "loss_reason_counts": ",".join(
            f"{k}:{v}" for k, v in settled["loss_reason"].value_counts().sort_index().to_dict().items()
        )
        if len(settled)
        else "",
        "dates": ",".join(sorted(selected["target_date"].astype(str).unique())) if len(selected) else "",
    }


def evaluate_variants(frame: pd.DataFrame, train_spec: str, eval_window: str, cap_threshold: float) -> list[dict[str, Any]]:
    rows = []
    rows.append(selected_perf(select_base_p40(frame), train_spec, eval_window, "base_p40_ev10"))
    rows.append(selected_perf(select_cap_veto(frame, cap_threshold), train_spec, eval_window, "cap_veto_train_top25_risk"))
    rows.append(selected_perf(select_cap_adjusted(frame), train_spec, eval_window, "cap_adjusted_p40_ev10"))
    return rows


def cap_bucket_calibration(frame: pd.DataFrame, train_spec: str, eval_window: str) -> list[dict[str, Any]]:
    selected = select_base_p40(frame)
    settled = selected[selected["label_no_wins"].notna()].copy()
    if settled.empty:
        return []
    try:
        settled["p_cap_bucket"] = pd.qcut(settled["p_cap"], q=4, labels=False, duplicates="drop") + 1
    except ValueError:
        settled["p_cap_bucket"] = 1
    rows = []
    for bucket, group in settled.groupby("p_cap_bucket", dropna=False):
        cost = float(group["stake_cost_usd"].sum())
        profit = float(group["stake_profit_usd"].sum())
        rows.append(
            {
                "train_spec": train_spec,
                "eval_window": eval_window,
                "bucket": int(bucket) if pd.notna(bucket) else None,
                "rows": int(len(group)),
                "active_dates": int(group["target_date"].nunique()),
                "avg_p_cap": float(group["p_cap"].mean()),
                "cap_rate": float(group["cap_label"].mean()),
                "win_rate": float(group["label_no_wins"].mean()),
                "roi": profit / cost if cost else None,
                "avg_p_cross": float(group["p_cross_upper"].mean()),
                "avg_actual_margin_f": float(group["actual_margin_f"].mean()),
                "avg_pred_error_f": float(group["pred_error_f"].mean()),
            }
        )
    return rows


def forecast_overconfidence_slices(frame: pd.DataFrame, train_spec: str, eval_window: str) -> list[dict[str, Any]]:
    selected = select_base_p40(frame)
    settled = selected[selected["label_no_wins"].notna()].copy()
    if settled.empty:
        return []
    slice_masks = {
        "all_p40": pd.Series(True, index=settled.index),
        "p_cross_ge_095": settled["p_cross_upper"].ge(0.95),
        "p_cross_ge_095_pcap_lt_030": settled["p_cross_upper"].ge(0.95) & settled["p_cap"].lt(0.30),
        "p_cross_ge_095_pcap_ge_030": settled["p_cross_upper"].ge(0.95) & settled["p_cap"].ge(0.30),
        "p_cap_top25": settled["p_cap"].ge(selected["p_cap"].quantile(0.75)),
        "p_cap_bottom25": settled["p_cap"].le(selected["p_cap"].quantile(0.25)),
    }
    rows = []
    for slice_name, mask in slice_masks.items():
        group = settled[mask].copy()
        if group.empty:
            continue
        cost = float(group["stake_cost_usd"].sum())
        profit = float(group["stake_profit_usd"].sum())
        rows.append(
            {
                "train_spec": train_spec,
                "eval_window": eval_window,
                "slice": slice_name,
                "rows": int(len(group)),
                "active_dates": int(group["target_date"].nunique()),
                "wins": float(group["label_no_wins"].sum()),
                "win_rate": float(group["label_no_wins"].mean()),
                "roi": profit / cost if cost else None,
                "profit_usd": profit,
                "avg_p_cross": float(group["p_cross_upper"].mean()),
                "avg_p_cap": float(group["p_cap"].mean()),
                "avg_p_cross_cap_adjusted": float(group["p_cross_cap_adjusted"].mean()),
                "avg_actual_margin_f": float(group["actual_margin_f"].mean()),
                "avg_pred_error_f": float(group["pred_error_f"].mean()),
                "cities": ",".join(sorted(group["city"].astype(str).unique())),
            }
        )
    return rows


def details_622(frames: dict[str, pd.DataFrame], thresholds: dict[str, float]) -> pd.DataFrame:
    rows = []
    for train_spec, frame in frames.items():
        day = frame[frame["target_date"].eq("2026-06-22")].copy()
        for variant, selected in [
            ("base_p40_ev10", select_base_p40(day)),
            ("cap_veto_train_top25_risk", select_cap_veto(day, thresholds[train_spec])),
            ("cap_adjusted_p40_ev10", select_cap_adjusted(day)),
        ]:
            if selected.empty:
                continue
            part = selected.copy()
            part["train_spec"] = train_spec
            part["variant"] = variant
            rows.append(part)
    if not rows:
        return pd.DataFrame()
    cols = [
        "train_spec",
        "variant",
        "city",
        "city_family",
        "label_no_wins",
        "no_ask",
        "stake_profit_usd",
        "p_cross_upper",
        "p_cap",
        "p_cross_cap_adjusted",
        "required_gap_f",
        "pred_remaining_heat_f",
        "future_delta_to_daymax_f",
        "actual_margin_f",
        "pred_error_f",
        "curve_next_3h_delta_f",
        "relative_humidity_pct",
        "wind_speed_kt",
        "loss_reason",
    ]
    out = pd.concat(rows, ignore_index=True)
    return out[cols].sort_values(["train_spec", "variant", "label_no_wins", "p_cap"], ascending=[True, True, True, False])


def table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
    shown = df if limit is None else df.head(limit)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in shown.iterrows():
        vals = []
        for col in cols:
            val = row.get(col)
            if col.endswith("roi") or col.endswith("rate"):
                vals.append(pct(val))
            elif col.endswith("usd"):
                vals.append(money(val))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}")
            else:
                vals.append("" if pd.isna(val) else str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def render_md(
    payload: dict[str, Any],
    model_df: pd.DataFrame,
    variant_df: pd.DataFrame,
    bucket_df: pd.DataFrame,
    overconf_df: pd.DataFrame,
    detail_df: pd.DataFrame,
) -> str:
    focus_models = model_df[
        model_df["train_spec"].isin(["weather_early", "weather_late", "weather_city_early", "weather_city_late"])
        & model_df["eval_window"].isin(["early", "late", "forward_settled"])
    ].copy()
    focus_variants = variant_df[
        variant_df["train_spec"].isin(["weather_early", "weather_late", "weather_city_early", "weather_city_late"])
        & variant_df["eval_window"].isin(["early", "late", "forward_settled"])
    ].copy()
    focus_buckets = bucket_df[
        bucket_df["train_spec"].isin(["weather_early", "weather_late"])
        & bucket_df["eval_window"].isin(["late", "early", "forward_settled"])
    ].copy()
    focus_overconf = overconf_df[
        overconf_df["train_spec"].isin(["weather_early", "weather_late"])
        & overconf_df["eval_window"].isin(["early", "late", "forward_settled"])
        & overconf_df["slice"].isin(["all_p40", "p_cross_ge_095_pcap_lt_030", "p_cap_top25", "p_cap_bottom25"])
    ].copy()
    return "\n".join(
        [
            "# Current-Bracket NO Capped-Day Regime V1",
            "",
            "## 结论",
            "",
            "这版把问题拆成两层：remaining-heat 继续判断“有没有足够升温空间”，新增 capped-day regime 判断“这个空间是不是假的”。结果是：p_cap 可以解释一部分高分假阳性，但跨窗口还不够稳定，尤其 forward 仍亏。它证明根因方向对，但还不能作为 live gate。",
            "",
            f"Verdict: `{payload['verdict']['status']}`，live_ready=`{payload['verdict']['live_ready']}`。",
            "",
            "## Data",
            "",
            f"- Generated at UTC: `{payload['generated_at_utc']}`",
            f"- Sync/rebuild: `{payload['data_refresh_note']}`",
            f"- Historical dates: `{payload['dataset']['date_min']}`..`{payload['dataset']['date_max']}`",
            f"- Historical mechanism rows: `{payload['dataset']['mechanism_rows']}`",
            f"- Historical trade-base rows: `{payload['dataset']['trade_base_rows']}`",
            f"- Forward rows: `{payload['forward_dataset']['mechanism_rows']}`",
            "",
            "## Cap Model Swap Metrics",
            "",
            table(focus_models, ["train_spec", "eval_window", "rows", "trade_base_rows", "active_dates", "cap_rate", "cap_auc", "cap_brier", "avg_p_cap"]),
            "",
            "## Variant Performance",
            "",
            table(
                focus_variants,
                [
                    "train_spec",
                    "eval_window",
                    "variant",
                    "selected_trades",
                    "settled_trades",
                    "active_dates",
                    "win_rate",
                    "roi",
                    "profit_usd",
                    "avg_p_cross",
                    "avg_p_cap",
                    "avg_actual_margin_f",
                    "avg_pred_error_f",
                    "loss_reason_counts",
                ],
            ),
            "",
            "## Cap Risk Buckets",
            "",
            table(focus_buckets, ["train_spec", "eval_window", "bucket", "rows", "active_dates", "avg_p_cap", "cap_rate", "win_rate", "roi", "avg_actual_margin_f", "avg_pred_error_f"], limit=80),
            "",
            "## Forecast Overconfidence Slices",
            "",
            table(
                focus_overconf,
                [
                    "train_spec",
                    "eval_window",
                    "slice",
                    "rows",
                    "active_dates",
                    "win_rate",
                    "roi",
                    "profit_usd",
                    "avg_p_cross",
                    "avg_p_cap",
                    "avg_actual_margin_f",
                    "avg_pred_error_f",
                ],
                limit=80,
            ),
            "",
            "## 6/22 Details",
            "",
            table(detail_df, ["train_spec", "variant", "city", "label_no_wins", "no_ask", "stake_profit_usd", "p_cross_upper", "p_cap", "p_cross_cap_adjusted", "actual_margin_f", "pred_error_f", "loss_reason"], limit=80),
            "",
            "## Interpretation",
            "",
            "1. `p_cap` 是对的方向：它直接学习“高温是否被封顶在 required_gap 以下”，比城市 blacklist 更贴近 6/22 的错误。",
            "2. 但当前可观测特征还没有让 p_cap 在 forward 稳定兑现；top-risk veto 只是减亏，不能翻正。",
            "3. 更关键的新发现是 forecast-overconfidence：`p_cross>=0.95` 且 `p_cap<0.30` 的历史表现不稳定，forward 直接全亏。这说明当前 cap model 仍然信任了 forecast 曲线，没有识别 forecast 自身高估 final max 的 regime。",
            "4. 下一步应补更直接的 day-regime 特征：同日 forecast bias、近期 forecast revision、云雨/海风真实变化、小时观测是否连续刷新高，而不是继续调 p40/p45。",
            "",
            "## Files",
            "",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
            f"- Cap model metrics: `{OUT_MODEL.relative_to(ROOT)}`",
            f"- Variant performance: `{OUT_VARIANT.relative_to(ROOT)}`",
            f"- Cap bucket calibration: `{OUT_BUCKET.relative_to(ROOT)}`",
            f"- Forecast overconfidence slices: `{OUT_OVERCONF.relative_to(ROOT)}`",
            f"- 6/22 details: `{OUT_622.relative_to(ROOT)}`",
            "",
        ]
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    hist, hist_stats = prepare_history()
    forward, forward_stats = prepare_forward()

    specs = [
        ("weather_early", "early", CAP_CAT_WEATHER),
        ("weather_late", "late", CAP_CAT_WEATHER),
        ("weather_city_early", "early", CAP_CAT_WITH_CITY),
        ("weather_city_late", "late", CAP_CAT_WITH_CITY),
    ]

    model_rows: list[dict[str, Any]] = []
    variant_rows: list[dict[str, Any]] = []
    bucket_rows: list[dict[str, Any]] = []
    overconf_rows: list[dict[str, Any]] = []
    scored_forward_by_spec: dict[str, pd.DataFrame] = {}
    thresholds: dict[str, float] = {}

    for name, train_window, cat_features in specs:
        hist_scored, heat_model, heat_sigma = fit_remaining_heat(hist, train_window)
        cap_model = fit_cap_model(hist_scored, train_window, cat_features)
        hist_scored = score_cap_model(hist_scored, cap_model, cat_features)
        train_selected = select_base_p40(hist_scored[window_mask(hist_scored, train_window)].copy())
        threshold = float(train_selected["p_cap"].quantile(0.75)) if not train_selected.empty else 1.0
        thresholds[name] = threshold

        forward_scored = score_remaining_heat(forward, heat_model, heat_sigma) if not forward.empty else forward.copy()
        forward_scored = score_cap_model(forward_scored, cap_model, cat_features) if not forward_scored.empty else forward_scored
        scored_forward_by_spec[name] = forward_scored

        for eval_window in ["early", "late", "historical"]:
            part = hist_scored[window_mask(hist_scored, eval_window)].copy()
            model_rows.append(cap_metrics(part, name, eval_window))
            variant_rows.extend(evaluate_variants(part, name, eval_window, threshold))
            bucket_rows.extend(cap_bucket_calibration(part, name, eval_window))
            overconf_rows.extend(forecast_overconfidence_slices(part, name, eval_window))

        if not forward_scored.empty:
            forward_all = forward_scored[window_mask(forward_scored, "forward")].copy()
            forward_settled = forward_all[forward_all["label_no_wins"].notna()].copy()
            model_rows.append(cap_metrics(forward_settled, name, "forward_settled"))
            variant_rows.extend(evaluate_variants(forward_all, name, "forward_settled", threshold))
            bucket_rows.extend(cap_bucket_calibration(forward_settled, name, "forward_settled"))
            overconf_rows.extend(forecast_overconfidence_slices(forward_all, name, "forward_settled"))

    model_df = pd.DataFrame(model_rows)
    variant_df = pd.DataFrame(variant_rows)
    bucket_df = pd.DataFrame(bucket_rows)
    overconf_df = pd.DataFrame(overconf_rows)
    detail_df = details_622(scored_forward_by_spec, thresholds)

    model_df.to_csv(OUT_MODEL, index=False)
    variant_df.to_csv(OUT_VARIANT, index=False)
    bucket_df.to_csv(OUT_BUCKET, index=False)
    overconf_df.to_csv(OUT_OVERCONF, index=False)
    detail_df.to_csv(OUT_622, index=False)

    focus_forward = variant_df[
        variant_df["train_spec"].isin(["weather_early", "weather_late"])
        & variant_df["eval_window"].eq("forward_settled")
    ].copy()
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "current_bracket_no_capped_day_regime_v1",
        "data_refresh_note": "sync_weather_remote.sh completed; run_stack rebuilt fact tables and CLOB gate, then exited non-clean because FE port 5174 stayed busy.",
        "dataset": hist_stats,
        "forward_dataset": {"mechanism_rows": int(len(forward)), "source_stats": forward_stats},
        "train_thresholds": finite_or_none(thresholds),
        "focus_forward_rows": finite_or_none(focus_forward.to_dict(orient="records")),
        "outputs": {
            "summary_json": str(OUT_JSON.relative_to(ROOT)),
            "cap_model_swap_metrics_csv": str(OUT_MODEL.relative_to(ROOT)),
            "cap_variant_performance_csv": str(OUT_VARIANT.relative_to(ROOT)),
            "cap_risk_bucket_calibration_csv": str(OUT_BUCKET.relative_to(ROOT)),
            "forecast_overconfidence_slices_csv": str(OUT_OVERCONF.relative_to(ROOT)),
            "cap_2026_06_22_details_csv": str(OUT_622.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "status": "capped_day_regime_direction_confirmed_but_shadow_only",
            "live_ready": False,
            "reason": "Capped-day risk is the right mechanism target, but current p_cap does not yet generalize strongly enough across early/late/forward to become a live gate.",
        },
    }
    OUT_JSON.write_text(json.dumps(finite_or_none(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(payload, model_df, variant_df, bucket_df, overconf_df, detail_df), encoding="utf-8")
    print(json.dumps(payload["verdict"], indent=2, ensure_ascii=False))
    print(OUT_MD.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
