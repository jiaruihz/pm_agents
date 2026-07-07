#!/usr/bin/env python3
"""Compare base vs enriched PIT physical features for late-window residual legs.

This is research-only. It keeps orderbook price/depth out of model inputs and
uses price only after scoring to evaluate fee-adjusted EV policies.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
IN_DIR = ROOT / "docs/analysis/2026-07/generated/late_window_residual_heating_done_v1"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/late_window_residual_physical_feature_ablation_v2"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-07-late-window-residual-physical-feature-ablation-v2.md"

MODEL_ID_BASE = "late_window_residual_physical_base_logit_v2"
MODEL_ID_CLEAN = "late_window_residual_physical_clean_clock_tick_obs_logit_v2"
MODEL_ID_PLUS = "late_window_residual_physical_plus_logit_v2"
RANDOM_SEED = 20260707
FORWARD_START = "2026-06-29"
FORWARD_END = "2026-07-06"
FEE_RATE = 0.05
EDGE_THRESHOLDS = [0.0, 0.01, 0.02, 0.03]

BASE_NUMERIC_FEATURES = [
    "local_time_float",
    "forecast_peak_delta_hours_local",
    "forecast_gap_to_running_native",
    "decline_native",
    "target_distance_native",
    "forecast_margin_to_target_native",
    "metar_obs_count_today",
    "running_value",
]

BASE_CATEGORICAL_FEATURES = [
    "leg",
    "unit",
    "path_state",
    "peak_delta_bucket",
    "forecast_gap_bucket",
]

PLUS_NUMERIC_FEATURES = [
    *BASE_NUMERIC_FEATURES,
    "true_local_time_float",
    "hours_past_local_noon",
    "solar_runway_hours_proxy",
    "local_late_day_index",
    "native_tick_size",
    "target_distance_ticks",
    "forecast_margin_to_target_ticks",
    "forecast_gap_to_running_ticks",
    "decline_ticks",
    "latest_gap_to_running_native",
    "latest_gap_to_running_ticks",
    "obs_age_minutes",
    "obs_count_log1p",
    "forecast_peak_delta_abs",
]

PLUS_CATEGORICAL_FEATURES = [
    *BASE_CATEGORICAL_FEATURES,
    "forecast_source",
    "obs_age_bucket",
    "obs_count_bucket",
    "true_local_time_bucket",
    "solar_runway_bucket",
]

CLEAN_NUMERIC_FEATURES = [
    "true_local_time_float",
    "forecast_peak_delta_hours_local",
    "forecast_gap_to_running_ticks",
    "decline_ticks",
    "target_distance_ticks",
    "forecast_margin_to_target_ticks",
    "obs_count_log1p",
    "obs_age_minutes",
]

CLEAN_CATEGORICAL_FEATURES = [
    "leg",
    "unit",
    "path_state",
    "peak_delta_bucket",
    "forecast_gap_bucket",
    "true_local_time_bucket",
    "obs_age_bucket",
    "obs_count_bucket",
]


def safe_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return math.nan
        out = float(value)
        return out if math.isfinite(out) else math.nan
    except Exception:
        return math.nan


def fee_per_share(price: float) -> float:
    return round(FEE_RATE * price * (1.0 - price), 5)


def _dt_utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True)


def _dt_naive(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


def _bucket(values: pd.Series, bins: list[float], labels: list[str]) -> pd.Series:
    return pd.cut(values, bins=bins, labels=labels, include_lowest=True).astype("object").fillna("missing")


def add_feature_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    # Preserve the v1 clock feature for base parity; add true city-local clock
    # from ts_local because some historical capture rows carry Beijing hour in
    # local_hour/local_minute.
    out["local_time_float"] = pd.to_numeric(out["local_hour"], errors="coerce") + (
        pd.to_numeric(out["local_minute"], errors="coerce").fillna(0.0) / 60.0
    )
    ts_local = _dt_naive(out.get("ts_local", pd.Series(index=out.index, dtype=object)))
    out["true_local_time_float"] = ts_local.dt.hour + ts_local.dt.minute.fillna(0.0) / 60.0
    out["hours_past_local_noon"] = (out["true_local_time_float"] - 12.0).clip(lower=0.0)
    out["solar_runway_hours_proxy"] = (18.0 - out["true_local_time_float"]).clip(lower=0.0)
    out["local_late_day_index"] = ((out["true_local_time_float"] - 14.0) / 4.0).clip(lower=0.0, upper=1.0)

    unit = out.get("unit", pd.Series(index=out.index, dtype=object)).astype(str).str.upper()
    out["native_tick_size"] = np.where(unit.eq("F"), 2.0, 1.0)

    out["target_distance_native"] = (
        pd.to_numeric(out["target_bracket_low_native"], errors="coerce")
        - pd.to_numeric(out["running_value"], errors="coerce")
    )
    out["forecast_margin_to_target_native"] = (
        pd.to_numeric(out["target_bracket_low_native"], errors="coerce")
        - pd.to_numeric(out["forecast_max_native"], errors="coerce")
    )
    out["forecast_peak_delta_abs"] = pd.to_numeric(out["forecast_peak_delta_hours_local"], errors="coerce").abs()
    out["target_distance_ticks"] = out["target_distance_native"] / out["native_tick_size"]
    out["forecast_margin_to_target_ticks"] = out["forecast_margin_to_target_native"] / out["native_tick_size"]
    out["forecast_gap_to_running_ticks"] = (
        pd.to_numeric(out["forecast_gap_to_running_native"], errors="coerce") / out["native_tick_size"]
    )
    out["decline_ticks"] = pd.to_numeric(out["decline_native"], errors="coerce") / out["native_tick_size"]
    out["latest_gap_to_running_native"] = (
        pd.to_numeric(out["running_native"], errors="coerce") - pd.to_numeric(out["latest_native"], errors="coerce")
    )
    out["latest_gap_to_running_ticks"] = out["latest_gap_to_running_native"] / out["native_tick_size"]

    snap_ts = _dt_utc(out.get("snapshot_ts_utc", pd.Series(index=out.index, dtype=object)))
    metar_ts = _dt_utc(out.get("metar_latest_ts_utc", pd.Series(index=out.index, dtype=object)))
    out["obs_age_minutes"] = (snap_ts - metar_ts).dt.total_seconds() / 60.0
    out.loc[out["obs_age_minutes"].lt(0), "obs_age_minutes"] = np.nan
    out["obs_count_log1p"] = np.log1p(pd.to_numeric(out["metar_obs_count_today"], errors="coerce"))

    out["obs_age_bucket"] = _bucket(
        out["obs_age_minutes"],
        [-0.001, 15.0, 35.0, 65.0, 120.0, float("inf")],
        ["fresh_0_15m", "ok_15_35m", "stale_35_65m", "old_65_120m", "very_old_120m_plus"],
    )
    out["obs_count_bucket"] = _bucket(
        pd.to_numeric(out["metar_obs_count_today"], errors="coerce"),
        [-0.001, 5.0, 12.0, 24.0, float("inf")],
        ["few_0_5", "normal_6_12", "dense_13_24", "very_dense_25_plus"],
    )
    out["true_local_time_bucket"] = _bucket(
        out["true_local_time_float"],
        [-0.001, 10.0, 12.0, 14.0, 16.0, 18.0, 24.0],
        ["morning", "late_morning", "early_afternoon", "peak_window", "late_afternoon", "evening_or_later"],
    )
    out["solar_runway_bucket"] = _bucket(
        out["solar_runway_hours_proxy"],
        [-0.001, 0.5, 1.5, 3.0, float("inf")],
        ["exhausted", "thin", "usable", "open"],
    )

    for col in sorted(set(BASE_NUMERIC_FEATURES + PLUS_NUMERIC_FEATURES)):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    for col in sorted(set(BASE_CATEGORICAL_FEATURES + PLUS_CATEGORICAL_FEATURES)):
        out[col] = out[col].astype("object").where(out[col].notna(), "missing").astype(str)
    return out


def make_model(numeric_features: list[str], categorical_features: list[str]) -> Pipeline:
    numeric = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
        ]
    )
    categorical = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore")),
        ]
    )
    pre = ColumnTransformer(
        transformers=[
            ("numeric", numeric, numeric_features),
            ("categorical", categorical, categorical_features),
        ]
    )
    return Pipeline(
        steps=[
            ("pre", pre),
            (
                "model",
                LogisticRegression(
                    C=0.35,
                    max_iter=2000,
                    random_state=RANDOM_SEED,
                ),
            ),
        ]
    )


def expanding_predictions(
    df: pd.DataFrame, numeric_features: list[str], categorical_features: list[str]
) -> pd.Series:
    preds = pd.Series(np.nan, index=df.index, dtype=float)
    features = numeric_features + categorical_features
    for target_date in sorted(df["target_date"].astype(str).unique()):
        train_mask = df["target_date"].astype(str).lt(target_date)
        test_mask = df["target_date"].astype(str).eq(target_date)
        if int(train_mask.sum()) < 80 or df.loc[train_mask, "win"].nunique() < 2:
            continue
        model = make_model(numeric_features, categorical_features)
        model.fit(df.loc[train_mask, features], df.loc[train_mask, "win"].astype(int))
        preds.loc[test_mask] = model.predict_proba(df.loc[test_mask, features])[:, 1]
    return preds


def lodo_predictions(df: pd.DataFrame, numeric_features: list[str], categorical_features: list[str]) -> pd.Series:
    preds = pd.Series(np.nan, index=df.index, dtype=float)
    features = numeric_features + categorical_features
    for target_date in sorted(df["target_date"].astype(str).unique()):
        train_mask = df["target_date"].astype(str).ne(target_date)
        test_mask = df["target_date"].astype(str).eq(target_date)
        if int(train_mask.sum()) < 50 or df.loc[train_mask, "win"].nunique() < 2:
            continue
        model = make_model(numeric_features, categorical_features)
        model.fit(df.loc[train_mask, features], df.loc[train_mask, "win"].astype(int))
        preds.loc[test_mask] = model.predict_proba(df.loc[test_mask, features])[:, 1]
    return preds


def fit_full_model(df: pd.DataFrame, numeric_features: list[str], categorical_features: list[str]) -> Pipeline:
    features = numeric_features + categorical_features
    model = make_model(numeric_features, categorical_features)
    model.fit(df[features], df["win"].astype(int))
    return model


def metric_row(label: str, scope: str, y: pd.Series, p: pd.Series, dates: pd.Series) -> dict[str, Any]:
    mask = y.notna() & p.notna()
    yy = y[mask].astype(float)
    pp = p[mask].astype(float).clip(1e-6, 1.0 - 1e-6)
    if yy.empty:
        return {"model": label, "scope": scope, "rows": 0}
    try:
        auc = roc_auc_score(yy, pp) if yy.nunique() == 2 else math.nan
    except ValueError:
        auc = math.nan
    return {
        "model": label,
        "scope": scope,
        "rows": int(len(yy)),
        "dates": int(dates[mask].nunique()),
        "hit_rate": float(yy.mean()),
        "logloss": float(log_loss(yy, pp, labels=[0.0, 1.0])),
        "brier": float(brier_score_loss(yy, pp)),
        "auc": auc,
        "avg_p": float(pp.mean()),
    }


def coefficient_table(
    model: Pipeline, label: str, numeric_features: list[str], categorical_features: list[str]
) -> pd.DataFrame:
    del numeric_features, categorical_features
    pre: ColumnTransformer = model.named_steps["pre"]
    clf: LogisticRegression = model.named_steps["model"]
    return pd.DataFrame(
        [
            {"model": label, "feature": name, "coef": float(coef), "abs_coef": float(abs(coef))}
            for name, coef in zip(pre.get_feature_names_out(), clf.coef_[0], strict=False)
        ]
    ).sort_values(["model", "abs_coef"], ascending=[True, False])


def add_trade_math(df: pd.DataFrame, pred_col: str) -> pd.DataFrame:
    out = df.copy()
    out["entry_price"] = pd.to_numeric(out["entry_price"], errors="coerce")
    out["fee_per_share"] = out["entry_price"].map(lambda x: fee_per_share(float(x)) if pd.notna(x) else math.nan)
    out["cost_per_share"] = out["entry_price"] + out["fee_per_share"]
    out["p_model"] = pd.to_numeric(out[pred_col], errors="coerce")
    out["model_edge_per_share"] = out["p_model"] - out["cost_per_share"]
    out["win"] = pd.to_numeric(out["win"], errors="coerce")
    out["pnl_per_share"] = out["win"] - out["cost_per_share"]
    out["roi"] = out["pnl_per_share"] / out["cost_per_share"]
    return out


def block_ci_daily_roi(df: pd.DataFrame, reps: int = 3000, seed: int = RANDOM_SEED) -> tuple[float | None, float | None]:
    if df.empty or df["target_date"].nunique() < 2:
        return None, None
    daily = df.groupby("target_date", as_index=False).agg(cost=("cost_per_share", "sum"), pnl=("pnl_per_share", "sum"))
    values = daily[["cost", "pnl"]].to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    rois: list[float] = []
    for _ in range(reps):
        sample = values[rng.integers(0, len(values), len(values))]
        cost = float(sample[:, 0].sum())
        if cost > 0:
            rois.append(float(sample[:, 1].sum() / cost))
    if not rois:
        return None, None
    return float(np.quantile(rois, 0.025)), float(np.quantile(rois, 0.975))


def summarize_policy(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for key, g in df.groupby(group_cols, dropna=False, observed=True):
        g = g.dropna(subset=["win", "cost_per_share", "pnl_per_share"])
        if g.empty:
            continue
        cost = float(g["cost_per_share"].sum())
        pnl = float(g["pnl_per_share"].sum())
        ci_low, ci_high = block_ci_daily_roi(g)
        rec: dict[str, Any] = {
            "rows": int(len(g)),
            "active_dates": int(g["target_date"].nunique()),
            "cities": int(g["city"].nunique()),
            "avg_p": float(g["p_model"].mean()),
            "avg_entry": float(g["entry_price"].mean()),
            "avg_edge": float(g["model_edge_per_share"].mean()),
            "hit_rate": float(g["win"].mean()),
            "cost": cost,
            "pnl": pnl,
            "roi": pnl / cost if cost else math.nan,
            "roi_ci_low": ci_low,
            "roi_ci_high": ci_high,
        }
        vals = key if isinstance(key, tuple) else (key,)
        rec.update(dict(zip(group_cols, vals)))
        rows.append(rec)
    return pd.DataFrame(rows)


def policy_grid(scored: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    detail_frames: list[pd.DataFrame] = []
    for model_name, pred_col in [
        ("base_expanding", "p_leg_win_physical_base_expanding_v2"),
        ("clean_expanding", "p_leg_win_physical_clean_expanding_v2"),
        ("plus_expanding", "p_leg_win_physical_plus_expanding_v2"),
    ]:
        base = add_trade_math(scored, pred_col)
        base = base[
            base["target_date"].astype(str).between(FORWARD_START, FORWARD_END, inclusive="both")
            & base["p_model"].notna()
            & base["entry_price"].notna()
            & base["win"].notna()
        ].copy()
        for threshold in EDGE_THRESHOLDS:
            chosen = base[base["model_edge_per_share"].ge(threshold)].copy()
            chosen["model"] = model_name
            chosen["edge_threshold"] = threshold
            detail_frames.append(chosen)
    details = pd.concat(detail_frames, ignore_index=True) if detail_frames else pd.DataFrame()
    if details.empty:
        return pd.DataFrame(), details
    grid = summarize_policy(details, ["model", "edge_threshold"])
    by_leg = summarize_policy(details, ["model", "leg", "edge_threshold"])
    return pd.concat([grid.assign(slice="all"), by_leg.assign(slice="by_leg")], ignore_index=True), details


def markdown_table(df: pd.DataFrame, columns: list[str], max_rows: int = 40) -> str:
    if df.empty:
        return "_empty_"
    sub = df[columns].head(max_rows).copy()
    for col in sub.columns:
        if pd.api.types.is_float_dtype(sub[col]):
            sub[col] = sub[col].map(lambda x: "" if pd.isna(x) else f"{x:.4f}")
    sub = sub.fillna("")
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in sub.itertuples(index=False):
        lines.append("| " + " | ".join(str(x) for x in row) + " |")
    return "\n".join(lines)


def write_report(
    summary: dict[str, Any],
    metrics: pd.DataFrame,
    policy: pd.DataFrame,
    coefs: pd.DataFrame,
) -> None:
    policy_all = policy[policy["slice"].eq("all")].copy() if not policy.empty else pd.DataFrame()
    policy_d1 = (
        policy[policy["slice"].eq("by_leg") & policy["leg"].astype(str).eq("d1_no")].copy()
        if not policy.empty and "leg" in policy.columns
        else pd.DataFrame()
    )
    lines = [
        "# Late-Window Residual Physical Feature Ablation v2",
        "",
        "Status: `snapshot`",
        f"Model ids: `{MODEL_ID_BASE}`, `{MODEL_ID_CLEAN}`, `{MODEL_ID_PLUS}`",
        "",
        "## Verdict",
        summary["verdict"],
        "",
        "## Data",
        f"- Input first-cross rows: {summary['input_rows']}",
        f"- Settled model rows: {summary['model_rows']}",
        f"- Target dates: {summary['date_min']}..{summary['date_max']} ({summary['date_count']} dates)",
        f"- Forward window: {FORWARD_START}..{FORWARD_END}",
        "- Model inputs exclude orderbook price/depth/spread, settlement labels, PnL, and ROI.",
        "- `physical_clean` uses true city-local clock, F/C tick-normalized distances, and observation age/count.",
        "- `physical_plus` further adds forecast source, solar-window proxies, and native duplicate features; it is included as a stress test, not the recommended view.",
        "",
        "## Probability Metrics",
        markdown_table(metrics, ["model", "scope", "rows", "dates", "hit_rate", "logloss", "brier", "auc", "avg_p"], max_rows=30),
        "",
        "## Forward EV Policy: All Legs",
        markdown_table(
            policy_all,
            [
                "model",
                "edge_threshold",
                "rows",
                "active_dates",
                "cities",
                "avg_p",
                "avg_entry",
                "avg_edge",
                "hit_rate",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
            ],
            max_rows=20,
        ),
        "",
        "## Forward EV Policy: d1 NO",
        markdown_table(
            policy_d1,
            [
                "model",
                "edge_threshold",
                "rows",
                "active_dates",
                "cities",
                "avg_p",
                "avg_entry",
                "avg_edge",
                "hit_rate",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
            ],
            max_rows=20,
        ),
        "",
        "## Top Coefficients",
        markdown_table(coefs, ["model", "feature", "coef", "abs_coef"], max_rows=40),
        "",
        "## Recommendation",
        "- Keep `p_leg_win` as an EV scorer, not a done/not-done gate.",
        "- Prefer the lean `physical_clean` view over `physical_plus` unless additional forward dates show the larger view is not overfitting.",
        "- Do not start the shadow scorer yet in this step; first use this report to decide whether the feature view should be frozen as an as-of model manifest.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    source = pd.read_csv(IN_DIR / "first_cross_rows.csv")
    data = add_feature_columns(source)
    data = data[data["settled"].astype(bool)].copy()
    data = data.dropna(subset=["win"])
    data["win"] = pd.to_numeric(data["win"], errors="coerce").astype(float)

    data["p_market_entry_v1"] = pd.to_numeric(data["entry_price"], errors="coerce").clip(1e-6, 1 - 1e-6)
    data["p_heating_done_score_v1"] = pd.to_numeric(data["leg_residual_done_score_v1"], errors="coerce").clip(
        1e-6, 1 - 1e-6
    )

    data["p_leg_win_physical_base_lodo_v2"] = lodo_predictions(
        data, BASE_NUMERIC_FEATURES, BASE_CATEGORICAL_FEATURES
    )
    data["p_leg_win_physical_base_expanding_v2"] = expanding_predictions(
        data, BASE_NUMERIC_FEATURES, BASE_CATEGORICAL_FEATURES
    )
    data["p_leg_win_physical_clean_lodo_v2"] = lodo_predictions(
        data, CLEAN_NUMERIC_FEATURES, CLEAN_CATEGORICAL_FEATURES
    )
    data["p_leg_win_physical_clean_expanding_v2"] = expanding_predictions(
        data, CLEAN_NUMERIC_FEATURES, CLEAN_CATEGORICAL_FEATURES
    )
    data["p_leg_win_physical_plus_lodo_v2"] = lodo_predictions(
        data, PLUS_NUMERIC_FEATURES, PLUS_CATEGORICAL_FEATURES
    )
    data["p_leg_win_physical_plus_expanding_v2"] = expanding_predictions(
        data, PLUS_NUMERIC_FEATURES, PLUS_CATEGORICAL_FEATURES
    )

    base_full = fit_full_model(data, BASE_NUMERIC_FEATURES, BASE_CATEGORICAL_FEATURES)
    clean_full = fit_full_model(data, CLEAN_NUMERIC_FEATURES, CLEAN_CATEGORICAL_FEATURES)
    plus_full = fit_full_model(data, PLUS_NUMERIC_FEATURES, PLUS_CATEGORICAL_FEATURES)
    data["p_leg_win_physical_base_full_v2"] = base_full.predict_proba(
        data[BASE_NUMERIC_FEATURES + BASE_CATEGORICAL_FEATURES]
    )[:, 1]
    data["p_leg_win_physical_clean_full_v2"] = clean_full.predict_proba(
        data[CLEAN_NUMERIC_FEATURES + CLEAN_CATEGORICAL_FEATURES]
    )[:, 1]
    data["p_leg_win_physical_plus_full_v2"] = plus_full.predict_proba(
        data[PLUS_NUMERIC_FEATURES + PLUS_CATEGORICAL_FEATURES]
    )[:, 1]

    forward_mask = data["target_date"].astype(str).between(FORWARD_START, FORWARD_END, inclusive="both")
    metric_rows: list[dict[str, Any]] = []
    for label, col in [
        ("physical_base", "p_leg_win_physical_base_lodo_v2"),
        ("physical_clean", "p_leg_win_physical_clean_lodo_v2"),
        ("physical_plus", "p_leg_win_physical_plus_lodo_v2"),
        ("heuristic_leg_score", "p_heating_done_score_v1"),
        ("market_entry_price", "p_market_entry_v1"),
    ]:
        metric_rows.append(metric_row(label, "all_lodo", data["win"], data[col], data["target_date"]))
        metric_rows.append(
            metric_row(label, "forward_lodo", data.loc[forward_mask, "win"], data.loc[forward_mask, col], data.loc[forward_mask, "target_date"])
        )
    for label, col in [
        ("physical_base", "p_leg_win_physical_base_expanding_v2"),
        ("physical_clean", "p_leg_win_physical_clean_expanding_v2"),
        ("physical_plus", "p_leg_win_physical_plus_expanding_v2"),
        ("heuristic_leg_score", "p_heating_done_score_v1"),
        ("market_entry_price", "p_market_entry_v1"),
    ]:
        metric_rows.append(
            metric_row(label, "forward_expanding", data.loc[forward_mask, "win"], data.loc[forward_mask, col], data.loc[forward_mask, "target_date"])
        )
    metrics = pd.DataFrame(metric_rows)

    policy, details = policy_grid(data)
    coefs = pd.concat(
        [
            coefficient_table(base_full, "physical_base_full", BASE_NUMERIC_FEATURES, BASE_CATEGORICAL_FEATURES),
            coefficient_table(clean_full, "physical_clean_full", CLEAN_NUMERIC_FEATURES, CLEAN_CATEGORICAL_FEATURES),
            coefficient_table(plus_full, "physical_plus_full", PLUS_NUMERIC_FEATURES, PLUS_CATEGORICAL_FEATURES),
        ],
        ignore_index=True,
    )

    scored_cols = [
        "snapshot_ts_utc",
        "ts_local",
        "city",
        "target_date",
        "leg",
        "bracket",
        "unit",
        "win",
        "entry_price",
        "p_market_entry_v1",
        "p_heating_done_score_v1",
        "p_leg_win_physical_base_lodo_v2",
        "p_leg_win_physical_base_expanding_v2",
        "p_leg_win_physical_clean_lodo_v2",
        "p_leg_win_physical_clean_expanding_v2",
        "p_leg_win_physical_plus_lodo_v2",
        "p_leg_win_physical_plus_expanding_v2",
        "p_leg_win_physical_base_full_v2",
        "p_leg_win_physical_clean_full_v2",
        "p_leg_win_physical_plus_full_v2",
        *PLUS_NUMERIC_FEATURES,
        *PLUS_CATEGORICAL_FEATURES,
    ]
    data[scored_cols].to_csv(OUT_DIR / "scored_first_cross_rows.csv", index=False)
    metrics.to_csv(OUT_DIR / "metrics.csv", index=False)
    policy.to_csv(OUT_DIR / "policy_grid.csv", index=False)
    details.to_csv(OUT_DIR / "selected_rows.csv", index=False)
    coefs.to_csv(OUT_DIR / "model_coefficients.csv", index=False)

    forward_plus = metrics[
        metrics["model"].eq("physical_plus") & metrics["scope"].eq("forward_expanding")
    ].iloc[0]
    forward_clean = metrics[
        metrics["model"].eq("physical_clean") & metrics["scope"].eq("forward_expanding")
    ].iloc[0]
    forward_base = metrics[
        metrics["model"].eq("physical_base") & metrics["scope"].eq("forward_expanding")
    ].iloc[0]
    clean_policy_0 = policy[
        policy["slice"].eq("all") & policy["model"].eq("clean_expanding") & policy["edge_threshold"].eq(0.0)
    ]
    plus_policy_0 = policy[
        policy["slice"].eq("all") & policy["model"].eq("plus_expanding") & policy["edge_threshold"].eq(0.0)
    ]
    base_policy_0 = policy[
        policy["slice"].eq("all") & policy["model"].eq("base_expanding") & policy["edge_threshold"].eq(0.0)
    ]
    clean_roi = None if clean_policy_0.empty else float(clean_policy_0.iloc[0]["roi"])
    plus_roi = None if plus_policy_0.empty else float(plus_policy_0.iloc[0]["roi"])
    base_roi = None if base_policy_0.empty else float(base_policy_0.iloc[0]["roi"])

    verdict = (
        "The lean enriched PIT physical view is the cleaner next research view because it fixes the local-clock "
        "and F/C scale issues and adds observation freshness without using market price as a model input. "
        f"In strict expanding-forward probability validation, base logloss={forward_base['logloss']:.4f}, "
        f"Brier={forward_base['brier']:.4f}, AUC={forward_base['auc']:.4f}; clean logloss={forward_clean['logloss']:.4f}, "
        f"Brier={forward_clean['brier']:.4f}, AUC={forward_clean['auc']:.4f}; plus logloss={forward_plus['logloss']:.4f}, "
        f"Brier={forward_plus['brier']:.4f}, AUC={forward_plus['auc']:.4f}. "
        f"Forward edge>=0 EV replay ROI is base={base_roi:.1%}, clean={clean_roi:.1%}, plus={plus_roi:.1%}. "
        "Clean improves Brier slightly but not logloss, AUC, or all-leg EV; plus-all overfits more. "
        "The d1 NO clean slice remains the only constructive candidate, but it is still research-only."
    )
    summary = {
        "model_id_base": MODEL_ID_BASE,
        "model_id_clean": MODEL_ID_CLEAN,
        "model_id_plus": MODEL_ID_PLUS,
        "input_rows": int(len(source)),
        "model_rows": int(len(data)),
        "date_min": str(data["target_date"].min()),
        "date_max": str(data["target_date"].max()),
        "date_count": int(data["target_date"].nunique()),
        "base_numeric_features": BASE_NUMERIC_FEATURES,
        "base_categorical_features": BASE_CATEGORICAL_FEATURES,
        "clean_numeric_features": CLEAN_NUMERIC_FEATURES,
        "clean_categorical_features": CLEAN_CATEGORICAL_FEATURES,
        "plus_numeric_features": PLUS_NUMERIC_FEATURES,
        "plus_categorical_features": PLUS_CATEGORICAL_FEATURES,
        "excluded_input_fields": ["entry_price", "best_bid", "spread", "top_size", "depth_5c", "final_yes", "pnl", "roi"],
        "verdict": verdict,
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(summary, metrics, policy, coefs)
    print(json.dumps({"summary": summary, "out_dir": str(OUT_DIR.relative_to(ROOT)), "report": str(OUT_MD.relative_to(ROOT))}, indent=2))


if __name__ == "__main__":
    main()
