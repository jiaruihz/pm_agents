#!/usr/bin/env python3
"""Calibrate late-window residual physical features into probability features.

This is research/shadow-only. It consumes PIT rows already produced by
research_late_window_residual_heating_done_v1.py and joins no live state. The
model deliberately excludes orderbook price, spread, depth, and settlement
fields from its input features; those are used only for evaluation.
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
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/late_window_residual_calibrated_features_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-07-late-window-residual-calibrated-features-v1.md"

FORWARD_START = "2026-06-29"
FORWARD_END = "2026-07-04"
MODEL_ID = "late_window_residual_physical_logit_v1"
RANDOM_SEED = 20260707

NUMERIC_FEATURES = [
    "local_time_float",
    "forecast_peak_delta_hours_local",
    "forecast_gap_to_running_native",
    "decline_native",
    "target_distance_native",
    "forecast_margin_to_target_native",
    "metar_obs_count_today",
    "running_value",
]

CATEGORICAL_FEATURES = [
    "leg",
    "unit",
    "path_state",
    "peak_delta_bucket",
    "forecast_gap_bucket",
]


def safe_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return math.nan
        out = float(value)
        return out if math.isfinite(out) else math.nan
    except Exception:
        return math.nan


def add_model_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["local_time_float"] = pd.to_numeric(out["local_hour"], errors="coerce") + (
        pd.to_numeric(out["local_minute"], errors="coerce").fillna(0.0) / 60.0
    )
    out["target_distance_native"] = (
        pd.to_numeric(out["target_bracket_low_native"], errors="coerce")
        - pd.to_numeric(out["running_value"], errors="coerce")
    )
    out["forecast_margin_to_target_native"] = (
        pd.to_numeric(out["target_bracket_low_native"], errors="coerce")
        - pd.to_numeric(out["forecast_max_native"], errors="coerce")
    )
    for col in NUMERIC_FEATURES:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    for col in CATEGORICAL_FEATURES:
        out[col] = out[col].astype(str).fillna("missing")
    return out


def make_model() -> Pipeline:
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
            ("numeric", numeric, NUMERIC_FEATURES),
            ("categorical", categorical, CATEGORICAL_FEATURES),
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


def metric_row(label: str, y: pd.Series, p: pd.Series, dates: pd.Series) -> dict[str, Any]:
    mask = y.notna() & p.notna()
    yy = y[mask].astype(float)
    pp = p[mask].astype(float).clip(1e-6, 1.0 - 1e-6)
    if yy.empty:
        return {"model": label, "rows": 0}
    try:
        auc = roc_auc_score(yy, pp) if yy.nunique() == 2 else math.nan
    except ValueError:
        auc = math.nan
    return {
        "model": label,
        "rows": int(len(yy)),
        "dates": int(dates[mask].nunique()),
        "hit_rate": float(yy.mean()),
        "logloss": float(log_loss(yy, pp, labels=[0.0, 1.0])),
        "brier": float(brier_score_loss(yy, pp)),
        "auc": auc,
        "avg_p": float(pp.mean()),
    }


def leave_one_date_predictions(df: pd.DataFrame) -> pd.Series:
    preds = pd.Series(np.nan, index=df.index, dtype=float)
    for target_date in sorted(df["target_date"].astype(str).unique()):
        train_mask = df["target_date"].astype(str).ne(target_date)
        test_mask = df["target_date"].astype(str).eq(target_date)
        if int(train_mask.sum()) < 50 or df.loc[train_mask, "win"].nunique() < 2:
            continue
        model = make_model()
        model.fit(df.loc[train_mask, NUMERIC_FEATURES + CATEGORICAL_FEATURES], df.loc[train_mask, "win"].astype(int))
        preds.loc[test_mask] = model.predict_proba(df.loc[test_mask, NUMERIC_FEATURES + CATEGORICAL_FEATURES])[:, 1]
    return preds


def expanding_forward_predictions(df: pd.DataFrame) -> pd.Series:
    preds = pd.Series(np.nan, index=df.index, dtype=float)
    for target_date in sorted(df["target_date"].astype(str).unique()):
        train_mask = df["target_date"].astype(str).lt(target_date)
        test_mask = df["target_date"].astype(str).eq(target_date)
        if int(train_mask.sum()) < 80 or df.loc[train_mask, "win"].nunique() < 2:
            continue
        model = make_model()
        model.fit(df.loc[train_mask, NUMERIC_FEATURES + CATEGORICAL_FEATURES], df.loc[train_mask, "win"].astype(int))
        preds.loc[test_mask] = model.predict_proba(df.loc[test_mask, NUMERIC_FEATURES + CATEGORICAL_FEATURES])[:, 1]
    return preds


def fit_full_model(df: pd.DataFrame) -> Pipeline:
    model = make_model()
    model.fit(df[NUMERIC_FEATURES + CATEGORICAL_FEATURES], df["win"].astype(int))
    return model


def coefficient_table(model: Pipeline) -> pd.DataFrame:
    pre: ColumnTransformer = model.named_steps["pre"]
    clf: LogisticRegression = model.named_steps["model"]
    feature_names = list(pre.get_feature_names_out())
    coefs = clf.coef_[0]
    rows = [
        {"feature": name, "coef": float(coef), "abs_coef": float(abs(coef))}
        for name, coef in zip(feature_names, coefs, strict=False)
    ]
    return pd.DataFrame(rows).sort_values("abs_coef", ascending=False)


def probability_slices(df: pd.DataFrame, pred_col: str) -> pd.DataFrame:
    out = df.copy()
    out["p_bucket"] = pd.cut(
        out[pred_col],
        bins=[0.0, 0.25, 0.50, 0.70, 0.85, 1.0],
        labels=["0_25", "25_50", "50_70", "70_85", "85_100"],
        include_lowest=True,
    )
    rows = []
    for keys, g in out.groupby(["leg", "p_bucket"], observed=False):
        leg, bucket = keys
        settled = g.dropna(subset=["win", pred_col])
        if settled.empty:
            continue
        cost = float(settled["cost_per_share"].sum())
        pnl = float(settled["pnl_per_share"].sum())
        rows.append(
            {
                "leg": leg,
                "p_bucket": str(bucket),
                "rows": int(len(settled)),
                "dates": int(settled["target_date"].nunique()),
                "avg_p": float(settled[pred_col].mean()),
                "hit_rate": float(settled["win"].mean()),
                "avg_entry": float(settled["entry_price"].mean()),
                "roi": pnl / cost if cost else math.nan,
            }
        )
    return pd.DataFrame(rows)


def markdown_table(df: pd.DataFrame, columns: list[str], max_rows: int = 30) -> str:
    if df.empty:
        return "_empty_"
    sub = df[columns].head(max_rows).copy()
    for col in sub.columns:
        if pd.api.types.is_float_dtype(sub[col]):
            sub[col] = sub[col].map(lambda x: "" if pd.isna(x) else f"{x:.4f}")
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in sub.itertuples(index=False):
        lines.append("| " + " | ".join(str(x) for x in row) + " |")
    return "\n".join(lines)


def write_report(summary: dict[str, Any], metrics: pd.DataFrame, slices: pd.DataFrame, coefs: pd.DataFrame) -> None:
    lines = [
        "# Late-Window Residual Calibrated Features v1",
        "",
        "Status: `snapshot`",
        f"Model id: `{MODEL_ID}`",
        "",
        "## Verdict",
        "This run promotes the hand-scored heating/residual flags into a calibrated research feature candidate: `p_leg_win_physical_v1`. The model uses only PIT physical/state fields and excludes orderbook price, spread, depth, and settlement labels from inputs. It is useful as a shared feature candidate, but not live-ready: leave-one-date validation improves over the heuristic score, while strict expanding-forward support is still small and market price remains a strong execution baseline.",
        "",
        "## Data",
        f"- Input rows: {summary['input_rows']}",
        f"- Settled first-cross rows used for modeling: {summary['model_rows']}",
        f"- Target dates: {summary['date_min']}..{summary['date_max']} ({summary['date_count']} dates)",
        f"- Features: `{', '.join(NUMERIC_FEATURES + CATEGORICAL_FEATURES)}`",
        "- Excluded from model inputs: `entry_price`, `best_bid`, `spread`, `top_size`, `depth_5c`, `final_yes`, `settlement`, `pnl`.",
        "",
        "## Validation Metrics",
        markdown_table(metrics, ["model", "scope", "rows", "dates", "hit_rate", "logloss", "brier", "auc", "avg_p"], max_rows=20),
        "",
        "## Probability Slices",
        markdown_table(slices, ["leg", "p_bucket", "rows", "dates", "avg_p", "hit_rate", "avg_entry", "roi"], max_rows=40),
        "",
        "## Top Coefficients",
        markdown_table(coefs, ["feature", "coef", "abs_coef"], max_rows=25),
        "",
        "## Recommendation",
        "- Keep `heating_done_score_v1` as an interpretable state summary, not a probability.",
        "- Treat `p_leg_win_physical_v1` as the next shared feature candidate for late-window exact-bracket residual work.",
        "- Do not use it as a live gate until it has more complete forward dates and an execution-cost model using shared `weather_feature_layer.execution` fields.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    source = pd.read_csv(IN_DIR / "first_cross_rows.csv")
    data = add_model_features(source)
    data = data[data["settled"].astype(bool)].copy()
    data = data.dropna(subset=["win"])
    data["win"] = data["win"].astype(float)

    data["p_market_entry_v1"] = pd.to_numeric(data["entry_price"], errors="coerce").clip(1e-6, 1 - 1e-6)
    data["p_heating_done_score_v1"] = pd.to_numeric(data["leg_residual_done_score_v1"], errors="coerce").clip(1e-6, 1 - 1e-6)
    data["p_leg_win_physical_lodo_v1"] = leave_one_date_predictions(data)
    data["p_leg_win_physical_expanding_v1"] = expanding_forward_predictions(data)

    full_model = fit_full_model(data)
    data["p_leg_win_physical_v1"] = full_model.predict_proba(data[NUMERIC_FEATURES + CATEGORICAL_FEATURES])[:, 1]

    forward_mask = data["target_date"].astype(str).between(FORWARD_START, FORWARD_END, inclusive="both")
    metric_rows = []
    for scope_name, scope_mask in [
        ("all_lodo", data["p_leg_win_physical_lodo_v1"].notna()),
        ("forward_lodo", forward_mask & data["p_leg_win_physical_lodo_v1"].notna()),
        ("forward_expanding", forward_mask & data["p_leg_win_physical_expanding_v1"].notna()),
    ]:
        scoped = data[scope_mask]
        if scoped.empty:
            continue
        for label, col in [
            ("physical_logit", "p_leg_win_physical_lodo_v1" if "lodo" in scope_name else "p_leg_win_physical_expanding_v1"),
            ("heuristic_leg_score", "p_heating_done_score_v1"),
            ("market_entry_price", "p_market_entry_v1"),
        ]:
            rec = metric_row(label, scoped["win"], scoped[col], scoped["target_date"])
            rec["scope"] = scope_name
            metric_rows.append(rec)
    metrics = pd.DataFrame(metric_rows)

    slices = probability_slices(data.dropna(subset=["p_leg_win_physical_lodo_v1"]), "p_leg_win_physical_lodo_v1")
    coefs = coefficient_table(full_model)

    out_cols = [
        "snapshot_ts_utc",
        "city",
        "target_date",
        "leg",
        "bracket",
        "unit",
        "win",
        "entry_price",
        "p_market_entry_v1",
        "p_heating_done_score_v1",
        "p_leg_win_physical_lodo_v1",
        "p_leg_win_physical_expanding_v1",
        "p_leg_win_physical_v1",
        *NUMERIC_FEATURES,
        *CATEGORICAL_FEATURES,
    ]
    data[out_cols].to_csv(OUT_DIR / "scored_first_cross_rows.csv", index=False)
    metrics.to_csv(OUT_DIR / "metrics.csv", index=False)
    slices.to_csv(OUT_DIR / "probability_slices.csv", index=False)
    coefs.to_csv(OUT_DIR / "model_coefficients.csv", index=False)

    summary = {
        "model_id": MODEL_ID,
        "input_rows": int(len(source)),
        "model_rows": int(len(data)),
        "date_min": str(data["target_date"].min()),
        "date_max": str(data["target_date"].max()),
        "date_count": int(data["target_date"].nunique()),
        "numeric_features": NUMERIC_FEATURES,
        "categorical_features": CATEGORICAL_FEATURES,
        "excluded_input_fields": ["entry_price", "best_bid", "spread", "top_size", "depth_5c", "final_yes", "pnl"],
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(summary, metrics, slices, coefs)
    print(json.dumps({"summary": summary, "out_dir": str(OUT_DIR.relative_to(ROOT)), "report": str(OUT_MD.relative_to(ROOT))}, indent=2))


if __name__ == "__main__":
    main()
