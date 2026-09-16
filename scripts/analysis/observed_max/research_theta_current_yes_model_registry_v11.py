#!/usr/bin/env python3
"""Versioned model registry and comparison for theta current-YES probability.

Research-only.  This script compares probability model versions for:

    P(current running-max bracket is the final winning bracket)

It does not touch N100, live config, or the live runner.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, precision_score, recall_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
FEATURE_ROWS = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_full_replay_v8/feature_rows.csv"
LIVE_MODEL = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_live_gate_v9/live_model.json"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/theta_current_yes_model_registry_v11"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-17-theta-current-yes-model-registry-v11.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-17-theta-current-yes-model-registry-v11.md"
SEED = 20260617

BASE_FEATURES = [
    "decision_hour_local",
    "month",
    "decline_c",
    "decline_native",
    "decline_band",
    "gap_running_to_d1_low_native",
    "gap_current_to_d1_low_native",
    "running_value",
    "current_native",
    "running_native",
    "tmpf_now",
    "dwpf_now",
    "dewpoint_depression_f",
    "relh_now",
    "sknt_now",
    "sky_now",
    "d_tmpf_1h",
    "d_tmpf_3h",
    "d_dwpf_3h",
    "d_relh_3h",
]
PRICE_FEATURES = ["yes_current_ask", "log_yes_size", "d1_no_ask", "ask_gap_d1_no_minus_yes"]
CAT_FEATURES = ["city", "unit"]
RESIDUAL_FEATURES = [
    "market_logit",
    "decline_c",
    "gap_running_to_d1_low_native",
    "gap_current_to_d1_low_native",
    "d_tmpf_1h",
    "d_tmpf_3h",
    "dewpoint_depression_f",
    "relh_now",
    "sknt_now",
    "sky_now",
    "decision_hour_local",
    "month",
]
EXTRA_FEATURES = ["ask_x_decline", "hour_x_decline", "near_next_band", "temp_trend_combo"]


@dataclass(frozen=True)
class ModelVersion:
    version: str
    pred_col: str
    family: str
    status: str
    feature_summary: str
    training: str
    deployable_now: bool
    note: str


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def logit(p: pd.Series | np.ndarray) -> np.ndarray:
    arr = np.clip(np.asarray(p, dtype=float), 1e-5, 1 - 1e-5)
    return np.log(arr / (1.0 - arr))


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.asarray(x, dtype=float)))


def connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    return conn


def db_self_check() -> dict[str, Any]:
    conn = connect_ro()
    try:
        return {
            "fact_built_at_utc": conn.execute("SELECT MAX(fact_built_at_utc) v FROM fact_trades").fetchone()["v"],
            "trade_class": [dict(r) for r in conn.execute("SELECT trade_class, COUNT(*) rows FROM fact_trades GROUP BY trade_class")],
            "settlement_status": [
                dict(r) for r in conn.execute("SELECT settlement_status, COUNT(*) rows FROM fact_trades GROUP BY settlement_status")
            ],
            "signal_coverage": dict(
                conn.execute(
                    "SELECT COUNT(*) rows, SUM(eligible) eligible, SUM(paper_ordered) paper_ordered, SUM(live_filled) live_filled FROM fact_signal_candidates"
                ).fetchone()
            ),
            "clob_order_fill_join": [
                dict(r)
                for r in conn.execute(
                    "SELECT o.status, COUNT(*) orders, SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) with_fill "
                    "FROM orders o LEFT JOIN fills f USING(execution_id) WHERE o.venue='polymarket_clob' GROUP BY o.status"
                )
            ],
        }
    finally:
        conn.close()


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["label"] = out["label_yes_wins"].astype(int)
    out["yes_current_ask"] = out["yes_current_ask"].clip(1e-5, 1 - 1e-5)
    out["market_logit"] = logit(out["yes_current_ask"])
    out["ask_x_decline"] = out["yes_current_ask"] * out["decline_c"]
    out["hour_x_decline"] = out["decision_hour_local"] * out["decline_c"]
    out["near_next_band"] = out["gap_running_to_d1_low_native"].fillna(0).clip(-5, 5)
    out["temp_trend_combo"] = out["d_tmpf_1h"].fillna(0) + 0.5 * out["d_tmpf_3h"].fillna(0)
    out["live_like_slice"] = (
        out["decision_hour_local"].between(13, 15)
        & out["decline_c"].ge(0.5)
        & out["yes_current_ask"].ge(0.55)
        & out["has_d1_no"].fillna(False).astype(bool)
    )
    return out


def numeric_cat_pipeline(numeric: list[str], categorical: list[str], *, c: float = 0.8) -> Pipeline:
    pre = ColumnTransformer(
        [
            ("num", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), numeric),
            ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=2), categorical),
        ]
    )
    return Pipeline([("pre", pre), ("clf", LogisticRegression(max_iter=3000, C=c, random_state=SEED))])


def hgb_pipeline(numeric: list[str], categorical: list[str]) -> Pipeline:
    pre = ColumnTransformer(
        [
            ("num", SimpleImputer(strategy="median"), numeric),
            ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=2, sparse_output=False), categorical),
        ],
        sparse_threshold=0,
    )
    return Pipeline(
        [
            (
                "clf_pipe",
                Pipeline(
                    [
                        ("pre", pre),
                        (
                            "clf",
                            HistGradientBoostingClassifier(
                                max_iter=80,
                                learning_rate=0.04,
                                max_leaf_nodes=12,
                                l2_regularization=0.05,
                                random_state=SEED,
                            ),
                        ),
                    ]
                ),
            )
        ]
    )


def score_live_artifact(rows: pd.DataFrame) -> np.ndarray:
    artifact = json.loads(LIVE_MODEL.read_text(encoding="utf-8"))
    num_features = list(artifact["numeric_features"])
    cat_features = list(artifact["categorical_features"])
    numeric = rows[num_features].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    medians = np.asarray(artifact["numeric_medians"], dtype=float)
    means = np.asarray(artifact["numeric_means"], dtype=float)
    scales = np.asarray(artifact["numeric_scales"], dtype=float)
    numeric = np.where(np.isfinite(numeric), numeric, medians)
    numeric = (numeric - means) / scales

    cat_parts = []
    for idx, feature in enumerate(cat_features):
        vals = rows[feature].astype(str).to_numpy()
        cats = [str(x) for x in artifact["categories"][idx]]
        lookup = {v: i for i, v in enumerate(cats)}
        mat = np.zeros((len(rows), len(cats)), dtype=float)
        for row_idx, value in enumerate(vals):
            col = lookup.get(value)
            if col is not None:
                mat[row_idx, col] = 1.0
        cat_parts.append(mat)
    x = np.concatenate([numeric, *cat_parts], axis=1)
    logits = x @ np.asarray(artifact["coef"], dtype=float) + float(artifact["intercept"])
    return sigmoid(logits)


def metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    pred = p >= 0.5
    return {
        "rows": int(len(y)),
        "actual_rate": float(np.mean(y)),
        "mean_pred": float(np.mean(p)),
        "auc": float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else float("nan"),
        "brier": float(brier_score_loss(y, p)),
        "logloss": float(log_loss(y, p)),
        "accuracy_50": float(accuracy_score(y, pred)),
        "precision_50": float(precision_score(y, pred, zero_division=0)),
        "recall_50": float(recall_score(y, pred, zero_division=0)),
    }


def fit_predictions(df: pd.DataFrame) -> tuple[pd.DataFrame, list[ModelVersion]]:
    out = df.copy()
    train = out[out["period"].eq("train")].copy()
    versions: list[ModelVersion] = []

    out["pred_m0_market_ask"] = out["yes_current_ask"]
    versions.append(
        ModelVersion(
            "m0_market_ask",
            "pred_m0_market_ask",
            "baseline",
            "baseline",
            "market YES ask only",
            "none",
            False,
            "市场隐含概率；所有新模型必须先和它比",
        )
    )

    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(train["yes_current_ask"].to_numpy(), train["label"].to_numpy())
    out["pred_m1_market_iso"] = iso.predict(out["yes_current_ask"].to_numpy())
    versions.append(
        ModelVersion(
            "m1_market_iso",
            "pred_m1_market_iso",
            "baseline_calibrated",
            "research_baseline",
            "train-only isotonic calibration on market ask",
            "train rows only",
            False,
            "校准后的市场基准，比 raw ask 更公平",
        )
    )

    out["pred_m2_live_v9_logit_artifact"] = score_live_artifact(out)
    versions.append(
        ModelVersion(
            "m2_live_v9_logit_artifact",
            "pred_m2_live_v9_logit_artifact",
            "current_live",
            "live_running",
            "weather path + price + city/unit logistic artifact",
            "frozen v9 artifact, train rows=1527",
            True,
            "当前 tiny-live 概率模型",
        )
    )

    weather = numeric_cat_pipeline(BASE_FEATURES, CAT_FEATURES)
    weather.fit(train[BASE_FEATURES + CAT_FEATURES], train["label"])
    out["pred_m3_weather_only_logit"] = weather.predict_proba(out[BASE_FEATURES + CAT_FEATURES])[:, 1]
    versions.append(
        ModelVersion(
            "m3_weather_only_logit",
            "pred_m3_weather_only_logit",
            "weather_only",
            "research_rejected",
            "METAR/ASOS path + city/unit, no price",
            "train rows only",
            False,
            "检验天气路径本身；目前明显弱于市场",
        )
    )

    residual = numeric_cat_pipeline(RESIDUAL_FEATURES, CAT_FEATURES, c=0.5)
    residual.fit(train[RESIDUAL_FEATURES + CAT_FEATURES], train["label"])
    out["pred_m4_market_residual_logit"] = residual.predict_proba(out[RESIDUAL_FEATURES + CAT_FEATURES])[:, 1]
    versions.append(
        ModelVersion(
            "m4_market_residual_logit",
            "pred_m4_market_residual_logit",
            "market_residual",
            "new_candidate",
            "market logit + no-reheat weather residual features + city/unit",
            "train rows only",
            False,
            "把市场价格当先验，天气只学习残差",
        )
    )

    residual_iso = CalibratedClassifierCV(numeric_cat_pipeline(RESIDUAL_FEATURES, CAT_FEATURES, c=0.5), method="isotonic", cv=3)
    residual_iso.fit(train[RESIDUAL_FEATURES + CAT_FEATURES], train["label"])
    out["pred_m5_market_residual_logit_iso"] = residual_iso.predict_proba(out[RESIDUAL_FEATURES + CAT_FEATURES])[:, 1]
    versions.append(
        ModelVersion(
            "m5_market_residual_logit_iso",
            "pred_m5_market_residual_logit_iso",
            "market_residual_calibrated",
            "new_candidate",
            "market logit + weather residual + train-only isotonic calibration",
            "train rows only, cv=3 calibration",
            False,
            "v11 主候选；必须在 live-like slice 打赢市场基准才有意义",
        )
    )

    hgb = CalibratedClassifierCV(hgb_pipeline(BASE_FEATURES + PRICE_FEATURES, CAT_FEATURES), method="isotonic", cv=3)
    hgb.fit(train[BASE_FEATURES + PRICE_FEATURES + CAT_FEATURES], train["label"])
    out["pred_m6_hgb_iso_v10"] = hgb.predict_proba(out[BASE_FEATURES + PRICE_FEATURES + CAT_FEATURES])[:, 1]
    versions.append(
        ModelVersion(
            "m6_hgb_iso_v10",
            "pred_m6_hgb_iso_v10",
            "nonlinear_calibrated",
            "research_candidate",
            "nonlinear weather+price HistGradientBoosting + isotonic",
            "train rows only, cv=3 calibration",
            False,
            "v10 全 holdout Brier 最好，但 live-like slice 不优",
        )
    )

    hgb_resid = CalibratedClassifierCV(hgb_pipeline(RESIDUAL_FEATURES + EXTRA_FEATURES, CAT_FEATURES), method="isotonic", cv=3)
    hgb_resid.fit(train[RESIDUAL_FEATURES + EXTRA_FEATURES + CAT_FEATURES], train["label"])
    out["pred_m7_hgb_residual_iso"] = hgb_resid.predict_proba(out[RESIDUAL_FEATURES + EXTRA_FEATURES + CAT_FEATURES])[:, 1]
    versions.append(
        ModelVersion(
            "m7_hgb_residual_iso",
            "pred_m7_hgb_residual_iso",
            "market_residual_nonlinear",
            "new_candidate",
            "market logit + residual features + nonlinear calibrated model",
            "train rows only, cv=3 calibration",
            False,
            "复杂残差候选，防止只看线性残差",
        )
    )
    return out, versions


def metric_table(rows: pd.DataFrame, versions: list[ModelVersion]) -> pd.DataFrame:
    parts = [
        ("train_all", rows["period"].eq("train")),
        ("holdout_all", rows["period"].eq("holdout")),
        ("holdout_live_like", rows["period"].eq("holdout") & rows["live_like_slice"]),
        ("holdout_non_live_like", rows["period"].eq("holdout") & ~rows["live_like_slice"]),
    ]
    out = []
    for scope, mask in parts:
        part = rows[mask].copy()
        if part.empty:
            continue
        y = part["label"].to_numpy()
        dates = int(part["target_date"].nunique())
        for v in versions:
            out.append(
                {
                    "version": v.version,
                    "scope": scope,
                    "dates": dates,
                    **metrics(y, part[v.pred_col].to_numpy()),
                    "family": v.family,
                    "status": v.status,
                    "deployable_now": v.deployable_now,
                    "note": v.note,
                }
            )
    return pd.DataFrame(out)


def bootstrap_delta(
    rows: pd.DataFrame,
    candidate: ModelVersion,
    baseline: ModelVersion,
    scope_mask: pd.Series,
    metric: str = "brier",
    n_boot: int = 2000,
) -> dict[str, Any]:
    part = rows[scope_mask].copy()
    dates = sorted(part["target_date"].astype(str).unique())
    if not dates:
        return {"candidate": candidate.version, "baseline": baseline.version, "n_rows": 0, "n_dates": 0}
    by_date = {d: g for d, g in part.groupby(part["target_date"].astype(str))}
    rng = np.random.default_rng(SEED)

    def score(sample: pd.DataFrame, col: str) -> float:
        y = sample["label"].to_numpy()
        p = np.clip(sample[col].to_numpy(dtype=float), 1e-6, 1 - 1e-6)
        if metric == "brier":
            return brier_score_loss(y, p)
        if metric == "logloss":
            return log_loss(y, p)
        raise ValueError(metric)

    point = score(part, candidate.pred_col) - score(part, baseline.pred_col)
    draws = []
    for _ in range(n_boot):
        sample_dates = rng.choice(dates, size=len(dates), replace=True)
        sample = pd.concat([by_date[d] for d in sample_dates], ignore_index=True)
        try:
            draws.append(score(sample, candidate.pred_col) - score(sample, baseline.pred_col))
        except Exception:
            pass
    ci = np.quantile(draws, [0.025, 0.975]).tolist() if draws else [None, None]
    return {
        "candidate": candidate.version,
        "baseline": baseline.version,
        "metric": metric,
        "scope_rows": int(len(part)),
        "scope_dates": int(len(dates)),
        "delta_candidate_minus_baseline": float(point),
        "ci95": [None if x is None else float(x) for x in ci],
        "lower_is_better": True,
    }


def calibration_by_bucket(rows: pd.DataFrame, version: ModelVersion, scope_mask: pd.Series) -> pd.DataFrame:
    part = rows[scope_mask].copy()
    part["prob_bucket"] = pd.cut(part[version.pred_col], [0, 0.55, 0.75, 0.9, 1.01], labels=["<0.55", "0.55-0.75", "0.75-0.90", ">=0.90"], right=False)
    return (
        part.groupby("prob_bucket", observed=False)
        .agg(rows=("label", "size"), dates=("target_date", "nunique"), pred_mean=(version.pred_col, "mean"), actual_rate=("label", "mean"), ask_mean=("yes_current_ask", "mean"))
        .reset_index()
    )


def registry_table(versions: list[ModelVersion]) -> pd.DataFrame:
    return pd.DataFrame([v.__dict__ for v in versions]).drop(columns=["pred_col"])


def pct(x: float | None, signed: bool = False) -> str:
    if x is None or not math.isfinite(float(x)):
        return "NA"
    sign = "+" if signed else ""
    return f"{100 * float(x):{sign}.1f}%"


def fmt(x: float | None, digits: int = 4) -> str:
    if x is None or not math.isfinite(float(x)):
        return "NA"
    return f"{float(x):.{digits}f}"


def write_report(payload: dict[str, Any]) -> None:
    metrics_df = pd.DataFrame(payload["metrics"])
    registry_df = pd.DataFrame(payload["registry"])
    hold = metrics_df[metrics_df["scope"].eq("holdout_all")].sort_values("brier")
    live = metrics_df[metrics_df["scope"].eq("holdout_live_like")].sort_values("brier")

    def metrics_table(df: pd.DataFrame) -> str:
        lines = ["| version | rows/dates | AUC | Brier | LogLoss | Acc@0.5 | mean p | actual |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for _, r in df.iterrows():
            lines.append(
                f"| `{r['version']}` | {int(r['rows'])}/{int(r['dates'])} | {fmt(r['auc'])} | {fmt(r['brier'])} | {fmt(r['logloss'])} | {pct(r['accuracy_50'])} | {pct(r['mean_pred'])} | {pct(r['actual_rate'])} |"
            )
        return "\n".join(lines)

    def registry_md(df: pd.DataFrame) -> str:
        lines = ["| version | family | status | deploy | feature summary |", "|---|---|---|---|---|"]
        for _, r in df.iterrows():
            lines.append(f"| `{r['version']}` | {r['family']} | {r['status']} | {str(r['deployable_now']).lower()} | {r['feature_summary']} |")
        return "\n".join(lines)

    deltas = payload["bootstrap_deltas"]
    md = f"""# Theta Current YES Model Registry v11

Status: research_only
Generated: {payload['generated_at_utc']}
Target metric: `current_yes_probability_model_registry` = maintain comparable versions for `P(current running-max bracket wins)`.

## Data Snapshot

- Evidence layer: v8 time-aligned orderbook replay feature rows + canonical DB self-check. This is not live fill PnL.
- Row grain: one row = city / target_date / decision hour / current running-max bracket.
- Rows: {payload['coverage']['rows']} total, {payload['coverage']['train_rows']} train, {payload['coverage']['holdout_rows']} holdout.
- Dates: {payload['coverage']['date_min']}..{payload['coverage']['date_max']}; holdout dates={payload['coverage']['holdout_dates']}.
- Live-like slice: {payload['coverage']['live_like_holdout_rows']} holdout rows / {payload['coverage']['live_like_holdout_dates']} dates.
- fact_built_at_utc: `{payload['db_self_check']['fact_built_at_utc']}`.
- fact_trades trade_class: `{payload['db_self_check']['trade_class']}`.
- fact_trades settlement_status: `{payload['db_self_check']['settlement_status']}`.
- fact_signal_candidates coverage: `{payload['db_self_check']['signal_coverage']}`.
- CLOB orders/fills join: `{payload['db_self_check']['clob_order_fill_join']}`.

## Trading Action

No live model replacement from v11.

The new residual idea is correct as a framework, but on current data it does not beat the simple market baseline in the live-like slice. The registry should become the gate: a model is not eligible for live unless it beats `m0_market_ask` and `m1_market_iso` on live-like Brier/logloss with date-cluster CI not crossing zero.

## Model Registry

{registry_md(registry_df)}

## Holdout All

{metrics_table(hold)}

## Holdout Live-Like Slice

{metrics_table(live)}

## Bootstrap Checks

Negative delta means the candidate is better.

"""
    for row in deltas:
        md += f"- `{row['candidate']}` vs `{row['baseline']}` on {row['metric']}: delta {fmt(row['delta_candidate_minus_baseline'])}, CI95 {row['ci95']} over {row['scope_rows']} rows / {row['scope_dates']} dates.\n"

    md += f"""
## Human Conclusion

1. Maintaining model versions is necessary. Without a registry, it is too easy to celebrate a new model that only beats the old model but not the market baseline.
2. The current live v9 logistic is a reasonable calibrated model, but it is not clearly better than market ask in the live-like slice.
3. `m1_market_iso` is now the fair baseline: market ask after train-only calibration. New models should beat both raw market and market_iso.
4. The v11 residual models are conceptually right, but current evidence says they are not ready. They did not produce a live-slice improvement thick enough to justify deployment.
5. The next effective model work is feature creation, not another classifier sweep: minutes since max, first/last max touch, forecast peak hour, fresh book ask, snapshot age, and city-hour calibration.

## Proposed Governance Rule

Every future theta-current-YES probability model must add one registry row with:

- version id and family;
- frozen feature list;
- train/holdout split;
- holdout_all metrics;
- holdout_live_like metrics;
- bootstrap delta vs `m0_market_ask`, `m1_market_iso`, and current live model;
- verdict: `research_only`, `shadow_candidate`, or `live_candidate`.

Promotion gate:

```text
live_candidate only if:
  live-like Brier/logloss beats m0_market_ask and m1_market_iso
  date-cluster CI for delta is < 0
  at least 10 holdout dates and 30+ live-like rows
  calibration table has no obvious high-probability overconfidence
```

## Artifacts

- JSON: `{OUT_JSON.relative_to(ROOT)}`
- registry CSV: `{(OUT_DIR / 'model_registry.csv').relative_to(ROOT)}`
- metrics CSV: `{(OUT_DIR / 'model_metrics.csv').relative_to(ROOT)}`
- scored rows CSV: `{(OUT_DIR / 'scored_rows.csv').relative_to(ROOT)}`
"""
    OUT_MD.write_text(md, encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = add_features(pd.read_csv(FEATURE_ROWS))
    scored, versions = fit_predictions(df)
    metrics_df = metric_table(scored, versions)
    registry_df = registry_table(versions)

    version_map = {v.version: v for v in versions}
    holdout = scored["period"].eq("holdout")
    live_like = holdout & scored["live_like_slice"]
    delta_specs = [
        ("m2_live_v9_logit_artifact", "m0_market_ask", live_like),
        ("m2_live_v9_logit_artifact", "m1_market_iso", live_like),
        ("m4_market_residual_logit", "m0_market_ask", live_like),
        ("m5_market_residual_logit_iso", "m1_market_iso", live_like),
        ("m7_hgb_residual_iso", "m1_market_iso", live_like),
        ("m6_hgb_iso_v10", "m2_live_v9_logit_artifact", holdout),
        ("m5_market_residual_logit_iso", "m2_live_v9_logit_artifact", holdout),
    ]
    deltas = [bootstrap_delta(scored, version_map[c], version_map[b], mask) for c, b, mask in delta_specs]

    best_live = metrics_df[metrics_df["scope"].eq("holdout_live_like")].sort_values("brier").iloc[0]["version"]
    cal = calibration_by_bucket(scored, version_map[str(best_live)], live_like)

    registry_df.to_csv(OUT_DIR / "model_registry.csv", index=False)
    metrics_df.to_csv(OUT_DIR / "model_metrics.csv", index=False)
    cal.to_csv(OUT_DIR / "best_live_like_calibration.csv", index=False)
    scored_cols = ["city", "target_date", "decision_hour_local", "current_bracket", "period", "live_like_slice", "label", "yes_current_ask"]
    scored_cols += [v.pred_col for v in versions]
    scored[scored_cols].to_csv(OUT_DIR / "scored_rows.csv", index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "current_yes_probability_model_registry",
        "db_self_check": db_self_check(),
        "coverage": {
            "rows": int(len(df)),
            "train_rows": int(df["period"].eq("train").sum()),
            "holdout_rows": int(df["period"].eq("holdout").sum()),
            "active_dates": int(df["target_date"].nunique()),
            "holdout_dates": int(df.loc[df["period"].eq("holdout"), "target_date"].nunique()),
            "date_min": str(df["target_date"].min()),
            "date_max": str(df["target_date"].max()),
            "live_like_holdout_rows": int(live_like.sum()),
            "live_like_holdout_dates": int(scored.loc[live_like, "target_date"].nunique()),
        },
        "registry": registry_df.to_dict(orient="records"),
        "metrics": metrics_df.to_dict(orient="records"),
        "bootstrap_deltas": deltas,
        "recommendation": {
            "live_action": "none",
            "registry_gate": "future models must beat market ask and market_iso on holdout_live_like with date-cluster CI < 0",
            "best_holdout_all_by_brier": str(metrics_df[metrics_df["scope"].eq("holdout_all")].sort_values("brier").iloc[0]["version"]),
            "best_holdout_live_like_by_brier": str(best_live),
        },
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    write_report(payload)
    print(json.dumps({"json": str(OUT_JSON), "md": str(OUT_MD), "best_live_like": str(best_live)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
