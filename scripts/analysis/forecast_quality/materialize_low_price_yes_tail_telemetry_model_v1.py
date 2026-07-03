#!/usr/bin/env python3
"""Materialize lightweight low-price YES tail telemetry models.

The live runner should not depend on sklearn at runtime.  This script trains
the two p_cal diagnostic models from the frozen p_cal v1 denominator and writes
a small JSON artifact that can be scored with plain Python.
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
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality.research_low_price_yes_tail_pcal_v1 import (
    HOLDOUT_START,
    NUMERIC_FEATURES,
    attach_asof_bias,
    load_bias_index,
    load_v1_rows,
)


OUT_JSON = ROOT / "src/strategies/weather_edge_v1/config/low_price_yes_tail_telemetry_model_v1.json"
MIN_CAT_COUNT = 5
RNG_SEED = 20260702


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def clean_float(value: Any) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    return out if math.isfinite(out) else None


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return clean_float(value)
    return value


def fit_encoder(train: pd.DataFrame, cat_features: list[str]) -> dict[str, Any]:
    numeric_stats: dict[str, dict[str, float]] = {}
    for col in NUMERIC_FEATURES:
        values = pd.to_numeric(train[col], errors="coerce")
        median = float(values.median()) if values.notna().any() else 0.0
        filled = values.fillna(median)
        mean = float(filled.mean())
        std = float(filled.std(ddof=0))
        if not math.isfinite(std) or std <= 0:
            std = 1.0
        numeric_stats[col] = {"median": median, "mean": mean, "scale": std}

    cat_stats: dict[str, dict[str, Any]] = {}
    for col in cat_features:
        values = train[col].fillna("__MISSING__").astype(str)
        counts = values.value_counts()
        cats = sorted(str(x) for x in counts[counts >= MIN_CAT_COUNT].index)
        cat_stats[col] = {
            "categories": cats,
            "other_token": "__OTHER__",
            "missing_token": "__MISSING__",
            "min_count": MIN_CAT_COUNT,
        }
    return {"numeric": numeric_stats, "categorical": cat_stats}


def transform(frame: pd.DataFrame, encoder: dict[str, Any], cat_features: list[str]) -> tuple[np.ndarray, list[str]]:
    blocks: list[np.ndarray] = []
    names: list[str] = []

    for col in NUMERIC_FEATURES:
        stats = encoder["numeric"][col]
        values = pd.to_numeric(frame[col], errors="coerce").fillna(float(stats["median"]))
        arr = ((values - float(stats["mean"])) / float(stats["scale"])).to_numpy(float).reshape(-1, 1)
        blocks.append(arr)
        names.append(f"num:{col}")

    for col in cat_features:
        spec = encoder["categorical"][col]
        cats = list(spec["categories"]) + [spec["other_token"]]
        values = frame[col].fillna(spec["missing_token"]).astype(str)
        values = values.where(values.isin(spec["categories"]), spec["other_token"])
        for cat in cats:
            blocks.append(values.eq(cat).astype(float).to_numpy().reshape(-1, 1))
            names.append(f"cat:{col}={cat}")

    return np.hstack(blocks), names


def metric_row(model: LogisticRegression, x: np.ndarray, y: pd.Series) -> dict[str, Any]:
    p = model.predict_proba(x)[:, 1]
    out = {
        "rows": int(len(y)),
        "positives": int(y.sum()),
        "p_mean": float(p.mean()),
        "y_mean": float(y.mean()),
    }
    if y.nunique() > 1:
        out.update(
            {
                "auc": float(roc_auc_score(y, p)),
                "brier": float(brier_score_loss(y, p)),
                "log_loss": float(log_loss(y, p)),
            }
        )
    return out


def train_model(df: pd.DataFrame, *, name: str, cat_features: list[str]) -> dict[str, Any]:
    train = df[(df["period"].eq("historical")) & (df["target_date"] < HOLDOUT_START)].copy()
    holdout = df[(df["period"].eq("historical")) & (df["target_date"] >= HOLDOUT_START)].copy()
    forward = df[df["period"].eq("forward")].copy()
    encoder = fit_encoder(train, cat_features)
    x_train, feature_names = transform(train, encoder, cat_features)
    model = LogisticRegression(C=0.2, solver="liblinear", max_iter=1000, random_state=RNG_SEED)
    model.fit(x_train, train["win"].astype(int))

    metrics: dict[str, Any] = {
        "train_pre_2026_06_21": metric_row(model, x_train, train["win"].astype(int)),
    }
    if not holdout.empty:
        x_holdout, _ = transform(holdout, encoder, cat_features)
        metrics["holdout_2026_06_21_26"] = metric_row(model, x_holdout, holdout["win"].astype(int))
    if not forward.empty:
        x_forward, _ = transform(forward, encoder, cat_features)
        metrics["forward_2026_06_27_30"] = metric_row(model, x_forward, forward["win"].astype(int))

    return {
        "name": name,
        "type": "logistic_l2_manual_encoder",
        "cat_features": cat_features,
        "numeric_features": NUMERIC_FEATURES,
        "encoder": encoder,
        "feature_names": feature_names,
        "intercept": float(model.intercept_[0]),
        "coefficients": [float(x) for x in model.coef_[0].tolist()],
        "metrics": metrics,
    }


def main() -> None:
    bias_index = load_bias_index()
    rows = attach_asof_bias(load_v1_rows(), bias_index)
    models = [
        train_model(rows, name="v1_edge20_no_city", cat_features=["forecast_model"]),
        train_model(rows, name="v1_edge20_city_diag", cat_features=["forecast_model", "city"]),
    ]
    payload = {
        "artifact_version": "low_price_yes_tail_telemetry_model_v1",
        "generated_at_utc": now_utc(),
        "source_script": "scripts/analysis/forecast_quality/materialize_low_price_yes_tail_telemetry_model_v1.py",
        "source_research": "docs/analysis/2026-07/2026-07-02-low-price-yes-tail-pcal-v1.md",
        "training_denominator": "v1_edge20, historical target_date < 2026-06-21",
        "verdict": "diagnostic_telemetry_only_not_live_selector",
        "models": {model["name"]: model for model in models},
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT_JSON.relative_to(ROOT)}")
    for model in models:
        metrics = model["metrics"]
        print(model["name"], metrics)


if __name__ == "__main__":
    main()
