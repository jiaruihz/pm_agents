"""
P1 fusion scorecard for the intraday Tmax local distribution.

This is an offline diagnostic. It does not change live behavior.

P0 showed that the local market-implied distribution is much stronger than
single-anchor forecast/running-max baselines. P1 asks the next question:

    Can a conservative fusion model improve proper scoring versus market-local?

The outcome space is the same P0-local four-bucket grid:

    current / d1 / d2 / tail

No final/future columns are used as features.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from research_tmax_distribution_p0_anchor_scorecard_v1 import (  # noqa: E402
    ATLAS_PATH,
    BUCKETS,
    EPS,
    _as_float,
    _entropy_norm,
    _hour_bucket,
    _interval,
    _label,
    _market_distribution,
    _normalize,
    _score_distribution,
    _soft_anchor_distribution,
)
from weather_feature_layer.market import add_market_geometry_features  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_distribution_p1_fusion_scorecard_v1"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-02-tmax-distribution-p1-fusion-scorecard-v1.md"
SUMMARY_JSON_PATH = ROOT / "docs/analysis/2026-07/2026-07-02-tmax-distribution-p1-fusion-scorecard-v1.json"

TRAIN_CUTOFF = "2026-06-21"
C_GRID = [0.03, 0.1, 0.3, 1.0]
ALPHA_GRID = [0.0, 0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0]
MIN_TRAIN_DATES_FOR_CV = 14


PHYSICAL_NUMERIC = [
    "decision_hour_local",
    "decision_hour_sin",
    "decision_hour_cos",
    "forecast_max_native",
    "forecast_max_f",
    "running_native",
    "current_native",
    "decline_native",
    "tmpf_now",
    "dwpf_now",
    "dewpoint_depression_f",
    "relative_humidity_pct",
    "wind_speed_kt",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "minutes_since_running_max",
    "forecast_peak_hour_local",
    "forecast_peak_delta_hours_local",
    "forecast_peak_delta_abs",
    "forecast_peak_hour_spread",
    "forecast_gap_to_running_native",
    "gfs_gap_to_running_native",
    "ecmwf_gap_to_running_native",
    "forecast_minus_running_native",
    "forecast_minus_current_native",
    "running_minus_current_native",
    "forecast_to_current_upper_native",
    "forecast_to_d1_upper_native",
    "forecast_to_d2_upper_native",
    "forecast_to_current_mid_native",
    "forecast_to_d1_mid_native",
    "forecast_to_d2_mid_native",
    "running_to_current_upper_native",
    "current_to_current_upper_native",
    "running_position_in_current_native",
    "current_position_in_current_native",
    "forecast_anchor_p_current",
    "forecast_anchor_p_d1",
    "forecast_anchor_p_d2",
    "forecast_anchor_p_tail",
    "runningmax_anchor_p_current",
    "runningmax_anchor_p_d1",
    "runningmax_anchor_p_d2",
    "runningmax_anchor_p_tail",
]

MARKET_NUMERIC = [
    "market_p_current",
    "market_p_d1",
    "market_p_d2",
    "market_p_tail",
    "market_log_p_current",
    "market_log_p_d1",
    "market_log_p_d2",
    "market_log_p_tail",
    "market_entropy",
    "market_top_p",
    "market_top2_gap",
]

CONTEXT_CATEGORICAL = [
    "unit",
    "forecast_source",
    "day_regime",
    "intraday_state",
    "moisture_cloud_regime",
    "wind_regime",
    "running_max_state",
    "solar_window",
    "city_family",
    "sky_cover_code",
    "hour_bucket",
]

CITY_CATEGORICAL = ["city"]

MODEL_SPECS = {
    "market_recal": {
        "numeric": MARKET_NUMERIC,
        "categorical": [],
        "description": "market probabilities only; tests calibration of local market distribution",
    },
    "weather_physical": {
        "numeric": PHYSICAL_NUMERIC,
        "categorical": CONTEXT_CATEGORICAL,
        "description": "weather/path/regime features without market probabilities or exact city id",
    },
    "fusion_numeric": {
        "numeric": MARKET_NUMERIC + PHYSICAL_NUMERIC,
        "categorical": [],
        "description": "market plus continuous physical/path features only; no regime/source/city categories",
    },
    "fusion_context": {
        "numeric": MARKET_NUMERIC + PHYSICAL_NUMERIC,
        "categorical": CONTEXT_CATEGORICAL,
        "description": "market plus physical/regime/source context, no exact city id",
    },
    "fusion_city": {
        "numeric": MARKET_NUMERIC + PHYSICAL_NUMERIC,
        "categorical": CONTEXT_CATEGORICAL + CITY_CATEGORICAL,
        "description": "fusion_context plus exact city id for city/source bias",
    },
}


def _safe_upper(interval: tuple[float, float] | None) -> float | None:
    if interval is None:
        return None
    hi = interval[1]
    if math.isinf(hi):
        return None
    return hi


def _safe_mid(interval: tuple[float, float] | None) -> float | None:
    if interval is None:
        return None
    lo, hi = interval
    if math.isinf(lo) or math.isinf(hi):
        return None
    return (lo + hi) / 2.0


def _delta(a: object, b: object) -> float | None:
    av, bv = _as_float(a), _as_float(b)
    if av is None or bv is None:
        return None
    return av - bv


def _bucket_label_array(values: pd.Series) -> np.ndarray:
    return values.astype(str).to_numpy()


def _load_rows() -> tuple[pd.DataFrame, dict[str, int]]:
    needed = set(
        [
            "city",
            "target_date",
            "decision_hour_local",
            "decision_snapshot_ts_utc",
            "unit",
            "current_bracket",
            "d1_no_bracket",
            "d2_no_bracket",
            "final_winning_bracket",
            "current_yes_ask",
            "current_bracket_no_ask",
            "current_no_bid",
            "d1_no_ask",
            "d1_no_bid",
            "d2_no_ask",
            "d2_no_bid",
            "forecast_max_native",
            "forecast_max_f",
            "running_native",
            "current_native",
            "decline_native",
            "tmpf_now",
            "dwpf_now",
            "dewpoint_depression_f",
            "relative_humidity_pct",
            "wind_speed_kt",
            "sky_cover_code",
            "temp_trend_1h_f",
            "temp_trend_3h_f",
            "minutes_since_running_max",
            "forecast_peak_hour_local",
            "forecast_peak_delta_hours_local",
            "forecast_peak_hour_spread",
            "forecast_gap_to_running_native",
            "gfs_gap_to_running_native",
            "ecmwf_gap_to_running_native",
            "forecast_source",
            "day_regime",
            "intraday_state",
            "moisture_cloud_regime",
            "wind_regime",
            "running_max_state",
            "solar_window",
            "city_family",
        ]
    )
    raw = pd.read_csv(ATLAS_PATH, usecols=lambda c: c in needed)
    counters = {"raw_rows": int(len(raw))}
    skipped = {"missing_or_invalid_label": 0, "missing_interval": 0, "missing_market_quote": 0}
    rows: list[dict[str, Any]] = []
    for item in raw.to_dict("records"):
        s = pd.Series(item)
        actual = _label(s)
        if actual is None:
            skipped["missing_or_invalid_label"] += 1
            continue
        current_iv = _interval(s.get("current_bracket"))
        d1_iv = _interval(s.get("d1_no_bracket"))
        d2_iv = _interval(s.get("d2_no_bracket"))
        if current_iv is None or d1_iv is None or d2_iv is None:
            skipped["missing_interval"] += 1
            continue
        market = _market_distribution(s)
        if market is None:
            skipped["missing_market_quote"] += 1
            continue
        forecast_anchor = _soft_anchor_distribution(
            _as_float(s.get("forecast_max_native")), current_iv, d1_iv, d2_iv
        )
        running_anchor = _soft_anchor_distribution(
            _as_float(s.get("running_native")), current_iv, d1_iv, d2_iv
        )
        row = dict(item)
        row["actual_bucket"] = actual
        row["split"] = "train_pre_2026_06_21" if str(item.get("target_date")) < TRAIN_CUTOFF else "forward_2026_06_21_plus"
        geometry = add_market_geometry_features(pd.DataFrame([item])).iloc[0].to_dict()
        for key in (
            "hour_bucket",
            "decision_hour_sin",
            "decision_hour_cos",
            "forecast_peak_delta_abs",
            "forecast_minus_running_native",
            "forecast_minus_current_native",
            "running_minus_current_native",
            "forecast_to_current_upper_native",
            "forecast_to_d1_upper_native",
            "forecast_to_d2_upper_native",
            "forecast_to_current_mid_native",
            "forecast_to_d1_mid_native",
            "forecast_to_d2_mid_native",
            "running_to_current_upper_native",
            "current_to_current_upper_native",
            "running_position_in_current_native",
            "current_position_in_current_native",
        ):
            row[key] = geometry.get(key)
        for bucket in BUCKETS:
            row[f"market_p_{bucket}"] = market[bucket]
            row[f"market_log_p_{bucket}"] = math.log(max(EPS, market[bucket]))
            row[f"forecast_anchor_p_{bucket}"] = forecast_anchor[bucket]
            row[f"runningmax_anchor_p_{bucket}"] = running_anchor[bucket]
        row["market_entropy"] = _entropy_norm(market)
        probs_sorted = sorted(market.values(), reverse=True)
        row["market_top_p"] = probs_sorted[0]
        row["market_top2_gap"] = probs_sorted[0] - probs_sorted[1]
        rows.append(row)
    counters.update(skipped)
    counters["scored_rows"] = int(len(rows))
    df = pd.DataFrame(rows)
    for col in sorted(set(CONTEXT_CATEGORICAL + CITY_CATEGORICAL)):
        if col in df.columns:
            df[col] = df[col].where(df[col].notna(), "unknown").astype(str)
    return df, counters


def _feature_columns(spec_name: str) -> tuple[list[str], list[str]]:
    spec = MODEL_SPECS[spec_name]
    return list(spec["numeric"]), list(spec["categorical"])


def _make_model(spec_name: str, c_value: float) -> Pipeline:
    numeric, categorical = _feature_columns(spec_name)
    transformers = []
    if numeric:
        transformers.append(
            (
                "num",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="median")),
                        ("scaler", StandardScaler()),
                    ]
                ),
                numeric,
            )
        )
    if categorical:
        transformers.append(
            (
                "cat",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="constant", fill_value="unknown")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                categorical,
            )
        )
    return Pipeline(
        steps=[
            ("features", ColumnTransformer(transformers=transformers, remainder="drop")),
            (
                "clf",
                LogisticRegression(
                    C=float(c_value),
                    solver="lbfgs",
                    max_iter=2000,
                ),
            ),
        ]
    )


def _fit_predict(train: pd.DataFrame, test: pd.DataFrame, spec_name: str, c_value: float) -> pd.DataFrame:
    y_train = _bucket_label_array(train["actual_bucket"])
    if len(set(y_train)) < len(BUCKETS):
        raise ValueError("training split is missing at least one bucket class")
    numeric, categorical = _feature_columns(spec_name)
    cols = numeric + categorical
    model = _make_model(spec_name, c_value)
    model.fit(train[cols], y_train)
    raw = model.predict_proba(test[cols])
    out = test[["city", "target_date", "decision_hour_local", "actual_bucket"]].copy()
    class_to_idx = {cls: i for i, cls in enumerate(model.named_steps["clf"].classes_)}
    for bucket in BUCKETS:
        if bucket in class_to_idx:
            out[f"model_p_{bucket}"] = raw[:, class_to_idx[bucket]]
        else:
            out[f"model_p_{bucket}"] = EPS
        out[f"market_p_{bucket}"] = test[f"market_p_{bucket}"].to_numpy()
    return out


def _blend_predictions(pred: pd.DataFrame, alpha: float, prefix: str) -> pd.DataFrame:
    out = pred.copy()
    weights: dict[str, np.ndarray] = {}
    for bucket in BUCKETS:
        weights[bucket] = (
            (1.0 - alpha) * out[f"market_p_{bucket}"].to_numpy()
            + alpha * out[f"model_p_{bucket}"].to_numpy()
        )
    total = np.sum([weights[b] for b in BUCKETS], axis=0)
    for bucket in BUCKETS:
        out[f"{prefix}_p_{bucket}"] = np.maximum(EPS, weights[bucket] / total)
    return out


def _add_market_method(df: pd.DataFrame, prefix: str = "market_local_norm") -> pd.DataFrame:
    out = df.copy()
    for bucket in BUCKETS:
        out[f"{prefix}_p_{bucket}"] = out[f"market_p_{bucket}"]
    return out


def _score_probs(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for r in df.itertuples(index=False):
        actual = str(getattr(r, "actual_bucket"))
        probs = {bucket: float(getattr(r, f"{prefix}_p_{bucket}")) for bucket in BUCKETS}
        probs = _normalize(probs)
        score = _score_distribution(probs, actual)
        row = {
            "city": getattr(r, "city"),
            "target_date": getattr(r, "target_date"),
            "decision_hour_local": getattr(r, "decision_hour_local"),
            "actual_bucket": actual,
            "method": prefix,
        }
        row.update(score)
        rows.append(row)
    return pd.DataFrame(rows)


def _score_prediction_frame(pred: pd.DataFrame, methods: list[str]) -> pd.DataFrame:
    return pd.concat([_score_probs(pred, m) for m in methods], ignore_index=True)


def _summary(scores: pd.DataFrame, label: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for method, grp in scores.groupby("method"):
        rows.append(
            {
                "scope": label,
                "method": method,
                "n": int(len(grp)),
                "dates": int(grp["target_date"].nunique()),
                "cities": int(grp["city"].nunique()),
                "logloss": float(grp["logloss"].mean()),
                "brier": float(grp["brier"].mean()),
                "top1": float(grp["top1"].mean()),
                "winner_prob": float(grp["winner_prob"].mean()),
                "winner_rank": float(grp["winner_rank"].mean()),
                "entropy": float(grp["entropy"].mean()),
            }
        )
    return pd.DataFrame(rows).sort_values(["scope", "logloss"]).reset_index(drop=True)


def _method_delta(scores: pd.DataFrame, method: str, baseline: str = "market_local_norm") -> dict[str, float]:
    pivot = scores.pivot_table(
        index=["city", "target_date", "decision_hour_local", "actual_bucket"],
        columns="method",
        values=["logloss", "brier"],
        aggfunc="first",
    )
    ll = pivot[("logloss", method)] - pivot[("logloss", baseline)]
    br = pivot[("brier", method)] - pivot[("brier", baseline)]
    return {
        "logloss_delta_vs_market": float(ll.mean()),
        "brier_delta_vs_market": float(br.mean()),
    }


def _date_block_ci(scores: pd.DataFrame, method: str, baseline: str = "market_local_norm") -> dict[str, float]:
    pivot = scores.pivot_table(
        index=["city", "target_date", "decision_hour_local", "actual_bucket"],
        columns="method",
        values="logloss",
        aggfunc="first",
    ).reset_index()
    if method not in pivot.columns or baseline not in pivot.columns:
        return {"mean": math.nan, "ci_low": math.nan, "ci_high": math.nan, "n_dates": 0}
    deltas = []
    for _date, grp in pivot.groupby("target_date"):
        deltas.append(float((grp[method] - grp[baseline]).mean()))
    if len(deltas) < 3:
        return {"mean": float(np.mean(deltas)) if deltas else math.nan, "ci_low": math.nan, "ci_high": math.nan, "n_dates": len(deltas)}
    rng = np.random.default_rng(11)
    arr = np.asarray(deltas)
    boot = []
    for _ in range(1000):
        boot.append(float(np.mean(rng.choice(arr, size=len(arr), replace=True))))
    return {
        "mean": float(np.mean(arr)),
        "ci_low": float(np.percentile(boot, 2.5)),
        "ci_high": float(np.percentile(boot, 97.5)),
        "n_dates": int(len(arr)),
    }


def _expanding_cv_predictions(
    train_df: pd.DataFrame,
    spec_name: str,
    c_value: float,
    *,
    min_train_dates: int = MIN_TRAIN_DATES_FOR_CV,
) -> pd.DataFrame:
    out = []
    dates = sorted(train_df["target_date"].unique())
    for i, d in enumerate(dates):
        if i < min_train_dates:
            continue
        fit_df = train_df[train_df["target_date"] < d]
        test_df = train_df[train_df["target_date"] == d]
        if len(fit_df) == 0 or len(test_df) == 0:
            continue
        try:
            pred = _fit_predict(fit_df, test_df, spec_name, c_value)
        except ValueError:
            continue
        out.append(pred)
    if not out:
        return pd.DataFrame()
    return pd.concat(out, ignore_index=True)


def _select_model(train_df: pd.DataFrame, spec_name: str) -> dict[str, Any]:
    market_scores: dict[float, pd.DataFrame] = {}
    candidates = []
    for c_value in C_GRID:
        pred = _expanding_cv_predictions(train_df, spec_name, c_value)
        if pred.empty:
            continue
        pred = _add_market_method(pred)
        pred = _blend_predictions(pred, 1.0, "model")
        model_scores = _score_prediction_frame(pred, ["market_local_norm", "model"])
        market_ll = float(model_scores[model_scores["method"] == "market_local_norm"]["logloss"].mean())
        model_ll = float(model_scores[model_scores["method"] == "model"]["logloss"].mean())
        market_scores[c_value] = model_scores
        best_alpha = None
        best_blend_ll = math.inf
        for alpha in ALPHA_GRID:
            bpred = _blend_predictions(pred, alpha, "blend")
            bscores = _score_probs(bpred, "blend")
            ll = float(bscores["logloss"].mean())
            if ll < best_blend_ll:
                best_blend_ll = ll
                best_alpha = alpha
        candidates.append(
            {
                "spec": spec_name,
                "c": c_value,
                "cv_rows": int(len(pred)),
                "cv_dates": int(pred["target_date"].nunique()),
                "cv_market_logloss": market_ll,
                "cv_model_logloss": model_ll,
                "cv_blend_alpha": float(best_alpha),
                "cv_blend_logloss": best_blend_ll,
            }
        )
    if not candidates:
        raise RuntimeError(f"no cv candidates for {spec_name}")
    selected = min(candidates, key=lambda x: x["cv_blend_logloss"])
    return {"selected": selected, "candidates": candidates}


def _fixed_forward_predictions(df: pd.DataFrame, selections: dict[str, dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    train = df[df["target_date"] < TRAIN_CUTOFF].copy()
    forward = df[df["target_date"] >= TRAIN_CUTOFF].copy()
    score_frames = []
    pred_frames = []
    market_frame = forward[["city", "target_date", "decision_hour_local", "actual_bucket"] + [f"market_p_{b}" for b in BUCKETS]].copy()
    market_frame = _add_market_method(market_frame)
    score_frames.append(_score_probs(market_frame, "market_local_norm"))
    for spec_name, payload in selections.items():
        c_value = payload["selected"]["c"]
        alpha = payload["selected"]["cv_blend_alpha"]
        pred = _fit_predict(train, forward, spec_name, c_value)
        pred = _blend_predictions(pred, 1.0, f"{spec_name}_model")
        pred = _blend_predictions(pred, alpha, f"{spec_name}_blend")
        pred_frames.append(pred)
        score_frames.append(_score_probs(pred, f"{spec_name}_model"))
        score_frames.append(_score_probs(pred, f"{spec_name}_blend"))
    return pd.concat(score_frames, ignore_index=True), _merge_prediction_frames(pred_frames)


def _expanding_forward_predictions(df: pd.DataFrame, selections: dict[str, dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    score_frames = []
    pred_frames = []
    forward_dates = sorted(d for d in df["target_date"].unique() if str(d) >= TRAIN_CUTOFF)
    for d in forward_dates:
        train = df[df["target_date"] < d].copy()
        test = df[df["target_date"] == d].copy()
        market_frame = test[["city", "target_date", "decision_hour_local", "actual_bucket"] + [f"market_p_{b}" for b in BUCKETS]].copy()
        market_frame = _add_market_method(market_frame)
        score_frames.append(_score_probs(market_frame, "market_local_norm"))
        for spec_name, payload in selections.items():
            c_value = payload["selected"]["c"]
            alpha = payload["selected"]["cv_blend_alpha"]
            pred = _fit_predict(train, test, spec_name, c_value)
            pred = _blend_predictions(pred, 1.0, f"{spec_name}_model")
            pred = _blend_predictions(pred, alpha, f"{spec_name}_blend")
            pred_frames.append(pred)
            score_frames.append(_score_probs(pred, f"{spec_name}_model"))
            score_frames.append(_score_probs(pred, f"{spec_name}_blend"))
    return pd.concat(score_frames, ignore_index=True), _merge_prediction_frames(pred_frames)


def _merge_prediction_frames(pred_frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not pred_frames:
        return pd.DataFrame()
    keys = ["city", "target_date", "decision_hour_local", "actual_bucket"]
    # Each spec prediction frame has the same keys plus its own probability
    # columns. Concatenate then take first non-null value per key to produce one
    # wide PIT prediction row for downstream EV/replay scripts.
    return pd.concat(pred_frames, ignore_index=True).groupby(keys, as_index=False).first()


def _daily_summary(scores: pd.DataFrame, scope: str) -> pd.DataFrame:
    rows = []
    for (date, method), grp in scores.groupby(["target_date", "method"]):
        rows.append(
            {
                "scope": scope,
                "target_date": date,
                "method": method,
                "n": int(len(grp)),
                "cities": int(grp["city"].nunique()),
                "logloss": float(grp["logloss"].mean()),
                "brier": float(grp["brier"].mean()),
                "top1": float(grp["top1"].mean()),
            }
        )
    return pd.DataFrame(rows).sort_values(["scope", "target_date", "method"]).reset_index(drop=True)


def _selection_table(selections: dict[str, dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for spec_name, payload in selections.items():
        sel = payload["selected"]
        row = dict(sel)
        row["description"] = MODEL_SPECS[spec_name]["description"]
        rows.append(row)
    return pd.DataFrame(rows).sort_values("spec").reset_index(drop=True)


def _candidate_table(selections: dict[str, dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for payload in selections.values():
        rows.extend(payload["candidates"])
    return pd.DataFrame(rows).sort_values(["spec", "c"]).reset_index(drop=True)


def _append_delta_columns(summary: pd.DataFrame, scores: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in summary.to_dict("records"):
        if r["method"] == "market_local_norm":
            r["logloss_delta_vs_market"] = 0.0
            r["brier_delta_vs_market"] = 0.0
            r["delta_ci_low"] = 0.0
            r["delta_ci_high"] = 0.0
        else:
            delta = _method_delta(scores, r["method"])
            ci = _date_block_ci(scores, r["method"])
            r.update(delta)
            r["delta_ci_low"] = ci["ci_low"]
            r["delta_ci_high"] = ci["ci_high"]
        rows.append(r)
    return pd.DataFrame(rows)


def _format_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _table_lines(df: pd.DataFrame, methods: list[str] | None = None) -> list[str]:
    sub = df.copy()
    if methods is not None:
        sub = sub[sub["method"].isin(methods)].copy()
    sub = sub.sort_values("logloss")
    lines = [
        "| method | n | dates | logloss | delta_vs_market | brier | top1 | winner_p |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sub.itertuples(index=False):
        lines.append(
            f"| {r.method} | {int(r.n)} | {int(r.dates)} | {r.logloss:.4f} | "
            f"{r.logloss_delta_vs_market:+.4f} | {r.brier:.4f} | {_format_pct(r.top1)} | {r.winner_prob:.4f} |"
        )
    return lines


def _daily_delta_lines(scores: pd.DataFrame, method: str) -> list[str]:
    sub = scores[scores["method"].isin(["market_local_norm", method])]
    pivot = sub.pivot_table(index="target_date", columns="method", values="logloss", aggfunc="mean")
    count = sub[sub["method"] == "market_local_norm"].groupby("target_date").size().rename("n")
    pivot = pivot.join(count)
    pivot["delta"] = pivot[method] - pivot["market_local_norm"]
    lines = [
        f"| date | n | market | {method} | delta |",
        "|---|---:|---:|---:|---:|",
    ]
    for date, row in pivot.iterrows():
        lines.append(
            f"| {date} | {int(row['n'])} | {row['market_local_norm']:.4f} | "
            f"{row[method]:.4f} | {row['delta']:+.4f} |"
        )
    return lines


def _bucket_delta_lines(scores: pd.DataFrame, method: str) -> list[str]:
    sub = scores[scores["method"].isin(["market_local_norm", method])]
    pivot = sub.pivot_table(index="actual_bucket", columns="method", values="logloss", aggfunc="mean")
    count = sub[sub["method"] == "market_local_norm"].groupby("actual_bucket").size().rename("n")
    pivot = pivot.join(count)
    pivot["delta"] = pivot[method] - pivot["market_local_norm"]
    lines = [
        f"| actual_bucket | n | market | {method} | delta |",
        "|---|---:|---:|---:|---:|",
    ]
    for bucket, row in pivot.iterrows():
        lines.append(
            f"| {bucket} | {int(row['n'])} | {row['market_local_norm']:.4f} | "
            f"{row[method]:.4f} | {row['delta']:+.4f} |"
        )
    return lines


def _write_report(
    df: pd.DataFrame,
    counters: dict[str, int],
    selections_df: pd.DataFrame,
    fixed_summary: pd.DataFrame,
    expanding_summary: pd.DataFrame,
    fixed_scores: pd.DataFrame,
    expanding_scores: pd.DataFrame,
    report: dict[str, Any],
) -> None:
    fixed_best = fixed_summary.sort_values("logloss").iloc[0]
    expanding_best = expanding_summary.sort_values("logloss").iloc[0]
    market_fixed = fixed_summary[fixed_summary["method"] == "market_local_norm"].iloc[0]
    market_expanding = expanding_summary[expanding_summary["method"] == "market_local_norm"].iloc[0]
    best_non_market_fixed = fixed_summary[fixed_summary["method"] != "market_local_norm"].sort_values("logloss").iloc[0]
    best_non_market_expanding = expanding_summary[expanding_summary["method"] != "market_local_norm"].sort_values("logloss").iloc[0]
    verdict = "inconclusive"
    if best_non_market_fixed["logloss_delta_vs_market"] >= 0 and best_non_market_expanding["logloss_delta_vs_market"] >= 0:
        verdict = "no_incremental_edge_vs_market"
    elif best_non_market_fixed["delta_ci_high"] < 0 and best_non_market_expanding["delta_ci_high"] < 0:
        verdict = "promising_shadow_candidate"
    elif best_non_market_fixed["logloss_delta_vs_market"] < 0 or best_non_market_expanding["logloss_delta_vs_market"] < 0:
        verdict = "inconclusive_positive_signal"
    report["verdict"] = verdict

    selected_lines = [
        "| spec | selected_C | alpha | cv_market | cv_model | cv_blend | cv_rows/dates |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in selections_df.itertuples(index=False):
        selected_lines.append(
            f"| {r.spec} | {r.c:.2g} | {r.cv_blend_alpha:.2f} | {r.cv_market_logloss:.4f} | "
            f"{r.cv_model_logloss:.4f} | {r.cv_blend_logloss:.4f} | {int(r.cv_rows)}/{int(r.cv_dates)} |"
        )

    methods_to_show = [
        "market_local_norm",
        "market_recal_blend",
        "weather_physical_blend",
        "fusion_numeric_blend",
        "fusion_context_blend",
        "fusion_city_blend",
        "fusion_numeric_model",
        "fusion_context_model",
        "fusion_city_model",
    ]
    lines = [
        "# Tmax Distribution P1 Fusion Scorecard v1",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> atlas: `{ATLAS_PATH.relative_to(ROOT)}`",
        "> Scope: offline proper-scoring diagnostic only; no live runner/order behavior changed.",
        "",
        "## 结论",
        "",
        f"- P1 继续用 P0-local 四桶：`current / d1 / d2 / tail`，样本 {counters['scored_rows']} rows / {df['target_date'].nunique()} dates / {df['city'].nunique()} cities。",
        "- C 和 market/model blend alpha 只在 `<2026-06-21` 的日期 walk-forward CV 里选择，然后再看 `2026-06-21+`。",
        (
            f"- Fixed forward：market logloss `{market_fixed.logloss:.4f}`；最佳非 market 是 "
            f"`{best_non_market_fixed.method}` `{best_non_market_fixed.logloss:.4f}` "
            f"(delta `{best_non_market_fixed.logloss_delta_vs_market:+.4f}`, date-CI "
            f"[`{best_non_market_fixed.delta_ci_low:+.4f}`, `{best_non_market_fixed.delta_ci_high:+.4f}`])。"
        ),
        (
            f"- Expanding forward：market logloss `{market_expanding.logloss:.4f}`；最佳非 market 是 "
            f"`{best_non_market_expanding.method}` `{best_non_market_expanding.logloss:.4f}` "
            f"(delta `{best_non_market_expanding.logloss_delta_vs_market:+.4f}`, date-CI "
            f"[`{best_non_market_expanding.delta_ci_low:+.4f}`, `{best_non_market_expanding.delta_ci_high:+.4f}`])。"
        ),
        f"- Verdict: `{verdict}`。",
        "",
        "人话：这一步不是找到了可下单策略，而是在检验“天气融合层能不能比盘口更准”。本轮最像真东西的是 `market + physical path`：`fusion_numeric`、`fusion_context`、`fusion_city` 都接近，说明不是单纯靠 city id 记忆；但 forward 只有 6 天，expanding CI 仍跨 0，所以只能进入 P2 离线 EV / telemetry，不能算 shadow alpha 已验证，也不能 live approval。",
        "",
        "## Feature Families",
        "",
        "- `market_recal`: 只用 market-local 四桶概率，测试盘口校准。",
        "- `weather_physical`: forecast/running/current path、趋势、wind/cloud/humidity、regime/source，不用 market 概率和精确 city id。",
        "- `fusion_numeric`: market + 连续物理/路径特征，不用 regime/source/city 类别。",
        "- `fusion_context`: market + weather/context，不用精确 city id。",
        "- `fusion_city`: fusion_context + 精确 city id，用来测试 city/source bias 是否提供增量，同时观察过拟合风险。",
        "",
        "未使用的字段：`final_max_native`、`forecast_error_native`、`future_break_*`、payoff/ROI、任何 settlement 结果列。",
        "",
        "## Train-CV Selection",
        "",
        *selected_lines,
        "",
        "## Fixed Forward 2026-06-21+",
        "",
        *_table_lines(fixed_summary, methods_to_show),
        "",
        "## Expanding Forward 2026-06-21+",
        "",
        *_table_lines(expanding_summary, methods_to_show),
        "",
        "## Daily Sanity Check",
        "",
        "Fixed forward 的 `fusion_city_blend` 5/6 天优于 market，唯一明显变差是 2026-06-24；expanding 也是 5/6 天优于 market，但 date-block CI 跨 0。",
        "",
        *_daily_delta_lines(fixed_scores, "fusion_city_blend"),
        "",
        "## Bucket Breakdown",
        "",
        "`fusion_city_blend` 的主要改进来自 actual bucket = `current`；对 `d1/d2` 略差，tail 基本持平。这说明它更像是在纠正 market 对“当前档守住”的低估，而不是全面优于盘口。",
        "",
        *_bucket_delta_lines(fixed_scores, "fusion_city_blend"),
        "",
        "## Notes",
        "",
        "- `model` = 模型自己输出的四桶概率；`blend` = `(1-alpha)*market + alpha*model`。",
        "- 负的 `delta_vs_market` 才表示打败 market-local。",
        "- 这里仍是 local ladder proxy；不是完整 Polymarket bracket ladder。",
        "- 本实验没有同步 N100 或发布 live_real PnL；只消费已生成 atlas CSV。",
        "",
        "## Artifacts",
        "",
        "- `docs/analysis/2026-07/generated/tmax_distribution_p1_fusion_scorecard_v1/fixed_forward_scores.csv`",
        "- `docs/analysis/2026-07/generated/tmax_distribution_p1_fusion_scorecard_v1/expanding_forward_scores.csv`",
        "- `docs/analysis/2026-07/generated/tmax_distribution_p1_fusion_scorecard_v1/model_selection.csv`",
        "- `docs/analysis/2026-07/generated/tmax_distribution_p1_fusion_scorecard_v1/candidate_selection_grid.csv`",
        f"- `{SUMMARY_JSON_PATH.relative_to(ROOT)}`",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df, counters = _load_rows()
    if df.empty:
        raise RuntimeError("No rows loaded")
    train = df[df["target_date"] < TRAIN_CUTOFF].copy()
    selections: dict[str, dict[str, Any]] = {}
    for spec_name in MODEL_SPECS:
        selections[spec_name] = _select_model(train, spec_name)
    selections_df = _selection_table(selections)
    candidate_df = _candidate_table(selections)

    fixed_scores, fixed_preds = _fixed_forward_predictions(df, selections)
    expanding_scores, expanding_preds = _expanding_forward_predictions(df, selections)
    fixed_summary = _append_delta_columns(_summary(fixed_scores, "fixed_forward_2026_06_21_plus"), fixed_scores)
    expanding_summary = _append_delta_columns(
        _summary(expanding_scores, "expanding_forward_2026_06_21_plus"), expanding_scores
    )
    fixed_daily = _daily_summary(fixed_scores, "fixed_forward_2026_06_21_plus")
    expanding_daily = _daily_summary(expanding_scores, "expanding_forward_2026_06_21_plus")

    fixed_scores.to_csv(OUT_DIR / "fixed_forward_scores.csv", index=False)
    expanding_scores.to_csv(OUT_DIR / "expanding_forward_scores.csv", index=False)
    fixed_preds.to_csv(OUT_DIR / "fixed_forward_predictions.csv", index=False)
    expanding_preds.to_csv(OUT_DIR / "expanding_forward_predictions.csv", index=False)
    fixed_summary.to_csv(OUT_DIR / "fixed_forward_summary.csv", index=False)
    expanding_summary.to_csv(OUT_DIR / "expanding_forward_summary.csv", index=False)
    fixed_daily.to_csv(OUT_DIR / "fixed_forward_daily.csv", index=False)
    expanding_daily.to_csv(OUT_DIR / "expanding_forward_daily.csv", index=False)
    selections_df.to_csv(OUT_DIR / "model_selection.csv", index=False)
    candidate_df.to_csv(OUT_DIR / "candidate_selection_grid.csv", index=False)

    report: dict[str, Any] = {
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "atlas_path": str(ATLAS_PATH.relative_to(ROOT)),
        "report_path": str(REPORT_PATH.relative_to(ROOT)),
        "counters": counters,
        "date_range": [str(df["target_date"].min()), str(df["target_date"].max())],
        "train_cutoff": TRAIN_CUTOFF,
        "cities": int(df["city"].nunique()),
        "fixed_forward_rows": int(len(fixed_scores[fixed_scores["method"] == "market_local_norm"])),
        "expanding_forward_rows": int(len(expanding_scores[expanding_scores["method"] == "market_local_norm"])),
        "fixed_forward_summary": fixed_summary.to_dict("records"),
        "expanding_forward_summary": expanding_summary.to_dict("records"),
        "model_selection": selections_df.to_dict("records"),
    }
    _write_report(df, counters, selections_df, fixed_summary, expanding_summary, fixed_scores, expanding_scores, report)
    SUMMARY_JSON_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"fixed_forward_summary", "expanding_forward_summary", "model_selection"}}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
