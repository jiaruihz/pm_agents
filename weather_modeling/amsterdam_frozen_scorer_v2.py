"""Portable scorers for the frozen Amsterdam next-print B2/M1/M2 models.

The module is pure: it reads no runtime state and has no action, order, market,
or credential capability.  Callers pass the exact JSON scoring parameters and
one feature vector and receive normalized PMFs on the frozen integer support.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np


SCORER_VERSION = "amsterdam_frozen_scorer_v2"
EPS = 1e-7


def _normalized(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(np.asarray(values, dtype=float), EPS, None)
    return clipped / clipped.sum()


def _temperature_scale(pmf: np.ndarray, temperature: float) -> np.ndarray:
    logits = np.log(np.clip(pmf, EPS, 1.0)) / float(temperature)
    logits -= logits.max()
    return _normalized(np.exp(logits))


def _half_up(value: float) -> int:
    return int(np.floor(float(value) + 0.5))


def score_b2(parameters: Mapping[str, Any], features: Mapping[str, Any]) -> list[float]:
    support = np.asarray(parameters["support"], dtype=int)
    counts = {int(key): int(value) for key, value in parameters["training_residual_counts"].items()}
    alpha = int(parameters["laplace_alpha_per_support_tick"])
    total = sum(counts.values()) + alpha * len(support)
    predicted_delta = _half_up(float(features["latest_fast_native_value"])) - int(
        round(float(features["last_official_native_value"]))
    )
    pmf = np.zeros(len(support), dtype=float)
    for residual in support:
        outcome = int(np.clip(predicted_delta + int(residual), support[0], support[-1]))
        index = int(np.searchsorted(support, outcome))
        pmf[index] += (counts.get(int(residual), 0) + alpha) / total
    return _normalized(pmf).tolist()


def score_m1(parameters: Mapping[str, Any], features: Mapping[str, Any]) -> list[float]:
    names = list(parameters["features"])
    imputer = np.asarray(parameters["imputer_statistics"], dtype=float)
    means = np.asarray(parameters["scaler_mean"], dtype=float)
    scales = np.asarray(parameters["scaler_scale"], dtype=float)
    raw = np.asarray([
        np.nan if features.get(name) is None else float(features[name]) for name in names
    ])
    x = np.where(np.isfinite(raw), raw, imputer)
    x = (x - means) / scales
    cdf: list[float] = []
    for threshold in parameters["threshold_models"]:
        if "constant_cdf" in threshold:
            probability = float(threshold["constant_cdf"])
        else:
            coefficient = np.asarray(threshold["coef"], dtype=float)[0]
            intercept = float(np.asarray(threshold["intercept"], dtype=float)[0])
            logit = float(np.dot(coefficient, x) + intercept)
            probability = float(1.0 / (1.0 + np.exp(-np.clip(logit, -700, 700))))
        cdf.append(probability)
    cdf_array = np.clip(np.maximum.accumulate(np.asarray(cdf, dtype=float)), EPS, 1 - EPS)
    pmf = np.concatenate(([cdf_array[0]], np.diff(cdf_array), [1 - cdf_array[-1]]))
    return _temperature_scale(_normalized(pmf), float(parameters["temperature"])).tolist()


def score_m2(parameters: Mapping[str, Any], features: Mapping[str, Any]) -> list[float]:
    support = np.asarray(parameters["support"], dtype=float)
    mean = float(parameters["intercept"])
    for feature, fill, component in zip(
        parameters["features"],
        parameters["training_fill_values"],
        parameters["components"],
    ):
        raw = features.get(feature["name"])
        value = float(fill) if raw is None or not np.isfinite(float(raw)) else float(raw)
        mean += float(np.interp(
            value,
            np.asarray(component["x_thresholds"], dtype=float),
            np.asarray(component["y_thresholds"], dtype=float),
        ))
    scale = float(parameters["residual_scale"])
    pmf = np.exp(-0.5 * ((support - mean) / scale) ** 2)
    return _normalized(pmf).tolist()


def score_frozen_models(
    manifests: Mapping[str, Mapping[str, Any]],
    features: Mapping[str, Any],
) -> dict[str, list[float]]:
    """Score B2/M1/M2 from their portable manifest parameters."""

    parameters = {
        name: manifests[name]["frozen_scoring_parameters"] for name in ("B2", "M1", "M2")
    }
    result = {
        "B2": score_b2(parameters["B2"], features),
        "M1": score_m1(parameters["M1"], features),
        "M2": score_m2(parameters["M2"], features),
    }
    expected = len(parameters["B2"]["support"])
    for name, pmf in result.items():
        if len(pmf) != expected or not np.isclose(sum(pmf), 1.0, atol=1e-12):
            raise RuntimeError(f"invalid frozen {name} PMF")
    return result
