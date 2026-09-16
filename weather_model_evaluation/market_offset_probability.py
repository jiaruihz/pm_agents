"""Reusable fixed-market-logit probability correction.

The market probability remains a coefficient-one offset.  A fitted weather
model may only add a regularized log-odds correction.  Model selection is
confined to an explicit development/validation split and scoring is equal by
``target_date``.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from weather_model_evaluation.probability import composite_grain_weights


EPSILON = 1e-6


def logit(values: pd.Series | np.ndarray) -> np.ndarray:
    probability = np.clip(np.asarray(values, dtype=float), EPSILON, 1.0 - EPSILON)
    return np.log(probability / (1.0 - probability))


def expit(values: pd.Series | np.ndarray) -> np.ndarray:
    linear = np.clip(np.asarray(values, dtype=float), -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-linear))


def _date_equal_weights(frame: pd.DataFrame, date_column: str) -> np.ndarray:
    counts = frame.groupby(date_column, sort=False)[date_column].transform("size")
    weights = 1.0 / counts.to_numpy(dtype=float)
    return weights / weights.mean()


def fit_fixed_market_offset(
    frame: pd.DataFrame,
    *,
    feature_columns: Sequence[str],
    market_probability_column: str,
    label_column: str,
    date_column: str = "target_date",
    l2_strength: float,
    membership_columns: Sequence[str] = (),
) -> dict[str, Any]:
    """Fit ``logit(p)=logit(p_market)+intercept+X beta`` with L2 shrinkage."""

    if frame.empty:
        raise ValueError("market-offset training frame is empty")
    if l2_strength <= 0:
        raise ValueError("l2_strength must be positive")
    features = list(feature_columns)
    missing = sorted(
        {market_probability_column, label_column, date_column, *features}
        - set(frame.columns)
    )
    if missing:
        raise ValueError(f"market-offset frame missing columns: {missing}")

    numeric = frame[features].apply(pd.to_numeric, errors="coerce")
    medians = numeric.median().fillna(0.0)
    imputed = numeric.fillna(medians)
    means = imputed.mean()
    scales = imputed.std(ddof=0).replace(0.0, 1.0).fillna(1.0)
    design = ((imputed - means) / scales).to_numpy(dtype=float)
    design = np.column_stack([np.ones(len(design)), design])
    labels = pd.to_numeric(frame[label_column], errors="raise").to_numpy(dtype=float)
    if not set(np.unique(labels)).issubset({0.0, 1.0}):
        raise ValueError("market-offset label must be binary")
    offset = logit(frame[market_probability_column])
    memberships = list(membership_columns)
    weights = (
        composite_grain_weights(
            frame,
            membership_columns=memberships,
            date_column=date_column,
        )
        if memberships
        else _date_equal_weights(frame, date_column)
    )

    def objective(theta: np.ndarray) -> tuple[float, np.ndarray]:
        linear = offset + design @ theta
        probability = expit(linear)
        loss = np.average(
            np.logaddexp(0.0, linear) - labels * linear,
            weights=weights,
        ) + l2_strength * float(theta[1:] @ theta[1:])
        residual = weights * (probability - labels) / weights.sum()
        gradient = design.T @ residual
        gradient[1:] += 2.0 * l2_strength * theta[1:]
        return float(loss), gradient

    fitted = minimize(
        objective,
        np.zeros(design.shape[1]),
        method="L-BFGS-B",
        jac=True,
        options={"maxiter": 1000},
    )
    if not fitted.success:
        raise RuntimeError(f"market-offset fit failed: {fitted.message}")
    return {
        "schema_version": "fixed_market_logit_offset_v1",
        "feature_columns": features,
        "market_probability_column": market_probability_column,
        "label_column": label_column,
        "date_column": date_column,
        "l2_strength": float(l2_strength),
        "intercept": float(fitted.x[0]),
        "coefficients": [float(value) for value in fitted.x[1:]],
        "medians": {key: float(value) for key, value in medians.items()},
        "means": {key: float(value) for key, value in means.items()},
        "scales": {key: float(value) for key, value in scales.items()},
        "training_rows": int(len(frame)),
        "training_dates": int(frame[date_column].astype(str).nunique()),
        "optimizer_objective": float(fitted.fun),
        "training_membership_columns": memberships,
    }


def _market_offset_correction(
    artifact: Mapping[str, Any], frame: pd.DataFrame
) -> np.ndarray:
    """Return the fitted logit correction before the market offset is added."""

    features = list(artifact["feature_columns"])
    missing = sorted(set(features) - set(frame.columns))
    if missing:
        raise ValueError(f"market-offset scoring frame missing columns: {missing}")
    numeric = frame[features].apply(pd.to_numeric, errors="coerce")
    medians = pd.Series(artifact["medians"], dtype=float).reindex(features)
    means = pd.Series(artifact["means"], dtype=float).reindex(features)
    scales = pd.Series(artifact["scales"], dtype=float).reindex(features)
    design = ((numeric.fillna(medians) - means) / scales).to_numpy(dtype=float)
    correction = float(artifact["intercept"]) + design @ np.asarray(
        artifact["coefficients"], dtype=float
    )
    correction *= float(artifact.get("correction_scale", 1.0))
    cap = artifact.get("correction_cap_logit")
    if cap is None:
        return correction
    cap_value = float(cap)
    if cap_value < 0:
        raise ValueError("correction_cap_logit must be non-negative")
    if cap_value == 0:
        return np.zeros(len(correction), dtype=float)
    return cap_value * np.tanh(correction / cap_value)


def predict_fixed_market_offset(
    artifact: Mapping[str, Any],
    frame: pd.DataFrame,
    *,
    market_probability: pd.Series | np.ndarray | None = None,
) -> np.ndarray:
    correction = _market_offset_correction(artifact, frame)
    if market_probability is None:
        market_probability = frame[str(artifact["market_probability_column"])]
    return expit(logit(market_probability) + correction)


def multi_grain_binary_score(
    frame: pd.DataFrame,
    probability: pd.Series | np.ndarray,
    *,
    label_column: str,
    membership_columns: Sequence[str],
    date_column: str = "target_date",
) -> dict[str, Any]:
    """Score checkpoint and structural grains with a fixed equal-weight objective."""

    values = np.asarray(probability, dtype=float)
    if len(values) != len(frame):
        raise ValueError("probability length does not match scoring frame")
    missing = sorted(set(membership_columns) - set(frame.columns))
    if missing:
        raise ValueError(f"multi-grain frame missing columns: {missing}")
    by_grain: dict[str, dict[str, float | int]] = {
        "checkpoint": date_equal_binary_score(
            frame,
            values,
            label_column=label_column,
            date_column=date_column,
        )
    }
    for column in membership_columns:
        mask = frame[column].astype(bool).to_numpy()
        if not mask.any():
            raise ValueError(f"multi-grain membership has no rows: {column}")
        by_grain[column] = date_equal_binary_score(
            frame.loc[mask],
            values[mask],
            label_column=label_column,
            date_column=date_column,
        )
    return {
        "objective_logloss": float(
            np.mean([float(score["logloss"]) for score in by_grain.values()])
        ),
        "objective_brier": float(
            np.mean([float(score["brier"]) for score in by_grain.values()])
        ),
        "grain_weights": {
            grain: 1.0 / len(by_grain) for grain in by_grain
        },
        "by_grain": by_grain,
    }


def date_equal_binary_score(
    frame: pd.DataFrame,
    probability: pd.Series | np.ndarray,
    *,
    label_column: str,
    date_column: str = "target_date",
) -> dict[str, float | int]:
    labels = pd.to_numeric(frame[label_column], errors="raise").to_numpy(dtype=float)
    values = np.clip(np.asarray(probability, dtype=float), EPSILON, 1.0 - EPSILON)
    scored = pd.DataFrame(
        {
            "target_date": frame[date_column].astype(str).to_numpy(),
            "brier": (values - labels) ** 2,
            "logloss": -(
                labels * np.log(values) + (1.0 - labels) * np.log(1.0 - values)
            ),
            "accuracy": (values >= 0.5) == labels.astype(bool),
        }
    ).groupby("target_date", sort=True).mean()
    return {
        "rows": int(len(frame)),
        "target_dates": int(len(scored)),
        "brier": float(scored["brier"].mean()),
        "logloss": float(scored["logloss"].mean()),
        "accuracy_0_5": float(scored["accuracy"].mean()),
    }


def date_block_score_delta(
    frame: pd.DataFrame,
    candidate_probability: pd.Series | np.ndarray,
    baseline_probability: pd.Series | np.ndarray,
    *,
    label_column: str,
    date_column: str = "target_date",
    draws: int = 10_000,
    seed: int = 20260812,
) -> dict[str, Any]:
    labels = pd.to_numeric(frame[label_column], errors="raise").to_numpy(dtype=float)
    candidate = np.clip(
        np.asarray(candidate_probability, dtype=float), EPSILON, 1.0 - EPSILON
    )
    baseline = np.clip(
        np.asarray(baseline_probability, dtype=float), EPSILON, 1.0 - EPSILON
    )
    daily = pd.DataFrame(
        {
            "target_date": frame[date_column].astype(str).to_numpy(),
            "brier": (candidate - labels) ** 2 - (baseline - labels) ** 2,
            "logloss": -(
                labels * np.log(candidate)
                + (1.0 - labels) * np.log(1.0 - candidate)
            )
            + labels * np.log(baseline)
            + (1.0 - labels) * np.log(1.0 - baseline),
        }
    ).groupby("target_date", sort=True).mean()
    rng = np.random.default_rng(seed)
    indexes = rng.integers(0, len(daily), size=(draws, len(daily)))
    output: dict[str, Any] = {
        "rows": int(len(frame)),
        "target_dates": int(len(daily)),
        "draws": int(draws),
    }
    for metric in ("brier", "logloss"):
        values = daily[metric].to_numpy(dtype=float)
        sampled = values[indexes].mean(axis=1)
        output[metric] = {
            "delta": float(values.mean()),
            "ci95": [float(value) for value in np.quantile(sampled, [0.025, 0.975])],
        }
    return output


def select_market_offset_model(
    frame: pd.DataFrame,
    *,
    feature_sets: Mapping[str, Sequence[str]],
    l2_grid: Sequence[float],
    fit_window: tuple[str, str],
    validation_window: tuple[str, str],
    refit_window: tuple[str, str],
    market_probability_column: str,
    label_column: str,
    date_column: str = "target_date",
    membership_columns: Sequence[str] = (),
    correction_cap_grid: Sequence[float | None] = (None,),
    correction_scale_grid: Sequence[float] = (1.0,),
) -> tuple[dict[str, Any], dict[str, Any]]:
    dates = frame[date_column].astype(str)
    fit_rows = frame.loc[dates.between(*fit_window)].copy()
    validation = frame.loc[dates.between(*validation_window)].copy()
    refit = frame.loc[dates.between(*refit_window)].copy()
    if fit_rows.empty or validation.empty or refit.empty:
        raise ValueError("market-offset fit/validation/refit windows must be non-empty")

    candidates: list[dict[str, Any]] = []
    for feature_set_id, features in feature_sets.items():
        for l2_strength in l2_grid:
            base_fitted = fit_fixed_market_offset(
                fit_rows,
                feature_columns=features,
                market_probability_column=market_probability_column,
                label_column=label_column,
                date_column=date_column,
                l2_strength=float(l2_strength),
                membership_columns=membership_columns,
            )
            for correction_cap in correction_cap_grid:
                for correction_scale in correction_scale_grid:
                    if float(correction_scale) < 0:
                        raise ValueError("correction scale must be non-negative")
                    fitted = dict(base_fitted)
                    fitted["correction_cap_logit"] = correction_cap
                    fitted["correction_scale"] = float(correction_scale)
                    probability = predict_fixed_market_offset(fitted, validation)
                    score = (
                        multi_grain_binary_score(
                            validation,
                            probability,
                            label_column=label_column,
                            membership_columns=membership_columns,
                            date_column=date_column,
                        )
                        if membership_columns
                        else date_equal_binary_score(
                            validation,
                            probability,
                            label_column=label_column,
                            date_column=date_column,
                        )
                    )
                    candidates.append(
                        {
                            "feature_set_id": feature_set_id,
                            "features": list(features),
                            "l2_strength": float(l2_strength),
                            "correction_cap_logit": correction_cap,
                            "correction_scale": float(correction_scale),
                            "validation": score,
                        }
                    )
    selected = min(
        candidates,
        key=lambda row: (
            float(
                row["validation"].get(
                    "objective_logloss", row["validation"].get("logloss")
                )
            ),
            float(
                row["validation"].get(
                    "objective_brier", row["validation"].get("brier")
                )
            ),
            len(row["features"]),
            float(row["l2_strength"]),
            float("inf")
            if row["correction_cap_logit"] is None
            else float(row["correction_cap_logit"]),
            float(row["correction_scale"]),
        ),
    )
    artifact = fit_fixed_market_offset(
        refit,
        feature_columns=selected["features"],
        market_probability_column=market_probability_column,
        label_column=label_column,
        date_column=date_column,
        l2_strength=float(selected["l2_strength"]),
        membership_columns=membership_columns,
    )
    artifact.update(
        {
            "schema_version": (
                "fixed_market_logit_offset_v2"
                if membership_columns or selected["correction_cap_logit"] is not None
                else artifact["schema_version"]
            ),
            "feature_set_id": selected["feature_set_id"],
            "correction_cap_logit": selected["correction_cap_logit"],
            "correction_scale": selected["correction_scale"],
            "selection_fit_window": list(fit_window),
            "selection_validation_window": list(validation_window),
            "refit_window": list(refit_window),
            "candidate_count": int(len(candidates)),
        }
    )
    return artifact, {
        "selected": selected,
        "candidates": candidates,
        "fit_rows": int(len(fit_rows)),
        "fit_dates": int(fit_rows[date_column].astype(str).nunique()),
        "validation_rows": int(len(validation)),
        "validation_dates": int(validation[date_column].astype(str).nunique()),
        "refit_rows": int(len(refit)),
        "refit_dates": int(refit[date_column].astype(str).nunique()),
        "membership_columns": list(membership_columns),
        "correction_cap_grid": list(correction_cap_grid),
        "correction_scale_grid": [float(value) for value in correction_scale_grid],
    }
