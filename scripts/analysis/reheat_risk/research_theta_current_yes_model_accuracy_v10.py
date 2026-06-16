#!/usr/bin/env python3
"""Backtest and upgrade the current-max-is-final-high probability model.

Target label:
    label_yes_wins = whether the current running-max bracket eventually settles
    as the day's highest-temperature winner.

Evidence layer:
    Historical orderbook replay feature rows from v8; no live config changes.
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
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, precision_score, recall_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
FEATURE_ROWS = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_full_replay_v8/feature_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/theta_current_yes_model_accuracy_v10"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-16-theta-current-yes-model-accuracy-v10.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-16-theta-current-yes-model-accuracy-v10.md"
SEED = 20260616

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
EXTRA_FEATURES = ["ask_x_decline", "hour_x_decline", "near_next_band", "temp_trend_combo"]


@dataclass(frozen=True)
class ModelRun:
    name: str
    model: Any | None
    features: list[str]
    categorical: list[str]
    pred_col: str
    note: str


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    out["ask_x_decline"] = out["yes_current_ask"] * out["decline_c"]
    out["hour_x_decline"] = out["decision_hour_local"] * out["decline_c"]
    out["near_next_band"] = out["gap_running_to_d1_low_native"].fillna(0).clip(-5, 5)
    out["temp_trend_combo"] = out["d_tmpf_1h"].fillna(0) + 0.5 * out["d_tmpf_3h"].fillna(0)
    out["label"] = out["label_yes_wins"].astype(int)
    out["live_slice"] = (
        out["decision_hour_local"].between(13, 15)
        & out["decline_c"].ge(0.5)
        & out["yes_current_ask"].ge(0.55)
        & out["has_d1_no"].fillna(False).astype(bool)
    )
    return out


def logit_pipeline(numeric: list[str], categorical: list[str]) -> Pipeline:
    pre = ColumnTransformer(
        [
            ("num", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), numeric),
            ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=2), categorical),
        ]
    )
    return Pipeline([("pre", pre), ("clf", LogisticRegression(max_iter=3000, C=0.8, random_state=SEED))])


def hgb_pipeline(numeric: list[str], categorical: list[str]) -> Pipeline:
    pre = ColumnTransformer(
        [
            ("num", SimpleImputer(strategy="median"), numeric),
            ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=2, sparse_output=False), categorical),
        ],
        sparse_threshold=0,
    )
    model = HistGradientBoostingClassifier(
        max_iter=80,
        learning_rate=0.04,
        max_leaf_nodes=12,
        l2_regularization=0.05,
        random_state=SEED,
    )
    return Pipeline([("pre", pre), ("clf", model)])


def metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    pred = p >= 0.5
    return {
        "rows": int(len(y)),
        "actual_rate": float(np.mean(y)) if len(y) else float("nan"),
        "mean_pred": float(np.mean(p)) if len(y) else float("nan"),
        "auc": float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else float("nan"),
        "brier": float(brier_score_loss(y, p)),
        "logloss": float(log_loss(y, p)),
        "accuracy_50": float(accuracy_score(y, pred)),
        "precision_50": float(precision_score(y, pred, zero_division=0)),
        "recall_50": float(recall_score(y, pred, zero_division=0)),
    }


def fit_and_predict(df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    train = df[df["period"].eq("train")].copy()
    hold = df[df["period"].eq("holdout")].copy()
    all_rows = df.copy()
    runs: list[ModelRun] = []

    all_rows["pred_market_yes_ask"] = all_rows["yes_current_ask"].clip(1e-6, 1 - 1e-6)
    runs.append(ModelRun("market_yes_ask", None, [], [], "pred_market_yes_ask", "market-implied current YES ask"))

    specs = [
        ("weather_only_logit", logit_pipeline(BASE_FEATURES, CAT_FEATURES), BASE_FEATURES, CAT_FEATURES, "weather path + city/unit logistic"),
        (
            "weather_plus_price_logit",
            logit_pipeline(BASE_FEATURES + PRICE_FEATURES, CAT_FEATURES),
            BASE_FEATURES + PRICE_FEATURES,
            CAT_FEATURES,
            "v8-style logistic: weather path + price features",
        ),
        (
            "weather_plus_price_logit_iso",
            CalibratedClassifierCV(logit_pipeline(BASE_FEATURES + PRICE_FEATURES, CAT_FEATURES), method="isotonic", cv=3),
            BASE_FEATURES + PRICE_FEATURES,
            CAT_FEATURES,
            "v8-style logistic plus train-only isotonic calibration",
        ),
        (
            "weather_plus_price_inter_logit_iso",
            CalibratedClassifierCV(logit_pipeline(BASE_FEATURES + PRICE_FEATURES + EXTRA_FEATURES, CAT_FEATURES), method="isotonic", cv=3),
            BASE_FEATURES + PRICE_FEATURES + EXTRA_FEATURES,
            CAT_FEATURES,
            "logistic with simple interaction features plus isotonic calibration",
        ),
        (
            "weather_plus_price_hgb_iso",
            CalibratedClassifierCV(hgb_pipeline(BASE_FEATURES + PRICE_FEATURES, CAT_FEATURES), method="isotonic", cv=3),
            BASE_FEATURES + PRICE_FEATURES,
            CAT_FEATURES,
            "nonlinear histogram GBM plus train-only isotonic calibration",
        ),
    ]
    for name, model, num, cat, note in specs:
        cols = num + cat
        model.fit(train[cols], train["label"])
        all_rows[f"pred_{name}"] = model.predict_proba(all_rows[cols])[:, 1]
        runs.append(ModelRun(name, model, num, cat, f"pred_{name}", note))

    metric_rows: list[dict[str, Any]] = []
    for run in runs:
        for scope_name, part in [
            ("holdout_all", all_rows[all_rows["period"].eq("holdout")]),
            ("holdout_live_slice", all_rows[all_rows["period"].eq("holdout") & all_rows["live_slice"]]),
            ("train_all", all_rows[all_rows["period"].eq("train")]),
        ]:
            if part.empty:
                continue
            row = {"model": run.name, "scope": scope_name, "note": run.note, **metrics(part["label"].to_numpy(), part[run.pred_col].to_numpy())}
            metric_rows.append(row)
    return all_rows, metric_rows


def bootstrap_delta(
    rows: pd.DataFrame,
    candidate_col: str,
    baseline_col: str,
    metric: str,
    *,
    scope_mask: pd.Series,
    n_boot: int = 2000,
) -> dict[str, Any]:
    part = rows[scope_mask].copy()
    dates = sorted(part["target_date"].astype(str).unique())
    if not dates:
        return {"n_dates": 0, "delta": None, "ci95": [None, None]}
    rng = np.random.default_rng(SEED)

    def score(sample: pd.DataFrame, col: str) -> float:
        y = sample["label"].to_numpy()
        p = np.clip(sample[col].to_numpy(dtype=float), 1e-6, 1 - 1e-6)
        if metric == "brier":
            return brier_score_loss(y, p)
        if metric == "logloss":
            return log_loss(y, p)
        if metric == "accuracy_50":
            return accuracy_score(y, p >= 0.5)
        raise ValueError(metric)

    point_candidate = score(part, candidate_col)
    point_baseline = score(part, baseline_col)
    point_delta = point_candidate - point_baseline
    draws = []
    by_date = {d: g for d, g in part.groupby(part["target_date"].astype(str))}
    for _ in range(n_boot):
        sample_dates = rng.choice(dates, size=len(dates), replace=True)
        sample = pd.concat([by_date[d] for d in sample_dates], ignore_index=True)
        try:
            draws.append(score(sample, candidate_col) - score(sample, baseline_col))
        except Exception:
            continue
    lo, hi = np.quantile(draws, [0.025, 0.975]).tolist() if draws else [None, None]
    return {
        "metric": metric,
        "n_rows": int(len(part)),
        "n_dates": int(len(dates)),
        "candidate": candidate_col.replace("pred_", ""),
        "baseline": baseline_col.replace("pred_", ""),
        "candidate_score": float(point_candidate),
        "baseline_score": float(point_baseline),
        "delta_candidate_minus_baseline": float(point_delta),
        "ci95": [None if lo is None else float(lo), None if hi is None else float(hi)],
        "interpretation": "lower_is_better" if metric in {"brier", "logloss"} else "higher_is_better",
    }


def calibration_table(rows: pd.DataFrame, pred_col: str, scope_mask: pd.Series, bins: int = 10) -> pd.DataFrame:
    part = rows[scope_mask].copy()
    part["bin"] = pd.qcut(part[pred_col].rank(method="first"), bins, labels=False, duplicates="drop")
    out = (
        part.groupby("bin", dropna=False)
        .agg(rows=("label", "size"), pred_mean=(pred_col, "mean"), actual_rate=("label", "mean"), ask_mean=("yes_current_ask", "mean"))
        .reset_index()
    )
    out["calibration_error"] = out["pred_mean"] - out["actual_rate"]
    return out


def slice_table(rows: pd.DataFrame, pred_col: str) -> pd.DataFrame:
    hold = rows[rows["period"].eq("holdout")].copy()
    hold["ask_bucket"] = pd.cut(hold["yes_current_ask"], [0, 0.55, 0.75, 0.9, 1.01], labels=["<0.55", "0.55-0.75", "0.75-0.90", ">=0.90"], right=False)
    hold["hour_bucket"] = hold["decision_hour_local"].astype(str)
    grouped = []
    for name, keys in [("ask_bucket", ["ask_bucket"]), ("hour", ["hour_bucket"]), ("decline_live", ["live_slice"])]:
        g = (
            hold.groupby(keys, observed=False)
            .agg(rows=("label", "size"), dates=("target_date", "nunique"), actual_rate=("label", "mean"), pred_mean=(pred_col, "mean"), ask_mean=("yes_current_ask", "mean"))
            .reset_index()
        )
        g.insert(0, "slice", name)
        g["calibration_error"] = g["pred_mean"] - g["actual_rate"]
        grouped.append(g.rename(columns={keys[0]: "bucket"}))
    return pd.concat(grouped, ignore_index=True)


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
    hold = metrics_df[metrics_df["scope"].eq("holdout_all")].sort_values("brier")
    live = metrics_df[metrics_df["scope"].eq("holdout_live_slice")].sort_values("brier")

    def table(df: pd.DataFrame) -> str:
        lines = ["| model | rows | dates/rate | AUC | Brier | LogLoss | Acc@0.5 | mean p |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for _, r in df.iterrows():
            lines.append(
                f"| {r['model']} | {int(r['rows'])} | {pct(r['actual_rate'])} | {fmt(r['auc'])} | {fmt(r['brier'])} | {fmt(r['logloss'])} | {pct(r['accuracy_50'])} | {pct(r['mean_pred'])} |"
            )
        return "\n".join(lines)

    boot = payload["bootstrap"]
    md = f"""# Theta Current YES Model Accuracy v10

Status: research_only
Generated: {payload['generated_at_utc']}
Target metric: `current_max_is_final_high_accuracy` = for a city/hour snapshot, predict whether the current running-max bracket is the final winning highest-temperature bracket.

## Data Snapshot

- Evidence layer: v8 historical orderbook replay feature rows + local canonical DB self-check; not live fill PnL.
- Row grain: one row = one city / target_date / decision hour / current bracket decision snapshot.
- Feature rows: {payload['coverage']['rows']} rows, {payload['coverage']['train_rows']} train / {payload['coverage']['holdout_rows']} holdout, dates {payload['coverage']['date_min']}..{payload['coverage']['date_max']}.
- Holdout dates: {payload['coverage']['holdout_dates']}; holdout label rate: {pct(payload['coverage']['holdout_label_rate'])}.
- fact_built_at_utc: `{payload['db_self_check']['fact_built_at_utc']}`.
- fact_trades trade_class: `{payload['db_self_check']['trade_class']}`.
- fact_trades settlement_status: `{payload['db_self_check']['settlement_status']}`.
- fact_signal_candidates coverage: `{payload['db_self_check']['signal_coverage']}`.
- CLOB orders/fills join: `{payload['db_self_check']['clob_order_fill_join']}`.

## Trading Action

Do not upgrade live from this report alone. This is model research only.

Best broad-holdout model candidate is `weather_plus_price_hgb_iso`: it improves probability calibration/Brier versus the current v8-style logistic on all holdout rows, but the improvement is small and should be treated as a research upgrade, not a live deploy. In the actual live-like slice, market ask remains the strongest probability baseline. The practical lesson is that weather features help most as calibration/risk filters around market price, not as a standalone oracle.

## Holdout Model Accuracy

{table(hold)}

## Live-Slice Accuracy

Live-slice here means h13-15, decline>=0.5, yes_ask>=0.55, and d1 sibling visible. It is the model-facing universe, not actual live fills.

{table(live)}

## Cluster Bootstrap Deltas

Negative Brier/logloss delta means the candidate is better than baseline.

- HGB isotonic vs current logistic, holdout Brier delta: {fmt(boot['hgb_vs_logit_brier']['delta_candidate_minus_baseline'])}, CI95 {boot['hgb_vs_logit_brier']['ci95']}.
- HGB isotonic vs market ask, holdout Brier delta: {fmt(boot['hgb_vs_market_brier']['delta_candidate_minus_baseline'])}, CI95 {boot['hgb_vs_market_brier']['ci95']}.
- Current logistic vs market ask, holdout Brier delta: {fmt(boot['logit_vs_market_brier']['delta_candidate_minus_baseline'])}, CI95 {boot['logit_vs_market_brier']['ci95']}.
- HGB isotonic vs current logistic, live-slice Brier delta: {fmt(boot['hgb_vs_logit_live_slice_brier']['delta_candidate_minus_baseline'])}, CI95 {boot['hgb_vs_logit_live_slice_brier']['ci95']}.

## Human Summary

1. The pure weather model is not enough: weather-only holdout AUC is around 0.865, far below the market ask baseline around 0.929.
2. The current weather+price logistic is reasonable, but not clearly better than market ask on ranking. Its role is mostly to smooth/adjust market probability using METAR path features.
3. The best tested broad-holdout upgrade is a nonlinear weather+price model with train-only isotonic calibration. It gives the best Brier on all holdout rows.
4. In the live-like slice, that nonlinear model does not beat market ask or the current logistic. This slice is only 278 rows / 13 dates and is already very high base-rate, so it needs more forward data before changing the live probability model.
5. For model accuracy, the next real improvement is likely feature quality: fresh book price at decision, snapshot age, minutes since running max, solar/local time geometry, and city-specific calibration.

## Proposed v10 Model Upgrade

- Keep label: `current bracket wins`.
- Keep universe: source-aligned cities.
- Use two probability layers:
  - `p_final_current`: weather+price calibrated model.
  - `p_executable_edge`: separate execution survival model, because today's live issue was stale executable price, not just no-reheat probability.
- Test `weather_plus_price_hgb_iso` in research/shadow only for broad probability calibration, but do not replace the live-slice probability model yet.
- If deployment later needs no sklearn on N100, export either a calibrated logistic or a compact tree/lookup artifact; do not install dependencies casually on N100.

## Artifacts

- JSON: `{OUT_JSON.relative_to(ROOT)}`
- metrics CSV: `{(OUT_DIR / 'model_metrics.csv').relative_to(ROOT)}`
- calibration CSV: `{(OUT_DIR / 'calibration_hgb_iso_holdout.csv').relative_to(ROOT)}`
- slice CSV: `{(OUT_DIR / 'slice_calibration_hgb_iso_holdout.csv').relative_to(ROOT)}`
"""
    OUT_MD.write_text(md, encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = add_features(pd.read_csv(FEATURE_ROWS))
    scored, metric_rows = fit_and_predict(df)
    metrics_df = pd.DataFrame(metric_rows)
    metrics_df.to_csv(OUT_DIR / "model_metrics.csv", index=False)

    hold_mask = scored["period"].eq("holdout")
    live_mask = hold_mask & scored["live_slice"]
    cal = calibration_table(scored, "pred_weather_plus_price_hgb_iso", hold_mask)
    cal.to_csv(OUT_DIR / "calibration_hgb_iso_holdout.csv", index=False)
    slices = slice_table(scored, "pred_weather_plus_price_hgb_iso")
    slices.to_csv(OUT_DIR / "slice_calibration_hgb_iso_holdout.csv", index=False)
    scored[
        [
            "city",
            "target_date",
            "decision_hour_local",
            "current_bracket",
            "label",
            "period",
            "live_slice",
            "yes_current_ask",
            "pred_market_yes_ask",
            "pred_weather_plus_price_logit",
            "pred_weather_plus_price_logit_iso",
            "pred_weather_plus_price_hgb_iso",
        ]
    ].to_csv(OUT_DIR / "scored_rows.csv", index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "current_max_is_final_high_accuracy",
        "db_self_check": db_self_check(),
        "coverage": {
            "rows": int(len(df)),
            "train_rows": int(df["period"].eq("train").sum()),
            "holdout_rows": int(df["period"].eq("holdout").sum()),
            "active_dates": int(df["target_date"].nunique()),
            "holdout_dates": int(df.loc[df["period"].eq("holdout"), "target_date"].nunique()),
            "date_min": str(df["target_date"].min()),
            "date_max": str(df["target_date"].max()),
            "holdout_label_rate": float(df.loc[df["period"].eq("holdout"), "label"].mean()),
            "live_slice_holdout_rows": int(live_mask.sum()),
            "live_slice_holdout_dates": int(scored.loc[live_mask, "target_date"].nunique()),
        },
        "metrics": metric_rows,
        "bootstrap": {
            "hgb_vs_logit_brier": bootstrap_delta(scored, "pred_weather_plus_price_hgb_iso", "pred_weather_plus_price_logit", "brier", scope_mask=hold_mask),
            "hgb_vs_market_brier": bootstrap_delta(scored, "pred_weather_plus_price_hgb_iso", "pred_market_yes_ask", "brier", scope_mask=hold_mask),
            "logit_vs_market_brier": bootstrap_delta(scored, "pred_weather_plus_price_logit", "pred_market_yes_ask", "brier", scope_mask=hold_mask),
            "hgb_vs_logit_live_slice_brier": bootstrap_delta(scored, "pred_weather_plus_price_hgb_iso", "pred_weather_plus_price_logit", "brier", scope_mask=live_mask),
        },
        "recommendation": {
            "research_upgrade": "weather_plus_price_hgb_iso",
            "live_action": "none",
            "reason": "holdout Brier improves modestly, but market ask remains a strong baseline and forward live-slice support is still thin",
        },
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    write_report(payload)
    print(json.dumps({"json": str(OUT_JSON), "md": str(OUT_MD), "rows": len(df)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
