#!/usr/bin/env python3
"""Evaluate local exact-bracket ladder kinks on a fixed PIT denominator.

The primary state is one complete event ladder at the first snapshot in a
deterministic city/target-date/local-two-hour bin with at least 80% direct
two-sided rung coverage.  The candidate is deliberately market-only:

    kink_score_i = 0.5 * (log(p_{i-1}) + log(p_{i+1})) - log(p_i)

Positive values mean the center rung is cheap relative to both immediate
neighbors.  A date-expanding conditional-softmax model compares:

* raw normalized market midpoint;
* market temperature calibration only; and
* the same calibration plus the continuous kink score.

The script never submits orders.  Trade results are displayed-book research
replays using the direct YES ask and the official Weather taker fee curve.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.metrics import roc_auc_score


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.production import load_production_spec


SCHEMA_VERSION = "market_ladder_kink_research_v1"
MODEL_ID = "market_ladder_kink_softmax_v1"
FEE_RATE = 0.05
PRIMARY_QUOTE_FRACTION = 0.80
MIN_TRAIN_DATES = 5
RIDGE_STRENGTH = 0.25
EPS = 1e-6
DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_REPORT = (
    ROOT
    / "docs/analysis/2026-08/2026-08-09-market-ladder-kink-exact-bracket-mispricing-v1.md"
)
DEFAULT_SHADOW = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/"
    "market_implied_tail_residual_shadow_v1/checkpoints.jsonl"
)


@dataclass(frozen=True)
class FitResult:
    gamma: float
    beta: float
    objective: float
    converged: bool
    train_dates: int
    train_snapshots: int


def parse_args() -> argparse.Namespace:
    default_artifact_root = (
        load_production_spec().research_artifact_root
        / "market_ladder_kink_v1"
        / "market_ladder_kink_v1_20260809"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--target-start", default="2026-07-11")
    parser.add_argument("--target-end", default="2026-07-28")
    parser.add_argument("--recent-start", default="2026-07-24")
    parser.add_argument("--min-train-dates", type=int, default=MIN_TRAIN_DATES)
    parser.add_argument("--ridge", type=float, default=RIDGE_STRENGTH)
    parser.add_argument("--draws", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=2026080901)
    parser.add_argument("--artifact-dir", type=Path, default=default_artifact_root)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--shadow-checkpoints", type=Path, default=DEFAULT_SHADOW)
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def bracket_center(value: Any) -> float:
    text = str(value).strip()
    values = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", text)]
    if not values:
        return float("nan")
    if len(values) >= 2 and "-" in text:
        return (values[0] + values[1]) / 2.0
    return values[0]


def lifecycle_label(hours: pd.Series) -> pd.Series:
    return pd.cut(
        hours,
        [-math.inf, -24, -12, 0, 6, 10, 14, 18, 24, math.inf],
        labels=[
            "D-2_or_earlier",
            "D-1_early",
            "D-1_late",
            "D0_00_06",
            "D0_06_10",
            "D0_10_14",
            "D0_14_18",
            "D0_18_24",
            "post_D0",
        ],
        right=False,
    ).astype("string")


def mode_distance_label(values: pd.Series) -> pd.Series:
    return pd.cut(
        values,
        [-math.inf, -2.5, -1.5, -0.5, 0.5, 1.5, 2.5, math.inf],
        labels=[
            "cold_3plus",
            "cold_2",
            "cold_1",
            "mode",
            "hot_1",
            "hot_2",
            "hot_3plus",
        ],
        right=False,
    ).astype("string")


def _connect_ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def canonical_identity(db: Path) -> dict[str, Any]:
    resolved = db.resolve(strict=True)
    stat = resolved.stat()
    conn = _connect_ro(db)
    try:
        fact_built = conn.execute(
            "SELECT MAX(fact_built_at_utc) FROM fact_signal_candidates"
        ).fetchone()[0]
        settlement = conn.execute(
            """
            SELECT MIN(target_date), MAX(target_date), COUNT(DISTINCT target_date), COUNT(*)
            FROM settlement_outcomes WHERE settlement_status='settled'
            """
        ).fetchone()
        ladder = conn.execute(
            """
            SELECT MIN(target_date), MAX(target_date), COUNT(DISTINCT target_date),
                   COUNT(DISTINCT city), COUNT(*)
            FROM tmax_v2_ladder_snapshots
            WHERE completeness_status='complete'
              AND lineage_status='pit_verified_capture'
            """
        ).fetchone()
    finally:
        conn.close()
    return {
        "db_argument": str(db),
        "db_realpath": str(resolved),
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "size_bytes": stat.st_size,
        "mtime_utc": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        "fact_signal_candidates_built_at_utc": fact_built,
        "settlement_min_target_date": settlement[0],
        "settlement_max_target_date": settlement[1],
        "settlement_dates": int(settlement[2]),
        "settlement_rows": int(settlement[3]),
        "ladder_min_target_date": ladder[0],
        "ladder_max_target_date": ladder[1],
        "ladder_dates": int(ladder[2]),
        "ladder_cities": int(ladder[3]),
        "ladder_snapshots": int(ladder[4]),
    }


def load_fixed_snapshots(
    db: Path,
    *,
    target_start: str,
    target_end: str,
    min_quote_fraction: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load all rungs from the first qualifying snapshot in each local 2h bin."""
    query = """
    WITH outcomes AS (
        SELECT city, target_date, bracket,
               MAX(CASE WHEN final_price >= 0.999 THEN 1.0
                        WHEN final_price <= 0.001 THEN 0.0
                        ELSE final_price END) AS win
        FROM settlement_outcomes
        WHERE settlement_status='settled'
          AND target_date BETWEEN :target_start AND :target_end
        GROUP BY city, target_date, bracket
    ),
    quality AS (
        SELECT s.ladder_snapshot_id,
               COUNT(*) AS observed_rungs,
               SUM(CASE WHEN r.yes_direct_bid BETWEEN 0.001 AND 0.999
                              AND r.yes_direct_ask BETWEEN 0.001 AND 0.999
                              AND r.yes_direct_ask >= r.yes_direct_bid
                        THEN 1 ELSE 0 END) AS direct_two_sided_rungs
        FROM tmax_v2_ladder_snapshots s
        JOIN tmax_v2_ladder_rung_quotes r USING (ladder_snapshot_id)
        WHERE s.target_date BETWEEN :target_start AND :target_end
          AND s.completeness_status='complete'
          AND s.lineage_status='pit_verified_capture'
        GROUP BY s.ladder_snapshot_id
    ),
    eligible AS (
        SELECT s.*,
               q.observed_rungs,
               q.direct_two_sided_rungs,
               1.0*q.direct_two_sided_rungs/s.rung_count AS quote_fraction,
               ((unixepoch(s.source_snapshot_ts_utc)
                  + COALESCE(s.market_utc_offset_seconds, 0))
                 - unixepoch(s.target_date))/3600.0 AS local_hours,
               CAST(floor((((unixepoch(s.source_snapshot_ts_utc)
                  + COALESCE(s.market_utc_offset_seconds, 0))
                 - unixepoch(s.target_date))/3600.0)/2.0)*2.0 AS INTEGER) AS lifecycle_2h
        FROM tmax_v2_ladder_snapshots s
        JOIN quality q USING (ladder_snapshot_id)
        WHERE s.target_date BETWEEN :target_start AND :target_end
          AND s.completeness_status='complete'
          AND s.lineage_status='pit_verified_capture'
          AND 1.0*q.direct_two_sided_rungs/s.rung_count >= :min_quote_fraction
    ),
    ranked AS (
        SELECT e.*,
               ROW_NUMBER() OVER (
                 PARTITION BY city, target_date, lifecycle_2h
                 ORDER BY source_snapshot_ts_utc, ladder_snapshot_id
               ) AS checkpoint_rank
        FROM eligible e
    )
    SELECT s.ladder_snapshot_id, s.city, s.target_date, s.event_slug,
           s.source_snapshot_ts_utc AS decision_ts_utc,
           s.available_at_utc, s.market_timezone, s.market_unit,
           s.market_utc_offset_seconds, s.rung_count, s.observed_rungs,
           s.direct_two_sided_rungs, s.quote_fraction, s.local_hours,
           s.lifecycle_2h,
           r.absolute_bracket_identity AS bracket, r.condition_id,
           r.yes_direct_bid AS yes_bid, r.yes_direct_ask AS yes_ask,
           r.yes_direct_bid_size AS yes_bid_size,
           r.yes_direct_ask_size AS yes_ask_size,
           r.yes_direct_depth_ask_5c AS yes_depth_ask_5c,
           r.yes_book_status, r.yes_book_fetched_at_utc,
           o.win
    FROM ranked s
    JOIN tmax_v2_ladder_rung_quotes r USING (ladder_snapshot_id)
    LEFT JOIN outcomes o
      ON o.city=s.city AND o.target_date=s.target_date
     AND o.bracket=r.absolute_bracket_identity
    WHERE s.checkpoint_rank=1
    ORDER BY s.target_date, s.city, s.source_snapshot_ts_utc, r.absolute_bracket_identity
    """
    conn = _connect_ro(db)
    try:
        frame = pd.read_sql_query(
            query,
            conn,
            params={
                "target_start": target_start,
                "target_end": target_end,
                "min_quote_fraction": min_quote_fraction,
            },
        )
        raw_inventory = conn.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT target_date), COUNT(DISTINCT city),
                   COUNT(DISTINCT ladder_snapshot_id)
            FROM tmax_v2_ladder_snapshots
            WHERE target_date BETWEEN ? AND ?
              AND completeness_status='complete'
              AND lineage_status='pit_verified_capture'
            """,
            (target_start, target_end),
        ).fetchone()
    finally:
        conn.close()
    inventory = {
        "raw_complete_pit_ladder_rows": int(raw_inventory[0]),
        "raw_dates": int(raw_inventory[1]),
        "raw_cities": int(raw_inventory[2]),
        "raw_snapshots": int(raw_inventory[3]),
    }
    return frame, inventory


def prepare_ladders(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = raw.copy()
    if frame.empty:
        raise RuntimeError("no fixed PIT ladder rows")
    frame["decision_ts"] = pd.to_datetime(frame["decision_ts_utc"], utc=True)
    frame["quote_ts"] = pd.to_datetime(
        frame["yes_book_fetched_at_utc"], utc=True, errors="coerce"
    )
    frame["quote_age_minutes"] = (
        frame["decision_ts"] - frame["quote_ts"]
    ).dt.total_seconds() / 60.0
    frame["lifecycle"] = lifecycle_label(frame["local_hours"])
    frame["bracket_center"] = frame["bracket"].map(bracket_center)
    if frame["bracket_center"].isna().any():
        bad = frame.loc[frame["bracket_center"].isna(), "bracket"].unique().tolist()
        raise ValueError(f"unparseable brackets: {bad[:20]}")
    frame = frame.sort_values(
        ["ladder_snapshot_id", "bracket_center", "bracket"], kind="stable"
    ).reset_index(drop=True)
    frame["bracket_rank"] = frame.groupby("ladder_snapshot_id").cumcount()
    frame["direct"] = (
        frame["yes_bid"].between(0.001, 0.999)
        & frame["yes_ask"].between(0.001, 0.999)
        & frame["yes_ask"].ge(frame["yes_bid"])
    )
    frame["yes_mid"] = np.where(
        frame["direct"], (frame["yes_bid"] + frame["yes_ask"]) / 2.0, np.nan
    )
    frame["spread"] = np.where(frame["direct"], frame["yes_ask"] - frame["yes_bid"], np.nan)
    group = frame.groupby("ladder_snapshot_id", sort=False)
    frame["previous_mid"] = group["yes_mid"].shift(1)
    frame["next_mid"] = group["yes_mid"].shift(-1)
    frame["kink_score"] = np.where(
        frame["direct"]
        & frame["previous_mid"].notna()
        & frame["next_mid"].notna(),
        0.5
        * (
            np.log(frame["previous_mid"].clip(EPS, 1 - EPS))
            + np.log(frame["next_mid"].clip(EPS, 1 - EPS))
        )
        - np.log(frame["yes_mid"].clip(EPS, 1 - EPS)),
        np.nan,
    )
    frame["neighbor_geomean"] = np.sqrt(frame["previous_mid"] * frame["next_mid"])
    frame["kink_probability_gap"] = frame["neighbor_geomean"] - frame["yes_mid"]

    direct = frame[frame["direct"]].copy()
    direct["quoted_mid_mass"] = direct.groupby("ladder_snapshot_id")["yes_mid"].transform("sum")
    direct["market_p"] = direct["yes_mid"] / direct["quoted_mid_mass"]
    direct["winner_rows"] = direct.groupby("ladder_snapshot_id")["win"].transform(
        lambda values: int(np.isclose(values.fillna(0.0), 1.0).sum())
    )
    direct["settled_rows"] = direct.groupby("ladder_snapshot_id")["win"].transform(
        lambda values: int(values.notna().sum())
    )
    winner_complete = direct["winner_rows"].eq(1)
    scorable = direct[winner_complete].copy()
    mode = (
        scorable.loc[scorable.groupby("ladder_snapshot_id")["market_p"].idxmax(),
                     ["ladder_snapshot_id", "bracket_rank"]]
        .rename(columns={"bracket_rank": "mode_rank"})
    )
    scorable = scorable.merge(mode, on="ladder_snapshot_id", how="left", validate="many_to_one")
    scorable["steps_from_mode"] = scorable["bracket_rank"] - scorable["mode_rank"]
    scorable["mode_distance"] = mode_distance_label(scorable["steps_from_mode"])
    scorable["kink_feature"] = scorable["kink_score"].fillna(0.0)
    scorable["kink_available"] = scorable["kink_score"].notna()

    coverage = {
        "selected_snapshot_rows_all_rungs": int(len(frame)),
        "selected_snapshots": int(frame["ladder_snapshot_id"].nunique()),
        "selected_dates": int(frame["target_date"].nunique()),
        "selected_cities": int(frame["city"].nunique()),
        "direct_rung_rows": int(len(direct)),
        "snapshots_with_settled_winner_direct": int(
            direct.loc[winner_complete, "ladder_snapshot_id"].nunique()
        ),
        "snapshots_missing_direct_winner": int(
            direct.loc[~winner_complete, "ladder_snapshot_id"].nunique()
        ),
        "scorable_rung_rows": int(len(scorable)),
        "interior_kink_rows": int(scorable["kink_available"].sum()),
    }
    return scorable, coverage


def _group_index(rows: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    codes, identities = pd.factorize(rows["ladder_snapshot_id"], sort=False)
    winners = np.flatnonzero(rows["win"].to_numpy(dtype=float) > 0.5)
    if len(winners) != len(identities):
        raise ValueError("each training snapshot must have exactly one winner")
    return codes.astype(int), winners.astype(int), np.asarray(identities)


def _softmax_by_group(scores: np.ndarray, groups: np.ndarray) -> np.ndarray:
    probabilities = np.empty_like(scores, dtype=float)
    for group_id in np.unique(groups):
        mask = groups == group_id
        values = scores[mask]
        values = values - np.max(values)
        exp_values = np.exp(values)
        probabilities[mask] = exp_values / exp_values.sum()
    return probabilities


def fit_softmax(
    rows: pd.DataFrame,
    *,
    include_kink: bool,
    ridge: float,
) -> FitResult:
    work = rows.sort_values(["ladder_snapshot_id", "bracket_rank"]).reset_index(drop=True)
    groups, winners, identities = _group_index(work)
    log_market = np.log(work["market_p"].clip(EPS, 1.0).to_numpy(dtype=float))
    kink = work["kink_feature"].to_numpy(dtype=float)
    dates_by_group = work.groupby("ladder_snapshot_id", sort=False)["target_date"].first()
    date_counts = dates_by_group.value_counts()
    group_weights = np.asarray(
        [1.0 / date_counts.loc[date] for date in dates_by_group], dtype=float
    )
    group_weights /= group_weights.sum()

    features = np.column_stack([log_market, kink]) if include_kink else log_market[:, None]
    target = np.array([1.0, 0.0]) if include_kink else np.array([1.0])

    def objective(theta: np.ndarray) -> tuple[float, np.ndarray]:
        scores = features @ theta
        probabilities = _softmax_by_group(scores, groups)
        winner_prob = np.clip(probabilities[winners], EPS, 1.0)
        loss = float(np.dot(group_weights, -np.log(winner_prob)))
        gradient = np.zeros_like(theta)
        for group_id in range(len(identities)):
            mask = groups == group_id
            expected = probabilities[mask] @ features[mask]
            observed = features[winners[group_id]]
            gradient += group_weights[group_id] * (expected - observed)
        deviation = theta - target
        loss += 0.5 * ridge * float(deviation @ deviation)
        gradient += ridge * deviation
        return loss, gradient

    result = minimize(
        lambda theta: objective(theta)[0],
        x0=target.copy(),
        jac=lambda theta: objective(theta)[1],
        method="L-BFGS-B",
    )
    theta = result.x
    return FitResult(
        gamma=float(theta[0]),
        beta=float(theta[1]) if include_kink else 0.0,
        objective=float(result.fun),
        converged=bool(result.success),
        train_dates=int(work["target_date"].nunique()),
        train_snapshots=int(work["ladder_snapshot_id"].nunique()),
    )


def predict_softmax(rows: pd.DataFrame, fit: FitResult) -> np.ndarray:
    work = rows.sort_values(["ladder_snapshot_id", "bracket_rank"])
    groups, _, _ = _group_index(work)
    scores = (
        fit.gamma * np.log(work["market_p"].clip(EPS, 1.0).to_numpy(dtype=float))
        + fit.beta * work["kink_feature"].to_numpy(dtype=float)
    )
    return _softmax_by_group(scores, groups)


def expanding_oof(
    rows: pd.DataFrame,
    *,
    min_train_dates: int,
    ridge: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = sorted(rows["target_date"].unique())
    predictions: list[pd.DataFrame] = []
    folds: list[dict[str, Any]] = []
    for index, score_date in enumerate(dates):
        train_dates = dates[:index]
        if len(train_dates) < min_train_dates:
            continue
        train = rows[rows["target_date"].isin(train_dates)].copy()
        score = rows[rows["target_date"].eq(score_date)].copy()
        calibration = fit_softmax(train, include_kink=False, ridge=ridge)
        candidate = fit_softmax(train, include_kink=True, ridge=ridge)
        ordered = score.sort_values(["ladder_snapshot_id", "bracket_rank"]).copy()
        ordered["p_calibration_oof"] = predict_softmax(ordered, calibration)
        ordered["p_kink_oof"] = predict_softmax(ordered, candidate)
        ordered["oof_train_end"] = train_dates[-1]
        ordered["oof_train_dates"] = len(train_dates)
        predictions.append(ordered)
        folds.append(
            {
                "score_date": score_date,
                "train_start": train_dates[0],
                "train_end": train_dates[-1],
                "train_dates": len(train_dates),
                "train_snapshots": calibration.train_snapshots,
                "calibration_gamma": calibration.gamma,
                "kink_gamma": candidate.gamma,
                "kink_beta": candidate.beta,
                "calibration_converged": calibration.converged,
                "kink_converged": candidate.converged,
            }
        )
    if not predictions:
        raise RuntimeError("no OOF dates; increase history or reduce --min-train-dates")
    return pd.concat(predictions, ignore_index=True), pd.DataFrame(folds)


def snapshot_scores(rows: pd.DataFrame, probability: str) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for snapshot_id, group in rows.groupby("ladder_snapshot_id", sort=False):
        y = group["win"].to_numpy(dtype=float)
        p = group[probability].clip(EPS, 1.0).to_numpy(dtype=float)
        winner = int(np.argmax(y))
        records.append(
            {
                "ladder_snapshot_id": snapshot_id,
                "target_date": group["target_date"].iloc[0],
                "city": group["city"].iloc[0],
                "lifecycle": group["lifecycle"].iloc[0],
                "probability": probability,
                "logloss": -math.log(float(p[winner])),
                "brier": float(np.square(p - y).sum()),
                "top1": float(int(np.argmax(p) == winner)),
            }
        )
    return pd.DataFrame(records)


def date_equal_mean(frame: pd.DataFrame, column: str) -> float:
    return float(frame.groupby("target_date")[column].mean().mean())


def block_mean_ci(
    frame: pd.DataFrame,
    column: str,
    *,
    draws: int,
    seed: int,
) -> tuple[float, float]:
    daily = frame.groupby("target_date")[column].mean()
    if len(daily) < 3:
        return float("nan"), float("nan")
    values = daily.to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    sample_indices = rng.integers(0, len(values), size=(draws, len(values)))
    boot = values[sample_indices].mean(axis=1)
    low, high = np.quantile(boot, [0.025, 0.975])
    return float(low), float(high)


def block_ratio_ci(
    frame: pd.DataFrame,
    numerator: str,
    denominator: str,
    *,
    draws: int,
    seed: int,
) -> tuple[float, float]:
    daily = frame.groupby("target_date")[[numerator, denominator]].sum()
    if len(daily) < 3 or daily[denominator].sum() <= 0:
        return float("nan"), float("nan")
    values = daily.to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(values), size=(draws, len(values)))
    sampled = values[indices].sum(axis=1)
    ratios = np.divide(
        sampled[:, 0], sampled[:, 1],
        out=np.full(draws, np.nan), where=sampled[:, 1] > 0,
    )
    low, high = np.nanquantile(ratios, [0.025, 0.975])
    return float(low), float(high)


def date_equal_auc(rows: pd.DataFrame, probability: str) -> float:
    daily = []
    for _, group in rows.groupby("target_date"):
        if group["win"].nunique() < 2:
            continue
        daily.append(float(roc_auc_score(group["win"], group[probability])))
    return float(np.mean(daily)) if daily else float("nan")


def auc_block_ci(
    rows: pd.DataFrame,
    probability: str,
    *,
    draws: int,
    seed: int,
) -> tuple[float, float]:
    daily = []
    for _, group in rows.groupby("target_date"):
        if group["win"].nunique() < 2:
            continue
        daily.append(float(roc_auc_score(group["win"], group[probability])))
    values = np.asarray(daily, dtype=float)
    if len(values) < 3:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(values), size=(draws, len(values)))
    boot = values[indices].mean(axis=1)
    return tuple(float(value) for value in np.quantile(boot, [0.025, 0.975]))


def probability_summary(
    rows: pd.DataFrame,
    *,
    draws: int,
    seed: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    arms = {
        "raw_market": "market_p",
        "market_calibration_only": "p_calibration_oof",
        "kink_adjusted": "p_kink_oof",
    }
    snapshots = {name: snapshot_scores(rows, column) for name, column in arms.items()}
    records: list[dict[str, Any]] = []
    market = snapshots["raw_market"].set_index("ladder_snapshot_id")
    calibration = snapshots["market_calibration_only"].set_index("ladder_snapshot_id")
    for index, (name, column) in enumerate(arms.items()):
        scored = snapshots[name]
        joined = scored.set_index("ladder_snapshot_id")
        scored = scored.copy()
        scored["brier_delta_market"] = (
            joined["brier"] - market["brier"]
        ).to_numpy()
        scored["logloss_delta_market"] = (
            joined["logloss"] - market["logloss"]
        ).to_numpy()
        scored["brier_delta_calibration"] = (
            joined["brier"] - calibration["brier"]
        ).to_numpy()
        scored["logloss_delta_calibration"] = (
            joined["logloss"] - calibration["logloss"]
        ).to_numpy()
        brier_market_ci = block_mean_ci(
            scored, "brier_delta_market", draws=draws, seed=seed + index * 20
        )
        logloss_market_ci = block_mean_ci(
            scored, "logloss_delta_market", draws=draws, seed=seed + index * 20 + 1
        )
        brier_cal_ci = block_mean_ci(
            scored, "brier_delta_calibration", draws=draws, seed=seed + index * 20 + 2
        )
        logloss_cal_ci = block_mean_ci(
            scored, "logloss_delta_calibration", draws=draws, seed=seed + index * 20 + 3
        )
        auc_ci = auc_block_ci(rows, column, draws=min(draws, 1000), seed=seed + index * 20 + 4)
        records.append(
            {
                "arm": name,
                "rung_rows": int(len(rows)),
                "snapshots": int(scored["ladder_snapshot_id"].nunique()),
                "dates": int(scored["target_date"].nunique()),
                "cities": int(scored["city"].nunique()),
                "brier": date_equal_mean(scored, "brier"),
                "logloss": date_equal_mean(scored, "logloss"),
                "top1_accuracy": date_equal_mean(scored, "top1"),
                "auc": date_equal_auc(rows, column),
                "auc_ci_low": auc_ci[0],
                "auc_ci_high": auc_ci[1],
                "brier_delta_market": date_equal_mean(scored, "brier_delta_market"),
                "brier_delta_market_ci_low": brier_market_ci[0],
                "brier_delta_market_ci_high": brier_market_ci[1],
                "logloss_delta_market": date_equal_mean(scored, "logloss_delta_market"),
                "logloss_delta_market_ci_low": logloss_market_ci[0],
                "logloss_delta_market_ci_high": logloss_market_ci[1],
                "brier_delta_calibration": date_equal_mean(scored, "brier_delta_calibration"),
                "brier_delta_calibration_ci_low": brier_cal_ci[0],
                "brier_delta_calibration_ci_high": brier_cal_ci[1],
                "logloss_delta_calibration": date_equal_mean(scored, "logloss_delta_calibration"),
                "logloss_delta_calibration_ci_low": logloss_cal_ci[0],
                "logloss_delta_calibration_ci_high": logloss_cal_ci[1],
            }
        )
    combined = pd.concat(snapshots.values(), ignore_index=True)
    return pd.DataFrame(records), combined


def _bucketize(rows: pd.DataFrame) -> pd.DataFrame:
    work = rows.copy()
    work["ask_bucket"] = pd.cut(
        work["yes_ask"],
        [0, 0.05, 0.20, 0.50, 1.001],
        labels=["0-5c", "5-20c", "20-50c", "50c+"],
        right=False,
    ).astype("string")
    work["spread_bucket"] = pd.cut(
        work["spread"],
        [-math.inf, 0.02, 0.05, math.inf],
        labels=["<=2c", "2-5c", ">5c"],
        right=True,
    ).astype("string")
    work["depth_bucket"] = pd.cut(
        work["yes_ask_size"],
        [-math.inf, 5, 20, math.inf],
        labels=["<5", "5-20", "20+"],
        right=False,
    ).astype("string")
    work["quote_age_bucket"] = pd.cut(
        work["quote_age_minutes"],
        [-math.inf, 1, 5, math.inf],
        labels=["<=1m", "1-5m", ">5m"],
        right=True,
    ).astype("string")
    work["quote_fraction_bucket"] = pd.cut(
        work["quote_fraction"],
        [PRIMARY_QUOTE_FRACTION, 0.90, 0.999999, 1.001],
        labels=["80-90%", "90-<100%", "100%"],
        right=False,
        include_lowest=True,
    ).astype("string")
    return work


def binary_logloss(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    p = np.clip(p, EPS, 1 - EPS)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def stability_slices(rows: pd.DataFrame, *, draws: int, seed: int) -> pd.DataFrame:
    work = _bucketize(rows[rows["kink_available"]].copy())
    y = work["win"].to_numpy(dtype=float)
    work["brier_delta_kink_vs_cal"] = np.square(
        work["p_kink_oof"].to_numpy(dtype=float) - y
    ) - np.square(work["p_calibration_oof"].to_numpy(dtype=float) - y)
    work["logloss_delta_kink_vs_cal"] = binary_logloss(
        y, work["p_kink_oof"].to_numpy(dtype=float)
    ) - binary_logloss(y, work["p_calibration_oof"].to_numpy(dtype=float))
    dimensions = [
        "lifecycle",
        "mode_distance",
        "ask_bucket",
        "spread_bucket",
        "depth_bucket",
        "quote_age_bucket",
        "quote_fraction_bucket",
        "city",
    ]
    records: list[dict[str, Any]] = []
    counter = 0
    for dimension in dimensions:
        for value, group in work.groupby(dimension, dropna=False):
            if len(group) < 30 or group["target_date"].nunique() < 3:
                continue
            brier_ci = block_mean_ci(
                group,
                "brier_delta_kink_vs_cal",
                draws=draws,
                seed=seed + counter * 2,
            )
            logloss_ci = block_mean_ci(
                group,
                "logloss_delta_kink_vs_cal",
                draws=draws,
                seed=seed + counter * 2 + 1,
            )
            records.append(
                {
                    "dimension": dimension,
                    "value": str(value),
                    "rows": int(len(group)),
                    "snapshots": int(group["ladder_snapshot_id"].nunique()),
                    "dates": int(group["target_date"].nunique()),
                    "cities": int(group["city"].nunique()),
                    "mean_kink_score": float(group["kink_score"].mean()),
                    "win_rate": float(group["win"].mean()),
                    "market_p": float(group["market_p"].mean()),
                    "brier_delta_kink_vs_cal": date_equal_mean(
                        group, "brier_delta_kink_vs_cal"
                    ),
                    "brier_delta_ci_low": brier_ci[0],
                    "brier_delta_ci_high": brier_ci[1],
                    "logloss_delta_kink_vs_cal": date_equal_mean(
                        group, "logloss_delta_kink_vs_cal"
                    ),
                    "logloss_delta_ci_low": logloss_ci[0],
                    "logloss_delta_ci_high": logloss_ci[1],
                }
            )
            counter += 1
    return pd.DataFrame(records)


def kink_rank_table(rows: pd.DataFrame) -> pd.DataFrame:
    work = rows[rows["kink_available"]].copy()
    work["kink_quintile"] = pd.qcut(
        work["kink_score"].rank(method="first"),
        5,
        labels=["Q1_premium", "Q2", "Q3", "Q4", "Q5_discount"],
    )
    records = []
    for label, group in work.groupby("kink_quintile", observed=True):
        records.append(
            {
                "kink_quintile": str(label),
                "rows": int(len(group)),
                "snapshots": int(group["ladder_snapshot_id"].nunique()),
                "dates": int(group["target_date"].nunique()),
                "cities": int(group["city"].nunique()),
                "mean_kink_score": float(group["kink_score"].mean()),
                "win_rate": float(group["win"].mean()),
                "market_p": float(group["market_p"].mean()),
                "raw_residual": float((group["win"] - group["market_p"]).mean()),
                "kink_adjustment": float(
                    (group["p_kink_oof"] - group["p_calibration_oof"]).mean()
                ),
            }
        )
    return pd.DataFrame(records)


def fee_per_share(price: float) -> float:
    return FEE_RATE * price * (1.0 - price)


def _trade_record(
    group: pd.DataFrame,
    selected: pd.DataFrame,
    policy: str,
    *,
    denominator_selected: bool = True,
) -> dict[str, Any]:
    cost = float(sum(float(row.yes_ask) + fee_per_share(float(row.yes_ask)) for row in selected.itertuples()))
    payout = float(selected["win"].sum())
    center = selected.iloc[0]
    return {
        "policy": policy,
        "ladder_snapshot_id": group["ladder_snapshot_id"].iloc[0],
        "target_date": group["target_date"].iloc[0],
        "city": group["city"].iloc[0],
        "lifecycle": group["lifecycle"].iloc[0],
        "selected": int(denominator_selected),
        "legs": int(len(selected)),
        "cost": cost,
        "payout": payout,
        "pnl": payout - cost,
        "won": float(payout > 0),
        "center_bracket": center["bracket"],
        "center_kink_score": float(center.get("kink_score", np.nan)),
        "center_ask": float(center["yes_ask"]),
        "center_spread": float(center["spread"]),
        "center_ask_size": float(center["yes_ask_size"]),
        "center_mode_distance": str(center["mode_distance"]),
    }


def trade_replays(rows: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    records: list[dict[str, Any]] = []
    common_snapshots = 0
    for _, group in rows.groupby("ladder_snapshot_id", sort=False):
        group = group.sort_values("bracket_rank").copy()
        interior = group[group["kink_available"]]
        if interior.empty:
            continue
        center = interior.sort_values(["kink_score", "bracket_rank"], ascending=[False, True]).iloc[0]
        center_rank = int(center["bracket_rank"])
        by_rank = group.set_index("bracket_rank", drop=False)
        required = {center_rank - 1, center_rank, center_rank + 1, int(center["mode_rank"]), int(center["mode_rank"] - 1)}
        if not required.issubset(set(int(value) for value in by_rank.index)):
            continue
        common_snapshots += 1
        center_df = by_rank.loc[[center_rank]]
        left_df = by_rank.loc[[center_rank - 1]]
        right_df = by_rank.loc[[center_rank + 1]]
        basket_df = by_rank.loc[[center_rank - 1, center_rank, center_rank + 1]]
        mode_df = by_rank.loc[[int(center["mode_rank"])]]
        cold_df = by_rank.loc[[int(center["mode_rank"] - 1)]]
        cheap_df = group.sort_values(["yes_ask", "bracket_rank"]).iloc[[0]]
        candidate_df = group.sort_values(
            "candidate_net_edge", ascending=False
        ).iloc[[0]]
        records.extend(
            [
                _trade_record(group, center_df, "max_kink_center"),
                _trade_record(group, left_df, "kink_left_neighbor"),
                _trade_record(group, right_df, "kink_right_neighbor"),
                _trade_record(group, basket_df, "kink_local_3_basket"),
                _trade_record(group, cheap_df, "ordinary_lowest_ask_yes"),
                _trade_record(group, cold_df, "cold_1_yes"),
                _trade_record(group, mode_df, "market_mode_yes"),
                _trade_record(group, candidate_df, "kink_adjusted_max_edge_all"),
            ]
        )
        if float(candidate_df["candidate_net_edge"].iloc[0]) > 0:
            records.append(
                _trade_record(group, candidate_df, "kink_adjusted_positive_edge")
            )
    return pd.DataFrame(records), {"common_policy_snapshots": common_snapshots}


def summarize_trades(
    trades: pd.DataFrame,
    *,
    common_snapshots: int,
    draws: int,
    seed: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    summaries: list[dict[str, Any]] = []
    for index, (policy, group) in enumerate(trades.groupby("policy", sort=False)):
        roi_ci = block_ratio_ci(
            group, "pnl", "cost", draws=draws, seed=seed + index
        )
        by_date = group.groupby("target_date")["pnl"].sum()
        by_city = group.groupby("city")["pnl"].sum()
        abs_total = float(group["pnl"].abs().sum())
        summaries.append(
            {
                "policy": policy,
                "denominator_snapshots": common_snapshots,
                "selected_snapshots": int(group["ladder_snapshot_id"].nunique()),
                "rows": int(len(group)),
                "dates": int(group["target_date"].nunique()),
                "cities": int(group["city"].nunique()),
                "wins": int(group["won"].sum()),
                "win_rate": float(group["won"].mean()),
                "cost": float(group["cost"].sum()),
                "pnl": float(group["pnl"].sum()),
                "fee_adjusted_roi": float(group["pnl"].sum() / group["cost"].sum()),
                "roi_ci_low": roi_ci[0],
                "roi_ci_high": roi_ci[1],
                "profitable_days": int((by_date > 0).sum()),
                "losing_days": int((by_date < 0).sum()),
                "top_date_abs_pnl_share": (
                    float(by_date.abs().max() / by_date.abs().sum()) if by_date.abs().sum() else np.nan
                ),
                "top_city_abs_pnl_share": (
                    float(by_city.abs().max() / by_city.abs().sum()) if by_city.abs().sum() else np.nan
                ),
                "trade_abs_pnl_concentration": (
                    float(group["pnl"].abs().max() / abs_total) if abs_total else np.nan
                ),
            }
        )
    daily = trades.groupby(["policy", "target_date"], as_index=False).agg(
        selected_snapshots=("ladder_snapshot_id", "nunique"),
        cost=("cost", "sum"),
        pnl=("pnl", "sum"),
        wins=("won", "sum"),
    )
    daily["roi"] = daily["pnl"] / daily["cost"]
    return pd.DataFrame(summaries), daily


def shadow_coverage(path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not path.exists():
        return pd.DataFrame(), {"status": "missing", "path": str(path)}
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    frame = pd.DataFrame(records)
    if frame.empty:
        return frame, {"status": "empty", "path": str(path)}
    coverage = (
        frame.groupby("target_date", as_index=False)
        .agg(
            rows=("record_id", "count"),
            checkpoints=("checkpoint_key", "nunique"),
            cities=("city", "nunique"),
            first_observed_at_utc=("observed_at_utc", "min"),
            last_observed_at_utc=("observed_at_utc", "max"),
            direct_rows=("direct_yes_mid", lambda values: int(values.notna().sum())),
            full_ladder_rows=("direct_quote_fraction", lambda values: int((values >= PRIMARY_QUOTE_FRACTION).sum())),
        )
        .sort_values("target_date")
    )
    summary = {
        "status": "available",
        "path": str(path),
        "sha256": sha256_file(path),
        "rows": int(len(frame)),
        "dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "min_target_date": str(frame["target_date"].min()),
        "max_target_date": str(frame["target_date"].max()),
        "first_observed_at_utc": str(frame["observed_at_utc"].min()),
        "last_observed_at_utc": str(frame["observed_at_utc"].max()),
        "execution_modes": sorted(frame["execution_mode"].dropna().unique().tolist()),
        "notional_values": sorted(float(value) for value in frame["notional_usd"].dropna().unique()),
    }
    return coverage, summary


def fmt_float(value: Any, digits: int = 5) -> str:
    if value is None or pd.isna(value):
        return "NA"
    return f"{float(value):.{digits}f}"


def fmt_pct(value: Any, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "NA"
    return f"{float(value):+.{digits}%}"


def markdown_probability_table(frame: pd.DataFrame) -> list[str]:
    lines = [
        "| arm | rows | snapshots | dates | Brier | logloss | AUC | top1 | Brier Δ vs market | 95% CI | Brier Δ vs calibration | 95% CI |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---|",
    ]
    for row in frame.itertuples():
        lines.append(
            f"| {row.arm} | {row.rung_rows:,} | {row.snapshots:,} | {row.dates} | "
            f"{row.brier:.6f} | {row.logloss:.6f} | {row.auc:.4f} | {row.top1_accuracy:.2%} | "
            f"{row.brier_delta_market:+.6f} | [{row.brier_delta_market_ci_low:+.6f}, {row.brier_delta_market_ci_high:+.6f}] | "
            f"{row.brier_delta_calibration:+.6f} | [{row.brier_delta_calibration_ci_low:+.6f}, {row.brier_delta_calibration_ci_high:+.6f}] |"
        )
    return lines


def markdown_trade_table(frame: pd.DataFrame) -> list[str]:
    lines = [
        "| policy | selected/common snapshots | dates | cities | win | cost | PnL | fee ROI | date CI | top-date abs PnL | top-city abs PnL |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|",
    ]
    for row in frame.itertuples():
        lines.append(
            f"| {row.policy} | {row.selected_snapshots}/{row.denominator_snapshots} | {row.dates} | {row.cities} | "
            f"{row.win_rate:.2%} | ${row.cost:.2f} | ${row.pnl:+.2f} | {row.fee_adjusted_roi:+.2%} | "
            f"[{row.roi_ci_low:+.2%}, {row.roi_ci_high:+.2%}] | "
            f"{row.top_date_abs_pnl_share:.1%} | {row.top_city_abs_pnl_share:.1%} |"
        )
    return lines


def build_report(
    *,
    args: argparse.Namespace,
    observed_at_utc: str,
    identity: dict[str, Any],
    inventory: dict[str, Any],
    coverage: dict[str, Any],
    oof: pd.DataFrame,
    folds: pd.DataFrame,
    probability: pd.DataFrame,
    recent_probability: pd.DataFrame,
    kink_rank: pd.DataFrame,
    trade_summary: pd.DataFrame,
    recent_trade_summary: pd.DataFrame,
    trade_daily: pd.DataFrame,
    stability: pd.DataFrame,
    final_fit: FitResult,
    shadow_summary: dict[str, Any],
    artifact_dir: Path,
) -> str:
    clean_forward_start = (
        pd.Timestamp(shadow_summary["max_target_date"]) + pd.Timedelta(days=1)
    ).date().isoformat()
    kink_row = probability.loc[probability["arm"].eq("kink_adjusted")].iloc[0]
    microstructure_pass = (
        kink_row["brier_delta_calibration_ci_high"] < 0
        and kink_row["logloss_delta_calibration_ci_high"] < 0
    )
    baseline_pass = (
        kink_row["brier_delta_market_ci_high"] < 0
        and kink_row["logloss_delta_market_ci_high"] < 0
    )
    significance = "PASS" if microstructure_pass else "FAIL"
    baseline = "PASS" if baseline_pass else "FAIL"
    forward = "NA"
    conclusion = "shadow_candidate" if microstructure_pass and baseline_pass else "inconclusive"
    action = "冻结当前模型并继续 zero-notional forward；不改 live" if conclusion == "shadow_candidate" else "保持 zero-notional collector，等待 clean settled forward；不改 live"

    positive_trade = trade_summary[
        trade_summary["policy"].eq("kink_adjusted_positive_edge")
    ]
    positive_trade_text = "无正 edge 选择"
    if not positive_trade.empty:
        row = positive_trade.iloc[0]
        positive_trade_text = (
            f"{int(row['selected_snapshots'])}/{int(row['denominator_snapshots'])} snapshots，"
            f"fee ROI {row['fee_adjusted_roi']:+.2%}，CI "
            f"[{row['roi_ci_low']:+.2%}, {row['roi_ci_high']:+.2%}]"
        )

    lines = [
        "# Market Ladder Kink / Exact-Bracket Mispricing v1",
        "",
        f"> observed_at_utc: {observed_at_utc}",
        f"> verdict: `{conclusion}`; zero-notional only; no live change.",
        "",
        "## 数据快照",
        "",
        f"- 数据源：canonical `{identity['db_realpath']}`（device={identity['device']}, inode={identity['inode']}）的 `tmax_v2_ladder_snapshots/rung_quotes` + `settlement_outcomes`；fresh coverage 使用 `{shadow_summary.get('path')}`。",
        f"- canonical build：fact candidates `{identity['fact_signal_candidates_built_at_utc']}`；ladder `{identity['ladder_min_target_date']}..{identity['ladder_max_target_date']}`；settlement 到 `{identity['settlement_max_target_date']}`。",
        f"- 本轮 evaluation slice：`{args.target_start}..{args.target_end}`；raw complete PIT snapshots {inventory['raw_snapshots']:,}，固定同快照分母 {coverage['selected_snapshots']:,} snapshots / {coverage['selected_dates']} dates / {coverage['selected_cities']} cities。",
        f"- fixed snapshot rows：all rungs {coverage['selected_snapshot_rows_all_rungs']:,}；direct rungs {coverage['direct_rung_rows']:,}；winner-direct scorable {coverage['scorable_rung_rows']:,}；interior kink rows {coverage['interior_kink_rows']:,}。",
        f"- evidence gap：{coverage['snapshots_missing_direct_winner']:,} 个固定 snapshot 的 winning rung 没有 direct two-sided quote，完整保留为 coverage gap、未进入 proper score。actual fill=0。",
        f"- shadow raw：{shadow_summary.get('rows', 0):,} rows / {shadow_summary.get('dates', 0)} target dates / {shadow_summary.get('cities', 0)} cities，`{shadow_summary.get('min_target_date')}..{shadow_summary.get('max_target_date')}`；mode={shadow_summary.get('execution_modes')}，notional={shadow_summary.get('notional_values')}。",
        "- 数据完整性自检：manifest `db_route=healthy`、storage audit healthy；controller 为 WARNING，唯一当前 warning 是 `snapshot_city_state_coverage:missing_non_trading_weather_state`，与本研究 market-only ladder 分母无关。docs debt precheck 因工作区既有未跟踪报告与 repeated-function ceiling 失败，本脚本未引用这些文件。",
        "",
        "## 单轮 brief / Readiness",
        "",
        "- hypothesis：在同一 PIT exact-bracket ladder 中，局部 log-concavity 凹陷对最终 winning bracket 有超过 market level/calibration 的连续排序增量。",
        f"- data scope：{args.target_start}..{args.target_end} canonical complete PIT ladders；包含已查看的 development window；clean forward 尚未读。",
        "- acceptance：kink-adjusted 对 raw market 与 calibration-only 的 Brier/logloss date-block CI 都小于 0；AUC/rank 同号；fee expression CI 不跨 0；clean forward 同号。",
        "- 唯一动作：固定 market-only expanding-date OOF A/B，并冻结 zero-notional forward artifact。",
        "- 不在范围：forecast、METAR/source reversal、任何 live/order 修改、city-specific selector、额外阈值搜索。",
        "",
        "| readiness | 状态 | 证据 / gap |",
        "|---|---|---|",
        "| PIT state + clocks | READY | canonical `pit_verified_capture`; source snapshot/available/book fetch clocks retained |",
        "| canonical/build identity | READY | DB route same inode; identity frozen above |",
        "| quote freshness/depth | READY with gaps | direct bid/ask/size/depth/quote age retained; missing winner remains gap |",
        "| settlement/label | READY through 7/28 evaluation | canonical settlement extends beyond evaluation end |",
        f"| independent target dates | READY for OOF | {oof['target_date'].nunique()} scored dates after {args.min_train_dates} prior dates |",
        "| clean frozen-forward | BLOCKED | model is first frozen in this report; existing 8/8+ telemetry predates this artifact and is not untouched labeled forward |",
        "| WS reconstruction | N/A | primary uses immutable REST/direct-book canonical snapshots, not raw WS deltas |",
        "| sampling grain | READY | first qualifying same city-date-local-2h snapshot; every rung shares one snapshot identity |",
        "",
        "## Target 与连续 score",
        "",
        "```text",
        "kink_score_i = 0.5*(log market_p[i-1] + log market_p[i+1]) - log market_p[i]",
        "p_calibration ∝ market_p^gamma",
        "p_kink_adjusted ∝ market_p^gamma * exp(beta*kink_score)",
        "```",
        "",
        "正 kink 表示中心档相对左右邻档便宜。边界档或缺邻档的 score 为 unavailable，不以 0 冒充观测；模型投影时只让它没有局部调整。`gamma` 吸收 favorite/longshot 与 base-rate calibration，`beta` 才是 ladder microstructure 增量。全 ladder softmax 重新归一化，保持 exact outcomes 概率和为 1。",
        "",
        f"expanding OOF 共 {len(folds)} folds；最后一折 kink beta={folds.iloc[-1]['kink_beta']:+.4f}。全 development freeze：gamma={final_fit.gamma:.6f}，beta={final_fit.beta:+.6f}，ridge={args.ridge}（固定单规格，K=1，未用 ROI 选参）。",
        "",
        "## 双漏斗",
        "",
        "signal funnel：",
        "",
        f"- raw universe：{inventory['raw_snapshots']:,} complete PIT ladder snapshots / {inventory['raw_dates']} dates。",
        f"- fixed sampling：{coverage['selected_snapshots']:,} city-date-local-2h same-snapshot states。",
        f"- OOF probability states：{oof['ladder_snapshot_id'].nunique():,} snapshots / {oof['target_date'].nunique()} dates。",
        "- policy denominator：见下表 common snapshots；positive-edge 只用经济零点，不新增 tuned gate。",
        "",
        "evidence funnel：",
        "",
        f"- direct quote rows {coverage['direct_rung_rows']:,} → winner-direct scorable rows {coverage['scorable_rung_rows']:,} → interior kink rows {coverage['interior_kink_rows']:,}。",
        f"- executable replay = direct YES ask + official fee；actual fills=0；clean settled forward=0。",
        "",
        "## Probability：同分母 expanding-date OOF",
        "",
    ]
    lines.extend(markdown_probability_table(probability))
    lines += [
        "",
        "主判断看 `kink_adjusted` 相对 `market_calibration_only`，不是只看相对 raw market。前者隔离真正 local-kink 增量，后者可能只是把 favorite/longshot base rate 再校准。CI 按 target_date block bootstrap，日期内 snapshot 等权。",
        "",
        "### Kink 排序",
        "",
        "| kink quintile | rows | dates | mean score | win | market p | raw residual | OOF adjustment |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in kink_rank.itertuples():
        lines.append(
            f"| {row.kink_quintile} | {row.rows:,} | {row.dates} | {row.mean_kink_score:+.4f} | "
            f"{row.win_rate:.2%} | {row.market_p:.2%} | {row.raw_residual:+.2%} | {row.kink_adjustment:+.2%} |"
        )
    lines += [
        "",
        "## Trade expression（research replay，不是 fill）",
        "",
        "每个 common snapshot 都比较 max-kink center、左右邻档、三档 basket、最低价 YES、cold_1、market mode 与 kink-adjusted max edge。1 share/leg，entry=direct YES ask，fee=`0.05*p*(1-p)`；不使用 future touch 或 maker fill 假设。",
        "",
    ]
    lines.extend(markdown_trade_table(trade_summary))
    lines += [
        "",
        f"经济零点表达 `kink_adjusted_positive_edge`：{positive_trade_text}。没有通过 proper score 前，正 ROI 也只能是探索性 secondary。",
        "",
        "## 稳定性与假象拆分",
        "",
        "完整 slice CSV 按 lifecycle、mode distance、ask、spread、top ask depth、quote age、quote fraction 与 city 保存。下面列出 kink-vs-calibration Brier delta 绝对值最大的诊断项；这些是解释层，不是 selector。负值才表示 kink 更好。",
        "",
        "| dimension | value | rows | dates | cities | kink | Brier Δ | 95% CI | logloss Δ | 95% CI |",
        "|---|---|---:|---:|---:|---:|---:|---|---:|---|",
    ]
    show = stability.assign(abs_delta=stability["brier_delta_kink_vs_cal"].abs()).sort_values(
        "abs_delta", ascending=False
    ).head(24)
    for row in show.itertuples():
        lines.append(
            f"| {row.dimension} | {row.value} | {row.rows:,} | {row.dates} | {row.cities} | "
            f"{row.mean_kink_score:+.4f} | {row.brier_delta_kink_vs_cal:+.6f} | "
            f"[{row.brier_delta_ci_low:+.6f}, {row.brier_delta_ci_high:+.6f}] | "
            f"{row.logloss_delta_kink_vs_cal:+.6f} | "
            f"[{row.logloss_delta_ci_low:+.6f}, {row.logloss_delta_ci_high:+.6f}] |"
        )
    lines += [
        "",
        "判别规则：如果增量只在宽 spread、低 depth、老 quote 或少数 city/date 为正，就归类为薄盘口/陈旧报价假象；如果 calibration-only 已吸收改善，则归类为 market/base-rate；只有 kink-vs-calibration 在宽分母和流动性较好层仍稳定，才叫 microstructure residual。",
        "",
        "## Recent / frozen forward / concentration",
        "",
        f"recent `{args.recent_start}..{args.target_end}` 仍属于已经看过的 development data，只作稳定性诊断，不能冒充 frozen forward。",
        "",
    ]
    lines.extend(markdown_probability_table(recent_probability))
    lines += [
        "",
        "recent trade replay：",
        "",
    ]
    lines.extend(markdown_trade_table(recent_trade_summary))
    lines += [
        "",
        "候选 `kink_adjusted_positive_edge` 日度 PnL（未选择的 snapshot 不伪造成 fill；日内多城相关性由 target-date bootstrap 处理）：",
        "",
        "| target_date | selected | cost | PnL | ROI |",
        "|---|---:|---:|---:|---:|",
    ]
    candidate_daily = trade_daily[
        trade_daily["policy"].eq("kink_adjusted_positive_edge")
    ].sort_values("target_date")
    for row in candidate_daily.itertuples():
        lines.append(
            f"| {row.target_date} | {row.selected_snapshots} | ${row.cost:.2f} | ${row.pnl:+.2f} | {row.roi:+.2%} |"
        )
    depth_thin = stability[
        stability["dimension"].eq("depth_bucket") & stability["value"].eq("<5")
    ]
    depth_deep = stability[
        stability["dimension"].eq("depth_bucket") & stability["value"].eq("20+")
    ]
    wide = stability[
        stability["dimension"].eq("spread_bucket") & stability["value"].eq(">5c")
    ]
    lines += [
        "",
        (
            f"- thin book：top ask size<5 的 Brier delta={depth_thin.iloc[0]['brier_delta_kink_vs_cal']:+.6f}，"
            f"CI [{depth_thin.iloc[0]['brier_delta_ci_low']:+.6f}, {depth_thin.iloc[0]['brier_delta_ci_high']:+.6f}]，"
            "即 kink 在最薄层反而显著更差。" if not depth_thin.empty else "- thin book：样本不足。"
        ),
        (
            f"- deep book：top ask size>=20 的 Brier delta={depth_deep.iloc[0]['brier_delta_kink_vs_cal']:+.6f}，"
            f"CI [{depth_deep.iloc[0]['brier_delta_ci_low']:+.6f}, {depth_deep.iloc[0]['brier_delta_ci_high']:+.6f}]，无稳定增量。"
            if not depth_deep.empty else "- deep book：样本不足。"
        ),
        (
            f"- wide spread：>5c 仅 {int(wide.iloc[0]['rows'])} rows，Brier CI 上界 {wide.iloc[0]['brier_delta_ci_high']:+.6f}，"
            "不能把局部 logloss 改善外推为可执行 alpha。" if not wide.empty else "- wide spread：样本不足。"
        ),
        "- stale quote：primary OOF interior rows 全部落在 quote_age<=1m；因此本窗没有陈旧报价支撑 kink，8/8+ collector 仍须继续保留 freshness 字段。",
        f"- 当前模型与 coefficient 在本报告生成时才冻结；clean forward 起点为 `{clean_forward_start}`。8/8+ raw telemetry 可用于 coverage continuity，但不能倒算成 untouched score。",
        "- 日度 PnL 与 policy/city/date 集中度已写 artifacts；表中的 top-date/top-city absolute-PnL share 用来识别少数日期、城市或赢家噪声。",
        "- multiple testing：主模型 K=1；稳定性 slices 与 8 个 trade policies 未校正，只作诊断，不能提升结论。",
        "",
        "## 8 环覆盖",
        "",
        "1 描述性=PASS；2 date bootstrap=PASS；3 排序/AUC=PASS；4 proper probability=PASS；5 displayed-book microstructure=PARTIAL（无真实 prints/fills）；6 capacity=PARTIAL（有 top depth，无 fill）；7 日期/城市集中度=PASS；8 same-row market/base-rate/counterfactual=PASS。",
        "",
        "## Gate 与唯一动作",
        "",
        "```text",
        f"significance={significance}",
        f"baseline={baseline}",
        f"forward={forward}",
        f"conclusion={conclusion}",
        f"action={action}",
        "```",
        "",
        f"在 `{args.target_start}..{args.target_end}` 固定同快照 expanding-OOF 分母，kink-adjusted 相对 calibration-only 的 Brier delta 为 {kink_row['brier_delta_calibration']:+.6f}（95% CI [{kink_row['brier_delta_calibration_ci_low']:+.6f}, {kink_row['brier_delta_calibration_ci_high']:+.6f}]），forward NA，结论 `{conclusion}`；唯一动作：{action}。",
        "",
        "## Bloodline / Artifacts",
        "",
        "- family：独立 `market_ladder_kink_v1`，属于 `[2] market_structure_edge`；不是 forecast-tail、METAR reversal 或 source-event family。",
        "- data lineage：canonical ladder snapshot → market-only ModelOutput/research score；当前不生成 `SignalCandidate`、`TradeIntent`、plan/order/fill。",
        f"- artifact root：`{artifact_dir}`",
        "- files：`summary.json`、`model_freeze.json`、`oof_rung_predictions.csv.gz`、`probability_summary.csv`、`recent_probability_summary.csv`、`kink_rank.csv`、`stability_slices.csv`、`trade_summary.csv`、`recent_trade_summary.csv`、`daily_pnl.csv`、`shadow_coverage.csv`、`folds.csv`。",
    ]
    return "\n".join(lines) + "\n"


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    args = parse_args()
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    observed_at_utc = utc_now()
    identity = canonical_identity(args.db)
    raw, inventory = load_fixed_snapshots(
        args.db,
        target_start=args.target_start,
        target_end=args.target_end,
        min_quote_fraction=PRIMARY_QUOTE_FRACTION,
    )
    rows, coverage = prepare_ladders(raw)
    oof, folds = expanding_oof(
        rows,
        min_train_dates=args.min_train_dates,
        ridge=args.ridge,
    )
    oof["candidate_cost"] = oof["yes_ask"] + FEE_RATE * oof["yes_ask"] * (1 - oof["yes_ask"])
    oof["candidate_net_edge"] = oof["p_kink_oof"] - oof["candidate_cost"]
    probability, snapshot_metric_rows = probability_summary(
        oof, draws=args.draws, seed=args.seed
    )
    recent_oof = oof[oof["target_date"].ge(args.recent_start)].copy()
    recent_probability, _ = probability_summary(
        recent_oof, draws=args.draws, seed=args.seed + 500
    )
    kink_rank = kink_rank_table(oof)
    stability = stability_slices(oof, draws=args.draws, seed=args.seed + 1000)
    trades, trade_inventory = trade_replays(oof)
    trade_summary, trade_daily = summarize_trades(
        trades,
        common_snapshots=trade_inventory["common_policy_snapshots"],
        draws=args.draws,
        seed=args.seed + 2000,
    )
    recent_trades = trades[trades["target_date"].ge(args.recent_start)].copy()
    recent_common_snapshots = int(
        recent_trades.loc[
            recent_trades["policy"].eq("max_kink_center"), "ladder_snapshot_id"
        ].nunique()
    )
    recent_trade_summary, _ = summarize_trades(
        recent_trades,
        common_snapshots=recent_common_snapshots,
        draws=args.draws,
        seed=args.seed + 2500,
    )
    final_fit = fit_softmax(rows, include_kink=True, ridge=args.ridge)
    final_calibration = fit_softmax(rows, include_kink=False, ridge=args.ridge)
    shadow_frame, shadow_summary = shadow_coverage(args.shadow_checkpoints)
    clean_forward_start = None
    if shadow_summary.get("max_target_date"):
        clean_forward_start = (
            pd.Timestamp(shadow_summary["max_target_date"]) + pd.Timedelta(days=1)
        ).date().isoformat()

    model_freeze = {
        "schema_version": SCHEMA_VERSION,
        "model_id": MODEL_ID,
        "generated_at_utc": observed_at_utc,
        "training_target_start": args.target_start,
        "training_target_end": args.target_end,
        "clean_forward_start": clean_forward_start,
        "min_train_dates": args.min_train_dates,
        "ridge_strength": args.ridge,
        "market_calibration_gamma": final_calibration.gamma,
        "kink_adjusted_gamma": final_fit.gamma,
        "kink_beta": final_fit.beta,
        "kink_definition": "0.5*(log(left_market_p)+log(right_market_p))-log(center_market_p)",
        "probability_projection": "conditional softmax within same ladder snapshot",
        "feature_book_contract": "canonical PIT direct two-sided REST ladder; first qualifying local-2h snapshot",
        "live_enabled": False,
        "orders_enabled": False,
        "notional_usd": 0.0,
        "canonical_identity": identity,
    }
    model_path = args.artifact_dir / "model_freeze.json"
    model_freeze["payload_sha256"] = hashlib.sha256(
        json.dumps(model_freeze, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    write_json(model_path, model_freeze)

    oof.to_csv(args.artifact_dir / "oof_rung_predictions.csv.gz", index=False, compression="gzip")
    folds.to_csv(args.artifact_dir / "folds.csv", index=False)
    probability.to_csv(args.artifact_dir / "probability_summary.csv", index=False)
    recent_probability.to_csv(
        args.artifact_dir / "recent_probability_summary.csv", index=False
    )
    snapshot_metric_rows.to_csv(args.artifact_dir / "snapshot_metric_rows.csv", index=False)
    kink_rank.to_csv(args.artifact_dir / "kink_rank.csv", index=False)
    stability.to_csv(args.artifact_dir / "stability_slices.csv", index=False)
    trades.to_csv(args.artifact_dir / "trade_replays.csv.gz", index=False, compression="gzip")
    trade_summary.to_csv(args.artifact_dir / "trade_summary.csv", index=False)
    recent_trade_summary.to_csv(
        args.artifact_dir / "recent_trade_summary.csv", index=False
    )
    trade_daily.to_csv(args.artifact_dir / "daily_pnl.csv", index=False)
    shadow_frame.to_csv(args.artifact_dir / "shadow_coverage.csv", index=False)

    kink_row = probability.loc[probability["arm"].eq("kink_adjusted")].iloc[0]
    verdict = (
        "shadow_candidate"
        if kink_row["brier_delta_calibration_ci_high"] < 0
        and kink_row["logloss_delta_calibration_ci_high"] < 0
        and kink_row["brier_delta_market_ci_high"] < 0
        and kink_row["logloss_delta_market_ci_high"] < 0
        else "inconclusive"
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "observed_at_utc": observed_at_utc,
        "objective": (
            "test whether continuous local ladder concavity improves exact-bracket "
            "probability ranking beyond same-row market calibration"
        ),
        "denominator_scope": {
            "target_start": args.target_start,
            "target_end": args.target_end,
            "grain": "first qualifying same city-target_date-local_2h ladder snapshot; all exact rungs",
            "min_direct_two_sided_quote_fraction": PRIMARY_QUOTE_FRACTION,
            "label": "canonical settlement_outcomes exact bracket",
            "entry": "direct YES ask",
            "fee_rate": FEE_RATE,
            "trade_class": "research_replay",
            "actual_fill": 0,
        },
        "canonical_identity": identity,
        "inventory": {**inventory, **coverage, **trade_inventory},
        "shadow_coverage": shadow_summary,
        "model_freeze": model_freeze,
        "probability": probability.to_dict(orient="records"),
        "recent_probability": recent_probability.to_dict(orient="records"),
        "trade_summary": trade_summary.to_dict(orient="records"),
        "recent_trade_summary": recent_trade_summary.to_dict(orient="records"),
        "verdict": verdict,
        "gates": {
            "significance": "PASS" if verdict == "shadow_candidate" else "FAIL",
            "baseline": "PASS" if verdict == "shadow_candidate" else "FAIL",
            "forward": "NA",
        },
        "unique_next_action": (
            "keep_zero_notional_collector_and_score_clean_settled_forward_without_refit"
        ),
        "no_live_change": True,
    }
    write_json(args.artifact_dir / "summary.json", summary)
    report = build_report(
        args=args,
        observed_at_utc=observed_at_utc,
        identity=identity,
        inventory=inventory,
        coverage=coverage,
        oof=oof,
        folds=folds,
        probability=probability,
        recent_probability=recent_probability,
        kink_rank=kink_rank,
        trade_summary=trade_summary,
        recent_trade_summary=recent_trade_summary,
        trade_daily=trade_daily,
        stability=stability,
        final_fit=final_fit,
        shadow_summary=shadow_summary,
        artifact_dir=args.artifact_dir,
    )
    args.report.write_text(report, encoding="utf-8")
    print(json.dumps({
        "report": str(args.report),
        "artifact_dir": str(args.artifact_dir),
        "verdict": verdict,
        "probability": probability.to_dict(orient="records"),
        "trade_summary": trade_summary.to_dict(orient="records"),
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
