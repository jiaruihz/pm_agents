#!/usr/bin/env python3
"""Run the two pre-registered market-ladder-kink challengers.

The broad static v1 artifact is immutable input.  This evaluator first tests a
side-aware settlement model whose only candidate increment is continuous
``kink * cold_distance``.  If that increment fails, it tests whether the same
feature predicts executable 15/30/60 minute local-ladder convergence.

Nothing in this file submits an order or writes to the live runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.metrics import roc_auc_score


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge import research_market_ladder_kink_v1 as v1
from src.strategies.runtime.production import load_production_spec


SCHEMA_VERSION = "market_ladder_kink_challengers_v1"
RUN_ID = "market_ladder_kink_challengers_20260809"
SIDE_MODEL_ID = "market_ladder_kink_side_aware_settlement_v1"
DYNAMIC_MODEL_ID = "market_ladder_kink_dynamic_repricing_v1"
DEFAULT_V1_ARTIFACT = Path(
    "/Volumes/jrs-archive/pm_agents/research/artifact_store/market_ladder_kink_v1/"
    "market_ladder_kink_v1_20260809/model_freeze.json"
)
DEVELOPMENT_START = "2026-07-11"
DEVELOPMENT_END = "2026-07-28"
FORWARD_START = "2026-08-11"
MIN_TRAIN_DATES = 5
RIDGE = 0.25
HORIZONS = (15, 30, 60)
EXIT_CAPTURE_TOLERANCE_MINUTES = 10
FEE_RATE = 0.05
EPS = 1e-6
LIFECYCLES = (
    "D-2_or_earlier",
    "D-1_early",
    "D-1_late",
    "D0_00_06",
    "D0_06_10",
    "D0_10_14",
    "D0_14_18",
    "D0_18_24",
    "post_D0",
)


@dataclass(frozen=True)
class FittedModel:
    coefficients: tuple[float, ...]
    feature_names: tuple[str, ...]
    objective: float
    converged: bool
    train_dates: int
    train_states: int


def parse_args() -> argparse.Namespace:
    artifact_dir = (
        load_production_spec().research_artifact_root
        / "market_ladder_kink_v1"
        / RUN_ID
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "runtime/weather.db")
    parser.add_argument("--v1-artifact", type=Path, default=DEFAULT_V1_ARTIFACT)
    parser.add_argument("--artifact-dir", type=Path, default=artifact_dir)
    parser.add_argument("--draws", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=2026080902)
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def validate_v1_freeze(path: Path) -> dict[str, Any]:
    freeze = read_json(path)
    payload = dict(freeze)
    expected_hash = payload.pop("payload_sha256", None)
    actual_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    if expected_hash != actual_hash:
        raise ValueError("v1 model_freeze payload hash mismatch")
    required = {
        "training_target_end": DEVELOPMENT_END,
        "clean_forward_start": FORWARD_START,
        "live_enabled": False,
        "orders_enabled": False,
        "notional_usd": 0.0,
    }
    mismatches = {
        key: {"expected": expected, "actual": freeze.get(key)}
        for key, expected in required.items()
        if freeze.get(key) != expected
    }
    if mismatches:
        raise ValueError(f"v1 freeze contract mismatch: {mismatches}")
    return freeze


def enrich_rows(rows: pd.DataFrame) -> pd.DataFrame:
    frame = rows.copy()
    modes = (
        frame.loc[
            frame.groupby("ladder_snapshot_id")["market_p"].idxmax(),
            ["ladder_snapshot_id", "bracket_center"],
        ]
        .rename(columns={"bracket_center": "mode_center"})
    )
    frame = frame.merge(modes, on="ladder_snapshot_id", how="left", validate="many_to_one")
    frame["native_distance"] = frame["bracket_center"] - frame["mode_center"]
    frame["cold"] = frame["steps_from_mode"].lt(0).astype(float)
    frame["hot"] = frame["steps_from_mode"].gt(0).astype(float)
    frame["cold_distance"] = (-frame["steps_from_mode"]).clip(lower=0).astype(float)
    frame["hot_distance"] = frame["steps_from_mode"].clip(lower=0).astype(float)
    frame["abs_distance"] = frame["steps_from_mode"].abs().astype(float)
    frame["kink_cold_distance"] = frame["kink_feature"] * frame["cold_distance"]
    return frame


def side_feature_names(include_kink: bool) -> tuple[str, ...]:
    names = [
        "log_market_p",
        "signed_distance",
        "abs_distance",
        "cold_side",
        "hot_side",
    ]
    for lifecycle in LIFECYCLES[1:]:
        names.extend((f"{lifecycle}:cold_distance", f"{lifecycle}:hot_distance"))
    names.extend(("native_F:signed_distance", "native_F:native_distance"))
    if include_kink:
        names.append("kink_x_cold_distance")
    return tuple(names)


def side_matrix(rows: pd.DataFrame, include_kink: bool) -> np.ndarray:
    values: list[np.ndarray] = [
        np.log(rows["market_p"].clip(EPS, 1.0).to_numpy(dtype=float)),
        rows["steps_from_mode"].to_numpy(dtype=float),
        rows["abs_distance"].to_numpy(dtype=float),
        rows["cold"].to_numpy(dtype=float),
        rows["hot"].to_numpy(dtype=float),
    ]
    for lifecycle in LIFECYCLES[1:]:
        mask = rows["lifecycle"].eq(lifecycle).to_numpy(dtype=float)
        values.extend(
            (
                mask * rows["cold_distance"].to_numpy(dtype=float),
                mask * rows["hot_distance"].to_numpy(dtype=float),
            )
        )
    native_f = rows["market_unit"].fillna("unknown").eq("F").to_numpy(dtype=float)
    values.extend(
        (
            native_f * rows["steps_from_mode"].to_numpy(dtype=float),
            native_f * rows["native_distance"].to_numpy(dtype=float),
        )
    )
    if include_kink:
        values.append(rows["kink_cold_distance"].to_numpy(dtype=float))
    return np.column_stack(values)


def grouped_softmax(scores: np.ndarray, groups: np.ndarray) -> np.ndarray:
    starts = np.r_[0, np.flatnonzero(np.diff(groups)) + 1]
    maxima = np.maximum.reduceat(scores, starts)
    exponentials = np.exp(scores - maxima[groups])
    totals = np.add.reduceat(exponentials, starts)
    return exponentials / totals[groups]


def fit_settlement(rows: pd.DataFrame, include_kink: bool) -> FittedModel:
    work = rows.sort_values(["ladder_snapshot_id", "bracket_rank"]).reset_index(drop=True)
    groups, identities = pd.factorize(work["ladder_snapshot_id"], sort=False)
    winners = np.flatnonzero(work["win"].to_numpy(dtype=float) > 0.5)
    if len(winners) != len(identities):
        raise ValueError("settlement model requires exactly one winner per snapshot")
    matrix = side_matrix(work, include_kink)
    dates = work.groupby("ladder_snapshot_id", sort=False)["target_date"].first()
    counts = dates.value_counts()
    group_weights = np.asarray([1.0 / counts.loc[date] for date in dates], dtype=float)
    group_weights /= group_weights.sum()
    anchor = np.zeros(matrix.shape[1], dtype=float)
    anchor[0] = 1.0

    def objective(coefficients: np.ndarray) -> tuple[float, np.ndarray]:
        probabilities = grouped_softmax(matrix @ coefficients, groups)
        loss = float(
            np.dot(group_weights, -np.log(np.clip(probabilities[winners], EPS, 1.0)))
        )
        weighted_probability = probabilities * group_weights[groups]
        gradient = matrix.T @ weighted_probability - matrix[winners].T @ group_weights
        deviation = coefficients - anchor
        loss += 0.5 * RIDGE * float(deviation @ deviation)
        gradient += RIDGE * deviation
        return loss, gradient

    result = minimize(
        lambda coefficients: objective(coefficients)[0],
        anchor,
        jac=lambda coefficients: objective(coefficients)[1],
        method="L-BFGS-B",
    )
    return FittedModel(
        coefficients=tuple(float(value) for value in result.x),
        feature_names=side_feature_names(include_kink),
        objective=float(result.fun),
        converged=bool(result.success),
        train_dates=int(work["target_date"].nunique()),
        train_states=int(work["ladder_snapshot_id"].nunique()),
    )


def predict_settlement(rows: pd.DataFrame, model: FittedModel) -> pd.DataFrame:
    include_kink = "kink_x_cold_distance" in model.feature_names
    work = rows.sort_values(["ladder_snapshot_id", "bracket_rank"]).copy()
    groups, _ = pd.factorize(work["ladder_snapshot_id"], sort=False)
    work["prediction"] = grouped_softmax(
        side_matrix(work, include_kink) @ np.asarray(model.coefficients), groups
    )
    return work


def expanding_settlement_oof(rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    outputs: list[pd.DataFrame] = []
    folds: list[dict[str, Any]] = []
    dates = sorted(rows["target_date"].unique())
    for index, score_date in enumerate(dates):
        train_dates = dates[:index]
        if len(train_dates) < MIN_TRAIN_DATES:
            continue
        train = rows[rows["target_date"].isin(train_dates)]
        score = rows[rows["target_date"].eq(score_date)]
        baseline = fit_settlement(train, include_kink=False)
        candidate = fit_settlement(train, include_kink=True)
        base_rows = predict_settlement(score, baseline).rename(
            columns={"prediction": "p_baseline"}
        )
        candidate_rows = predict_settlement(score, candidate)[
            ["ladder_snapshot_id", "bracket_rank", "prediction"]
        ].rename(columns={"prediction": "p_candidate"})
        merged = base_rows.merge(
            candidate_rows,
            on=["ladder_snapshot_id", "bracket_rank"],
            validate="one_to_one",
        )
        outputs.append(merged)
        folds.append(
            {
                "score_date": score_date,
                "train_start": train_dates[0],
                "train_end": train_dates[-1],
                "train_dates": len(train_dates),
                "baseline_converged": baseline.converged,
                "candidate_converged": candidate.converged,
                "candidate_kink_coefficient": candidate.coefficients[-1],
            }
        )
    if not outputs:
        raise RuntimeError("no side-aware OOF folds")
    return pd.concat(outputs, ignore_index=True), pd.DataFrame(folds)


def paired_date_ci(
    daily: pd.DataFrame,
    column: str,
    *,
    draws: int,
    seed: int,
) -> tuple[float, float]:
    values = daily[column].to_numpy(dtype=float)
    if len(values) < 3:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(values), size=(draws, len(values)))
    boot = values[indices].mean(axis=1)
    low, high = np.quantile(boot, [0.025, 0.975])
    return float(low), float(high)


def date_auc(rows: pd.DataFrame, probability: str) -> float:
    values = []
    for _, group in rows.groupby("target_date"):
        if group["win"].nunique() == 2:
            values.append(roc_auc_score(group["win"], group[probability]))
    return float(np.mean(values)) if values else float("nan")


def settlement_summary(
    rows: pd.DataFrame,
    *,
    draws: int,
    seed: int,
) -> tuple[dict[str, Any], pd.DataFrame]:
    records = []
    for snapshot_id, group in rows.groupby("ladder_snapshot_id", sort=False):
        label = group["win"].to_numpy(dtype=float)
        winner = int(np.argmax(label))
        base = group["p_baseline"].to_numpy(dtype=float)
        candidate = group["p_candidate"].to_numpy(dtype=float)
        records.append(
            {
                "ladder_snapshot_id": snapshot_id,
                "target_date": group["target_date"].iloc[0],
                "city": group["city"].iloc[0],
                "baseline_brier": float(np.square(base - label).sum()),
                "candidate_brier": float(np.square(candidate - label).sum()),
                "baseline_logloss": -math.log(max(EPS, float(base[winner]))),
                "candidate_logloss": -math.log(max(EPS, float(candidate[winner]))),
            }
        )
    state_scores = pd.DataFrame(records)
    state_scores["brier_delta"] = (
        state_scores["candidate_brier"] - state_scores["baseline_brier"]
    )
    state_scores["logloss_delta"] = (
        state_scores["candidate_logloss"] - state_scores["baseline_logloss"]
    )
    daily = state_scores.groupby("target_date", as_index=False).mean(numeric_only=True)
    brier_ci = paired_date_ci(daily, "brier_delta", draws=draws, seed=seed)
    logloss_ci = paired_date_ci(daily, "logloss_delta", draws=draws, seed=seed + 1)
    return {
        "rung_rows": int(len(rows)),
        "snapshots": int(rows["ladder_snapshot_id"].nunique()),
        "dates": int(rows["target_date"].nunique()),
        "cities": int(rows["city"].nunique()),
        "baseline_brier": float(daily["baseline_brier"].mean()),
        "candidate_brier": float(daily["candidate_brier"].mean()),
        "brier_delta": float(daily["brier_delta"].mean()),
        "brier_delta_ci_low": brier_ci[0],
        "brier_delta_ci_high": brier_ci[1],
        "baseline_logloss": float(daily["baseline_logloss"].mean()),
        "candidate_logloss": float(daily["candidate_logloss"].mean()),
        "logloss_delta": float(daily["logloss_delta"].mean()),
        "logloss_delta_ci_low": logloss_ci[0],
        "logloss_delta_ci_high": logloss_ci[1],
        "baseline_auc": date_auc(rows, "p_baseline"),
        "candidate_auc": date_auc(rows, "p_candidate"),
        "pass": bool(brier_ci[1] < 0 and logloss_ci[1] < 0),
    }, daily


def connect_ro(db: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30.0)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def load_snapshot_timeline(db: Path) -> pd.DataFrame:
    connection = connect_ro(db)
    try:
        frame = pd.read_sql_query(
            """
            SELECT ladder_snapshot_id, city, target_date, event_identity,
                   source_snapshot_ts_utc
            FROM tmax_v2_ladder_snapshots
            WHERE target_date BETWEEN ? AND ?
              AND completeness_status='complete'
              AND lineage_status='pit_verified_capture'
            ORDER BY city, target_date, event_identity, source_snapshot_ts_utc,
                     ladder_snapshot_id
            """,
            connection,
            params=(DEVELOPMENT_START, DEVELOPMENT_END),
        )
    finally:
        connection.close()
    frame["snapshot_ts"] = pd.to_datetime(frame["source_snapshot_ts_utc"], utc=True)
    return frame


def future_snapshot_map(entry_rows: pd.DataFrame, timeline: pd.DataFrame) -> pd.DataFrame:
    entries = entry_rows[
        ["ladder_snapshot_id", "city", "target_date", "event_slug", "decision_ts"]
    ].drop_duplicates("ladder_snapshot_id")
    event_map = timeline[
        ["ladder_snapshot_id", "event_identity"]
    ].drop_duplicates("ladder_snapshot_id")
    entries = entries.merge(event_map, on="ladder_snapshot_id", validate="one_to_one")
    groups = {
        key: group.sort_values("snapshot_ts")
        for key, group in timeline.groupby(
            ["city", "target_date", "event_identity"], sort=False
        )
    }
    output = []
    for row in entries.itertuples(index=False):
        group = groups[(row.city, row.target_date, row.event_identity)]
        timestamps = group["snapshot_ts"].astype("int64").to_numpy()
        for horizon in HORIZONS:
            desired = row.decision_ts + pd.Timedelta(minutes=horizon)
            end = desired + pd.Timedelta(minutes=EXIT_CAPTURE_TOLERANCE_MINUTES)
            first = int(np.searchsorted(timestamps, desired.value, side="left"))
            last = int(np.searchsorted(timestamps, end.value, side="right"))
            for candidate_rank, position in enumerate(range(first, last)):
                exit_row = group.iloc[position]
                lag_minutes = (
                    exit_row["snapshot_ts"] - desired
                ).total_seconds() / 60.0
                output.append(
                    {
                        "feature_book_snapshot_id": row.ladder_snapshot_id,
                        "horizon_minutes": horizon,
                        "execution_exit_book_snapshot_id": exit_row[
                            "ladder_snapshot_id"
                        ],
                        "exit_snapshot_ts_utc": exit_row["source_snapshot_ts_utc"],
                        "exit_capture_lag_minutes": lag_minutes,
                        "exit_candidate_rank": candidate_rank,
                    }
                )
    return pd.DataFrame(output)


def load_exit_quotes(db: Path, snapshot_ids: list[str]) -> pd.DataFrame:
    frames = []
    connection = connect_ro(db)
    try:
        for start in range(0, len(snapshot_ids), 400):
            chunk = snapshot_ids[start : start + 400]
            placeholders = ",".join("?" for _ in chunk)
            frames.append(
                pd.read_sql_query(
                    f"""
                    SELECT ladder_snapshot_id AS execution_exit_book_snapshot_id,
                           condition_id, absolute_bracket_identity AS bracket,
                           yes_direct_bid AS exit_yes_bid,
                           yes_direct_ask AS exit_yes_ask,
                           yes_direct_bid_size AS exit_yes_bid_size,
                           yes_direct_ask_size AS exit_yes_ask_size,
                           yes_book_fetched_at_utc AS exit_book_fetched_at_utc
                    FROM tmax_v2_ladder_rung_quotes
                    WHERE ladder_snapshot_id IN ({placeholders})
                    """,
                    connection,
                    params=chunk,
                )
            )
    finally:
        connection.close()
    if not frames:
        return pd.DataFrame()
    frame = pd.concat(frames, ignore_index=True)
    frame["bracket_center"] = frame["bracket"].map(v1.bracket_center)
    frame = frame.sort_values(
        ["execution_exit_book_snapshot_id", "bracket_center", "bracket"],
        kind="stable",
    )
    frame["exit_direct"] = (
        frame["exit_yes_bid"].between(0.001, 0.999)
        & frame["exit_yes_ask"].between(0.001, 0.999)
        & frame["exit_yes_ask"].ge(frame["exit_yes_bid"])
    )
    frame["exit_yes_mid"] = np.where(
        frame["exit_direct"],
        (frame["exit_yes_bid"] + frame["exit_yes_ask"]) / 2.0,
        np.nan,
    )
    group = frame.groupby("execution_exit_book_snapshot_id", sort=False)
    previous_mid = group["exit_yes_mid"].shift(1)
    next_mid = group["exit_yes_mid"].shift(-1)
    frame["exit_kink_score"] = np.where(
        frame["exit_direct"] & previous_mid.notna() & next_mid.notna(),
        0.5
        * (
            np.log(previous_mid.clip(EPS, 1 - EPS))
            + np.log(next_mid.clip(EPS, 1 - EPS))
        )
        - np.log(frame["exit_yes_mid"].clip(EPS, 1 - EPS)),
        np.nan,
    )
    return frame


def build_dynamic_rows(
    entry_rows: pd.DataFrame,
    mapping: pd.DataFrame,
    exit_quotes: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    mechanism = entry_rows[
        entry_rows["kink_available"] & entry_rows["cold_distance"].gt(0)
    ].copy()
    mechanism["feature_book_snapshot_id"] = mechanism["ladder_snapshot_id"]
    mechanism["execution_entry_book_snapshot_id"] = mechanism["ladder_snapshot_id"]
    rows = mechanism.merge(mapping, on="feature_book_snapshot_id", how="left")
    rows = rows.merge(
        exit_quotes,
        on=["execution_exit_book_snapshot_id", "condition_id"],
        how="left",
        validate="many_to_one",
    )
    rows = rows.sort_values(
        [
            "feature_book_snapshot_id",
            "horizon_minutes",
            "condition_id",
            "exit_candidate_rank",
        ]
    )
    scorable_mask = rows["exit_kink_score"].notna()
    scorable = rows[scorable_mask].drop_duplicates(
        ["feature_book_snapshot_id", "horizon_minutes", "condition_id"],
        keep="first",
    ).copy()
    scorable["relative_kink_convergence"] = (
        scorable["kink_score"] - scorable["exit_kink_score"]
    )
    scorable["converged"] = scorable["relative_kink_convergence"].gt(0).astype(float)
    scorable["entry_fee"] = (
        FEE_RATE * scorable["yes_ask"] * (1.0 - scorable["yes_ask"])
    )
    scorable["exit_fee"] = (
        FEE_RATE * scorable["exit_yes_bid"] * (1.0 - scorable["exit_yes_bid"])
    )
    scorable["entry_cost"] = scorable["yes_ask"] + scorable["entry_fee"]
    scorable["exit_proceeds"] = scorable["exit_yes_bid"] - scorable["exit_fee"]
    scorable["executable_pnl"] = (
        scorable["exit_proceeds"] - scorable["entry_cost"]
    )
    scorable["execution_eligible"] = (
        scorable["yes_ask"].between(0.001, 0.999)
        & scorable["exit_yes_bid"].between(0.001, 0.999)
        & scorable["yes_ask_size"].ge(1.0)
        & scorable["exit_yes_bid_size"].ge(1.0)
    )
    coverage = {
        "entry_snapshots": int(entry_rows["ladder_snapshot_id"].nunique()),
        "mechanism_rung_rows": int(len(mechanism)),
        "mechanism_snapshots": int(mechanism["ladder_snapshot_id"].nunique()),
        "future_mapping_rows": int(len(mapping)),
        "future_candidate_snapshot_rows_by_horizon": {
            str(int(key)): int(value)
            for key, value in mapping.groupby("horizon_minutes").size().items()
        },
        "future_mapped_entry_snapshots_by_horizon": {
            str(int(key)): int(value)
            for key, value in mapping.groupby("horizon_minutes")[
                "feature_book_snapshot_id"
            ].nunique().items()
        },
        "scorable_rows_by_horizon": {
            str(int(key)): int(value)
            for key, value in scorable.groupby("horizon_minutes").size().items()
        },
        "scorable_rows": int(len(scorable)),
        "executable_rows": int(scorable["execution_eligible"].sum()),
        "actual_fills": 0,
    }
    return scorable, coverage


def dynamic_feature_names(include_kink: bool) -> tuple[str, ...]:
    names = list(side_feature_names(False))
    if include_kink:
        names.append("kink_x_cold_distance")
    return tuple(names)


def fit_binary(rows: pd.DataFrame, include_kink: bool) -> FittedModel:
    matrix = side_matrix(rows, include_kink)
    labels = rows["converged"].to_numpy(dtype=float)
    date_counts = rows["target_date"].value_counts()
    weights = np.asarray([1.0 / date_counts.loc[date] for date in rows["target_date"]])
    weights /= weights.sum()
    anchor = np.zeros(matrix.shape[1], dtype=float)

    def objective(coefficients: np.ndarray) -> tuple[float, np.ndarray]:
        logits = np.clip(matrix @ coefficients, -30, 30)
        probabilities = 1.0 / (1.0 + np.exp(-logits))
        loss = -float(
            np.dot(
                weights,
                labels * np.log(np.clip(probabilities, EPS, 1.0))
                + (1.0 - labels) * np.log(np.clip(1.0 - probabilities, EPS, 1.0)),
            )
        )
        gradient = matrix.T @ (weights * (probabilities - labels))
        loss += 0.5 * RIDGE * float(coefficients @ coefficients)
        gradient += RIDGE * coefficients
        return loss, gradient

    result = minimize(
        lambda coefficients: objective(coefficients)[0],
        anchor,
        jac=lambda coefficients: objective(coefficients)[1],
        method="L-BFGS-B",
    )
    return FittedModel(
        coefficients=tuple(float(value) for value in result.x),
        feature_names=dynamic_feature_names(include_kink),
        objective=float(result.fun),
        converged=bool(result.success),
        train_dates=int(rows["target_date"].nunique()),
        train_states=int(len(rows)),
    )


def predict_binary(rows: pd.DataFrame, model: FittedModel) -> np.ndarray:
    include_kink = "kink_x_cold_distance" in model.feature_names
    logits = np.clip(
        side_matrix(rows, include_kink) @ np.asarray(model.coefficients), -30, 30
    )
    return 1.0 / (1.0 + np.exp(-logits))


def expanding_dynamic_oof(rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    outputs = []
    folds = []
    for horizon in HORIZONS:
        horizon_rows = rows[rows["horizon_minutes"].eq(horizon)].copy()
        dates = sorted(horizon_rows["target_date"].unique())
        for index, score_date in enumerate(dates):
            train_dates = dates[:index]
            if len(train_dates) < MIN_TRAIN_DATES:
                continue
            train = horizon_rows[horizon_rows["target_date"].isin(train_dates)]
            score = horizon_rows[horizon_rows["target_date"].eq(score_date)].copy()
            baseline = fit_binary(train, include_kink=False)
            candidate = fit_binary(train, include_kink=True)
            score["p_baseline"] = predict_binary(score, baseline)
            score["p_candidate"] = predict_binary(score, candidate)
            outputs.append(score)
            folds.append(
                {
                    "horizon_minutes": horizon,
                    "score_date": score_date,
                    "train_start": train_dates[0],
                    "train_end": train_dates[-1],
                    "train_dates": len(train_dates),
                    "train_rows": len(train),
                    "baseline_converged": baseline.converged,
                    "candidate_converged": candidate.converged,
                    "candidate_kink_coefficient": candidate.coefficients[-1],
                }
            )
    if not outputs:
        raise RuntimeError("no dynamic OOF folds")
    return pd.concat(outputs, ignore_index=True), pd.DataFrame(folds)


def binary_probability_summary(
    rows: pd.DataFrame,
    *,
    draws: int,
    seed: int,
) -> pd.DataFrame:
    records = []
    for offset, horizon in enumerate(HORIZONS):
        frame = rows[rows["horizon_minutes"].eq(horizon)].copy()
        if frame.empty:
            records.append(
                {
                    "horizon_minutes": horizon,
                    "status": "coverage_gap_no_three_rung_direct_exit",
                    "rows": 0,
                    "snapshots": 0,
                    "dates": 0,
                    "cities": 0,
                    "baseline_brier": None,
                    "candidate_brier": None,
                    "brier_delta": None,
                    "brier_delta_ci_low": None,
                    "brier_delta_ci_high": None,
                    "baseline_logloss": None,
                    "candidate_logloss": None,
                    "logloss_delta": None,
                    "logloss_delta_ci_low": None,
                    "logloss_delta_ci_high": None,
                    "baseline_auc": None,
                    "candidate_auc": None,
                    "probability_pass": False,
                }
            )
            continue
        frame["baseline_brier"] = np.square(frame["p_baseline"] - frame["converged"])
        frame["candidate_brier"] = np.square(frame["p_candidate"] - frame["converged"])
        frame["baseline_logloss"] = -(
            frame["converged"] * np.log(frame["p_baseline"].clip(EPS, 1 - EPS))
            + (1 - frame["converged"])
            * np.log((1 - frame["p_baseline"]).clip(EPS, 1 - EPS))
        )
        frame["candidate_logloss"] = -(
            frame["converged"] * np.log(frame["p_candidate"].clip(EPS, 1 - EPS))
            + (1 - frame["converged"])
            * np.log((1 - frame["p_candidate"]).clip(EPS, 1 - EPS))
        )
        frame["brier_delta"] = frame["candidate_brier"] - frame["baseline_brier"]
        frame["logloss_delta"] = (
            frame["candidate_logloss"] - frame["baseline_logloss"]
        )
        daily = frame.groupby("target_date", as_index=False).mean(numeric_only=True)
        brier_ci = paired_date_ci(
            daily, "brier_delta", draws=draws, seed=seed + offset * 10
        )
        logloss_ci = paired_date_ci(
            daily, "logloss_delta", draws=draws, seed=seed + offset * 10 + 1
        )
        auc_base = []
        auc_candidate = []
        for _, group in frame.groupby("target_date"):
            if group["converged"].nunique() == 2:
                auc_base.append(roc_auc_score(group["converged"], group["p_baseline"]))
                auc_candidate.append(
                    roc_auc_score(group["converged"], group["p_candidate"])
                )
        records.append(
            {
                "horizon_minutes": horizon,
                "status": "scored",
                "rows": len(frame),
                "snapshots": frame["feature_book_snapshot_id"].nunique(),
                "dates": frame["target_date"].nunique(),
                "cities": frame["city"].nunique(),
                "baseline_brier": daily["baseline_brier"].mean(),
                "candidate_brier": daily["candidate_brier"].mean(),
                "brier_delta": daily["brier_delta"].mean(),
                "brier_delta_ci_low": brier_ci[0],
                "brier_delta_ci_high": brier_ci[1],
                "baseline_logloss": daily["baseline_logloss"].mean(),
                "candidate_logloss": daily["candidate_logloss"].mean(),
                "logloss_delta": daily["logloss_delta"].mean(),
                "logloss_delta_ci_low": logloss_ci[0],
                "logloss_delta_ci_high": logloss_ci[1],
                "baseline_auc": float(np.mean(auc_base)) if auc_base else None,
                "candidate_auc": (
                    float(np.mean(auc_candidate)) if auc_candidate else None
                ),
                "probability_pass": bool(brier_ci[1] < 0 and logloss_ci[1] < 0),
            }
        )
    return pd.DataFrame(records)


def ratio_ci(
    daily: pd.DataFrame,
    *,
    draws: int,
    seed: int,
) -> tuple[float, float]:
    values = daily[["pnl", "cost"]].to_numpy(dtype=float)
    if len(values) < 3:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(values), size=(draws, len(values)))
    sampled = values[indices].sum(axis=1)
    ratios = sampled[:, 0] / sampled[:, 1]
    return tuple(float(value) for value in np.quantile(ratios, [0.025, 0.975]))


def select_policy(rows: pd.DataFrame, probability: str, policy: str) -> pd.DataFrame:
    eligible = rows[rows["execution_eligible"]].copy()
    selected = eligible.loc[
        eligible.groupby(
            ["feature_book_snapshot_id", "horizon_minutes"], sort=False
        )[probability].idxmax()
    ].copy()
    selected["policy"] = policy
    return selected


def trade_summaries(
    rows: pd.DataFrame,
    *,
    draws: int,
    seed: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    trades = pd.concat(
        (
            select_policy(rows, "p_baseline", "side_baseline"),
            select_policy(rows, "p_candidate", "kink_candidate"),
        ),
        ignore_index=True,
    )
    summaries = []
    daily_outputs = []
    for policy_index, ((horizon, policy), frame) in enumerate(
        trades.groupby(["horizon_minutes", "policy"], sort=True)
    ):
        daily = (
            frame.groupby("target_date")
            .agg(pnl=("executable_pnl", "sum"), cost=("entry_cost", "sum"))
            .reset_index()
        )
        daily["roi"] = daily["pnl"] / daily["cost"]
        daily["horizon_minutes"] = horizon
        daily["policy"] = policy
        daily_outputs.append(daily)
        interval = ratio_ci(daily, draws=draws, seed=seed + policy_index)
        date_abs = frame.groupby("target_date")["executable_pnl"].sum().abs()
        city_abs = frame.groupby("city")["executable_pnl"].sum().abs()
        summaries.append(
            {
                "horizon_minutes": int(horizon),
                "policy": policy,
                "trades": int(len(frame)),
                "dates": int(frame["target_date"].nunique()),
                "cities": int(frame["city"].nunique()),
                "cost": float(frame["entry_cost"].sum()),
                "pnl": float(frame["executable_pnl"].sum()),
                "fee_adjusted_roi": float(
                    frame["executable_pnl"].sum() / frame["entry_cost"].sum()
                ),
                "roi_ci_low": interval[0],
                "roi_ci_high": interval[1],
                "profitable_days": int((daily["pnl"] > 0).sum()),
                "losing_days": int((daily["pnl"] < 0).sum()),
                "top_date_abs_pnl_share": float(date_abs.max() / date_abs.sum()),
                "top_city_abs_pnl_share": float(city_abs.max() / city_abs.sum()),
            }
        )
    summary = pd.DataFrame(summaries)
    daily = pd.concat(daily_outputs, ignore_index=True)
    return summary, daily, trades


def delta_trade_summary(
    trades: pd.DataFrame,
    *,
    draws: int,
    seed: int,
) -> pd.DataFrame:
    paired = trades.pivot_table(
        index=["feature_book_snapshot_id", "horizon_minutes", "target_date"],
        columns="policy",
        values="executable_pnl",
        aggfunc="first",
    ).dropna()
    paired["pnl_delta"] = paired["kink_candidate"] - paired["side_baseline"]
    output = []
    for offset, (horizon, frame) in enumerate(paired.groupby(level="horizon_minutes")):
        daily = frame.reset_index().groupby("target_date", as_index=False)["pnl_delta"].sum()
        interval = paired_date_ci(
            daily, "pnl_delta", draws=draws, seed=seed + offset
        )
        output.append(
            {
                "horizon_minutes": int(horizon),
                "paired_snapshots": int(len(frame)),
                "dates": int(len(daily)),
                "candidate_minus_baseline_pnl": float(frame["pnl_delta"].sum()),
                "daily_pnl_delta_mean": float(daily["pnl_delta"].mean()),
                "daily_pnl_delta_ci_low": interval[0],
                "daily_pnl_delta_ci_high": interval[1],
            }
        )
    return pd.DataFrame(output)


def verdict(
    settlement: dict[str, Any],
    dynamic_probability: pd.DataFrame,
    trade_summary: pd.DataFrame,
) -> dict[str, Any]:
    dynamic_passes = []
    failure_by_horizon: dict[str, str | None] = {}
    for row in dynamic_probability.itertuples():
        if not row.rows:
            dynamic_passes.append(False)
            failure_by_horizon[str(row.horizon_minutes)] = "execution_coverage"
            continue
        matching_trade = trade_summary[
            trade_summary["horizon_minutes"].eq(row.horizon_minutes)
            & trade_summary["policy"].eq("kink_candidate")
        ]
        if matching_trade.empty:
            dynamic_passes.append(False)
            failure_by_horizon[str(row.horizon_minutes)] = "execution_coverage"
            continue
        trade = matching_trade.iloc[0]
        passed = bool(
            row.probability_pass
            and trade.roi_ci_low > 0
            and trade.top_date_abs_pnl_share <= 0.30
            and trade.top_city_abs_pnl_share <= 0.30
        )
        dynamic_passes.append(passed)
        if passed:
            failure_by_horizon[str(row.horizon_minutes)] = None
        elif not row.probability_pass:
            failure_by_horizon[str(row.horizon_minutes)] = "probability_and_baseline"
        else:
            failure_by_horizon[str(row.horizon_minutes)] = "execution"
    return {
        "side_aware_settlement": {
            "pass": bool(settlement["pass"]),
            "failure_layer": None if settlement["pass"] else "probability_and_baseline",
        },
        "dynamic_repricing": {
            "pass_by_horizon": {
                str(horizon): passed
                for horizon, passed in zip(HORIZONS, dynamic_passes, strict=True)
            },
            "pass": bool(any(dynamic_passes)),
            "failure_by_horizon": failure_by_horizon,
        },
        "untouched_forward": {
            "start": FORWARD_START,
            "status": "NA_no_canonical_ladder_rows_as_of_run",
            "used_for_tuning": False,
        },
        "final": (
            "HISTORICAL_PASS_FORWARD_REQUIRED"
            if settlement["pass"] or any(dynamic_passes)
            else "BRANCH_EXHAUSTED"
        ),
    }


def main() -> int:
    args = parse_args()
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    observed_at = utc_now()
    v1_freeze = validate_v1_freeze(args.v1_artifact)
    canonical = v1.canonical_identity(args.db)
    if canonical["ladder_max_target_date"] >= FORWARD_START:
        raise RuntimeError(
            "untouched forward rows are now present; this development-only evaluator must not consume them"
        )

    raw, raw_inventory = v1.load_fixed_snapshots(
        args.db,
        target_start=DEVELOPMENT_START,
        target_end=DEVELOPMENT_END,
        min_quote_fraction=v1.PRIMARY_QUOTE_FRACTION,
    )
    prepared, coverage = v1.prepare_ladders(raw)
    rows = enrich_rows(prepared)

    settlement_oof, settlement_folds = expanding_settlement_oof(rows)
    settlement_result, settlement_daily = settlement_summary(
        settlement_oof, draws=args.draws, seed=args.seed
    )
    final_side_baseline = fit_settlement(rows, include_kink=False)
    final_side_candidate = fit_settlement(rows, include_kink=True)

    timeline = load_snapshot_timeline(args.db)
    future_map = future_snapshot_map(rows, timeline)
    exit_quotes = load_exit_quotes(
        args.db,
        sorted(future_map["execution_exit_book_snapshot_id"].unique()),
    )
    dynamic_rows, dynamic_coverage = build_dynamic_rows(rows, future_map, exit_quotes)
    dynamic_oof, dynamic_folds = expanding_dynamic_oof(dynamic_rows)
    dynamic_probability = binary_probability_summary(
        dynamic_oof, draws=args.draws, seed=args.seed + 100
    )
    trade_summary, daily_pnl, trades = trade_summaries(
        dynamic_oof, draws=args.draws, seed=args.seed + 200
    )
    trade_delta = delta_trade_summary(
        trades, draws=args.draws, seed=args.seed + 300
    )
    final_verdict = verdict(settlement_result, dynamic_probability, trade_summary)

    settlement_oof.to_csv(
        args.artifact_dir / "side_aware_settlement_oof.csv.gz", index=False
    )
    settlement_folds.to_csv(args.artifact_dir / "side_aware_folds.csv", index=False)
    settlement_daily.to_csv(args.artifact_dir / "side_aware_daily_scores.csv", index=False)
    dynamic_oof.to_csv(args.artifact_dir / "dynamic_repricing_oof.csv.gz", index=False)
    dynamic_folds.to_csv(args.artifact_dir / "dynamic_repricing_folds.csv", index=False)
    dynamic_probability.to_csv(
        args.artifact_dir / "dynamic_probability_summary.csv", index=False
    )
    trades.to_csv(args.artifact_dir / "dynamic_trade_replays.csv.gz", index=False)
    trade_summary.to_csv(args.artifact_dir / "dynamic_trade_summary.csv", index=False)
    trade_delta.to_csv(args.artifact_dir / "dynamic_trade_delta.csv", index=False)
    daily_pnl.to_csv(args.artifact_dir / "dynamic_daily_pnl.csv", index=False)
    future_map.to_csv(args.artifact_dir / "future_snapshot_map.csv.gz", index=False)

    model_manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": RUN_ID,
        "generated_at_utc": observed_at,
        "development_window": [DEVELOPMENT_START, DEVELOPMENT_END],
        "untouched_forward_start": FORWARD_START,
        "v1_freeze_path": str(args.v1_artifact),
        "v1_freeze_payload_sha256": v1_freeze["payload_sha256"],
        "side_model_id": SIDE_MODEL_ID,
        "dynamic_model_id": DYNAMIC_MODEL_ID,
        "baseline_features": list(side_feature_names(False)),
        "candidate_only_feature": "kink_x_cold_distance",
        "ridge": RIDGE,
        "min_train_dates": MIN_TRAIN_DATES,
        "horizons_minutes": list(HORIZONS),
        "exit_capture_contract": (
            "first PIT snapshot at or after TTL (at most 10 minutes late) where "
            "the target rung and both neighbors have direct two-sided quotes"
        ),
        "execution_contract": (
            "one share; current direct YES ask entry; future direct YES bid exit; "
            "entry and exit size >=1; official fee on both legs; no maker/future-touch fill"
        ),
        "feature_book_snapshot_id": "entry ladder_snapshot_id",
        "execution_entry_book_snapshot_id": "entry ladder_snapshot_id",
        "execution_exit_book_snapshot_id": "first valid post-TTL ladder_snapshot_id",
        "notional_usd": 0.0,
        "orders_enabled": False,
        "fills_expected": 0,
        "production_deployable": False,
        "verdict": final_verdict,
    }
    write_json(args.artifact_dir / "model_run_manifest.json", model_manifest)

    summary = {
        "schema_version": SCHEMA_VERSION,
        "run_id": RUN_ID,
        "observed_at_utc": observed_at,
        "canonical_identity": canonical,
        "raw_inventory": raw_inventory,
        "settlement_coverage": coverage,
        "side_aware_settlement": settlement_result,
        "side_final_baseline_model": asdict(final_side_baseline),
        "side_final_candidate_model": asdict(final_side_candidate),
        "dynamic_coverage": dynamic_coverage,
        "dynamic_probability": json.loads(
            dynamic_probability.to_json(orient="records")
        ),
        "dynamic_trade_summary": trade_summary.to_dict(orient="records"),
        "dynamic_trade_delta": trade_delta.to_dict(orient="records"),
        "verdict": final_verdict,
        "no_live_change": True,
        "orders": 0,
        "fills": 0,
    }
    write_json(args.artifact_dir / "summary.json", summary)
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
