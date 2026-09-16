"""PIT-safe simplex calibration helpers for city weather models."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression


EPS = 1e-8


def simplex_log_ratio_features(
    probabilities: np.ndarray,
    *,
    context: np.ndarray | None = None,
    context_interactions: bool = True,
) -> np.ndarray:
    """Return additive-log-ratio features, optionally with PIT context.

    The last simplex class is the reference.  Context may contain only
    information available at decision time, such as forecast availability.
    """

    q = np.asarray(probabilities, dtype=float)
    if q.ndim != 2 or q.shape[1] < 2:
        raise ValueError("probabilities must be a 2-D simplex")
    if not np.isfinite(q).all() or (q < 0).any():
        raise ValueError("probabilities must be finite and non-negative")
    row_sum = q.sum(axis=1, keepdims=True)
    if (row_sum <= 0).any():
        raise ValueError("probability rows must have positive mass")
    q = np.clip(q / row_sum, EPS, None)
    q /= q.sum(axis=1, keepdims=True)
    log_ratio = np.log(q[:, :-1]) - np.log(q[:, [-1]])
    if context is None:
        return log_ratio
    value = np.asarray(context, dtype=float)
    if value.ndim == 1:
        value = value[:, None]
    if value.ndim != 2 or value.shape[0] != len(q):
        raise ValueError("context must have one row per probability row")
    if not np.isfinite(value).all():
        raise ValueError("context must be finite")
    pieces = [log_ratio, value]
    if context_interactions:
        pieces.append(
            (log_ratio[:, :, None] * value[:, None, :]).reshape(
                len(q), -1
            )
        )
    return np.column_stack(pieces)


def fit_simplex_logit_calibrator(
    probabilities: np.ndarray,
    labels: Sequence[int] | np.ndarray,
    *,
    sample_weight: Sequence[float] | np.ndarray | None = None,
    context: np.ndarray | None = None,
    context_interactions: bool = True,
    class_count: int | None = None,
) -> dict[str, Any]:
    """Fit fixed-L2 multinomial calibration without threshold tuning."""

    q = np.asarray(probabilities, dtype=float)
    y = np.asarray(labels, dtype=int)
    if len(q) != len(y):
        raise ValueError("probability and label rows must match")
    expected = q.shape[1] if class_count is None else int(class_count)
    if expected != q.shape[1]:
        raise ValueError("class_count must match probability columns")
    if ((y < 0) | (y >= expected)).any():
        raise ValueError("label outside calibration classes")
    classes = np.unique(y)
    if not np.array_equal(classes, np.arange(expected)):
        raise ValueError(
            f"calibration training lacks classes: observed={classes.tolist()}"
        )
    weight = None
    if sample_weight is not None:
        weight = np.asarray(sample_weight, dtype=float)
        if weight.shape != (len(q),):
            raise ValueError("sample_weight must be one-dimensional")
        if not np.isfinite(weight).all() or (weight < 0).any():
            raise ValueError("sample_weight must be finite and non-negative")
    features = simplex_log_ratio_features(
        q,
        context=context,
        context_interactions=context_interactions,
    )
    model = LogisticRegression(
        C=1.0,
        solver="lbfgs",
        max_iter=2000,
        random_state=20260731,
    )
    model.fit(features, y, sample_weight=weight)
    return {
        "model": model,
        "class_count": expected,
        "context_columns": (
            0
            if context is None
            else int(np.asarray(context).reshape(len(q), -1).shape[1])
        ),
        "context_interactions": bool(context_interactions),
    }


def predict_simplex_logit_calibrator(
    calibrator: dict[str, Any],
    probabilities: np.ndarray,
    *,
    context: np.ndarray | None = None,
) -> np.ndarray:
    """Apply a fitted simplex calibrator and retain the full class order."""

    q = np.asarray(probabilities, dtype=float)
    expected_context = int(calibrator["context_columns"])
    actual_context = (
        0
        if context is None
        else int(np.asarray(context).reshape(len(q), -1).shape[1])
    )
    if actual_context != expected_context:
        raise ValueError(
            f"expected {expected_context} context columns, "
            f"got {actual_context}"
        )
    features = simplex_log_ratio_features(
        q,
        context=context,
        context_interactions=bool(calibrator["context_interactions"]),
    )
    raw = calibrator["model"].predict_proba(features)
    output = np.zeros((len(q), int(calibrator["class_count"])), dtype=float)
    output[:, calibrator["model"].classes_.astype(int)] = raw
    output = np.clip(output, EPS, None)
    output /= output.sum(axis=1, keepdims=True)
    return output
