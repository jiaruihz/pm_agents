#!/usr/bin/env python3
"""Strict 15-day frozen-forward audit for the Tokyo ladder model.

The entire 2026-07-16..2026-07-30 target-date window is excluded from model
fit, calibration, model selection and strategy-policy selection.  Model
specification and the current/next-bracket 2% edge policy are frozen from the
pre-forward Tokyo v2 work.  Results are research replay only: no orders are
submitted.
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import defaultdict
import csv
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable

import joblib
import numpy as np


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge import (  # noqa: E402
    research_tokyo_continuous_ladder_probability_v1 as v1,
)
from scripts.analysis.market_structure_edge import (  # noqa: E402
    research_tokyo_continuous_ladder_probability_v2 as v2,
)
from scripts.analysis.market_structure_edge.research_tokyo_jma_multivariate_path_v1 import (  # noqa: E402
    date_weights,
    write_rows,
)


FORWARD_START = "2026-07-16"
FORWARD_END = "2026-07-30"
TRAIN_CUTOFF = "2026-07-15"
EDGE_THRESHOLD = 0.02
SHARES = 5.0
FEE_RATE = 0.05
MODEL_NAMES = (
    "direct_checkpoint_hgb_v3",
    "direct_checkpoint_hgb_v3__episode_state",
    "coherent_checkpoint_hgb_v3",
    "coherent_checkpoint_hgb_v3__episode_state",
    "coherent_multigrain_hgb_v3",
    "coherent_multigrain_hgb_v3__episode_state",
)
CHAMPION = "direct_checkpoint_hgb_v3"
OUTCOMES = ("delta_0", "delta_1", "delta_2", "delta_3plus")
FROZEN_PARAMS = {
    "learning_rate": 0.035,
    "max_iter": 220,
    "max_leaf_nodes": 7,
    "min_samples_leaf": 120,
    "l2_regularization": 5.0,
}
FROZEN_TEMPERATURES = {
    "direct_checkpoint_hgb_v3": 1.15,
    # Hold temperature fixed to the baseline so the paired experiment changes
    # only the feature set; no forward labels are used for recalibration.
    "direct_checkpoint_hgb_v3__episode_state": 1.15,
    "coherent_checkpoint_hgb_v3": 0.95,
    "coherent_checkpoint_hgb_v3__episode_state": 0.95,
    "coherent_multigrain_hgb_v3": 0.90,
    "coherent_multigrain_hgb_v3__episode_state": 0.90,
}
DEFAULT_OUT = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_continuous_ladder_forward_v3"
)
EPS = 1e-8
EPISODE_STATE_FEATURES = (
    "jma_episode_peak_c",
    "jma_episode_trough_c",
    "jma_episode_giveback_c",
    "jma_recovery_from_trough_c",
    "jma_recovery_fraction",
    "minutes_since_episode_trough",
    "jma_heating_reacceleration_cph",
    "jma_short_long_slope_reversal_cph",
    "jma_cross_count_current_boundary",
    "jma_has_pullback_then_recovery",
    "jma_reheat_active",
)
BASELINE_FEATURES = tuple(v1.MODEL_FEATURES)
EPISODE_FEATURE_SET = BASELINE_FEATURES + EPISODE_STATE_FEATURES
EPISODE_FEATURE_SEMANTIC_VERSION = "tokyo_episode_state_prefix_v1"
FEATURE_AB_PAIRS = {
    "direct_checkpoint_hgb_v3__episode_state": "direct_checkpoint_hgb_v3",
    "coherent_checkpoint_hgb_v3__episode_state": "coherent_checkpoint_hgb_v3",
    "coherent_multigrain_hgb_v3__episode_state": (
        "coherent_multigrain_hgb_v3"
    ),
}


def add_episode_state_features(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Materialize prefix-only Tokyo path morphology at each JMA checkpoint.

    Every value is computed from the current target-date prefix, including the
    current observation.  Settlement labels and later observations never enter
    the state calculation.
    """
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in rows:
        grouped[str(raw["target_date"])].append(dict(raw))

    output: list[dict[str, Any]] = []
    for target_date in sorted(grouped):
        selected = sorted(
            grouped[target_date], key=lambda row: str(row["decision_ts_utc"])
        )
        episode_peak: float | None = None
        episode_trough: float | None = None
        episode_trough_ts: datetime | None = None
        relation_by_boundary: dict[float, int] = {}
        cross_count_by_boundary: dict[float, int] = defaultdict(int)
        for row in selected:
            temperature = float(row["jma_temp_c"])
            observed_at = v1.parse_ts(str(row["decision_ts_utc"]))
            if episode_peak is None or temperature > episode_peak + 1e-9:
                episode_peak = temperature
                episode_trough = temperature
                episode_trough_ts = observed_at
            elif episode_trough is None or temperature < episode_trough - 1e-9:
                episode_trough = temperature
                episode_trough_ts = observed_at

            boundary = float(int(row["current_bracket"]) + 0.5)
            relation = int(temperature >= boundary)
            previous_relation = relation_by_boundary.get(boundary)
            if previous_relation is not None and relation != previous_relation:
                cross_count_by_boundary[boundary] += 1
            relation_by_boundary[boundary] = relation

            giveback = max(0.0, float(episode_peak) - float(episode_trough))
            recovery = max(0.0, temperature - float(episode_trough))
            recovery_fraction = recovery / giveback if giveback > 0.05 else 0.0
            delta_10m = v1.finite(row.get("jma_temp_delta_10m")) or 0.0
            slope_30m = v1.finite(row.get("jma_temp_slope_30m_cph")) or 0.0
            slope_60m = v1.finite(row.get("jma_temp_slope_60m_cph")) or 0.0
            pulled_back_then_recovered = giveback >= 0.3 and recovery >= 0.2
            row.update(
                {
                    "jma_episode_peak_c": episode_peak,
                    "jma_episode_trough_c": episode_trough,
                    "jma_episode_giveback_c": giveback,
                    "jma_recovery_from_trough_c": recovery,
                    "jma_recovery_fraction": min(recovery_fraction, 2.0),
                    "minutes_since_episode_trough": (
                        0.0
                        if episode_trough_ts is None
                        else max(
                            0.0,
                            (observed_at - episode_trough_ts).total_seconds()
                            / 60.0,
                        )
                    ),
                    "jma_heating_reacceleration_cph": slope_30m - slope_60m,
                    "jma_short_long_slope_reversal_cph": (
                        delta_10m * 6.0 - slope_60m
                    ),
                    "jma_cross_count_current_boundary": (
                        cross_count_by_boundary[boundary]
                    ),
                    "jma_has_pullback_then_recovery": int(
                        pulled_back_then_recovered
                    ),
                    "jma_reheat_active": int(
                        pulled_back_then_recovered
                        and delta_10m > 0.0
                        and slope_30m > 0.0
                    ),
                }
            )
            output.append(row)
    return output


def load_winners_from_reference_rows(
    path: Path,
    *,
    start: str = FORWARD_START,
    end: str = FORWARD_END,
) -> dict[str, str]:
    """Recover evaluation labels from an immutable prior prediction table.

    This is label-only evidence.  It is never joined into model features and
    is useful after hot-layer pm_history retention has moved an old window.
    """
    if not path.exists():
        raise FileNotFoundError(path)
    by_date: dict[str, set[str]] = defaultdict(set)
    for row in v1.read_rows(path):
        target_date = str(row.get("target_date") or "")
        winner = str(row.get("winning_bracket") or "")
        if start <= target_date <= end and winner:
            by_date[target_date].add(winner)
    conflicts = {
        target_date: sorted(values)
        for target_date, values in by_date.items()
        if len(values) != 1
    }
    if conflicts:
        raise RuntimeError(f"conflicting settlement reference rows: {conflicts}")
    return {
        target_date: next(iter(values))
        for target_date, values in by_date.items()
    }


def episode_slice_memberships(row: dict[str, Any]) -> tuple[str, ...]:
    memberships = ["all_checkpoints"]
    if str(row.get("path_phase")) == "pullback":
        memberships.append("path_pullback")
    if int(float(row.get("jma_has_pullback_then_recovery") or 0)):
        memberships.append("pullback_then_recovery")
    if int(float(row.get("jma_reheat_active") or 0)):
        memberships.append("reheat_active")
    if float(row.get("jma_cross_count_current_boundary") or 0) >= 2:
        memberships.append("current_boundary_recross")
    if float(row.get("local_hour") or 0) >= 13:
        memberships.append("local_hour_ge_13")
    return tuple(memberships)


def episode_slice_probability_scores(
    rows: list[dict[str, Any]],
    predictions: dict[str, np.ndarray],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for slice_name in (
        "all_checkpoints",
        "path_pullback",
        "pullback_then_recovery",
        "reheat_active",
        "current_boundary_recross",
        "local_hour_ge_13",
    ):
        indexes = np.asarray(
            [
                index
                for index, row in enumerate(rows)
                if slice_name in episode_slice_memberships(row)
            ],
            dtype=int,
        )
        selected = [rows[index] for index in indexes]
        if not selected:
            continue
        for candidate, baseline in FEATURE_AB_PAIRS.items():
            candidate_probabilities = predictions[candidate][indexes]
            baseline_probabilities = predictions[baseline][indexes]
            brier_delta, brier_low, brier_high = v1.date_bootstrap_delta(
                selected,
                candidate_probabilities,
                baseline_probabilities,
                metric="brier",
            )
            logloss_delta, logloss_low, logloss_high = v1.date_bootstrap_delta(
                selected,
                candidate_probabilities,
                baseline_probabilities,
                metric="logloss",
            )
            output.append(
                {
                    "split": "frozen_forward_15d",
                    "slice": slice_name,
                    "grain": "checkpoint",
                    "candidate": candidate,
                    "baseline": baseline,
                    "states": len(selected),
                    "target_dates": len(
                        {str(row["target_date"]) for row in selected}
                    ),
                    "candidate_brier": v1.date_equal_loss(
                        selected, candidate_probabilities, metric="brier"
                    ),
                    "baseline_brier": v1.date_equal_loss(
                        selected, baseline_probabilities, metric="brier"
                    ),
                    "brier_delta_candidate_minus_baseline": brier_delta,
                    "brier_delta_ci_low": brier_low,
                    "brier_delta_ci_high": brier_high,
                    "logloss_delta_candidate_minus_baseline": logloss_delta,
                    "logloss_delta_ci_low": logloss_low,
                    "logloss_delta_ci_high": logloss_high,
                }
            )
    return output


def paired_trade_changes(
    trades: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for candidate, baseline in FEATURE_AB_PAIRS.items():
        baseline_rows = {
            str(row["position_key"]): row
            for row in trades
            if row["model"] == baseline
        }
        candidate_rows = {
            str(row["position_key"]): row
            for row in trades
            if row["model"] == candidate
        }
        for position_key in sorted(set(baseline_rows) | set(candidate_rows)):
            old = baseline_rows.get(position_key)
            new = candidate_rows.get(position_key)
            same_action = bool(
                old
                and new
                and old["side"] == new["side"]
                and old["expression_bracket"] == new["expression_bracket"]
                and old["snapshot_ts_utc"] == new["snapshot_ts_utc"]
            )
            output.append(
                {
                    "candidate": candidate,
                    "baseline": baseline,
                    "position_key": position_key,
                    "change_class": (
                        "same_action"
                        if same_action
                        else "candidate_only"
                        if old is None
                        else "baseline_only"
                        if new is None
                        else "changed_action"
                    ),
                    "target_date": str((new or old)["target_date"]),
                    "baseline_side": old.get("side") if old else None,
                    "candidate_side": new.get("side") if new else None,
                    "baseline_snapshot_ts_utc": (
                        old.get("snapshot_ts_utc") if old else None
                    ),
                    "candidate_snapshot_ts_utc": (
                        new.get("snapshot_ts_utc") if new else None
                    ),
                    "baseline_pnl_usd": (
                        (v1.finite(old.get("fee_adjusted_pnl_usd")) or 0.0)
                        if old
                        else 0.0
                    ),
                    "candidate_pnl_usd": (
                        (v1.finite(new.get("fee_adjusted_pnl_usd")) or 0.0)
                        if new
                        else 0.0
                    ),
                    "pnl_delta_usd": (
                        (v1.finite(new.get("fee_adjusted_pnl_usd")) or 0.0)
                        if new
                        else 0.0
                    )
                    - (
                        (v1.finite(old.get("fee_adjusted_pnl_usd")) or 0.0)
                        if old
                        else 0.0
                    ),
                }
            )
    return output


def split_train_forward(
    rows: list[dict[str, Any]],
    *,
    train_cutoff: str = TRAIN_CUTOFF,
    forward_start: str = FORWARD_START,
    forward_end: str = FORWARD_END,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if train_cutoff >= forward_start:
        raise ValueError("train cutoff must precede forward start")
    train = [
        row for row in rows if str(row["target_date"]) <= train_cutoff
    ]
    forward = [
        row
        for row in rows
        if forward_start <= str(row["target_date"]) <= forward_end
    ]
    overlap = {
        str(row["state_id"]) for row in train
    } & {str(row["state_id"]) for row in forward}
    if overlap:
        raise RuntimeError("train/forward state overlap")
    return train, forward


def fit_frozen_models(
    train: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    direct = v1.fit_hgb(
        train,
        "remaining_rise_class",
        feature_names=BASELINE_FEATURES,
    )
    episode_direct = v1.fit_hgb(
        train,
        "remaining_rise_class",
        feature_names=EPISODE_FEATURE_SET,
    )
    checkpoint = v2.fit_coherent_hurdle(
        train, date_weights(train), FROZEN_PARAMS
    )
    episode_checkpoint = v2.fit_coherent_hurdle(
        train,
        date_weights(train),
        FROZEN_PARAMS,
        feature_names=EPISODE_FEATURE_SET,
    )
    multigrain = v2.fit_coherent_hurdle(
        train, v2.multigrain_weights(train), FROZEN_PARAMS
    )
    episode_multigrain = v2.fit_coherent_hurdle(
        train,
        v2.multigrain_weights(train),
        FROZEN_PARAMS,
        feature_names=EPISODE_FEATURE_SET,
    )
    return {
        "direct_checkpoint_hgb_v3": {
            "kind": "direct",
            "model": direct,
            "feature_names": BASELINE_FEATURES,
            "temperature": FROZEN_TEMPERATURES[
                "direct_checkpoint_hgb_v3"
            ],
        },
        "direct_checkpoint_hgb_v3__episode_state": {
            "kind": "direct",
            "model": episode_direct,
            "feature_names": EPISODE_FEATURE_SET,
            "temperature": FROZEN_TEMPERATURES[
                "direct_checkpoint_hgb_v3__episode_state"
            ],
        },
        "coherent_checkpoint_hgb_v3": {
            "kind": "coherent_hurdle",
            "model": checkpoint,
            "feature_names": BASELINE_FEATURES,
            "temperature": FROZEN_TEMPERATURES[
                "coherent_checkpoint_hgb_v3"
            ],
        },
        "coherent_checkpoint_hgb_v3__episode_state": {
            "kind": "coherent_hurdle",
            "model": episode_checkpoint,
            "feature_names": EPISODE_FEATURE_SET,
            "temperature": FROZEN_TEMPERATURES[
                "coherent_checkpoint_hgb_v3__episode_state"
            ],
        },
        "coherent_multigrain_hgb_v3": {
            "kind": "coherent_hurdle",
            "model": multigrain,
            "feature_names": BASELINE_FEATURES,
            "temperature": FROZEN_TEMPERATURES[
                "coherent_multigrain_hgb_v3"
            ],
        },
        "coherent_multigrain_hgb_v3__episode_state": {
            "kind": "coherent_hurdle",
            "model": episode_multigrain,
            "feature_names": EPISODE_FEATURE_SET,
            "temperature": FROZEN_TEMPERATURES[
                "coherent_multigrain_hgb_v3__episode_state"
            ],
        },
    }


def predict_models(
    artifacts: dict[str, dict[str, Any]],
    rows: list[dict[str, Any]],
) -> dict[str, np.ndarray]:
    output = {}
    for name, artifact in artifacts.items():
        if artifact["kind"] == "direct":
            output[name] = v1.apply_temperature(
                v1.aligned_probabilities(
                    artifact["model"],
                    rows,
                    feature_names=tuple(artifact["feature_names"]),
                ),
                float(artifact["temperature"]),
            )
        else:
            output[name] = v2.coherent_probabilities(
                artifact["model"],
                rows,
                temperature=float(artifact["temperature"]),
                feature_names=tuple(artifact["feature_names"]),
            )
    return output


def bootstrap_mean(
    values_by_date: dict[str, list[float]],
) -> tuple[float, float, float]:
    blocks = np.asarray(
        [float(np.mean(values)) for values in values_by_date.values()],
        dtype=float,
    )
    if not len(blocks):
        return math.nan, math.nan, math.nan
    rng = np.random.default_rng(20260731)
    draws = np.asarray(
        [
            float(np.mean(rng.choice(blocks, len(blocks), replace=True)))
            for _ in range(5000)
        ]
    )
    return (
        float(np.mean(blocks)),
        float(np.quantile(draws, 0.025)),
        float(np.quantile(draws, 0.975)),
    )


def wilson_interval(wins: int, total: int) -> tuple[float, float]:
    if total <= 0:
        return math.nan, math.nan
    z = 1.959963984540054
    p = wins / total
    denominator = 1.0 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = (
        z
        * math.sqrt(
            p * (1.0 - p) / total + z * z / (4 * total * total)
        )
        / denominator
    )
    low = 0.0 if wins == 0 else max(0.0, center - half)
    high = 1.0 if wins == total else min(1.0, center + half)
    return low, high


def probability_scores(
    rows: list[dict[str, Any]],
    predictions: dict[str, np.ndarray],
    *,
    split: str,
) -> list[dict[str, Any]]:
    output = []
    for grain in v2.GRAINS:
        indexes = v2.grain_indices(rows, grain)
        selected = [rows[index] for index in indexes]
        baseline = predictions[CHAMPION][indexes]
        labels = np.asarray(
            [int(row["remaining_rise_class"]) for row in selected]
        )
        for model, all_probabilities in predictions.items():
            probabilities = all_probabilities[indexes]
            guesses = np.argmax(probabilities, axis=1)
            stay_labels = (labels == 0).astype(int)
            stay_guesses = (probabilities[:, 0] >= 0.5).astype(int)
            daily_exact: dict[str, list[float]] = defaultdict(list)
            daily_within: dict[str, list[float]] = defaultdict(list)
            daily_stay_accuracy: dict[str, list[float]] = defaultdict(list)
            for row, guess, label, stay_guess, stay_label in zip(
                selected,
                guesses,
                labels,
                stay_guesses,
                stay_labels,
            ):
                target_date = str(row["target_date"])
                daily_exact[target_date].append(float(guess == label))
                daily_within[target_date].append(
                    float(abs(int(guess) - int(label)) <= 1)
                )
                daily_stay_accuracy[target_date].append(
                    float(stay_guess == stay_label)
                )
            exact_mean, exact_low, exact_high = bootstrap_mean(daily_exact)
            within_mean, within_low, within_high = bootstrap_mean(
                daily_within
            )
            stay_mean, stay_low, stay_high = bootstrap_mean(
                daily_stay_accuracy
            )
            record = {
                "split": split,
                "grain": grain,
                "model": model,
                "states": len(selected),
                "target_dates": len(
                    {str(row["target_date"]) for row in selected}
                ),
                "multiclass_logloss": v1.date_equal_loss(
                    selected, probabilities, metric="logloss"
                ),
                "multiclass_brier": v1.date_equal_loss(
                    selected, probabilities, metric="brier"
                ),
                "ranked_probability_score": v1.date_equal_loss(
                    selected, probabilities, metric="rps"
                ),
                "exact_class_accuracy": exact_mean,
                "exact_class_accuracy_ci_low": exact_low,
                "exact_class_accuracy_ci_high": exact_high,
                "within_one_accuracy": within_mean,
                "within_one_accuracy_ci_low": within_low,
                "within_one_accuracy_ci_high": within_high,
                "stay_leave_accuracy": stay_mean,
                "stay_leave_accuracy_ci_low": stay_low,
                "stay_leave_accuracy_ci_high": stay_high,
            }
            daily_stay_brier: dict[str, list[float]] = defaultdict(list)
            for row, probability, label in zip(
                selected, probabilities[:, 0], stay_labels
            ):
                daily_stay_brier[str(row["target_date"])].append(
                    (float(probability) - int(label)) ** 2
                )
            record["stay_brier"] = bootstrap_mean(daily_stay_brier)[0]
            if model != CHAMPION:
                for metric in ("brier", "logloss"):
                    delta, low, high = v1.date_bootstrap_delta(
                        selected,
                        probabilities,
                        baseline,
                        metric=metric,
                    )
                    record[f"{metric}_delta_vs_frozen_champion"] = delta
                    record[f"{metric}_delta_ci_low"] = low
                    record[f"{metric}_delta_ci_high"] = high
            output.append(record)
    return output


def daily_probability_scores(
    rows: list[dict[str, Any]],
    predictions: dict[str, np.ndarray],
) -> list[dict[str, Any]]:
    output = []
    by_date: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        by_date[str(row["target_date"])].append(index)
    for target_date, indexes in sorted(by_date.items()):
        selected = [rows[index] for index in indexes]
        labels = np.asarray(
            [int(row["remaining_rise_class"]) for row in selected]
        )
        for model, all_probabilities in predictions.items():
            probabilities = all_probabilities[indexes]
            guesses = np.argmax(probabilities, axis=1)
            output.append(
                {
                    "target_date": target_date,
                    "model": model,
                    "states": len(selected),
                    "multiclass_brier": v1.date_equal_loss(
                        selected, probabilities, metric="brier"
                    ),
                    "ranked_probability_score": v1.date_equal_loss(
                        selected, probabilities, metric="rps"
                    ),
                    "exact_class_accuracy": float(
                        np.mean(guesses == labels)
                    ),
                    "within_one_accuracy": float(
                        np.mean(np.abs(guesses - labels) <= 1)
                    ),
                }
            )
    return output


def outcome_calibration(
    rows: list[dict[str, Any]],
    predictions: dict[str, np.ndarray],
) -> list[dict[str, Any]]:
    """Date-equal calibration for the two intended trade outcomes."""
    weights = date_weights(rows, normalize=False)
    output = []
    for model, probabilities in predictions.items():
        for outcome_index in (0, 1):
            outcome_id = OUTCOMES[outcome_index]
            values = probabilities[:, outcome_index]
            labels = np.asarray(
                [
                    int(int(row["remaining_rise_class"]) == outcome_index)
                    for row in rows
                ],
                dtype=float,
            )
            for bucket in (
                "[0,.05)",
                "[.05,.10)",
                "[.10,.25)",
                "[.25,.50)",
                "[.50,.75)",
                "[.75,.90)",
                "[.90,1]",
            ):
                mask = np.asarray(
                    [probability_bin(float(value)) == bucket for value in values]
                )
                if not np.any(mask):
                    continue
                selected_weights = weights[mask]
                weight_mass = float(selected_weights.sum())
                selected_weights /= weight_mass
                mean_probability = float(
                    np.sum(values[mask] * selected_weights)
                )
                actual_rate = float(
                    np.sum(labels[mask] * selected_weights)
                )
                output.append(
                    {
                        "split": "frozen_forward_15d",
                        "model": model,
                        "outcome_id": outcome_id,
                        "p_model_bin": bucket,
                        "states": int(np.sum(mask)),
                        "target_dates": len(
                            {
                                str(row["target_date"])
                                for row, keep in zip(rows, mask)
                                if keep
                            }
                        ),
                        "date_equal_weight_mass": weight_mass,
                        "mean_p_model": mean_probability,
                        "actual_outcome_rate": actual_rate,
                        "calibration_gap": mean_probability - actual_rate,
                    }
                )
    return output


def persist_models(
    out: Path,
    artifacts: dict[str, dict[str, Any]],
    feature_hash: str,
) -> dict[str, dict[str, str]]:
    model_dir = out / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    output = {}
    for name, artifact in artifacts.items():
        feature_semantic_hash = feature_hash
        if tuple(artifact["feature_names"]) == EPISODE_FEATURE_SET:
            feature_semantic_hash = hashlib.sha256(
                v2.canonical_json(
                    {
                        "base_feature_semantic_sha256": feature_hash,
                        "derived_feature_semantic_version": (
                            EPISODE_FEATURE_SEMANTIC_VERSION
                        ),
                        "derived_features": list(EPISODE_STATE_FEATURES),
                    }
                ).encode("utf-8")
            ).hexdigest()
        spec = {
            "schema_version": "tokyo_continuous_ladder_forward_v3",
            "model": name,
            "features": list(artifact["feature_names"]),
            "feature_semantic_sha256": feature_semantic_hash,
            "training_cutoff": TRAIN_CUTOFF,
            "forward_window": [FORWARD_START, FORWARD_END],
            "forward_labels_used_in_fit": False,
            "parameters": (
                FROZEN_PARAMS
                if name not in {
                    "direct_checkpoint_hgb_v3",
                    "direct_checkpoint_hgb_v3__episode_state",
                }
                else {
                    "learning_rate": 0.035,
                    "max_iter": 220,
                    "max_leaf_nodes": 15,
                    "min_samples_leaf": 120,
                    "l2_regularization": 5.0,
                }
            ),
            "temperature": FROZEN_TEMPERATURES[name],
            "temperature_selection_window": "2025-07-01/2025-12-31",
        }
        spec_bytes = (v2.canonical_json(spec) + "\n").encode("utf-8")
        spec_path = model_dir / f"{name}.spec.json"
        spec_path.write_bytes(spec_bytes)
        model_path = model_dir / f"{name}.joblib"
        joblib.dump(artifact, model_path, compress=3)
        output[name] = {
            "feature_semantic_sha256": feature_semantic_hash,
            "model_spec_sha256": hashlib.sha256(spec_bytes).hexdigest(),
            "model_artifact_sha256": hashlib.sha256(
                model_path.read_bytes()
            ).hexdigest(),
        }
    return output


def long_predictions(
    rows: list[dict[str, Any]],
    predictions: dict[str, np.ndarray],
    exact: dict[str, datetime],
    hashes: dict[str, dict[str, str]],
) -> Iterable[dict[str, Any]]:
    for index, row in enumerate(rows):
        observed = v1.parse_ts(str(row["decision_ts_utc"]))
        exact_available = exact.get(observed.isoformat())
        availability_class = (
            "collector_exact_hash_verified"
            if exact_available is not None
            else "archive_observation_timestamp_not_first_seen"
        )
        for model, probabilities in predictions.items():
            distribution_id = hashlib.sha256(
                (
                    f"{row['state_id']}|{model}|"
                    f"{hashes[model]['model_artifact_sha256']}"
                ).encode()
            ).hexdigest()
            for outcome_index, outcome in enumerate(OUTCOMES):
                yield {
                    "schema_version": (
                        "weather_city_probability_prediction_v3_forward"
                    ),
                    "city": "Tokyo",
                    "target_date": row["target_date"],
                    "decision_ts_utc": row["decision_ts_utc"],
                    "prediction_ts_utc": (
                        exact_available or observed
                    ).isoformat(),
                    "availability_clock_class": availability_class,
                    "state_id": row["state_id"],
                    "path_phase": row["path_phase"],
                    "target_id": "eod_remaining_rise_delta_distribution",
                    "distribution_id": distribution_id,
                    "outcome_id": outcome,
                    "p_model": float(
                        probabilities[index, outcome_index]
                    ),
                    "label": int(
                        int(row["remaining_rise_class"]) == outcome_index
                    ),
                    "label_delta": row["remaining_rise_class"],
                    "current_bracket": row["current_bracket"],
                    "model_id": model,
                    "training_cutoff": TRAIN_CUTOFF,
                    "frozen_forward": 1,
                    "forward_labels_used_in_fit": 0,
                    "feature_semantic_sha256": hashes[model][
                        "feature_semantic_sha256"
                    ],
                    **hashes[model],
                    "pit_provenance": row["pit_provenance"],
                }


def official_fee_per_share(price: float) -> float:
    return round(FEE_RATE * price * (1.0 - price), 5)


def current_next_candidates(
    joined: list[dict[str, Any]],
    model_names: Iterable[str],
) -> list[dict[str, Any]]:
    """Generate only current/next exact-bracket taker candidates."""
    output = []
    for row in joined:
        if int(row["settlement_lower_bound_violation"]):
            continue
        quotes = json.loads(str(row["quotes_json"]))
        current = int(row["current_bracket"])
        for model in model_names:
            probabilities = json.loads(
                str(row[f"{model}_distribution_json"])
            )
            for delta in (0, 1):
                bracket = str(current + delta)
                quote = quotes.get(bracket)
                if not quote:
                    continue
                p_yes = float(probabilities[delta])
                yes_ask = v1.finite(quote.get("ask"))
                yes_bid = v1.finite(quote.get("bid"))
                for side, p_win, ask in (
                    ("YES", p_yes, yes_ask),
                    (
                        "NO",
                        1.0 - p_yes,
                        None if yes_bid is None else 1.0 - yes_bid,
                    ),
                ):
                    if ask is None or not 0 < ask < 1:
                        continue
                    fee = official_fee_per_share(float(ask))
                    candidate = dict(row)
                    candidate.update(
                        {
                            "model": model,
                            "expression_delta": delta,
                            "expression_bracket": bracket,
                            "side": side,
                            "p_win": p_win,
                            "selected_side_ask": ask,
                            "fee_per_share": fee,
                            "fee_adjusted_edge": p_win - ask - fee,
                            "strategy_policy": (
                                "first_current_or_next_exact_edge_ge_2pct"
                            ),
                            "trade_class": "research_counterfactual",
                        }
                    )
                    output.append(candidate)
    return output


def select_first_signal(
    candidates: list[dict[str, Any]],
    raw_books: Path,
    *,
    selection_policy: str = "first_signal_per_model_target_date",
) -> list[dict[str, Any]]:
    if selection_policy not in {
        "first_signal_per_model_target_date",
        "first_signal_per_model_target_date_bracket",
    }:
        raise ValueError(f"unsupported selection policy: {selection_policy}")
    eligible = [
        row
        for row in candidates
        if float(row["fee_adjusted_edge"]) >= EDGE_THRESHOLD
    ]
    best_by_decision: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in eligible:
        if selection_policy == "first_signal_per_model_target_date":
            key = (str(row["model"]), str(row["state_id"]))
        else:
            # A condition can appear first as "next" and later as "current".
            # Treat the exact bracket as one position and choose at most one
            # side at a checkpoint before applying the no-add-on rule.
            key = (
                str(row["model"]),
                str(row["target_date"]),
                str(row["expression_bracket"]),
                str(row["snapshot_ts_utc"]),
            )
        previous = best_by_decision.get(key)
        if previous is None or float(row["fee_adjusted_edge"]) > float(
            previous["fee_adjusted_edge"]
        ):
            best_by_decision[key] = row
    first_by_position: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in sorted(
        best_by_decision.values(),
        key=lambda item: str(item["availability_ts_utc"]),
    ):
        position_key: tuple[str, ...] = (
            str(row["model"]),
            str(row["target_date"]),
        )
        if (
            selection_policy
            == "first_signal_per_model_target_date_bracket"
        ):
            position_key += (str(row["expression_bracket"]),)
        first_by_position.setdefault(position_key, row)

    output = []
    for row in first_by_position.values():
        ask_size = v1.raw_ask_size(
            raw_books,
            str(row["target_date"]),
            str(row["snapshot_ts_utc"]),
            str(row["expression_bracket"]),
            str(row["side"]),
        )
        executable = ask_size is not None and ask_size >= SHARES
        winner = str(row["winning_bracket"])
        won = (
            winner == str(row["expression_bracket"])
            if row["side"] == "YES"
            else winner != str(row["expression_bracket"])
        )
        selected = dict(row)
        selected.update(
            {
                "strategy_policy": selection_policy,
                "position_key": "|".join(
                    (
                        str(row["target_date"]),
                        str(row["expression_bracket"]),
                    )
                ),
                "add_on_allowed": 0,
                "ask_size": ask_size,
                "five_share_executable": int(executable),
                "settled_win": int(won),
            }
        )
        if executable:
            cost = SHARES * (
                float(row["selected_side_ask"])
                + float(row["fee_per_share"])
            )
            pnl = (SHARES if won else 0.0) - cost
            selected.update(
                {
                    "shares": SHARES,
                    "entry_cost_usd": cost,
                    "payout_usd": SHARES if won else 0.0,
                    "fee_adjusted_pnl_usd": pnl,
                }
            )
        output.append(selected)
    return output


def roi_bootstrap(
    trades: list[dict[str, Any]],
    denominator_dates: list[str],
) -> tuple[float, float, float, float, float, float]:
    daily = {
        target_date: {"pnl": 0.0, "cost": 0.0}
        for target_date in denominator_dates
    }
    for row in trades:
        if not int(row.get("five_share_executable", 0)):
            continue
        target_date = str(row["target_date"])
        daily[target_date]["pnl"] += float(row["fee_adjusted_pnl_usd"])
        daily[target_date]["cost"] += float(row["entry_cost_usd"])
    blocks = list(daily.values())
    total_pnl = sum(row["pnl"] for row in blocks)
    total_cost = sum(row["cost"] for row in blocks)
    roi = total_pnl / total_cost if total_cost else math.nan
    rng = np.random.default_rng(20260731)
    roi_draws = []
    pnl_draws = []
    for _ in range(5000):
        indexes = rng.integers(0, len(blocks), len(blocks))
        draw_pnl = sum(blocks[index]["pnl"] for index in indexes)
        draw_cost = sum(blocks[index]["cost"] for index in indexes)
        pnl_draws.append(draw_pnl / len(blocks))
        roi_draws.append(
            draw_pnl / draw_cost if draw_cost else math.nan
        )
    valid_roi_draws = np.asarray(
        [value for value in roi_draws if math.isfinite(value)]
    )
    if len(valid_roi_draws):
        roi_low = float(np.quantile(valid_roi_draws, 0.025))
        roi_high = float(np.quantile(valid_roi_draws, 0.975))
    else:
        roi_low = math.nan
        roi_high = math.nan
    return (
        roi,
        roi_low,
        roi_high,
        float(np.mean(pnl_draws)),
        float(np.quantile(pnl_draws, 0.025)),
        float(np.quantile(pnl_draws, 0.975)),
    )


def strategy_summary(
    candidates: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    *,
    split: str,
    denominator_dates: list[str],
    model_names: Iterable[str] = MODEL_NAMES,
) -> list[dict[str, Any]]:
    output = []
    for model in model_names:
        model_candidates = [
            row for row in candidates if row["model"] == model
        ]
        selected = [row for row in trades if row["model"] == model]
        executable = [
            row
            for row in selected
            if int(row.get("five_share_executable", 0)) == 1
        ]
        cost = sum(float(row["entry_cost_usd"]) for row in executable)
        pnl = sum(
            float(row["fee_adjusted_pnl_usd"]) for row in executable
        )
        wins = sum(int(row["settled_win"]) for row in executable)
        win_rate_low, win_rate_high = wilson_interval(
            wins, len(executable)
        )
        (
            roi,
            roi_low,
            roi_high,
            mean_daily_pnl,
            mean_daily_pnl_low,
            mean_daily_pnl_high,
        ) = roi_bootstrap(executable, denominator_dates)
        output.append(
            {
                "split": split,
                "model": model,
                "forward_denominator_dates": len(denominator_dates),
                "quote_candidates": len(model_candidates),
                "eligible_candidate_states": len(
                    {
                        str(row["state_id"])
                        for row in model_candidates
                        if float(row["fee_adjusted_edge"])
                        >= EDGE_THRESHOLD
                    }
                ),
                "selected_signals": len(selected),
                "selected_signal_dates": len(
                    {str(row["target_date"]) for row in selected}
                ),
                "five_share_executable": len(executable),
                "execution_eligibility_rate": (
                    len(executable) / len(selected) if selected else None
                ),
                "wins": wins,
                "strategy_win_rate": (
                    wins / len(executable)
                    if executable
                    else None
                ),
                "strategy_win_rate_ci_low": win_rate_low,
                "strategy_win_rate_ci_high": win_rate_high,
                "cost_usd": cost,
                "fee_adjusted_pnl_usd": pnl,
                "fee_adjusted_roi": roi,
                "roi_ci_low": roi_low,
                "roi_ci_high": roi_high,
                "mean_pnl_per_forward_date_usd": mean_daily_pnl,
                "mean_pnl_per_forward_date_ci_low": mean_daily_pnl_low,
                "mean_pnl_per_forward_date_ci_high": mean_daily_pnl_high,
                "actual_orders": 0,
                "actual_fills": 0,
                "zero_notional": 1,
            }
        )
    return output


def strategy_side_summary(
    trades: list[dict[str, Any]],
    *,
    split: str,
    model_names: Iterable[str] = MODEL_NAMES,
) -> list[dict[str, Any]]:
    output = []
    for model in model_names:
        for side in ("YES", "NO"):
            for delta in (0, 1):
                selected = [
                    row
                    for row in trades
                    if row["model"] == model
                    and row["side"] == side
                    and int(row["expression_delta"]) == delta
                    and int(row.get("five_share_executable", 0)) == 1
                ]
                if not selected:
                    continue
                cost = sum(
                    float(row["entry_cost_usd"]) for row in selected
                )
                pnl = sum(
                    float(row["fee_adjusted_pnl_usd"]) for row in selected
                )
                output.append(
                    {
                        "split": split,
                        "model": model,
                        "side": side,
                        "expression_delta": delta,
                        "trades": len(selected),
                        "target_dates": len(
                            {
                                str(row["target_date"])
                                for row in selected
                            }
                        ),
                        "wins": sum(
                            int(row["settled_win"]) for row in selected
                        ),
                        "win_rate": sum(
                            int(row["settled_win"]) for row in selected
                        )
                        / len(selected),
                        "cost_usd": cost,
                        "fee_adjusted_pnl_usd": pnl,
                        "fee_adjusted_roi": pnl / cost if cost else None,
                    }
                )
    return output


def probability_bin(value: float) -> str:
    boundaries = (0.0, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 1.01)
    labels = (
        "[0,.05)",
        "[.05,.10)",
        "[.10,.25)",
        "[.25,.50)",
        "[.50,.75)",
        "[.75,.90)",
        "[.90,1]",
    )
    for index, (low, high) in enumerate(
        zip(boundaries[:-1], boundaries[1:])
    ):
        if low <= value < high:
            return labels[index]
    raise ValueError(value)


def edge_bin(value: float) -> str:
    if value < 0.02:
        return "<.02"
    if value < 0.05:
        return "[.02,.05)"
    if value < 0.10:
        return "[.05,.10)"
    if value < 0.25:
        return "[.10,.25)"
    return ">=.25"


def order_distribution(
    trades: list[dict[str, Any]], *, split: str
) -> list[dict[str, Any]]:
    grouped: dict[
        tuple[str, str, str, str, str], list[dict[str, Any]]
    ] = defaultdict(list)
    for row in trades:
        if not int(row.get("five_share_executable", 0)):
            continue
        grouped[
            (
                str(row["model"]),
                str(row["side"]),
                str(row["expression_delta"]),
                probability_bin(float(row["p_win"])),
                probability_bin(float(row["selected_side_ask"])),
            )
        ].append(row)
    output = []
    for (
        model,
        side,
        delta,
        p_bin,
        ask_bin,
    ), selected in sorted(grouped.items()):
        cost = sum(float(row["entry_cost_usd"]) for row in selected)
        pnl = sum(
            float(row["fee_adjusted_pnl_usd"]) for row in selected
        )
        output.append(
            {
                "split": split,
                "model": model,
                "side": side,
                "expression_delta": delta,
                "p_win_bin": p_bin,
                "selected_side_ask_bin": ask_bin,
                "trades": len(selected),
                "target_dates": len(
                    {str(row["target_date"]) for row in selected}
                ),
                "mean_p_win": float(
                    np.mean([float(row["p_win"]) for row in selected])
                ),
                "mean_selected_side_ask": float(
                    np.mean(
                        [
                            float(row["selected_side_ask"])
                            for row in selected
                        ]
                    )
                ),
                "mean_fee_adjusted_edge": float(
                    np.mean(
                        [
                            float(row["fee_adjusted_edge"])
                            for row in selected
                        ]
                    )
                ),
                "wins": sum(int(row["settled_win"]) for row in selected),
                "win_rate": sum(
                    int(row["settled_win"]) for row in selected
                )
                / len(selected),
                "cost_usd": cost,
                "fee_adjusted_pnl_usd": pnl,
                "fee_adjusted_roi": pnl / cost if cost else None,
            }
        )
    return output


def edge_distribution(
    trades: list[dict[str, Any]], *, split: str
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in trades:
        if int(row.get("five_share_executable", 0)):
            grouped[
                (str(row["model"]), edge_bin(float(row["fee_adjusted_edge"])))
            ].append(row)
    output = []
    for (model, bucket), selected in sorted(grouped.items()):
        cost = sum(float(row["entry_cost_usd"]) for row in selected)
        pnl = sum(
            float(row["fee_adjusted_pnl_usd"]) for row in selected
        )
        output.append(
            {
                "split": split,
                "model": model,
                "edge_bin": bucket,
                "trades": len(selected),
                "wins": sum(int(row["settled_win"]) for row in selected),
                "win_rate": sum(
                    int(row["settled_win"]) for row in selected
                )
                / len(selected),
                "cost_usd": cost,
                "fee_adjusted_pnl_usd": pnl,
                "fee_adjusted_roi": pnl / cost if cost else None,
            }
        )
    return output


def join_market_asof_books(
    rows: list[dict[str, Any]],
    predictions: dict[str, np.ndarray],
    exact: dict[str, datetime],
    markets: dict[str, list[dict[str, Any]]],
    winners: dict[str, str],
    *,
    max_state_age_min: float = 30.0,
) -> list[dict[str, Any]]:
    """Join each book to the latest weather state available at book time.

    A weather-state -> next-book join can pair an old probability with a book
    observed after one or more newer JMA updates.  Book time is the executable
    decision clock, so it owns the grain and receives one latest-as-of state.
    """
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    prediction_by_state: dict[
        tuple[str, str], np.ndarray
    ] = {}
    for index, raw in enumerate(rows):
        row = dict(raw)
        observed = v1.parse_ts(str(row["decision_ts_utc"]))
        exact_available = exact.get(observed.isoformat())
        row["_available_ts"] = (
            exact_available
            if exact_available is not None
            else observed + timedelta(minutes=15)
        )
        row["_availability_clock_class"] = (
            "collector_exact_hash_verified"
            if exact_available is not None
            else "archive_reconstructed_plus_15m"
        )
        by_date[str(row["target_date"])].append(row)
        for model, values in predictions.items():
            prediction_by_state[(str(row["state_id"]), model)] = values[
                index
            ]
    for selected in by_date.values():
        selected.sort(key=lambda row: row["_available_ts"])

    output = []
    for target_date, states in sorted(by_date.items()):
        winner = winners.get(target_date)
        winner_anchor = v1.label_anchor(winner) if winner else None
        if winner_anchor is None:
            continue
        availability_times = [row["_available_ts"] for row in states]
        for market in markets.get(target_date, []):
            snapshot = market["timestamp"]
            state_index = bisect_right(availability_times, snapshot) - 1
            if state_index < 0:
                continue
            state = states[state_index]
            age_min = (
                snapshot - state["_available_ts"]
            ).total_seconds() / 60.0
            if age_min > max_state_age_min:
                continue
            current = int(state["current_bracket"])
            actual_delta = int(winner_anchor) - current
            quotes = v1.normalized_yes_quotes(market["quotes"])
            market_distribution, stale_mass = (
                v1.conditional_market_distribution(quotes, current)
            )
            if market_distribution is None:
                continue
            record = {
                "state_id": state["state_id"],
                "target_date": target_date,
                "decision_ts_utc": state["decision_ts_utc"],
                "availability_ts_utc": state["_available_ts"].isoformat(),
                "availability_clock_class": state[
                    "_availability_clock_class"
                ],
                "snapshot_ts_utc": snapshot.isoformat(),
                "availability_to_book_min": age_min,
                "book_join_policy": (
                    "book_snapshot_latest_available_weather_state"
                ),
                "max_state_age_min": max_state_age_min,
                "current_bracket": current,
                "winning_bracket": winner,
                "actual_delta": actual_delta,
                "settlement_lower_bound_violation": int(actual_delta < 0),
                "stale_market_mass_below_current": stale_mass,
                "quotes_json": json.dumps(quotes, sort_keys=True),
                "market_distribution_json": json.dumps(
                    market_distribution.tolist()
                ),
                "local_hour": state["local_hour"],
                "path_phase": state["path_phase"],
                "is_transition": state["is_transition"],
                "is_state_entry": state["is_state_entry"],
                **{
                    feature: state.get(feature)
                    for feature in EPISODE_STATE_FEATURES
                },
            }
            for model in predictions:
                probability = prediction_by_state[
                    (str(state["state_id"]), model)
                ]
                record[f"{model}_distribution_json"] = json.dumps(
                    probability.tolist()
                )
                record[f"{model}_p_current"] = float(probability[0])
            output.append(record)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, default=v1.FEATURE_ROWS)
    parser.add_argument(
        "--exact-first-seen", type=Path, default=v1.EXACT_FIRST_SEEN
    )
    parser.add_argument("--market-states", type=Path, default=v1.MARKET_STATES)
    parser.add_argument("--pm-history", type=Path, default=v1.PM_HISTORY)
    parser.add_argument(
        "--settlement-reference-rows",
        type=Path,
        help=(
            "immutable prior evaluation rows carrying winning_bracket; "
            "label-only supplement when hot pm_history no longer retains "
            "the requested window"
        ),
    )
    parser.add_argument("--raw-books", type=Path, default=v1.RAW_BOOKS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--selection-policy",
        choices=(
            "first_signal_per_model_target_date",
            "first_signal_per_model_target_date_bracket",
        ),
        default="first_signal_per_model_target_date",
    )
    parser.add_argument(
        "--analysis-version",
        default="tokyo_continuous_ladder_forward_v3",
    )
    parser.add_argument(
        "--strategy-evaluation-status",
        default="frozen_before_forward",
    )
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    strategy_market_split = (
        "frozen_forward_15d_market_available"
        if args.strategy_evaluation_status == "frozen_before_forward"
        else "post_forward_selector_diagnostic_market_available"
    )
    strategy_exact_split = (
        "frozen_forward_collector_exact"
        if args.strategy_evaluation_status == "frozen_before_forward"
        else "post_forward_selector_diagnostic_collector_exact"
    )

    raw = v1.read_rows(args.features)
    continuous = v2.annotate_grains(
        add_episode_state_features([
            row
            for row in v1.build_continuous_rows(raw)
            if row["remaining_rise_class"] is not None
        ])
    )
    train, forward = split_train_forward(continuous)
    train_dates = sorted({str(row["target_date"]) for row in train})
    forward_dates = sorted({str(row["target_date"]) for row in forward})
    if forward_dates != [
        date(2026, 7, day).isoformat() for day in range(16, 31)
    ]:
        raise RuntimeError(f"forward window is not 15 complete days: {forward_dates}")

    artifacts = fit_frozen_models(train)
    predictions = predict_models(artifacts, forward)
    feature_hash = v2.semantic_file_hash(args.features)
    hashes = persist_models(args.out, artifacts, feature_hash)
    exact = v1.exact_first_seen(args.exact_first_seen)

    model_scores = probability_scores(
        forward, predictions, split="frozen_forward_15d"
    )
    episode_slice_scores = episode_slice_probability_scores(
        forward, predictions
    )
    daily_scores = daily_probability_scores(forward, predictions)
    calibration = outcome_calibration(forward, predictions)
    prediction_rows = list(
        long_predictions(forward, predictions, exact, hashes)
    )

    markets = v1.load_market_states(args.market_states)
    winners = v1.load_winners(
        args.pm_history, date(2026, 7, 16), date(2026, 7, 30)
    )
    if args.settlement_reference_rows is not None:
        winners.update(
            load_winners_from_reference_rows(args.settlement_reference_rows)
        )
    joined = join_market_asof_books(
        forward,
        predictions,
        exact,
        markets,
        winners,
    )
    for row in joined:
        row["market_split"] = "frozen_forward_15d_market_available"
    market_dates = sorted({str(row["target_date"]) for row in joined})
    exact_joined = [
        row
        for row in joined
        if row["availability_clock_class"]
        == "collector_exact_hash_verified"
    ]
    exact_dates = sorted(
        {str(row["target_date"]) for row in exact_joined}
    )

    market_scores = v1.market_score_rows(
        joined, MODEL_NAMES, "frozen_forward_15d_market_available"
    )
    market_scores.extend(
        v1.market_score_rows(
            exact_joined, MODEL_NAMES, "frozen_forward_collector_exact"
        )
    )
    market_binary_scores = v1.market_binary_scores(
        joined, MODEL_NAMES, "frozen_forward_15d_market_available"
    )
    market_binary_scores.extend(
        v1.market_binary_scores(
            exact_joined, MODEL_NAMES, "frozen_forward_collector_exact"
        )
    )

    candidates = current_next_candidates(joined, MODEL_NAMES)
    trades = select_first_signal(
        candidates,
        args.raw_books,
        selection_policy=args.selection_policy,
    )
    exact_candidates = [
        row
        for row in candidates
        if row["availability_clock_class"]
        == "collector_exact_hash_verified"
    ]
    # Preserve the policy denominator: select on the complete available
    # forward stream first, then audit which selected triggers were exact.
    # Re-selecting after filtering to exact rows would move the first signal
    # later and create collector-coverage survivor bias.
    exact_trades = [
        row
        for row in trades
        if row["availability_clock_class"]
        == "collector_exact_hash_verified"
    ]
    strategy_scores = strategy_summary(
        candidates,
        trades,
        split=strategy_market_split,
        denominator_dates=forward_dates,
    )
    strategy_scores.extend(
        strategy_summary(
            exact_candidates,
            exact_trades,
            split=strategy_exact_split,
            denominator_dates=forward_dates,
        )
    )
    side_scores = strategy_side_summary(
        trades, split=strategy_market_split
    )
    side_scores.extend(
        strategy_side_summary(
            exact_trades, split=strategy_exact_split
        )
    )
    distributions = order_distribution(
        trades, split=strategy_market_split
    )
    distributions.extend(
        order_distribution(
            exact_trades, split=strategy_exact_split
        )
    )
    edge_distributions = edge_distribution(
        trades, split=strategy_market_split
    )
    edge_distributions.extend(
        edge_distribution(
            exact_trades, split=strategy_exact_split
        )
    )
    trade_changes = paired_trade_changes(trades)

    market_episode_slice_scores: list[dict[str, Any]] = []
    for slice_name in (
        "all_checkpoints",
        "path_pullback",
        "pullback_then_recovery",
        "reheat_active",
        "current_boundary_recross",
        "local_hour_ge_13",
    ):
        selected = [
            row
            for row in joined
            if slice_name in episode_slice_memberships(row)
        ]
        if selected:
            market_episode_slice_scores.extend(
                v1.market_score_rows(
                    selected,
                    MODEL_NAMES,
                    f"frozen_forward_market_{slice_name}",
                )
            )

    signal_funnel = [
        {
            "funnel": "signal",
            "stage": "frozen_forward_weather_checkpoints",
            "unit": "state",
            "count": len(forward),
            "target_dates": len(forward_dates),
        },
        {
            "funnel": "signal",
            "stage": "market_joined_current_next_expressions",
            "unit": "expression",
            "count": len(candidates),
            "target_dates": len(market_dates),
        },
        {
            "funnel": "signal",
            "stage": (
                "first_city_day_signal_champion"
                if args.selection_policy
                == "first_signal_per_model_target_date"
                else "first_city_day_bracket_signal_champion"
            ),
            "unit": "signal",
            "count": sum(row["model"] == CHAMPION for row in trades),
            "target_dates": len(
                {
                    str(row["target_date"])
                    for row in trades
                    if row["model"] == CHAMPION
                }
            ),
        },
    ]
    evidence_funnel = [
        {
            "funnel": "evidence",
            "stage": "settled_pit_book_join",
            "unit": "state",
            "count": len(joined),
            "target_dates": len(market_dates),
        },
        {
            "funnel": "evidence",
            "stage": "collector_exact_book_join",
            "unit": "state",
            "count": len(exact_joined),
            "target_dates": len(exact_dates),
        },
        {
            "funnel": "evidence",
            "stage": "five_share_executable_champion",
            "unit": "research_counterfactual_trade",
            "count": sum(
                row["model"] == CHAMPION
                and int(row.get("five_share_executable", 0))
                for row in trades
            ),
            "target_dates": len(
                {
                    str(row["target_date"])
                    for row in trades
                    if row["model"] == CHAMPION
                    and int(row.get("five_share_executable", 0))
                }
            ),
        },
        {
            "funnel": "evidence",
            "stage": "actual_fill",
            "unit": "fill",
            "count": 0,
            "target_dates": 0,
        },
    ]

    write_rows(args.out / "model_scores.csv", model_scores)
    write_rows(args.out / "episode_slice_scores.csv", episode_slice_scores)
    write_rows(args.out / "daily_model_scores.csv", daily_scores)
    write_rows(args.out / "model_outcome_calibration.csv", calibration)
    write_rows(args.out / "prediction_long.csv.gz", prediction_rows)
    write_rows(args.out / "market_join_rows.csv.gz", joined)
    write_rows(args.out / "market_scores.csv", market_scores)
    write_rows(
        args.out / "market_episode_slice_scores.csv",
        market_episode_slice_scores,
    )
    write_rows(
        args.out / "market_binary_scores.csv", market_binary_scores
    )
    write_rows(args.out / "current_next_candidates.csv.gz", candidates)
    write_rows(args.out / "selected_trades.csv", trades)
    write_rows(args.out / "selected_trades_exact.csv", exact_trades)
    write_rows(args.out / "paired_trade_changes.csv", trade_changes)
    write_rows(args.out / "strategy_summary.csv", strategy_scores)
    write_rows(args.out / "strategy_side_summary.csv", side_scores)
    write_rows(args.out / "order_probability_distribution.csv", distributions)
    write_rows(args.out / "order_edge_distribution.csv", edge_distributions)
    write_rows(
        args.out / "funnel.csv", signal_funnel + evidence_funnel
    )

    summary = {
        "schema_version": args.analysis_version,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_only_zero_notional": True,
        "live_behavior_changed": False,
        "denominator_scope": (
            "Tokyo JMA/RJTT daylight 10-minute checkpoints with prior "
            "METAR running maximum and terminal exact-bracket label; "
            "training target_date<=2026-07-15, untouched probability "
            "forward=2026-07-16..2026-07-30"
        ),
        "input_artifacts": {
            "features": str(args.features),
            "exact_first_seen": str(args.exact_first_seen),
            "market_states": str(args.market_states),
            "raw_books": str(args.raw_books),
        },
        "training_cutoff": TRAIN_CUTOFF,
        "training_rows": len(train),
        "training_dates": len(train_dates),
        "forward_start": FORWARD_START,
        "forward_end": FORWARD_END,
        "forward_rows": len(forward),
        "forward_dates": len(forward_dates),
        "forward_labels_used_in_fit": False,
        "market_join_rows": len(joined),
        "settlement_label_sources": {
            "hot_pm_history": str(args.pm_history),
            "immutable_reference_rows": (
                str(args.settlement_reference_rows)
                if args.settlement_reference_rows is not None
                else None
            ),
            "winner_dates": sorted(winners),
            "labels_used_as_features": False,
        },
        "market_join_policy": (
            "book_snapshot_latest_available_weather_state"
        ),
        "max_weather_state_age_at_book_min": 30,
        "market_join_dates": market_dates,
        "market_coverage_gap_dates": sorted(
            set(forward_dates) - set(market_dates)
        ),
        "collector_exact_rows": len(exact_joined),
        "collector_exact_dates": exact_dates,
        "champion_frozen_before_forward": CHAMPION,
        "paired_feature_challenger": {
            "models": [
                name for name in MODEL_NAMES if name.endswith("__episode_state")
            ],
            "paired_baselines": {
                name: name.removesuffix("__episode_state")
                for name in MODEL_NAMES
                if name.endswith("__episode_state")
            },
            "changed_factor": "episode_state_features_only",
            "features_added": list(EPISODE_STATE_FEATURES),
            "same_rows_labels_clocks_hyperparameters_temperature": True,
        },
        "strategy_policy": {
            "evaluation_status": args.strategy_evaluation_status,
            "expressions": ["current_exact", "next_exact"],
            "sides": ["YES", "NO"],
            "edge_threshold": EDGE_THRESHOLD,
            "shares": SHARES,
            "entry": "taker_selected_side_ask",
            "fee_rate": FEE_RATE,
            "fee_rounding": "per_share_5_decimal",
            "selection": args.selection_policy,
            "position_key": (
                "model,target_date"
                if args.selection_policy
                == "first_signal_per_model_target_date"
                else "model,target_date,expression_bracket"
            ),
            "add_on_allowed": False,
        },
        "input_feature_semantic_sha256": feature_hash,
        "episode_feature_semantic_version": (
            EPISODE_FEATURE_SEMANTIC_VERSION
        ),
        "model_hashes": hashes,
    }
    if args.strategy_evaluation_status == "frozen_before_forward":
        # Preserve the v3 summary contract for existing consumers.
        summary["strategy_policy_frozen_before_forward"] = summary[
            "strategy_policy"
        ]
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
