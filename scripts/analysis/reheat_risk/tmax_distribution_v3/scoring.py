"""Paired proper-score evaluation for tmax_distribution_v3.

Primary denominator: hourly-last, market-score-ready, settlement-labeled
states where every route produced a walk-forward prediction (paired rows).
Full-ladder complete-quote states are a secondary audit only.
"""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pandas as pd

from src.strategies.weather_edge_v1.tools.tmax_distribution_v3 import (
    BUCKETS,
    brier,
    multiclass_logloss,
)

from .common import SCOPE_RETRO, date_block_bootstrap_ci

BUCKET_INDEX = {bucket: i for i, bucket in enumerate(BUCKETS)}
KEY = ["city", "target_date", "decision_hour_local", "decision_snapshot_ts_utc"]


def paired_denominator(hourly_labeled: pd.DataFrame, route_probs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    base = hourly_labeled[hourly_labeled["market_score_ready"]].copy()
    keys = None
    for frame in route_probs.values():
        if frame.empty:
            return pd.DataFrame()
        frame_keys = set(map(tuple, frame[KEY].to_numpy()))
        keys = frame_keys if keys is None else keys & frame_keys
    base = base[base[KEY].apply(tuple, axis=1).isin(keys)]
    return base


EMPTY_SCORE_COLUMNS = KEY + ["actual_bucket", "unit", "route", "logloss", "brier", "scope"]


def score_rows(paired: pd.DataFrame, route_probs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    if paired.empty:
        return pd.DataFrame(columns=EMPTY_SCORE_COLUMNS)
    actual = paired["actual_bucket"].map(BUCKET_INDEX).to_numpy(dtype=int)
    frames = []
    market = paired[[f"market_p_{b}" for b in BUCKETS]].to_numpy(dtype=float)
    base_cols = paired[KEY + ["actual_bucket", "unit"]].reset_index(drop=True)
    market_frame = base_cols.copy()
    market_frame["route"] = "market"
    market_frame["logloss"] = multiclass_logloss(market, actual)
    market_frame["brier"] = brier(market, actual)
    frames.append(market_frame)
    for route, probs in route_probs.items():
        merged = paired[KEY + ["actual_bucket", "unit"]].merge(probs, on=KEY, how="inner", suffixes=("", "_r"))
        arr = merged[[f"route_p_{b}" for b in BUCKETS]].to_numpy(dtype=float)
        merged_actual = merged["actual_bucket"].map(BUCKET_INDEX).to_numpy(dtype=int)
        frame = merged[KEY + ["actual_bucket", "unit", "fusion_alpha", "coherence_weight"]].copy()
        frame["route"] = route
        frame["logloss"] = multiclass_logloss(arr, merged_actual)
        frame["brier"] = brier(arr, merged_actual)
        frames.append(frame)
    out = pd.concat(frames, ignore_index=True)
    out["scope"] = SCOPE_RETRO
    return out


def score_summary(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame(
            columns=["route", "rows", "dates", "logloss_date_equal", "brier_date_equal", "logloss_row_mean", "brier_row_mean"]
        )

    def _one(group: pd.DataFrame) -> pd.Series:
        daily = group.groupby("target_date")[["logloss", "brier"]].mean()
        return pd.Series(
            {
                "rows": len(group),
                "dates": group["target_date"].nunique(),
                "logloss_date_equal": daily["logloss"].mean(),
                "brier_date_equal": daily["brier"].mean(),
                "logloss_row_mean": group["logloss"].mean(),
                "brier_row_mean": group["brier"].mean(),
            }
        )

    return rows.groupby("route").apply(_one, include_groups=False).reset_index()


def ablation_deltas(rows: pd.DataFrame, pairs: list[tuple[str, str]]) -> pd.DataFrame:
    """Date-equal paired logloss deltas with date-block bootstrap CI."""
    out = []
    for challenger, baseline in pairs:
        challenger_daily = rows[rows["route"] == challenger].groupby("target_date")["logloss"].mean()
        baseline_daily = rows[rows["route"] == baseline].groupby("target_date")["logloss"].mean()
        delta = (challenger_daily - baseline_daily).dropna()
        low, high = date_block_bootstrap_ci(delta)
        out.append(
            {
                "challenger": challenger,
                "baseline": baseline,
                "dates": len(delta),
                "delta_logloss_date_equal": float(delta.mean()) if len(delta) else math.nan,
                "delta_ci_low": low,
                "delta_ci_high": high,
                "direction_stable": bool(high < 0) if math.isfinite(high) else False,
            }
        )
    return pd.DataFrame(out)


def calibration_bins(paired: pd.DataFrame, route_probs: dict[str, pd.DataFrame], n_bins: int = 10) -> pd.DataFrame:
    if paired.empty:
        return pd.DataFrame(columns=["bin", "rows", "mean_predicted", "mean_outcome", "route"])
    frames = []
    market = paired[[f"market_p_{b}" for b in BUCKETS]]
    sources = {"market": (paired, market)}
    for route, probs in route_probs.items():
        merged = paired[KEY + ["actual_bucket"]].merge(
            probs.drop(columns=["actual_bucket", "labeled", "market_score_ready", "unit"], errors="ignore"),
            on=KEY,
            how="inner",
        )
        sources[route] = (merged, merged[[f"route_p_{b}" for b in BUCKETS]])
    for route, (frame, probs) in sources.items():
        # column-major stacking keeps predicted/outcome aligned per bucket
        outcome = np.concatenate(
            [(frame["actual_bucket"] == bucket).to_numpy(dtype=float) for bucket in BUCKETS]
        )
        predicted = np.concatenate([probs.iloc[:, i].to_numpy(dtype=float) for i in range(len(BUCKETS))])
        bins = np.clip((predicted * n_bins).astype(int), 0, n_bins - 1)
        grouped = pd.DataFrame({"bin": bins, "p": predicted, "y": outcome}).groupby("bin")
        cal = grouped.agg(rows=("y", "size"), mean_predicted=("p", "mean"), mean_outcome=("y", "mean")).reset_index()
        cal["route"] = route
        frames.append(cal)
    return pd.concat(frames, ignore_index=True)


def full_ladder_secondary_audit(
    paired: pd.DataFrame, route_probs: dict[str, pd.DataFrame], primary_route: str
) -> dict[str, Any]:
    """Exact-rung audit on the complete-quote subset (secondary evidence only)."""
    if paired.empty or route_probs.get(primary_route) is None or route_probs[primary_route].empty:
        return {"rows": 0, "status": "no_paired_rows"}
    subset = paired[paired["ladder_quote_complete"] & paired["labeled"]].copy()
    if subset.empty:
        return {"rows": 0, "status": "no_complete_quote_states"}
    probs = route_probs[primary_route]
    merged = subset.merge(probs, on=KEY, how="inner", suffixes=("", "_r"))
    if merged.empty:
        return {"rows": 0, "status": "no_paired_complete_quote_states"}
    market_ll, model_ll = [], []
    for record in merged.to_dict("records"):
        book = json.loads(record["ladder_book_json"])
        mids = [row["yes_mid"] for row in book]
        winner = record["winner_rung_index"]
        if any(m is None for m in mids) or not np.isfinite(float(winner)):
            continue
        winner = int(winner)
        anchor = int(record["anchor_rung_index"])
        market_arr = np.clip(np.asarray(mids, dtype=float), 1e-9, None)
        market_arr /= market_arr.sum()
        model_bucket = [record[f"route_p_{b}"] for b in BUCKETS]
        model_arr = np.full(len(book), 1e-9)
        below_rungs = max(anchor, 0)
        if below_rungs:
            model_arr[:anchor] = model_bucket[0] / below_rungs
        model_arr[anchor] = model_bucket[1]
        model_arr[anchor + 1] = model_bucket[2]
        model_arr[anchor + 2] = model_bucket[3]
        tail_rungs = len(book) - anchor - 3
        if tail_rungs > 0:
            h = float(np.clip(record.get("h_cont", 0.3), 1e-6, 1 - 1e-6))
            masses = [model_bucket[4] * (1 - h) * h**k for k in range(tail_rungs - 1)]
            masses.append(max(0.0, model_bucket[4] - sum(masses)))
            model_arr[anchor + 3 :] = masses
        model_arr = np.clip(model_arr, 1e-9, None)
        model_arr /= model_arr.sum()
        market_ll.append(-math.log(market_arr[winner]))
        model_ll.append(-math.log(model_arr[winner]))
    return {
        "rows": len(market_ll),
        "dates": int(merged["target_date"].nunique()),
        "market_exact_rung_logloss": float(np.mean(market_ll)) if market_ll else math.nan,
        "model_exact_rung_logloss": float(np.mean(model_ll)) if model_ll else math.nan,
        "status": "saved_visible_ladder_only_not_proof_of_complete_absolute_event_ladder",
    }
