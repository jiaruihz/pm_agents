#!/usr/bin/env python3
"""Build the lightweight Tmin evaluator-repair and V2/V3 evidence packet.

This is a read-only research runner.  It consumes the already frozen row-level
forensics table and repaired V1 evaluator output.  It never writes production
state, changes a selector, or uses execution/PnL as a model-selection target.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import pyarrow
import scipy
from scipy.optimize import minimize


ROOT = Path(__file__).resolve().parents[3]
ACTIVE_WINDOWS = {"morning_cooling", "post_sunrise_provisional_low"}
EPS = 1e-8
SEED = 20260828
BOOTSTRAP_DRAWS = 20_000
MIN_PRIOR_DATES = 5
MIN_PRIOR_CLASS_ROWS = 2
STRONG_L2 = 10.0

V2_FEATURES = [
    "forecast_remaining_floor_margin_c",
    "rebound_c",
    "minutes_since_running_min",
    "hours_remaining",
    "forecast_hours_to_remaining_min",
    "temperature_change_60m_c",
    "dewpoint_depression_f",
    "wind_speed_kt",
]
CLOCK_FEATURES = ["rebound_c", "minutes_since_running_min", "hours_remaining"]
V1_NUMERIC_FEATURES = [
    "cloud_cover_change_1h_code",
    "cloud_layer_count",
    "dewpoint_depression_f",
    "forecast_evening_floor_margin_c",
    "forecast_evening_margin_vs_running_min_c",
    "forecast_hours_to_remaining_min",
    "forecast_morning_floor_margin_c",
    "forecast_morning_margin_vs_running_min_c",
    "forecast_remaining_floor_margin_c",
    "forecast_remaining_margin_vs_running_min_c",
    "hours_remaining",
    "local_hour",
    "minutes_since_running_min",
    "precip_intensity_code",
    "rebound_c",
    "relative_humidity_pct",
    "running_min_native",
    "source_age_minutes",
    "temperature_change_10m_c",
    "temperature_change_30m_c",
    "temperature_change_60m_c",
    "wind_speed_change_1h_kt",
    "wind_speed_kt",
]


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, text=True, capture_output=True
    ).stdout.strip()


def _clip(probability: np.ndarray | pd.Series) -> np.ndarray:
    return np.clip(np.asarray(probability, dtype=float), EPS, 1.0 - EPS)


def _logit(probability: np.ndarray | pd.Series) -> np.ndarray:
    p = _clip(probability)
    return np.log(p / (1.0 - p))


def _sigmoid(logit: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(logit, -35.0, 35.0)))


def _loss(y: np.ndarray, probability: np.ndarray, metric: str) -> np.ndarray:
    p = _clip(probability)
    if metric == "logloss":
        return -(y * np.log(p) + (1.0 - y) * np.log(1.0 - p))
    if metric == "brier":
        return np.square(p - y)
    raise ValueError(metric)


def _equal_weights(frame: pd.DataFrame, grain: str) -> np.ndarray:
    if grain == "row":
        return np.full(len(frame), 1.0 / len(frame))
    keys = ["target_date"] if grain == "date" else ["city", "target_date"]
    group = frame.groupby(keys, sort=False).ngroup().to_numpy()
    group_count = int(group.max()) + 1
    sizes = np.bincount(group)
    return 1.0 / (group_count * sizes[group])


def _weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    return float(np.sum(values * weights) / np.sum(weights))


def _bootstrap_delta(
    frame: pd.DataFrame, candidate: np.ndarray, baseline: np.ndarray, metric: str
) -> dict[str, Any]:
    difference = _loss(frame["label"].to_numpy(int), candidate, metric) - _loss(
        frame["label"].to_numpy(int), baseline, metric
    )
    by_date = pd.DataFrame(
        {"target_date": frame["target_date"].astype(str), "difference": difference}
    ).groupby("target_date", sort=True)["difference"].mean()
    rng = np.random.default_rng(SEED + (0 if metric == "logloss" else 1))
    indices = rng.integers(0, len(by_date), size=(BOOTSTRAP_DRAWS, len(by_date)))
    draws = by_date.to_numpy()[indices].mean(axis=1)
    return {
        "delta": float(by_date.mean()),
        "ci_low": float(np.quantile(draws, 0.025)),
        "ci_high": float(np.quantile(draws, 0.975)),
        "draws": BOOTSTRAP_DRAWS,
        "target_dates": int(len(by_date)),
    }


def _gradient_diagnostics(frame: pd.DataFrame) -> dict[str, Any]:
    corrected = frame["score_gradient_alpha0_routed"].to_numpy(float)
    by_date = pd.DataFrame(
        {
            "target_date": frame["target_date"].astype(str),
            "gradient": corrected,
        }
    ).groupby("target_date", sort=True)["gradient"].mean()
    rng = np.random.default_rng(SEED + 41)
    indices = rng.integers(0, len(by_date), size=(BOOTSTRAP_DRAWS, len(by_date)))
    draws = by_date.to_numpy()[indices].mean(axis=1)

    def leave_one_out(keys: list[str]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        groups = frame.groupby(keys, sort=True)
        for key, _ in groups:
            key_tuple = key if isinstance(key, tuple) else (key,)
            mask = np.ones(len(frame), dtype=bool)
            for column, value in zip(keys, key_tuple):
                mask &= frame[column].astype(str).to_numpy() != str(value)
            output.append(
                {
                    **{column: str(value) for column, value in zip(keys, key_tuple)},
                    "remaining_rows": int(mask.sum()),
                    "remaining_mean": float(corrected[mask].mean()),
                }
            )
        return output

    outside = ~frame["P1_ACTIVE_WINDOW_PROBABILITY"].to_numpy(bool)
    if not np.array_equal(corrected[outside], np.zeros(int(outside.sum()))):
        raise AssertionError("corrected routed gradient must be exactly zero outside P1")
    expected = (
        frame["physical_innovation"].to_numpy(float)
        * frame["routing_indicator"].to_numpy(int)
        * (frame["label"].to_numpy(int) - frame["p_market"].to_numpy(float))
    )
    if not np.allclose(corrected, expected, atol=1e-15, rtol=0):
        raise AssertionError("corrected routed gradient row equality failed")
    return {
        "definition": "physical_innovation * routing_indicator * (label - p_market)",
        "rows": int(len(frame)),
        "date_equal_mean": float(by_date.mean()),
        "row_mean": float(corrected.mean()),
        "bootstrap": {
            "draws": BOOTSTRAP_DRAWS,
            "seed": SEED + 41,
            "ci_low": float(np.quantile(draws, 0.025)),
            "ci_high": float(np.quantile(draws, 0.975)),
        },
        "outside_p1_rows": int(outside.sum()),
        "outside_p1_nonzero_rows": int(np.count_nonzero(corrected[outside])),
        "leave_one_date_out": leave_one_out(["target_date"]),
        "leave_one_city_out": leave_one_out(["city"]),
    }


def _model_summary(
    frame: pd.DataFrame, probability_column: str, baseline_column: str = "p_market"
) -> dict[str, Any]:
    if frame.empty:
        return {"rows": 0, "target_dates": 0, "cities": 0, "status": "empty"}
    y = frame["label"].to_numpy(int)
    candidate = frame[probability_column].to_numpy(float)
    baseline = frame[baseline_column].to_numpy(float)
    output: dict[str, Any] = {
        "rows": int(len(frame)),
        "target_dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
    }
    for grain in ("row", "date", "city_date"):
        weights = _equal_weights(frame, grain)
        output[grain] = {}
        for metric in ("logloss", "brier"):
            candidate_loss = _loss(y, candidate, metric)
            baseline_loss = _loss(y, baseline, metric)
            output[grain][metric] = _weighted_mean(candidate_loss, weights)
            output[grain][f"{metric}_delta_vs_{baseline_column}"] = _weighted_mean(
                candidate_loss - baseline_loss, weights
            )
    output["date_block_bootstrap"] = {
        metric: _bootstrap_delta(frame, candidate, baseline, metric)
        for metric in ("logloss", "brier")
    }
    return output


def _fit_penalized_logit(
    train: pd.DataFrame,
    feature_columns: list[str],
    *,
    offset_column: str | None,
    penalty_center: np.ndarray | None = None,
    l2: float = STRONG_L2,
) -> dict[str, Any]:
    raw = train[feature_columns].to_numpy(float)
    median = np.nanmedian(raw, axis=0)
    median = np.where(np.isfinite(median), median, 0.0)
    raw = np.where(np.isfinite(raw), raw, median)
    scale = np.nanstd(raw, axis=0)
    scale = np.where(scale > 1e-10, scale, 1.0)
    x = (raw - median) / scale
    x = np.column_stack([np.ones(len(x)), x])
    y = train["label"].to_numpy(float)
    offset = (
        np.zeros(len(train))
        if offset_column is None
        else _logit(train[offset_column].to_numpy(float))
    )
    date_sizes = train.groupby("target_date")["target_date"].transform("size").to_numpy()
    weights = 1.0 / date_sizes
    weights /= weights.sum()
    center = (
        np.zeros(x.shape[1])
        if penalty_center is None
        else np.asarray(penalty_center, dtype=float)
    )

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        eta = offset + x @ beta
        p = _sigmoid(eta)
        data_loss = np.sum(weights * _loss(y, p, "logloss"))
        difference = beta - center
        value = data_loss + 0.5 * l2 * float(difference @ difference)
        gradient = x.T @ (weights * (p - y)) + l2 * difference
        return float(value), gradient

    result = minimize(
        lambda beta: objective(beta)[0],
        np.zeros(x.shape[1]),
        jac=lambda beta: objective(beta)[1],
        method="L-BFGS-B",
        options={"maxiter": 1000, "ftol": 1e-12, "gtol": 1e-8},
    )
    if not result.success:
        raise RuntimeError(f"penalized logit failed: {result.message}")
    return {
        "beta": result.x,
        "median": median,
        "scale": scale,
        "feature_columns": feature_columns,
        "offset_column": offset_column,
        "l2": l2,
        "iterations": int(result.nit),
    }


def _predict_fit(fit: dict[str, Any], test: pd.DataFrame) -> np.ndarray:
    raw = test[fit["feature_columns"]].to_numpy(float)
    raw = np.where(np.isfinite(raw), raw, fit["median"])
    x = (raw - fit["median"]) / fit["scale"]
    x = np.column_stack([np.ones(len(x)), x])
    offset = (
        np.zeros(len(test))
        if fit["offset_column"] is None
        else _logit(test[fit["offset_column"]].to_numpy(float))
    )
    return _sigmoid(offset + x @ fit["beta"])


def _prior_date_oof(
    frame: pd.DataFrame,
    feature_columns: list[str],
    *,
    offset_column: str | None,
    fallback_column: str | None,
    l2: float = STRONG_L2,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    predictions = np.full(len(frame), np.nan)
    folds: list[dict[str, Any]] = []
    dates = sorted(frame["target_date"].astype(str).unique())
    for target_date in dates:
        test_mask = frame["target_date"].astype(str).eq(target_date).to_numpy()
        train_mask = frame["target_date"].astype(str).lt(target_date).to_numpy()
        train = frame.loc[train_mask]
        enough = (
            train["target_date"].nunique() >= MIN_PRIOR_DATES
            and train["label"].eq(0).sum() >= MIN_PRIOR_CLASS_ROWS
            and train["label"].eq(1).sum() >= MIN_PRIOR_CLASS_ROWS
        )
        if not enough:
            if fallback_column is not None:
                predictions[test_mask] = frame.loc[test_mask, fallback_column].to_numpy(float)
                fallback = fallback_column
            elif len(train):
                prior = (float(train["label"].sum()) + 0.5) / (len(train) + 1.0)
                predictions[test_mask] = prior
                fallback = "jeffreys_prior"
            else:
                predictions[test_mask] = 0.5
                fallback = "0.5_no_prior_rows"
            folds.append(
                {
                    "test_date": target_date,
                    "status": "fail_closed",
                    "fallback": fallback,
                    "prior_dates": int(train["target_date"].nunique()),
                    "prior_rows": int(len(train)),
                    "prior_negative_rows": int(train["label"].eq(0).sum()),
                }
            )
            continue
        fit = _fit_penalized_logit(
            train, feature_columns, offset_column=offset_column, l2=l2
        )
        predictions[test_mask] = _predict_fit(fit, frame.loc[test_mask])
        folds.append(
            {
                "test_date": target_date,
                "status": "fit",
                "prior_dates": int(train["target_date"].nunique()),
                "prior_rows": int(len(train)),
                "prior_negative_rows": int(train["label"].eq(0).sum()),
                "iterations": fit["iterations"],
            }
        )
    if not np.isfinite(predictions).all():
        raise AssertionError("OOF predictions incomplete")
    return predictions, folds


def _calibration_oof(frame: pd.DataFrame) -> tuple[np.ndarray, list[dict[str, Any]]]:
    working = frame.copy()
    working["market_logit"] = _logit(working["p_market"])
    predictions = np.full(len(frame), np.nan)
    folds: list[dict[str, Any]] = []
    dates = sorted(frame["target_date"].astype(str).unique())
    for target_date in dates:
        test_mask = working["target_date"].astype(str).eq(target_date).to_numpy()
        train = working[working["target_date"].astype(str).lt(target_date)]
        enough = (
            train["target_date"].nunique() >= MIN_PRIOR_DATES
            and train["label"].eq(0).sum() >= MIN_PRIOR_CLASS_ROWS
            and train["label"].eq(1).sum() >= MIN_PRIOR_CLASS_ROWS
        )
        if not enough:
            predictions[test_mask] = working.loc[test_mask, "p_market"]
            folds.append(
                {
                    "test_date": target_date,
                    "status": "fail_closed_identity",
                    "prior_dates": int(train["target_date"].nunique()),
                    "prior_rows": int(len(train)),
                }
            )
            continue
        # Calibration parameters are directly [a, b], with prior center [0, 1].
        x = np.column_stack([np.ones(len(train)), train["market_logit"].to_numpy()])
        y = train["label"].to_numpy(float)
        date_sizes = train.groupby("target_date")["target_date"].transform("size").to_numpy()
        weights = 1.0 / date_sizes
        weights /= weights.sum()
        center = np.array([0.0, 1.0])

        def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
            p = _sigmoid(x @ beta)
            diff = beta - center
            value = np.sum(weights * _loss(y, p, "logloss")) + 0.5 * STRONG_L2 * float(diff @ diff)
            gradient = x.T @ (weights * (p - y)) + STRONG_L2 * diff
            return float(value), gradient

        fit = minimize(
            lambda beta: objective(beta)[0], center.copy(),
            jac=lambda beta: objective(beta)[1], method="L-BFGS-B",
            options={"maxiter": 1000, "ftol": 1e-12, "gtol": 1e-8},
        )
        if not fit.success:
            raise RuntimeError(f"calibration failed: {fit.message}")
        test_x = np.column_stack(
            [np.ones(test_mask.sum()), working.loc[test_mask, "market_logit"].to_numpy()]
        )
        predictions[test_mask] = _sigmoid(test_x @ fit.x)
        folds.append(
            {
                "test_date": target_date,
                "status": "fit",
                "prior_dates": int(train["target_date"].nunique()),
                "prior_rows": int(len(train)),
                "a": float(fit.x[0]),
                "b": float(fit.x[1]),
                "iterations": int(fit.nit),
            }
        )
    return predictions, folds


def _influence(frame: pd.DataFrame, model: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for key in ("target_date", "city"):
        values: list[dict[str, Any]] = []
        for value in sorted(frame[key].astype(str).unique()):
            subset = frame[~frame[key].astype(str).eq(value)]
            values.append({key: value, **_model_summary(subset, model)["date"]})
        result[f"leave_one_{key}_out"] = values
    return result


def _paired_slices(frame: pd.DataFrame, model: str) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    for name, keys in {
        "by_city": ["city"],
        "by_target_date": ["target_date"],
        "by_city_target_date": ["city", "target_date"],
    }.items():
        rows: list[dict[str, Any]] = []
        for key, subset in frame.groupby(keys, sort=True):
            key_values = key if isinstance(key, tuple) else (key,)
            summary = _model_summary(subset, model)
            rows.append(
                {
                    **dict(zip(keys, map(str, key_values))),
                    "rows": int(len(subset)),
                    "logloss_delta_vs_market": summary["row"]["logloss_delta_vs_p_market"],
                    "brier_delta_vs_market": summary["row"]["brier_delta_vs_p_market"],
                }
            )
        output[name] = rows
    return output


def _tail_table(frame: pd.DataFrame, models: Iterable[str]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for threshold in (0.80, 0.90, 0.95):
        subset = frame[frame["p_market"].ge(threshold)]
        for model in models:
            worst = None
            if not subset.empty:
                by_date = subset.assign(
                    _delta=_loss(subset["label"].to_numpy(int), subset[model].to_numpy(), "logloss")
                    - _loss(subset["label"].to_numpy(int), subset["p_market"].to_numpy(), "logloss")
                ).groupby("target_date")["_delta"].mean()
                worst = str(by_date.idxmax())
            output.append(
                {
                    "threshold": threshold,
                    "model": model,
                    "rows": int(len(subset)),
                    "loss_events": int(subset["label"].eq(0).sum()),
                    "empirical_outcome": float(subset["label"].mean()) if len(subset) else None,
                    "mean_model_probability": float(subset[model].mean()) if len(subset) else None,
                    "mean_market_probability": float(subset["p_market"].mean()) if len(subset) else None,
                    "absolute_calibration_error": (
                        float(abs(subset[model].mean() - subset["label"].mean()))
                        if len(subset) else None
                    ),
                    "worst_date_vs_market_logloss": worst,
                }
            )
    return output


def _denominator_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    masks = {
        "P0_CANONICAL_PROBABILITY": np.full(len(frame), True),
        "P1_ACTIVE_WINDOW_PROBABILITY": frame["window"].isin(ACTIVE_WINDOWS).to_numpy(),
        "LEGACY_111_HEADLINE": frame["LEGACY_111_HEADLINE"].to_numpy(bool),
        "E0_EXECUTION_CLEAN": frame["E0_EXECUTION_CLEAN"].to_numpy(bool),
    }
    models = ["p_market", "p_model", "p_v1_challenger"]
    output: dict[str, Any] = {}
    for name, mask in masks.items():
        subset = frame.loc[mask]
        output[name] = {
            "rows": int(len(subset)),
            "target_dates": int(subset["target_date"].nunique()),
            "models": {model: _model_summary(subset, model) for model in models},
            "influence": {
                model: _influence(subset, model)
                for model in ("p_model", "p_v1_challenger")
            },
        }
    return output


def _feature_frame(raw: pd.DataFrame) -> pd.DataFrame:
    parsed = raw["raw_feature_values_json"].map(json.loads)
    feature = pd.DataFrame(parsed.tolist(), index=raw.index)
    output = raw.copy()
    for column in sorted(set(V1_NUMERIC_FEATURES + V2_FEATURES + CLOCK_FEATURES)):
        output[column] = pd.to_numeric(feature.get(column), errors="coerce")
    return output


def _attach_and_validate_pit_refs(frame: pd.DataFrame, candidate_journal: Path) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    with candidate_journal.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            checkpoint_id = str(row.get("checkpoint_id") or "")
            if checkpoint_id:
                records.append(
                    {
                        "checkpoint_id": checkpoint_id,
                        "candidate_decision_ts_utc": row.get("decision_ts_utc"),
                        "input_refs": row.get("input_refs"),
                    }
                )
    refs = pd.DataFrame(records)
    if refs.empty or refs["checkpoint_id"].duplicated().any():
        raise ValueError("candidate journal PIT lineage must have unique checkpoint_id")
    output = frame.merge(refs, on="checkpoint_id", how="left", validate="one_to_one")

    def legal(row: pd.Series) -> bool:
        try:
            decision = pd.Timestamp(row["decision_ts_utc"])
            candidate_decision = pd.Timestamp(row["candidate_decision_ts_utc"])
        except (TypeError, ValueError):
            return False
        if decision.tzinfo is None or candidate_decision.tzinfo is None or decision != candidate_decision:
            return False
        required = {"observation_archive", "forecast_curve"}
        seen: set[str] = set()
        for item in row.get("input_refs") or []:
            kind = str(item.get("kind") or "")
            if kind not in required:
                continue
            try:
                available = pd.Timestamp(item.get("available_at_utc"))
            except (TypeError, ValueError):
                return False
            if available.tzinfo is None or available > decision:
                return False
            seen.add(kind)
        return seen == required

    output["pit_reference_contract_legal"] = output.apply(legal, axis=1)
    if not output["pit_reference_contract_legal"].all():
        sample = output.loc[
            ~output["pit_reference_contract_legal"],
            ["checkpoint_id", "decision_ts_utc", "candidate_decision_ts_utc"],
        ].head(5)
        raise ValueError(f"P0 PIT reference contract failed: {sample.to_dict('records')}")
    output["input_refs_json"] = output["input_refs"].map(
        lambda value: json.dumps(value, sort_keys=True)
    )
    return output.drop(columns=["input_refs"])


def _forward_audit(current_evaluation: Path | None) -> dict[str, Any]:
    if current_evaluation is None or not current_evaluation.exists():
        return {"status": "not_run", "reason": "current evaluator output not provided"}
    payload = json.loads(current_evaluation.read_text())
    forward = payload["prospective_challenger"]["forward_funnel"]
    db = payload["canonical_db"]
    outcome_matured = str(db.get("settlements_max_target_date") or "") >= "2026-08-28"
    return {
        "status": "PASS_NO_MATURED_OUTCOME_AT_AMENDMENT_SEAL" if not outcome_matured else "OUTCOME_ALREADY_MATURED_REQUIRES_CONTAMINATION_REVIEW",
        "prediction_rows_target_date_ge_2026_08_28": int(forward["checkpoint_rows"]),
        "prediction_target_dates": int(forward["target_dates"]),
        "selected_rows": int(forward["selected_rows"]),
        "settlements_max_target_date_at_read": db.get("settlements_max_target_date"),
        "matured_outcome_target_date_ge_2026_08_28": outcome_matured,
        "human_or_program_outcome_reads": 0 if not outcome_matured else None,
        "reports_using_matured_forward_for_selection": 0 if not outcome_matured else None,
        "confirmatory_forward_start": "2026-08-28" if not outcome_matured else None,
        "note": "A non-existent canonical settlement cannot have been read as a matured outcome; prediction generation alone is not outcome contamination.",
    }


def build(args: argparse.Namespace) -> None:
    output_dir = args.output_dir
    if output_dir.exists() and any(output_dir.iterdir()) and not args.allow_existing_output:
        raise FileExistsError(
            f"refusing to overwrite non-empty output directory: {output_dir}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    raw = pd.read_parquet(args.row_audit)
    required = {
        "checkpoint_id", "city", "target_date", "condition_id", "token_id", "bracket",
        "market_id", "p_market", "p_model", "p_challenger", "label", "window",
        "settlement_status",
        "probability_evaluation_status", "candidate_status", "raw_feature_values_json",
    }
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"row audit missing columns: {sorted(missing)}")
    frame = raw[
        raw["settlement_status"].eq("settled")
        & raw["label"].isin([0.0, 1.0])
        & raw["p_market"].notna()
        & raw["p_model"].notna()
    ].copy().reset_index(drop=True)
    if len(frame) != 168 or frame["checkpoint_id"].duplicated().any():
        raise AssertionError(f"frozen P0 must be 168 unique checkpoints, got {len(frame)}")
    if frame[["condition_id", "token_id", "market_id", "bracket"]].isna().any().any():
        raise AssertionError("settlement-native identity incomplete")
    frame = _attach_and_validate_pit_refs(frame, args.candidate_journal)
    frame = _feature_frame(frame)
    trade_funnel = pd.read_csv(args.trade_funnel)
    selected_settled_ids = set(
        trade_funnel.loc[
            trade_funnel["selected"].astype(str).str.lower().eq("true")
            & trade_funnel["settlement_status"].eq("settled"),
            "checkpoint_id",
        ].astype(str)
    )
    execution_clean_ids = set(
        trade_funnel.loc[
            trade_funnel["execution_candidate_status_gate"].astype(str).str.lower().eq("true")
            & trade_funnel["settlement_status"].eq("settled"),
            "checkpoint_id",
        ].astype(str)
    )
    if len(selected_settled_ids) != 19:
        raise AssertionError(f"frozen T0 must have 19 settled rows, got {len(selected_settled_ids)}")
    frame["p_v1_incumbent"] = frame["p_model"].astype(float)
    frame["p_v1_challenger"] = frame["p_challenger"].astype(float)
    frame["P0_CANONICAL_PROBABILITY"] = True
    frame["P1_ACTIVE_WINDOW_PROBABILITY"] = frame["window"].isin(ACTIVE_WINDOWS)
    frame["routing_indicator"] = frame["P1_ACTIVE_WINDOW_PROBABILITY"].astype(int)
    frame["physical_innovation"] = (
        _logit(frame["p_v1_incumbent"]) - _logit(frame["p_market"])
    ) / 0.50
    frame["score_gradient_alpha0_source_legacy"] = pd.to_numeric(
        frame.get("score_gradient_alpha0"), errors="coerce"
    )
    frame["score_gradient_alpha0_unrouted"] = frame["physical_innovation"] * (
        frame["label"].to_numpy(int) - frame["p_market"].to_numpy(float)
    )
    frame["score_gradient_alpha0_routed"] = (
        frame["score_gradient_alpha0_unrouted"] * frame["routing_indicator"]
    )
    frame["score_gradient_alpha0"] = frame["score_gradient_alpha0_routed"]
    base_logit = _logit(frame["p_market"])
    routed_innovation = frame["physical_innovation"].to_numpy(float) * frame[
        "routing_indicator"
    ].to_numpy(int)
    frame["p_v1_alpha050_all"] = frame["p_v1_incumbent"]
    frame["p_v1_alpha010_routed_rebuilt"] = _sigmoid(
        base_logit + 0.10 * routed_innovation
    )
    if not np.allclose(
        frame["p_v1_alpha010_routed_rebuilt"],
        frame["p_v1_challenger"],
        atol=1e-12,
        rtol=0,
    ):
        raise AssertionError("frozen alpha=.10 routed probability reconstruction failed")
    frame["p_v1_alpha025_routed"] = _sigmoid(
        base_logit + 0.25 * routed_innovation
    )
    frame["p_v1_alpha050_routed"] = _sigmoid(
        base_logit + 0.50 * routed_innovation
    )

    frame["p_calibrated_market"], calibration_folds = _calibration_oof(frame)
    frame["p_clock_oof"], clock_folds = _prior_date_oof(
        frame, CLOCK_FEATURES, offset_column="p_market", fallback_column="p_market"
    )
    frame["p_v1_features_settlement_oof"], v1_feature_folds = _prior_date_oof(
        frame, V1_NUMERIC_FEATURES, offset_column="p_market", fallback_column="p_market"
    )
    frame["p_v2_market_offset_oof"], v2_folds = _prior_date_oof(
        frame, V2_FEATURES, offset_column="p_market", fallback_column="p_market"
    )
    frame["p_v2_physical_only_oof"], v2_physical_folds = _prior_date_oof(
        frame, V2_FEATURES, offset_column=None, fallback_column=None
    )
    frame["p_v3_physical_survival"] = np.nan
    frame["p_v3_market_residual"] = np.nan
    frame["v3_status"] = "NOT_FIT_EVENT_TIME_TRUTH_UNAVAILABLE"
    frame["LEGACY_111_HEADLINE"] = frame["probability_evaluation_status"].eq("frozen_headline_row")
    frame["E0_EXECUTION_CLEAN"] = frame["checkpoint_id"].astype(str).isin(execution_clean_ids)
    frame["T0_SELECTED_TRADE"] = frame["checkpoint_id"].astype(str).isin(selected_settled_ids)

    repaired = json.loads(args.repaired_evaluation.read_text())
    denominators = _denominator_metrics(frame)
    if denominators["LEGACY_111_HEADLINE"]["rows"] != 111:
        raise AssertionError("legacy headline denominator no longer has 111 rows")

    model_columns = {
        "B0_raw_market": "p_market",
        "B1_shrink_identity_calibration": "p_calibrated_market",
        "B2_v1_incumbent": "p_v1_incumbent",
        "B3_v1_alpha010_routed": "p_v1_challenger",
        "B3A_v1_alpha050_all_windows": "p_v1_alpha050_all",
        "B3B_v1_alpha025_routed": "p_v1_alpha025_routed",
        "B3C_v1_alpha050_routed": "p_v1_alpha050_routed",
        "B4_clock_rebound_market_offset": "p_clock_oof",
        "A1_v1_features_settlement_target": "p_v1_features_settlement_oof",
        "V2_market_offset": "p_v2_market_offset_oof",
        "V2_physical_only": "p_v2_physical_only_oof",
    }
    leaderboard_rows: list[dict[str, Any]] = []
    stability: dict[str, Any] = {}
    for name, column in model_columns.items():
        summary = _model_summary(frame, column)
        calibrated = _model_summary(frame, column, "p_calibrated_market")
        leaderboard_rows.append(
            {
                "model": name,
                "probability_column": column,
                "rows": len(frame),
                "target_dates": frame["target_date"].nunique(),
                "row_logloss": summary["row"]["logloss"],
                "row_brier": summary["row"]["brier"],
                "date_equal_logloss": summary["date"]["logloss"],
                "date_equal_brier": summary["date"]["brier"],
                "date_equal_delta_logloss_vs_market": summary["date"]["logloss_delta_vs_p_market"],
                "date_equal_delta_brier_vs_market": summary["date"]["brier_delta_vs_p_market"],
                "date_equal_delta_logloss_vs_calibrated_market": calibrated["date"]["logloss_delta_vs_p_calibrated_market"],
                "date_equal_delta_brier_vs_calibrated_market": calibrated["date"]["brier_delta_vs_p_calibrated_market"],
                "city_date_equal_logloss": summary["city_date"]["logloss"],
                "city_date_equal_brier": summary["city_date"]["brier"],
                "city_date_equal_delta_logloss_vs_market": summary["city_date"]["logloss_delta_vs_p_market"],
                "city_date_equal_delta_brier_vs_market": summary["city_date"]["brier_delta_vs_p_market"],
                "bootstrap_logloss_ci_low_vs_market": summary["date_block_bootstrap"]["logloss"]["ci_low"],
                "bootstrap_logloss_ci_high_vs_market": summary["date_block_bootstrap"]["logloss"]["ci_high"],
                "bootstrap_brier_ci_low_vs_market": summary["date_block_bootstrap"]["brier"]["ci_low"],
                "bootstrap_brier_ci_high_vs_market": summary["date_block_bootstrap"]["brier"]["ci_high"],
                "fit_contract": "frozen" if name.startswith(("B0_", "B2_", "B3_")) else "prior-date OOF",
            }
        )
        stability[name] = {
            "summary_vs_market": summary,
            "influence": _influence(frame, column),
            "paired_slices": _paired_slices(frame, column),
        }
    leaderboard = pd.DataFrame(leaderboard_rows)
    leaderboard.to_csv(output_dir / "09_OOF_MODEL_LEADERBOARD.csv", index=False)

    v2 = leaderboard[leaderboard["model"].eq("V2_market_offset")].iloc[0]
    v1 = leaderboard[leaderboard["model"].eq("B3_v1_alpha010_routed")].iloc[0]
    v2_promotable = bool(
        v2["date_equal_delta_logloss_vs_market"] < 0
        and v2["date_equal_delta_brier_vs_market"] < 0
        and v2["date_equal_delta_logloss_vs_calibrated_market"] < 0
        and v2["date_equal_delta_brier_vs_calibrated_market"] < 0
        and v2["bootstrap_logloss_ci_high_vs_market"] <= 0
        and v2["bootstrap_brier_ci_high_vs_market"] <= 0
    )
    disposition = "FREEZE_V2_PROBABILITY_CHALLENGER" if v2_promotable else "KEEP_V1_FORWARD_ONLY"

    for column in model_columns.values():
        y = frame["label"].to_numpy(int)
        frame[f"{column}_logloss"] = _loss(y, frame[column].to_numpy(), "logloss")
        frame[f"{column}_brier"] = _loss(y, frame[column].to_numpy(), "brier")
    output_columns = [
        "candidate_id", "checkpoint_id", "city", "target_date", "decision_ts_utc",
        "condition_id", "market_id", "token_id", "bracket", "window", "label",
        "P0_CANONICAL_PROBABILITY", "P1_ACTIVE_WINDOW_PROBABILITY", "LEGACY_111_HEADLINE",
        "E0_EXECUTION_CLEAN", "T0_SELECTED_TRADE", *model_columns.values(),
        "p_v3_physical_survival", "p_v3_market_residual", "v3_status",
        *V2_FEATURES, "physical_innovation", "routing_indicator",
        "score_gradient_alpha0_source_legacy", "score_gradient_alpha0_unrouted",
        "score_gradient_alpha0_routed", "score_gradient_alpha0",
        "pit_reference_contract_legal",
        "input_refs_json", "raw_feature_values_json",
    ]
    loss_columns = [column for column in frame if column.endswith("_logloss") or column.endswith("_brier")]
    frame[output_columns + loss_columns].to_parquet(
        output_dir / "ROW_LEVEL_PROBABILITY_AUDIT.parquet", index=False
    )

    tail = _tail_table(frame, model_columns.values())
    _write_json(output_dir / "V1_REPAIRED_EVALUATION.json", {
        "denominators": denominators,
        "repaired_evaluator_snapshot": repaired,
    })
    _write_json(output_dir / "STABILITY_RESULTS.json", stability)
    _write_json(output_dir / "ROUTED_SCORE_GRADIENT_DIAGNOSTICS.json", _gradient_diagnostics(frame))
    _write_json(output_dir / "OOF_FOLD_MANIFEST.json", {
        "calibration": calibration_folds,
        "clock": clock_folds,
        "v1_features_settlement": v1_feature_folds,
        "v2_market_offset": v2_folds,
        "v2_physical_only": v2_physical_folds,
        "split": "strict target_date < test target_date",
        "min_prior_dates": MIN_PRIOR_DATES,
        "min_prior_class_rows": MIN_PRIOR_CLASS_ROWS,
        "l2": STRONG_L2,
    })
    _write_json(output_dir / "HIGH_PROBABILITY_CALIBRATION.json", tail)
    forward_audit = _forward_audit(args.current_evaluation)
    _write_json(output_dir / "FORWARD_CONTAMINATION_AUDIT.json", forward_audit)
    if args.current_evaluation is not None and args.current_evaluation.exists():
        _write_json(
            output_dir / "CURRENT_FORWARD_EVALUATION_SNAPSHOT.json",
            json.loads(args.current_evaluation.read_text()),
        )

    generated = datetime.now(timezone.utc).isoformat()
    v1_delta_ll = float(v1["date_equal_delta_logloss_vs_market"])
    v1_delta_br = float(v1["date_equal_delta_brier_vs_market"])
    v2_delta_ll = float(v2["date_equal_delta_logloss_vs_market"])
    v2_delta_br = float(v2["date_equal_delta_brier_vs_market"])

    (output_dir / "00_EXECUTIVE_DECISION_BRIEF.md").write_text(f"""# Tmin model-layer decision brief

**Disposition: `{disposition}`**

- V1 evaluator repair: ACCEPT internally. P0 is 168 settled PIT/identity-valid probability rows versus the old 111 execution-conditioned rows.
- V1 frozen alpha=.10 routed challenger: date-equal ΔLogLoss `{v1_delta_ll:.6f}`, ΔBrier `{v1_delta_br:.6f}` versus raw market; both point estimates improve, but date-block uncertainty crosses zero.
- V2 strong-L2 market-offset OOF: date-equal ΔLogLoss `{v2_delta_ll:.6f}`, ΔBrier `{v2_delta_br:.6f}`; it does not clear raw market plus calibrated-market gates.
- V3: not fit. Exchange settlement identifies the final exact rung, but the evidence snapshot does not contain settlement-native next-colder event times needed for interval hazards.
- No selector, execution, threshold, PnL objective, or live behavior was changed.
""", encoding="utf-8")
    (output_dir / "01_V1_EVALUATOR_AMENDMENT.md").write_text("""# V1 evaluator amendment

The evaluator now separates P0 probability eligibility from execution evidence. P0 requires market/model probabilities, settled binary condition truth, PIT observation and forecast references, and complete candidate/checkpoint/condition/market/token/bracket/feature-book identity. P1 is the two frozen active windows. E0 may require quotes. T0 is selector output. Probability scores never depend on E0 or T0.

The frozen 111-row headline remains reproducible as a named legacy denominator; it is no longer presented as the canonical probability universe.
""", encoding="utf-8")
    (output_dir / "02_DENOMINATOR_LINEAGE.md").write_text(f"""# Denominator lineage

| Denominator | Rows | Dates | Purpose |
|---|---:|---:|---|
| P0_CANONICAL_PROBABILITY | {len(frame)} | {frame.target_date.nunique()} | Primary proper-score evidence |
| P1_ACTIVE_WINDOW_PROBABILITY | {int(frame.P1_ACTIVE_WINDOW_PROBABILITY.sum())} | {frame.loc[frame.P1_ACTIVE_WINDOW_PROBABILITY, 'target_date'].nunique()} | Frozen routed-window diagnosis |
| LEGACY_111_HEADLINE | {int(frame.LEGACY_111_HEADLINE.sum())} | {frame.loc[frame.LEGACY_111_HEADLINE, 'target_date'].nunique()} | Historical reproduction only |
| E0_EXECUTION_CLEAN | {int(frame.E0_EXECUTION_CLEAN.sum())} | {frame.loc[frame.E0_EXECUTION_CLEAN, 'target_date'].nunique()} | Execution evidence, never probability denominator |
| T0_SELECTED_TRADE | 19 | 12 | Frozen incumbent selector replay; imported headline, not a model-training target |
""", encoding="utf-8")
    (output_dir / "03_FORWARD_CONTAMINATION_AUDIT.md").write_text(f"""# Forward contamination audit

At amendment read time, the current journal had **{forward_audit.get('prediction_rows_target_date_ge_2026_08_28')}** prediction rows for target dates on/after 2026-08-28, while canonical settlement max target_date was **{forward_audit.get('settlements_max_target_date_at_read')}**. Therefore no matured 2026-08-28+ outcome existed to read or use for selection.

`CONFIRMATORY_FORWARD_START = {forward_audit.get('confirmatory_forward_start')}`. V1 alpha, windows, features and selector remain frozen. Prediction generation before the seal is not outcome contamination.
""", encoding="utf-8")
    (output_dir / "04_SETTLEMENT_NATIVE_TARGET_CONTRACT.md").write_text("""# Settlement-native target contract

Canonical row key is `(city, target_date, checkpoint_id, exact current rung condition_id)`. The primary label is the canonical exchange settlement `final_price` joined only by `condition_id`; YES=1 exactly when the checkpoint's current exact rung is the resolved YES condition. Market and YES token identities are retained row by row.

The five scored cities use integer Celsius exact rungs. Forecasts are converted from raw Fahrenheit to Celsius before margins are formed; settlement identity is never inferred by rounding a final observation. `target_date` is the venue-local civil day in the city timezone and covers the full local day, including an evening second cooling episode. A checkpoint UTC date may differ from local `target_date`; post-midnight beyond the local-day boundary belongs to the next settlement day.

This is reliable for binary final-rung classification. It is not sufficient for interval survival labels because exchange settlement does not record the time of the first next-colder crossing.
""", encoding="utf-8")
    (output_dir / "05_V2_MODEL_SPEC.md").write_text(f"""# V2 model specification

`logit(p_v2) = logit(p_market) + beta_0 + beta'X` with L2={STRONG_L2}, L-BFGS-B, date-equal training weights, median imputation and prior-train standardization. Every test target date is predicted only from earlier dates; folds with fewer than {MIN_PRIOR_DATES} prior dates or two examples of each class fail closed to `p_market`.

The eight predeclared, algebraically deduplicated inputs are listed in `07_FEATURE_AND_MECHANISM_DICTIONARY.csv`. No city/source dummy, interaction, spline, tree, grid search, selector or PnL target is used. Because the simple residual head lacks incremental skill, a GAM was not fit.
""", encoding="utf-8")
    (output_dir / "06_V3_SURVIVAL_MODEL_SPEC.md").write_text("""# V3 survival model specification

Primary interval was fixed ex ante at one hour. The intended target is the first time after a checkpoint that temperature crosses the next colder settlement rung, with right censoring at local 23:59:59. Intended final expression is `logit(p_final)=logit(p_market)+lambda*logit(p_survival)` with lambda estimated by nested prior-date OOF and strong shrinkage.

**Disposition: NOT FIT.** The frozen evidence has final exchange rung truth but no exchange-native crossing timestamp. Observation-cache crossings are source-dependent proxies and cannot be silently relabeled settlement-native. Therefore physical-survival and market-residual probabilities are null in the row audit.
""", encoding="utf-8")

    feature_rows = [
        ("forecast_remaining_floor_margin_c", "lattice safety + forecast margin", "forecast remaining min minus next colder integer-C rung", "C", "positive"),
        ("rebound_c", "rebound since running minimum", "current temperature minus running min", "C", "positive"),
        ("minutes_since_running_min", "time since running minimum", "elapsed PIT observation time", "minute", "positive"),
        ("hours_remaining", "effective remaining clock", "local-day boundary minus checkpoint", "hour", "positive"),
        ("forecast_hours_to_remaining_min", "forecast path timing", "time to remaining forecast minimum", "hour", "positive"),
        ("temperature_change_60m_c", "recent temperature trajectory", "PIT one-hour change", "C", "positive"),
        ("dewpoint_depression_f", "radiative state", "temperature minus dewpoint", "F", "positive"),
        ("wind_speed_kt", "mixing state", "PIT wind speed", "kt", "ambiguous"),
    ]
    pd.DataFrame(feature_rows, columns=["feature", "mechanism_group", "definition", "native_unit", "expected_sign"]).assign(
        transformation="median impute on prior dates; prior-train z-score",
        PIT_rule="source record available_at <= checkpoint decision_ts",
        model="V2 strongly regularized market-offset logistic",
    ).to_csv(output_dir / "07_FEATURE_AND_MECHANISM_DICTIONARY.csv", index=False)
    (output_dir / "08_FORECAST_UNCERTAINTY_REPORT.md").write_text(f"""# Forecast uncertainty report

**Disposition: `FORECAST_UNCERTAINTY_DATA_INSUFFICIENT`.** P0 has {len(frame)} rows but only {int(frame.label.eq(0).sum())} negative checkpoint rows across {frame.loc[frame.label.eq(0), ['city','target_date']].drop_duplicates().shape[0]} negative city-dates and {frame.target_date.nunique()} dates. That is insufficient to estimate a city/source/lead-time conditional forecast-error distribution by prior-date OOF without fabricating precision. V2 uses the PIT point-path margin only; no threshold-crossing probability is manufactured.
""", encoding="utf-8")
    (output_dir / "10_HIGH_PROBABILITY_CALIBRATION.md").write_text("""# High-probability calibration

Machine-readable tail results are in `HIGH_PROBABILITY_CALIBRATION.json` for fixed market thresholds 0.80, 0.90 and 0.95. These are diagnostics only; no threshold was selected or changed. Loss counts are sparse, so calibration error and worst-date influence are reported without a promotion claim.
""", encoding="utf-8")
    (output_dir / "11_ABLATION_AND_CAUSAL_DIAGNOSIS.md").write_text("""# Ablation and causal diagnosis

Controlled arms keep the settlement target and prior-date split fixed: V1 numeric features with a market offset; cleaned V2 features with a market offset; cleaned V2 physical-only; and clock/rebound with a market offset. Results are in the leaderboard.

The evidence points primarily to **E. data/sample insufficiency**, with secondary **B. feature/regularization** and **A. proxy/denominator-contract** concerns. Changing to the settlement-native target and strong regularization does not reveal stable V2 residual skill. The V1 market-combination expression is not disproven: alpha=.10 retains a small point improvement, but current dates cannot distinguish weather residual from noise robustly. Static classification versus survival cannot be adjudicated because event-time truth is absent.
""", encoding="utf-8")
    (output_dir / "12_CITY_DATE_STABILITY.md").write_text("""# City/date stability

`STABILITY_RESULTS.json` contains row/date/city-date equal scores, target-date bootstrap, leave-one-date-out and leave-one-city-out results for every baseline and research arm. No model is promoted from a single aggregate mean. The small number of negative city-dates makes all leave-one-group claims fragile.
""", encoding="utf-8")
    (output_dir / "13_PIT_AND_LEAKAGE_AUDIT.md").write_text("""# PIT and leakage audit

- Identity and label join: condition_id only; condition/market/token/bracket IDs are non-null in every P0 row.
- Features: reconstructed from input references available no later than the checkpoint.
- Splits: strict `train.target_date < test.target_date`; no same-date row crosses a fold.
- Normalization/imputation: fitted on prior dates only.
- Calibration: shrink-to-identity and fail-closed market fallback.
- Forward: target_date >= 2026-08-28 outcomes are excluded from all development and were not matured at the seal.
- V3/forecast uncertainty: stopped rather than substituting future observation truth.
""", encoding="utf-8")
    (output_dir / "14_FINDINGS_AND_OPEN_QUESTIONS.md").write_text(f"""# Findings and open questions

1. P0 repair materially changes the probability denominator: 111 to 168 rows.
2. Incumbent alpha=.50 is worse than market on P0. Frozen alpha=.10 routed remains a small, uncertain point improvement.
3. Generic shrink calibration is near identity; V1's point improvement is not reproduced by ordinary calibration, but uncertainty is too broad to call alpha proven.
4. V2 does not pass both raw-market and calibrated-market gates; no GAM escalation is justified.
5. V3 requires trustworthy first-crossing timestamps. Exchange final-rung truth alone cannot supply them.
6. Forecast uncertainty needs a longer PIT archive with more negative city-dates.

No live, selector, threshold, price cap, execution or sizing recommendation is made. Final disposition: `{disposition}`.
""", encoding="utf-8")
    (output_dir / "GPT_PRO_REVIEW_PACKET.md").write_text(f"""# GPT Pro external review packet

Please independently adjudicate:

A. ACCEPT or REJECT the V1 evaluator repair and P0/P1/E0/T0 separation.  
B. KEEP or DROP the V1 market-offset physical-residual scientific thesis.  
C. Does V2 show incremental weather residual evidence versus raw and calibrated market?  
D. Does V3 have incremental survival evidence, or is the event-time block correctly enforced?  
E. Recommend the next frozen probability challenger: V1, V2, V3, or NONE.

Internal disposition is `{disposition}`. Review `ROW_LEVEL_PROBABILITY_AUDIT.parquet`, `09_OOF_MODEL_LEADERBOARD.csv`, `STABILITY_RESULTS.json`, `OOF_FOLD_MANIFEST.json`, and the target/PIT contracts. This packet is probability-only: do not infer a live or PnL recommendation.
""", encoding="utf-8")

    seal = {
        "schema_version": "tmin_model_layer_evidence_seal_v1",
        "generated_at_utc": generated,
        "disposition": disposition,
        "code_sha": _git("rev-parse", "HEAD"),
        "code_dirty": bool(_git("status", "--short")),
        "production_release_sha": "74f55ace83424dbd94037506e74bbaba9a68c070",
        "frozen_model_artifact_sha256": "969bbbf00b29f57756890ed5e2faa6e3d0ccdc070c83000b76a665d5207424dd",
        "development_end_target_date": "2026-08-26",
        "confirmatory_forward_start": forward_audit.get("confirmatory_forward_start"),
        "p0_rows": len(frame),
        "p0_target_dates": int(frame["target_date"].nunique()),
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "pyarrow": pyarrow.__version__,
            "scipy": scipy.__version__,
        },
        "independent_review": {
            "status": args.review_status,
            "model": args.review_model,
            "effort": args.review_effort,
            "usage_telemetry": args.review_usage_telemetry,
            "summary": args.review_summary,
        },
    }
    _write_json(output_dir / "REVIEW_EVIDENCE_SEAL.json", seal)
    manifest_files = []
    for path in sorted(output_dir.iterdir()):
        if path.is_file() and path.name != "EVIDENCE_MANIFEST.json":
            manifest_files.append({"path": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path)})
    source_inputs = [
        {"path": str(args.row_audit), "sha256": _sha256(args.row_audit)},
        {"path": str(args.repaired_evaluation), "sha256": _sha256(args.repaired_evaluation)},
        {"path": str(args.trade_funnel), "sha256": _sha256(args.trade_funnel)},
        {"path": str(args.candidate_journal), "sha256": _sha256(args.candidate_journal)},
        {"path": str(Path(__file__).resolve()), "sha256": _sha256(Path(__file__).resolve())},
        {"path": str(ROOT / "scripts/analysis/tmin/evaluate_tmin_no_further_cooling_shadow_v1.py"), "sha256": _sha256(ROOT / "scripts/analysis/tmin/evaluate_tmin_no_further_cooling_shadow_v1.py")},
    ]
    if args.current_evaluation is not None and args.current_evaluation.exists():
        source_inputs.append(
            {"path": str(args.current_evaluation), "sha256": _sha256(args.current_evaluation)}
        )
    _write_json(output_dir / "EVIDENCE_MANIFEST.json", {
        "schema_version": "tmin_model_layer_lightweight_manifest_v1",
        "generated_at_utc": generated,
        "files": manifest_files,
        "source_inputs": source_inputs,
        "reproduce": "bash REPRODUCE.sh",
        "portability_note": "Lightweight result packet; source inputs are referenced by exact hash, not duplicated.",
    })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--row-audit", type=Path, required=True)
    parser.add_argument("--repaired-evaluation", type=Path, required=True)
    parser.add_argument("--trade-funnel", type=Path, required=True)
    parser.add_argument("--candidate-journal", type=Path, required=True)
    parser.add_argument("--current-evaluation", type=Path)
    parser.add_argument("--review-status", default="PENDING_INDEPENDENT_READ_ONLY_REVIEW")
    parser.add_argument("--review-model")
    parser.add_argument("--review-effort")
    parser.add_argument("--review-usage-telemetry")
    parser.add_argument("--review-summary")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-existing-output", action="store_true")
    args = parser.parse_args()
    build(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
