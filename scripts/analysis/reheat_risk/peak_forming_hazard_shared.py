"""Shared, version-neutral helpers for peak-forming hazard experiments."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

try:
    from .weather_research_data_shared import data_self_check
except ImportError:  # direct script execution
    from weather_research_data_shared import data_self_check


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    return value


def metric_row(name: str, frame: pd.DataFrame, p_col: str) -> dict[str, Any]:
    if frame.empty:
        return {"model": name, "rows": 0, "active_dates": 0}
    y = frame["label_current_yes_survives"].to_numpy(dtype=int)
    probability = np.clip(frame[p_col].to_numpy(dtype=float), 1e-6, 1 - 1e-6)
    return {
        "model": name,
        "rows": int(len(frame)),
        "active_dates": int(frame["target_date"].nunique()),
        "actual_survive_rate": float(y.mean()),
        "mean_pred_survive": float(probability.mean()),
        "auc": (
            float(roc_auc_score(y, probability))
            if len(np.unique(y)) > 1
            else None
        ),
        "brier": float(brier_score_loss(y, probability)),
        "logloss": (
            float(log_loss(y, probability)) if len(np.unique(y)) > 1 else None
        ),
        "mean_edge_vs_ask": float(
            (frame[p_col] - frame["current_yes_ask"]).mean()
        ),
    }


def date_cluster_bootstrap_roi(
    frame: pd.DataFrame,
    cost_col: str,
    pnl_col: str,
    *,
    seed: int,
    reps: int = 3000,
) -> list[float | None]:
    by_date = frame.groupby("target_date")[[cost_col, pnl_col]].sum()
    if by_date.shape[0] < 2:
        return [None, None]
    rng = np.random.default_rng(seed)
    values = by_date.to_numpy(dtype=float)
    results = []
    for _ in range(reps):
        indexes = rng.integers(0, len(values), size=len(values))
        sample = values[indexes]
        cost = float(sample[:, 0].sum())
        results.append(float(sample[:, 1].sum() / cost) if cost else np.nan)
    finite = np.asarray(results)
    finite = finite[np.isfinite(finite)]
    if len(finite) == 0:
        return [None, None]
    return [float(np.quantile(finite, 0.025)), float(np.quantile(finite, 0.975))]


def summarize_trade(
    frame: pd.DataFrame, p_col: str, name: str, *, seed: int
) -> dict[str, Any]:
    if frame.empty:
        return {
            "rule": name,
            "orders": 0,
            "active_dates": 0,
            "cities": 0,
            "cost": 0.0,
            "pnl": 0.0,
            "roi": None,
            "win_rate": None,
            "avg_ask": None,
            "avg_p": None,
            "avg_edge": None,
            "bootstrap_roi_ci95": [None, None],
        }
    cost = float(frame["current_yes_ask"].sum())
    pnl_series = frame["label_current_yes_survives"] - frame["current_yes_ask"]
    pnl = float(pnl_series.sum())
    confidence_interval = date_cluster_bootstrap_roi(
        frame.assign(_pnl=pnl_series),
        cost_col="current_yes_ask",
        pnl_col="_pnl",
        seed=seed,
    )
    return {
        "rule": name,
        "orders": int(len(frame)),
        "active_dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost else None,
        "win_rate": float(frame["label_current_yes_survives"].mean()),
        "avg_ask": float(frame["current_yes_ask"].mean()),
        "avg_p": float(frame[p_col].mean()),
        "avg_edge": float((frame[p_col] - frame["current_yes_ask"]).mean()),
        "bootstrap_roi_ci95": confidence_interval,
    }


def approx_metar_veto(frame: pd.DataFrame) -> pd.Series:
    minutes = pd.to_numeric(frame["minutes_since_running_max"], errors="coerce")
    warming = pd.to_numeric(frame["temp_trend_3h_f"], errors="coerce")
    min_gap = pd.to_numeric(
        frame["min_forecast_gap_to_running_native"], errors="coerce"
    )
    return (
        (minutes.notna() & minutes.lt(10))
        | (warming.notna() & warming.ge(1.5))
        | (min_gap.notna() & min_gap.lt(-0.1))
    )


def build_pipeline(
    numeric_features: list[str] | None = None,
    c: float = 0.8,
    *,
    default_numeric_features: list[str],
    categorical_features: list[str],
    seed: int,
) -> Pipeline:
    if numeric_features is None:
        numeric_features = default_numeric_features
    preprocessor = ColumnTransformer(
        [
            (
                "num",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="median")),
                        ("scale", StandardScaler()),
                    ]
                ),
                numeric_features,
            ),
            ("cat", OneHotEncoder(handle_unknown="ignore"), categorical_features),
        ]
    )
    return Pipeline(
        [
            ("pre", preprocessor),
            ("model", LogisticRegression(max_iter=3000, C=c, random_state=seed)),
        ]
    )


def artifact_from_model(
    model: Pipeline,
    train: pd.DataFrame,
    numeric_features: list[str],
    artifact_type: str,
    strategy_id: str,
    *,
    categorical_features: list[str],
    source_feature_rows: Path,
    root: Path,
    peak_decline_max_native: float,
    seed: int,
    trained_at_utc: Callable[[], str],
) -> dict[str, Any]:
    preprocessor = model.named_steps["pre"]
    numeric_pipeline = preprocessor.named_transformers_["num"]
    categorical = preprocessor.named_transformers_["cat"]
    classifier = model.named_steps["model"]
    categories = [[str(value) for value in values] for values in categorical.categories_]
    feature_names = list(numeric_features)
    for feature, category_values in zip(
        categorical_features, categories, strict=True
    ):
        feature_names.extend(
            [f"cat__{feature}_{category_value}" for category_value in category_values]
        )
    return {
        "artifact_type": artifact_type,
        "strategy_id": strategy_id,
        "label": "current_yes_survives",
        "source_feature_rows": str(source_feature_rows.relative_to(root)),
        "trained_at_utc": trained_at_utc(),
        "training_filter": (
            f"period == train AND decline_native <= {peak_decline_max_native}"
        ),
        "numeric_features": numeric_features,
        "categorical_features": categorical_features,
        "numeric_medians": numeric_pipeline.named_steps["imputer"].statistics_.tolist(),
        "numeric_means": numeric_pipeline.named_steps["scale"].mean_.tolist(),
        "numeric_scales": numeric_pipeline.named_steps["scale"].scale_.tolist(),
        "categories": categories,
        "feature_names": feature_names,
        "coef": classifier.coef_[0].tolist(),
        "intercept": float(classifier.intercept_[0]),
        "train_rows": int(len(train)),
        "train_dates": int(train["target_date"].nunique()),
        "train_positive_rate": float(train["label_current_yes_survives"].mean()),
        "sklearn_spec": {
            "model": "LogisticRegression(max_iter=3000, C=0.8)",
            "numeric_preprocess": "median_impute_then_standard_scale",
            "categorical_preprocess": "one_hot_handle_unknown_ignore",
            "random_state": seed,
        },
    }


def score_artifact(
    rows: pd.DataFrame,
    artifact: dict[str, Any],
    *,
    base_alias: dict[str, str],
) -> np.ndarray:
    numeric_features = list(artifact["numeric_features"])
    categorical_features = list(artifact["categorical_features"])
    work = rows.copy()
    for feature, alias in base_alias.items():
        if feature not in work.columns and alias in work.columns:
            work[feature] = work[alias]
    for feature in numeric_features:
        if feature not in work.columns:
            work[feature] = np.nan
    for feature in categorical_features:
        if feature not in work.columns:
            work[feature] = ""
    numeric = work[numeric_features].apply(
        pd.to_numeric, errors="coerce"
    ).to_numpy(dtype=float)
    medians = np.asarray(artifact["numeric_medians"], dtype=float)
    means = np.asarray(artifact["numeric_means"], dtype=float)
    scales = np.asarray(artifact["numeric_scales"], dtype=float)
    numeric = np.where(np.isfinite(numeric), numeric, medians)
    numeric = (numeric - means) / scales
    categorical_parts = []
    for index, feature in enumerate(categorical_features):
        values = work[feature].astype(str).to_numpy()
        categories = [str(value) for value in artifact["categories"][index]]
        matrix = np.zeros((len(work), len(categories)), dtype=float)
        lookup = {category: i for i, category in enumerate(categories)}
        for row_index, value in enumerate(values):
            column_index = lookup.get(str(value))
            if column_index is not None:
                matrix[row_index, column_index] = 1.0
        categorical_parts.append(matrix)
    design = np.hstack([numeric] + categorical_parts)
    logits = design @ np.asarray(artifact["coef"], dtype=float) + float(
        artifact["intercept"]
    )
    return 1.0 / (1.0 + np.exp(-logits))


def select_grid(train: pd.DataFrame, *, seed: int) -> list[dict[str, Any]]:
    rows = []
    for min_probability in [0.55, 0.60, 0.65, 0.70, 0.75]:
        for min_edge in [0.00, 0.02, 0.04, 0.06, 0.08]:
            for min_hour in [13, 14, 15]:
                mask = (
                    train["decision_hour_local"].between(min_hour, 17)
                    & train["current_yes_ask"].between(
                        0.50, 0.97, inclusive="both"
                    )
                    & train["min_forecast_peak_delta_hours_local"]
                    .fillna(-999)
                    .ge(-1.0)
                    & train["p_hazard"].ge(min_probability)
                    & (train["p_hazard"] - train["current_yes_ask"]).ge(min_edge)
                )
                subset = train[mask]
                if len(subset) < 20 or subset["target_date"].nunique() < 5:
                    continue
                stats = summarize_trade(
                    subset,
                    "p_hazard",
                    f"grid_h{min_hour}_p{min_probability:.2f}_edge{min_edge:.2f}",
                    seed=seed,
                )
                rows.append(
                    {
                        **stats,
                        "min_p": min_probability,
                        "min_edge": min_edge,
                        "min_hour": min_hour,
                    }
                )
    rows.sort(
        key=lambda row: (
            row.get("roi") if row.get("roi") is not None else -999,
            row["orders"],
        ),
        reverse=True,
    )
    return rows[:10]
