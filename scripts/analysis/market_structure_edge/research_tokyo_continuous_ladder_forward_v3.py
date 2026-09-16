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
from bisect import bisect_left, bisect_right
from collections import defaultdict
import csv
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import sqlite3
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
from scripts.analysis.versioned_artifact_output import (  # noqa: E402
    prepare_new_run_output,
    resolve_run_output,
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
FULL_FUSION_WEATHER_MODELS = (
    "direct_checkpoint_hgb_v3",
    "coherent_checkpoint_hgb_v3",
    "coherent_multigrain_hgb_v3",
)
FULL_FUSION_MARKET_TEMPERATURES = (0.5, 0.6, 0.7, 0.8, 1.0)
FULL_FUSION_WEATHER_WEIGHTS = (0.0, 0.25, 0.33, 0.5, 0.67)
FULL_FUSION_MODEL_ID = (
    "weather.city_intraday_probability.tokyo_continuous_full_probability"
)
FROZEN_FULL_FUSION_WEATHER_MODEL = "coherent_multigrain_hgb_v3"
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
ARTIFACT_FAMILY = "tokyo_continuous_ladder_forward_v3"
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


def full_distribution_geometric_pool(
    market: np.ndarray,
    weather: np.ndarray,
    *,
    market_temperature: float,
    weather_weight: float,
) -> np.ndarray:
    """Fuse complete PIT market/weather distributions without dropping tails."""

    if market.shape != weather.shape or market.ndim != 2:
        raise ValueError("market and weather distributions must have equal 2D shape")
    if market_temperature <= 0:
        raise ValueError("market_temperature must be positive")
    if not 0.0 <= weather_weight <= 1.0:
        raise ValueError("weather_weight must be in [0, 1]")
    market_power = 1.0 / float(market_temperature)
    logits = (
        (1.0 - float(weather_weight))
        * market_power
        * np.log(np.clip(market, EPS, 1.0))
        + float(weather_weight) * np.log(np.clip(weather, EPS, 1.0))
    )
    logits -= logits.max(axis=1, keepdims=True)
    output = np.exp(logits)
    return output / output.sum(axis=1, keepdims=True)


def _read_expression_feature_rows(
    path: Path, *, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    rows = v1.read_rows(path)
    selected = [
        row
        for row in rows
        if row.get("evaluation_role") == "strict_pit_forward"
        and start_date <= str(row.get("target_date") or "") <= end_date
    ]
    selected.sort(
        key=lambda row: (
            str(row["target_date"]),
            v1.parse_ts(str(row["quote_ts_utc"])),
        )
    )
    return selected


def _score_frozen_weather_distribution(
    rows: list[dict[str, Any]], *, artifact_path: Path, spec_path: Path
) -> np.ndarray:
    artifact = joblib.load(artifact_path)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    features = tuple(str(value) for value in spec["features"])
    model_rows = [
        {
            feature: v1.finite(row.get(f"weather_feature__{feature}"))
            for feature in features
        }
        for row in rows
    ]
    if artifact.get("kind") == "coherent_hurdle":
        return v2.coherent_probabilities(
            artifact["model"],
            model_rows,
            temperature=float(artifact["temperature"]),
            feature_names=features,
        )
    if artifact.get("kind") != "direct":
        raise ValueError(f"unsupported frozen weather artifact kind: {artifact.get('kind')}")
    matrix = np.asarray(
        [
            [
                (
                    np.nan if model_row[feature] is None else model_row[feature]
                )
                for feature in features
            ]
            for model_row in model_rows
        ],
        dtype=float,
    )
    raw = artifact["model"].predict_proba(matrix)
    classes = [int(value) for value in artifact["model"].classes_]
    aligned = np.zeros((len(rows), 4), dtype=float)
    for index, outcome in enumerate(classes):
        aligned[:, outcome] = raw[:, index]
    temperature = float(artifact["temperature"])
    logits = np.log(np.clip(aligned, EPS, 1.0)) / temperature
    logits -= logits.max(axis=1, keepdims=True)
    calibrated = np.exp(logits)
    return calibrated / calibrated.sum(axis=1, keepdims=True)


def _load_tokyo_canonical_ladders(
    db_path: Path, *, start_date: str, end_date: str
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    connection = sqlite3.connect(
        f"file:{db_path}?mode=ro", uri=True, timeout=3.0
    )
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=3000")
    connection.row_factory = sqlite3.Row
    try:
        quote_rows = connection.execute(
            """
            SELECT s.ladder_snapshot_id, s.target_date,
                   s.source_snapshot_ts_utc, s.available_at_utc,
                   s.source_path, s.lineage_status, s.completeness_status,
                   r.absolute_bracket_identity AS bracket,
                   r.yes_direct_bid, r.yes_direct_ask,
                   r.yes_direct_bid_size, r.yes_direct_ask_size,
                   r.no_direct_bid, r.no_direct_ask,
                   r.no_direct_bid_size, r.no_direct_ask_size
            FROM tmax_v2_ladder_snapshots AS s
            JOIN tmax_v2_ladder_rung_quotes AS r
              USING (ladder_snapshot_id)
            WHERE s.city = 'Tokyo'
              AND s.target_date BETWEEN ? AND ?
              AND s.completeness_status = 'complete'
              AND s.lineage_status = 'pit_verified_capture'
            ORDER BY s.target_date, s.available_at_utc,
                     s.ladder_snapshot_id, r.absolute_bracket_identity
            """,
            (start_date, end_date),
        ).fetchall()
        winner_rows = connection.execute(
            """
            SELECT target_date, bracket
            FROM settlement_outcomes
            WHERE city = 'Tokyo'
              AND target_date BETWEEN ? AND ?
              AND settlement_status = 'settled'
              AND final_price >= 0.99
            """,
            (start_date, end_date),
        ).fetchall()
    finally:
        connection.close()
    grouped: dict[str, dict[str, Any]] = {}
    for raw in quote_rows:
        row = dict(raw)
        snapshot = grouped.setdefault(
            str(row["ladder_snapshot_id"]),
            {
                "ladder_snapshot_id": str(row["ladder_snapshot_id"]),
                "target_date": str(row["target_date"]),
                "source_snapshot_ts_utc": str(row["source_snapshot_ts_utc"]),
                "available_at_utc": str(row["available_at_utc"]),
                "source_path": str(row["source_path"]),
                "quotes": {},
            },
        )
        direct_yes_bid = v1.finite(row.get("yes_direct_bid"))
        direct_yes_ask = v1.finite(row.get("yes_direct_ask"))
        direct_no_bid = v1.finite(row.get("no_direct_bid"))
        direct_no_ask = v1.finite(row.get("no_direct_ask"))
        bid_candidates = [
            value
            for value in (
                direct_yes_bid,
                None if direct_no_ask is None else 1.0 - direct_no_ask,
            )
            if value is not None
        ]
        ask_candidates = [
            value
            for value in (
                direct_yes_ask,
                None if direct_no_bid is None else 1.0 - direct_no_bid,
            )
            if value is not None
        ]
        bid = max(bid_candidates) if bid_candidates else None
        ask = min(ask_candidates) if ask_candidates else None
        mid = (
            (bid + ask) / 2.0
            if bid is not None and ask is not None
            else ask if ask is not None else bid
        )
        snapshot["quotes"][str(row["bracket"])] = {
            key: value
            for key, value in {
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "yes_ask": direct_yes_ask,
                "yes_ask_size": v1.finite(row.get("yes_direct_ask_size")),
                "no_ask": direct_no_ask,
                "no_ask_size": v1.finite(row.get("no_direct_ask_size")),
            }.items()
            if value is not None
        }
    ladders = list(grouped.values())
    ladders.sort(
        key=lambda row: (
            str(row["target_date"]),
            v1.parse_ts(str(row["available_at_utc"])),
            str(row["ladder_snapshot_id"]),
        )
    )
    winners = {str(row["target_date"]): str(row["bracket"]) for row in winner_rows}
    return ladders, winners


def join_frozen_forward_to_canonical_ladders(
    expression_rows: list[dict[str, Any]],
    weather_probabilities: np.ndarray,
    ladders: list[dict[str, Any]],
    winners: dict[str, str],
    *,
    maximum_ladder_wait_minutes: float = 10.0,
) -> list[dict[str, Any]]:
    """Join each exact JMA state to its first causal full-ladder capture.

    The ladder must arrive after the feature frame and before the next JMA
    state.  This keeps August evaluation PIT while avoiding hundreds of
    repeated five-minute books per one physical weather update.
    """

    if len(expression_rows) != len(weather_probabilities):
        raise ValueError("expression/probability length mismatch")
    ladders_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ladder in ladders:
        ladders_by_date[str(ladder["target_date"])].append(ladder)
    states_by_date: dict[str, list[tuple[dict[str, Any], np.ndarray]]] = defaultdict(list)
    for row, probability in zip(expression_rows, weather_probabilities):
        states_by_date[str(row["target_date"])].append((row, probability))

    output: list[dict[str, Any]] = []
    for target_date, states in sorted(states_by_date.items()):
        winner = winners.get(target_date)
        winner_anchor = v1.label_anchor(winner) if winner else None
        if winner_anchor is None:
            continue
        available_ladders = ladders_by_date.get(target_date, [])
        ladder_times = [
            v1.parse_ts(str(row["available_at_utc"])) for row in available_ladders
        ]
        for state_index, (state, weather_probability) in enumerate(states):
            feature_time = v1.parse_ts(str(state["quote_ts_utc"]))
            next_feature_time = (
                v1.parse_ts(str(states[state_index + 1][0]["quote_ts_utc"]))
                if state_index + 1 < len(states)
                else None
            )
            ladder_index = bisect_left(ladder_times, feature_time)
            if ladder_index >= len(available_ladders):
                continue
            ladder = available_ladders[ladder_index]
            ladder_time = ladder_times[ladder_index]
            wait_minutes = (ladder_time - feature_time).total_seconds() / 60.0
            if wait_minutes > maximum_ladder_wait_minutes:
                continue
            if next_feature_time is not None and ladder_time >= next_feature_time:
                continue
            current = int(float(state["bracket"]))
            quotes = ladder["quotes"]
            market_probability, stale_mass = v1.conditional_market_distribution(
                quotes, current
            )
            if market_probability is None:
                continue
            actual_delta = winner_anchor - current
            output.append(
                {
                    "state_id": str(state["event_id"]),
                    "target_date": target_date,
                    "decision_ts_utc": str(state["source_obs_ts_utc"]),
                    "availability_ts_utc": str(ladder["available_at_utc"]),
                    "availability_clock_class": str(
                        state["availability_clock_class"]
                    ),
                    "snapshot_ts_utc": str(ladder["source_snapshot_ts_utc"]),
                    "feature_book_snapshot_id": str(state["book_snapshot_id"]),
                    "execution_book_snapshot_id": str(
                        ladder["ladder_snapshot_id"]
                    ),
                    "feature_to_ladder_wait_min": wait_minutes,
                    "book_join_policy": (
                        "first_full_ladder_after_exact_feature_before_next_jma_state"
                    ),
                    "current_bracket": current,
                    "winning_bracket": winner,
                    "actual_delta": actual_delta,
                    "settlement_lower_bound_violation": int(actual_delta < 0),
                    "stale_market_mass_below_current": stale_mass,
                    "quotes_json": json.dumps(quotes, sort_keys=True),
                    "market_distribution_json": json.dumps(
                        market_probability.tolist()
                    ),
                    f"{FROZEN_FULL_FUSION_WEATHER_MODEL}_distribution_json": json.dumps(
                        weather_probability.tolist()
                    ),
                    "source_path": str(ladder["source_path"]),
                }
            )
    return output


def _date_equal_distribution_losses(
    rows: list[dict[str, Any]], probabilities: np.ndarray
) -> dict[str, float | int]:
    if len(rows) != len(probabilities):
        raise ValueError("row/probability length mismatch")
    daily: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
    for row, probability in zip(rows, probabilities):
        label = min(max(int(row["actual_delta"]), 0), 3)
        target = np.eye(4, dtype=float)[label]
        brier = float(np.sum((probability - target) ** 2))
        logloss = float(-math.log(max(float(probability[label]), EPS)))
        rps = float(
            np.mean(
                (
                    np.cumsum(probability)[:-1]
                    - np.cumsum(target)[:-1]
                )
                ** 2
            )
        )
        daily[str(row["target_date"])].append((brier, logloss, rps))
    date_means = [np.mean(values, axis=0) for values in daily.values()]
    aggregate = np.mean(date_means, axis=0)
    return {
        "rows": len(rows),
        "target_dates": len(daily),
        "multiclass_brier": float(aggregate[0]),
        "multiclass_logloss": float(aggregate[1]),
        "ranked_probability_score": float(aggregate[2]),
    }


def _date_block_distribution_delta(
    rows: list[dict[str, Any]],
    candidate: np.ndarray,
    baseline: np.ndarray,
    *,
    metric: str,
    draws: int = 20_000,
) -> dict[str, float | int]:
    metric_index = {"brier": 0, "logloss": 1, "rps": 2}[metric]
    grouped: dict[str, list[float]] = defaultdict(list)
    for row, candidate_probability, baseline_probability in zip(
        rows, candidate, baseline
    ):
        label = min(max(int(row["actual_delta"]), 0), 3)
        target = np.eye(4, dtype=float)[label]
        values = []
        for probability in (candidate_probability, baseline_probability):
            values.append(
                (
                    float(np.sum((probability - target) ** 2)),
                    float(-math.log(max(float(probability[label]), EPS))),
                    float(
                        np.mean(
                            (
                                np.cumsum(probability)[:-1]
                                - np.cumsum(target)[:-1]
                            )
                            ** 2
                        )
                    ),
                )[metric_index]
            )
        grouped[str(row["target_date"])].append(values[0] - values[1])
    blocks = np.asarray(
        [float(np.mean(values)) for values in grouped.values()], dtype=float
    )
    rng = np.random.default_rng(20260813)
    sampled = np.asarray(
        [
            float(np.mean(rng.choice(blocks, len(blocks), replace=True)))
            for _ in range(draws)
        ]
    )
    return {
        "delta": float(np.mean(blocks)),
        "ci_low": float(np.quantile(sampled, 0.025)),
        "ci_high": float(np.quantile(sampled, 0.975)),
        "draws": draws,
        "target_dates": len(blocks),
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
                yes_ask = v1.finite(quote.get("yes_ask"))
                if yes_ask is None:
                    yes_ask = v1.finite(quote.get("ask"))
                yes_bid = v1.finite(quote.get("bid"))
                yes_ask_size = v1.finite(quote.get("yes_ask_size"))
                no_ask = v1.finite(quote.get("no_ask"))
                no_ask_size = v1.finite(quote.get("no_ask_size"))
                for side, p_win, ask in (
                    ("YES", p_yes, yes_ask),
                    (
                        "NO",
                        1.0 - p_yes,
                        (
                            no_ask
                            if no_ask is not None
                            else None if yes_bid is None else 1.0 - yes_bid
                        ),
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
                            "selected_side_ask_size": (
                                yes_ask_size if side == "YES" else no_ask_size
                            ),
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
        ask_size = v1.finite(row.get("selected_side_ask_size"))
        if ask_size is None:
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


def run_full_probability_fusion_audit(
    *,
    input_path: Path,
    output_dir: Path,
    raw_books: Path,
    selection_end: str,
    clean_forward_start: str,
) -> dict[str, Any]:
    """Develop and freeze Tokyo V3 full-ladder market/weather fusion.

    The supplied July market-overlap slice has already been inspected by prior
    research, so the later temporal split is validation, not clean forward.
    A genuinely clean window starts only after this candidate spec is frozen.
    """

    raw_rows = v1.read_rows(input_path)
    rows = [
        row
        for row in raw_rows
        if int(float(row.get("settlement_lower_bound_violation") or 0)) == 0
        and row.get("market_distribution_json")
        and all(
            row.get(f"{model}_distribution_json")
            for model in FULL_FUSION_WEATHER_MODELS
        )
    ]
    rows.sort(key=lambda row: (str(row["target_date"]), str(row["snapshot_ts_utc"])))
    if not rows:
        raise RuntimeError("no complete settled full-ladder rows")
    selection_rows = [row for row in rows if str(row["target_date"]) <= selection_end]
    validation_rows = [row for row in rows if str(row["target_date"]) > selection_end]
    if len({str(row["target_date"]) for row in selection_rows}) < 5:
        raise RuntimeError("full fusion selection requires at least five target dates")
    if len({str(row["target_date"]) for row in validation_rows}) < 2:
        raise RuntimeError("full fusion validation requires at least two target dates")

    market = np.asarray(
        [json.loads(str(row["market_distribution_json"])) for row in rows],
        dtype=float,
    )
    weather_by_model = {
        model: np.asarray(
            [
                json.loads(str(row[f"{model}_distribution_json"]))
                for row in rows
            ],
            dtype=float,
        )
        for model in FULL_FUSION_WEATHER_MODELS
    }
    selection_mask = np.asarray(
        [str(row["target_date"]) <= selection_end for row in rows], dtype=bool
    )
    validation_mask = ~selection_mask

    parameter_rows: list[dict[str, Any]] = []
    probability_by_key: dict[tuple[str, float, float], np.ndarray] = {}
    for weather_model, weather in weather_by_model.items():
        for market_temperature in FULL_FUSION_MARKET_TEMPERATURES:
            for weather_weight in FULL_FUSION_WEATHER_WEIGHTS:
                probability = full_distribution_geometric_pool(
                    market,
                    weather,
                    market_temperature=market_temperature,
                    weather_weight=weather_weight,
                )
                key = (weather_model, market_temperature, weather_weight)
                probability_by_key[key] = probability
                selection_score = _date_equal_distribution_losses(
                    selection_rows, probability[selection_mask]
                )
                parameter_rows.append(
                    {
                        "weather_model": weather_model,
                        "market_temperature": market_temperature,
                        "weather_weight": weather_weight,
                        "selection_end": selection_end,
                        **selection_score,
                    }
                )
    selected = min(
        parameter_rows,
        key=lambda row: (
            float(row["multiclass_brier"]),
            float(row["multiclass_logloss"]),
        ),
    )
    selected_key = (
        str(selected["weather_model"]),
        float(selected["market_temperature"]),
        float(selected["weather_weight"]),
    )
    candidate = probability_by_key[selected_key]

    market_temperature_rows = []
    calibrated_market_by_temperature: dict[float, np.ndarray] = {}
    for temperature in FULL_FUSION_MARKET_TEMPERATURES:
        calibrated = full_distribution_geometric_pool(
            market,
            market,
            market_temperature=temperature,
            weather_weight=0.0,
        )
        calibrated_market_by_temperature[temperature] = calibrated
        market_temperature_rows.append(
            {
                "market_temperature": temperature,
                "selection_end": selection_end,
                **_date_equal_distribution_losses(
                    selection_rows, calibrated[selection_mask]
                ),
            }
        )
    selected_market_temperature = float(
        min(
            market_temperature_rows,
            key=lambda row: (
                float(row["multiclass_brier"]),
                float(row["multiclass_logloss"]),
            ),
        )["market_temperature"]
    )
    calibrated_market = calibrated_market_by_temperature[
        selected_market_temperature
    ]

    score_rows: list[dict[str, Any]] = []
    for split, mask, selected_rows in (
        ("parameter_selection", selection_mask, selection_rows),
        ("temporal_validation_reused", validation_mask, validation_rows),
    ):
        for model, probability in (
            ("same_checkpoint_market_full_distribution", market),
            ("selection_calibrated_market_full_distribution", calibrated_market),
            ("tokyo_v3_full_ladder_fusion", candidate),
        ):
            score_rows.append(
                {
                    "split": split,
                    "model": model,
                    **_date_equal_distribution_losses(
                        selected_rows, probability[mask]
                    ),
                }
            )

    bootstrap_rows: list[dict[str, Any]] = []
    for baseline_name, baseline in (
        ("same_checkpoint_market_full_distribution", market),
        ("selection_calibrated_market_full_distribution", calibrated_market),
    ):
        for metric in ("brier", "logloss", "rps"):
            bootstrap_rows.append(
                {
                    "split": "temporal_validation_reused",
                    "candidate": "tokyo_v3_full_ladder_fusion",
                    "baseline": baseline_name,
                    "metric": metric,
                    **_date_block_distribution_delta(
                        validation_rows,
                        candidate[validation_mask],
                        baseline[validation_mask],
                        metric=metric,
                    ),
                }
            )

    prediction_rows = []
    for row, candidate_probability, market_probability, calibrated_probability in zip(
        rows, candidate, market, calibrated_market
    ):
        output = dict(row)
        output["tokyo_v3_full_ladder_fusion_distribution_json"] = json.dumps(
            candidate_probability.tolist()
        )
        output["selection_calibrated_market_full_distribution_json"] = json.dumps(
            calibrated_probability.tolist()
        )
        output["raw_market_full_distribution_json"] = json.dumps(
            market_probability.tolist()
        )
        output["fusion_split"] = (
            "parameter_selection"
            if str(row["target_date"]) <= selection_end
            else "temporal_validation_reused"
        )
        prediction_rows.append(output)

    validation_predictions = [
        row
        for row in prediction_rows
        if row["fusion_split"] == "temporal_validation_reused"
    ]
    for row in validation_predictions:
        row["tokyo_v3_full_ladder_fusion_distribution_json"] = row.pop(
            "tokyo_v3_full_ladder_fusion_distribution_json"
        )
        row["selection_calibrated_market_distribution_json"] = row.pop(
            "selection_calibrated_market_full_distribution_json"
        )
    candidates = current_next_candidates(
        validation_predictions,
        (
            "tokyo_v3_full_ladder_fusion",
            "selection_calibrated_market",
        ),
    )
    trades = select_first_signal(
        candidates,
        raw_books,
        selection_policy="first_signal_per_model_target_date_bracket",
    )
    trade_summary = strategy_summary(
        candidates,
        trades,
        split="temporal_validation_reused",
        denominator_dates=sorted(
            {str(row["target_date"]) for row in validation_rows}
        ),
        model_names=(
            "tokyo_v3_full_ladder_fusion",
            "selection_calibrated_market",
        ),
    )

    validation_candidate = next(
        row
        for row in score_rows
        if row["split"] == "temporal_validation_reused"
        and row["model"] == "tokyo_v3_full_ladder_fusion"
    )
    validation_market = next(
        row
        for row in score_rows
        if row["split"] == "temporal_validation_reused"
        and row["model"] == "same_checkpoint_market_full_distribution"
    )
    validation_brier_delta = next(
        row
        for row in bootstrap_rows
        if row["baseline"] == "same_checkpoint_market_full_distribution"
        and row["metric"] == "brier"
    )
    spec = {
        "schema_version": "tokyo_continuous_full_probability_candidate_v2",
        "user_facing_version": "Tokyo V3",
        "human_summary": (
            "连续全概率模型：每个PIT checkpoint联合完整market ladder与天气路径，"
            "输出stay/+1/+2/+3+，不再丢弃market tail shape"
        ),
        "model_id": FULL_FUSION_MODEL_ID,
        "weather_model": selected_key[0],
        "market_temperature": selected_key[1],
        "weather_weight": selected_key[2],
        "fusion": "normalized geometric pool over all four outcomes",
        "outcomes": list(OUTCOMES),
        "selection_end": selection_end,
        "clean_forward_start": clean_forward_start,
        "clean_forward_labels_used_in_selection": False,
        "deployment_status": "research_only_not_in_runtime",
        "live_notional": 0.0,
    }
    summary = {
        "schema_version": "tokyo_continuous_full_probability_fusion_audit_v2",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_id": FULL_FUSION_MODEL_ID,
        "input_artifact": str(input_path),
        "denominator_scope": (
            "Tokyo July 16-29 settled full-ladder PIT book snapshots joined as-of "
            "the latest available JMA checkpoint; lower-bound violations excluded"
        ),
        "raw_rows": len(raw_rows),
        "usable_rows": len(rows),
        "usable_target_dates": len({str(row["target_date"]) for row in rows}),
        "selection": {
            "end": selection_end,
            "rows": len(selection_rows),
            "target_dates": len(
                {str(row["target_date"]) for row in selection_rows}
            ),
            "candidate_count_k": len(parameter_rows),
            "selected": spec,
            "selected_market_temperature": selected_market_temperature,
        },
        "validation": {
            "role": "temporal_validation_reused_not_clean_forward",
            "rows": len(validation_rows),
            "target_dates": len(
                {str(row["target_date"]) for row in validation_rows}
            ),
            "candidate_multiclass_brier": validation_candidate[
                "multiclass_brier"
            ],
            "market_multiclass_brier": validation_market[
                "multiclass_brier"
            ],
            "brier_delta_vs_market": validation_brier_delta["delta"],
            "brier_delta_ci": [
                validation_brier_delta["ci_low"],
                validation_brier_delta["ci_high"],
            ],
        },
        "signal_funnel": {
            "raw_full_ladder_states": len(raw_rows),
            "usable_settled_states": len(rows),
            "validation_current_next_candidates": len(candidates),
            "validation_first_date_bracket_signals": len(trades),
        },
        "evidence_funnel": {
            "pit_full_ladder_states": len(rows),
            "settled_states": len(rows),
            "five_share_executable_signals": sum(
                int(row.get("five_share_executable", 0)) for row in trades
            ),
            "actual_fills": 0,
        },
        "research_status": "shadow_candidate_clean_forward_required",
        "action": (
            "freeze full-ladder fusion and collect zero-notional clean forward; "
            "do not replace Tokyo V2 or change live behavior"
        ),
        "research_only_zero_notional": True,
        "live_behavior_changed": False,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    write_rows(output_dir / "parameter_selection.csv", parameter_rows)
    write_rows(output_dir / "market_temperature_selection.csv", market_temperature_rows)
    write_rows(output_dir / "scores.csv", score_rows)
    write_rows(output_dir / "bootstrap.csv", bootstrap_rows)
    write_rows(output_dir / "predictions.csv.gz", prediction_rows)
    write_rows(output_dir / "trade_candidates.csv.gz", candidates)
    write_rows(output_dir / "selected_trades.csv", trades)
    write_rows(output_dir / "trade_summary.csv", trade_summary)
    (output_dir / "frozen_candidate_spec.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def run_frozen_full_probability_forward(
    *,
    expressions_path: Path,
    db_path: Path,
    weather_artifact_path: Path,
    weather_spec_path: Path,
    frozen_candidate_spec_path: Path,
    output_dir: Path,
    raw_books: Path,
    start_date: str,
    end_date: str,
    maximum_ladder_wait_minutes: float,
) -> dict[str, Any]:
    """Score an already-frozen Tokyo V3 candidate on later exact states."""

    spec = json.loads(frozen_candidate_spec_path.read_text(encoding="utf-8"))
    if spec.get("model_id") != FULL_FUSION_MODEL_ID:
        raise ValueError("frozen candidate has the wrong model_id")
    if spec.get("weather_model") != FROZEN_FULL_FUSION_WEATHER_MODEL:
        raise ValueError("frozen candidate has the wrong weather model")
    expressions = _read_expression_feature_rows(
        expressions_path, start_date=start_date, end_date=end_date
    )
    if not expressions:
        raise RuntimeError("no strict-PIT expression feature rows in requested window")
    weather = _score_frozen_weather_distribution(
        expressions,
        artifact_path=weather_artifact_path,
        spec_path=weather_spec_path,
    )
    ladders, winners = _load_tokyo_canonical_ladders(
        db_path, start_date=start_date, end_date=end_date
    )
    joined_all = join_frozen_forward_to_canonical_ladders(
        expressions,
        weather,
        ladders,
        winners,
        maximum_ladder_wait_minutes=maximum_ladder_wait_minutes,
    )
    if not joined_all:
        raise RuntimeError("no settled causal full-ladder joins in requested window")
    lower_bound_violations = [
        row
        for row in joined_all
        if int(float(row.get("settlement_lower_bound_violation") or 0)) != 0
    ]
    joined = [
        row
        for row in joined_all
        if int(float(row.get("settlement_lower_bound_violation") or 0)) == 0
    ]
    if not joined:
        raise RuntimeError("all causal joins violate the observed settlement lower bound")

    market = np.asarray(
        [json.loads(str(row["market_distribution_json"])) for row in joined],
        dtype=float,
    )
    joined_weather = np.asarray(
        [
            json.loads(
                str(
                    row[
                        f"{FROZEN_FULL_FUSION_WEATHER_MODEL}_distribution_json"
                    ]
                )
            )
            for row in joined
        ],
        dtype=float,
    )
    candidate = full_distribution_geometric_pool(
        market,
        joined_weather,
        market_temperature=float(spec["market_temperature"]),
        weather_weight=float(spec["weather_weight"]),
    )
    calibrated_market = full_distribution_geometric_pool(
        market,
        market,
        market_temperature=float(spec["market_temperature"]),
        weather_weight=0.0,
    )
    split = "august_reused_audit_not_clean_forward"
    model_probabilities = (
        ("raw_market_full_distribution", market),
        ("selection_calibrated_market", calibrated_market),
        ("tokyo_v3_full_ladder_fusion", candidate),
    )
    score_rows = [
        {
            "split": split,
            "model": model,
            **_date_equal_distribution_losses(joined, probability),
        }
        for model, probability in model_probabilities
    ]
    bootstrap_rows = []
    for baseline_name, baseline in model_probabilities[:2]:
        for metric in ("brier", "logloss", "rps"):
            bootstrap_rows.append(
                {
                    "split": split,
                    "candidate": "tokyo_v3_full_ladder_fusion",
                    "baseline": baseline_name,
                    "metric": metric,
                    **_date_block_distribution_delta(
                        joined,
                        candidate,
                        baseline,
                        metric=metric,
                    ),
                }
            )

    prediction_rows = []
    for row, raw_probability, calibrated_probability, candidate_probability in zip(
        joined, market, calibrated_market, candidate
    ):
        output = dict(row)
        output["raw_market_full_distribution_distribution_json"] = json.dumps(
            raw_probability.tolist()
        )
        output["selection_calibrated_market_distribution_json"] = json.dumps(
            calibrated_probability.tolist()
        )
        output["tokyo_v3_full_ladder_fusion_distribution_json"] = json.dumps(
            candidate_probability.tolist()
        )
        prediction_rows.append(output)
    model_names = tuple(model for model, _ in model_probabilities)
    candidates = current_next_candidates(prediction_rows, model_names)
    trades = select_first_signal(
        candidates,
        raw_books,
        selection_policy="first_signal_per_model_target_date_bracket",
    )
    denominator_dates = sorted({str(row["target_date"]) for row in joined})
    trade_summary = strategy_summary(
        candidates,
        trades,
        split=split,
        denominator_dates=denominator_dates,
        model_names=model_names,
    )
    side_summary = strategy_side_summary(
        trades, split=split, model_names=model_names
    )
    candidate_score = next(
        row for row in score_rows if row["model"] == "tokyo_v3_full_ladder_fusion"
    )
    market_score = next(
        row for row in score_rows if row["model"] == "raw_market_full_distribution"
    )
    brier_delta = next(
        row
        for row in bootstrap_rows
        if row["baseline"] == "raw_market_full_distribution"
        and row["metric"] == "brier"
    )
    v3_trade_result = next(
        row
        for row in trade_summary
        if row["model"] == "tokyo_v3_full_ladder_fusion"
    )
    summary = {
        "schema_version": "tokyo_continuous_full_probability_august_replay_v1",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_id": FULL_FUSION_MODEL_ID,
        "evaluation_role": split,
        "denominator_scope": (
            f"Tokyo {start_date}..{end_date} strict collector-first-seen JMA feature "
            "states joined to the first canonical complete PIT full-ladder capture "
            "before the next JMA state; only canonical settled target dates scored; "
            "source/settlement lower-bound violations excluded from probability and trade metrics"
        ),
        "frozen_parameters": {
            "weather_model": spec["weather_model"],
            "market_temperature": spec["market_temperature"],
            "weather_weight": spec["weather_weight"],
            "selection_end": spec["selection_end"],
            "parameters_refit_on_august": False,
        },
        "inputs": {
            "expressions": str(expressions_path),
            "canonical_db": str(db_path.resolve()),
            "weather_artifact": str(weather_artifact_path),
            "weather_spec": str(weather_spec_path),
            "frozen_candidate_spec": str(frozen_candidate_spec_path),
        },
        "signal_funnel": {
            "strict_pit_expression_states": len(expressions),
            "strict_pit_expression_dates": len(
                {str(row["target_date"]) for row in expressions}
            ),
            "causal_settled_full_ladder_states": len(joined),
            "causal_settled_full_ladder_dates": len(denominator_dates),
            "current_next_expression_candidates": len(candidates),
            "first_date_bracket_signals": len(trades),
        },
        "evidence_funnel": {
            "canonical_complete_pit_ladders": len(ladders),
            "canonical_ladder_dates": len(
                {str(row["target_date"]) for row in ladders}
            ),
            "settlement_dates": len(winners),
            "causal_joins_before_source_settlement_check": len(joined_all),
            "settlement_lower_bound_violations_excluded": len(
                lower_bound_violations
            ),
            "settlement_lower_bound_violation_dates": sorted(
                {str(row["target_date"]) for row in lower_bound_violations}
            ),
            "five_share_executable_signals": sum(
                int(row.get("five_share_executable", 0)) for row in trades
            ),
            "actual_fills": 0,
        },
        "probability_result": {
            "v3_multiclass_brier": candidate_score["multiclass_brier"],
            "market_multiclass_brier": market_score["multiclass_brier"],
            "brier_delta_vs_market": brier_delta["delta"],
            "brier_delta_ci": [brier_delta["ci_low"], brier_delta["ci_high"]],
        },
        "v3_trade_replay": {
            key: v3_trade_result[key]
            for key in (
                "selected_signals",
                "five_share_executable",
                "wins",
                "strategy_win_rate",
                "cost_usd",
                "fee_adjusted_pnl_usd",
                "fee_adjusted_roi",
                "roi_ci_low",
                "roi_ci_high",
            )
        },
        "research_status": (
            "reused_august_probability_fail_trade_expression_positive_"
            "clean_forward_required"
        ),
        "action": (
            "retain research-only zero-notional telemetry; do not replace Tokyo V2 "
            "or change live behavior; score the frozen candidate on target dates "
            "from 2026-08-13 onward before any promotion"
        ),
        "clean_forward_status": (
            "not_clean_forward_window_precedes_2026-08-13_candidate_freeze"
        ),
        "research_only_zero_notional": True,
        "live_behavior_changed": False,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    write_rows(output_dir / "forward_rows.csv.gz", prediction_rows)
    write_rows(output_dir / "probability_scores.csv", score_rows)
    write_rows(output_dir / "probability_bootstrap.csv", bootstrap_rows)
    write_rows(output_dir / "selected_trades.csv", trades)
    write_rows(output_dir / "trade_summary.csv", trade_summary)
    write_rows(output_dir / "side_summary.csv", side_summary)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


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
    parser.add_argument("--run-id")
    parser.add_argument("--out", type=Path)
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
    parser.add_argument(
        "--full-probability-fusion-input",
        type=Path,
        help=(
            "Run only the Tokyo V3 complete-ladder fusion audit using an "
            "existing market_join_rows CSV/CSV.GZ from this runner."
        ),
    )
    parser.add_argument(
        "--fusion-selection-end",
        default="2026-07-23",
        help="Last target date allowed to select V3 fusion parameters.",
    )
    parser.add_argument(
        "--fusion-clean-forward-start",
        default="2026-08-13",
        help="First target date reserved for post-freeze clean forward.",
    )
    parser.add_argument(
        "--frozen-full-probability-forward-expressions",
        type=Path,
        help=(
            "Score a frozen Tokyo V3 candidate on strict-PIT expression feature "
            "rows joined to canonical complete ladders. No parameter refit."
        ),
    )
    parser.add_argument("--frozen-forward-db", type=Path)
    parser.add_argument("--frozen-forward-weather-artifact", type=Path)
    parser.add_argument("--frozen-forward-weather-spec", type=Path)
    parser.add_argument("--frozen-forward-candidate-spec", type=Path)
    parser.add_argument("--frozen-forward-start", default="2026-08-01")
    parser.add_argument("--frozen-forward-end", default="2026-08-11")
    parser.add_argument(
        "--frozen-forward-maximum-ladder-wait-minutes",
        type=float,
        default=10.0,
    )
    args = parser.parse_args(argv)
    args.out = prepare_new_run_output(
        resolve_run_output(
            ARTIFACT_FAMILY,
            run_id=args.run_id,
            explicit_output=args.out,
        )
    )
    if args.frozen_full_probability_forward_expressions is not None:
        required = {
            "--frozen-forward-db": args.frozen_forward_db,
            "--frozen-forward-weather-artifact": (
                args.frozen_forward_weather_artifact
            ),
            "--frozen-forward-weather-spec": args.frozen_forward_weather_spec,
            "--frozen-forward-candidate-spec": args.frozen_forward_candidate_spec,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            parser.error("missing required frozen-forward arguments: " + ", ".join(missing))
        summary = run_frozen_full_probability_forward(
            expressions_path=args.frozen_full_probability_forward_expressions,
            db_path=args.frozen_forward_db,
            weather_artifact_path=args.frozen_forward_weather_artifact,
            weather_spec_path=args.frozen_forward_weather_spec,
            frozen_candidate_spec_path=args.frozen_forward_candidate_spec,
            output_dir=args.out,
            raw_books=args.raw_books,
            start_date=args.frozen_forward_start,
            end_date=args.frozen_forward_end,
            maximum_ladder_wait_minutes=(
                args.frozen_forward_maximum_ladder_wait_minutes
            ),
        )
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    if args.full_probability_fusion_input is not None:
        summary = run_full_probability_fusion_audit(
            input_path=args.full_probability_fusion_input,
            output_dir=args.out,
            raw_books=args.raw_books,
            selection_end=args.fusion_selection_end,
            clean_forward_start=args.fusion_clean_forward_start,
        )
        print(json.dumps(summary, ensure_ascii=False))
        return 0
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
