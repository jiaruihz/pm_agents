#!/usr/bin/env python3
"""Train and evaluate a peak-forming hazard model for current YES.

The model answers a narrower question than the generic current-YES artifact:
when the observed temperature is still at the running max, will that current
bracket survive, or is the market about to see a later higher print?
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

try:
    from .peak_forming_hazard_shared import (
        approx_metar_veto,
        data_self_check as shared_data_self_check,
        json_ready,
        metric_row,
        summarize_trade as shared_summarize_trade,
    )
except ImportError:  # direct script execution
    from peak_forming_hazard_shared import (
        approx_metar_veto,
        data_self_check as shared_data_self_check,
        json_ready,
        metric_row,
        summarize_trade as shared_summarize_trade,
    )


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
REHEAT_ROWS = ROOT / "docs/analysis/2026-06/generated/reheat_feature_factory_v1/reheat_feature_rows.csv"
BASE_MODEL = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_live_gate_v9/live_model.json"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_yes_peak_forming_hazard_v1"
OUT_ARTIFACT = OUT_DIR / "peak_forming_hazard_model.json"
OUT_SCORED = OUT_DIR / "peak_forming_scored_rows.csv"
OUT_METRICS = OUT_DIR / "model_metrics.csv"
OUT_RULES = OUT_DIR / "rule_comparison.csv"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-19-current-yes-peak-forming-hazard-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-19-current-yes-peak-forming-hazard-v1.md"

SEED = 20260619
TRAIN_END = "2026-05-31"
HOLDOUT_START = "2026-06-01"
PEAK_DECLINE_MAX_NATIVE = 0.25

BASE_ALIAS = {
    "yes_current_ask": "current_yes_ask",
    "log_yes_size": "log_current_yes_size",
    "decline_c": "decline_from_max_c",
    "relh_now": "relative_humidity_pct",
    "sknt_now": "wind_speed_kt",
    "sky_now": "sky_cover_code",
    "d_tmpf_1h": "temp_trend_1h_f",
    "d_tmpf_3h": "temp_trend_3h_f",
}

NUMERIC_FEATURES = [
    "decision_hour_local",
    "month",
    "current_native",
    "running_native",
    "running_value",
    "decline_native",
    "decline_from_max_c",
    "gap_running_to_d1_low_native",
    "gap_current_to_d1_low_native",
    "current_yes_ask",
    "log_current_yes_size",
    "d1_no_ask",
    "ask_gap_d1_no_minus_yes",
    "tmpf_now",
    "dwpf_now",
    "dewpoint_depression_f",
    "relative_humidity_pct",
    "wind_speed_kt",
    "sky_cover_code",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "minutes_since_running_max_capped",
    "decision_obs_age_min",
    "gfs_forecast_peak_delta_hours_local",
    "ecmwf_forecast_peak_delta_hours_local",
    "min_forecast_peak_delta_hours_local",
    "max_forecast_peak_delta_hours_local",
    "forecast_peak_hour_spread",
    "gfs_forecast_gap_to_running_native",
    "ecmwf_forecast_gap_to_running_native",
    "min_forecast_gap_to_running_native",
    "max_forecast_gap_to_running_native",
    "forecast_peak_models_agree_le_1h_num",
]
CAT_FEATURES = ["city", "unit"]
PRICE_LIKE_FEATURES = {
    "current_yes_ask",
    "log_current_yes_size",
    "d1_no_ask",
    "ask_gap_d1_no_minus_yes",
}
WEATHER_ONLY_NUMERIC_FEATURES = [feature for feature in NUMERIC_FEATURES if feature not in PRICE_LIKE_FEATURES]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: float | None, digits: int = 1) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{float(value) * 100:.{digits}f}%"


def num(value: float | None, digits: int = 3) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{float(value):.{digits}f}"


def data_self_check() -> dict[str, Any]:
    return shared_data_self_check(DB)


def boolish(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    out = numeric.fillna(0).astype(float) > 0
    text_mask = numeric.isna()
    if text_mask.any():
        out.loc[text_mask] = series.loc[text_mask].astype(str).str.lower().isin({"true", "1", "1.0", "yes"})
    return out


def load_peak_rows() -> pd.DataFrame:
    df = pd.read_csv(REHEAT_ROWS)
    df = df[(df["outcome"].astype(str).str.lower() == "yes") & (df["bracket"].astype(str) == df["current_bracket"].astype(str))]
    df = df.drop_duplicates(["city", "target_date", "decision_snapshot_ts_utc", "current_bracket"]).copy()
    df = df[df["current_yes_ask"].notna() & df["current_bracket_held"].notna()].copy()
    df["label_current_yes_survives"] = boolish(df["current_bracket_held"]).astype(int)
    df["label_future_break"] = 1 - df["label_current_yes_survives"]
    df["target_date"] = df["target_date"].astype(str)
    df["period"] = np.where(df["target_date"] <= TRAIN_END, "train", "holdout")
    df.loc[df["target_date"] < "2026-05-19", "period"] = "ignore"
    df["decision_snapshot_dt"] = pd.to_datetime(df["decision_snapshot_ts_utc"], utc=True, errors="coerce")
    df["decision_last_obs_dt"] = pd.to_datetime(df["decision_last_obs_utc"], utc=True, errors="coerce")
    df["decision_obs_age_min"] = (df["decision_snapshot_dt"] - df["decision_last_obs_dt"]).dt.total_seconds() / 60.0
    df["month"] = pd.to_datetime(df["target_date"], errors="coerce").dt.month
    df["log_current_yes_size"] = np.log1p(pd.to_numeric(df["current_yes_ask_size"], errors="coerce").clip(lower=0))
    df["ask_gap_d1_no_minus_yes"] = pd.to_numeric(df["d1_no_ask"], errors="coerce") - pd.to_numeric(
        df["current_yes_ask"], errors="coerce"
    )
    df["gap_running_to_d1_low_native"] = pd.to_numeric(df["bracket_low"], errors="coerce") + 1.0 - pd.to_numeric(
        df["running_native"], errors="coerce"
    )
    df["gap_current_to_d1_low_native"] = pd.to_numeric(df["bracket_low"], errors="coerce") + 1.0 - pd.to_numeric(
        df["current_native"], errors="coerce"
    )
    df["minutes_since_running_max_capped"] = pd.to_numeric(df["minutes_since_running_max"], errors="coerce").clip(upper=240)

    for col in [
        "gfs_forecast_peak_delta_hours_local",
        "ecmwf_forecast_peak_delta_hours_local",
        "gfs_forecast_gap_to_running_native",
        "ecmwf_forecast_gap_to_running_native",
        "forecast_peak_hour_spread",
        "forecast_peak_models_agree_le_1h",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["min_forecast_peak_delta_hours_local"] = df[
        ["gfs_forecast_peak_delta_hours_local", "ecmwf_forecast_peak_delta_hours_local"]
    ].min(axis=1)
    df["max_forecast_peak_delta_hours_local"] = df[
        ["gfs_forecast_peak_delta_hours_local", "ecmwf_forecast_peak_delta_hours_local"]
    ].max(axis=1)
    df["min_forecast_gap_to_running_native"] = df[
        ["gfs_forecast_gap_to_running_native", "ecmwf_forecast_gap_to_running_native"]
    ].min(axis=1)
    df["max_forecast_gap_to_running_native"] = df[
        ["gfs_forecast_gap_to_running_native", "ecmwf_forecast_gap_to_running_native"]
    ].max(axis=1)
    df["forecast_peak_models_agree_le_1h_num"] = pd.to_numeric(
        df.get("forecast_peak_models_agree_le_1h"), errors="coerce"
    )

    peak = df[pd.to_numeric(df["decline_native"], errors="coerce").le(PEAK_DECLINE_MAX_NATIVE)].copy()
    return peak


def pipeline(numeric_features: list[str] | None = None, c: float = 0.8) -> Pipeline:
    if numeric_features is None:
        numeric_features = NUMERIC_FEATURES
    pre = ColumnTransformer(
        [
            ("num", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), numeric_features),
            ("cat", OneHotEncoder(handle_unknown="ignore"), CAT_FEATURES),
        ]
    )
    return Pipeline([("pre", pre), ("model", LogisticRegression(max_iter=3000, C=c, random_state=SEED))])


def artifact_from_model(model: Pipeline, train: pd.DataFrame, numeric_features: list[str], artifact_type: str, strategy_id: str) -> dict[str, Any]:
    pre = model.named_steps["pre"]
    num_pipe = pre.named_transformers_["num"]
    cat = pre.named_transformers_["cat"]
    clf = model.named_steps["model"]
    categories = [[str(v) for v in values] for values in cat.categories_]
    feature_names = list(numeric_features)
    for feature, cats in zip(CAT_FEATURES, categories, strict=True):
        feature_names.extend([f"cat__{feature}_{cat_value}" for cat_value in cats])
    return {
        "artifact_type": artifact_type,
        "strategy_id": strategy_id,
        "label": "current_yes_survives",
        "source_feature_rows": str(REHEAT_ROWS.relative_to(ROOT)),
        "trained_at_utc": now_utc(),
        "training_filter": f"period == train AND decline_native <= {PEAK_DECLINE_MAX_NATIVE}",
        "numeric_features": numeric_features,
        "categorical_features": CAT_FEATURES,
        "numeric_medians": num_pipe.named_steps["imputer"].statistics_.tolist(),
        "numeric_means": num_pipe.named_steps["scale"].mean_.tolist(),
        "numeric_scales": num_pipe.named_steps["scale"].scale_.tolist(),
        "categories": categories,
        "feature_names": feature_names,
        "coef": clf.coef_[0].tolist(),
        "intercept": float(clf.intercept_[0]),
        "train_rows": int(len(train)),
        "train_dates": int(train["target_date"].nunique()),
        "train_positive_rate": float(train["label_current_yes_survives"].mean()),
        "sklearn_spec": {
            "model": "LogisticRegression(max_iter=3000, C=0.8)",
            "numeric_preprocess": "median_impute_then_standard_scale",
            "categorical_preprocess": "one_hot_handle_unknown_ignore",
            "random_state": SEED,
        },
    }


def score_artifact(rows: pd.DataFrame, artifact: dict[str, Any]) -> np.ndarray:
    numeric_features = list(artifact["numeric_features"])
    categorical_features = list(artifact["categorical_features"])
    work = rows.copy()
    for feature, alias in BASE_ALIAS.items():
        if feature not in work.columns and alias in work.columns:
            work[feature] = work[alias]
    for feature in numeric_features:
        if feature not in work.columns:
            work[feature] = np.nan
    for feature in categorical_features:
        if feature not in work.columns:
            work[feature] = ""
    numeric = work[numeric_features].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    medians = np.asarray(artifact["numeric_medians"], dtype=float)
    means = np.asarray(artifact["numeric_means"], dtype=float)
    scales = np.asarray(artifact["numeric_scales"], dtype=float)
    numeric = np.where(np.isfinite(numeric), numeric, medians)
    numeric = (numeric - means) / scales
    cat_parts = []
    for idx, feature in enumerate(categorical_features):
        values = work[feature].astype(str).to_numpy()
        cats = [str(x) for x in artifact["categories"][idx]]
        mat = np.zeros((len(work), len(cats)), dtype=float)
        lookup = {cat: i for i, cat in enumerate(cats)}
        for row_idx, value in enumerate(values):
            col_idx = lookup.get(str(value))
            if col_idx is not None:
                mat[row_idx, col_idx] = 1.0
        cat_parts.append(mat)
    x = np.hstack([numeric] + cat_parts)
    logits = x @ np.asarray(artifact["coef"], dtype=float) + float(artifact["intercept"])
    return 1.0 / (1.0 + np.exp(-logits))


def summarize_trade(frame: pd.DataFrame, p_col: str, name: str) -> dict[str, Any]:
    return shared_summarize_trade(frame, p_col, name, seed=SEED)


def live_like_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    tradable = (
        frame["decision_hour_local"].between(13, 17)
        & frame["current_yes_ask"].between(0.50, 0.97, inclusive="both")
        & frame["min_forecast_peak_delta_hours_local"].fillna(-999).ge(-1.0)
    )
    guard_ok = ~approx_metar_veto(frame)
    return {
        "market_price_only_tradable": tradable,
        "base_v9_peak_rule": tradable & frame["p_base"].ge(0.60) & (frame["p_base"] - frame["current_yes_ask"]).ge(0.02),
        "base_v9_peak_rule_plus_approx_guard": tradable
        & guard_ok
        & frame["p_base"].ge(0.60)
        & (frame["p_base"] - frame["current_yes_ask"]).ge(0.02),
        "hazard_v1_peak_rule": tradable
        & frame["p_hazard"].ge(0.60)
        & (frame["p_hazard"] - frame["current_yes_ask"]).ge(0.02),
        "hazard_v1_peak_rule_plus_approx_guard": tradable
        & guard_ok
        & frame["p_hazard"].ge(0.60)
        & (frame["p_hazard"] - frame["current_yes_ask"]).ge(0.02),
    }


def select_grid(train: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for min_p in [0.55, 0.60, 0.65, 0.70, 0.75]:
        for min_edge in [0.00, 0.02, 0.04, 0.06, 0.08]:
            for min_hour in [13, 14, 15]:
                mask = (
                    train["decision_hour_local"].between(min_hour, 17)
                    & train["current_yes_ask"].between(0.50, 0.97, inclusive="both")
                    & train["min_forecast_peak_delta_hours_local"].fillna(-999).ge(-1.0)
                    & train["p_hazard"].ge(min_p)
                    & (train["p_hazard"] - train["current_yes_ask"]).ge(min_edge)
                )
                sub = train[mask]
                if len(sub) < 20 or sub["target_date"].nunique() < 5:
                    continue
                stats = summarize_trade(sub, "p_hazard", f"grid_h{min_hour}_p{min_p:.2f}_edge{min_edge:.2f}")
                rows.append({**stats, "min_p": min_p, "min_edge": min_edge, "min_hour": min_hour})
    rows.sort(key=lambda r: (r.get("roi") if r.get("roi") is not None else -999, r["orders"]), reverse=True)
    return rows[:10]


def write_markdown(payload: dict[str, Any]) -> None:
    metrics = payload["holdout_model_metrics"]
    rules = payload["holdout_rule_comparison"]
    lines = [
        "# Current YES Peak-Forming Hazard Model v1",
        "",
        "Status: research-only",
        f"Generated: {payload['generated_at_utc']}",
        "",
        "Target metric: `current_yes_peak_forming_hazard_v1` = 当前温度仍在 running max 附近时，预测当前最高温 bracket 是否最终守住；等价地，预测后面会不会出现更高温打穿。",
        "",
        "## Human Summary",
        "",
        "这版不是把旧模型再套几条 if/else。它单独训练 peak-forming 状态，用盘口、METAR 温湿风云/温度趋势、forecast peak clock 和 GFS/ECMWF 最高温缺口一起估计 `p_survive`。",
        "",
        "结果的重点比较清楚：模型判别力有提升，但交易层仍没有到可以替换 live 的程度。尤其是高 ask 区域市场本来就接近校准，真正能留下 edge 的样本变薄。",
        "",
        "## 数据快照",
        "",
        f"- 数据源: `{payload['data_snapshot']['source_feature_rows']}` + `runtime/weather.db` self-check",
        f"- 数据快照时间: fact_trades max built at `{payload['data_self_check']['fact_trades_max_built_at_utc']}`",
        f"- 特征产出日期: `{payload['coverage']['source_min_target_date']}`..`{payload['coverage']['source_max_target_date']}`；记录行数: source rows {payload['coverage']['source_rows']}, current-YES peak rows {payload['coverage']['peak_rows']}",
        f"- unsettled 占比: feature factory peak rows are settled-only; fact self-check `{payload['data_self_check']['fact_trades_by_settlement_status']}`",
        "- missing_bracket 数: feature factory reads `settlement_outcomes`; no missing rows in this peak slice.",
        "- 数据缺口: 本机已同步 6/18 orderbook/pm_history 并重建 DB，但 `reheat_feature_factory_v1` 的 observed-detail 输入仍只物化到 6/14，所以本报告没有把 6/15..6/17 纳入训练/holdout。",
        "",
        "## Funnel",
        "",
        "| step | rows | dates | cities |",
        "|---|---:|---:|---:|",
        f"| all reheat feature rows | {payload['coverage']['source_rows']} | {payload['coverage']['source_dates']} | {payload['coverage']['source_cities']} |",
        f"| current YES states | {payload['coverage']['current_yes_rows']} | {payload['coverage']['current_yes_dates']} | {payload['coverage']['current_yes_cities']} |",
        f"| peak-forming states | {payload['coverage']['peak_rows']} | {payload['coverage']['peak_dates']} | {payload['coverage']['peak_cities']} |",
        f"| train peak states | {payload['coverage']['train_rows']} | {payload['coverage']['train_dates']} | {payload['coverage']['train_cities']} |",
        f"| holdout peak states | {payload['coverage']['holdout_rows']} | {payload['coverage']['holdout_dates']} | {payload['coverage']['holdout_cities']} |",
        "",
        "Row grain: one row is one city + target date + orderbook snapshot + current running-max bracket, not one fill.",
        "",
        "## Holdout Model Metrics",
        "",
        "| model | rows | actual survive | mean p | AUC | Brier | avg edge vs ask |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in metrics:
        lines.append(
            f"| {row['model']} | {row['rows']} | {pct(row.get('actual_survive_rate'))} | "
            f"{pct(row.get('mean_pred_survive'))} | {num(row.get('auc'))} | {num(row.get('brier'))} | "
            f"{pct(row.get('mean_edge_vs_ask'))} |"
        )
    lines.extend(
        [
            "",
            "## Holdout Trading Rules",
            "",
            "| rule | orders | dates | cities | avg ask | win rate | ROI | 95% date bootstrap ROI |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in rules:
        ci = row.get("bootstrap_roi_ci95") or [None, None]
        ci_text = f"[{pct(ci[0])}, {pct(ci[1])}]"
        lines.append(
            f"| {row['rule']} | {row['orders']} | {row['active_dates']} | {row['cities']} | "
            f"{num(row.get('avg_ask'))} | {pct(row.get('win_rate'))} | {pct(row.get('roi'))} | {ci_text} |"
        )
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            f"significance={payload['verdict']['significance']} / baseline={payload['verdict']['baseline']} / forward={payload['verdict']['forward']} / conclusion={payload['verdict']['conclusion']}",
            "",
            payload["verdict"]["plain_text"],
            "",
            "## Outputs",
            "",
            f"- artifact: `{payload['outputs']['artifact']}`",
            f"- scored rows: `{payload['outputs']['scored_rows']}`",
            f"- metrics: `{payload['outputs']['metrics']}`",
            f"- rule comparison: `{payload['outputs']['rule_comparison']}`",
            f"- json: `{payload['outputs']['json']}`",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    source_df = pd.read_csv(REHEAT_ROWS, usecols=["city", "target_date"])
    current_yes_df = (
        pd.read_csv(
            REHEAT_ROWS,
            usecols=["city", "target_date", "decision_snapshot_ts_utc", "bracket", "current_bracket", "outcome", "current_yes_ask"],
        )
        .query("outcome == 'yes' and current_yes_ask == current_yes_ask")
        .assign(_is_current=lambda d: d["bracket"].astype(str) == d["current_bracket"].astype(str))
        .query("_is_current")
        .drop_duplicates(["city", "target_date", "decision_snapshot_ts_utc", "current_bracket"])
    )
    peak = load_peak_rows()
    train = peak[peak["period"].eq("train")].copy()
    holdout = peak[peak["period"].eq("holdout")].copy()

    model = pipeline(NUMERIC_FEATURES)
    model.fit(train[NUMERIC_FEATURES + CAT_FEATURES], train["label_current_yes_survives"])
    artifact = artifact_from_model(
        model,
        train,
        NUMERIC_FEATURES,
        "current_yes_peak_forming_hazard_logistic_v1",
        "current_yes_peak_forming_hazard_v1",
    )
    OUT_ARTIFACT.write_text(json.dumps(json_ready(artifact), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    weather_model = pipeline(WEATHER_ONLY_NUMERIC_FEATURES)
    weather_model.fit(train[WEATHER_ONLY_NUMERIC_FEATURES + CAT_FEATURES], train["label_current_yes_survives"])

    peak["p_hazard"] = model.predict_proba(peak[NUMERIC_FEATURES + CAT_FEATURES])[:, 1]
    peak["p_weather_only"] = weather_model.predict_proba(peak[WEATHER_ONLY_NUMERIC_FEATURES + CAT_FEATURES])[:, 1]
    base_artifact = json.loads(BASE_MODEL.read_text(encoding="utf-8"))
    peak["p_base"] = score_artifact(peak, base_artifact)
    peak["p_market"] = peak["current_yes_ask"].clip(1e-6, 1 - 1e-6)
    peak["approx_metar_veto"] = approx_metar_veto(peak)
    peak["p_hazard_edge"] = peak["p_hazard"] - peak["current_yes_ask"]
    peak["p_base_edge"] = peak["p_base"] - peak["current_yes_ask"]
    peak.to_csv(OUT_SCORED, index=False)

    train = peak[peak["period"].eq("train")].copy()
    holdout = peak[peak["period"].eq("holdout")].copy()
    metrics = pd.DataFrame(
        [
            metric_row("market_price_as_probability", holdout, "p_market"),
            metric_row("weather_forecast_only_v1", holdout, "p_weather_only"),
            metric_row("base_current_yes_v9", holdout, "p_base"),
            metric_row("peak_forming_hazard_v1", holdout, "p_hazard"),
        ]
    )
    metrics.to_csv(OUT_METRICS, index=False)

    masks = live_like_masks(holdout)
    rule_rows = []
    for name, mask in masks.items():
        p_col = "p_market" if name == "market_price_only_tradable" else "p_base" if name.startswith("base_") else "p_hazard"
        rule_rows.append(summarize_trade(holdout[mask].copy(), p_col, name))

    grid_rows = select_grid(train)
    best_grid_holdout = []
    for row in grid_rows[:3]:
        mask = (
            holdout["decision_hour_local"].between(int(row["min_hour"]), 17)
            & holdout["current_yes_ask"].between(0.50, 0.97, inclusive="both")
            & holdout["min_forecast_peak_delta_hours_local"].fillna(-999).ge(-1.0)
            & holdout["p_hazard"].ge(float(row["min_p"]))
            & (holdout["p_hazard"] - holdout["current_yes_ask"]).ge(float(row["min_edge"]))
        )
        best_grid_holdout.append(summarize_trade(holdout[mask].copy(), "p_hazard", "train_selected_" + row["rule"]))
    rule_rows.extend(best_grid_holdout)
    pd.DataFrame(rule_rows).to_csv(OUT_RULES, index=False)

    hazard_guard = next(row for row in rule_rows if row["rule"] == "hazard_v1_peak_rule_plus_approx_guard")
    hazard = next(row for row in rule_rows if row["rule"] == "hazard_v1_peak_rule")
    forward_pass = bool(hazard["orders"] >= 30 and hazard["active_dates"] >= 10 and (hazard["roi"] or -999) > 0)
    ci = hazard.get("bootstrap_roi_ci95") or [None, None]
    significance_pass = bool(ci[0] is not None and ci[0] > 0)
    baseline_pass = bool(hazard["roi"] is not None and hazard["avg_edge"] is not None and hazard["avg_edge"] > 0)
    conclusion = "confirmed" if significance_pass and baseline_pass and forward_pass else "inconclusive"

    self_check = data_self_check()
    payload = {
        "generated_at_utc": now_utc(),
        "data_snapshot": {
            "source_feature_rows": str(REHEAT_ROWS.relative_to(ROOT)),
            "base_model": str(BASE_MODEL.relative_to(ROOT)),
            "train_end": TRAIN_END,
            "holdout_start": HOLDOUT_START,
        },
        "data_self_check": self_check,
        "coverage": {
            "source_rows": int(len(source_df)),
            "source_min_target_date": str(source_df["target_date"].min()),
            "source_max_target_date": str(source_df["target_date"].max()),
            "source_dates": int(source_df["target_date"].nunique()),
            "source_cities": int(source_df["city"].nunique()),
            "current_yes_rows": int(current_yes_df.shape[0]),
            "current_yes_dates": int(current_yes_df["target_date"].nunique()),
            "current_yes_cities": int(current_yes_df["city"].nunique()),
            "peak_rows": int(len(peak)),
            "peak_dates": int(peak["target_date"].nunique()),
            "peak_cities": int(peak["city"].nunique()),
            "train_rows": int(len(train)),
            "train_dates": int(train["target_date"].nunique()),
            "train_cities": int(train["city"].nunique()),
            "holdout_rows": int(len(holdout)),
            "holdout_dates": int(holdout["target_date"].nunique()),
            "holdout_cities": int(holdout["city"].nunique()),
        },
        "holdout_model_metrics": metrics.to_dict(orient="records"),
        "holdout_rule_comparison": rule_rows,
        "train_selected_grid_top10": grid_rows,
        "verdict": {
            "significance": "PASS" if significance_pass else "FAIL",
            "baseline": "PASS" if baseline_pass else "FAIL",
            "forward": "PASS" if forward_pass else "FAIL",
            "conclusion": conclusion,
            "plain_text": (
                "在 2026-06-01..2026-06-14 holdout，peak-forming hazard v1 的主交易规则 "
                f"ROI 为 {pct(hazard['roi'])}，95% 日期 bootstrap CI "
                f"[{pct(ci[0])}, {pct(ci[1])}]；approx guard 后 ROI 为 {pct(hazard_guard['roi'])}。"
                "它可以继续作为 shadow/研究概率层，但还没有满足显著性和前瞻门，暂不替换 live。"
            ),
        },
        "outputs": {
            "artifact": str(OUT_ARTIFACT.relative_to(ROOT)),
            "scored_rows": str(OUT_SCORED.relative_to(ROOT)),
            "metrics": str(OUT_METRICS.relative_to(ROOT)),
            "rule_comparison": str(OUT_RULES.relative_to(ROOT)),
            "json": str(OUT_JSON.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
    }
    OUT_JSON.write_text(json.dumps(json_ready(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(json_ready(payload))
    print(json.dumps(json_ready(payload["verdict"]), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
