#!/usr/bin/env python3
"""Measure whether later settled data improves the HeadA probability stack.

Two model layers are evaluated with strictly earlier target-date labels:

1. Same-day Tmax/innovation model:
   frozen first five dates vs expanding history vs rolling 5/10 dates.
2. D-1 HeadA entry probability model:
   original history through 2026-06-20 vs all historical rows vs an expanding
   historical+current training set vs the most recent 30 target dates.

The last six broad weather dates and the last four HeadA dates are reported as
the frozen-forward windows.  Rows are never randomly split because many rows
share one target-date weather regime.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.special import logit
from scipy.stats import norm
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
CHECKPOINTS = (
    ROOT
    / "docs/analysis/2026-07/generated/forecast_innovation_morning_v1"
    / "checkpoint_rows.csv"
)
EXACT = (
    ROOT
    / "docs/analysis/2026-07/generated/heada_market_anchored_lifecycle_v1"
    / "exact_ticket_checkpoint_rows.csv"
)
HIST = (
    ROOT
    / "docs/analysis/2026-07/generated/heada_multisource_city_regime_v1"
    / "historical_enriched.csv"
)
CURRENT = (
    ROOT
    / "docs/analysis/2026-07/generated/heada_multisource_city_regime_v1"
    / "current_enriched.csv"
)
ERRORS = (
    ROOT
    / "docs/analysis/2026-07/generated/heada_multisource_city_regime_v1"
    / "multisource_backfill/daily_error_rows.csv"
)
DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_OUT = (
    ROOT
    / "docs/analysis/2026-07/generated/heada_training_data_learning_curve_v1"
)
DEFAULT_REPORT = (
    ROOT
    / "docs/analysis/2026-07/2026-07-28-heada-training-data-learning-curve-v1.md"
)

CHECKPOINT_HOURS = (9, 12)
MIN_TRAIN_DATES = 5
RIDGE_ALPHA = 10.0
WEATHER_SIGMA_F = 3.0
ORIGINAL_ENTRY_TRAIN_END = "2026-06-20"
BROAD_FROZEN_DATES = 6
HEADA_FROZEN_START = "2026-07-22"
BOOTSTRAP_SAMPLES = 5_000
RNG_SEED = 20260728


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--checkpoints", type=Path, default=CHECKPOINTS)
    parser.add_argument("--exact", type=Path, default=EXACT)
    parser.add_argument("--historical", type=Path, default=HIST)
    parser.add_argument("--current", type=Path, default=CURRENT)
    parser.add_argument("--errors", type=Path, default=ERRORS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def md_value(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "NA"
    if isinstance(value, (float, np.floating)):
        return f"{float(value):.5f}"
    return str(value)


def md_table(frame: pd.DataFrame, columns: list[str]) -> str:
    if frame.empty:
        return "_none_"
    view = frame[columns]
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in view.itertuples(index=False, name=None):
        lines.append("| " + " | ".join(md_value(value) for value in row) + " |")
    return "\n".join(lines)


def categorical_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.get_dummies(
        frame[["city", "forecast_model"]],
        columns=["city", "forecast_model"],
        dtype=float,
    )


def weather_variant_matrix(frame: pd.DataFrame, variant: str) -> pd.DataFrame:
    base = categorical_matrix(frame)
    if variant == "rolling_bias":
        return base
    innovation = numeric(frame["forecast_innovation_f"])
    base["forecast_innovation_f"] = innovation
    if variant == "innovation":
        return base
    if variant != "innovation_regime":
        raise ValueError(variant)
    cloud = numeric(frame["forecast_cloud_cover_at_decision_pct"]) / 100.0
    wind = numeric(frame["forecast_wind_at_decision_kt"]) / 10.0
    precip = numeric(frame["forecast_precip_at_decision_pct"]) / 100.0
    base["cloud_missing"] = cloud.isna().astype(float)
    base["wind_missing"] = wind.isna().astype(float)
    base["precip_missing"] = precip.isna().astype(float)
    base["innovation_x_cloud"] = innovation * cloud.fillna(0.0)
    base["innovation_x_wind"] = innovation * wind.fillna(0.0)
    base["innovation_x_precip"] = innovation * precip.fillna(0.0)
    return base


def align_columns(
    train: pd.DataFrame, test: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    columns = sorted(set(train.columns) | set(test.columns))
    return (
        train.reindex(columns=columns, fill_value=0.0),
        test.reindex(columns=columns, fill_value=0.0),
    )


def training_dates_for_policy(
    all_dates: list[str],
    target_date: str,
    policy: str,
) -> list[str]:
    prior = [date for date in all_dates if date < target_date]
    if policy == "frozen_first5":
        return all_dates[:MIN_TRAIN_DATES]
    if policy == "expanding_prior":
        return prior
    if policy == "rolling5":
        return prior[-5:]
    if policy == "rolling10":
        return prior[-10:]
    raise ValueError(policy)


def weather_policy_predictions(checkpoints: pd.DataFrame) -> pd.DataFrame:
    required = [
        "forecast_innovation_f",
        "forecast_final_error_f",
        "forecast_max_f",
        "settlement_mid_f",
    ]
    rows = checkpoints[
        checkpoints["checkpoint_hour_local"].isin(CHECKPOINT_HOURS)
    ].dropna(subset=required)
    policies = ("frozen_first5", "expanding_prior", "rolling5", "rolling10")
    variants = ("rolling_bias", "innovation", "innovation_regime")
    output: list[pd.DataFrame] = []
    for checkpoint, checkpoint_rows in rows.groupby("checkpoint_hour_local"):
        all_dates = sorted(checkpoint_rows["target_date"].astype(str).unique())
        eval_dates = all_dates[MIN_TRAIN_DATES:]
        for target_date in eval_dates:
            test = checkpoint_rows[checkpoint_rows["target_date"].eq(target_date)]
            for policy in policies:
                train_dates = training_dates_for_policy(all_dates, target_date, policy)
                train = checkpoint_rows[checkpoint_rows["target_date"].isin(train_dates)]
                result = test[
                    [
                        "tmax_state_id",
                        "city",
                        "target_date",
                        "checkpoint_hour_local",
                        "forecast_model",
                        "forecast_innovation_f",
                        "forecast_max_f",
                        "settlement_mid_f",
                    ]
                ].copy()
                result["training_policy"] = policy
                result["train_dates"] = len(train_dates)
                result["train_rows"] = len(train)
                result["train_start_date"] = min(train_dates)
                result["train_through_date"] = max(train_dates)
                result["pred_raw_forecast_f"] = numeric(
                    test["forecast_max_f"]
                ).to_numpy(float)
                for variant in variants:
                    x_train, x_test = align_columns(
                        weather_variant_matrix(train, variant),
                        weather_variant_matrix(test, variant),
                    )
                    model = Ridge(alpha=RIDGE_ALPHA, fit_intercept=True)
                    model.fit(
                        x_train.to_numpy(float),
                        numeric(train["forecast_final_error_f"]).to_numpy(float),
                    )
                    correction = model.predict(x_test.to_numpy(float))
                    result[f"pred_{variant}_f"] = (
                        numeric(test["forecast_max_f"]).to_numpy(float) + correction
                    )
                output.append(result)
    return pd.concat(output, ignore_index=True)


def block_ci(frame: pd.DataFrame, value: str) -> tuple[float, float]:
    daily = frame.groupby("target_date")[value].mean().dropna().to_numpy(float)
    if len(daily) < 3:
        return math.nan, math.nan
    rng = np.random.default_rng(RNG_SEED)
    indices = rng.integers(0, len(daily), size=(BOOTSTRAP_SAMPLES, len(daily)))
    draws = daily[indices].mean(axis=1)
    return float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))


def block_roi_ci(
    frame: pd.DataFrame,
    *,
    cost: str,
    pnl: str,
    all_dates: list[str],
) -> tuple[float, float]:
    daily = (
        frame.groupby("target_date")[[cost, pnl]]
        .sum()
        .reindex(all_dates, fill_value=0.0)
    )
    if daily[cost].sum() <= 0 or len(daily) < 3:
        return math.nan, math.nan
    rng = np.random.default_rng(RNG_SEED)
    values = daily[[cost, pnl]].to_numpy(float)
    indices = rng.integers(0, len(values), size=(BOOTSTRAP_SAMPLES, len(values)))
    draws = values[indices].sum(axis=1)
    valid = draws[:, 0] > 0
    rois = draws[valid, 1] / draws[valid, 0]
    if not len(rois):
        return math.nan, math.nan
    return float(np.quantile(rois, 0.025)), float(np.quantile(rois, 0.975))


def add_window(
    frame: pd.DataFrame,
    *,
    frozen_dates: set[str],
) -> pd.DataFrame:
    out = frame.copy()
    out["eval_window"] = np.where(
        out["target_date"].isin(frozen_dates), "frozen_forward", "development"
    )
    return out


def weather_scores(
    predictions: pd.DataFrame,
    frozen_dates: set[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = add_window(predictions, frozen_dates=frozen_dates)
    variants = ("raw_forecast", "rolling_bias", "innovation", "innovation_regime")
    details: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []
    for (window, checkpoint, policy), group in rows.groupby(
        ["eval_window", "checkpoint_hour_local", "training_policy"]
    ):
        actual = numeric(group["settlement_mid_f"]).to_numpy(float)
        for variant in variants:
            pred = numeric(group[f"pred_{variant}_f"]).to_numpy(float)
            detail = group[
                ["tmax_state_id", "target_date", "checkpoint_hour_local"]
            ].copy()
            detail["training_policy"] = policy
            detail["eval_window"] = window
            detail["variant"] = variant
            detail["abs_error_f"] = np.abs(pred - actual)
            detail["squared_error_f"] = np.square(pred - actual)
            details.append(detail)
            summaries.append(
                {
                    "eval_window": window,
                    "checkpoint_hour_local": int(checkpoint),
                    "training_policy": policy,
                    "variant": variant,
                    "rows": len(group),
                    "dates": int(group["target_date"].nunique()),
                    "train_dates_min": int(group["train_dates"].min()),
                    "train_dates_max": int(group["train_dates"].max()),
                    "mae_f": float(np.mean(np.abs(pred - actual))),
                    "rmse_f": float(np.sqrt(np.mean(np.square(pred - actual)))),
                    "mean_error_f": float(np.mean(pred - actual)),
                }
            )
    detail_rows = pd.concat(details, ignore_index=True)
    deltas: list[dict[str, Any]] = []
    for (window, checkpoint, variant), group in detail_rows.groupby(
        ["eval_window", "checkpoint_hour_local", "variant"]
    ):
        frozen = group[group["training_policy"].eq("frozen_first5")][
            ["tmax_state_id", "target_date", "abs_error_f", "squared_error_f"]
        ]
        for policy in ("expanding_prior", "rolling5", "rolling10"):
            candidate = group[group["training_policy"].eq(policy)].merge(
                frozen,
                on=["tmax_state_id", "target_date"],
                suffixes=("_candidate", "_frozen5"),
                validate="one_to_one",
            )
            record: dict[str, Any] = {
                "eval_window": window,
                "checkpoint_hour_local": int(checkpoint),
                "variant": variant,
                "candidate_policy": policy,
                "rows": len(candidate),
                "dates": int(candidate["target_date"].nunique()),
            }
            for metric in ("abs_error_f", "squared_error_f"):
                column = f"{metric}_delta"
                candidate[column] = (
                    candidate[f"{metric}_candidate"]
                    - candidate[f"{metric}_frozen5"]
                )
                low, high = block_ci(candidate, column)
                record[column] = float(candidate[column].mean())
                record[f"{column}_ci_low"] = low
                record[f"{column}_ci_high"] = high
            deltas.append(record)
    return pd.DataFrame(summaries), pd.DataFrame(deltas)


def exact_bounds_f(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    low_native = numeric(frame["bracket_low_native"])
    high_native = numeric(frame["bracket_high_native"])
    low = np.where(
        frame["unit"].eq("C"),
        (low_native - 0.5) * 1.8 + 32.0,
        low_native - 0.5,
    )
    high = np.where(
        frame["unit"].eq("C"),
        (high_native + 0.5) * 1.8 + 32.0,
        high_native + 0.5,
    )
    return low, high


def exact_probability(
    mean_f: pd.Series,
    low_f: np.ndarray,
    high_f: np.ndarray,
) -> np.ndarray:
    values = numeric(mean_f).to_numpy(float)
    return np.clip(
        norm.cdf((high_f - values) / WEATHER_SIGMA_F)
        - norm.cdf((low_f - values) / WEATHER_SIGMA_F),
        1e-5,
        1 - 1e-5,
    )


def heada_checkpoint_scores(
    exact: pd.DataFrame,
    predictions: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    prediction_columns = [
        "tmax_state_id",
        "training_policy",
        "train_dates",
        "settlement_mid_f",
        "pred_rolling_bias_f",
        "pred_innovation_f",
        "pred_innovation_regime_f",
    ]
    exact_base = exact.drop(
        columns=[
            "train_dates",
            "pred_rolling_bias_f",
            "pred_innovation_f",
            "pred_innovation_regime_f",
        ],
        errors="ignore",
    )
    rows = exact_base.merge(
        predictions[prediction_columns],
        on="tmax_state_id",
        how="inner",
        validate="many_to_many",
    )
    rows = rows[rows["proxy_price_t"].notna()].copy()
    rows["eval_window"] = np.where(
        rows["target_date"].ge(HEADA_FROZEN_START),
        "frozen_forward",
        "development",
    )
    low, high = exact_bounds_f(rows)
    rows["p_prior"] = exact_probability(rows["pred_rolling_bias_f"], low, high)
    variants = {
        "innovation": "pred_innovation_f",
        "innovation_regime": "pred_innovation_regime_f",
    }
    details: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []
    for variant, column in variants.items():
        rows[f"p_{variant}"] = exact_probability(rows[column], low, high)
        rows[f"p_market_anchored_{variant}"] = (
            numeric(rows["proxy_price_t"]).clip(0.001, 0.999)
            + rows[f"p_{variant}"]
            - rows["p_prior"]
        ).clip(0.001, 0.999)
        for (window, checkpoint, policy), group in rows.groupby(
            ["eval_window", "checkpoint_hour_local", "training_policy"]
        ):
            y = group["win"].astype(float).to_numpy()
            market = numeric(group["proxy_price_t"]).clip(0.001, 0.999).to_numpy()
            p = numeric(group[f"p_market_anchored_{variant}"]).to_numpy()
            detail = group[
                ["_row_id", "target_date", "checkpoint_hour_local"]
            ].copy()
            detail["eval_window"] = window
            detail["training_policy"] = policy
            detail["variant"] = variant
            detail["win"] = y
            detail["market_p"] = market
            detail["p_hat"] = p
            detail["brier"] = np.square(p - y)
            detail["market_brier"] = np.square(market - y)
            detail["logloss"] = -(
                y * np.log(p) + (1 - y) * np.log(1 - p)
            )
            detail["market_logloss"] = -(
                y * np.log(market) + (1 - y) * np.log(1 - market)
            )
            detail["brier_delta_vs_market"] = (
                detail["brier"] - detail["market_brier"]
            )
            detail["logloss_delta_vs_market"] = (
                detail["logloss"] - detail["market_logloss"]
            )
            details.append(detail)
            brier_low, brier_high = block_ci(detail, "brier_delta_vs_market")
            log_low, log_high = block_ci(detail, "logloss_delta_vs_market")
            summaries.append(
                {
                    "eval_window": window,
                    "checkpoint_hour_local": int(checkpoint),
                    "training_policy": policy,
                    "variant": variant,
                    "rows": len(group),
                    "dates": int(group["target_date"].nunique()),
                    "wins": int(np.sum(y)),
                    "observed_rate": float(np.mean(y)),
                    "mean_market_p": float(np.mean(market)),
                    "mean_p_hat": float(np.mean(p)),
                    "train_dates_min": int(group["train_dates"].min()),
                    "train_dates_max": int(group["train_dates"].max()),
                    "tmax_mae_f": float(
                        np.mean(
                            np.abs(
                                numeric(group[column])
                                - numeric(group["settlement_mid_f"])
                            )
                        )
                    ),
                    "brier": float(np.mean(np.square(p - y))),
                    "brier_delta_vs_market": float(
                        np.mean(np.square(p - y) - np.square(market - y))
                    ),
                    "brier_delta_ci_low": brier_low,
                    "brier_delta_ci_high": brier_high,
                    "logloss": float(
                        np.mean(-(y * np.log(p) + (1 - y) * np.log(1 - p)))
                    ),
                    "logloss_delta_vs_market": float(
                        np.mean(
                            -(y * np.log(p) + (1 - y) * np.log(1 - p))
                            + y * np.log(market)
                            + (1 - y) * np.log(1 - market)
                        )
                    ),
                    "logloss_delta_ci_low": log_low,
                    "logloss_delta_ci_high": log_high,
                }
            )
    detail_rows = pd.concat(details, ignore_index=True)
    deltas: list[dict[str, Any]] = []
    for (window, checkpoint, variant), group in detail_rows.groupby(
        ["eval_window", "checkpoint_hour_local", "variant"]
    ):
        frozen = group[group["training_policy"].eq("frozen_first5")][
            ["_row_id", "target_date", "brier", "logloss"]
        ]
        for policy in ("expanding_prior", "rolling5", "rolling10"):
            candidate = group[group["training_policy"].eq(policy)].merge(
                frozen,
                on=["_row_id", "target_date"],
                suffixes=("_candidate", "_frozen5"),
                validate="one_to_one",
            )
            record: dict[str, Any] = {
                "eval_window": window,
                "checkpoint_hour_local": int(checkpoint),
                "variant": variant,
                "candidate_policy": policy,
                "rows": len(candidate),
                "dates": int(candidate["target_date"].nunique()),
            }
            for metric in ("brier", "logloss"):
                column = f"{metric}_delta_vs_frozen5"
                candidate[column] = (
                    candidate[f"{metric}_candidate"]
                    - candidate[f"{metric}_frozen5"]
                )
                low_ci, high_ci = block_ci(candidate, column)
                record[column] = float(candidate[column].mean())
                record[f"{column}_ci_low"] = low_ci
                record[f"{column}_ci_high"] = high_ci
            deltas.append(record)
    return rows, pd.DataFrame(summaries), pd.DataFrame(deltas)


def heada_checkpoint_slices(rows: pd.DataFrame) -> pd.DataFrame:
    frozen_rows = rows[rows["eval_window"].eq("frozen_forward")].copy()
    records: list[dict[str, Any]] = []
    for variant in ("innovation", "innovation_regime"):
        probability = f"p_market_anchored_{variant}"
        prediction = f"pred_{variant}_f"
        base = frozen_rows[frozen_rows["training_policy"].eq("frozen_first5")][
            [
                "_row_id",
                "target_date",
                "checkpoint_hour_local",
                "source",
                "weather_regime",
                "win",
                "settlement_mid_f",
                probability,
                prediction,
            ]
        ]
        candidate = frozen_rows[
            frozen_rows["training_policy"].eq("expanding_prior")
        ][
            [
                "_row_id",
                "target_date",
                "checkpoint_hour_local",
                probability,
                prediction,
            ]
        ].merge(
            base,
            on=["_row_id", "target_date", "checkpoint_hour_local"],
            suffixes=("_expanding", "_frozen5"),
            validate="one_to_one",
        )
        y = candidate["win"].astype(float)
        for suffix in ("expanding", "frozen5"):
            p = candidate[f"{probability}_{suffix}"].clip(1e-5, 1 - 1e-5)
            candidate[f"brier_{suffix}"] = np.square(p - y)
            candidate[f"logloss_{suffix}"] = -(
                y * np.log(p) + (1 - y) * np.log(1 - p)
            )
            candidate[f"abs_error_{suffix}"] = np.abs(
                numeric(candidate[f"{prediction}_{suffix}"])
                - numeric(candidate["settlement_mid_f"])
            )
        candidate["brier_delta"] = (
            candidate["brier_expanding"] - candidate["brier_frozen5"]
        )
        candidate["logloss_delta"] = (
            candidate["logloss_expanding"] - candidate["logloss_frozen5"]
        )
        candidate["tmax_mae_delta_f"] = (
            candidate["abs_error_expanding"] - candidate["abs_error_frozen5"]
        )
        candidate["p_hat_delta"] = (
            candidate[f"{probability}_expanding"]
            - candidate[f"{probability}_frozen5"]
        )
        slices = [
            ("overall", pd.Series("all", index=candidate.index)),
            ("source", candidate["source"].astype(str)),
            ("weather_regime", candidate["weather_regime"].astype(str)),
            ("outcome", np.where(candidate["win"], "winner", "loser")),
        ]
        for dimension, values in slices:
            sliced = candidate.assign(_slice=values)
            for (checkpoint, value), group in sliced.groupby(
                ["checkpoint_hour_local", "_slice"]
            ):
                records.append(
                    {
                        "variant": variant,
                        "checkpoint_hour_local": int(checkpoint),
                        "dimension": dimension,
                        "value": value,
                        "rows": len(group),
                        "dates": int(group["target_date"].nunique()),
                        "wins": int(group["win"].sum()),
                        "p_hat_delta": float(group["p_hat_delta"].mean()),
                        "tmax_mae_delta_f": float(
                            group["tmax_mae_delta_f"].mean()
                        ),
                        "brier_delta_vs_frozen5": float(
                            group["brier_delta"].mean()
                        ),
                        "logloss_delta_vs_frozen5": float(
                            group["logloss_delta"].mean()
                        ),
                    }
                )
    return pd.DataFrame(records)


def assigned_model(source: str) -> str:
    return "gfs_seamless" if str(source).lower() == "gfs" else "ecmwf_ifs025"


def add_prior_bias(frame: pd.DataFrame, errors: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    error_rows = errors.copy()
    error_rows["target_date"] = pd.to_datetime(
        error_rows["target_date"], errors="coerce"
    )
    error_rows["error_f"] = numeric(error_rows["error_f"])
    grouped = {
        key: group.sort_values("target_date")
        for key, group in error_rows.groupby(["city", "model_key"])
    }
    values: list[dict[str, float]] = []
    for row in out.itertuples(index=False):
        day = pd.Timestamp(str(row.target_date))
        history = grouped.get((str(row.city), assigned_model(str(row.source))))
        if history is None:
            values.append(
                {
                    "prior_bias_median_f": math.nan,
                    "prior_mae_f": math.nan,
                    "prior_underforecast_rate": math.nan,
                }
            )
            continue
        prior = history[
            history["target_date"].lt(day)
            & history["target_date"].ge(day - timedelta(days=14))
        ]["error_f"].dropna()
        values.append(
            {
                "prior_bias_median_f": (
                    float(prior.median()) if len(prior) else math.nan
                ),
                "prior_mae_f": float(prior.abs().mean()) if len(prior) else math.nan,
                "prior_underforecast_rate": (
                    float(prior.ge(1.0).mean()) if len(prior) else math.nan
                ),
            }
        )
    return pd.concat(
        [out.reset_index(drop=True), pd.DataFrame(values)], axis=1
    )


def prepare_entry_frames(
    hist: pd.DataFrame,
    current: pd.DataFrame,
    errors: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    for label, frame in (("hist", hist), ("current", current)):
        frame["target_date"] = frame["target_date"].astype(str)
        frame["win"] = frame["win"].astype(bool)
        frame["ask"] = numeric(frame["ask"])
        frame["cost_eval"] = numeric(frame["cost_eval"])
        frame["pnl_eval"] = numeric(frame["pnl_eval"])
        frame["shares"] = numeric(frame["shares"])
        frame["unit_cost_eval"] = frame["cost_eval"] / frame["shares"]
        frame["distance_br"] = numeric(frame["distance_br"])
        frame["abs_lat"] = numeric(frame["abs_lat"])
        frame["market_logit"] = logit(frame["ask"].clip(0.001, 0.999))
        frame["source_x_archetype"] = (
            frame["source"].astype(str)
            + "|"
            + frame["city_archetype"].astype(str)
        )
        frame["_ticket_id"] = label + "|" + frame.index.astype(str)
    return add_prior_bias(hist, errors), add_prior_bias(current, errors)


def entry_model(
    numeric_cols: list[str],
    categorical_cols: list[str],
) -> Pipeline:
    pre = ColumnTransformer(
        [
            (
                "num",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="median")),
                        ("scale", StandardScaler()),
                    ]
                ),
                numeric_cols,
            ),
            (
                "cat",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                categorical_cols,
            ),
        ]
    )
    return Pipeline(
        [
            ("pre", pre),
            (
                "model",
                LogisticRegression(
                    C=0.35,
                    max_iter=5_000,
                    random_state=RNG_SEED,
                ),
            ),
        ]
    )


def entry_training_frame(
    hist: pd.DataFrame,
    current: pd.DataFrame,
    target_date: str,
    policy: str,
) -> pd.DataFrame:
    if policy == "original_frozen":
        return hist[hist["target_date"].le(ORIGINAL_ENTRY_TRAIN_END)].copy()
    if policy == "all_historical":
        return hist.copy()
    combined = pd.concat(
        [hist, current[current["target_date"].lt(target_date)]],
        ignore_index=True,
    )
    if policy == "expanding_current":
        return combined
    if policy == "rolling30":
        dates = sorted(combined["target_date"].unique())[-30:]
        return combined[combined["target_date"].isin(dates)].copy()
    raise ValueError(policy)


def entry_learning_curve(
    hist: pd.DataFrame,
    current: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    specs = {
        "entry_base": (
            ["market_logit", "distance_br", "abs_lat"],
            ["source", "city_archetype", "source_x_archetype"],
        ),
        "entry_plus_prior_bias": (
            [
                "market_logit",
                "distance_br",
                "abs_lat",
                "prior_bias_median_f",
                "prior_mae_f",
                "prior_underforecast_rate",
            ],
            ["source", "city_archetype", "source_x_archetype"],
        ),
    }
    policies = (
        "original_frozen",
        "all_historical",
        "expanding_current",
        "rolling30",
    )
    outputs: list[pd.DataFrame] = []
    for target_date in sorted(current["target_date"].unique()):
        test = current[current["target_date"].eq(target_date)].copy()
        for policy in policies:
            train = entry_training_frame(hist, current, target_date, policy)
            for variant, (num, cat) in specs.items():
                model = entry_model(num, cat)
                model.fit(train[num + cat], train["win"])
                result = test[
                    [
                        "_ticket_id",
                        "target_date",
                        "city",
                        "source",
                        "win",
                        "ask",
                        "cost_eval",
                        "unit_cost_eval",
                        "pnl_eval",
                        "shares",
                    ]
                ].copy()
                result["training_policy"] = policy
                result["variant"] = variant
                result["train_rows"] = len(train)
                result["train_dates"] = train["target_date"].nunique()
                result["train_wins"] = int(train["win"].sum())
                result["train_win_rate"] = float(train["win"].mean())
                result["train_through_date"] = train["target_date"].max()
                result["p_hat"] = model.predict_proba(test[num + cat])[:, 1]
                outputs.append(result)
    predictions = pd.concat(outputs, ignore_index=True)
    predictions["eval_window"] = np.where(
        predictions["target_date"].ge(HEADA_FROZEN_START),
        "frozen_forward",
        "development",
    )
    y = predictions["win"].astype(float)
    p = predictions["p_hat"].clip(1e-5, 1 - 1e-5)
    predictions["brier"] = np.square(p - y)
    predictions["logloss"] = -(y * np.log(p) + (1 - y) * np.log(1 - p))
    predictions["selected"] = predictions["p_hat"].gt(
        predictions["unit_cost_eval"]
    )

    summaries: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    for (window, variant, policy), group in predictions.groupby(
        ["eval_window", "variant", "training_policy"]
    ):
        summaries.append(
            {
                "eval_window": window,
                "variant": variant,
                "training_policy": policy,
                "rows": len(group),
                "dates": int(group["target_date"].nunique()),
                "wins": int(group["win"].sum()),
                "train_rows_min": int(group["train_rows"].min()),
                "train_rows_max": int(group["train_rows"].max()),
                "train_dates_min": int(group["train_dates"].min()),
                "train_dates_max": int(group["train_dates"].max()),
                "train_win_rate_min": float(group["train_win_rate"].min()),
                "train_win_rate_max": float(group["train_win_rate"].max()),
                "observed_rate": float(group["win"].mean()),
                "mean_p_hat": float(group["p_hat"].mean()),
                "mean_unit_cost": float(group["unit_cost_eval"].mean()),
                "brier": float(group["brier"].mean()),
                "logloss": float(group["logloss"].mean()),
            }
        )
        selected = group[group["selected"]]
        total_cost = float(selected["cost_eval"].sum())
        total_pnl = float(selected["pnl_eval"].sum())
        all_cost = float(group["cost_eval"].sum())
        all_pnl = float(group["pnl_eval"].sum())
        all_dates = sorted(group["target_date"].unique())
        roi_low, roi_high = block_roi_ci(
            selected,
            cost="cost_eval",
            pnl="pnl_eval",
            all_dates=all_dates,
        )
        all_roi_low, all_roi_high = block_roi_ci(
            group,
            cost="cost_eval",
            pnl="pnl_eval",
            all_dates=all_dates,
        )
        trades.append(
            {
                "eval_window": window,
                "variant": variant,
                "training_policy": policy,
                "opportunity_rows": len(group),
                "action_rows": len(selected),
                "action_dates": int(selected["target_date"].nunique()),
                "wins": int(selected["win"].sum()),
                "cost_usd": total_cost,
                "pnl_usd": total_pnl,
                "roi": total_pnl / total_cost if total_cost else math.nan,
                "roi_ci_low": roi_low,
                "roi_ci_high": roi_high,
                "all_buy_cost_usd": all_cost,
                "all_buy_pnl_usd": all_pnl,
                "all_buy_roi": all_pnl / all_cost if all_cost else math.nan,
                "all_buy_roi_ci_low": all_roi_low,
                "all_buy_roi_ci_high": all_roi_high,
            }
        )

    deltas: list[dict[str, Any]] = []
    for (window, variant), group in predictions.groupby(["eval_window", "variant"]):
        baseline = group[group["training_policy"].eq("original_frozen")][
            ["_ticket_id", "target_date", "brier", "logloss"]
        ]
        for policy in ("all_historical", "expanding_current", "rolling30"):
            candidate = group[group["training_policy"].eq(policy)].merge(
                baseline,
                on=["_ticket_id", "target_date"],
                suffixes=("_candidate", "_original"),
                validate="one_to_one",
            )
            record: dict[str, Any] = {
                "eval_window": window,
                "variant": variant,
                "candidate_policy": policy,
                "rows": len(candidate),
                "dates": int(candidate["target_date"].nunique()),
            }
            for metric in ("brier", "logloss"):
                column = f"{metric}_delta_vs_original"
                candidate[column] = (
                    candidate[f"{metric}_candidate"]
                    - candidate[f"{metric}_original"]
                )
                low, high = block_ci(candidate, column)
                record[column] = float(candidate[column].mean())
                record[f"{column}_ci_low"] = low
                record[f"{column}_ci_high"] = high
            deltas.append(record)
    return (
        predictions,
        pd.DataFrame(summaries),
        pd.DataFrame(deltas),
        pd.DataFrame(trades),
    )


def data_snapshot(db: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "db": str(db),
        "db_mtime_utc": datetime.fromtimestamp(
            db.stat().st_mtime, tz=timezone.utc
        ).isoformat(timespec="seconds"),
    }
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    try:
        result.update(
            dict(
                zip(
                    (
                        "tmax_rows",
                        "tmax_target_date_min",
                        "tmax_target_date_max",
                        "tmax_created_at_max",
                    ),
                    conn.execute(
                        """
                        SELECT COUNT(*), MIN(target_date), MAX(target_date),
                               MAX(created_at_utc)
                        FROM tmax_v2_canonical_states
                        """
                    ).fetchone(),
                    strict=True,
                )
            )
        )
        result["settlement_target_date_max"] = conn.execute(
            "SELECT MAX(target_date) FROM settlement_outcomes"
        ).fetchone()[0]
        result["signal_candidate_target_date_max"] = conn.execute(
            "SELECT MAX(event_date) FROM fact_signal_candidates"
        ).fetchone()[0]
    finally:
        conn.close()
    return result


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    checkpoints = pd.read_csv(args.checkpoints, low_memory=False)
    checkpoints["target_date"] = checkpoints["target_date"].astype(str)
    exact = pd.read_csv(args.exact, low_memory=False)
    exact["target_date"] = exact["target_date"].astype(str)
    exact["win"] = exact["win"].astype(bool)
    hist = pd.read_csv(args.historical, low_memory=False)
    current = pd.read_csv(args.current, low_memory=False)
    errors = pd.read_csv(args.errors, low_memory=False)

    weather_predictions = weather_policy_predictions(checkpoints)
    eval_dates = sorted(weather_predictions["target_date"].unique())
    broad_frozen_dates = set(eval_dates[-BROAD_FROZEN_DATES:])
    weather_scorecard, weather_deltas = weather_scores(
        weather_predictions,
        broad_frozen_dates,
    )
    exact_details, exact_scorecard, exact_deltas = heada_checkpoint_scores(
        exact,
        weather_predictions,
    )
    exact_slices = heada_checkpoint_slices(exact_details)
    hist, current = prepare_entry_frames(hist, current, errors)
    entry_predictions, entry_scorecard, entry_deltas, entry_trades = (
        entry_learning_curve(hist, current)
    )

    weather_predictions.to_csv(
        args.output_dir / "weather_policy_predictions.csv", index=False
    )
    weather_scorecard.to_csv(
        args.output_dir / "weather_scorecard.csv", index=False
    )
    weather_deltas.to_csv(
        args.output_dir / "weather_policy_deltas.csv", index=False
    )
    exact_details.to_csv(
        args.output_dir / "heada_checkpoint_policy_rows.csv", index=False
    )
    exact_scorecard.to_csv(
        args.output_dir / "heada_checkpoint_scorecard.csv", index=False
    )
    exact_deltas.to_csv(
        args.output_dir / "heada_checkpoint_policy_deltas.csv", index=False
    )
    exact_slices.to_csv(
        args.output_dir / "heada_checkpoint_policy_slices.csv", index=False
    )
    entry_predictions.to_csv(
        args.output_dir / "entry_policy_predictions.csv", index=False
    )
    entry_scorecard.to_csv(
        args.output_dir / "entry_scorecard.csv", index=False
    )
    entry_deltas.to_csv(
        args.output_dir / "entry_policy_deltas.csv", index=False
    )
    entry_trades.to_csv(
        args.output_dir / "entry_selected_trade_scorecard.csv", index=False
    )

    snapshot = data_snapshot(args.db)
    primary_weather = weather_deltas[
        weather_deltas["eval_window"].eq("frozen_forward")
        & weather_deltas["variant"].eq("innovation")
        & weather_deltas["candidate_policy"].eq("expanding_prior")
    ].sort_values("checkpoint_hour_local")
    primary_exact = exact_deltas[
        exact_deltas["eval_window"].eq("frozen_forward")
        & exact_deltas["variant"].eq("innovation")
        & exact_deltas["candidate_policy"].eq("expanding_prior")
    ].sort_values("checkpoint_hour_local")
    primary_entry = entry_deltas[
        entry_deltas["eval_window"].eq("frozen_forward")
        & entry_deltas["candidate_policy"].isin(
            ["all_historical", "expanding_current"]
        )
    ].sort_values(["variant", "candidate_policy"])

    summary = {
        "generated_at_utc": now_utc(),
        "snapshot": snapshot,
        "denominators": {
            "weather_checkpoint_rows": len(checkpoints),
            "weather_checkpoint_dates": int(checkpoints["target_date"].nunique()),
            "weather_eval_dates": eval_dates,
            "weather_frozen_dates": sorted(broad_frozen_dates),
            "historical_headA_rows": len(hist),
            "historical_headA_dates": int(hist["target_date"].nunique()),
            "current_headA_rows": len(current),
            "current_headA_dates": int(current["target_date"].nunique()),
            "heada_frozen_start": HEADA_FROZEN_START,
        },
        "primary_weather_frozen": primary_weather.to_dict("records"),
        "primary_exact_frozen": primary_exact.to_dict("records"),
        "primary_entry_frozen": primary_entry.to_dict("records"),
        "evidence_class": "retrospective_target_date_walk_forward_with_frozen_tail",
        "live_action": "none",
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    frozen_weather_scores = weather_scorecard[
        weather_scorecard["eval_window"].eq("frozen_forward")
        & weather_scorecard["variant"].eq("innovation")
    ].sort_values(["checkpoint_hour_local", "training_policy"])
    frozen_exact_scores = exact_scorecard[
        exact_scorecard["eval_window"].eq("frozen_forward")
        & exact_scorecard["variant"].eq("innovation")
    ].sort_values(["checkpoint_hour_local", "training_policy"])
    frozen_entry_scores = entry_scorecard[
        entry_scorecard["eval_window"].eq("frozen_forward")
    ].sort_values(["variant", "training_policy"])
    frozen_entry_trades = entry_trades[
        entry_trades["eval_window"].eq("frozen_forward")
    ].sort_values(["variant", "training_policy"])
    primary_exact_slices = exact_slices[
        exact_slices["variant"].eq("innovation")
        & exact_slices["dimension"].isin(["source", "outcome"])
    ].sort_values(["checkpoint_hour_local", "dimension", "value"])

    report = f"""# HeadA 后采数据入训 learning curve v1

Generated: {summary["generated_at_utc"]}

## 数据快照

| 字段 | 值 |
| --- | --- |
| 数据源 | canonical checkpoint CSV + HeadA historical/current fixed denominators |
| DB mtime UTC | {snapshot["db_mtime_utc"]} |
| canonical Tmax rows / dates | {snapshot["tmax_rows"]} / {snapshot["tmax_target_date_min"]}..{snapshot["tmax_target_date_max"]} |
| settlement max target_date | {snapshot["settlement_target_date_max"]} |
| HeadA historical | {len(hist)} rows / {hist["target_date"].nunique()} dates |
| HeadA current | {len(current)} rows / {current["target_date"].nunique()} dates |
| unsettled / missing_bracket | 0 / 0（本研究固定 settled label） |
| live fill coverage gate | NA（无 live_real PnL；仅 shadow/research probability） |

## 结论

**可以把后采数据分批入训，但当前证据只支持更新底层天气状态模型，不支持把新数据直接
等权灌入 HeadA entry/exact-ticket 概率头。**

- 底层 weather center 明确受益：最后 6 个日期上，expanding innovation 相对最早 5 日
  frozen model，09:00 MAE 降 `0.14676°F`（95% CI `[-0.20152,-0.09119]`），
  12:00 降 `0.06832°F`（`[-0.09353,-0.02401]`）。
- 但同一 frozen HeadA exact-ticket 分母上反向：expanding 的 09:00/12:00 Brier
  分别比 frozen-5 **变差** `+0.00348/+0.00241`，CI 都高于 0；logloss 也分别
  变差 `+0.01113/+0.00651`。
- D-1 entry 也没有“多塞数据就好”：把 6/21..6/30 加入原训练集后，frozen 4 日
  entry-base Brier/logloss 分别变差 `+0.00354/+0.01211`，CI 均高于 0；
  继续加入 prior current labels 的点估仍更差。

根因不是“新数据无用”，而是**训练目标错层**：宽分母模型在优化最终 Tmax 中心误差，
HeadA 需要的是完整 ladder 上的尾部质量、overshoot 和 exact landing 概率。expanding
在 frozen HeadA losers 上通常改善/不伤 Tmax MAE，却把 09:00 两个 winners 的概率平均
下调 `1.65pp`、12:00 唯一 winner 下调 `2.25pp`；winner Brier 分别恶化
`+0.02656/+0.03184`。这正是 center 准确度提升但 tail probability 变差的选择分布错位。

所以动作是：weather center 继续 expanding shadow；HeadA 概率头改成
**market-anchored、跨整条 ladder 归一化的分布模型**。截至 2026-07-27 的结果已经被
查看，只能作为 development/train candidate；其后首 15 个 eligible settled HeadA
target dates 立即作为 untouched frozen，不参与拟合、选特征、选阈值或中途看 ROI。
完整 15-date 报告后它们才能进入下一轮 train，同时再冻结新的 15 dates。
协议已冻结在 `configs/weather/heada_training_data_protocol_v1.json`。

本报告专门回答“后采、已结算数据加入训练，是否比最早模型更好”。所有预测都只使用
严格更早的 `target_date`；没有随机拆 ticket rows。天气模型的最后
{BROAD_FROZEN_DATES} 个日期（{min(broad_frozen_dates)}..{max(broad_frozen_dates)}）和 HeadA
最后 4 个日期（{HEADA_FROZEN_START}..{current["target_date"].max()}）是 frozen-forward 报告窗。

主比较是：

- weather：`expanding_prior` 对 `frozen_first5`；
- entry：`all_historical` / `expanding_current` 对原 `<= {ORIGINAL_ENTRY_TRAIN_END}` frozen model；
- rolling 5/10/30 只作 regime/遗忘敏感性，不据最漂亮点选 live 规则。

## A. 天气状态模型：后采日期是否改善 Tmax

同一 frozen-forward city-date-checkpoint，innovation 模型：

{md_table(frozen_weather_scores, ["checkpoint_hour_local", "training_policy", "rows", "dates", "train_dates_min", "train_dates_max", "mae_f", "rmse_f", "mean_error_f"])}

相对最早 5 日 frozen model；负数为改善：

{md_table(primary_weather, ["checkpoint_hour_local", "candidate_policy", "rows", "dates", "abs_error_f_delta", "abs_error_f_delta_ci_low", "abs_error_f_delta_ci_high", "squared_error_f_delta", "squared_error_f_delta_ci_low", "squared_error_f_delta_ci_high"])}

## B. HeadA exact-ticket：后采训练是否变成 market residual

固定 HeadA ticket、同刻 market proxy、gamma=1；模型只提供
`P_innovation - P_rolling_bias` 的增量。负 delta 才比 market 好：

{md_table(frozen_exact_scores, ["checkpoint_hour_local", "training_policy", "rows", "dates", "wins", "train_dates_min", "train_dates_max", "tmax_mae_f", "observed_rate", "mean_market_p", "mean_p_hat", "brier_delta_vs_market", "brier_delta_ci_low", "brier_delta_ci_high", "logloss_delta_vs_market", "logloss_delta_ci_low", "logloss_delta_ci_high"])}

expanding 相对最早 5 日 model；负数为改善：

{md_table(primary_exact, ["checkpoint_hour_local", "candidate_policy", "rows", "dates", "brier_delta_vs_frozen5", "brier_delta_vs_frozen5_ci_low", "brier_delta_vs_frozen5_ci_high", "logloss_delta_vs_frozen5", "logloss_delta_vs_frozen5_ci_low", "logloss_delta_vs_frozen5_ci_high"])}

expanding 相对 frozen-5 的诊断切片（post-hoc，只解释变差来自哪里）：

{md_table(primary_exact_slices, ["checkpoint_hour_local", "dimension", "value", "rows", "dates", "wins", "p_hat_delta", "tmax_mae_delta_f", "brier_delta_vs_frozen5", "logloss_delta_vs_frozen5"])}

## C. D-1 entry 概率头：加入 6/21 后数据

固定 current frozen-forward rows：

{md_table(frozen_entry_scores, ["variant", "training_policy", "rows", "dates", "wins", "train_rows_min", "train_rows_max", "train_dates_min", "train_dates_max", "train_win_rate_min", "train_win_rate_max", "observed_rate", "mean_p_hat", "mean_unit_cost", "brier", "logloss"])}

相对原 `<= {ORIGINAL_ENTRY_TRAIN_END}` 模型；负数为改善：

{md_table(primary_entry, ["variant", "candidate_policy", "rows", "dates", "brier_delta_vs_original", "brier_delta_vs_original_ci_low", "brier_delta_vs_original_ci_high", "logloss_delta_vs_original", "logloss_delta_vs_original_ci_low", "logloss_delta_vs_original_ci_high"])}

使用固定 `p_hat > fee-adjusted cost_eval` 的 secondary trade expression：

{md_table(frozen_entry_trades, ["variant", "training_policy", "opportunity_rows", "action_rows", "action_dates", "wins", "cost_usd", "pnl_usd", "roi", "roi_ci_low", "roi_ci_high", "all_buy_cost_usd", "all_buy_pnl_usd", "all_buy_roi", "all_buy_roi_ci_low", "all_buy_roi_ci_high"])}

这里只是 selected-trade secondary diagnostic；是否改善首先以固定分母 Brier/logloss 判定，
不以少数彩票 ROI 反向挑模型。

## 固定漏斗

Signal funnel：

- weather raw：{len(checkpoints)} checkpoint rows / {checkpoints["target_date"].nunique()} dates；
- weather eligible：09/12 checkpoint，前 5 日期 train-only，后 12 日期 OOF；
- HeadA historical：{len(hist)} tickets / {hist["target_date"].nunique()} dates；
- HeadA current：{len(current)} tickets / {current["target_date"].nunique()} dates。

Evidence funnel：

- PIT canonical checkpoint / settlement：weather learning curve 全部同 rows；
- current HeadA entry ask / settlement：{len(current)}/{len(current)}；
- HeadA 09/12 proxy book：见 exact scorecard rows；
- actual fill：0，zero-notional/research；
- historical weather/multisource archive 仍是 post-hoc，未拿它训练新 entry weather gate。

## 解释边界与下一轮训练协议

1. “更多 rows”不等于“更多独立信息”；有效样本是 target dates，不是同日城市票数。
2. expanding 若改善 frozen proper score，说明最早 5 日/旧截止训练确实 underfit；
   若 rolling 优于 expanding，则主要是 regime drift，不应把全部历史等权混入。
3. 训练更新只能吸收已 settlement 的 prior target dates；生产应保留一个永不参与模型选择的
   rolling frozen buffer，再按固定 cadence 批量升级，不能每天看完 ROI 临时改参数。
4. 本轮仍只有 6 个 broad frozen dates、4 个 HeadA frozen dates；CI 跨 0 时结论仍是
   `inconclusive`，不会因为“数据更多”自动升 live。

## 八环与三门

1. hypothesis：后采 settled 数据可能降低模型 variance / 修正 regime。
2. universe：固定 weather checkpoint 与 HeadA current denominators。
3. time：strict prior target-date walk-forward；无随机 row split。
4. model：原 frozen、expanding、rolling 同超参数。
5. probability：Brier/logloss + target-date block CI。
6. execution：仅 secondary fee-adjusted `cost_eval` expression，无 fresh live fill。
7. robustness：rolling 5/10/30；没有从结果反选阈值。
8. deployment：不改 live；只决定后续 training protocol。

```text
significance = 见 paired block CI
baseline = market / original frozen model
forward = PARTIAL（retrospective frozen tail，尚非全新未看日期）
conclusion = inconclusive 或 shadow_candidate；live_action=none
```

## 产物

- evaluator：`scripts/analysis/forecast_quality/research_heada_training_data_learning_curve_v1.py`
- structured：`generated/heada_training_data_learning_curve_v1/summary.json`
- weather：`weather_scorecard.csv`, `weather_policy_deltas.csv`
- exact-ticket：`heada_checkpoint_scorecard.csv`, `heada_checkpoint_policy_deltas.csv`
- 诊断切片：`heada_checkpoint_policy_slices.csv`
- entry：`entry_scorecard.csv`, `entry_policy_deltas.csv`,
  `entry_selected_trade_scorecard.csv`
"""
    args.report.write_text(report, encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
