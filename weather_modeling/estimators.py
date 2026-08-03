"""Pluggable probability estimators for weather research experiments."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


class ProbabilityEstimator(Protocol):
    def fit(
        self,
        frame: pd.DataFrame,
        *,
        features: Sequence[str],
        label_column: str,
        sample_weight_column: str | None = None,
    ) -> None: ...

    def predict(
        self,
        frame: pd.DataFrame,
        *,
        features: Sequence[str],
    ) -> np.ndarray: ...


EstimatorFactory = Callable[[Mapping[str, Any]], ProbabilityEstimator]
_ESTIMATORS: dict[str, EstimatorFactory] = {}


def register_estimator(name: str) -> Callable[[EstimatorFactory], EstimatorFactory]:
    def decorator(factory: EstimatorFactory) -> EstimatorFactory:
        if name in _ESTIMATORS:
            raise ValueError(f"estimator already registered: {name}")
        _ESTIMATORS[name] = factory
        return factory

    return decorator


def build_estimator(name: str, parameters: Mapping[str, Any]) -> ProbabilityEstimator:
    try:
        factory = _ESTIMATORS[name]
    except KeyError as exc:
        raise ValueError(
            f"unknown estimator {name!r}; registered={sorted(_ESTIMATORS)}"
        ) from exc
    return factory(parameters)


def registered_estimators() -> tuple[str, ...]:
    return tuple(sorted(_ESTIMATORS))


class MarketIdentityEstimator:
    def __init__(self, probability_column: str):
        self.probability_column = probability_column

    def fit(
        self,
        frame: pd.DataFrame,
        *,
        features: Sequence[str],
        label_column: str,
        sample_weight_column: str | None = None,
    ) -> None:
        del frame, features, label_column, sample_weight_column

    def predict(
        self,
        frame: pd.DataFrame,
        *,
        features: Sequence[str],
    ) -> np.ndarray:
        del features
        return (
            pd.to_numeric(frame[self.probability_column], errors="coerce")
            .clip(1e-6, 1.0 - 1e-6)
            .to_numpy(float)
        )


class LogisticProbabilityEstimator:
    def __init__(self, *, c_value: float, max_iter: int):
        self.pipeline = Pipeline(
            [
                (
                    "imputer",
                    SimpleImputer(
                        strategy="constant",
                        fill_value=0.0,
                        add_indicator=True,
                        keep_empty_features=True,
                    ),
                ),
                ("scale", StandardScaler()),
                (
                    "model",
                    LogisticRegression(
                        C=c_value,
                        max_iter=max_iter,
                        solver="lbfgs",
                        random_state=0,
                    ),
                ),
            ]
        )

    @staticmethod
    def _matrix(frame: pd.DataFrame, features: Sequence[str]) -> pd.DataFrame:
        if not features:
            raise ValueError("logistic estimator requires at least one feature")
        return frame.loc[:, list(features)].apply(pd.to_numeric, errors="coerce")

    def fit(
        self,
        frame: pd.DataFrame,
        *,
        features: Sequence[str],
        label_column: str,
        sample_weight_column: str | None = None,
    ) -> None:
        label = pd.to_numeric(frame[label_column], errors="raise").astype(int)
        if label.nunique() < 2:
            raise ValueError("training fold has only one label class")
        fit_params: dict[str, Any] = {}
        if sample_weight_column is not None:
            fit_params["model__sample_weight"] = pd.to_numeric(
                frame[sample_weight_column], errors="raise"
            ).to_numpy(float)
        self.pipeline.fit(self._matrix(frame, features), label, **fit_params)

    def predict(
        self,
        frame: pd.DataFrame,
        *,
        features: Sequence[str],
    ) -> np.ndarray:
        return np.clip(
            self.pipeline.predict_proba(self._matrix(frame, features))[:, 1],
            1e-6,
            1.0 - 1e-6,
        )


class HistGradientBoostingProbabilityEstimator:
    def __init__(self, parameters: Mapping[str, Any]):
        self.model = HistGradientBoostingClassifier(
            learning_rate=float(parameters.get("learning_rate", 0.05)),
            max_iter=int(parameters.get("max_iter", 150)),
            max_leaf_nodes=int(parameters.get("max_leaf_nodes", 15)),
            min_samples_leaf=int(parameters.get("min_samples_leaf", 100)),
            l2_regularization=float(parameters.get("l2_regularization", 2.0)),
            early_stopping=False,
            random_state=0,
        )

    @staticmethod
    def _matrix(frame: pd.DataFrame, features: Sequence[str]) -> pd.DataFrame:
        if not features:
            raise ValueError(
                "hist_gradient_boosting estimator requires at least one feature"
            )
        return frame.loc[:, list(features)].apply(pd.to_numeric, errors="coerce")

    def fit(
        self,
        frame: pd.DataFrame,
        *,
        features: Sequence[str],
        label_column: str,
        sample_weight_column: str | None = None,
    ) -> None:
        label = pd.to_numeric(frame[label_column], errors="raise").astype(int)
        if label.nunique() < 2:
            raise ValueError("training fold has only one label class")
        sample_weight = None
        if sample_weight_column is not None:
            sample_weight = pd.to_numeric(
                frame[sample_weight_column], errors="raise"
            ).to_numpy(float)
        self.model.fit(
            self._matrix(frame, features),
            label,
            sample_weight=sample_weight,
        )

    def predict(
        self,
        frame: pd.DataFrame,
        *,
        features: Sequence[str],
    ) -> np.ndarray:
        return np.clip(
            self.model.predict_proba(self._matrix(frame, features))[:, 1],
            1e-6,
            1.0 - 1e-6,
        )


class MultiHorizonHistGradientBoostingProbabilityEstimator:
    """Separate 1h/2h/EOD heads with monotone probability projection."""

    def __init__(self, parameters: Mapping[str, Any]):
        labels = parameters.get("horizon_label_columns")
        if not isinstance(labels, Sequence) or isinstance(labels, str):
            raise ValueError("horizon_label_columns must be a sequence")
        self.horizon_label_columns = [str(value) for value in labels]
        self.parameters = dict(parameters)
        self.models: dict[str, HistGradientBoostingClassifier] = {}
        self.eod_label_column: str | None = None

    def _new_model(self) -> HistGradientBoostingClassifier:
        return HistGradientBoostingClassifier(
            learning_rate=float(self.parameters.get("learning_rate", 0.05)),
            max_iter=int(self.parameters.get("max_iter", 150)),
            max_leaf_nodes=int(
                self.parameters.get("max_leaf_nodes", 15)
            ),
            min_samples_leaf=int(
                self.parameters.get("min_samples_leaf", 100)
            ),
            l2_regularization=float(
                self.parameters.get("l2_regularization", 2.0)
            ),
            early_stopping=False,
            random_state=0,
        )

    @staticmethod
    def _matrix(frame: pd.DataFrame, features: Sequence[str]) -> pd.DataFrame:
        if not features:
            raise ValueError("multi-horizon estimator requires features")
        return frame.loc[:, list(features)].apply(
            pd.to_numeric, errors="coerce"
        )

    def fit(
        self,
        frame: pd.DataFrame,
        *,
        features: Sequence[str],
        label_column: str,
        sample_weight_column: str | None = None,
    ) -> None:
        self.eod_label_column = label_column
        labels = [*self.horizon_label_columns, label_column]
        sample_weight = None
        if sample_weight_column is not None:
            sample_weight = pd.to_numeric(
                frame[sample_weight_column], errors="raise"
            ).to_numpy(float)
        matrix = self._matrix(frame, features)
        self.models = {}
        for horizon_label in labels:
            label_values = pd.to_numeric(
                frame[horizon_label], errors="raise"
            )
            valid = label_values.notna()
            label = label_values.loc[valid].astype(int)
            if label.nunique() < 2:
                raise ValueError(
                    f"training fold has one class for {horizon_label}"
                )
            model = self._new_model()
            horizon_weight = (
                sample_weight[valid.to_numpy()]
                if sample_weight is not None
                else None
            )
            model.fit(
                matrix.loc[valid],
                label,
                sample_weight=horizon_weight,
            )
            self.models[horizon_label] = model

    def predict_horizons(
        self,
        frame: pd.DataFrame,
        *,
        features: Sequence[str],
    ) -> dict[str, np.ndarray]:
        if self.eod_label_column is None:
            raise ValueError("multi-horizon estimator is not fitted")
        matrix = self._matrix(frame, features)
        raw = [
            np.clip(
                self.models[label].predict_proba(matrix)[:, 1],
                1e-6,
                1.0 - 1e-6,
            )
            for label in [
                *self.horizon_label_columns,
                self.eod_label_column,
            ]
        ]
        projected = np.maximum.accumulate(np.vstack(raw), axis=0)
        return {
            label: projected[index]
            for index, label in enumerate(
                [
                    *self.horizon_label_columns,
                    self.eod_label_column,
                ]
            )
        }

    def predict(
        self,
        frame: pd.DataFrame,
        *,
        features: Sequence[str],
    ) -> np.ndarray:
        if self.eod_label_column is None:
            raise ValueError("multi-horizon estimator is not fitted")
        return self.predict_horizons(frame, features=features)[
            self.eod_label_column
        ]


@register_estimator("market_identity")
def _market_identity_factory(
    parameters: Mapping[str, Any],
) -> ProbabilityEstimator:
    return MarketIdentityEstimator(
        str(parameters.get("probability_column") or "market_probability")
    )


@register_estimator("logistic")
def _logistic_factory(parameters: Mapping[str, Any]) -> ProbabilityEstimator:
    return LogisticProbabilityEstimator(
        c_value=float(parameters.get("c") or 0.3),
        max_iter=int(parameters.get("max_iter") or 2_000),
    )


@register_estimator("hist_gradient_boosting")
def _hist_gradient_boosting_factory(
    parameters: Mapping[str, Any],
) -> ProbabilityEstimator:
    return HistGradientBoostingProbabilityEstimator(parameters)


@register_estimator("multi_horizon_hist_gradient_boosting")
def _multi_horizon_hist_gradient_boosting_factory(
    parameters: Mapping[str, Any],
) -> ProbabilityEstimator:
    return MultiHorizonHistGradientBoostingProbabilityEstimator(parameters)
