"""Shared coherent quote calibrator used by tmax research and runtime."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


BUCKETS = ["current", "d1", "d2", "tail"]
KEYS = ["city", "target_date", "decision_hour_local", "actual_bucket"]
EPS = 1e-6
PRIMARY_SPEC = "coherent_cal_quote"
PRIMARY_C = 0.3
PRIMARY_ALPHA = 0.25

LOG_PROB_FEATURES = [f"base_log_p_{bucket}" for bucket in BUCKETS]
QUOTE_SOURCE_COLUMNS = [
    "current_yes_ask",
    "current_bracket_no_ask",
    "current_no_bid",
    "d1_no_ask",
    "d1_no_bid",
    "d2_no_ask",
    "d2_no_bid",
]
QUOTE_NUMERIC = [
    *QUOTE_SOURCE_COLUMNS,
    "current_no_spread",
    "d1_no_spread",
    "d2_no_spread",
    "d1_yes_effective_ask",
    "d2_yes_effective_ask",
]


def prepare_quote_rows(
    features: pd.DataFrame,
    base_predictions: pd.DataFrame,
    *,
    model_method: str,
    base_variant: str = "historical_full_features",
) -> pd.DataFrame:
    feature_rows = features[KEYS + QUOTE_SOURCE_COLUMNS].drop_duplicates(KEYS)
    base = base_predictions[base_predictions["variant"].eq(base_variant)].copy()
    out = base.merge(feature_rows, on=KEYS, how="inner", validate="one_to_one")
    for bucket in BUCKETS:
        source = f"{model_method}_p_{bucket}"
        out[f"base_p_{bucket}"] = pd.to_numeric(out[source], errors="coerce").clip(EPS, 1.0 - EPS)
        out[f"base_log_p_{bucket}"] = np.log(out[f"base_p_{bucket}"])
    out["current_no_spread"] = out["current_bracket_no_ask"] - out["current_no_bid"]
    out["d1_no_spread"] = out["d1_no_ask"] - out["d1_no_bid"]
    out["d2_no_spread"] = out["d2_no_ask"] - out["d2_no_bid"]
    out["d1_yes_effective_ask"] = 1.0 - out["d1_no_bid"]
    out["d2_yes_effective_ask"] = 1.0 - out["d2_no_bid"]
    out["target_date"] = out["target_date"].astype(str)
    return out


def make_quote_calibrator(c_value: float = PRIMARY_C) -> Pipeline:
    return Pipeline(
        [
            (
                "features",
                ColumnTransformer(
                    transformers=[
                        (
                            "num",
                            Pipeline(
                                [
                                    ("imputer", SimpleImputer(strategy="median")),
                                    ("scale", StandardScaler()),
                                ]
                            ),
                            [*LOG_PROB_FEATURES, *QUOTE_NUMERIC],
                        )
                    ],
                    remainder="drop",
                ),
            ),
            ("clf", LogisticRegression(C=float(c_value), solver="lbfgs", max_iter=3000)),
        ]
    )


def raw_quote_predictions(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    c_value: float = PRIMARY_C,
) -> pd.DataFrame:
    columns = [*LOG_PROB_FEATURES, *QUOTE_NUMERIC]
    model = make_quote_calibrator(c_value)
    model.fit(train[columns], train["actual_bucket"].astype(str))
    raw = model.predict_proba(test[columns])
    class_index = {str(value): idx for idx, value in enumerate(model.named_steps["clf"].classes_)}
    missing = [bucket for bucket in BUCKETS if bucket not in class_index]
    if missing:
        raise ValueError(f"quote calibrator training lacks outcome classes: {missing}")
    out = test[KEYS + [f"base_p_{bucket}" for bucket in BUCKETS]].copy()
    for bucket in BUCKETS:
        out[f"raw_cal_p_{bucket}"] = raw[:, class_index[bucket]]
    return out


def blend_quote_predictions(
    raw: pd.DataFrame,
    *,
    model_method: str,
    alpha: float = PRIMARY_ALPHA,
    variant: str = PRIMARY_SPEC,
) -> pd.DataFrame:
    out = raw[KEYS].copy()
    total = np.zeros(len(raw), dtype=float)
    blended: dict[str, np.ndarray] = {}
    for bucket in BUCKETS:
        values = (
            (1.0 - alpha) * raw[f"base_p_{bucket}"].to_numpy(dtype=float)
            + alpha * raw[f"raw_cal_p_{bucket}"].to_numpy(dtype=float)
        )
        blended[bucket] = np.maximum(EPS, values)
        total += blended[bucket]
    for bucket in BUCKETS:
        out[f"{model_method}_p_{bucket}"] = blended[bucket] / total
    out["variant"] = variant
    return out


def fit_predict_quote_calibrator(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    model_method: str,
    c_value: float = PRIMARY_C,
    alpha: float = PRIMARY_ALPHA,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    raw = raw_quote_predictions(train, test, c_value=c_value)
    pred = blend_quote_predictions(raw, model_method=model_method, alpha=alpha)
    return pred, {
        "calibrator_spec": PRIMARY_SPEC,
        "calibrator_c": c_value,
        "calibrator_alpha": alpha,
        "calibrator_fit_rows": int(len(train)),
        "calibrator_fit_dates": int(train["target_date"].nunique()),
    }
