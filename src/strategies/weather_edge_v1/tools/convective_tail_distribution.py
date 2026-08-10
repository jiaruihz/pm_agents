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
    "c4_directional_split_tail_offset",
    "c5_directional_neighbor_transport",
)
REGULARIZATION = {
    "c1_market_power_temperature": 20.0,
    "c2_adjacent_kernel_diffusion": 20.0,
    "c3_center_curvature_offset": 30.0,
    "c4_directional_split_tail_offset": 30.0,
    "c5_directional_neighbor_transport": 20.0,
}

C4_CENTER_DIMENSIONS = 8
C4_TAIL_DIMENSIONS = 7


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


def neighbor_transport(p: np.ndarray, direction: str) -> np.ndarray:
    """Retain half the mass and move half by one ordered ladder rung."""
    if len(p) == 1:
        return p.copy()
    out = 0.50 * p
    if direction == "hot":
        out[1:] += 0.50 * p[:-1]
        out[-1] += 0.50 * p[-1]
    elif direction == "cold":
        out[:-1] += 0.50 * p[1:]
        out[0] += 0.50 * p[0]
    else:
        raise ValueError(f"unknown transport direction: {direction}")
    return out / out.sum()


def directional_physical_projection(feature_vector: Iterable[float]) -> tuple[np.ndarray, np.ndarray]:
    """Project the wide PIT feature contract into low-dimensional physical heads.

    The projection keeps missing rows in the denominator.  Missing weather
    values contribute zero standardized signal, while the tail head receives
    the observed weather-field coverage fraction as a reliability feature.
    """
    vector = np.asarray(list(feature_vector), dtype=float)
    expected = 2 * len(BASE_FEATURES)
    if len(vector) != expected:
        raise ValueError(f"directional feature vector must have {expected} values")
    values = vector[: len(BASE_FEATURES)].copy()
    missing = vector[len(BASE_FEATURES) :] > 0.5
    values[missing] = 0.0

    weather_indices = np.array([2, 3, 4, 5], dtype=int)
    available = ~missing[weather_indices]
    coverage = float(available.mean())
    convective = float(values[weather_indices][available].mean()) if available.any() else 0.0
    warming = float(values[7])

    center = np.array(
        [1.0, values[0], values[1], warming, values[8], values[9], values[10], values[11]],
        dtype=float,
    )
    tail = np.array(
        [1.0, convective, coverage, values[6], warming, abs(warming), values[8]],
        dtype=float,
    )
    return center, tail


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
    if challenger == "c4_directional_split_tail_offset":
        center = np.r_[0.0, np.zeros(C4_CENTER_DIMENSIONS - 1)]
        hot = np.r_[-4.0, np.zeros(C4_TAIL_DIMENSIONS - 1)]
        cold = np.r_[-4.0, np.zeros(C4_TAIL_DIMENSIONS - 1)]
        return np.r_[center, hot, cold]
    if challenger == "c5_directional_neighbor_transport":
        direction = np.zeros(C4_CENTER_DIMENSIONS)
        width = np.r_[-4.0, np.zeros(C4_TAIL_DIMENSIONS - 1)]
        return np.r_[direction, width]
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
    elif challenger == "c4_directional_split_tail_offset":
        expected = C4_CENTER_DIMENSIONS + 2 * C4_TAIL_DIMENSIONS
        if len(theta) != expected:
            raise ValueError("C4 parameter length does not match physical projection")
        center_x, tail_x = directional_physical_projection(feature_vector)
        center_eta = float(center_x @ theta[:C4_CENTER_DIMENSIONS])
        hot_start = C4_CENTER_DIMENSIONS
        cold_start = hot_start + C4_TAIL_DIMENSIONS
        hot_eta = float(tail_x @ theta[hot_start:cold_start])
        cold_eta = float(tail_x @ theta[cold_start:])
        center = 2.0 * math.tanh(center_eta)
        hot = 1.5 / (1.0 + math.exp(-float(np.clip(hot_eta, -30, 30))))
        cold = 1.5 / (1.0 + math.exp(-float(np.clip(cold_eta, -30, 30))))
        z = native_distance_z(distances)
        hot_basis = np.maximum(z, 0.0) ** 2
        cold_basis = np.minimum(z, 0.0) ** 2
        hot_basis -= float(np.sum(p * hot_basis))
        cold_basis -= float(np.sum(p * cold_basis))
        logits = np.log(p) + center * z + hot * hot_basis + cold * cold_basis
        logits -= float(np.max(logits))
        q = np.exp(logits)
    elif challenger == "c5_directional_neighbor_transport":
        expected = C4_CENTER_DIMENSIONS + C4_TAIL_DIMENSIONS
        if len(theta) != expected:
            raise ValueError("C5 parameter length does not match physical projection")
        direction_x, width_x = directional_physical_projection(feature_vector)
        direction_eta = float(direction_x @ theta[:C4_CENTER_DIMENSIONS])
        width_eta = float(width_x @ theta[C4_CENTER_DIMENSIONS:])
        hot_share = 1.0 / (1.0 + math.exp(-float(np.clip(direction_eta, -30, 30))))
        width = 1.0 / (1.0 + math.exp(-float(np.clip(width_eta, -30, 30))))
        hot = neighbor_transport(p, "hot")
        cold = neighbor_transport(p, "cold")
        kernel = (1.0 - hot_share) * cold + hot_share * hot
        q = (1.0 - width) * p + width * kernel
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
            elif challenger == "c4_directional_split_tail_offset":
                center_x, tail_x = directional_physical_projection(feature_matrix[index])
                hot_start = C4_CENTER_DIMENSIONS
                cold_start = hot_start + C4_TAIL_DIMENSIONS
                center_eta = float(center_x @ theta[:hot_start])
                hot_eta = float(tail_x @ theta[hot_start:cold_start])
                cold_eta = float(tail_x @ theta[cold_start:])
                center_tanh = math.tanh(center_eta)
                hot_logistic = 1.0 / (1.0 + math.exp(-float(np.clip(hot_eta, -30, 30))))
                cold_logistic = 1.0 / (1.0 + math.exp(-float(np.clip(cold_eta, -30, 30))))
                center = 2.0 * center_tanh
                hot = 1.5 * hot_logistic
                cold = 1.5 * cold_logistic
                hot_basis = np.maximum(z, 0.0) ** 2
                cold_basis = np.minimum(z, 0.0) ** 2
                hot_basis -= float(np.sum(p * hot_basis))
                cold_basis -= float(np.sum(p * cold_basis))
                logits = np.log(p) + center * z + hot * hot_basis + cold * cold_basis
                logits -= float(np.max(logits))
                q = np.exp(logits)
                q /= q.sum()
                residual = q - y
                center_derivative = float(np.sum(residual * z)) * 2.0 * (1.0 - center_tanh**2)
                hot_derivative = float(np.sum(residual * hot_basis)) * 1.5 * hot_logistic * (1.0 - hot_logistic)
                cold_derivative = float(np.sum(residual * cold_basis)) * 1.5 * cold_logistic * (1.0 - cold_logistic)
                gradient[:hot_start] += weights[index] * center_derivative * center_x
                gradient[hot_start:cold_start] += weights[index] * hot_derivative * tail_x
                gradient[cold_start:] += weights[index] * cold_derivative * tail_x
            elif challenger == "c5_directional_neighbor_transport":
                direction_x, width_x = directional_physical_projection(feature_matrix[index])
                direction_eta = float(direction_x @ theta[:C4_CENTER_DIMENSIONS])
                width_eta = float(width_x @ theta[C4_CENTER_DIMENSIONS:])
                hot_share = 1.0 / (1.0 + math.exp(-float(np.clip(direction_eta, -30, 30))))
                width = 1.0 / (1.0 + math.exp(-float(np.clip(width_eta, -30, 30))))
                hot = neighbor_transport(p, "hot")
                cold = neighbor_transport(p, "cold")
                kernel = (1.0 - hot_share) * cold + hot_share * hot
                q = (1.0 - width) * p + width * kernel
                q = np.clip(q, EPS, 1.0)
                direction_derivative = -float(
                    np.sum(y * (width * hot_share * (1.0 - hot_share) * (hot - cold)) / q)
                )
                width_derivative = -float(
                    np.sum(y * ((kernel - p) * width * (1.0 - width)) / q)
                )
                gradient[:C4_CENTER_DIMENSIONS] += weights[index] * direction_derivative * direction_x
                gradient[C4_CENTER_DIMENSIONS:] += weights[index] * width_derivative * width_x
            else:
                raise ValueError(f"unknown challenger: {challenger}")
            loss += weights[index] * float(-np.sum(y * np.log(np.clip(q, EPS, 1.0))))
        # Intercepts are not penalized. Scale ridge by state count.
        if challenger == "c3_center_curvature_offset":
            width = dimensions + 1
            mask = np.ones_like(theta)
            mask[0] = 0.0
            mask[width] = 0.0
        elif challenger == "c4_directional_split_tail_offset":
            mask = np.ones_like(theta)
            mask[0] = 0.0
            mask[C4_CENTER_DIMENSIONS] = 0.0
            mask[C4_CENTER_DIMENSIONS + C4_TAIL_DIMENSIONS] = 0.0
        elif challenger == "c5_directional_neighbor_transport":
            mask = np.ones_like(theta)
            mask[0] = 0.0
            mask[C4_CENTER_DIMENSIONS] = 0.0
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
