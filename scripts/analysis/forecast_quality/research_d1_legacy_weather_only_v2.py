#!/usr/bin/env python3
"""Train D-1 weather-only v2 challengers on legacy development evidence.

This is explicitly not the clean run-aware forward.  A policy-selected legacy
daily-cache slice trains A-E.  The first 18 reconstructed D-1 target dates select the F
ensemble/spread overlay, and the remaining dates are an untouched legacy
holdout.  Market is evaluated on identical rows but never enters a weather
model.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.special import ndtr

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality import research_d1_cross_city_hierarchy_v1 as base  # noqa: E402


DEFAULT_OUT = ROOT / "docs/analysis/2026-08/generated/d1_legacy_weather_only_v2"
DEFAULT_REPORT = ROOT / "docs/analysis/2026-08/2026-08-05-d1-legacy-weather-only-v2.md"
DEFAULT_ENRICHMENT_HISTORY = (
    ROOT
    / "docs/analysis/2026-07/generated/historical_forecast_enrichment_bias_v1/daily_error_rows.csv"
)
KERNEL_SD_F = 0.75
EPS = 1e-8
ENRICHMENT_AVAILABLE_DATE = "2026-07-08"
HISTORY_POLICIES = (
    "summer_best",
    "all_season_best",
    "harmonic_all_season_best",
    "all_season_all_models",
)


def _unit_values(values_f: np.ndarray, unit: str) -> np.ndarray:
    return (values_f - 32.0) * 5.0 / 9.0 if unit == "C" else values_f


def empirical_vector(
    state: dict[str, Any],
    values_f: np.ndarray,
    *,
    kernel_sd_f: float,
) -> np.ndarray:
    values = _unit_values(np.asarray(values_f, dtype=float), state["market_unit"])
    kernel = kernel_sd_f * (5.0 / 9.0 if state["market_unit"] == "C" else 1.0)
    probabilities: list[float] = []
    for bracket in state["brackets"]:
        if bracket.bottom:
            probability = float(np.mean(ndtr((float(bracket.high) + 0.5 - values) / kernel)))
        elif bracket.top:
            probability = float(np.mean(1.0 - ndtr((float(bracket.low) - 0.5 - values) / kernel)))
        else:
            upper = ndtr((float(bracket.high) + 0.5 - values) / kernel)
            lower = ndtr((float(bracket.low) - 0.5 - values) / kernel)
            probability = float(np.mean(upper - lower))
        probabilities.append(probability)
    vector = np.clip(np.asarray(probabilities), EPS, None)
    return vector / vector.sum()


def _harmonic_design(months: np.ndarray) -> np.ndarray:
    angle = 2.0 * np.pi * (np.asarray(months, dtype=float) - 1.0) / 12.0
    return np.column_stack(
        [
            np.ones(len(angle)),
            np.sin(angle),
            np.cos(angle),
            np.sin(2.0 * angle),
            np.cos(2.0 * angle),
        ]
    )


def fit_season_coefficients(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    coefficients: dict[str, np.ndarray] = {}
    for model, group in frame.groupby("model"):
        design = _harmonic_design(group["month_num"].to_numpy(dtype=float))
        target = group["error_f_actual_minus_forecast"].to_numpy(dtype=float)
        coefficients[str(model)] = np.linalg.lstsq(design, target, rcond=None)[0]
    return coefficients


def season_center(model: str, month: int, coefficients: dict[str, np.ndarray]) -> float:
    if model not in coefficients:
        return 0.0
    return float(_harmonic_design(np.asarray([month], dtype=float))[0] @ coefficients[model])


def prepare_history(
    history: pd.DataFrame,
    test_start: str,
    history_policy: str,
    assignments: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, bool]:
    if history_policy not in HISTORY_POLICIES:
        raise ValueError(f"unsupported history_policy={history_policy}")
    use_all_models = history_policy == "all_season_all_models"
    eligible = history.loc[history["date"] < test_start].copy()
    if not use_all_models:
        if assignments is None:
            eligible = eligible.loc[eligible["is_best_model"]].copy()
        else:
            eligible = eligible.merge(
                assignments[["city", "model"]],
                on=["city", "model"],
                how="inner",
                validate="many_to_one",
            )
    train = eligible
    seasonal = history_policy == "harmonic_all_season_best"
    if history_policy == "summer_best":
        train = train.loc[train["month_num"].isin([5, 6, 7, 8])].copy()
    return train, seasonal


def _attach_hierarchy_error(
    frame: pd.DataFrame,
    coefficients: dict[str, np.ndarray],
) -> pd.DataFrame:
    result = frame.copy()
    result["hierarchy_error"] = [
        float(error) - season_center(str(model), int(month), coefficients)
        for error, model, month in zip(
            result["error_f_actual_minus_forecast"],
            result["model"],
            result["month_num"],
        )
    ]
    return result


def select_hierarchy_lambdas(train: pd.DataFrame, *, seasonal: bool = False) -> dict[str, float]:
    fit = train.loc[train["date"] < "2026-01-01"].copy()
    validation = train.loc[train["date"] >= "2026-01-01"].copy()
    if fit.empty or validation.empty:
        raise ValueError("history cannot support pre-2026 hierarchy validation")
    coefficients = fit_season_coefficients(fit) if seasonal else {}
    fit = _attach_hierarchy_error(fit, coefficients)
    validation = _attach_hierarchy_error(validation, coefficients)
    global_stats = fit.groupby("model")["hierarchy_error"].agg(["mean", "std"])
    city_stats = fit.groupby(["city", "model"])["hierarchy_error"].agg(["count", "mean", "std"])
    candidates: list[dict[str, float]] = []
    for center_lambda in (10.0, 30.0, 60.0, 120.0, 240.0):
        for scale_lambda in (60.0, 120.0, 240.0, 480.0):
            losses: list[float] = []
            for row in validation.itertuples(index=False):
                key = (row.city, row.model)
                if key not in city_stats.index or row.model not in global_stats.index:
                    continue
                city = city_stats.loc[key]
                glob = global_stats.loc[row.model]
                n = float(city["count"])
                center_weight = n / (n + center_lambda)
                scale_weight = n / (n + scale_lambda)
                center = float(glob["mean"]) + center_weight * (float(city["mean"]) - float(glob["mean"]))
                city_sd = max(float(city["std"]), 0.5)
                global_sd = max(float(glob["std"]), 0.5)
                scale = global_sd * math.exp(scale_weight * math.log(city_sd / global_sd))
                error = float(row.hierarchy_error)
                losses.append(math.log(scale) + 0.5 * ((error - center) / scale) ** 2)
            candidates.append(
                {
                    "center_lambda": center_lambda,
                    "scale_lambda": scale_lambda,
                    "validation_nll": float(np.mean(losses)),
                }
            )
    return min(candidates, key=lambda row: row["validation_nll"])


def fit_legacy_history_slice(
    history: pd.DataFrame,
    test_start: str,
    *,
    history_policy: str = "summer_best",
    assignment_policy: str = "legacy_is_best_model",
) -> dict[str, Any]:
    assignments = base.model_assignments(history, assignment_policy)
    train, seasonal = prepare_history(
        history,
        test_start,
        history_policy,
        assignments=None if history_policy == "all_season_all_models" else assignments,
    )
    assigned_pairs = set(
        map(tuple, assignments[["city", "model"]].itertuples(index=False, name=None))
    )
    selected = select_hierarchy_lambdas(train, seasonal=seasonal)
    coefficients = fit_season_coefficients(train) if seasonal else {}
    train = _attach_hierarchy_error(train, coefficients)
    global_errors = {
        model: group["hierarchy_error"].to_numpy(dtype=float)
        for model, group in train.groupby("model")
    }
    city_stats = train.groupby(["city", "model"])["hierarchy_error"].agg(["count", "mean", "std"])
    specs: dict[str, Any] = {}
    for (city, model), city_row in city_stats.iterrows():
        if (city, model) not in assigned_pairs:
            continue
        errors = global_errors[model]
        global_mean = float(np.mean(errors))
        global_sd = max(float(np.std(errors, ddof=1)), 0.5)
        n = float(city_row["count"])
        center_weight = n / (n + selected["center_lambda"])
        scale_weight = n / (n + selected["scale_lambda"])
        center = global_mean + center_weight * (float(city_row["mean"]) - global_mean)
        city_sd = max(float(city_row["std"]), 0.5)
        scale = global_sd * math.exp(scale_weight * math.log(city_sd / global_sd))
        standardized_shape = (errors - global_mean) / global_sd
        specs[city] = {
            "model": model,
            "global_mean": global_mean,
            "global_sd": global_sd,
            "global_errors": errors,
            "partial_center": center,
            "partial_scale": scale,
            "partial_errors": center + scale * standardized_shape,
            "train_rows": int(n),
            "season_coefficients": coefficients.get(str(model)),
        }
    climatology = (
        train[["city", "date", "actual_max_f"]]
        .drop_duplicates(["city", "date"])
        .assign(month=lambda frame: pd.to_datetime(frame["date"]).dt.month)
    )
    return {
        "train": train,
        "specs": specs,
        "climatology": climatology,
        "lambda_selection": selected,
        "history_policy": history_policy,
        "assignment_policy": assignment_policy,
        "seasonal_harmonic": seasonal,
    }


# Compatibility for the locked robust-tail W0 runner and historical callers.
fit_long_history = fit_legacy_history_slice


def attach_multi_model(states: list[dict[str, Any]], forecasts: pd.DataFrame) -> None:
    grouped = forecasts.groupby("snapshot_key")["forecast_max_f"].agg(["median", "mean", "min", "max"])
    grouped["spread"] = grouped["max"] - grouped["min"]
    model_values = {
        str(snapshot_key): {
            str(row.model_key): float(row.forecast_max_f)
            for row in group.itertuples(index=False)
        }
        for snapshot_key, group in forecasts.groupby("snapshot_key")
    }
    for state in states:
        row = grouped.loc[state["snapshot_key"]]
        state["ensemble_median_f"] = float(row["median"])
        state["ensemble_mean_f"] = float(row["mean"])
        state["model_spread_f"] = float(row["spread"])
        state["model_values_f"] = model_values[str(state["snapshot_key"])]


def select_enrichment_lambdas(history: pd.DataFrame) -> dict[str, float]:
    dates = sorted(history["target_date"].astype(str).unique())
    split = max(1, int(len(dates) * 0.75))
    fit_dates = set(dates[:split])
    fit = history.loc[history["target_date"].astype(str).isin(fit_dates)].copy()
    validation = history.loc[~history["target_date"].astype(str).isin(fit_dates)].copy()
    global_stats = fit.groupby("model_key")["error_f"].agg(["mean", "std"])
    city_stats = fit.groupby(["city", "model_key"])["error_f"].agg(["count", "mean", "std"])
    candidates: list[dict[str, float]] = []
    for center_lambda in (10.0, 30.0, 60.0, 120.0):
        for scale_lambda in (60.0, 120.0, 240.0, 480.0):
            losses: list[float] = []
            for row in validation.itertuples(index=False):
                key = (row.city, row.model_key)
                if key not in city_stats.index or row.model_key not in global_stats.index:
                    continue
                city = city_stats.loc[key]
                glob = global_stats.loc[row.model_key]
                n = float(city["count"])
                center_weight = n / (n + center_lambda)
                scale_weight = n / (n + scale_lambda)
                center = float(glob["mean"]) + center_weight * (
                    float(city["mean"]) - float(glob["mean"])
                )
                global_sd = max(float(glob["std"]), 0.5)
                city_sd = max(float(city["std"]), 0.5)
                scale = global_sd * math.exp(scale_weight * math.log(city_sd / global_sd))
                losses.append(math.log(scale) + 0.5 * ((float(row.error_f) - center) / scale) ** 2)
            candidates.append(
                {
                    "center_lambda": center_lambda,
                    "scale_lambda": scale_lambda,
                    "validation_nll": float(np.mean(losses)),
                    "fit_dates": len(fit_dates),
                    "validation_dates": len(dates) - len(fit_dates),
                }
            )
    return min(candidates, key=lambda row: row["validation_nll"])


def fit_enrichment_history(history: pd.DataFrame) -> dict[str, Any]:
    selected = select_enrichment_lambdas(history)
    global_errors = {
        str(model): group["error_f"].to_numpy(dtype=float)
        for model, group in history.groupby("model_key")
    }
    city_stats = history.groupby(["city", "model_key"])["error_f"].agg(
        ["count", "mean", "std"]
    )
    specs: dict[tuple[str, str], dict[str, Any]] = {}
    for (city, model), row in city_stats.iterrows():
        errors = global_errors[str(model)]
        global_mean = float(np.mean(errors))
        global_sd = max(float(np.std(errors, ddof=1)), 0.5)
        n = float(row["count"])
        center_weight = n / (n + selected["center_lambda"])
        scale_weight = n / (n + selected["scale_lambda"])
        center = global_mean + center_weight * (float(row["mean"]) - global_mean)
        city_sd = max(float(row["std"]), 0.5)
        scale = global_sd * math.exp(scale_weight * math.log(city_sd / global_sd))
        specs[(str(city), str(model))] = {
            "errors": center + scale * ((errors - global_mean) / global_sd),
            "rows": int(n),
        }
    return {
        "rows": int(len(history)),
        "dates": int(history["target_date"].nunique()),
        "cities": int(history["city"].nunique()),
        "models": int(history["model_key"].nunique()),
        "lambda_selection": selected,
        "specs": specs,
        "global_errors": global_errors,
    }


def enrichment_vector(
    state: dict[str, Any],
    fitted: dict[str, Any],
) -> np.ndarray | None:
    samples: list[np.ndarray] = []
    for model, forecast in state["model_values_f"].items():
        spec = fitted["specs"].get((state["city"], model))
        errors = spec["errors"] if spec else fitted["global_errors"].get(model)
        if errors is not None and len(errors):
            samples.append(float(forecast) + np.asarray(errors, dtype=float))
    if not samples:
        return None
    # Equal source weight: a source with more archive rows must not silently
    # receive more probability mass.
    per_source = [
        empirical_vector(state, values, kernel_sd_f=KERNEL_SD_F)
        for values in samples
    ]
    vector = np.mean(np.vstack(per_source), axis=0)
    return vector / vector.sum()


def model_vectors(
    state: dict[str, Any],
    fitted: dict[str, Any],
    *,
    ensemble_weight: float,
    spread_beta: float,
    only_arm: str | None = None,
    enrichment_fitted: dict[str, Any] | None = None,
) -> dict[str, np.ndarray]:
    spec = fitted["specs"][state["city"]]
    month = int(pd.Timestamp(state["target_date"]).month)
    climate = fitted["climatology"]
    climate_values = climate.loc[
        (climate["city"] == state["city"]) & (climate["month"] == month),
        "actual_max_f",
    ].to_numpy(dtype=float)
    if len(climate_values) < 20:
        climate_values = climate.loc[climate["city"] == state["city"], "actual_max_f"].to_numpy(dtype=float)
    if len(climate_values) == 0:
        raise ValueError(f"missing climatology training rows for city={state['city']}")
    assigned = float(state["forecast_max_f"])
    season_bias = season_center(
        str(spec["model"]),
        month,
        ({str(spec["model"]): spec["season_coefficients"]} if spec["season_coefficients"] is not None else {}),
    )
    global_center = season_bias + float(spec["global_mean"])
    partial_center = season_bias + float(spec["partial_center"])
    global_errors = season_bias + np.asarray(spec["global_errors"], dtype=float)
    partial_errors = season_bias + np.asarray(spec["partial_errors"], dtype=float)
    blended = (1.0 - ensemble_weight) * assigned + ensemble_weight * float(state["ensemble_median_f"])
    scale_multiplier = math.sqrt(
        1.0 + spread_beta * (float(state["model_spread_f"]) / max(spec["global_sd"], 0.5)) ** 2
    )
    f_vector = empirical_vector(
        state,
        blended
        + partial_center
        + (partial_errors - partial_center) * scale_multiplier,
        kernel_sd_f=KERNEL_SD_F,
    )
    if only_arm == "F_ensemble_spread":
        return {"F_ensemble_spread": f_vector}
    result = {
        "A_climatology": empirical_vector(state, climate_values, kernel_sd_f=KERNEL_SD_F),
        "B_pooled_normal_zero_bias": base.probability_vector(state, 0.0, spec["global_sd"]),
        "C_bias_corrected_pooled_normal": base.probability_vector(state, global_center, spec["global_sd"]),
        "D_coherent_pooled_empirical": empirical_vector(
            state,
            assigned + global_errors,
            kernel_sd_f=KERNEL_SD_F,
        ),
        "E_partial_hierarchy_v2": empirical_vector(
            state,
            assigned + partial_errors,
            kernel_sd_f=KERNEL_SD_F,
        ),
        "F_ensemble_spread": f_vector,
        "market": state["market_probs"],
    }
    if enrichment_fitted is not None and str(state["target_date"]) > ENRICHMENT_AVAILABLE_DATE:
        vector = enrichment_vector(state, enrichment_fitted)
        if vector is not None:
            result["H_archive_known_multimodel"] = vector
    return result


def score(
    states: list[dict[str, Any]],
    fitted: dict[str, Any],
    *,
    ensemble_weight: float,
    spread_beta: float,
    only_arm: str | None = None,
    enrichment_fitted: dict[str, Any] | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for state in states:
        for arm, vector in model_vectors(
            state,
            fitted,
            ensemble_weight=ensemble_weight,
            spread_beta=spread_beta,
            only_arm=only_arm,
            enrichment_fitted=enrichment_fitted,
        ).items():
            logloss, brier, rps, winner_probability, top1 = base.score_vector(vector, state["winner_index"])
            rows.append(
                {
                    "snapshot_key": state["snapshot_key"],
                    "city": state["city"],
                    "target_date": state["target_date"],
                    "arm": arm,
                    "logloss": logloss,
                    "brier": brier,
                    "rps": rps,
                    "winner_probability": winner_probability,
                    "top1_accuracy": top1,
                    "model_spread_f": state["model_spread_f"],
                    "probabilities_json": json.dumps(vector.tolist(), separators=(",", ":")),
                    "winner_index": int(state["winner_index"]),
                }
            )
    return pd.DataFrame(rows)


def date_equal(frame: pd.DataFrame) -> pd.DataFrame:
    metrics = ["logloss", "brier", "rps", "winner_probability", "top1_accuracy"]
    daily = frame.groupby(["target_date", "arm"], as_index=False)[metrics].mean()
    summary = daily.groupby("arm", as_index=False)[metrics].mean()
    counts = frame.groupby("arm").agg(states=("snapshot_key", "size"), dates=("target_date", "nunique"), cities=("city", "nunique")).reset_index()
    return summary.merge(counts, on="arm")


def select_f_overlay(dev_states: list[dict[str, Any]], fitted: dict[str, Any]) -> dict[str, float]:
    candidates: list[dict[str, float]] = []
    for weight in (0.0, 0.25, 0.5, 0.75, 1.0):
        for beta in (0.0, 0.25, 0.5, 1.0):
            frame = score(
                dev_states,
                fitted,
                ensemble_weight=weight,
                spread_beta=beta,
                only_arm="F_ensemble_spread",
            )
            value = float(
                frame.loc[frame["arm"] == "F_ensemble_spread"]
                .groupby("target_date")["logloss"]
                .mean()
                .mean()
            )
            candidates.append({"ensemble_weight": weight, "spread_beta": beta, "dev_logloss": value})
    return min(candidates, key=lambda row: row["dev_logloss"])


def bootstrap_delta(frame: pd.DataFrame, left: str, right: str, metric: str) -> dict[str, Any]:
    daily = frame.loc[frame["arm"].isin([left, right])].groupby(["target_date", "arm"])[metric].mean().unstack().dropna()
    deltas = (daily[left] - daily[right]).to_numpy(dtype=float)
    rng = np.random.default_rng(20260805)
    draws = rng.choice(deltas, size=(20000, len(deltas)), replace=True).mean(axis=1)
    return {
        "left": left,
        "right": right,
        "metric": metric,
        "dates": len(deltas),
        "delta": float(np.mean(deltas)),
        "ci_low": float(np.quantile(draws, 0.025)),
        "ci_high": float(np.quantile(draws, 0.975)),
    }


def calibration(frame: pd.DataFrame) -> pd.DataFrame:
    bins = [-0.001, 0.05, 0.10, 0.20, 0.40, 0.60, 1.001]
    rows: list[dict[str, Any]] = []
    for row in frame.itertuples(index=False):
        for rung_index, probability in enumerate(json.loads(row.probabilities_json)):
            rows.append(
                {
                    "arm": row.arm,
                    "snapshot_key": row.snapshot_key,
                    "predicted_probability": float(probability),
                    "outcome": int(rung_index == row.winner_index),
                }
            )
    source = pd.DataFrame(rows)
    source["probability_bin"] = pd.cut(source["predicted_probability"], bins=bins, right=True)
    return source.groupby(["arm", "probability_bin"], observed=True).agg(
        rungs=("snapshot_key", "size"),
        mean_predicted=("predicted_probability", "mean"),
        actual_frequency=("outcome", "mean"),
    ).reset_index()


def probability_diagnostics(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for arm, group in frame.groupby("arm"):
        expanded: list[tuple[float, int]] = []
        bottom_predicted: list[float] = []
        bottom_actual: list[int] = []
        top_predicted: list[float] = []
        top_actual: list[int] = []
        for row in group.itertuples(index=False):
            vector = np.asarray(json.loads(row.probabilities_json), dtype=float)
            outcomes = np.arange(len(vector)) == int(row.winner_index)
            expanded.extend(zip(vector.tolist(), outcomes.astype(int).tolist()))
            bottom_predicted.append(float(vector[0]))
            bottom_actual.append(int(row.winner_index == 0))
            top_predicted.append(float(vector[-1]))
            top_actual.append(int(row.winner_index == len(vector) - 1))
        expanded_frame = pd.DataFrame(expanded, columns=["probability", "outcome"])
        expanded_frame["bin"] = pd.cut(
            expanded_frame["probability"],
            bins=[-0.001, 0.05, 0.10, 0.20, 0.40, 0.60, 1.001],
        )
        grouped = expanded_frame.groupby("bin", observed=True).agg(
            n=("outcome", "size"), predicted=("probability", "mean"), actual=("outcome", "mean")
        )
        ece = float(((grouped["predicted"] - grouped["actual"]).abs() * grouped["n"]).sum() / grouped["n"].sum())
        high = expanded_frame.loc[expanded_frame["probability"] >= 0.40]
        rows.append(
            {
                "arm": arm,
                "rung_ece": ece,
                "bottom_predicted": float(np.mean(bottom_predicted)),
                "bottom_actual": float(np.mean(bottom_actual)),
                "top_predicted": float(np.mean(top_predicted)),
                "top_actual": float(np.mean(top_actual)),
                "high_probability_rungs": int(len(high)),
                "high_probability_mean": float(high["probability"].mean()) if len(high) else None,
                "high_probability_actual": float(high["outcome"].mean()) if len(high) else None,
                "winner_probability_le_001": int((group["winner_probability"] <= 0.01).sum()),
            }
        )
    return pd.DataFrame(rows)


def render_report(
    summary: dict[str, Any],
    scores: pd.DataFrame,
    deltas: pd.DataFrame,
    enrichment_scores: pd.DataFrame,
) -> str:
    rows = {row.arm: row for row in scores.itertuples(index=False)}
    lines = [
        "# D-1 legacy weather-only v2 训练与 holdout 报告",
        "",
        "weather-only:",
        f"significance={summary['weather_gate']['significance']}",
        f"calibration={summary['weather_gate']['calibration']}",
        "pooled_baseline=B_pooled_normal_zero_bias",
        "forward=legacy_reconstructed_holdout_not_clean_run_aware_forward",
        "",
        "market residual:",
        "baseline=market_same_rows",
        "forward=not_run_by_contract",
        "execution=not_run_by_contract",
        "",
        "production:",
        "live_action=none",
        "orders_changed=0",
        "",
        "## 结论",
        "",
        summary["conclusion"],
        "",
        f"legacy holdout 为 {summary['holdout']['states']} states / {summary['holdout']['dates']} target dates / {summary['holdout']['cities']} cities。它可以否定坏结构和选择开发方向，但不能替代新 collector 的 frozen forward。",
        "",
        "## 同分母 holdout score",
        "",
        "| arm | logloss | Brier | RPS | winner P | top-1 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for arm in ("market", "A_climatology", "B_pooled_normal_zero_bias", "C_bias_corrected_pooled_normal", "D_coherent_pooled_empirical", "E_partial_hierarchy_v2", "F_ensemble_spread"):
        row = rows[arm]
        lines.append(f"| {arm} | {row.logloss:.4f} | {row.brier:.4f} | {row.rps:.4f} | {row.winner_probability:.3f} | {row.top1_accuracy:.1%} |")
    if not enrichment_scores.empty:
        enrichment_rows = {
            row.arm: row for row in enrichment_scores.itertuples(index=False)
        }
        lines += [
            "",
            "## 7 月 8 日已知的 multi-model archive challenger",
            "",
            "这部分历史在 2026-07-08 才形成可审计 artifact，因此只评分 2026-07-16..23；没有回灌到更早 decision。它是 historical archive calibration，不是真实 D-1 run lineage。",
            "",
            "| arm | dates | states | logloss | Brier | RPS |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for arm in ("market", "F_ensemble_spread", "H_archive_known_multimodel"):
            row = enrichment_rows[arm]
            lines.append(
                f"| {arm} | {row.dates} | {row.states} | {row.logloss:.4f} | {row.brier:.4f} | {row.rps:.4f} |"
            )
        h_vs_f = summary["archive_known_multimodel_comparison"]["h_vs_f_logloss"]
        h_vs_market = summary["archive_known_multimodel_comparison"]["h_vs_market_logloss"]
        lines += [
            "",
            f"- H vs F logloss Δ={h_vs_f['delta']:+.4f}（95% CI {h_vs_f['ci_low']:+.4f}..{h_vs_f['ci_high']:+.4f}）。",
            f"- H vs market logloss Δ={h_vs_market['delta']:+.4f}（95% CI {h_vs_market['ci_low']:+.4f}..{h_vs_market['ci_high']:+.4f}）。",
            "- H 对每个可用 source 等权；archive 行数多的 source 不会因此获得更大权重。",
        ]
    lines += [
        "",
        "G（physical-width）没有进入本轮：旧 reconstruction 没有同 clock 的 rain/convective/cloud/wind 完整特征。缺特征记 coverage blocker，不用事后天气或 hard filter 补洞。",
        "",
        "## 训练合同",
        "",
        f"- legacy training slice：policy=`{summary['history_policy']}`，{summary['training']['rows']} rows / {summary['training']['cities']} cities / {summary['training']['dates']} dates；lineage=`legacy_daily_cache_non_strict_pit_training_prior`。",
        f"- denominator funnel：artifact {summary['training']['denominator']['artifact_input']['rows']} rows / {summary['training']['denominator']['artifact_input']['cities']} cities → best-model {summary['training']['denominator']['best_model_only']['rows']} rows → summer slice {summary['training']['denominator']['best_model_summer_training_slice']['rows']} rows；这不是项目全部历史。",
        f"- enrichment history：{summary['enrichment_history']['rows']} rows / {summary['enrichment_history']['cities']} cities / {summary['enrichment_history']['models']} models；lineage=`archive_known_2026-07-08_not_collector_exact_run`。",
        f"- partial hierarchy：center λ={summary['partial_hierarchy']['center_lambda']:.0f}，scale λ={summary['partial_hierarchy']['scale_lambda']:.0f}；只在历史 validation 选。",
        f"- F overlay：ensemble weight={summary['f_overlay']['ensemble_weight']:.2f}，spread beta={summary['f_overlay']['spread_beta']:.2f}；只在前 {summary['development']['dates']} 个 reconstructed dates 选。",
        f"- holdout：{summary['holdout']['start']}..{summary['holdout']['end']}；没有调 λ、weight 或 beta。",
        "- market 只作同 rows baseline，没有进入 A-F；market residual 和交易表达均未运行。",
        "- F 中 run revision / lead / run age 在旧 reconstruction 中不可用；本轮 F 实际只检验 ensemble median 与 spread。开发集选择 `spread beta=0`，现有 spread 没有增量价值。",
        "",
        "## Calibration 与 tail",
        "",
        f"- F rung ECE={summary['diagnostics']['F_ensemble_spread']['rung_ece']:.4f}，pooled={summary['diagnostics']['B_pooled_normal_zero_bias']['rung_ece']:.4f}，market={summary['diagnostics']['market']['rung_ece']:.4f}。",
        f"- F bottom tail：预测 {summary['diagnostics']['F_ensemble_spread']['bottom_predicted']:.1%} / 实际 {summary['diagnostics']['F_ensemble_spread']['bottom_actual']:.1%}；top tail：预测 {summary['diagnostics']['F_ensemble_spread']['top_predicted']:.1%} / 实际 {summary['diagnostics']['F_ensemble_spread']['top_actual']:.1%}。",
        f"- F 给真实 winner ≤1% 概率的 state={summary['diagnostics']['F_ensemble_spread']['winner_probability_le_001']}；仍需在更大 clean forward 上检查 city 灾难尾。",
        "",
        "## 下一步",
        "",
        "本轮只做 development challenger 比较，不因 legacy holdout 结果冻结参数。新 exact-run collector 积累 settlement 后，用预注册候选在 fresh frozen forward 重估；只有 clean run-aware forward 通过 weather gate，才运行 market residual。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--forecasts", type=Path, default=base.DEFAULT_FORECASTS)
    parser.add_argument("--baskets", type=Path, default=base.DEFAULT_BASKETS)
    parser.add_argument("--history", type=Path, default=base.DEFAULT_HISTORY)
    parser.add_argument("--enrichment-history", type=Path, default=DEFAULT_ENRICHMENT_HISTORY)
    parser.add_argument("--db", type=Path, default=base.DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--history-policy", choices=HISTORY_POLICIES, default="summer_best")
    args = parser.parse_args(argv)

    forecasts = pd.read_csv(args.forecasts, dtype={"target_date": str})
    baskets = pd.read_csv(args.baskets, dtype={"target_date": str})
    history = pd.read_csv(args.history, dtype={"date": str})
    enrichment_history = pd.read_csv(args.enrichment_history, dtype={"target_date": str})
    history["is_best_model"] = history["is_best_model"].astype(str).str.lower().isin(["true", "1"])
    history["month_num"] = pd.to_datetime(history["date"]).dt.month
    assignments = base.model_assignments(history)
    states, funnels = base.build_states(forecasts, baskets, assignments, base.load_settlements(args.db))
    states = [state for state in states if state["policy"] == base.PRIMARY_POLICY]
    attach_multi_model(states, forecasts)
    target_dates = sorted({state["target_date"] for state in states})
    split = min(18, len(target_dates) - 5)
    development_dates = set(target_dates[:split])
    holdout_dates = set(target_dates[split:])
    development_states = [state for state in states if state["target_date"] in development_dates]
    holdout_states = [state for state in states if state["target_date"] in holdout_dates]
    fitted = fit_legacy_history_slice(history, min(target_dates), history_policy=args.history_policy)
    enrichment_fitted = fit_enrichment_history(enrichment_history)
    overlay = select_f_overlay(development_states, fitted)
    scored = score(
        holdout_states,
        fitted,
        ensemble_weight=overlay["ensemble_weight"],
        spread_beta=overlay["spread_beta"],
        enrichment_fitted=enrichment_fitted,
    )
    score_summary = date_equal(scored)
    diagnostics = probability_diagnostics(scored)
    deltas = pd.DataFrame(
        [
            bootstrap_delta(scored, left, right, metric)
            for metric in ("logloss", "brier", "rps")
            for left, right in (
                ("F_ensemble_spread", "B_pooled_normal_zero_bias"),
                ("F_ensemble_spread", "E_partial_hierarchy_v2"),
                ("F_ensemble_spread", "market"),
                ("E_partial_hierarchy_v2", "B_pooled_normal_zero_bias"),
            )
        ]
    )
    scores_by_arm = score_summary.set_index("arm")
    f_vs_b = deltas.loc[(deltas["metric"] == "logloss") & (deltas["left"] == "F_ensemble_spread") & (deltas["right"] == "B_pooled_normal_zero_bias")].iloc[0]
    f_vs_market = deltas.loc[(deltas["metric"] == "logloss") & (deltas["left"] == "F_ensemble_spread") & (deltas["right"] == "market")].iloc[0]
    significance = "improves_pooled" if f_vs_b.ci_high < 0 else "not_significant_vs_pooled"
    conclusion = (
        f"F ensemble/spread holdout logloss={scores_by_arm.loc['F_ensemble_spread', 'logloss']:.4f}；"
        f"相对 zero-bias pooled Δ={f_vs_b.delta:+.4f}（95% CI {f_vs_b.ci_low:+.4f}..{f_vs_b.ci_high:+.4f}）。"
        f"相对 market Δ={f_vs_market.delta:+.4f}（{f_vs_market.ci_low:+.4f}..{f_vs_market.ci_high:+.4f}）。"
    )
    enrichment_dates = set(
        scored.loc[scored["arm"] == "H_archive_known_multimodel", "target_date"]
    )
    if not enrichment_dates:
        raise ValueError("archive-known multimodel challenger has no scoreable holdout dates")
    enrichment_scored = scored.loc[scored["target_date"].isin(enrichment_dates)].copy()
    enrichment_comparison = date_equal(enrichment_scored)
    enrichment_deltas = pd.DataFrame(
        [
            bootstrap_delta(enrichment_scored, left, right, metric)
            for metric in ("logloss", "brier", "rps")
            for left, right in (
                ("H_archive_known_multimodel", "F_ensemble_spread"),
                ("H_archive_known_multimodel", "market"),
            )
        ]
    )
    h_vs_f_logloss = enrichment_deltas.loc[
        (enrichment_deltas["metric"] == "logloss")
        & (enrichment_deltas["left"] == "H_archive_known_multimodel")
        & (enrichment_deltas["right"] == "F_ensemble_spread")
    ].iloc[0]
    h_vs_market_logloss = enrichment_deltas.loc[
        (enrichment_deltas["metric"] == "logloss")
        & (enrichment_deltas["left"] == "H_archive_known_multimodel")
        & (enrichment_deltas["right"] == "market")
    ].iloc[0]
    summary = {
        "schema_version": "d1_legacy_weather_only_v2",
        "run_id": f"history_policy_{args.history_policy}",
        "history_policy": args.history_policy,
        "training": {
            "lineage": "legacy_daily_cache_non_strict_pit_training_prior",
            "denominator": base.history_denominator_funnel(
                history, min(target_dates), input_artifact=args.history
            ),
            "rows": int(len(fitted["train"])),
            "cities": int(fitted["train"]["city"].nunique()),
            "dates": int(fitted["train"]["date"].nunique()),
            "seasonal_harmonic": bool(fitted["seasonal_harmonic"]),
        },
        "development": {"dates": len(development_dates), "states": len(development_states)},
        "holdout": {
            "dates": len(holdout_dates),
            "states": len(holdout_states),
            "cities": len({state["city"] for state in holdout_states}),
            "start": min(holdout_dates),
            "end": max(holdout_dates),
            "lineage": "single_run_reconstructed_conservative_12h_lag",
        },
        "partial_hierarchy": fitted["lambda_selection"],
        "f_overlay": overlay,
        "funnels": funnels,
        "weather_gate": {
            "significance": significance,
            "calibration": "legacy_holdout_only_not_clean_forward",
            "market_residual": "not_run_by_contract",
        },
        "physical_width_status": "blocked_missing_same_clock_legacy_features",
        "enrichment_history": {
            "lineage": "archive_known_2026-07-08_not_collector_exact_run",
            "available_date": ENRICHMENT_AVAILABLE_DATE,
            **{key: enrichment_fitted[key] for key in ("rows", "dates", "cities", "models")},
            "lambda_selection": enrichment_fitted["lambda_selection"],
        },
        "archive_known_multimodel_comparison": {
            "dates": len(enrichment_dates),
            "start": min(enrichment_dates),
            "end": max(enrichment_dates),
            "h_vs_f_logloss": h_vs_f_logloss.to_dict(),
            "h_vs_market_logloss": h_vs_market_logloss.to_dict(),
            "scores": enrichment_comparison.to_dict("records"),
            "paired_deltas": enrichment_deltas.to_dict("records"),
        },
        "conclusion": conclusion,
        "scores": score_summary.to_dict("records"),
        "diagnostics": {row["arm"]: row for row in diagnostics.to_dict("records")},
        "paired_deltas": deltas.to_dict("records"),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    scored.to_csv(args.out / "holdout_scored_states.csv", index=False)
    score_summary.to_csv(args.out / "holdout_score_summary.csv", index=False)
    enrichment_comparison.to_csv(
        args.out / "archive_known_multimodel_score_summary.csv", index=False
    )
    enrichment_deltas.to_csv(
        args.out / "archive_known_multimodel_paired_date_bootstrap.csv", index=False
    )
    deltas.to_csv(args.out / "holdout_paired_date_bootstrap.csv", index=False)
    calibration(scored).to_csv(args.out / "holdout_calibration.csv", index=False)
    diagnostics.to_csv(args.out / "holdout_probability_diagnostics.csv", index=False)
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.report.write_text(
        render_report(summary, score_summary, deltas, enrichment_comparison),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
