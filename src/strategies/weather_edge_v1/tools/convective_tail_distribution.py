"""Continuous full-ladder residual models for the Convective Tail sleeve.

The module is deliberately execution-agnostic.  It accepts a normalized market
distribution plus PIT weather features and returns another normalized exact-
bracket distribution.  Callers may journal ModelOutput/SignalCandidate rows,
but this module never creates an intent or touches an exchange.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
from scipy.optimize import minimize


EPS = 5e-4
BASE_FEATURES = (
    "city_bias_pit_f",
    "source_bias_pit_f",
    "peak_pop_pct",
    "peak_cloud_pct",
    "peak_wind_kt",
    "relative_humidity_pct",
    "forecast_dispersion_f",
    "warming_innovation_f",
    "instant_innovation_f",
    "forecast_remaining_warming_f",
    "local_hour_sin",
    "local_hour_cos",
)
CHALLENGERS = (
    "c1_market_power_temperature",
    "c2_adjacent_kernel_diffusion",
    "c3_center_curvature_offset",
)
REGULARIZATION = {
    "c1_market_power_temperature": 20.0,
    "c2_adjacent_kernel_diffusion": 20.0,
    "c3_center_curvature_offset": 30.0,
}


@dataclass(frozen=True)
class FeatureTransform:
    feature_names: tuple[str, ...]
    medians: tuple[float, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_names": list(self.feature_names),
            "medians": list(self.medians),
            "means": list(self.means),
            "scales": list(self.scales),
            "output_contract": "standardized_values_then_missing_indicators",
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "FeatureTransform":
        return cls(
            tuple(str(x) for x in raw["feature_names"]),
            tuple(float(x) for x in raw["medians"]),
            tuple(float(x) for x in raw["means"]),
            tuple(float(x) for x in raw["scales"]),
        )


def _number(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return math.nan
    return out if math.isfinite(out) else math.nan


def fit_feature_transform(rows: Sequence[Mapping[str, Any]]) -> FeatureTransform:
    values = np.array([[_number(row.get(name)) for name in BASE_FEATURES] for row in rows], dtype=float)
    medians = []
    for column in values.T:
        finite = column[np.isfinite(column)]
        medians.append(float(np.median(finite)) if len(finite) else 0.0)
    filled = np.where(np.isfinite(values), values, np.asarray(medians))
    means = filled.mean(axis=0) if len(filled) else np.zeros(len(BASE_FEATURES))
    scales = filled.std(axis=0) if len(filled) else np.ones(len(BASE_FEATURES))
    scales = np.where(scales < 1e-9, 1.0, scales)
    return FeatureTransform(BASE_FEATURES, tuple(medians), tuple(means), tuple(scales))


def transform_features(rows: Sequence[Mapping[str, Any]], transform: FeatureTransform) -> np.ndarray:
    values = np.array([[_number(row.get(name)) for name in transform.feature_names] for row in rows], dtype=float)
    missing = ~np.isfinite(values)
    filled = np.where(missing, np.asarray(transform.medians), values)
    standardized = (filled - np.asarray(transform.means)) / np.asarray(transform.scales)
    return np.concatenate([standardized, missing.astype(float)], axis=1)


def normalize_market(probabilities: Iterable[float]) -> np.ndarray:
    p = np.asarray(list(probabilities), dtype=float)
    p = np.where(np.isfinite(p), p, EPS)
    p = np.clip(p, EPS, 1.0 - EPS)
    return p / p.sum()


def native_distance_z(distances: Iterable[float]) -> np.ndarray:
    distance = np.asarray(list(distances), dtype=float)
    ordered = np.unique(np.sort(distance[np.isfinite(distance)]))
    diffs = np.diff(ordered)
    step = float(np.median(diffs[diffs > 1e-9])) if np.any(diffs > 1e-9) else 1.0
    return np.clip(np.where(np.isfinite(distance), distance / step, 0.0), -6.0, 6.0)


def adjacent_diffusion(p: np.ndarray) -> np.ndarray:
    if len(p) == 1:
        return p.copy()
    out = 0.50 * p
    out[:-1] += 0.25 * p[1:]
    out[1:] += 0.25 * p[:-1]
    out[0] += 0.25 * p[0]
    out[-1] += 0.25 * p[-1]
    return out / out.sum()


def initial_parameters(challenger: str, dimensions: int) -> np.ndarray:
    if challenger == "c1_market_power_temperature":
        # tau=1 under the registered [0.67, 2.5] logistic map.
        intercept = math.log((1.0 - 0.67) / (2.5 - 1.0))
        return np.r_[intercept, np.zeros(dimensions)]
    if challenger == "c2_adjacent_kernel_diffusion":
        return np.r_[-4.0, np.zeros(dimensions)]
    if challenger == "c3_center_curvature_offset":
        center = np.r_[0.0, np.zeros(dimensions)]
        tail = np.r_[-4.0, np.zeros(dimensions)]
        return np.r_[center, tail]
    raise ValueError(f"unknown challenger: {challenger}")


def apply_challenger(
    challenger: str,
    market: Iterable[float],
    feature_vector: Iterable[float],
    parameters: Iterable[float],
    distances: Iterable[float],
) -> np.ndarray:
    p = normalize_market(market)
    x = np.r_[1.0, np.asarray(list(feature_vector), dtype=float)]
    theta = np.asarray(list(parameters), dtype=float)
    if challenger == "c1_market_power_temperature":
        eta = float(x @ theta)
        tau = 0.67 + (2.5 - 0.67) / (1.0 + math.exp(-float(np.clip(eta, -30, 30))))
        q = np.exp(np.log(p) / tau)
    elif challenger == "c2_adjacent_kernel_diffusion":
        eta = float(x @ theta)
        weight = 1.0 / (1.0 + math.exp(-float(np.clip(eta, -30, 30))))
        q = (1.0 - weight) * p + weight * adjacent_diffusion(p)
    elif challenger == "c3_center_curvature_offset":
        width = len(x)
        if len(theta) != 2 * width:
            raise ValueError("C3 parameter length does not match feature transform")
        center_eta = float(x @ theta[:width])
        tail_eta = float(x @ theta[width:])
        center = 2.0 * math.tanh(center_eta)
        tail = 1.5 / (1.0 + math.exp(-float(np.clip(tail_eta, -30, 30))))
        z = native_distance_z(distances)
        centered_square = z**2 - float(np.sum(p * z**2))
        logits = np.log(p) + center * z + tail * centered_square
        logits -= float(np.max(logits))
        q = np.exp(logits)
    else:
        raise ValueError(f"unknown challenger: {challenger}")
    q = np.clip(q, EPS, None)
    return q / q.sum()


def fit_challenger(
    challenger: str,
    feature_matrix: np.ndarray,
    market_distributions: Sequence[np.ndarray],
    outcomes: Sequence[np.ndarray],
    distances: Sequence[np.ndarray],
    state_weights: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    dimensions = int(feature_matrix.shape[1])
    initial = initial_parameters(challenger, dimensions)
    regularization = REGULARIZATION[challenger]
    weights = np.asarray(state_weights, dtype=float)
    weights = weights / weights.sum()
    design = np.c_[np.ones(len(feature_matrix)), feature_matrix]
    prepared_market = [normalize_market(value) for value in market_distributions]
    prepared_z = [native_distance_z(value) for value in distances]

    def objective(theta: np.ndarray) -> tuple[float, np.ndarray]:
        loss = 0.0
        gradient = np.zeros_like(theta)
        for index, (p, y, z) in enumerate(zip(prepared_market, outcomes, prepared_z, strict=True)):
            x = design[index]
            if challenger == "c1_market_power_temperature":
                eta = float(x @ theta)
                logistic = 1.0 / (1.0 + math.exp(-float(np.clip(eta, -30, 30))))
                tau = 0.67 + (2.5 - 0.67) * logistic
                logits = np.log(p) / tau
                logits -= float(np.max(logits))
                q = np.exp(logits)
                q /= q.sum()
                derivative = (
                    float(np.sum(y * np.log(p))) - float(np.sum(q * np.log(p)))
                ) / (tau**2)
                derivative *= (2.5 - 0.67) * logistic * (1.0 - logistic)
                gradient += weights[index] * derivative * x
            elif challenger == "c2_adjacent_kernel_diffusion":
                eta = float(x @ theta)
                weight = 1.0 / (1.0 + math.exp(-float(np.clip(eta, -30, 30))))
                kernel = adjacent_diffusion(p)
                q = (1.0 - weight) * p + weight * kernel
                derivative = -float(np.sum(y * (kernel - p) / np.clip(q, EPS, 1.0)))
                derivative *= weight * (1.0 - weight)
                gradient += weights[index] * derivative * x
            elif challenger == "c3_center_curvature_offset":
                width = dimensions + 1
                center_eta = float(x @ theta[:width])
                tail_eta = float(x @ theta[width:])
                center_tanh = math.tanh(center_eta)
                tail_logistic = 1.0 / (1.0 + math.exp(-float(np.clip(tail_eta, -30, 30))))
                center = 2.0 * center_tanh
                tail = 1.5 * tail_logistic
                centered_square = z**2 - float(np.sum(p * z**2))
                logits = np.log(p) + center * z + tail * centered_square
                logits -= float(np.max(logits))
                q = np.exp(logits)
                q /= q.sum()
                center_derivative = float(np.sum((q - y) * z)) * 2.0 * (1.0 - center_tanh**2)
                tail_derivative = (
                    float(np.sum((q - y) * centered_square))
                    * 1.5
                    * tail_logistic
                    * (1.0 - tail_logistic)
                )
                gradient[:width] += weights[index] * center_derivative * x
                gradient[width:] += weights[index] * tail_derivative * x
            else:
                raise ValueError(f"unknown challenger: {challenger}")
            loss += weights[index] * float(-np.sum(y * np.log(np.clip(q, EPS, 1.0))))
        # Intercepts are not penalized. Scale ridge by state count.
        if challenger == "c3_center_curvature_offset":
            width = dimensions + 1
            mask = np.ones_like(theta)
            mask[0] = 0.0
            mask[width] = 0.0
        else:
            mask = np.ones_like(theta)
            mask[0] = 0.0
        penalty_scale = regularization / max(1, len(weights))
        loss += 0.5 * penalty_scale * float((theta * mask) @ (theta * mask))
        gradient += penalty_scale * theta * mask
        return float(loss), gradient

    result = minimize(objective, initial, method="L-BFGS-B", jac=True, options={"maxiter": 100, "ftol": 1e-9})
    return np.asarray(result.x, dtype=float), {
        "success": bool(result.success),
        "status": int(result.status),
        "message": str(result.message),
        "iterations": int(result.nit),
        "objective": float(result.fun),
        "regularization_l2": regularization,
    }


def score_from_artifact(
    artifact: Mapping[str, Any],
    feature_row: Mapping[str, Any],
    market: Iterable[float],
    distances: Iterable[float],
) -> np.ndarray:
    transform = FeatureTransform.from_dict(artifact["feature_transform"])
    x = transform_features([feature_row], transform)[0]
    return apply_challenger(
        str(artifact["selected_challenger"]),
        market,
        x,
        artifact["parameters"],
        distances,
    )
