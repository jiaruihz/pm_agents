"""Date-safe challenger for D-1 single-YES forecast-update repricing.

The input is the candidate-level output of
``research_lmvm_forecast_innovation_v2.py``.  Models predict either the future
bid move or the fully executable taker markout.  Model, horizon, and entry
threshold are selected only on expanding target-date OOF predictions; the
last block of dates is opened once as a secondary holdout.

This module is research-only.  It writes model/evidence artifacts and never
touches a strategy runtime, order journal, or exchange API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import joblib
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.production import load_production_spec


SEED = 20260809
HORIZONS = (15, 30, 60, 120)
MIN_TOTAL_DATES = 30
MIN_SELECTION_DATES = 8
MIN_SELECTION_SIGNALS = 20
OOF_BLOCK_DATES = 3


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    feature_set: str
    estimator: str
    target_mode: str
    execution_style: str


MODEL_SPECS = (
    ModelSpec("R1_weather_ridge_gross", "weather", "ridge", "gross_move", "taker"),
    ModelSpec("R2_market_ridge_gross", "market", "ridge", "gross_move", "taker"),
    ModelSpec("R3_joint_ridge_gross", "joint", "ridge", "gross_move", "taker"),
    ModelSpec("R4_joint_hgb_gross", "joint", "hgb", "gross_move", "taker"),
    ModelSpec("R5_joint_ridge_net", "joint", "ridge", "net_markout", "taker"),
    ModelSpec("R6_joint_hgb_net", "joint", "hgb", "net_markout", "taker"),
    ModelSpec("R7_maker_weather_ridge_gross", "weather", "ridge", "gross_move", "maker_conditional"),
    ModelSpec("R8_maker_joint_ridge_gross", "joint", "ridge", "gross_move", "maker_conditional"),
    ModelSpec("R9_maker_joint_hgb_gross", "joint", "hgb", "gross_move", "maker_conditional"),
    ModelSpec("R10_maker_joint_hgb_net", "joint", "hgb", "net_markout", "maker_conditional"),
)

WEATHER_NUMERIC = (
    "model_probability_before",
    "model_probability_after",
    "model_probability_delta",
    "model_prob",
    "forecast_max_f",
    "decision_hour_sin",
    "decision_hour_cos",
    "rung_count",
)
MARKET_NUMERIC = (
    "market_probability_before",
    "market_probability_after",
    "market_probability_delta",
    "market_prob",
    "entry_bid",
    "entry_ask",
    "entry_spread",
    "entry_fee_per_share",
    "estimated_exit_fee_per_share",
    "execution_hurdle",
    "log_entry_bid_size",
    "log_entry_ask_size",
    "decision_hour_sin",
    "decision_hour_cos",
    "rung_count",
)
CATEGORICAL = ("city", "forecast_source", "forecast_model")

FULL_LADDER_HORIZONS = (5, 15, 30, 60)
FULL_LADDER_MIN_DATES = 24
FULL_LADDER_CATEGORICAL = ("forecast_source", "forecast_model")


@dataclass(frozen=True)
class FullLadderSpec:
    model_id: str
    feature_set: str


FULL_LADDER_SPECS = (
    FullLadderSpec("market_level_only", "market_level"),
    FullLadderSpec("weather_innovation_only", "weather_innovation"),
    FullLadderSpec("weather_plus_market_level", "weather_market"),
    FullLadderSpec(
        "market_plus_static_microstructure_control",
        "market_static_microstructure",
    ),
    FullLadderSpec(
        "weather_plus_market_plus_static_microstructure",
        "weather_market_static_microstructure",
    ),
)

FULL_WEATHER_NUMERIC = (
    "lead_days",
    "model_probability_before",
    "model_probability_after",
    "model_probability_delta",
    "forecast_innovation_score",
    "forecast_max_f",
    "model_probability_rank",
    "decision_hour_sin",
    "decision_hour_cos",
    "rung_count",
)
FULL_MARKET_LEVEL_NUMERIC = (
    "market_probability_after",
    "market_probability_rank",
    "entry_bid",
    "entry_mid",
    "market_mid_sum_raw",
    "decision_hour_sin",
    "decision_hour_cos",
    "rung_count",
)
FULL_STATIC_MICROSTRUCTURE_NUMERIC = (
    "entry_spread",
    "log_entry_bid_size",
    "log_entry_ask_size",
    "top_size_imbalance",
)


def weather_fee(price: pd.Series | np.ndarray | float) -> Any:
    return 0.05 * price * (1.0 - price)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "unknown"


def prepare_candidates(rows: pd.DataFrame) -> pd.DataFrame:
    required = {
        "policy", "lead_days", "target_date", "snapshot_epoch", "city",
        "entry_bid", "entry_ask", "entry_fee_per_share",
        "forecast_innovation_score", "model_probability_delta",
        "market_probability_delta",
    }
    missing = sorted(required - set(rows.columns))
    if missing:
        raise ValueError(f"candidate input missing required columns: {missing}")
    frame = rows[
        rows["policy"].eq("forecast_innovation_argmax")
        & rows["lead_days"].eq(1)
    ].copy()
    frame["target_date"] = frame["target_date"].astype(str)
    frame["entry_spread"] = frame["entry_ask"] - frame["entry_bid"]
    frame["estimated_exit_fee_per_share"] = weather_fee(frame["entry_bid"])
    frame["execution_hurdle"] = (
        frame["entry_spread"]
        + frame["entry_fee_per_share"]
        + frame["estimated_exit_fee_per_share"]
    )
    for column in (
        "entry_bid_size", "entry_ask_size", "decision_hour_local",
        "forecast_source", "forecast_model", "forecast_max_f",
        "model_edge_after_entry_fee", "rung_count", "model_prob", "market_prob",
        "model_probability_before", "model_probability_after",
        "market_probability_before", "market_probability_after",
    ):
        if column not in frame:
            frame[column] = np.nan
    for canonical, legacy in (
        ("model_probability_after", "model_prob"),
        ("market_probability_after", "market_prob"),
    ):
        if frame[canonical].notna().sum() == 0:
            frame[canonical] = pd.to_numeric(frame[legacy], errors="coerce")
    frame["log_entry_bid_size"] = np.log1p(
        pd.to_numeric(frame["entry_bid_size"], errors="coerce").clip(lower=0)
    )
    frame["log_entry_ask_size"] = np.log1p(
        pd.to_numeric(frame["entry_ask_size"], errors="coerce").clip(lower=0)
    )
    hour = pd.to_numeric(frame["decision_hour_local"], errors="coerce")
    frame["decision_hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
    frame["decision_hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
    for column in CATEGORICAL:
        frame[column] = frame[column].fillna("<missing>").astype(str)
    frame = frame.replace([np.inf, -np.inf], np.nan)
    return frame.sort_values(["target_date", "snapshot_epoch", "city"]).reset_index(drop=True)


def feature_columns(spec: ModelSpec) -> tuple[list[str], list[str]]:
    if spec.feature_set == "weather":
        numeric = list(WEATHER_NUMERIC)
    elif spec.feature_set == "market":
        numeric = list(MARKET_NUMERIC)
    elif spec.feature_set == "joint":
        numeric = list(
            dict.fromkeys(
                (*WEATHER_NUMERIC, *MARKET_NUMERIC, "forecast_innovation_score", "model_edge_after_entry_fee")
            )
        )
    else:
        raise ValueError(spec.feature_set)
    return numeric, list(CATEGORICAL)


def build_model(spec: ModelSpec) -> Pipeline:
    numeric, categorical = feature_columns(spec)
    numeric_pipe = Pipeline(
        [("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]
    )
    categorical_pipe = Pipeline(
        [
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=5, sparse_output=False)),
        ]
    )
    preprocess = ColumnTransformer(
        [("numeric", numeric_pipe, numeric), ("categorical", categorical_pipe, categorical)],
        remainder="drop",
        sparse_threshold=0.0,
    )
    if spec.estimator == "ridge":
        estimator = Ridge(alpha=10.0)
    elif spec.estimator == "hgb":
        estimator = HistGradientBoostingRegressor(
            learning_rate=0.04,
            max_iter=120,
            max_depth=3,
            min_samples_leaf=30,
            l2_regularization=10.0,
            random_state=SEED,
        )
    else:
        raise ValueError(spec.estimator)
    return Pipeline([("preprocess", preprocess), ("model", estimator)])


def label_column(spec: ModelSpec, horizon: int) -> str:
    if spec.target_mode == "gross_move":
        return f"h{horizon}_gross_bid_move"
    return f"h{horizon}_{spec.execution_style}_net_markout_per_share"


def add_targets(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for horizon in HORIZONS:
        result[f"h{horizon}_gross_bid_move"] = result[f"h{horizon}_bid"] - result["entry_bid"]
        result[f"h{horizon}_taker_net_markout_per_share"] = result[
            f"h{horizon}_net_markout_per_share"
        ]
        result[f"h{horizon}_maker_conditional_net_markout_per_share"] = (
            result[f"h{horizon}_bid"]
            - result[f"h{horizon}_exit_fee_per_share"]
            - result["entry_bid"]
        )
    return result


def date_equal_weights(rows: pd.DataFrame) -> np.ndarray:
    counts = rows.groupby("target_date")["target_date"].transform("size").astype(float)
    return (1.0 / counts).to_numpy()


def prepare_full_ladder_rows(rows: pd.DataFrame) -> pd.DataFrame:
    required = {
        "forecast_event_id",
        "target_date",
        "city",
        "lead_days",
        "condition_id",
        "snapshot_epoch",
        "entry_bid",
        "entry_ask",
        "market_probability_after",
        "model_probability_before",
        "model_probability_after",
        "model_probability_delta",
    }
    missing = sorted(required - set(rows.columns))
    if missing:
        raise ValueError(f"full-ladder input missing required columns: {missing}")
    frame = rows[rows["lead_days"].isin([1, 2])].copy()
    frame["target_date"] = frame["target_date"].astype(str)
    frame["forecast_event_id"] = frame["forecast_event_id"].astype(str)
    frame["entry_mid"] = (frame["entry_bid"] + frame["entry_ask"]) / 2.0
    frame["entry_spread"] = frame["entry_ask"] - frame["entry_bid"]
    frame["entry_fee_per_share"] = weather_fee(frame["entry_ask"])
    frame["estimated_exit_fee_per_share"] = weather_fee(frame["entry_bid"])
    frame["taker_execution_hurdle"] = (
        frame["entry_spread"]
        + frame["entry_fee_per_share"]
        + frame["estimated_exit_fee_per_share"]
    )
    for column in (
        "entry_bid_size",
        "entry_ask_size",
        "decision_hour_local",
        "forecast_source",
        "forecast_model",
        "forecast_max_f",
        "model_probability_rank",
        "market_probability_rank",
        "market_mid_sum_raw",
        "rung_count",
        "forecast_innovation_score",
    ):
        if column not in frame:
            frame[column] = np.nan
    bid_size = pd.to_numeric(frame["entry_bid_size"], errors="coerce").clip(lower=0)
    ask_size = pd.to_numeric(frame["entry_ask_size"], errors="coerce").clip(lower=0)
    frame["log_entry_bid_size"] = np.log1p(bid_size)
    frame["log_entry_ask_size"] = np.log1p(ask_size)
    total_size = bid_size + ask_size
    frame["top_size_imbalance"] = np.where(
        total_size.gt(0), (bid_size - ask_size) / total_size, np.nan
    )
    hour = pd.to_numeric(frame["decision_hour_local"], errors="coerce")
    frame["decision_hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
    frame["decision_hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
    for column in FULL_LADDER_CATEGORICAL:
        frame[column] = frame[column].fillna("<missing>").astype(str)
    return (
        frame.replace([np.inf, -np.inf], np.nan)
        .sort_values(["target_date", "snapshot_epoch", "city", "forecast_event_id", "condition_id"])
        .reset_index(drop=True)
    )


def full_ladder_feature_columns(spec: FullLadderSpec) -> tuple[list[str], list[str]]:
    if spec.feature_set == "market_level":
        numeric = list(FULL_MARKET_LEVEL_NUMERIC)
        categorical: list[str] = []
    elif spec.feature_set == "weather_innovation":
        numeric = list(FULL_WEATHER_NUMERIC)
        categorical = list(FULL_LADDER_CATEGORICAL)
    elif spec.feature_set == "weather_market":
        numeric = list(dict.fromkeys((*FULL_WEATHER_NUMERIC, *FULL_MARKET_LEVEL_NUMERIC)))
        categorical = list(FULL_LADDER_CATEGORICAL)
    elif spec.feature_set == "market_static_microstructure":
        numeric = list(
            dict.fromkeys((*FULL_MARKET_LEVEL_NUMERIC, *FULL_STATIC_MICROSTRUCTURE_NUMERIC))
        )
        categorical = []
    elif spec.feature_set == "weather_market_static_microstructure":
        numeric = list(
            dict.fromkeys(
                (
                    *FULL_WEATHER_NUMERIC,
                    *FULL_MARKET_LEVEL_NUMERIC,
                    *FULL_STATIC_MICROSTRUCTURE_NUMERIC,
                )
            )
        )
        categorical = list(FULL_LADDER_CATEGORICAL)
    else:
        raise ValueError(spec.feature_set)
    return numeric, categorical


def build_full_ladder_model(spec: FullLadderSpec) -> Pipeline:
    numeric, categorical = full_ladder_feature_columns(spec)
    transformers: list[tuple[str, Pipeline, list[str]]] = [
        (
            "numeric",
            Pipeline(
                [("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]
            ),
            numeric,
        )
    ]
    if categorical:
        transformers.append(
            (
                "categorical",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        (
                            "onehot",
                            OneHotEncoder(
                                handle_unknown="ignore",
                                min_frequency=5,
                                sparse_output=False,
                            ),
                        ),
                    ]
                ),
                categorical,
            )
        )
    preprocess = ColumnTransformer(transformers, remainder="drop", sparse_threshold=0.0)
    return Pipeline([("preprocess", preprocess), ("model", Ridge(alpha=10.0))])


def date_event_rung_equal_weights(rows: pd.DataFrame) -> np.ndarray:
    """Equal target dates, then events, then rungs within each event."""

    events_per_date = rows.groupby("target_date")["forecast_event_id"].transform("nunique")
    rungs_per_event = rows.groupby(["target_date", "forecast_event_id"])[
        "condition_id"
    ].transform("size")
    return (1.0 / (events_per_date.astype(float) * rungs_per_event.astype(float))).to_numpy()


def full_ladder_label(horizon: int) -> str:
    return f"h{horizon}_gross_bid_move"


def add_full_ladder_targets(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    for horizon in FULL_LADDER_HORIZONS:
        bid = f"h{horizon}_bid"
        output[full_ladder_label(horizon)] = output[bid] - output["entry_bid"]
        output[f"h{horizon}_taker_net_markout_per_share"] = (
            output[bid]
            - output[f"h{horizon}_exit_fee_per_share"]
            - output["entry_ask"]
            - output["entry_fee_per_share"]
        )
        output[f"h{horizon}_maker_conditional_net_markout_per_share"] = (
            output[bid]
            - output[f"h{horizon}_exit_fee_per_share"]
            - output["entry_bid"]
        )
    return output


def fit_full_ladder_model(
    train: pd.DataFrame,
    test: pd.DataFrame,
    spec: FullLadderSpec,
    horizon: int,
) -> tuple[Pipeline | None, np.ndarray]:
    label = full_ladder_label(horizon)
    usable = train[train[label].notna()].copy()
    if usable["target_date"].nunique() < 3 or usable["forecast_event_id"].nunique() < 30:
        return None, np.full(len(test), np.nan)
    numeric, categorical = full_ladder_feature_columns(spec)
    model = build_full_ladder_model(spec)
    model.fit(
        usable[numeric + categorical],
        usable[label].astype(float),
        model__sample_weight=date_event_rung_equal_weights(usable),
    )
    return model, model.predict(test[numeric + categorical])


def fit_predict(
    train: pd.DataFrame,
    test: pd.DataFrame,
    spec: ModelSpec,
    horizon: int,
) -> np.ndarray:
    label = label_column(spec, horizon)
    usable = train[train[label].notna()].copy()
    if usable["target_date"].nunique() < 3 or len(usable) < 30:
        return np.full(len(test), np.nan)
    numeric, categorical = feature_columns(spec)
    columns = numeric + categorical
    model = build_model(spec)
    model.fit(
        usable[columns],
        usable[label].astype(float),
        model__sample_weight=date_equal_weights(usable),
    )
    raw = model.predict(test[columns])
    if spec.target_mode == "gross_move":
        hurdle = (
            test["execution_hurdle"]
            if spec.execution_style == "taker"
            else test["estimated_exit_fee_per_share"]
        )
        return raw - hurdle.to_numpy(float)
    return raw


def fit_frozen_model(rows: pd.DataFrame, spec: ModelSpec, horizon: int) -> Pipeline:
    label = label_column(spec, horizon)
    usable = rows[rows[label].notna()].copy()
    numeric, categorical = feature_columns(spec)
    model = build_model(spec)
    model.fit(
        usable[numeric + categorical],
        usable[label].astype(float),
        model__sample_weight=date_equal_weights(usable),
    )
    return model


def score_frozen_rows(
    rows: pd.DataFrame,
    model: Pipeline,
    spec: ModelSpec,
    threshold: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = prepare_candidates(rows)
    numeric, categorical = feature_columns(spec)
    raw = model.predict(frame[numeric + categorical])
    if spec.target_mode == "gross_move":
        hurdle = (
            frame["execution_hurdle"]
            if spec.execution_style == "taker"
            else frame["estimated_exit_fee_per_share"]
        )
        frame["predicted_net_markout"] = raw - hurdle.to_numpy(float)
    else:
        frame["predicted_net_markout"] = raw
    frame["entry_threshold"] = float(threshold)
    frame["threshold_cross"] = frame["predicted_net_markout"].ge(float(threshold))
    selected = first_threshold_cross(frame, float(threshold))
    return frame, selected


def expanding_oof(
    development: pd.DataFrame,
    spec: ModelSpec,
    horizon: int,
    min_train_dates: int,
) -> pd.DataFrame:
    dates = sorted(development["target_date"].unique())
    predictions: list[pd.DataFrame] = []
    for index in range(min_train_dates, len(dates), OOF_BLOCK_DATES):
        train_dates = dates[:index]
        test_dates = dates[index : index + OOF_BLOCK_DATES]
        train = development[development["target_date"].isin(train_dates)]
        test = development[development["target_date"].isin(test_dates)].copy()
        test["predicted_net_markout"] = fit_predict(train, test, spec, horizon)
        test["model_id"] = spec.model_id
        test["horizon_min"] = horizon
        test["max_train_date"] = train_dates[-1]
        predictions.append(test)
    return pd.concat(predictions, ignore_index=True) if predictions else pd.DataFrame()


def first_threshold_cross(rows: pd.DataFrame, threshold: float) -> pd.DataFrame:
    qualified = rows[rows["predicted_net_markout"].ge(threshold)].copy()
    if qualified.empty:
        return qualified
    return (
        qualified.sort_values(["target_date", "city", "snapshot_epoch"])
        .groupby(["target_date", "city"], as_index=False, sort=False)
        .head(1)
    )


def roi_stats(
    rows: pd.DataFrame,
    horizon: int,
    execution_style: str,
    draws: int,
    seed: int,
) -> dict[str, Any]:
    shares = f"h{horizon}_executable_shares"
    markout = f"h{horizon}_{execution_style}_net_markout_per_share"
    usable = rows[rows[markout].notna() & rows[shares].gt(0)].copy()
    usable["strategy_pnl_usd"] = usable[markout] * usable[shares]
    entry_per_share = (
        usable["entry_ask"] + usable["entry_fee_per_share"]
        if execution_style == "taker"
        else usable["entry_bid"]
    )
    usable["entry_cost_usd"] = entry_per_share * usable[shares]
    by_date = usable.groupby("target_date", as_index=False).agg(
        pnl=("strategy_pnl_usd", "sum"),
        cost=("entry_cost_usd", "sum"),
        signals=("strategy_pnl_usd", "size"),
    )
    cost = float(by_date["cost"].sum()) if len(by_date) else 0.0
    point = float(by_date["pnl"].sum() / cost) if cost else math.nan
    boot: list[float] = []
    if len(by_date) >= 3 and draws:
        values = by_date[["pnl", "cost"]].to_numpy(float)
        rng = np.random.default_rng(seed)
        for _ in range(draws):
            sample = values[rng.integers(0, len(values), size=len(values))]
            denominator = float(sample[:, 1].sum())
            if denominator:
                boot.append(float(sample[:, 0].sum() / denominator))
    return {
        "signals": int(len(usable)),
        "target_dates": int(len(by_date)),
        "cost_usd": cost,
        "pnl_usd": float(by_date["pnl"].sum()) if len(by_date) else 0.0,
        "roi": point,
        "ci_low": float(np.percentile(boot, 2.5)) if boot else math.nan,
        "ci_high": float(np.percentile(boot, 97.5)) if boot else math.nan,
        "positive_signal_rate": float((usable["strategy_pnl_usd"] > 0).mean()) if len(usable) else math.nan,
        "positive_date_rate": float((by_date["pnl"] > 0).mean()) if len(by_date) else math.nan,
    }


def expanding_full_ladder_oof(
    development: pd.DataFrame,
    spec: FullLadderSpec,
    horizon: int,
    min_train_dates: int,
) -> pd.DataFrame:
    dates = sorted(development["target_date"].unique())
    output: list[pd.DataFrame] = []
    for index in range(min_train_dates, len(dates), OOF_BLOCK_DATES):
        train_dates = dates[:index]
        test_dates = dates[index : index + OOF_BLOCK_DATES]
        train = development[development["target_date"].isin(train_dates)]
        test = development[development["target_date"].isin(test_dates)].copy()
        _, predicted = fit_full_ladder_model(train, test, spec, horizon)
        test["predicted_bid_move"] = predicted
        test["model_id"] = spec.model_id
        test["horizon_min"] = horizon
        test["max_train_date"] = train_dates[-1]
        output.append(test)
    return pd.concat(output, ignore_index=True) if output else pd.DataFrame()


def vector_markout_metrics(rows: pd.DataFrame, horizon: int) -> dict[str, Any]:
    label = full_ladder_label(horizon)
    usable = rows[rows[label].notna() & rows["predicted_bid_move"].notna()].copy()
    if usable.empty:
        return {
            "rungs": 0,
            "events": 0,
            "target_dates": 0,
            "mse": math.nan,
            "mae": math.nan,
            "directional_accuracy": math.nan,
        }
    usable["sq_error"] = (usable["predicted_bid_move"] - usable[label]) ** 2
    usable["abs_error"] = (usable["predicted_bid_move"] - usable[label]).abs()
    usable["direction_correct"] = (
        np.sign(usable["predicted_bid_move"]) == np.sign(usable[label])
    ).astype(float)
    event = usable.groupby(["target_date", "forecast_event_id"], as_index=False).agg(
        mse=("sq_error", "mean"),
        mae=("abs_error", "mean"),
        directional_accuracy=("direction_correct", "mean"),
    )
    by_date = event.groupby("target_date", as_index=False).agg(
        mse=("mse", "mean"),
        mae=("mae", "mean"),
        directional_accuracy=("directional_accuracy", "mean"),
    )
    return {
        "rungs": int(len(usable)),
        "events": int(usable["forecast_event_id"].nunique()),
        "target_dates": int(len(by_date)),
        "mse": float(by_date["mse"].mean()),
        "mae": float(by_date["mae"].mean()),
        "directional_accuracy": float(by_date["directional_accuracy"].mean()),
    }


def paired_vector_loss_delta(
    challenger: pd.DataFrame,
    baseline: pd.DataFrame,
    horizon: int,
    draws: int,
    seed: int,
) -> dict[str, Any]:
    label = full_ladder_label(horizon)
    keys = ["target_date", "forecast_event_id", "condition_id"]
    left = challenger[keys + [label, "predicted_bid_move"]].dropna()
    right = baseline[keys + ["predicted_bid_move"]].dropna()
    paired = left.merge(right, on=keys, suffixes=("_challenger", "_baseline"))
    if paired.empty:
        return {
            "paired_rungs": 0,
            "paired_events": 0,
            "target_dates": 0,
            "mse_delta_challenger_minus_market": math.nan,
            "ci_low": math.nan,
            "ci_high": math.nan,
        }
    actual = paired[label]
    paired["loss_delta"] = (
        (paired["predicted_bid_move_challenger"] - actual) ** 2
        - (paired["predicted_bid_move_baseline"] - actual) ** 2
    )
    event = paired.groupby(["target_date", "forecast_event_id"], as_index=False)[
        "loss_delta"
    ].mean()
    by_date = event.groupby("target_date", as_index=False)["loss_delta"].mean()
    point = float(by_date["loss_delta"].mean())
    boot: list[float] = []
    if len(by_date) >= 3 and draws:
        values = by_date["loss_delta"].to_numpy(float)
        rng = np.random.default_rng(seed)
        for _ in range(draws):
            boot.append(float(values[rng.integers(0, len(values), size=len(values))].mean()))
    return {
        "paired_rungs": int(len(paired)),
        "paired_events": int(paired["forecast_event_id"].nunique()),
        "target_dates": int(len(by_date)),
        "mse_delta_challenger_minus_market": point,
        "ci_low": float(np.percentile(boot, 2.5)) if boot else math.nan,
        "ci_high": float(np.percentile(boot, 97.5)) if boot else math.nan,
    }


def select_full_ladder_signals(rows: pd.DataFrame, threshold: float = 0.0) -> pd.DataFrame:
    qualified = rows[rows["predicted_taker_net_markout"].gt(float(threshold))].copy()
    if qualified.empty:
        return qualified
    per_event = (
        qualified.sort_values(
            ["target_date", "forecast_event_id", "predicted_taker_net_markout"],
            ascending=[True, True, False],
        )
        .groupby(["target_date", "forecast_event_id"], as_index=False, sort=False)
        .head(1)
    )
    return (
        per_event.sort_values(["target_date", "city", "snapshot_epoch"])
        .groupby(["target_date", "city"], as_index=False, sort=False)
        .head(1)
    )


def execution_capacity(rows: pd.DataFrame, horizon: int) -> dict[str, Any]:
    usable = rows[rows[f"h{horizon}_bid"].notna()].copy()
    entry_depth = pd.to_numeric(usable["entry_ask_size"], errors="coerce")
    exit_depth = pd.to_numeric(usable[f"h{horizon}_bid_size"], errors="coerce")
    usable["raw_top_executable_shares"] = pd.concat([entry_depth, exit_depth], axis=1).min(axis=1)
    usable["raw_top_executable_shares"] = usable["raw_top_executable_shares"].clip(lower=0)
    usable["raw_top_entry_cost_usd"] = (
        usable["entry_ask"] + usable["entry_fee_per_share"]
    ) * usable["raw_top_executable_shares"]
    return {
        "signals_with_depth": int(usable["raw_top_executable_shares"].notna().sum()),
        "signals_5_share_executable": int(usable["raw_top_executable_shares"].ge(5).sum()),
        "median_raw_top_executable_shares": float(usable["raw_top_executable_shares"].median()) if len(usable) else math.nan,
        "p25_raw_top_executable_shares": float(usable["raw_top_executable_shares"].quantile(0.25)) if len(usable) else math.nan,
        "total_raw_top_entry_cost_usd": float(usable["raw_top_entry_cost_usd"].sum()) if len(usable) else 0.0,
    }


def policy_roi_delta(
    challenger: pd.DataFrame,
    baseline: pd.DataFrame,
    horizon: int,
    draws: int,
    seed: int,
) -> dict[str, Any]:
    pnl_col = f"h{horizon}_taker_net_markout_per_share"
    shares_col = f"h{horizon}_executable_shares"

    def daily(frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
        usable = frame[frame[pnl_col].notna() & frame[shares_col].gt(0)].copy()
        usable["pnl"] = usable[pnl_col] * usable[shares_col]
        usable["cost"] = (
            usable["entry_ask"] + usable["entry_fee_per_share"]
        ) * usable[shares_col]
        return usable.groupby("target_date", as_index=False).agg(
            **{f"pnl_{prefix}": ("pnl", "sum"), f"cost_{prefix}": ("cost", "sum")}
        )

    merged = daily(challenger, "challenger").merge(
        daily(baseline, "baseline"), on="target_date", how="outer"
    ).fillna(0.0)
    if merged.empty:
        return {"roi_delta": math.nan, "ci_low": math.nan, "ci_high": math.nan, "target_dates": 0}
    values = merged[
        ["pnl_challenger", "cost_challenger", "pnl_baseline", "cost_baseline"]
    ].to_numpy(float)

    def ratio_delta(sample: np.ndarray) -> float:
        if sample[:, 1].sum() <= 0 or sample[:, 3].sum() <= 0:
            return math.nan
        return float(sample[:, 0].sum() / sample[:, 1].sum() - sample[:, 2].sum() / sample[:, 3].sum())

    point = ratio_delta(values)
    boot: list[float] = []
    if len(values) >= 3 and draws:
        rng = np.random.default_rng(seed)
        for _ in range(draws):
            value = ratio_delta(values[rng.integers(0, len(values), size=len(values))])
            if math.isfinite(value):
                boot.append(value)
    return {
        "roi_delta": point,
        "ci_low": float(np.percentile(boot, 2.5)) if boot else math.nan,
        "ci_high": float(np.percentile(boot, 97.5)) if boot else math.nan,
        "target_dates": int(len(values)),
    }


def run_full_ladder_tournament(
    rows: pd.DataFrame,
    *,
    min_train_dates: int = 15,
    holdout_fraction: float = 0.20,
    draws: int = 2_000,
) -> dict[str, Any]:
    frame = add_full_ladder_targets(prepare_full_ladder_rows(rows))
    dates = sorted(frame["target_date"].unique())
    coverage = {
        horizon: {
            "rungs": int(frame[f"h{horizon}_bid"].notna().sum()),
            "events": int(frame.loc[frame[f"h{horizon}_bid"].notna(), "forecast_event_id"].nunique()),
            "target_dates": int(frame.loc[frame[f"h{horizon}_bid"].notna(), "target_date"].nunique()),
        }
        for horizon in FULL_LADDER_HORIZONS
    }
    eligible_horizons = [
        horizon
        for horizon, item in coverage.items()
        if item["target_dates"] >= FULL_LADDER_MIN_DATES
    ]
    if len(dates) < FULL_LADDER_MIN_DATES or not eligible_horizons:
        return {
            "status": "blocked_insufficient_full_ladder_dates",
            "available_target_dates": len(dates),
            "required_target_dates": FULL_LADDER_MIN_DATES,
            "coverage": coverage,
            "frame": frame,
        }
    holdout_count = max(6, int(math.ceil(len(dates) * holdout_fraction)))
    development_dates = dates[:-holdout_count]
    holdout_dates = dates[-holdout_count:]
    development = frame[frame["target_date"].isin(development_dates)].copy()
    holdout = frame[frame["target_date"].isin(holdout_dates)].copy()

    oof_parts: list[pd.DataFrame] = []
    for spec in FULL_LADDER_SPECS:
        for horizon in eligible_horizons:
            part = expanding_full_ladder_oof(development, spec, horizon, min_train_dates)
            if not part.empty:
                oof_parts.append(part)
    oof = pd.concat(oof_parts, ignore_index=True)
    development_metrics: list[dict[str, Any]] = []
    development_deltas: list[dict[str, Any]] = []
    development_incremental: list[dict[str, Any]] = []
    incremental_baseline = {
        "weather_innovation_only": "market_level_only",
        "weather_plus_market_level": "market_level_only",
        "weather_plus_market_plus_static_microstructure": "market_plus_static_microstructure_control",
    }
    for horizon in eligible_horizons:
        baseline = oof[
            oof["model_id"].eq("market_level_only") & oof["horizon_min"].eq(horizon)
        ]
        for spec in FULL_LADDER_SPECS:
            group = oof[oof["model_id"].eq(spec.model_id) & oof["horizon_min"].eq(horizon)]
            development_metrics.append(
                {"model_id": spec.model_id, "horizon_min": horizon, **vector_markout_metrics(group, horizon)}
            )
            if spec.model_id != "market_level_only":
                development_deltas.append(
                    {
                        "model_id": spec.model_id,
                        "horizon_min": horizon,
                        **paired_vector_loss_delta(
                            group,
                            baseline,
                            horizon,
                            draws,
                            SEED + horizon + len(development_deltas),
                        ),
                    }
                )
        for model_id, baseline_id in incremental_baseline.items():
            challenger = oof[
                oof["model_id"].eq(model_id) & oof["horizon_min"].eq(horizon)
            ]
            matched_baseline = oof[
                oof["model_id"].eq(baseline_id) & oof["horizon_min"].eq(horizon)
            ]
            development_incremental.append(
                {
                    "model_id": model_id,
                    "baseline_model_id": baseline_id,
                    "horizon_min": horizon,
                    **paired_vector_loss_delta(
                        challenger,
                        matched_baseline,
                        horizon,
                        draws,
                        SEED + 1000 + horizon + len(development_incremental),
                    ),
                }
            )
    delta_frame = pd.DataFrame(development_deltas)
    incremental_frame = pd.DataFrame(development_incremental)
    selected_row = incremental_frame.sort_values(
        ["mse_delta_challenger_minus_market", "horizon_min", "model_id"]
    ).iloc[0]
    selected_model_id = str(selected_row["model_id"])
    selected_baseline_model_id = str(selected_row["baseline_model_id"])
    selected_horizon = int(selected_row["horizon_min"])
    threshold = 0.0

    holdout_parts: list[pd.DataFrame] = []
    fitted_models: dict[str, Pipeline] = {}
    for spec in FULL_LADDER_SPECS:
        model, predicted = fit_full_ladder_model(development, holdout, spec, selected_horizon)
        scored = holdout.copy()
        scored["predicted_bid_move"] = predicted
        scored["predicted_taker_net_markout"] = (
            scored["predicted_bid_move"] - scored["taker_execution_hurdle"]
        )
        scored["model_id"] = spec.model_id
        scored["horizon_min"] = selected_horizon
        holdout_parts.append(scored)
        if model is not None:
            fitted_models[spec.model_id] = model
    holdout_scored = pd.concat(holdout_parts, ignore_index=True)
    holdout_metrics: list[dict[str, Any]] = []
    holdout_deltas: list[dict[str, Any]] = []
    holdout_incremental: list[dict[str, Any]] = []
    market_holdout = holdout_scored[holdout_scored["model_id"].eq("market_level_only")]
    for spec in FULL_LADDER_SPECS:
        group = holdout_scored[holdout_scored["model_id"].eq(spec.model_id)]
        holdout_metrics.append(
            {
                "model_id": spec.model_id,
                "horizon_min": selected_horizon,
                **vector_markout_metrics(group, selected_horizon),
            }
        )
        if spec.model_id != "market_level_only":
            holdout_deltas.append(
                {
                    "model_id": spec.model_id,
                    "horizon_min": selected_horizon,
                    **paired_vector_loss_delta(
                        group,
                        market_holdout,
                        selected_horizon,
                        draws,
                        SEED + 5000 + len(holdout_deltas),
                    ),
                }
            )

    for model_id, baseline_id in incremental_baseline.items():
        challenger = holdout_scored[holdout_scored["model_id"].eq(model_id)]
        matched_baseline = holdout_scored[
            holdout_scored["model_id"].eq(baseline_id)
        ]
        holdout_incremental.append(
            {
                "model_id": model_id,
                "baseline_model_id": baseline_id,
                "horizon_min": selected_horizon,
                **paired_vector_loss_delta(
                    challenger,
                    matched_baseline,
                    selected_horizon,
                    draws,
                    SEED + 6000 + len(holdout_incremental),
                ),
            }
        )

    selected_scored = holdout_scored[holdout_scored["model_id"].eq(selected_model_id)]
    market_scored = holdout_scored[holdout_scored["model_id"].eq("market_level_only")]
    matching_baseline_scored = holdout_scored[
        holdout_scored["model_id"].eq(selected_baseline_model_id)
    ]
    selected_signals = select_full_ladder_signals(selected_scored, threshold)
    market_signals = select_full_ladder_signals(market_scored, threshold)
    matching_baseline_signals = select_full_ladder_signals(
        matching_baseline_scored, threshold
    )
    taker = roi_stats(selected_signals, selected_horizon, "taker", draws, SEED + 7000)
    market_taker = roi_stats(market_signals, selected_horizon, "taker", draws, SEED + 7100)
    matching_baseline_taker = roi_stats(
        matching_baseline_signals,
        selected_horizon,
        "taker",
        draws,
        SEED + 7150,
    )
    maker_conditional = roi_stats(
        selected_signals,
        selected_horizon,
        "maker_conditional",
        draws,
        SEED + 7200,
    )
    execution_delta = policy_roi_delta(
        selected_signals,
        matching_baseline_signals,
        selected_horizon,
        draws,
        SEED + 7300,
    )
    capacity = execution_capacity(selected_signals, selected_horizon)
    selected_holdout_delta = next(
        item for item in holdout_incremental if item["model_id"] == selected_model_id
    )
    status = "historical_reconstructed_point_only"
    if (
        selected_holdout_delta["ci_high"] < 0
        and taker["ci_low"] > 0
        and execution_delta["ci_low"] > 0
    ):
        status = "historical_reconstructed_pass_forward_still_required"
    return {
        "status": status,
        "frame": frame,
        "coverage": coverage,
        "development_dates": development_dates,
        "holdout_dates": holdout_dates,
        "eligible_horizons": eligible_horizons,
        "oof_predictions": oof,
        "development_metrics": pd.DataFrame(development_metrics),
        "development_deltas": delta_frame,
        "development_incremental_deltas": pd.DataFrame(development_incremental),
        "selected_model_id": selected_model_id,
        "selected_baseline_model_id": selected_baseline_model_id,
        "selected_horizon": selected_horizon,
        "threshold": threshold,
        "holdout_predictions": holdout_scored,
        "holdout_metrics": pd.DataFrame(holdout_metrics),
        "holdout_deltas": pd.DataFrame(holdout_deltas),
        "holdout_incremental_deltas": pd.DataFrame(holdout_incremental),
        "selected_signals": selected_signals,
        "market_signals": market_signals,
        "matching_baseline_signals": matching_baseline_signals,
        "taker": taker,
        "market_taker": market_taker,
        "matching_baseline_taker": matching_baseline_taker,
        "maker_conditional": maker_conditional,
        "execution_delta": execution_delta,
        "capacity": capacity,
        "fitted_model": fitted_models.get(selected_model_id),
        "multiple_testing_arms": len(FULL_LADDER_SPECS) * len(eligible_horizons),
        "formal_forward": "NA_reconstructed_archive_and_no_post_freeze_settled_dates",
        "ws_dynamic_microstructure": "BLOCKED_no_D2_D1_policy_valid_capture",
        "actual_maker_fills": 0,
    }


def threshold_grid(predictions: pd.Series) -> list[tuple[str, float]]:
    clean = predictions.dropna().astype(float)
    if clean.empty:
        return []
    values = [("zero", 0.0)]
    for quantile in (0.50, 0.70, 0.80, 0.90, 0.95):
        values.append((f"q{int(quantile * 100)}", float(clean.quantile(quantile))))
    unique: dict[float, str] = {}
    for name, value in values:
        unique.setdefault(round(value, 10), name)
    return [(name, value) for value, name in unique.items()]


def select_configuration(
    predictions: pd.DataFrame, draws: int
) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (model_id, horizon), group in predictions.groupby(["model_id", "horizon_min"]):
        spec = next(item for item in MODEL_SPECS if item.model_id == model_id)
        for threshold_name, threshold in threshold_grid(group["predicted_net_markout"]):
            selected = first_threshold_cross(group, threshold)
            stats = roi_stats(
                selected,
                int(horizon),
                spec.execution_style,
                draws,
                SEED + int(horizon),
            )
            rows.append(
                {
                    "model_id": model_id,
                    "execution_style": spec.execution_style,
                    "horizon_min": int(horizon),
                    "threshold_name": threshold_name,
                    "threshold": threshold,
                    **stats,
                }
            )
    tournament = pd.DataFrame(rows)
    eligible = tournament[
        tournament["signals"].ge(MIN_SELECTION_SIGNALS)
        & tournament["target_dates"].ge(MIN_SELECTION_DATES)
    ].copy()
    if eligible.empty:
        raise ValueError("no development configuration meets minimum signals/dates")
    freeze_eligible = eligible[
        eligible["roi"].gt(0)
        & eligible["positive_date_rate"].ge(0.50)
    ].copy()
    selection_pool = freeze_eligible if not freeze_eligible.empty else eligible
    selection_pool["selection_score"] = selection_pool["ci_low"].fillna(-np.inf)
    best = selection_pool.sort_values(
        ["selection_score", "roi", "target_dates", "signals"], ascending=False
    ).iloc[0].to_dict()
    best["development_freeze_eligible"] = bool(not freeze_eligible.empty)
    return tournament, best


def run_tournament(
    rows: pd.DataFrame,
    *,
    min_train_dates: int = 15,
    holdout_fraction: float = 0.20,
    draws: int = 2_000,
) -> dict[str, Any]:
    frame = add_targets(prepare_candidates(rows))
    dates = sorted(frame["target_date"].unique())
    coverage_dates = {
        horizon: int(frame.loc[frame[f"h{horizon}_net_markout_per_share"].notna(), "target_date"].nunique())
        for horizon in HORIZONS
    }
    eligible_horizons = [
        horizon for horizon, count in coverage_dates.items() if count >= MIN_TOTAL_DATES
    ]
    if len(dates) < MIN_TOTAL_DATES or not eligible_horizons:
        return {
            "status": "blocked_insufficient_target_dates",
            "required_target_dates": MIN_TOTAL_DATES,
            "available_target_dates": len(dates),
            "markout_target_dates_by_horizon": coverage_dates,
            "available_rows": len(frame),
            "date_start": dates[0] if dates else None,
            "date_end": dates[-1] if dates else None,
        }
    holdout_count = max(6, int(math.ceil(len(dates) * holdout_fraction)))
    development_dates = dates[:-holdout_count]
    holdout_dates = dates[-holdout_count:]
    if len(development_dates) <= min_train_dates + MIN_SELECTION_DATES:
        raise ValueError("not enough development dates after frozen holdout split")
    development = frame[frame["target_date"].isin(development_dates)].copy()
    holdout = frame[frame["target_date"].isin(holdout_dates)].copy()

    oof_parts: list[pd.DataFrame] = []
    for spec in MODEL_SPECS:
        for horizon in eligible_horizons:
            part = expanding_oof(development, spec, horizon, min_train_dates)
            if not part.empty:
                oof_parts.append(part)
    oof = pd.concat(oof_parts, ignore_index=True)
    tournament, selected = select_configuration(oof, draws)
    spec = next(item for item in MODEL_SPECS if item.model_id == selected["model_id"])
    horizon = int(selected["horizon_min"])
    holdout_scored = holdout.copy()
    holdout_scored["predicted_net_markout"] = fit_predict(
        development, holdout_scored, spec, horizon
    )
    holdout_scored["model_id"] = spec.model_id
    holdout_scored["horizon_min"] = horizon
    selected_holdout = first_threshold_cross(holdout_scored, float(selected["threshold"]))
    holdout_stats = roi_stats(
        selected_holdout,
        horizon,
        spec.execution_style,
        draws,
        SEED + 9000 + horizon,
    )
    baseline = (
        holdout.sort_values(["target_date", "city", "snapshot_epoch"])
        .groupby(["target_date", "city"], as_index=False, sort=False)
        .head(1)
    )
    baseline_stats = roi_stats(
        baseline,
        horizon,
        spec.execution_style,
        draws,
        SEED + 10000 + horizon,
    )

    label = f"h{horizon}_{spec.execution_style}_net_markout_per_share"
    calibration_rows = holdout_scored[
        holdout_scored["predicted_net_markout"].notna()
        & holdout_scored[label].notna()
    ]
    calibration = {
        "mean_predicted_net_markout": float(calibration_rows["predicted_net_markout"].mean()) if len(calibration_rows) else math.nan,
        "mean_actual_net_markout": float(calibration_rows[label].mean()) if len(calibration_rows) else math.nan,
        "mae": float((calibration_rows["predicted_net_markout"] - calibration_rows[label]).abs().mean()) if len(calibration_rows) else math.nan,
    }
    freeze_ok = (
        holdout_stats["signals"] >= MIN_SELECTION_SIGNALS
        and holdout_stats["target_dates"] >= 6
        and math.isfinite(holdout_stats["roi"])
        and holdout_stats["roi"] > 0
        and holdout_stats["roi"] > baseline_stats["roi"]
        and holdout_stats["positive_date_rate"] >= 0.50
        and selected["roi"] > 0
        and selected["positive_date_rate"] >= 0.50
    )
    frozen_model = fit_frozen_model(frame, spec, horizon) if freeze_ok else None
    status = (
        "freeze_candidate"
        if freeze_ok and spec.execution_style == "taker"
        else "maker_probe_candidate"
        if freeze_ok
        else "challenger_rejected"
    )
    return {
        "status": status,
        "frame": frame,
        "oof_predictions": oof,
        "tournament": tournament,
        "development_dates": development_dates,
        "holdout_dates": holdout_dates,
        "selected": selected,
        "holdout_predictions": holdout_scored,
        "selected_holdout": selected_holdout,
        "holdout": holdout_stats,
        "baseline": baseline_stats,
        "calibration": calibration,
        "model_spec": asdict(spec),
        "frozen_model": frozen_model,
        "multiple_testing_arms": len(MODEL_SPECS) * len(eligible_horizons),
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def write_full_ladder_outputs(
    result: dict[str, Any], input_path: Path, output_dir: Path
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    frame = result["frame"]
    denominator = {
        "scope": "reconstructed D-2/D-1 complete-ladder forecast revisions; every selected and unselected rung retained",
        "rungs": int(len(frame)),
        "forecast_events": int(frame["forecast_event_id"].nunique()) if len(frame) else 0,
        "target_dates": int(frame["target_date"].nunique()) if len(frame) else 0,
        "cities": int(frame["city"].nunique()) if len(frame) else 0,
        "selected_rows": int(frame.get("selected_by_innovation", pd.Series(dtype=bool)).fillna(False).sum()),
        "unselected_rows": int(len(frame) - frame.get("selected_by_innovation", pd.Series(dtype=bool)).fillna(False).sum()),
        "by_lead": {
            f"D-{int(lead)}": {
                "rungs": int(len(group)),
                "events": int(group["forecast_event_id"].nunique()),
                "target_dates": int(group["target_date"].nunique()),
            }
            for lead, group in frame.groupby("lead_days")
        },
        "clocks": {
            "forecast_issue_time": "model_init_utc_estimated_when_present",
            "provider_first_seen": "unavailable_in_reconstructed_archive",
            "collector_first_seen": "legacy_earliest_observed_not_collector_exact",
            "book_snapshot_time": "snapshot_ts_utc",
            "execution_time": "same_snapshot_replay_assumption",
        },
    }
    if result["status"] == "blocked_insufficient_full_ladder_dates":
        summary = {
            "schema_version": "forecast_repricing_full_ladder_v1",
            "generated_at_utc": generated_at,
            "status": result["status"],
            "input_path": str(input_path),
            "input_sha256": _sha256(input_path),
            "code_revision": _git_sha(),
            "denominator": denominator,
            "coverage": result["coverage"],
            "production": {"live_action": "none", "orders_changed": 0},
        }
        (output_dir / "summary.json").write_text(
            json.dumps(_json_safe(summary), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (output_dir / "report.md").write_text(
            "# Forecast Repricing full-ladder evaluation\n\n"
            "status=BLOCKED_INSUFFICIENT_DATES\n\n"
            f"固定分母有 {denominator['forecast_events']} 个 forecast events / "
            f"{denominator['target_dates']} 个 target dates；没有 horizon 达到 "
            f"{FULL_LADDER_MIN_DATES} 个日期。production orders_changed=0。\n",
            encoding="utf-8",
        )
        return

    for key, name in (
        ("oof_predictions", "development_oof_predictions.csv"),
        ("development_metrics", "development_model_metrics.csv"),
        ("development_deltas", "development_model_deltas_vs_market.csv"),
        ("development_incremental_deltas", "development_weather_incremental_deltas.csv"),
        ("holdout_predictions", "secondary_holdout_predictions.csv"),
        ("holdout_metrics", "secondary_holdout_model_metrics.csv"),
        ("holdout_deltas", "secondary_holdout_model_deltas_vs_market.csv"),
        ("holdout_incremental_deltas", "secondary_holdout_weather_incremental_deltas.csv"),
        ("selected_signals", "secondary_holdout_selected_signals.csv"),
        ("market_signals", "secondary_holdout_market_baseline_signals.csv"),
        ("matching_baseline_signals", "secondary_holdout_matching_baseline_signals.csv"),
    ):
        result[key].to_csv(output_dir / name, index=False)
    selected_delta = result["holdout_incremental_deltas"].loc[
        result["holdout_incremental_deltas"]["model_id"].eq(result["selected_model_id"])
    ].iloc[0].to_dict()
    summary = {
        "schema_version": "forecast_repricing_full_ladder_v1",
        "generated_at_utc": generated_at,
        "status": result["status"],
        "input_path": str(input_path),
        "input_sha256": _sha256(input_path),
        "code_revision": _git_sha(),
        "denominator": denominator,
        "coverage": result["coverage"],
        "development_dates": result["development_dates"],
        "secondary_holdout_dates": result["holdout_dates"],
        "eligible_horizons": result["eligible_horizons"],
        "model_families": [asdict(spec) for spec in FULL_LADDER_SPECS],
        "selected_model_id": result["selected_model_id"],
        "selected_incremental_baseline_model_id": result["selected_baseline_model_id"],
        "selected_horizon_min": result["selected_horizon"],
        "entry_threshold_predicted_taker_net_markout": result["threshold"],
        "selection_contract": "development expanding OOF only; Ridge fixed; threshold fixed at zero; one rung per forecast event then first signal per city-target_date",
        "development_model_metrics": result["development_metrics"].to_dict("records"),
        "development_deltas_vs_market": result["development_deltas"].to_dict("records"),
        "development_weather_incremental_deltas": result[
            "development_incremental_deltas"
        ].to_dict("records"),
        "secondary_holdout_model_metrics": result["holdout_metrics"].to_dict("records"),
        "secondary_holdout_deltas_vs_market": result["holdout_deltas"].to_dict("records"),
        "secondary_holdout_weather_incremental_deltas": result[
            "holdout_incremental_deltas"
        ].to_dict("records"),
        "selected_holdout_weather_incremental_delta": selected_delta,
        "taker": result["taker"],
        "market_level_execution_baseline": result["market_taker"],
        "matching_incremental_execution_baseline": result["matching_baseline_taker"],
        "taker_roi_delta_vs_matching_incremental_baseline": result["execution_delta"],
        "maker_conditional_quote_return": result["maker_conditional"],
        "maker_evidence": {
            "actual_maker_fills": result["actual_maker_fills"],
            "future_touch_used_as_fill": False,
            "queue_partial_expire_adverse_selection": "not_observed",
        },
        "capacity": result["capacity"],
        "formal_forward": result["formal_forward"],
        "ws_dynamic_microstructure": result["ws_dynamic_microstructure"],
        "multiple_testing_arms": result["multiple_testing_arms"],
        "production": {"live_action": "none", "orders_changed": 0},
    }
    (output_dir / "summary.json").write_text(
        json.dumps(_json_safe(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if result.get("fitted_model") is not None:
        model_path = output_dir / "reconstructed_candidate_model.joblib"
        joblib.dump(result["fitted_model"], model_path)
        (output_dir / "reconstructed_candidate_model.json").write_text(
            json.dumps(
                _json_safe(
                    {
                        "status": "reconstructed_development_only_not_formal_forward",
                        "model_id": result["selected_model_id"],
                        "horizon_min": result["selected_horizon"],
                        "threshold": result["threshold"],
                        "model_path": model_path.name,
                        "model_sha256": _sha256(model_path),
                        "input_sha256": summary["input_sha256"],
                    }
                ),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    model_rows = []
    holdout_delta_by_model = {
        row["model_id"]: row for row in result["holdout_deltas"].to_dict("records")
    }
    for row in result["holdout_metrics"].to_dict("records"):
        delta = holdout_delta_by_model.get(row["model_id"])
        delta_text = "baseline" if delta is None else (
            f"{delta['mse_delta_challenger_minus_market']:+.6f} "
            f"[{delta['ci_low']:+.6f}, {delta['ci_high']:+.6f}]"
        )
        model_rows.append(
            f"| {row['model_id']} | {row['events']} | {row['target_dates']} | "
            f"{row['mse']:.6f} | {row['mae']:.6f} | {row['directional_accuracy']:.2%} | {delta_text} |"
        )
    taker = result["taker"]
    market_taker = result["market_taker"]
    maker = result["maker_conditional"]
    report = f"""# Forecast Repricing full-ladder reconstructed evaluation v1

significance={'PASS' if selected_delta['ci_high'] < 0 else 'FAIL'}_weather_incremental_vector_markout; execution_taker_CI={'PASS' if taker['ci_low'] > 0 else 'FAIL'}

baseline=`{result['selected_baseline_model_id']}`; selected incremental weather delta={selected_delta['mse_delta_challenger_minus_market']:+.6f} CI=[{selected_delta['ci_low']:+.6f}, {selected_delta['ci_high']:+.6f}]

forward=NA；secondary chronological holdout 仍来自 reconstructed archive，不是 post-freeze collector-exact forward

production: live_action=none; orders_changed=0

## 固定分母与模型

- {denominator['forecast_events']:,} 个 `city × target_date × forecast-event`，{denominator['rungs']:,} 个完整 ladder rungs；selected={denominator['selected_rows']:,}、unselected={denominator['unselected_rows']:,}。
- D-1/D-2 同时保留；horizon={result['selected_horizon']}m、Ridge、threshold=`predicted taker net > 0` 只在 development expanding OOF 固定。
- 四时钟未混写：forecast issue、provider first-seen、collector first-seen、book snapshot、execution replay 分列；legacy archive 的 provider first-seen 明确缺失。
- settlement probability head 不在此模型中；此处只预测 future bid repricing。

## Secondary holdout 同分母模型比较

| model | events | dates | vector MSE | vector MAE | direction | MSE delta vs market [95% CI] |
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(model_rows)}

## 执行表达

| expression | signals | dates | ROI | 95% CI | evidence |
|---|---:|---:|---:|---:|---|
| taker | {taker['signals']} | {taker['target_dates']} | {taker['roi']:.2%} | [{taker['ci_low']:.2%}, {taker['ci_high']:.2%}] | entry ask + real depth + 双边 Weather fee + future bid |
| market-level taker baseline | {market_taker['signals']} | {market_taker['target_dates']} | {market_taker['roi']:.2%} | [{market_taker['ci_low']:.2%}, {market_taker['ci_high']:.2%}] | 同分母独立 policy |
| matching `{result['selected_baseline_model_id']}` taker baseline | {result['matching_baseline_taker']['signals']} | {result['matching_baseline_taker']['target_dates']} | {result['matching_baseline_taker']['roi']:.2%} | [{result['matching_baseline_taker']['ci_low']:.2%}, {result['matching_baseline_taker']['ci_high']:.2%}] | 用来隔离 forecast 对 microstructure 的增量 |
| maker conditional quote | {maker['signals']} | {maker['target_dates']} | {maker['roi']:.2%} | [{maker['ci_low']:.2%}, {maker['ci_high']:.2%}] | **不含 fill probability；不能当可实现 ROI** |

maker actual fills=0；future touch 没有当 fill。queue、partial fill、expire、adverse selection 与 maker-then-taker fallback 都是当前 blocker。WS dynamics 也因 D-2/D-1 policy-valid capture=0 而未进入正式 model arm。

## 判定

`{result['status']}`。历史 archive 只能给 reconstructed point estimate/secondary holdout；没有 post-freeze settled collector-exact dates，因此不构成 frozen forward，也不授权 shadow deployment 或真实订单。
"""
    (output_dir / "report.md").write_text(report, encoding="utf-8")


def write_outputs(result: dict[str, Any], input_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    if result["status"] == "blocked_insufficient_target_dates":
        summary = {
            **result,
            "generated_at_utc": generated_at,
            "input_path": str(input_path),
            "input_sha256": _sha256(input_path),
            "code_revision": _git_sha(),
            "production": {"live_action": "none", "orders_changed": 0},
        }
        (output_dir / "summary.json").write_text(
            json.dumps(_json_safe(summary), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (output_dir / "report.md").write_text(
            "# LMVM repricing challenger\n\n"
            "significance=BLOCKED\n\ncalibration=BLOCKED\n\n"
            "baseline=NOT_RUN\n\nforward=NOT_RUN\n\n"
            "production: live_action=none; orders_changed=0\n\n"
            f"只有 {result['available_target_dates']} 个 target dates / {result['available_rows']} rows，"
            f"低于合同要求的 {result['required_target_dates']} 个日期。\n",
            encoding="utf-8",
        )
        return

    for key, name in (
        ("oof_predictions", "development_oof_predictions.csv"),
        ("tournament", "development_tournament.csv"),
        ("holdout_predictions", "holdout_predictions.csv"),
        ("selected_holdout", "selected_holdout_signals.csv"),
    ):
        result[key].to_csv(output_dir / name, index=False)
    summary = {
        "schema_version": "lmvm_repricing_challenger_v1",
        "generated_at_utc": generated_at,
        "input_path": str(input_path),
        "input_sha256": _sha256(input_path),
        "code_revision": _git_sha(),
        "status": result["status"],
        "denominator_scope": "forecast_innovation_argmax; D-1; PIT snapshot candidate; first threshold cross per city-target_date",
        "development_dates": result["development_dates"],
        "holdout_dates": result["holdout_dates"],
        "model_spec": result["model_spec"],
        "selected": result["selected"],
        "holdout": result["holdout"],
        "baseline": result["baseline"],
        "calibration": result["calibration"],
        "multiple_testing_arms": result["multiple_testing_arms"],
        "freeze_rule": "development OOF ROI>0 and secondary holdout ROI>0, holdout beats mechanical baseline, both positive-date rates>=50%, with >=20 signals across >=6 holdout dates; fresh forward still required",
        "production": {"live_action": "none", "orders_changed": 0},
    }
    (output_dir / "summary.json").write_text(
        json.dumps(_json_safe(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    candidate = {
        "schema_version": "lmvm_repricing_candidate_model_v1",
        "status": result["status"],
        "model_spec": result["model_spec"],
        "horizon_min": int(result["selected"]["horizon_min"]),
        "entry_threshold_predicted_net_markout": float(result["selected"]["threshold"]),
        "entry_policy": "first threshold cross per city-target_date",
        "feature_clock": "decision snapshot only",
        "execution_assumption": (
            "taker ask entry; future executable bid exit; both Weather fees"
            if result["model_spec"]["execution_style"] == "taker"
            else "conditional maker fill at entry best bid; future executable bid exit with Weather taker fee; maker fill probability not assumed"
        ),
        "development_end": result["development_dates"][-1],
        "holdout_start": result["holdout_dates"][0],
        "input_sha256": summary["input_sha256"],
        "code_revision": summary["code_revision"],
    }
    (output_dir / "candidate_model.json").write_text(
        json.dumps(_json_safe(candidate), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if result["status"] in {"freeze_candidate", "maker_probe_candidate"}:
        stem = "frozen_model" if result["status"] == "freeze_candidate" else "maker_probe_model"
        model_path = output_dir / f"{stem}.joblib"
        joblib.dump(result["frozen_model"], model_path)
        frozen_payload = {
            **candidate,
            "model_path": model_path.name,
            "model_sha256": _sha256(model_path),
            "fit_scope": "all recovered historical rows after secondary-holdout gate; for fresh forward only",
        }
        (output_dir / f"{stem}.json").write_text(
            json.dumps(_json_safe(frozen_payload), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    holdout = result["holdout"]
    baseline = result["baseline"]
    selected = result["selected"]
    calibration = result["calibration"]
    report = f"""# LMVM D-1 repricing challenger v1

significance={'PASS_POINT_ONLY' if result['status'] in {'freeze_candidate', 'maker_probe_candidate'} else 'FAIL'}; holdout ROI={holdout['roi']:.2%}, target-date bootstrap 95% CI=[{holdout['ci_low']:.2%}, {holdout['ci_high']:.2%}]

calibration=holdout predicted mean {calibration['mean_predicted_net_markout']:+.4f} vs actual {calibration['mean_actual_net_markout']:+.4f}; MAE={calibration['mae']:.4f}

baseline=matching `{result['model_spec']['execution_style']}` first mechanical innovation candidate per city-day ROI {baseline['roi']:.2%}; challenger delta {holdout['roi'] - baseline['roi']:+.2%}

forward=secondary chronological holdout {result['holdout_dates'][0]}..{result['holdout_dates'][-1]}; fresh frozen forward still required

production: live_action=none; orders_changed=0

## 固定设计

- 分母：D-1 `forecast_innovation_argmax` 更新事件；execution style=`{result['model_spec']['execution_style']}`，{int(selected['horizon_min'])}m 首个容差内 bid 退出。
- 训练：{len(result['development_dates'])} 个 development target dates；严格 expanding-date OOF。
- 盲开：最后 {len(result['holdout_dates'])} 个日期只在模型、horizon 和阈值冻结后评分。
- 赛马：{result['multiple_testing_arms']} 个 model×horizon arms；阈值只在 development OOF 选择。
- 防重叠：每个 city×target_date 只取第一次预测穿越阈值的信号。

## 选中版本

`{selected['model_id']}`，horizon={int(selected['horizon_min'])}m，predicted-net threshold={selected['threshold']:+.4f}。

| period | signals | dates | cost | PnL | ROI | positive dates | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| development OOF | {int(selected['signals'])} | {int(selected['target_dates'])} | ${selected['cost_usd']:.2f} | ${selected['pnl_usd']:+.2f} | {selected['roi']:.2%} | {selected['positive_date_rate']:.1%} | [{selected['ci_low']:.2%}, {selected['ci_high']:.2%}] |
| secondary holdout | {holdout['signals']} | {holdout['target_dates']} | ${holdout['cost_usd']:.2f} | ${holdout['pnl_usd']:+.2f} | {holdout['roi']:.2%} | {holdout['positive_date_rate']:.1%} | [{holdout['ci_low']:.2%}, {holdout['ci_high']:.2%}] |
| mechanical first-update baseline | {baseline['signals']} | {baseline['target_dates']} | ${baseline['cost_usd']:.2f} | ${baseline['pnl_usd']:+.2f} | {baseline['roi']:.2%} | {baseline['positive_date_rate']:.1%} | [{baseline['ci_low']:.2%}, {baseline['ci_high']:.2%}] |

## 判定

`{result['status']}`。`freeze_candidate` 只表示 taker 规则值得锁定后积累 clean forward；`maker_probe_candidate` 只表示 conditional quote return 值得冻结并采集真实 queue/fill，绝不等于已实现 ROI。两者都不是 confirmed alpha，也不允许直接 live。
"""
    (output_dir / "report.md").write_text(report, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--candidate-csv", type=Path)
    source.add_argument(
        "--full-ladder-event-csv",
        type=Path,
        help="evaluate every D-2/D-1 ladder rung on a fixed forecast-event denominator",
    )
    source.add_argument(
        "--mass-transport-db",
        type=Path,
        help="run the canonical PIT ladder-mass-transport M0-M3 evaluation",
    )
    source.add_argument(
        "--mass-transport-postprocess-dir",
        type=Path,
        help="write reporting-only tables from an already frozen validation run",
    )
    source.add_argument(
        "--mass-transport-forward-parent",
        type=Path,
        help="score 2026-07-29..08-08 from an existing frozen mass-transport artifact",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--min-train-dates", type=int, default=15)
    parser.add_argument("--holdout-fraction", type=float, default=0.20)
    parser.add_argument("--draws", type=int, default=2_000)
    parser.add_argument("--mass-transport-identity-db", type=Path, default=Path("runtime/weather.db"))
    parser.add_argument(
        "--mass-transport-history-snapshot-dir",
        type=Path,
        default=load_production_spec().resolved_historical_paper_snapshot_root(),
        help=(
            "override the production-contract historical PIT snapshot root; "
            "normally omit this option"
        ),
    )
    parser.add_argument("--mass-transport-history-workers", type=int, default=8)
    parser.add_argument(
        "--mass-transport-history-cache",
        type=Path,
        help="optional run-scoped local cache for streaming archived snapshots",
    )
    parser.add_argument(
        "--mass-transport-resume-fixed-panel",
        action="store_true",
        help="reuse the immutable fixed_panel artifact and rerun OOF/validation",
    )
    parser.add_argument(
        "--score-model-dir",
        type=Path,
        help="score candidate rows with an existing frozen/probe artifact instead of training",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.mass_transport_forward_parent is not None:
        if args.score_model_dir is not None:
            raise ValueError("--score-model-dir is not supported with --mass-transport-forward-parent")
        from weather_model_evaluation.ladder_mass_transport import run_recent_forward

        result=run_recent_forward(
            args.mass_transport_identity_db,
            args.mass_transport_forward_parent,
            args.output_dir,
            draws=args.draws,
        )
        print(json.dumps({"status":result["status"],"output_dir":str(args.output_dir)},ensure_ascii=False))
        return 0
    if args.mass_transport_postprocess_dir is not None:
        from weather_model_evaluation.ladder_mass_transport import (
            finalize_saved_validation,
            postprocess_artifact,
        )

        if (args.mass_transport_postprocess_dir / "summary.json").exists():
            result = postprocess_artifact(args.mass_transport_postprocess_dir, draws=args.draws, db=args.mass_transport_identity_db)
        else:
            result = finalize_saved_validation(
                args.mass_transport_postprocess_dir,
                args.mass_transport_identity_db,
                args.mass_transport_history_cache,
                draws=args.draws,
            )
        print(json.dumps({"status": result["status"], "output_dir": str(args.mass_transport_postprocess_dir)}, ensure_ascii=False))
        return 0
    if args.mass_transport_db is not None:
        if args.score_model_dir is not None:
            raise ValueError("--score-model-dir is not supported with --mass-transport-db")
        from weather_model_evaluation.ladder_mass_transport import run

        result = run(
            args.mass_transport_db,
            args.mass_transport_history_snapshot_dir,
            args.output_dir,
            draws=args.draws,
            history_workers=args.mass_transport_history_workers,
            history_cache_path=args.mass_transport_history_cache,
            resume_fixed_panel=args.mass_transport_resume_fixed_panel,
        )
        print(json.dumps({"status": result["status"], "output_dir": str(args.output_dir)}, ensure_ascii=False))
        return 0
    input_path = args.full_ladder_event_csv or args.candidate_csv
    assert input_path is not None
    rows = pd.read_csv(input_path, low_memory=False)
    if args.full_ladder_event_csv is not None:
        if args.score_model_dir is not None:
            raise ValueError("--score-model-dir is only supported with --candidate-csv")
        result = run_full_ladder_tournament(
            rows,
            min_train_dates=args.min_train_dates,
            holdout_fraction=args.holdout_fraction,
            draws=args.draws,
        )
        write_full_ladder_outputs(result, input_path, args.output_dir)
        print(
            json.dumps(
                {"status": result["status"], "output_dir": str(args.output_dir)},
                ensure_ascii=False,
            )
        )
        return 0
    if args.score_model_dir is not None:
        json_candidates = (
            args.score_model_dir / "maker_probe_model.json",
            args.score_model_dir / "frozen_model.json",
        )
        meta_path = next((path for path in json_candidates if path.exists()), None)
        if meta_path is None:
            raise FileNotFoundError(f"no model metadata under {args.score_model_dir}")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        model_path = args.score_model_dir / str(meta["model_path"])
        if _sha256(model_path) != str(meta["model_sha256"]):
            raise ValueError("model artifact SHA-256 mismatch")
        spec = ModelSpec(**meta["model_spec"])
        scored, selected = score_frozen_rows(
            rows,
            joblib.load(model_path),
            spec,
            float(meta["entry_threshold_predicted_net_markout"]),
        )
        args.output_dir.mkdir(parents=True, exist_ok=True)
        scored.to_csv(args.output_dir / "forward_scores.csv", index=False)
        selected.to_csv(args.output_dir / "forward_candidates.csv", index=False)
        payload = {
            "schema_version": "lmvm_repricing_forward_score_v1",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "model_meta": str(meta_path),
            "model_sha256": meta["model_sha256"],
            "input_path": str(input_path),
            "input_sha256": _sha256(input_path),
            "score_rows": len(scored),
            "threshold_cross_rows": int(scored["threshold_cross"].sum()),
            "first_cross_candidates": len(selected),
            "execution_style": spec.execution_style,
            "production": {"live_action": "none", "orders_changed": 0},
        }
        (args.output_dir / "forward_summary.json").write_text(
            json.dumps(_json_safe(payload), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    result = run_tournament(
        rows,
        min_train_dates=args.min_train_dates,
        holdout_fraction=args.holdout_fraction,
        draws=args.draws,
    )
    write_outputs(result, input_path, args.output_dir)
    print(json.dumps({"status": result["status"], "output_dir": str(args.output_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
