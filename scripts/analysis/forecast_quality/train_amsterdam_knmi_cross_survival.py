#!/usr/bin/env python3
"""Train the Amsterdam KNMI previous-bracket survival probability head.

The target is deliberately narrower than remaining heat: after the first KNMI
ta cross of +0.5C for a target-date/current-bracket pair, estimate whether the
final EHAM settlement leaves that previous exact bracket.  Model selection is
confined to 2024; 2025 is a one-shot chronological test.  No market prices are
used by this weather head.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality.train_amsterdam_knmi_remaining_heat_v8 import (
    DEFAULT_DATASET,
    FEATURES,
    date_equal_weights,
)
from weather_modeling.forecast_path import (
    FORECAST_PATH_FEATURES,
    add_fixed_lead_forecast_path_features,
)
from weather_modeling.knmi_10m_path import add_knmi_10m_path_features
from weather_modeling.solar_geometry import add_solar_geometry_features


DEFAULT_FORECAST = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/research/"
    "amsterdam_ecmwf_previous_day1_path_v1/forecast_hourly.csv.gz"
)
DEFAULT_OUTPUT = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/research/model_runs/"
    "amsterdam_knmi_cross_survival"
)
EPS = 1e-7
MIN_MARGIN_C = 0.5
TRAIN_START = "2024-03-01"
SELECTION_TRAIN_END = "2024-09-30"
SELECTION_VALIDATION_START = "2024-10-01"
SELECTION_VALIDATION_END = "2024-12-31"
FROZEN_TEST_START = "2025-01-01"
FROZEN_TEST_END = "2025-12-31"
FINAL_TRAIN_END = "2025-12-31"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare(dataset: Path, forecast_path: Path) -> tuple[pd.DataFrame, list[str]]:
    frame = pd.read_csv(dataset)
    frame = frame[
        frame["target_date"].astype(str).between(TRAIN_START, FINAL_TRAIN_END)
    ].copy()
    frame = add_knmi_10m_path_features(frame)
    frame = add_solar_geometry_features(frame)
    frame = add_fixed_lead_forecast_path_features(frame, pd.read_csv(forecast_path))
    frame["ta_cross_margin_c"] = frame["ta_c"] - frame["current_bracket_c"]
    frame["tx_cross_margin_c"] = frame["tx_c"] - frame["current_bracket_c"]
    frame = frame[frame["ta_cross_margin_c"].ge(MIN_MARGIN_C - 1e-9)].copy()
    frame = frame.sort_values("observed_at_utc").drop_duplicates(
        ["target_date", "current_bracket_c"], keep="first"
    )
    frame["label"] = pd.to_numeric(frame["label_d1_cross_eod"], errors="coerce")
    frame = frame[frame["label"].notna()].copy()
    frame["label"] = frame["label"].astype(int)
    features = list(FEATURES) + list(FORECAST_PATH_FEATURES) + [
        "ta_cross_margin_c",
        "tx_cross_margin_c",
    ]
    missing = sorted(set(features) - set(frame.columns))
    if missing:
        raise ValueError(f"cross-survival feature contract incomplete: {missing}")
    return frame, features


def matrix(frame: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    return frame.loc[:, features].apply(pd.to_numeric, errors="coerce")


def candidates() -> dict[str, Any]:
    return {
        "cross_logistic_l2": make_pipeline(
            SimpleImputer(strategy="median", add_indicator=True),
            StandardScaler(),
            LogisticRegression(C=0.1, max_iter=3000, random_state=20260812),
        ),
        "cross_hgb_shallow": HistGradientBoostingClassifier(
            learning_rate=0.035,
            max_iter=220,
            max_leaf_nodes=15,
            min_samples_leaf=50,
            l2_regularization=5.0,
            early_stopping=False,
            random_state=20260812,
        ),
    }


def score(rows: pd.DataFrame, probability: np.ndarray) -> dict[str, Any]:
    scored = rows[["target_date", "label"]].copy()
    scored["p"] = np.clip(probability, EPS, 1 - EPS)
    scored["brier"] = (scored["p"] - scored["label"]) ** 2
    scored["logloss"] = -(
        scored["label"] * np.log(scored["p"])
        + (1 - scored["label"]) * np.log(1 - scored["p"])
    )
    daily = scored.groupby("target_date")[["brier", "logloss"]].mean()
    result = {
        "rows": int(len(rows)),
        "target_dates": int(rows["target_date"].nunique()),
        "positives": int(rows["label"].sum()),
        "negative_terminal_false": int((1 - rows["label"]).sum()),
        "brier_date_equal": float(daily["brier"].mean()),
        "logloss_date_equal": float(daily["logloss"].mean()),
        "accuracy_0_5": float(((scored["p"] >= .5) == scored["label"].astype(bool)).mean()),
        "mean_probability": float(scored["p"].mean()),
    }
    if rows["label"].nunique() == 2:
        result["auc"] = float(roc_auc_score(rows["label"], scored["p"]))
    else:
        result["auc"] = None
    return result


def threshold_table(rows: pd.DataFrame, probability: np.ndarray) -> list[dict[str, Any]]:
    """Describe a fixed family of safety-veto cutoffs without selecting on test ROI."""
    probability = np.asarray(probability, dtype=float)
    result: list[dict[str, Any]] = []
    positives = int(rows["label"].sum())
    for threshold in (.90, .95, .97, .98, .99):
        kept = probability >= threshold
        selected = rows.loc[kept]
        selected_positives = int(selected["label"].sum())
        selected_false = int((1 - selected["label"]).sum())
        result.append({
            "minimum_probability": threshold,
            "rows_kept": int(kept.sum()),
            "row_retention": float(kept.mean()),
            "positive_recall": float(selected_positives / positives) if positives else None,
            "terminal_false_kept": selected_false,
            "terminal_false_rate_kept": (
                float(selected_false / len(selected)) if len(selected) else None
            ),
        })
    return result


def frozen_margin_slices(
    pretest: pd.DataFrame,
    frozen_test: pd.DataFrame,
    frozen_probability: np.ndarray,
) -> dict[str, Any]:
    """Evaluate predeclared KNMI cross margins on the untouched 2025 dates."""
    output: dict[str, Any] = {}
    margins = frozen_test["ta_cross_margin_c"].to_numpy(float)
    for minimum_margin in (.5, .6, .7, .8):
        mask = margins >= minimum_margin - 1e-9
        subset = frozen_test.loc[mask].copy()
        probability = np.asarray(frozen_probability)[mask]
        train_subset = pretest[
            pretest["ta_cross_margin_c"].ge(minimum_margin - 1e-9)
        ]
        train_rate = float(
            np.average(train_subset["label"], weights=date_equal_weights(train_subset))
        )
        baseline = np.full(len(subset), train_rate)
        output[f"ta_margin_ge_{minimum_margin:.1f}c"] = {
            "model": score(subset, probability),
            "historical_margin_base_rate": score(subset, baseline),
            "paired_model_minus_base": paired_delta(subset, probability, baseline),
            "model_safety_veto_table": threshold_table(subset, probability),
        }
    return output


def fit_predict(estimator: Any, train: pd.DataFrame, test: pd.DataFrame, features: list[str]) -> np.ndarray:
    estimator.fit(
        matrix(train, features),
        train["label"],
        **({"sample_weight": date_equal_weights(train)} if not hasattr(estimator, "steps") else {"logisticregression__sample_weight": date_equal_weights(train)}),
    )
    return estimator.predict_proba(matrix(test, features))[:, 1]


def objective(metrics: dict[str, Any]) -> float:
    return float(metrics["brier_date_equal"] + .2 * metrics["logloss_date_equal"])


def paired_delta(
    rows: pd.DataFrame,
    candidate: np.ndarray,
    baseline: np.ndarray,
) -> dict[str, Any]:
    scored = rows[["target_date", "label"]].copy()
    label = scored["label"].to_numpy(float)
    candidate = np.clip(candidate, EPS, 1 - EPS)
    baseline = np.clip(baseline, EPS, 1 - EPS)
    scored["brier_delta"] = (candidate - label) ** 2 - (baseline - label) ** 2
    scored["logloss_delta"] = (
        -(label * np.log(candidate) + (1 - label) * np.log(1 - candidate))
        + label * np.log(baseline)
        + (1 - label) * np.log(1 - baseline)
    )
    daily = scored.groupby("target_date")[["brier_delta", "logloss_delta"]].mean()
    rng = np.random.default_rng(20260812)
    index = rng.integers(0, len(daily), size=(20_000, len(daily)))
    result: dict[str, Any] = {"target_dates": int(len(daily))}
    for column in ("brier_delta", "logloss_delta"):
        values = daily[column].to_numpy(float)
        draws = values[index].mean(axis=1)
        result[column] = {
            "mean": float(values.mean()),
            "ci95": [float(value) for value in np.quantile(draws, [.025, .975])],
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--forecast-path", type=Path, default=DEFAULT_FORECAST)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    frame, features = prepare(args.dataset, args.forecast_path)
    selection_train = frame[frame["target_date"].between(TRAIN_START, SELECTION_TRAIN_END)]
    validation = frame[frame["target_date"].between(SELECTION_VALIDATION_START, SELECTION_VALIDATION_END)]
    pretest = frame[frame["target_date"].between(TRAIN_START, SELECTION_VALIDATION_END)]
    frozen_test = frame[frame["target_date"].between(FROZEN_TEST_START, FROZEN_TEST_END)]
    if min(len(selection_train), len(validation), len(frozen_test)) == 0:
        raise ValueError("cross-survival split is empty")

    validation_results: dict[str, Any] = {}
    for model_id, estimator in candidates().items():
        prediction = fit_predict(estimator, selection_train, validation, features)
        validation_results[model_id] = score(validation, prediction)
    selected_id = min(validation_results, key=lambda key: objective(validation_results[key]))

    selected_for_test = candidates()[selected_id]
    frozen_probability = fit_predict(selected_for_test, pretest, frozen_test, features)
    train_rate = float(np.average(pretest["label"], weights=date_equal_weights(pretest)))
    baseline_probability = np.full(len(frozen_test), train_rate)
    frozen_scores = score(frozen_test, frozen_probability)
    baseline_scores = score(frozen_test, baseline_probability)
    frozen_predictions = frozen_test[
        ["target_date", "observed_at_utc", "current_bracket_c", "ta_cross_margin_c", "label"]
    ].copy()
    frozen_predictions["p_cross_survives"] = frozen_probability
    frozen_predictions["p_train_base_rate"] = baseline_probability

    final_estimator = candidates()[selected_id]
    final_estimator.fit(
        matrix(frame, features),
        frame["label"],
        **({"sample_weight": date_equal_weights(frame)} if not hasattr(final_estimator, "steps") else {"logisticregression__sample_weight": date_equal_weights(frame)}),
    )
    identity = {
        "dataset_sha256": sha256(args.dataset),
        "forecast_dataset_sha256": sha256(args.forecast_path),
        "script_sha256": sha256(Path(__file__)),
        "model_id": "amsterdam_knmi_cross_survival",
        "train_end": FINAL_TRAIN_END,
        "feature_contract_sha256": hashlib.sha256(
            json.dumps(features, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    run_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
    output = args.output_root / f"run={run_id}"
    output.mkdir(parents=True, exist_ok=True)
    artifact_path = output / "model.pkl"
    with artifact_path.open("wb") as handle:
        pickle.dump({
            "schema_version": "amsterdam_knmi_cross_survival_model_v1",
            "model_id": "amsterdam_knmi_cross_survival",
            "target": "P(final EHAM settlement leaves previous exact bracket | first KNMI ta +0.5C cross)",
            "minimum_cross_margin_c": MIN_MARGIN_C,
            "features": features,
            "estimator": final_estimator,
            "artifact_train_end": FINAL_TRAIN_END,
            "clean_forward_start": "2026-08-12",
            "identity": identity,
        }, handle)
    frozen_predictions.to_csv(output / "frozen_test_2025.csv.gz", index=False, compression="gzip")
    summary = {
        "schema_version": "amsterdam_knmi_cross_survival_training_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "artifact_path": str(artifact_path),
        "artifact_sha256": sha256(artifact_path),
        "model_id": "amsterdam_knmi_cross_survival",
        "selected_model": selected_id,
        "features": len(features),
        "denominator_scope": "first ta>=official current bracket+0.5C per target_date×current bracket; 2024-03-01..2025-12-31",
        "selection_validation": validation_results,
        "frozen_test_2025": frozen_scores,
        "frozen_test_base_rate": baseline_scores,
        "frozen_test_delta": {
            "brier": frozen_scores["brier_date_equal"] - baseline_scores["brier_date_equal"],
            "logloss": frozen_scores["logloss_date_equal"] - baseline_scores["logloss_date_equal"],
        },
        "frozen_test_paired_delta": paired_delta(
            frozen_test, frozen_probability, baseline_probability
        ),
        "frozen_test_safety_veto_table": threshold_table(
            frozen_test, frozen_probability
        ),
        "frozen_test_by_cross_margin": frozen_margin_slices(
            pretest, frozen_test, frozen_probability
        ),
        "training_rows": int(len(frame)),
        "training_dates": int(frame["target_date"].nunique()),
        "identity": identity,
        "frozen_2026_read": False,
    }
    (output / "metrics.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
