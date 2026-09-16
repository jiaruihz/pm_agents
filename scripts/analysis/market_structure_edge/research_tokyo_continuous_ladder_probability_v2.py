#!/usr/bin/env python3
"""Tokyo continuous-ladder probability model v2.

This is a zero-notional research pipeline.  It repairs three structural
weaknesses in v1:

1. one coherent hurdle distribution:
   P(delta=0)=P(stay), P(delta=k)=P(leave)*P(delta=k|leave);
2. equal training emphasis on checkpoint, transition and bracket-state-entry
   grains, while retaining separate evaluation at every grain;
3. immutable semantic/model hashes and a long-form prediction artifact.

JMA remains a soft probabilistic source.  Only a strictly earlier RJTT METAR
running maximum is a hard settlement-support floor.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, datetime
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge import (
    research_tokyo_continuous_ladder_probability_v1 as v1,
)
from scripts.analysis.market_structure_edge.research_tokyo_jma_multivariate_path_v1 import (
    date_weights,
    finite,
    matrix,
    write_rows,
)

DEFAULT_OUT = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_continuous_ladder_probability_v2"
)
MODEL_NAMES = (
    "direct_checkpoint_hgb_v1",
    "coherent_checkpoint_hgb_v2",
    "coherent_multigrain_hgb_v2",
)
OUTCOMES = ("delta_0", "delta_1", "delta_2", "delta_3plus")
GRAINS = ("checkpoint", "transition", "state_entry")
EPS = 1e-8


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def semantic_file_hash(path: Path) -> str:
    """Hash decompressed logical content, not gzip container metadata."""
    digest = hashlib.sha256()
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def path_phase(row: dict[str, Any]) -> str:
    """Pre-label physical path phase used for transition-grain membership."""
    slope = finite(row.get("jma_temp_slope_30m_cph")) or 0.0
    pullback = finite(row.get("jma_pullback_from_running_max_c")) or 0.0
    age = finite(row.get("minutes_since_jma_strict_high"))
    delta = finite(row.get("jma_temp_delta_10m")) or 0.0
    if age is not None and age <= 10 and delta > 0.05:
        return "new_high"
    if slope >= 0.4 and pullback >= -0.25:
        return "warming"
    if pullback <= -0.4:
        return "pullback"
    if abs(slope) <= 0.3 and (age is None or age <= 60):
        return "plateau"
    return "other"


def annotate_grains(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add pre-label checkpoint/transition/state-entry membership."""
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in rows:
        row = dict(raw)
        row["path_phase"] = path_phase(row)
        row["is_checkpoint"] = 1
        row["is_transition"] = 0
        row["is_state_entry"] = 0
        by_date[str(row["target_date"])].append(row)

    output: list[dict[str, Any]] = []
    for target_date in sorted(by_date):
        selected = sorted(
            by_date[target_date], key=lambda row: str(row["decision_ts_utc"])
        )
        previous_signature: tuple[int, str] | None = None
        seen_brackets: set[int] = set()
        for row in selected:
            bracket = int(row["current_bracket"])
            signature = (bracket, str(row["path_phase"]))
            if signature != previous_signature:
                row["is_transition"] = 1
            if bracket not in seen_brackets:
                row["is_state_entry"] = 1
                seen_brackets.add(bracket)
            previous_signature = signature
            output.append(row)
    return output


def grain_indices(rows: list[dict[str, Any]], grain: str) -> np.ndarray:
    if grain not in GRAINS:
        raise ValueError(grain)
    field = f"is_{grain}"
    return np.asarray(
        [index for index, row in enumerate(rows) if int(row[field]) == 1],
        dtype=int,
    )


def multigrain_weights(rows: list[dict[str, Any]]) -> np.ndarray:
    """Give every target date and each of the three grains equal total mass."""
    counts: dict[tuple[str, str], int] = Counter()
    for row in rows:
        target_date = str(row["target_date"])
        for grain in GRAINS:
            if int(row[f"is_{grain}"]) == 1:
                counts[(target_date, grain)] += 1
    weights = []
    for row in rows:
        target_date = str(row["target_date"])
        value = sum(
            1.0 / (len(GRAINS) * counts[(target_date, grain)])
            for grain in GRAINS
            if int(row[f"is_{grain}"]) == 1
        )
        weights.append(value)
    result = np.asarray(weights, dtype=float)
    return result / np.mean(result)


def hgb_pipeline(params: dict[str, Any]) -> Pipeline:
    return Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            (
                "model",
                HistGradientBoostingClassifier(
                    learning_rate=float(params["learning_rate"]),
                    max_iter=int(params["max_iter"]),
                    max_leaf_nodes=int(params["max_leaf_nodes"]),
                    min_samples_leaf=int(params["min_samples_leaf"]),
                    l2_regularization=float(params["l2_regularization"]),
                    random_state=20260731,
                ),
            ),
        ]
    )


def fit_weighted_hgb(
    rows: list[dict[str, Any]],
    label: str,
    weights: np.ndarray,
    params: dict[str, Any],
    *,
    feature_names: tuple[str, ...] = v1.MODEL_FEATURES,
) -> Pipeline:
    model = hgb_pipeline(params)
    model.fit(
        matrix(rows, feature_names),
        np.asarray([int(row[label]) for row in rows]),
        model__sample_weight=weights,
    )
    return model


def probability_for_class(
    model: Pipeline,
    rows: list[dict[str, Any]],
    wanted: int,
    *,
    feature_names: tuple[str, ...] = v1.MODEL_FEATURES,
) -> np.ndarray:
    raw = model.predict_proba(matrix(rows, feature_names))
    classes = [
        int(value) for value in model.named_steps["model"].classes_
    ]
    return raw[:, classes.index(wanted)]


def aligned_tail_probabilities(
    model: Pipeline,
    rows: list[dict[str, Any]],
    *,
    feature_names: tuple[str, ...] = v1.MODEL_FEATURES,
) -> np.ndarray:
    raw = model.predict_proba(matrix(rows, feature_names))
    classes = [
        int(value) for value in model.named_steps["model"].classes_
    ]
    output = np.zeros((len(rows), 3), dtype=float)
    for index, value in enumerate(classes):
        if value not in (1, 2, 3):
            raise RuntimeError(f"unexpected positive-tail class: {value}")
        output[:, value - 1] = raw[:, index]
    totals = output.sum(axis=1, keepdims=True)
    if np.any(totals <= 0):
        raise RuntimeError("positive-tail model produced zero total mass")
    return output / totals


def fit_coherent_hurdle(
    rows: list[dict[str, Any]],
    weights: np.ndarray,
    params: dict[str, Any],
    *,
    feature_names: tuple[str, ...] = v1.MODEL_FEATURES,
) -> dict[str, Any]:
    leave = fit_weighted_hgb(
        rows,
        "binary_leave_current",
        weights,
        params,
        feature_names=feature_names,
    )
    positive_indexes = np.asarray(
        [
            index
            for index, row in enumerate(rows)
            if int(row["remaining_rise_class"]) > 0
        ],
        dtype=int,
    )
    positive_rows = [rows[index] for index in positive_indexes]
    positive_weights = weights[positive_indexes]
    positive_weights = positive_weights / np.mean(positive_weights)
    tail = fit_weighted_hgb(
        positive_rows,
        "remaining_rise_class",
        positive_weights,
        params,
        feature_names=feature_names,
    )
    return {
        "leave": leave,
        "tail": tail,
        "params": dict(params),
        "feature_names": tuple(feature_names),
    }


def coherent_probabilities(
    model: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    temperature: float = 1.0,
    feature_names: tuple[str, ...] | None = None,
) -> np.ndarray:
    selected_features = tuple(
        feature_names or model.get("feature_names") or v1.MODEL_FEATURES
    )
    p_leave = probability_for_class(
        model["leave"], rows, 1, feature_names=selected_features
    )
    conditional_tail = aligned_tail_probabilities(
        model["tail"], rows, feature_names=selected_features
    )
    output = np.column_stack(
        [1.0 - p_leave, p_leave[:, None] * conditional_tail]
    )
    if temperature != 1.0:
        output = v1.apply_temperature(output, temperature)
    return output / output.sum(axis=1, keepdims=True)


def direct_probabilities(
    model: Pipeline,
    rows: list[dict[str, Any]],
    *,
    temperature: float,
) -> np.ndarray:
    return v1.apply_temperature(
        v1.aligned_probabilities(model, rows), temperature
    )


def model_probabilities(
    artifact: dict[str, Any], rows: list[dict[str, Any]]
) -> np.ndarray:
    if artifact["kind"] == "direct":
        return direct_probabilities(
            artifact["model"],
            rows,
            temperature=float(artifact["temperature"]),
        )
    return coherent_probabilities(
        artifact["model"],
        rows,
        temperature=float(artifact["temperature"]),
    )


def select_hyperparameters(
    train: list[dict[str, Any]],
    validation: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    candidates = (
        {
            "learning_rate": 0.035,
            "max_iter": 220,
            "max_leaf_nodes": 7,
            "min_samples_leaf": 120,
            "l2_regularization": 5.0,
        },
        {
            "learning_rate": 0.035,
            "max_iter": 220,
            "max_leaf_nodes": 15,
            "min_samples_leaf": 120,
            "l2_regularization": 5.0,
        },
        {
            "learning_rate": 0.03,
            "max_iter": 260,
            "max_leaf_nodes": 15,
            "min_samples_leaf": 240,
            "l2_regularization": 8.0,
        },
    )
    weights = multigrain_weights(train)
    score_rows = []
    for candidate_id, params in enumerate(candidates, start=1):
        model = fit_coherent_hurdle(train, weights, params)
        raw = coherent_probabilities(model, validation)
        temperature = v1.choose_temperature(validation, raw)
        probabilities = v1.apply_temperature(raw, temperature)
        record = {
            "candidate_id": candidate_id,
            **params,
            "temperature": temperature,
            "validation_rps": v1.date_equal_loss(
                validation, probabilities, metric="rps"
            ),
            "validation_brier": v1.date_equal_loss(
                validation, probabilities, metric="brier"
            ),
            "validation_logloss": v1.date_equal_loss(
                validation, probabilities, metric="logloss"
            ),
        }
        score_rows.append(record)
    best = min(
        score_rows,
        key=lambda row: (
            float(row["validation_rps"]),
            float(row["validation_brier"]),
        ),
    )
    selected = {
        key: best[key]
        for key in (
            "learning_rate",
            "max_iter",
            "max_leaf_nodes",
            "min_samples_leaf",
            "l2_regularization",
        )
    }
    return selected, score_rows


def score_predictions(
    rows: list[dict[str, Any]],
    predictions: dict[str, np.ndarray],
    *,
    split: str,
    grain: str,
) -> list[dict[str, Any]]:
    indexes = grain_indices(rows, grain)
    selected_rows = [rows[index] for index in indexes]
    baseline = predictions["direct_checkpoint_hgb_v1"][indexes]
    output = []
    for model_name, all_probabilities in predictions.items():
        probabilities = all_probabilities[indexes]
        labels = np.asarray(
            [int(row["remaining_rise_class"]) for row in selected_rows]
        )
        guesses = np.argmax(probabilities, axis=1)
        record = {
            "split": split,
            "grain": grain,
            "model": model_name,
            "states": len(selected_rows),
            "target_dates": len(
                {str(row["target_date"]) for row in selected_rows}
            ),
            "multiclass_logloss": v1.date_equal_loss(
                selected_rows, probabilities, metric="logloss"
            ),
            "multiclass_brier": v1.date_equal_loss(
                selected_rows, probabilities, metric="brier"
            ),
            "ranked_probability_score": v1.date_equal_loss(
                selected_rows, probabilities, metric="rps"
            ),
            "exact_accuracy": float(np.mean(guesses == labels)),
            "within_one_accuracy": float(
                np.mean(np.abs(guesses - labels) <= 1)
            ),
        }
        if model_name != "direct_checkpoint_hgb_v1":
            for metric in ("brier", "logloss"):
                delta, low, high = v1.date_bootstrap_delta(
                    selected_rows,
                    probabilities,
                    baseline,
                    metric=metric,
                )
                record[f"{metric}_delta_vs_direct"] = delta
                record[f"{metric}_delta_ci_low"] = low
                record[f"{metric}_delta_ci_high"] = high
        output.append(record)
    return output


def calibration_rows(
    rows: list[dict[str, Any]],
    probabilities: np.ndarray,
    *,
    split: str,
    grain: str,
    model: str,
) -> list[dict[str, Any]]:
    indexes = grain_indices(rows, grain)
    selected = [rows[index] for index in indexes]
    p_stay = probabilities[indexes, 0]
    weights = date_weights(selected, normalize=False)
    output = []
    for bin_index in range(10):
        low = bin_index / 10
        high = (bin_index + 1) / 10
        mask = np.asarray(
            [
                value >= low
                and (value < high or (bin_index == 9 and value <= high))
                for value in p_stay
            ]
        )
        if not np.any(mask):
            continue
        bin_weights = weights[mask]
        weight_mass = float(bin_weights.sum())
        bin_weights /= bin_weights.sum()
        labels = np.asarray(
            [
                int(int(row["remaining_rise_class"]) == 0)
                for row, keep in zip(selected, mask)
                if keep
            ],
            dtype=float,
        )
        mean_probability = float(np.sum(p_stay[mask] * bin_weights))
        observed_rate = float(np.sum(labels * bin_weights))
        output.append(
            {
                "split": split,
                "grain": grain,
                "model": model,
                "bin_low": low,
                "bin_high": high,
                "states": int(np.sum(mask)),
                "target_dates": len(
                    {
                        str(row["target_date"])
                        for row, keep in zip(selected, mask)
                        if keep
                    }
                ),
                "date_equal_weight_mass": weight_mass,
                "mean_p_stay": mean_probability,
                "observed_stay_rate": observed_rate,
                "absolute_calibration_gap": abs(
                    mean_probability - observed_rate
                ),
            }
        )
    return output


def calibration_summary_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = (
        defaultdict(list)
    )
    for row in rows:
        grouped[
            (str(row["split"]), str(row["grain"]), str(row["model"]))
        ].append(row)
    output = []
    for (split, grain, model), selected in sorted(grouped.items()):
        total_mass = sum(
            float(row["date_equal_weight_mass"]) for row in selected
        )
        ece = (
            sum(
                float(row["date_equal_weight_mass"])
                * float(row["absolute_calibration_gap"])
                for row in selected
            )
            / total_mass
        )
        output.append(
            {
                "split": split,
                "grain": grain,
                "model": model,
                "date_equal_stay_ece": ece,
            }
        )
    return output


def model_specs(
    selected_params: dict[str, Any],
    feature_semantic_hash: str,
) -> dict[str, dict[str, Any]]:
    base = {
        "schema_version": "tokyo_continuous_ladder_probability_v2",
        "features": list(v1.MODEL_FEATURES),
        "feature_semantic_sha256": feature_semantic_hash,
        "hyperparameter_fit_end": "2025-06-30",
        "calibration_selection_window": "2025-07-01/2025-12-31",
        "final_refit_end": "2025-12-31",
        "outcomes": list(OUTCOMES),
        "hard_support_floor": "strictly_prior_rjtt_metar_running_max",
        "soft_sources": ["jma_amedas"],
    }
    return {
        "direct_checkpoint_hgb_v1": {
            **base,
            "objective": "direct_multinomial_checkpoint_date_equal",
            "parameters": {
                "learning_rate": 0.035,
                "max_iter": 220,
                "max_leaf_nodes": 15,
                "min_samples_leaf": 120,
                "l2_regularization": 5.0,
            },
        },
        "coherent_checkpoint_hgb_v2": {
            **base,
            "objective": "coherent_hurdle_checkpoint_date_equal",
            "parameters": selected_params,
        },
        "coherent_multigrain_hgb_v2": {
            **base,
            "objective": (
                "coherent_hurdle_equal_checkpoint_transition_state_entry"
            ),
            "parameters": selected_params,
        },
    }


def persist_artifacts(
    output_dir: Path,
    artifacts: dict[str, dict[str, Any]],
    specs: dict[str, dict[str, Any]],
) -> dict[str, dict[str, str]]:
    model_dir = output_dir / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, dict[str, str]] = {}
    for name, artifact in artifacts.items():
        spec_bytes = (canonical_json(specs[name]) + "\n").encode("utf-8")
        spec_path = model_dir / f"{name}.spec.json"
        spec_path.write_bytes(spec_bytes)
        model_path = model_dir / f"{name}.joblib"
        joblib.dump(artifact, model_path, compress=3)
        hashes[name] = {
            "model_spec_sha256": sha256_bytes(spec_bytes),
            "model_artifact_sha256": sha256_bytes(model_path.read_bytes()),
        }
    return hashes


def prediction_artifact_rows(
    rows: list[dict[str, Any]],
    predictions: dict[str, np.ndarray],
    *,
    split: str,
    feature_hash: str,
    hashes: dict[str, dict[str, str]],
    exact: dict[str, datetime],
) -> Iterable[dict[str, Any]]:
    for row_index, row in enumerate(rows):
        observed = v1.parse_ts(str(row["decision_ts_utc"]))
        exact_available = exact.get(observed.isoformat())
        availability_class = (
            "collector_exact_hash_verified"
            if exact_available is not None
            else "archive_observation_timestamp_not_first_seen"
        )
        prediction_ts = exact_available or observed
        memberships = [
            grain for grain in GRAINS if int(row[f"is_{grain}"]) == 1
        ]
        label = int(row["remaining_rise_class"])
        for model_name, probabilities in predictions.items():
            distribution_id = sha256_bytes(
                (
                    f"{row['state_id']}|{model_name}|"
                    f"{hashes[model_name]['model_artifact_sha256']}"
                ).encode("utf-8")
            )
            for outcome_index, outcome in enumerate(OUTCOMES):
                yield {
                    "schema_version": (
                        "weather_city_probability_prediction_v2_long"
                    ),
                    "city": "Tokyo",
                    "target_date": row["target_date"],
                    "decision_ts_utc": row["decision_ts_utc"],
                    "prediction_ts_utc": prediction_ts.isoformat(),
                    "availability_clock_class": availability_class,
                    "state_id": row["state_id"],
                    "state_grains": ",".join(memberships),
                    "path_phase": row["path_phase"],
                    "target_id": "eod_remaining_rise_delta_distribution",
                    "distribution_id": distribution_id,
                    "outcome_id": outcome,
                    "p_model": float(
                        probabilities[row_index, outcome_index]
                    ),
                    "label": int(label == outcome_index),
                    "label_delta": label,
                    "current_bracket": row["current_bracket"],
                    "hard_support_floor": row["current_bracket"],
                    "split": split,
                    "out_of_fit_sample": 1,
                    "frozen_evaluation": int(
                        split in {"frozen_weather", "market_holdout"}
                    ),
                    "feature_set_id": "tokyo_jma_metar_path_v1_features",
                    "feature_semantic_sha256": feature_hash,
                    "model_id": model_name,
                    **hashes[model_name],
                    "pit_provenance": row["pit_provenance"],
                    "forecast_route": (
                        "physical_observation_expert_no_strict_pit_forecast"
                    ),
                }


def add_predictions_to_market_rows(
    joined: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> None:
    by_state = {str(row["state_id"]): row for row in rows}
    for row in joined:
        source = by_state[str(row["state_id"])]
        row["path_phase"] = source["path_phase"]
        row["is_transition"] = source["is_transition"]
        row["is_state_entry"] = source["is_state_entry"]


def wait_one_candidates(
    joined: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    now_selected: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    states_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in joined:
        states_by_date[str(row["target_date"])].append(row)
    for rows in states_by_date.values():
        rows.sort(key=lambda row: str(row["availability_ts_utc"]))
    candidates_by_key: dict[tuple[str, str], list[dict[str, Any]]] = (
        defaultdict(list)
    )
    for row in candidates:
        candidates_by_key[
            (str(row["model"]), str(row["state_id"]))
        ].append(row)

    output = []
    pairs = []
    for now in now_selected:
        model = str(now["model"])
        target_date = str(now["target_date"])
        states = states_by_date[target_date]
        current_index = next(
            (
                index
                for index, state in enumerate(states)
                if str(state["state_id"]) == str(now["state_id"])
            ),
            None,
        )
        next_state = (
            states[current_index + 1]
            if current_index is not None and current_index + 1 < len(states)
            else None
        )
        pair = {
            "model": model,
            "target_date": target_date,
            "now_state_id": now["state_id"],
            "now_availability_ts_utc": now["availability_ts_utc"],
            "now_expression_bracket": now["expression_bracket"],
            "now_side": now["side"],
            "now_fee_adjusted_edge": now["fee_adjusted_edge"],
            "next_state_id": (
                next_state["state_id"] if next_state is not None else None
            ),
            "next_availability_ts_utc": (
                next_state["availability_ts_utc"]
                if next_state is not None
                else None
            ),
            "wait_minutes": (
                (
                    v1.parse_ts(str(next_state["availability_ts_utc"]))
                    - v1.parse_ts(str(now["availability_ts_utc"]))
                ).total_seconds()
                / 60
                if next_state is not None
                else None
            ),
            "wait_status": "no_next_checkpoint",
        }
        if next_state is not None:
            next_candidates = candidates_by_key.get(
                (model, str(next_state["state_id"])), []
            )
            eligible = [
                row
                for row in next_candidates
                if float(row["fee_adjusted_edge"]) >= 0.02
            ]
            if eligible:
                best = max(
                    eligible, key=lambda row: float(row["fee_adjusted_edge"])
                )
                output.append(best)
                pair.update(
                    {
                        "wait_status": "eligible",
                        "wait_expression_bracket": best[
                            "expression_bracket"
                        ],
                        "wait_side": best["side"],
                        "wait_fee_adjusted_edge": best[
                            "fee_adjusted_edge"
                        ],
                    }
                )
            else:
                pair["wait_status"] = "edge_disappeared"
        pairs.append(pair)
    return output, pairs


def policy_summary(
    now_trades: list[dict[str, Any]],
    wait_trades: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    *,
    split: str,
) -> list[dict[str, Any]]:
    output = []
    for model in sorted({str(row["model"]) for row in now_trades}):
        model_pairs = [row for row in pairs if row["model"] == model]
        for policy, trades in (
            ("trade_now", now_trades),
            ("wait_one_jma_checkpoint", wait_trades),
        ):
            selected = [
                row
                for row in trades
                if row["model"] == model
                and int(row.get("five_share_executable", 0)) == 1
            ]
            cost = sum(float(row["entry_cost_usd"]) for row in selected)
            pnl = sum(
                float(row["fee_adjusted_pnl_usd"]) for row in selected
            )
            output.append(
                {
                    "split": split,
                    "model": model,
                    "policy": policy,
                    "trigger_dates": len(model_pairs),
                    "eligible_after_wait": (
                        len(model_pairs)
                        if policy == "trade_now"
                        else sum(
                            row["wait_status"] == "eligible"
                            for row in model_pairs
                        )
                    ),
                    "executable_trades": len(selected),
                    "wins": sum(int(row["won"]) for row in selected),
                    "cost_usd": cost,
                    "fee_adjusted_pnl_usd": pnl,
                    "fee_adjusted_roi": pnl / cost if cost else None,
                }
            )
    return output


def paired_policy_delta(
    now_trades: list[dict[str, Any]],
    wait_trades: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    *,
    split: str,
) -> list[dict[str, Any]]:
    output = []
    for model in sorted({str(row["model"]) for row in now_trades}):
        dates = sorted(
            {
                str(row["target_date"])
                for row in pairs
                if row["model"] == model
            }
        )
        now_map = {
            str(row["target_date"]): float(
                row.get("fee_adjusted_pnl_usd", 0.0)
            )
            for row in now_trades
            if row["model"] == model
            and int(row.get("five_share_executable", 0)) == 1
        }
        wait_map = {
            str(row["target_date"]): float(
                row.get("fee_adjusted_pnl_usd", 0.0)
            )
            for row in wait_trades
            if row["model"] == model
            and int(row.get("five_share_executable", 0)) == 1
        }
        deltas = np.asarray(
            [wait_map.get(day, 0.0) - now_map.get(day, 0.0) for day in dates]
        )
        if len(deltas):
            rng = np.random.default_rng(20260731)
            draws = np.asarray(
                [
                    float(
                        np.mean(
                            rng.choice(deltas, len(deltas), replace=True)
                        )
                    )
                    for _ in range(5000)
                ]
            )
            mean = float(np.mean(deltas))
            low = float(np.quantile(draws, 0.025))
            high = float(np.quantile(draws, 0.975))
        else:
            mean = low = high = math.nan
        output.append(
            {
                "split": split,
                "model": model,
                "trigger_dates": len(dates),
                "mean_pnl_delta_wait_minus_now_usd": mean,
                "delta_ci_low": low,
                "delta_ci_high": high,
            }
        )
    return output


def typical_cases(
    joined: list[dict[str, Any]], model_name: str
) -> list[dict[str, Any]]:
    output = []
    for row in joined:
        if int(row["settlement_lower_bound_violation"]):
            continue
        probability = np.asarray(
            json.loads(row[f"{model_name}_distribution_json"]), dtype=float
        )
        label = min(int(row["actual_delta"]), 3)
        prediction = int(np.argmax(probability))
        scored = dict(row)
        scored.update(
            {
                "model": model_name,
                "actual_delta_class": label,
                "predicted_delta_class": prediction,
                "p_actual": float(probability[label]),
                "prediction_absolute_error": abs(prediction - label),
                "case_type": (
                    "correct"
                    if prediction == label
                    else "high_confidence_error"
                ),
            }
        )
        output.append(scored)
    errors = sorted(
        [row for row in output if row["case_type"] != "correct"],
        key=lambda row: float(row["p_actual"]),
    )[:12]
    correct = sorted(
        [row for row in output if row["case_type"] == "correct"],
        key=lambda row: -float(row["p_actual"]),
    )[:12]
    return errors + correct


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, default=v1.FEATURE_ROWS)
    parser.add_argument(
        "--exact-first-seen", type=Path, default=v1.EXACT_FIRST_SEEN
    )
    parser.add_argument("--market-states", type=Path, default=v1.MARKET_STATES)
    parser.add_argument("--pm-history", type=Path, default=v1.PM_HISTORY)
    parser.add_argument("--raw-books", type=Path, default=v1.RAW_BOOKS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    feature_hash = semantic_file_hash(args.features)
    raw_rows = v1.read_rows(args.features)
    continuous = annotate_grains(
        [
            row
            for row in v1.build_continuous_rows(raw_rows)
            if row["remaining_rise_class"] is not None
        ]
    )
    groups = {
        split: [
            row
            for row in continuous
            if v1.split_name(str(row["target_date"])) == split
        ]
        for split in (
            "train",
            "validation",
            "frozen_weather",
            "market_development",
            "market_holdout",
        )
    }
    train = groups["train"]
    validation = groups["validation"]

    selected_params, hyperparameter_rows = select_hyperparameters(
        train, validation
    )
    # The 2025H2 block is untouched during candidate fitting.  It selects
    # hyperparameters/calibration, after which the frozen specification is
    # refit on all data available before the first 2026 evaluation date.
    direct_selection = v1.fit_hgb(train, "remaining_rise_class")
    direct_validation_raw = v1.aligned_probabilities(
        direct_selection, validation
    )
    direct_temperature = v1.choose_temperature(
        validation, direct_validation_raw
    )
    coherent_checkpoint_selection = fit_coherent_hurdle(
        train, date_weights(train), selected_params
    )
    coherent_checkpoint_raw = coherent_probabilities(
        coherent_checkpoint_selection, validation
    )
    coherent_checkpoint_temperature = v1.choose_temperature(
        validation, coherent_checkpoint_raw
    )
    coherent_multigrain_selection = fit_coherent_hurdle(
        train, multigrain_weights(train), selected_params
    )
    coherent_multigrain_raw = coherent_probabilities(
        coherent_multigrain_selection, validation
    )
    coherent_multigrain_temperature = v1.choose_temperature(
        validation, coherent_multigrain_raw
    )
    final_train = train + validation
    direct = v1.fit_hgb(final_train, "remaining_rise_class")
    coherent_checkpoint = fit_coherent_hurdle(
        final_train, date_weights(final_train), selected_params
    )
    coherent_multigrain = fit_coherent_hurdle(
        final_train, multigrain_weights(final_train), selected_params
    )

    artifacts = {
        "direct_checkpoint_hgb_v1": {
            "kind": "direct",
            "model": direct,
            "temperature": direct_temperature,
        },
        "coherent_checkpoint_hgb_v2": {
            "kind": "coherent_hurdle",
            "model": coherent_checkpoint,
            "temperature": coherent_checkpoint_temperature,
        },
        "coherent_multigrain_hgb_v2": {
            "kind": "coherent_hurdle",
            "model": coherent_multigrain,
            "temperature": coherent_multigrain_temperature,
        },
    }
    specs = model_specs(selected_params, feature_hash)
    artifact_hashes = persist_artifacts(args.out, artifacts, specs)
    exact = v1.exact_first_seen(args.exact_first_seen)

    scores: list[dict[str, Any]] = []
    calibration: list[dict[str, Any]] = []
    prediction_rows: list[dict[str, Any]] = []
    predictions_by_split: dict[str, dict[str, np.ndarray]] = {}
    for split, rows in groups.items():
        if split in {"train", "validation"} or not rows:
            continue
        predictions = {
            name: model_probabilities(artifact, rows)
            for name, artifact in artifacts.items()
        }
        predictions_by_split[split] = predictions
        for grain in GRAINS:
            scores.extend(
                score_predictions(
                    rows, predictions, split=split, grain=grain
                )
            )
            for model_name, probabilities in predictions.items():
                calibration.extend(
                    calibration_rows(
                        rows,
                        probabilities,
                        split=split,
                        grain=grain,
                        model=model_name,
                    )
                )
        prediction_rows.extend(
            prediction_artifact_rows(
                rows,
                predictions,
                split=split,
                feature_hash=feature_hash,
                hashes=artifact_hashes,
                exact=exact,
            )
        )

    markets = v1.load_market_states(args.market_states)
    winners = v1.load_winners(
        args.pm_history, date(2026, 7, 15), date(2026, 7, 30)
    )
    market_joined: list[dict[str, Any]] = []
    market_scores: list[dict[str, Any]] = []
    market_binary_scores: list[dict[str, Any]] = []
    counterfactual_trades: list[dict[str, Any]] = []
    wait_trades: list[dict[str, Any]] = []
    wait_pairs: list[dict[str, Any]] = []
    policy_scores: list[dict[str, Any]] = []
    policy_deltas: list[dict[str, Any]] = []
    joined_by_split: dict[str, list[dict[str, Any]]] = {}
    for split in ("market_development", "market_holdout"):
        rows = groups[split]
        joined = v1.join_market(
            rows,
            predictions_by_split[split],
            np.asarray([0]),
            np.ones((len(rows), 1)),
            exact,
            markets,
            winners,
        )
        add_predictions_to_market_rows(joined, rows)
        for row in joined:
            row["market_split"] = split
        joined_by_split[split] = joined
        market_joined.extend(joined)
        market_scores.extend(v1.market_score_rows(joined, MODEL_NAMES, split))
        market_binary_scores.extend(
            v1.market_binary_scores(joined, MODEL_NAMES, split)
        )

        candidates = v1.expression_candidates(joined, MODEL_NAMES)
        now = v1.select_trades(candidates, args.raw_books)
        waited_candidates, pairs = wait_one_candidates(
            joined, candidates, now
        )
        waited = v1.select_trades(waited_candidates, args.raw_books)
        for row in now:
            row["policy"] = "trade_now"
            row["market_split"] = split
        for row in waited:
            row["policy"] = "wait_one_jma_checkpoint"
            row["market_split"] = split
        for row in pairs:
            row["market_split"] = split
        counterfactual_trades.extend(now)
        wait_trades.extend(waited)
        wait_pairs.extend(pairs)
        policy_scores.extend(
            policy_summary(now, waited, pairs, split=split)
        )
        policy_deltas.extend(
            paired_policy_delta(now, waited, pairs, split=split)
        )

        exact_joined = [
            row
            for row in joined
            if row["availability_clock_class"]
            == "collector_exact_hash_verified"
        ]
        if exact_joined:
            exact_split = f"{split}_collector_exact"
            market_scores.extend(
                v1.market_score_rows(
                    exact_joined, MODEL_NAMES, exact_split
                )
            )
            market_binary_scores.extend(
                v1.market_binary_scores(
                    exact_joined, MODEL_NAMES, exact_split
                )
            )
            exact_candidates = v1.expression_candidates(
                exact_joined, MODEL_NAMES
            )
            exact_now = v1.select_trades(
                exact_candidates, args.raw_books
            )
            exact_wait_candidates, exact_pairs = wait_one_candidates(
                exact_joined, exact_candidates, exact_now
            )
            exact_wait = v1.select_trades(
                exact_wait_candidates, args.raw_books
            )
            for row in exact_now:
                row["policy"] = "trade_now"
                row["market_split"] = exact_split
            for row in exact_wait:
                row["policy"] = "wait_one_jma_checkpoint"
                row["market_split"] = exact_split
            for row in exact_pairs:
                row["market_split"] = exact_split
            counterfactual_trades.extend(exact_now)
            wait_trades.extend(exact_wait)
            wait_pairs.extend(exact_pairs)
            policy_scores.extend(
                policy_summary(
                    exact_now, exact_wait, exact_pairs, split=exact_split
                )
            )
            policy_deltas.extend(
                paired_policy_delta(
                    exact_now, exact_wait, exact_pairs, split=exact_split
                )
            )

    frozen_exact_rows = [
        row
        for row in joined_by_split.get("market_holdout", [])
        if row["availability_clock_class"]
        == "collector_exact_hash_verified"
    ]
    cases = typical_cases(
        frozen_exact_rows or joined_by_split.get("market_holdout", []),
        "coherent_multigrain_hgb_v2",
    )
    frozen_checkpoint = [
        row
        for row in scores
        if row["split"] == "frozen_weather"
        and row["grain"] == "checkpoint"
    ]
    frozen_state_entry = [
        row
        for row in scores
        if row["split"] == "frozen_weather"
        and row["grain"] == "state_entry"
    ]
    champion = min(
        frozen_checkpoint,
        key=lambda row: float(row["multiclass_brier"]),
    )["model"]
    state_entry_challenger = min(
        frozen_state_entry,
        key=lambda row: float(row["multiclass_brier"]),
    )["model"]
    exact_market_models = [
        row
        for row in market_scores
        if row["split"] == "market_holdout_collector_exact"
        and row["model"] != "conditional_market"
    ]
    exact_market_gate_pass = bool(exact_market_models) and any(
        float(row["brier_delta_ci_high"]) < 0
        for row in exact_market_models
    )
    model_decisions = [
        {
            "role": "continuous_probability_champion",
            "model": champion,
            "status": "retain",
            "reason": (
                "lowest frozen-2026 checkpoint multiclass Brier; binary "
                "P(stay) is derived from the same four-outcome distribution"
            ),
        },
        {
            "role": "state_entry_structural_challenger",
            "model": state_entry_challenger,
            "status": "research_challenger",
            "reason": (
                "best frozen-2026 state-entry Brier, but July state-entry "
                "holdout is unstable"
            ),
        },
        {
            "role": "market_residual_trade_gate",
            "model": "all_v2_weather_models",
            "status": "pass" if exact_market_gate_pass else "blocked",
            "reason": (
                "requires collector-exact target-date bootstrap upper CI "
                "for Brier delta vs market below zero"
            ),
        },
        {
            "role": "wait_one_checkpoint_policy",
            "model": "all_v2_weather_models",
            "status": "research_only",
            "reason": (
                "market-development and collector-exact results are not "
                "directionally stable on the fixed trigger denominator"
            ),
        },
    ]

    grain_counts = []
    for split, rows in groups.items():
        for grain in GRAINS:
            indexes = grain_indices(rows, grain)
            grain_counts.append(
                {
                    "split": split,
                    "grain": grain,
                    "states": len(indexes),
                    "target_dates": len(
                        {
                            str(rows[index]["target_date"])
                            for index in indexes
                        }
                    ),
                }
            )
    funnel = [
        {
            "funnel": "signal",
            "stage": "eligible_daytime_checkpoints",
            "unit": "state",
            "count": len(continuous),
            "target_dates": len(
                {str(row["target_date"]) for row in continuous}
            ),
        },
        {
            "funnel": "signal",
            "stage": "transition_states",
            "unit": "state",
            "count": sum(int(row["is_transition"]) for row in continuous),
            "target_dates": len(
                {
                    str(row["target_date"])
                    for row in continuous
                    if int(row["is_transition"])
                }
            ),
        },
        {
            "funnel": "signal",
            "stage": "bracket_state_entries",
            "unit": "state",
            "count": sum(int(row["is_state_entry"]) for row in continuous),
            "target_dates": len(
                {
                    str(row["target_date"])
                    for row in continuous
                    if int(row["is_state_entry"])
                }
            ),
        },
        {
            "funnel": "evidence",
            "stage": "pit_book_and_settlement_join",
            "unit": "state",
            "count": len(market_joined),
            "target_dates": len(
                {str(row["target_date"]) for row in market_joined}
            ),
        },
        {
            "funnel": "evidence",
            "stage": "collector_exact_join",
            "unit": "state",
            "count": sum(
                row["availability_clock_class"]
                == "collector_exact_hash_verified"
                for row in market_joined
            ),
            "target_dates": len(
                {
                    str(row["target_date"])
                    for row in market_joined
                    if row["availability_clock_class"]
                    == "collector_exact_hash_verified"
                }
            ),
        },
    ]

    write_rows(args.out / "continuous_feature_rows.csv.gz", continuous)
    write_rows(args.out / "prediction_long.csv.gz", prediction_rows)
    write_rows(args.out / "hyperparameter_selection.csv", hyperparameter_rows)
    write_rows(args.out / "model_scores_by_grain.csv", scores)
    write_rows(args.out / "stay_calibration_by_grain.csv", calibration)
    write_rows(
        args.out / "stay_calibration_summary.csv",
        calibration_summary_rows(calibration),
    )
    write_rows(args.out / "model_decisions.csv", model_decisions)
    write_rows(args.out / "grain_counts.csv", grain_counts)
    write_rows(args.out / "market_join_rows.csv.gz", market_joined)
    write_rows(args.out / "market_scores.csv", market_scores)
    write_rows(args.out / "market_binary_scores.csv", market_binary_scores)
    write_rows(
        args.out / "trade_now_counterfactual.csv", counterfactual_trades
    )
    write_rows(args.out / "trade_wait_one_counterfactual.csv", wait_trades)
    write_rows(args.out / "wait_one_pairs.csv", wait_pairs)
    write_rows(args.out / "wait_policy_summary.csv", policy_scores)
    write_rows(args.out / "wait_policy_paired_delta.csv", policy_deltas)
    write_rows(args.out / "typical_cases.csv", cases)
    write_rows(args.out / "funnel.csv", funnel)

    summary = {
        "schema_version": "tokyo_continuous_ladder_probability_v2",
        "research_only_zero_notional": True,
        "live_behavior_changed": False,
        "feature_semantic_sha256": feature_hash,
        "model_hashes": artifact_hashes,
        "selected_hyperparameters": selected_params,
        "temperature_scaling": {
            name: float(artifact["temperature"])
            for name, artifact in artifacts.items()
        },
        "continuous_rows": len(continuous),
        "continuous_dates": len(
            {str(row["target_date"]) for row in continuous}
        ),
        "market_join_rows": len(market_joined),
        "market_join_dates": len(
            {str(row["target_date"]) for row in market_joined}
        ),
        "collector_exact_rows": sum(
            row["availability_clock_class"]
            == "collector_exact_hash_verified"
            for row in market_joined
        ),
        "collector_exact_dates": len(
            {
                str(row["target_date"])
                for row in market_joined
                if row["availability_clock_class"]
                == "collector_exact_hash_verified"
            }
        ),
        "hard_support_floor": (
            "strictly prior RJTT METAR running maximum; JMA is soft only"
        ),
        "forecast_route": (
            "physical observation expert; strict PIT Tokyo forecast unavailable"
        ),
        "wait_policy": (
            "first fee-adjusted-edge>=0.02 signal, then fixed next joined "
            "JMA checkpoint; no label-conditioned waiting"
        ),
        "probability_champion": champion,
        "state_entry_structural_challenger": state_entry_challenger,
        "collector_exact_market_gate_pass": exact_market_gate_pass,
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
