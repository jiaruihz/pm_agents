#!/usr/bin/env python3
"""Train and compare structurally repaired Helsinki market-expression v2 models.

The incumbent artifact is never overwritten.  Every challenger uses expanding
prior-target-date training and the same 2026-07-20..29 OOF market rows.  The
clean forward beginning 2026-07-31 is deliberately not read.

Challenger set (exploratory family, no multiple-testing adjustment):

* balanced compact fixed-offset logistic ridge path (4/16/64) and full r4;
* compact active-clock interaction model with stronger ridge shrinkage;
* coherent four-class delta-max market-offset model;
* shallow HGB nonlinear benchmark.
* monotone bounded weather-vs-market residual and fitted reliability curves.

Training objective gives checkpoint, state-entry and date-X-entry grains one
third each, with target dates equal inside each grain.  Candidate selection is
probability-first and requires non-degradation on active post-source date-X
rows; trade ROI is never used to select the probability model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Sequence

import joblib
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.optimize import minimize_scalar
from scipy.special import expit, softmax
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.analysis.reheat_risk import (  # noqa: E402
    research_helsinki_market_offset_residual_v1 as base,
)
from scripts.analysis.versioned_artifact_output import (  # noqa: E402
    prepare_new_run_output,
    resolve_content_addressed_artifact,
    resolve_run_output,
)


INCUMBENT_ARTIFACT_SHA256 = "398b92b295f43e04c7b4deab26ccdd2b1f40354474e2d187f453092dfa3a3ed9"
INCUMBENT_OOF_SHA256 = "e101bee0ad8e852c6f0b8f7d1b6ead57f35da7d0708091bf2f45c792ed5c3d21"
SEED = 20260731
EPS = 1e-6
BOOTSTRAP_DRAWS = 5000
MIN_TRAIN_DATES = 5

COMPACT_FEATURES = (
    "weather_market_logit_gap",
    "forecast_peak_h",
    "forecast_future_peak_margin_vs_boundary_c",
    "forecast_future_reheat_strength_c",
    "forecast_current_innovation_c",
    "temp_slope_30m_cph",
    "global_radiation_slope_30m",
    "official_latest_pullback_c",
    "official_report_age_min",
    "fmi_minus_official_latest_temp_c",
    "fade_reheat_p_eod",
    "local_hour_sin",
    "local_hour_cos",
)

CLOCK_FEATURES = COMPACT_FEATURES + (
    "book_active",
    "active_weather_gap",
    "active_margin",
    "active_peak_h",
    "active_temp_slope",
    "active_fade_eod",
)

CANDIDATES = {
    "p_reliability_tanh_s010": {
        "kind": "fitted_weather_reliability",
        "gap_scale": 0.10,
    },
    "p_reliability_tanh_s025": {
        "kind": "fitted_weather_reliability",
        "gap_scale": 0.25,
    },
    "p_reliability_tanh_s050": {
        "kind": "fitted_weather_reliability",
        "gap_scale": 0.50,
    },
    "p_reliability_tanh_s100": {
        "kind": "fitted_weather_reliability",
        "gap_scale": 1.00,
    },
    "p_bounded_weather_market_c025": {
        "kind": "bounded_weather_market_residual",
        "logit_cap": 0.25,
    },
    "p_bounded_weather_market_c005": {
        "kind": "bounded_weather_market_residual",
        "logit_cap": 0.05,
    },
    "p_bounded_weather_market_c0075": {
        "kind": "bounded_weather_market_residual",
        "logit_cap": 0.075,
    },
    "p_bounded_weather_market_c010": {
        "kind": "bounded_weather_market_residual",
        "logit_cap": 0.10,
    },
    "p_bounded_weather_market_c015": {
        "kind": "bounded_weather_market_residual",
        "logit_cap": 0.15,
    },
    "p_bounded_weather_market_c020": {
        "kind": "bounded_weather_market_residual",
        "logit_cap": 0.20,
    },
    "p_bounded_weather_market_c050": {
        "kind": "bounded_weather_market_residual",
        "logit_cap": 0.50,
    },
    "p_bounded_weather_market_c100": {
        "kind": "bounded_weather_market_residual",
        "logit_cap": 1.00,
    },
    "p_bounded_weather_market_c150": {
        "kind": "bounded_weather_market_residual",
        "logit_cap": 1.50,
    },
    "p_bounded_weather_market_c200": {
        "kind": "bounded_weather_market_residual",
        "logit_cap": 2.00,
    },
    "p_balanced_compact_r4": {
        "kind": "binary_offset",
        "features": COMPACT_FEATURES,
        "ridge": 4.0,
    },
    "p_balanced_full_r4": {
        "kind": "binary_offset",
        "features": base.FADE_FEATURES,
        "ridge": 4.0,
    },
    "p_balanced_compact_r16": {
        "kind": "binary_offset",
        "features": COMPACT_FEATURES,
        "ridge": 16.0,
    },
    "p_balanced_compact_r64": {
        "kind": "binary_offset",
        "features": COMPACT_FEATURES,
        "ridge": 64.0,
    },
    "p_clock_compact_r16": {
        "kind": "binary_offset",
        "features": CLOCK_FEATURES,
        "ridge": 16.0,
    },
    "p_joint_clock_r16": {
        "kind": "joint_offset",
        "features": CLOCK_FEATURES,
        "ridge": 16.0,
    },
    "p_balanced_shallow_hgb": {
        "kind": "shallow_hgb",
        "features": ("market_logit", *CLOCK_FEATURES),
    },
}


def add_v2_features(rows: pd.DataFrame) -> pd.DataFrame:
    frame = rows.copy()
    frame["book_active"] = frame["book_evidence"].eq(
        "active_first_after_source"
    ).astype(float)
    frame["active_weather_gap"] = (
        frame["book_active"] * frame["weather_market_logit_gap"]
    )
    frame["active_margin"] = (
        frame["book_active"]
        * frame["forecast_future_peak_margin_vs_boundary_c"]
    )
    frame["active_peak_h"] = frame["book_active"] * frame["forecast_peak_h"]
    frame["active_temp_slope"] = (
        frame["book_active"] * frame["temp_slope_30m_cph"]
    )
    frame["active_fade_eod"] = (
        frame["book_active"] * frame["fade_reheat_p_eod"]
    )
    delta = (
        pd.to_numeric(frame["official_final_max_c"], errors="coerce")
        - pd.to_numeric(frame["official_running_max_c"], errors="coerce")
    )
    if delta.isna().any() or (delta < 0).any():
        raise RuntimeError("invalid delta-max label")
    frame["delta_max_class"] = delta.clip(0, 3).astype(int)
    return frame


def date_x_entries(rows: pd.DataFrame) -> pd.DataFrame:
    return (
        rows.sort_values("decision_ts_utc")
        .groupby(["target_date", "official_running_max_c"], as_index=False)
        .head(1)
    )


def balanced_training_rows(rows: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for grain, frame in (
        ("checkpoint", rows),
        ("state_entry", base.state_entry_rows(rows)),
        ("date_x_entry", date_x_entries(rows)),
    ):
        part = frame.copy()
        part["training_grain"] = grain
        count = part.groupby("target_date")["target_date"].transform("size")
        date_count = part["target_date"].nunique()
        part["objective_weight"] = 1.0 / (3 * date_count * count)
        frames.append(part)
    stacked = pd.concat(frames, ignore_index=True)
    stacked["objective_weight"] *= len(stacked) / stacked["objective_weight"].sum()
    return stacked


def feature_stats(
    unique_train: pd.DataFrame, features: Sequence[str]
) -> dict[str, dict[str, float]]:
    raw = unique_train[list(features)].replace([np.inf, -np.inf], np.nan)
    median = raw.median().fillna(0)
    filled = raw.fillna(median)
    mean = filled.mean()
    scale = filled.std(ddof=0).replace(0, 1).fillna(1)
    return {
        "median": median.to_dict(),
        "mean": mean.to_dict(),
        "scale": scale.to_dict(),
    }


def transform(
    rows: pd.DataFrame, features: Sequence[str], stats: dict[str, dict[str, float]]
) -> np.ndarray:
    raw = rows[list(features)].replace([np.inf, -np.inf], np.nan)
    median = pd.Series(stats["median"])
    mean = pd.Series(stats["mean"])
    scale = pd.Series(stats["scale"])
    return ((raw.fillna(median) - mean) / scale).to_numpy(float)


def fit_binary_offset(
    unique_train: pd.DataFrame, features: Sequence[str], ridge: float
) -> dict[str, Any]:
    stacked = balanced_training_rows(unique_train)
    stats = feature_stats(unique_train, features)
    x = transform(stacked, features, stats)
    x = np.column_stack([np.ones(len(x)), x])
    y = stacked["y_break"].to_numpy(float)
    offset = stacked["market_logit"].to_numpy(float)
    weights = stacked["objective_weight"].to_numpy(float)

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        probability = np.clip(expit(offset + x @ beta), EPS, 1 - EPS)
        loss = np.average(
            -y * np.log(probability) - (1 - y) * np.log(1 - probability),
            weights=weights,
        )
        # Penalize the whole correction, including its intercept.  This is the
        # structural shrinkage requested by the reliability audit.
        penalty = ridge * float(beta @ beta) / len(unique_train)
        gradient = x.T @ (weights * (probability - y)) / weights.sum()
        gradient += 2 * ridge * beta / len(unique_train)
        return loss + penalty, gradient

    result = minimize(
        lambda beta: objective(beta),
        np.zeros(x.shape[1]),
        jac=True,
        method="L-BFGS-B",
    )
    if not result.success:
        raise RuntimeError(f"binary offset fit failed: {result.message}")
    return {
        "kind": "binary_offset",
        "features": list(features),
        "ridge": ridge,
        **stats,
        "beta": result.x.tolist(),
    }


def predict_binary_offset(artifact: dict[str, Any], rows: pd.DataFrame) -> np.ndarray:
    x = transform(rows, artifact["features"], artifact)
    x = np.column_stack([np.ones(len(x)), x])
    return expit(rows["market_logit"].to_numpy(float) + x @ artifact["beta"])


def market_weather_joint_base(rows: pd.DataFrame) -> np.ndarray:
    market = np.clip(rows["market_probability"].to_numpy(float), 1e-4, 1 - 1e-4)
    severity = rows[["q1_v7", "q2_v7", "q3_v7"]].to_numpy(float)
    severity = np.clip(severity, EPS, None)
    severity /= severity.sum(axis=1, keepdims=True)
    distribution = np.column_stack([1 - market, market[:, None] * severity])
    distribution = np.clip(distribution, EPS, None)
    return distribution / distribution.sum(axis=1, keepdims=True)


def fit_joint_offset(
    unique_train: pd.DataFrame, features: Sequence[str], ridge: float
) -> dict[str, Any]:
    stacked = balanced_training_rows(unique_train)
    stats = feature_stats(unique_train, features)
    x = transform(stacked, features, stats)
    x = np.column_stack([np.ones(len(x)), x])
    y = stacked["delta_max_class"].to_numpy(int)
    weights = stacked["objective_weight"].to_numpy(float)
    base_distribution = market_weather_joint_base(stacked)
    base_log_odds = np.log(base_distribution[:, 1:] / base_distribution[:, [0]])
    shape = (x.shape[1], 3)

    def objective(flat: np.ndarray) -> tuple[float, np.ndarray]:
        beta = flat.reshape(shape)
        logits = np.column_stack([np.zeros(len(x)), base_log_odds + x @ beta])
        probability = np.clip(softmax(logits, axis=1), EPS, 1 - EPS)
        loss = np.average(-np.log(probability[np.arange(len(y)), y]), weights=weights)
        penalty = ridge * float(np.sum(beta * beta)) / len(unique_train)
        residual = probability
        residual[np.arange(len(y)), y] -= 1
        gradient = x.T @ (weights[:, None] * residual[:, 1:]) / weights.sum()
        gradient += 2 * ridge * beta / len(unique_train)
        return loss + penalty, gradient.ravel()

    result = minimize(
        lambda beta: objective(beta),
        np.zeros(np.prod(shape)),
        jac=True,
        method="L-BFGS-B",
    )
    if not result.success:
        raise RuntimeError(f"joint offset fit failed: {result.message}")
    return {
        "kind": "joint_offset",
        "features": list(features),
        "ridge": ridge,
        **stats,
        "beta": result.x.reshape(shape).tolist(),
    }


def predict_joint_offset(
    artifact: dict[str, Any], rows: pd.DataFrame
) -> np.ndarray:
    x = transform(rows, artifact["features"], artifact)
    x = np.column_stack([np.ones(len(x)), x])
    base_distribution = market_weather_joint_base(rows)
    base_log_odds = np.log(base_distribution[:, 1:] / base_distribution[:, [0]])
    logits = np.column_stack(
        [np.zeros(len(x)), base_log_odds + x @ np.asarray(artifact["beta"])]
    )
    return softmax(logits, axis=1)


def fit_hgb(unique_train: pd.DataFrame, features: Sequence[str]) -> dict[str, Any]:
    stacked = balanced_training_rows(unique_train)
    x = stacked[list(features)].replace([np.inf, -np.inf], np.nan).to_numpy(float)
    model = HistGradientBoostingClassifier(
        learning_rate=0.05,
        max_iter=80,
        max_leaf_nodes=5,
        min_samples_leaf=30,
        l2_regularization=5.0,
        random_state=SEED,
    )
    model.fit(
        x,
        stacked["y_break"].to_numpy(int),
        sample_weight=stacked["objective_weight"].to_numpy(float),
    )
    return {"kind": "shallow_hgb", "features": list(features), "model": model}


def predict_hgb(artifact: dict[str, Any], rows: pd.DataFrame) -> np.ndarray:
    x = rows[artifact["features"]].replace([np.inf, -np.inf], np.nan).to_numpy(float)
    return artifact["model"].predict_proba(x)[:, 1]


def fit_candidate(
    unique_train: pd.DataFrame, definition: dict[str, Any]
) -> dict[str, Any]:
    if definition["kind"] == "fitted_weather_reliability":
        stacked = balanced_training_rows(unique_train)
        scale = float(definition["gap_scale"])
        offset = stacked["market_logit"].to_numpy(float)
        correction = scale * np.tanh(
            stacked["weather_market_logit_gap"].to_numpy(float) / scale
        )
        y = stacked["y_break"].to_numpy(float)
        weights = stacked["objective_weight"].to_numpy(float)

        def objective(alpha: float) -> float:
            probability = np.clip(expit(offset + alpha * correction), EPS, 1 - EPS)
            return float(np.average(
                -y * np.log(probability) - (1 - y) * np.log(1 - probability),
                weights=weights,
            ))

        fitted = minimize_scalar(objective, bounds=(0.0, 1.0), method="bounded")
        if not fitted.success:
            raise RuntimeError(f"weather reliability fit failed: {fitted.message}")
        return {**definition, "weather_reliability": float(fitted.x)}
    if definition["kind"] == "bounded_weather_market_residual":
        return dict(definition)
    if definition["kind"] == "binary_offset":
        return fit_binary_offset(
            unique_train, definition["features"], definition["ridge"]
        )
    if definition["kind"] == "joint_offset":
        return fit_joint_offset(
            unique_train, definition["features"], definition["ridge"]
        )
    if definition["kind"] == "shallow_hgb":
        return fit_hgb(unique_train, definition["features"])
    raise ValueError(definition["kind"])


def predict_candidate(
    artifact: dict[str, Any], rows: pd.DataFrame
) -> tuple[np.ndarray, np.ndarray | None]:
    if artifact["kind"] == "fitted_weather_reliability":
        scale = float(artifact["gap_scale"])
        correction = scale * np.tanh(
            rows["weather_market_logit_gap"].to_numpy(float) / scale
        )
        probability = expit(
            rows["market_logit"].to_numpy(float)
            + float(artifact["weather_reliability"]) * correction
        )
        return probability, None
    if artifact["kind"] == "bounded_weather_market_residual":
        cap = float(artifact["logit_cap"])
        market = np.clip(rows["market_probability"].to_numpy(float), EPS, 1 - EPS)
        weather = np.clip(rows["p_break_v7"].to_numpy(float), EPS, 1 - EPS)
        market_logit = np.log(market / (1 - market))
        weather_logit = np.log(weather / (1 - weather))
        correction = cap * np.tanh((weather_logit - market_logit) / cap)
        return expit(market_logit + correction), None
    if artifact["kind"] == "binary_offset":
        return predict_binary_offset(artifact, rows), None
    if artifact["kind"] == "joint_offset":
        joint = predict_joint_offset(artifact, rows)
        return 1 - joint[:, 0], joint
    if artifact["kind"] == "shallow_hgb":
        return predict_hgb(artifact, rows), None
    raise ValueError(artifact["kind"])


def expanding_predictions(rows: pd.DataFrame) -> pd.DataFrame:
    dates = sorted(rows["target_date"].astype(str).unique())
    outputs = []
    for index, target_date in enumerate(dates):
        if index < MIN_TRAIN_DATES:
            continue
        train = rows.loc[rows["target_date"].astype(str).isin(dates[:index])].copy()
        test = rows.loc[rows["target_date"].astype(str).eq(target_date)].copy()
        output = test.copy()
        incumbent_train = base.state_entry_rows(train)
        output["p_frozen_old"] = base.fit_offset(
            incumbent_train, test, base.FADE_FEATURES
        )
        for name, definition in CANDIDATES.items():
            artifact = fit_candidate(train, definition)
            probability, joint = predict_candidate(artifact, test)
            output[name] = probability
            if joint is not None:
                for class_id in range(4):
                    output[f"q_joint_clock_r16_{class_id}"] = joint[:, class_id]
        joint_base = market_weather_joint_base(test)
        for class_id in range(4):
            output[f"q_market_weather_base_{class_id}"] = joint_base[:, class_id]
        outputs.append(output)
    return pd.concat(outputs, ignore_index=True)


def binary_metrics(rows: pd.DataFrame, probability: str) -> dict[str, Any]:
    daily = []
    for _, group in rows.groupby("target_date"):
        y = group["y_break"].to_numpy(float)
        p = np.clip(group[probability].to_numpy(float), EPS, 1 - EPS)
        daily.append(
            {
                "brier": np.mean((p - y) ** 2),
                "logloss": np.mean(-y * np.log(p) - (1 - y) * np.log(1 - p)),
                "bias": np.mean(p - y),
            }
        )
    daily_frame = pd.DataFrame(daily)
    y = rows["y_break"].to_numpy(int)
    p = rows[probability].to_numpy(float)
    return {
        "rows": int(len(rows)),
        "target_dates": int(rows["target_date"].nunique()),
        "brier": float(daily_frame["brier"].mean()),
        "logloss": float(daily_frame["logloss"].mean()),
        "calibration_bias": float(daily_frame["bias"].mean()),
        "accuracy_0p5": float(np.mean((p >= 0.5) == y)),
        "auc": float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None,
    }


def multiclass_metrics(rows: pd.DataFrame, prefix: str) -> dict[str, Any]:
    probabilities = rows[[f"{prefix}_{i}" for i in range(4)]].to_numpy(float)
    probabilities = np.clip(probabilities, EPS, 1 - EPS)
    probabilities /= probabilities.sum(axis=1, keepdims=True)
    labels = rows["delta_max_class"].to_numpy(int)
    one_hot = np.eye(4)[labels]
    losses = {
        "multiclass_logloss": -np.log(probabilities[np.arange(len(labels)), labels]),
        "multiclass_brier": np.sum((probabilities - one_hot) ** 2, axis=1),
        "rps": np.sum(
            (np.cumsum(probabilities, axis=1)[:, :-1] - np.cumsum(one_hot, axis=1)[:, :-1])
            ** 2,
            axis=1,
        )
        / 3,
    }
    output = {"rows": int(len(rows)), "target_dates": int(rows["target_date"].nunique())}
    for metric, values in losses.items():
        output[metric] = float(
            pd.DataFrame({"target_date": rows["target_date"], "loss": values})
            .groupby("target_date")["loss"]
            .mean()
            .mean()
        )
    output["exact_accuracy"] = float(np.mean(probabilities.argmax(axis=1) == labels))
    return output


def block_bootstrap(
    rows: pd.DataFrame, challenger: str, baseline_probability: str
) -> list[dict[str, Any]]:
    rng = np.random.default_rng(SEED)
    y = rows["y_break"].to_numpy(float)
    p1 = np.clip(rows[challenger].to_numpy(float), EPS, 1 - EPS)
    p0 = np.clip(rows[baseline_probability].to_numpy(float), EPS, 1 - EPS)
    output = []
    for metric in ("brier", "logloss"):
        if metric == "brier":
            delta = (p1 - y) ** 2 - (p0 - y) ** 2
        else:
            delta = (
                -y * np.log(p1)
                - (1 - y) * np.log(1 - p1)
                + y * np.log(p0)
                + (1 - y) * np.log(1 - p0)
            )
        daily = (
            pd.DataFrame({"target_date": rows["target_date"], "delta": delta})
            .groupby("target_date")["delta"]
            .mean()
        )
        draws = rng.choice(
            daily.to_numpy(), size=(BOOTSTRAP_DRAWS, len(daily)), replace=True
        ).mean(axis=1)
        output.append(
            {
                "metric": metric,
                "challenger": challenger,
                "baseline": baseline_probability,
                "delta": float(daily.mean()),
                "ci_low": float(np.quantile(draws, 0.025)),
                "ci_high": float(np.quantile(draws, 0.975)),
                "probability_challenger_better": float(np.mean(draws < 0)),
            }
        )
    return output


def trade_case_comparison(trades: pd.DataFrame) -> pd.DataFrame:
    incumbent = trades.loc[trades["model"].eq("p_frozen_old")].copy()
    incumbent = incumbent.rename(
        columns={
            "decision_ts_utc": "old_decision_ts_utc",
            "probability": "old_probability",
            "effective_cost": "old_effective_cost",
            "edge": "old_edge",
            "pnl": "old_pnl",
            "won": "old_won",
            "book_evidence": "old_book_evidence",
            "path_state": "old_path_state",
        }
    )
    incumbent = incumbent[
        [
            "target_date",
            "current_x",
            "old_decision_ts_utc",
            "old_probability",
            "old_effective_cost",
            "old_edge",
            "old_pnl",
            "old_won",
            "old_book_evidence",
            "old_path_state",
        ]
    ]
    outputs = []
    for model in CANDIDATES:
        challenger = trades.loc[trades["model"].eq(model)].copy()
        challenger = challenger.rename(
            columns={
                "decision_ts_utc": "new_decision_ts_utc",
                "probability": "new_probability",
                "effective_cost": "new_effective_cost",
                "edge": "new_edge",
                "pnl": "new_pnl",
                "won": "new_won",
                "book_evidence": "new_book_evidence",
                "path_state": "new_path_state",
            }
        )
        challenger = challenger[
            [
                "target_date",
                "current_x",
                "new_decision_ts_utc",
                "new_probability",
                "new_effective_cost",
                "new_edge",
                "new_pnl",
                "new_won",
                "new_book_evidence",
                "new_path_state",
            ]
        ]
        pair = incumbent.merge(
            challenger,
            on=["target_date", "current_x"],
            how="outer",
            indicator=True,
        )
        pair["candidate"] = model
        pair["signal_change"] = pair["_merge"].map(
            {"left_only": "removed", "right_only": "added", "both": "retained"}
        )
        pair["entry_time_changed"] = (
            pair["_merge"].eq("both")
            & pair["old_decision_ts_utc"].ne(pair["new_decision_ts_utc"])
        )
        pair["probability_change"] = pair["new_probability"] - pair["old_probability"]
        outputs.append(pair.drop(columns="_merge"))
    return pd.concat(outputs, ignore_index=True)


def selection_table(scores: pd.DataFrame) -> pd.DataFrame:
    score = scores.set_index(["grain", "evidence", "model"])
    old = "p_frozen_old"
    market = "market_probability"
    rows = []
    for candidate in CANDIDATES:
        integrated_candidate = scores.loc[
            scores["model"].eq(candidate) & scores["evidence"].eq("all")
        ][["brier", "logloss"]].mean()
        integrated_old = scores.loc[
            scores["model"].eq(old) & scores["evidence"].eq("all")
        ][["brier", "logloss"]].mean()
        date_x_candidate = score.loc[("date_x_entry", "all", candidate)]
        date_x_old = score.loc[("date_x_entry", "all", old)]
        active_candidate = score.loc[
            ("date_x_entry", "active_first_after_source", candidate)
        ]
        active_market = score.loc[
            ("date_x_entry", "active_first_after_source", market)
        ]
        gate_integrated = bool(
            (integrated_candidate["brier"] <= integrated_old["brier"])
            and (integrated_candidate["logloss"] <= integrated_old["logloss"])
        )
        gate_date_x = bool(
            (date_x_candidate["brier"] <= date_x_old["brier"])
            and (date_x_candidate["logloss"] <= date_x_old["logloss"])
        )
        gate_active = bool(
            (active_candidate["brier"] <= active_market["brier"])
            and (active_candidate["logloss"] <= active_market["logloss"])
        )
        rows.append(
            {
                "candidate": candidate,
                "integrated_brier": float(integrated_candidate["brier"]),
                "integrated_logloss": float(integrated_candidate["logloss"]),
                "date_x_brier": float(date_x_candidate["brier"]),
                "date_x_logloss": float(date_x_candidate["logloss"]),
                "active_date_x_brier": float(active_candidate["brier"]),
                "active_date_x_logloss": float(active_candidate["logloss"]),
                "gate_integrated_vs_old": gate_integrated,
                "gate_date_x_vs_old": gate_date_x,
                "gate_active_vs_market": gate_active,
                "replacement_pass": gate_integrated and gate_date_x and gate_active,
            }
        )
    frame = pd.DataFrame(rows)
    frame["research_rank"] = (
        frame["date_x_brier"].rank()
        + frame["date_x_logloss"].rank()
        + frame["active_date_x_brier"].rank()
        + frame["active_date_x_logloss"].rank()
    )
    return frame.sort_values(["replacement_pass", "research_rank"], ascending=[False, True])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Run-specific research output; deployed artifacts are never overwritten implicitly.",
    )
    args = parser.parse_args()
    resolved_output = resolve_run_output(
        "helsinki_market_expression_v2",
        run_id=args.run_id,
        explicit_output=args.output_dir,
    )
    incumbent_artifact = resolve_content_addressed_artifact(
        INCUMBENT_ARTIFACT_SHA256
    )
    incumbent_oof = resolve_content_addressed_artifact(INCUMBENT_OOF_SHA256)
    rows, coverage = base.prepare_rows()
    rows = add_v2_features(rows)
    rows["target_date"] = rows["target_date"].astype(str)
    predictions = expanding_predictions(rows)
    incumbent = pd.read_csv(incumbent_oof)
    incumbent = incumbent.sort_values(
        ["target_date", "decision_ts_utc", "official_running_max_c"]
    ).reset_index(drop=True)
    check = predictions.sort_values(
        ["target_date", "decision_ts_utc", "official_running_max_c"]
    ).reset_index(drop=True)
    if len(check) != len(incumbent):
        raise RuntimeError("incumbent OOF denominator mismatch")
    reproduction_error = float(
        np.max(np.abs(check["p_frozen_old"] - incumbent["p_offset_fade"]))
    )
    if reproduction_error > 1e-10:
        raise RuntimeError(f"incumbent reproduction error={reproduction_error}")

    grains = {
        "checkpoint": predictions,
        "state_entry": base.state_entry_rows(predictions),
        "date_x_entry": date_x_entries(predictions),
    }
    probabilities = (
        "market_probability",
        "p_break_v7",
        "p_frozen_old",
        *CANDIDATES.keys(),
    )
    score_rows = []
    bootstrap_rows = []
    for grain_name, grain in grains.items():
        evidence_sets = {"all": grain}
        if grain_name in {"checkpoint", "date_x_entry"}:
            for evidence in (
                "active_first_after_source",
                "full_ladder_before_source",
            ):
                evidence_sets[evidence] = grain.loc[grain["book_evidence"].eq(evidence)]
        for evidence, subset in evidence_sets.items():
            for probability in probabilities:
                score_rows.append(
                    {
                        "grain": grain_name,
                        "evidence": evidence,
                        "model": probability,
                        **binary_metrics(subset, probability),
                    }
                )
            for challenger in ("p_frozen_old", *CANDIDATES.keys()):
                for baseline_probability in ("market_probability", "p_frozen_old"):
                    if challenger == baseline_probability:
                        continue
                    for result in block_bootstrap(
                        subset, challenger, baseline_probability
                    ):
                        bootstrap_rows.append(
                            {"grain": grain_name, "evidence": evidence, **result}
                        )

    scores = pd.DataFrame(score_rows)
    bootstraps = pd.DataFrame(bootstrap_rows)
    selection = selection_table(scores)
    passing = selection.loc[selection["replacement_pass"]]
    bounded_candidates = [
        name
        for name, definition in CANDIDATES.items()
        if definition["kind"] == "bounded_weather_market_residual"
    ]
    robust_rows = []
    for candidate in bounded_candidates:
        probability = np.clip(predictions[candidate].to_numpy(float), EPS, 1 - EPS)
        y = predictions["y_break"].to_numpy(float)
        losses = pd.DataFrame(
            {
                "target_date": predictions["target_date"],
                "logloss": -y * np.log(probability) - (1 - y) * np.log(1 - probability),
            }
        ).groupby("target_date")["logloss"].mean()
        robust_rows.append(
            {
                "candidate": candidate,
                "worst_target_date_logloss": float(losses.max()),
                "mean_target_date_logloss": float(losses.mean()),
            }
        )
    robust_selection = pd.DataFrame(robust_rows).sort_values(
        ["worst_target_date_logloss", "mean_target_date_logloss"]
    )
    research_challenger = str(robust_selection.iloc[0]["candidate"])
    selected_replacement = (
        str(passing.iloc[0]["candidate"]) if not passing.empty else None
    )

    multiclass_rows = []
    for grain_name, grain in grains.items():
        for name, prefix in (
            ("market_weather_joint_base", "q_market_weather_base"),
            ("joint_clock_r16", "q_joint_clock_r16"),
        ):
            multiclass_rows.append(
                {"grain": grain_name, "model": name, **multiclass_metrics(grain, prefix)}
            )
    multiclass = pd.DataFrame(multiclass_rows)

    oof_dates = sorted(predictions["target_date"].unique())
    trades_5 = pd.concat(
        [base.route(predictions, probability, shares=5) for probability in probabilities[1:]],
        ignore_index=True,
    )
    trades_10 = pd.concat(
        [base.route(predictions, probability, shares=10) for probability in probabilities[1:]],
        ignore_index=True,
    )
    trade_summary_rows = []
    for shares, trades in ((5, trades_5), (10, trades_10)):
        for probability in probabilities[1:]:
            subset = trades.loc[trades["model"].eq(probability)]
            for evidence, evidence_subset in (
                ("all", subset),
                (
                    "active_first_after_source",
                    subset.loc[subset["book_evidence"].eq("active_first_after_source")],
                ),
                (
                    "full_ladder_before_source",
                    subset.loc[subset["book_evidence"].eq("full_ladder_before_source")],
                ),
            ):
                trade_summary_rows.append(
                    {
                        "shares": shares,
                        "model": probability,
                        "evidence": evidence,
                        **base.trade_summary(evidence_subset, oof_dates),
                    }
                )
    trade_summaries = pd.DataFrame(trade_summary_rows)
    case_comparison = trade_case_comparison(trades_5)
    trade_pair_rows = []
    for shares, trades in ((5, trades_5), (10, trades_10)):
        incumbent_trades = trades.loc[trades["model"].eq("p_frozen_old")]
        for candidate in CANDIDATES:
            challenger_trades = trades.loc[trades["model"].eq(candidate)]
            for evidence in ("all", "active_first_after_source"):
                if evidence == "all":
                    challenger_subset = challenger_trades
                    incumbent_subset = incumbent_trades
                else:
                    challenger_subset = challenger_trades.loc[
                        challenger_trades["book_evidence"].eq(evidence)
                    ]
                    incumbent_subset = incumbent_trades.loc[
                        incumbent_trades["book_evidence"].eq(evidence)
                    ]
                trade_pair_rows.append(
                    {
                        "shares": shares,
                        "candidate": candidate,
                        "evidence": evidence,
                        **base.trade_pair_bootstrap(
                            challenger_subset, incumbent_subset, oof_dates
                        ),
                    }
                )
    trade_pairs = pd.DataFrame(trade_pair_rows)
    prediction_reference = predictions.rename(
        columns={"official_running_max_c": "current_x"}
    )[
        [
            "target_date",
            "current_x",
            "decision_ts_utc",
            "official_final_max_c",
            "winner_bracket",
            "y_break",
            "temp_c",
            "path_state",
            "forecast_future_peak_margin_vs_boundary_c",
            "forecast_future_reheat_strength_c",
            "temp_slope_30m_cph",
            "global_radiation_slope_30m",
            "official_latest_pullback_c",
            "official_report_age_min",
            "fmi_minus_official_latest_temp_c",
            "market_probability",
            "p_break_v7",
            "p_frozen_old",
            *CANDIDATES.keys(),
        ]
    ]
    selected_case_atlas = trades_5.merge(
        prediction_reference,
        on=["target_date", "current_x", "decision_ts_utc", "path_state"],
        how="left",
        validate="many_to_one",
    )

    final_definition = CANDIDATES[research_challenger]
    final_artifact = fit_candidate(rows, final_definition)
    final_artifact.update(
        {
            "model_id": "helsinki_market_expression_v2_research_challenger",
            "candidate_name": research_challenger,
            "candidate_count_k": len(CANDIDATES),
            "multiple_testing_adjustment": None,
            "selection_rule": (
                "within the monotone bounded market-plus-weather family, minimize "
                "worst-target-date checkpoint logloss; ROI is not used"
            ),
            "train_rows_unique": int(len(rows)),
            "train_target_dates": int(rows["target_date"].nunique()),
            "train_start": str(rows["target_date"].min()),
            "train_end": str(rows["target_date"].max()),
            "clean_forward_start": "2026-07-31",
            "clean_forward_read": False,
            "replacement_selected": selected_replacement is not None,
            "expression_sides": ["NO", "YES"],
            "expression_policy": "first_best_fee_adjusted_edge_per_date_bracket",
            "incumbent_artifact_sha256": hashlib.sha256(
                incumbent_artifact.read_bytes()
            ).hexdigest(),
        }
    )
    output = prepare_new_run_output(resolved_output)
    artifact_path = output / "helsinki_market_expression_v2_research_challenger.joblib"
    joblib.dump(final_artifact, artifact_path)
    artifact_sha = hashlib.sha256(artifact_path.read_bytes()).hexdigest()

    summary = {
        "status": (
            "replacement_candidate_selected_requires_frozen_forward"
            if selected_replacement
            else "no_replacement_pass_research_challenger_only"
        ),
        "coverage": coverage,
        "oof": {
            "rows": int(len(predictions)),
            "target_dates": int(predictions["target_date"].nunique()),
            "start": str(predictions["target_date"].min()),
            "end": str(predictions["target_date"].max()),
            "incumbent_reproduction_max_abs_error": reproduction_error,
        },
        "candidate_count_k": len(CANDIDATES),
        "multiple_testing_adjustment": None,
        "selection_rule": (
            "within the monotone bounded market-plus-weather family, minimize "
            "worst-target-date checkpoint logloss; ROI is not used"
        ),
        "selected_replacement": selected_replacement,
        "research_challenger": research_challenger,
        "artifact": (
            str(artifact_path.relative_to(ROOT))
            if artifact_path.is_relative_to(ROOT)
            else str(artifact_path)
        ),
        "artifact_sha256": artifact_sha,
        "incumbent_artifact_sha256": final_artifact["incumbent_artifact_sha256"],
        "forward": "2026-07-31+ labels not read; no retuning",
        "live_change": False,
        "actual_fills": 0,
    }
    predictions.to_csv(
        output / "oof_checkpoint_predictions.csv.gz",
        index=False,
        compression={"method": "gzip", "mtime": 0},
    )
    grains["state_entry"].to_csv(output / "oof_state_entries.csv", index=False)
    grains["date_x_entry"].to_csv(output / "oof_date_x_entries.csv", index=False)
    scores.to_csv(output / "probability_scores.csv", index=False)
    bootstraps.to_csv(output / "target_date_bootstrap.csv", index=False)
    multiclass.to_csv(output / "multiclass_scores.csv", index=False)
    selection.to_csv(output / "candidate_selection.csv", index=False)
    robust_selection.to_csv(output / "bounded_robust_loss_selection.csv", index=False)
    trades_5.to_csv(output / "trade_replay_5share.csv", index=False)
    trades_10.to_csv(output / "trade_replay_10share.csv", index=False)
    trade_summaries.to_csv(output / "trade_summaries.csv", index=False)
    trade_pairs.to_csv(output / "trade_pair_bootstrap_vs_incumbent.csv", index=False)
    case_comparison.to_csv(output / "trade_case_comparison.csv", index=False)
    selected_case_atlas.to_csv(output / "selected_trade_case_atlas.csv", index=False)
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
