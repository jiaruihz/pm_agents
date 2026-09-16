"""Reusable coherent ordinal probability model for weather outcome lattices."""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin, clone


class OrderedThresholdClassifier(ClassifierMixin, BaseEstimator):
    """Fit cumulative ``P(y > k)`` heads and return a coherent class PMF."""

    def __init__(
        self,
        base_estimator: Any,
        *,
        n_classes: int,
        sample_weight_parameter: str = "sample_weight",
        epsilon: float = 1e-8,
    ) -> None:
        self.base_estimator = base_estimator
        self.n_classes = n_classes
        self.sample_weight_parameter = sample_weight_parameter
        self.epsilon = epsilon

    def fit(
        self,
        X: Any,
        y: Any,
        sample_weight: Any | None = None,
    ) -> "OrderedThresholdClassifier":
        labels = np.asarray(y, dtype=int)
        if labels.ndim != 1 or len(labels) == 0:
            raise ValueError("ordinal labels must be a non-empty 1D array")
        if labels.min() < 0 or labels.max() >= self.n_classes:
            raise ValueError("ordinal label outside configured classes")
        weights = (
            np.ones(len(labels), dtype=float)
            if sample_weight is None
            else np.asarray(sample_weight, dtype=float)
        )
        if weights.shape != labels.shape or not np.isfinite(weights).all() or (weights < 0).any():
            raise ValueError("invalid ordinal sample weights")

        self.estimators_: list[Any | None] = []
        self.constant_survival_: list[float | None] = []
        for threshold in range(self.n_classes - 1):
            binary = labels > threshold
            if binary.min() == binary.max():
                positive = float(np.average(binary.astype(float), weights=weights))
                self.estimators_.append(None)
                self.constant_survival_.append(positive)
                continue
            estimator = clone(self.base_estimator)
            estimator.fit(
                X,
                binary.astype(int),
                **{self.sample_weight_parameter: weights},
            )
            self.estimators_.append(estimator)
            self.constant_survival_.append(None)
        self.classes_ = np.arange(self.n_classes, dtype=int)
        return self

    def predict_survival(self, X: Any) -> np.ndarray:
        if not hasattr(self, "estimators_"):
            raise RuntimeError("ordered threshold classifier is not fitted")
        columns: list[np.ndarray] = []
        n_rows = len(X)
        for estimator, constant in zip(self.estimators_, self.constant_survival_, strict=True):
            if estimator is None:
                columns.append(np.full(n_rows, float(constant), dtype=float))
                continue
            classes = np.asarray(estimator.classes_, dtype=int)
            positive = np.flatnonzero(classes == 1)
            if len(positive) != 1:
                raise RuntimeError("binary ordinal head does not expose class 1")
            columns.append(estimator.predict_proba(X)[:, int(positive[0])])
        survival = np.column_stack(columns)
        survival = np.clip(survival, self.epsilon, 1.0 - self.epsilon)
        return np.minimum.accumulate(survival, axis=1)

    def predict_proba(self, X: Any) -> np.ndarray:
        survival = self.predict_survival(X)
        probability = np.empty((len(survival), self.n_classes), dtype=float)
        probability[:, 0] = 1.0 - survival[:, 0]
        probability[:, 1:-1] = survival[:, :-1] - survival[:, 1:]
        probability[:, -1] = survival[:, -1]
        probability = np.clip(probability, self.epsilon, None)
        return probability / probability.sum(axis=1, keepdims=True)
