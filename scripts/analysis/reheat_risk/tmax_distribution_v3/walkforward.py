"""Date-expanding walk-forward engine for tmax_distribution_v3 routes.

For every route and every target date, the hazard chain is trained only on
strictly earlier dates.  Fusion alpha and temporal-coherence weight are
selected per target date on inner walk-forward predictions of the trailing
training dates using date-equal logloss only (pre-registered grids).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from src.strategies.weather_edge_v1.tools.tmax_distribution_v3 import (
    ALPHA_CANDIDATES,
    BUCKETS,
    COHERENCE_CANDIDATES,
    ConstrainedMarketRecalibratorV3,
    RECALIBRATION_C_CANDIDATES,
    RECALIBRATION_ROUTES,
    ROUTE_SPECS,
    TmaxHazardChainV3,
    apply_temporal_coherence,
    date_equal_mean,
    fuse_log_linear,
    multiclass_logloss,
    route_feature_columns,
)

from .common import MIN_TRAIN_DATES_INNER, MIN_TRAIN_DATES_OUTER, INNER_SELECT_DATES, bracket_step_shift

BUCKET_INDEX = {bucket: i for i, bucket in enumerate(BUCKETS)}
MARKET_COLS = [f"market_p_{b}" for b in BUCKETS]
DEFAULT_ALPHA = 0.5  # pre-registered fallback when no inner date is available
DEFAULT_COHERENCE = 0.25
DEFAULT_RECAL_C = 0.03


def _market_probs(frame: pd.DataFrame) -> np.ndarray:
    return frame[MARKET_COLS].to_numpy(dtype=float)


def _model_probs(pred: pd.DataFrame) -> np.ndarray:
    return pred[[f"p_{b}" for b in BUCKETS]].to_numpy(dtype=float)


def fit_predict_by_date(
    hourly_labeled: pd.DataFrame,
    exec_states: pd.DataFrame,
    route: str,
    train_window_dates: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Fit per date on earlier hourly states, predict that date's exec states.

    Returns {date: {"chain", "pred" (indexed like exec subset), "exec_index"}}.
    ``train_window_dates`` switches expanding -> trailing window (robustness).
    """
    columns = route_feature_columns(route)
    out: dict[str, dict[str, Any]] = {}
    dates = sorted(hourly_labeled["target_date"].unique())
    for date in sorted(exec_states["target_date"].unique()):
        train_dates = [d for d in dates if d < date]
        if train_window_dates is not None:
            train_dates = train_dates[-train_window_dates:]
        if len(train_dates) < MIN_TRAIN_DATES_INNER:
            continue
        train = hourly_labeled[hourly_labeled["target_date"].isin(train_dates)]
        if train["actual_bucket"].nunique() < 2:
            continue
        test = exec_states[exec_states["target_date"] == date]
        if test.empty:
            continue
        chain = TmaxHazardChainV3(feature_columns=columns).fit(train)
        pred = chain.predict_five_bucket(test)
        out[date] = {
            "chain": chain,
            "pred": pred,
            "exec_index": test.index,
            "train_dates": len(train_dates),
        }
    return out


def _inner_alpha_selection(
    route: str,
    date: str,
    preds: dict[str, dict[str, Any]],
    exec_states: pd.DataFrame,
    hourly_keys: set[tuple],
) -> tuple[float, dict[float, float], int]:
    spec = ROUTE_SPECS[route]
    if spec["fusion"] == "fixed_alpha_0":
        return 0.0, {}, 0
    inner_dates = [d for d in preds if d < date][-INNER_SELECT_DATES:]
    frames = []
    for inner_date in inner_dates:
        entry = preds[inner_date]
        rows = exec_states.loc[entry["exec_index"]]
        mask = (
            rows["labeled"]
            & rows["market_score_ready"]
            & rows.apply(lambda r: (r["city"], r["target_date"], r["decision_hour_local"], r["decision_snapshot_ts_utc"]) in hourly_keys, axis=1)
        )
        if not mask.any():
            continue
        frames.append(
            (
                _model_probs(entry["pred"].loc[mask.index[mask]]),
                _market_probs(rows[mask]),
                rows.loc[mask, "actual_bucket"].map(BUCKET_INDEX).to_numpy(dtype=int),
                rows.loc[mask, "target_date"],
            )
        )
    if not frames:
        return DEFAULT_ALPHA, {}, 0
    model = np.vstack([f[0] for f in frames])
    market = np.vstack([f[1] for f in frames])
    actual = np.concatenate([f[2] for f in frames])
    dates = pd.concat([f[3] for f in frames])
    scores: dict[float, float] = {}
    for alpha in ALPHA_CANDIDATES:
        fused = fuse_log_linear(model, market, alpha)
        frame = pd.DataFrame({"target_date": dates.to_numpy(), "logloss": multiclass_logloss(fused, actual)})
        scores[alpha] = date_equal_mean(frame, "logloss")
    best = min(scores, key=lambda a: (scores[a], a))
    return best, scores, len(frames)


def _apply_coherence_sequences(
    rows: pd.DataFrame, fused: np.ndarray, h_cont: float, weight: float
) -> np.ndarray:
    """Sequentially condition each city-day decision on its previous posterior."""
    out = fused.copy()
    order = rows.reset_index(drop=True)
    for _, group in order.groupby(["city", "target_date"], sort=False):
        indices = group.sort_values("decision_snapshot_ts_utc").index.to_numpy()
        prev_posterior = None
        prev_bracket = None
        for position in indices:
            cur_bracket = str(order.at[position, "current_bracket"])
            shift = bracket_step_shift(prev_bracket, cur_bracket) if prev_bracket is not None else 0
            out[position] = apply_temporal_coherence(
                out[position], prev_posterior, shift, h_cont, weight
            )
            prev_posterior = out[position]
            prev_bracket = cur_bracket
    return out


def _inner_coherence_selection(
    route: str,
    date: str,
    preds: dict[str, dict[str, Any]],
    exec_states: pd.DataFrame,
    hourly_keys: set[tuple],
    alpha: float,
) -> float:
    if not ROUTE_SPECS[route]["coherence"]:
        return 0.0
    inner_dates = [d for d in preds if d < date][-INNER_SELECT_DATES:]
    scores: dict[float, list[pd.DataFrame]] = {w: [] for w in COHERENCE_CANDIDATES}
    for inner_date in inner_dates:
        entry = preds[inner_date]
        rows = exec_states.loc[entry["exec_index"]].reset_index(drop=True)
        fused = fuse_log_linear(_model_probs(entry["pred"]), _market_probs(rows), alpha)
        labeled_mask = (
            rows["labeled"]
            & rows["market_score_ready"]
            & rows.apply(lambda r: (r["city"], r["target_date"], r["decision_hour_local"], r["decision_snapshot_ts_utc"]) in hourly_keys, axis=1)
        ).to_numpy()
        if not labeled_mask.any():
            continue
        actual = rows.loc[labeled_mask, "actual_bucket"].map(BUCKET_INDEX).to_numpy(dtype=int)
        for weight in COHERENCE_CANDIDATES:
            coherent = _apply_coherence_sequences(rows, fused, entry["chain"].h_cont, weight)
            scores[weight].append(
                pd.DataFrame(
                    {
                        "target_date": rows.loc[labeled_mask, "target_date"].to_numpy(),
                        "logloss": multiclass_logloss(coherent[labeled_mask], actual),
                    }
                )
            )
    totals = {
        w: date_equal_mean(pd.concat(frames, ignore_index=True), "logloss") if frames else math.nan
        for w, frames in scores.items()
    }
    finite_totals = {w: s for w, s in totals.items() if math.isfinite(s)}
    if not finite_totals:
        return DEFAULT_COHERENCE
    return min(finite_totals, key=lambda w: (finite_totals[w], w))


def run_route(
    route: str,
    hourly_labeled: pd.DataFrame,
    exec_states: pd.DataFrame,
    train_window_dates: int | None = None,
) -> pd.DataFrame:
    """Produce fused + coherent five-bucket probabilities per exec state."""
    if route in RECALIBRATION_ROUTES:
        return run_market_recalibration_route(route, hourly_labeled, exec_states, train_window_dates)
    preds = fit_predict_by_date(hourly_labeled, exec_states, route, train_window_dates)
    hourly_keys = set(
        map(
            tuple,
            hourly_labeled[["city", "target_date", "decision_hour_local", "decision_snapshot_ts_utc"]].to_numpy(),
        )
    )
    frames = []
    for date, entry in sorted(preds.items()):
        if entry["train_dates"] < MIN_TRAIN_DATES_OUTER:
            continue
        alpha, _, inner_used = _inner_alpha_selection(route, date, preds, exec_states, hourly_keys)
        weight = _inner_coherence_selection(route, date, preds, exec_states, hourly_keys, alpha)
        rows = exec_states.loc[entry["exec_index"]].reset_index(drop=True)
        fused = fuse_log_linear(_model_probs(entry["pred"]), _market_probs(rows), alpha)
        coherent = _apply_coherence_sequences(rows, fused, entry["chain"].h_cont, weight)
        result = rows[
            [
                "city", "target_date", "decision_hour_local", "decision_snapshot_ts_utc",
                "actual_bucket", "labeled", "market_score_ready", "unit",
            ]
        ].copy()
        for position, bucket in enumerate(BUCKETS):
            result[f"route_p_{bucket}"] = coherent[:, position]
        for source, target in (("h0", "hazard_reach_d1"), ("h1", "hazard_reach_d2"), ("h2", "hazard_reach_tail")):
            result[target] = entry["pred"][source].to_numpy(dtype=float)
        result["hazard_tail_continue"] = float(entry["chain"].h_cont)
        result["route"] = route
        result["fusion_alpha"] = alpha
        result["coherence_weight"] = weight
        result["inner_dates_used"] = inner_used
        result["h_cont"] = entry["chain"].h_cont
        result["train_through_date"] = entry["chain"].train_through
        frames.append(result)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _select_recalibration_c(
    route: str, train: pd.DataFrame, train_dates: list[str]
) -> tuple[float, dict[float, float], int]:
    include_path = route == "market_recal_path"
    validation_dates = train_dates[-INNER_SELECT_DATES:]
    scores: dict[float, list[pd.DataFrame]] = {c: [] for c in RECALIBRATION_C_CANDIDATES}
    used_dates = 0
    for validation_date in validation_dates:
        inner_dates = [date for date in train_dates if date < validation_date]
        if len(inner_dates) < MIN_TRAIN_DATES_INNER:
            continue
        inner_train = train[train["target_date"].isin(inner_dates) & train["market_score_ready"]]
        validation = train[(train["target_date"] == validation_date) & train["market_score_ready"]]
        if validation.empty or inner_train["actual_bucket"].nunique() < 2:
            continue
        actual = validation["actual_bucket"].map(BUCKET_INDEX).to_numpy(dtype=int)
        used_dates += 1
        for c_value in RECALIBRATION_C_CANDIDATES:
            model = ConstrainedMarketRecalibratorV3(include_path=include_path, c_value=c_value).fit(inner_train)
            pred = _model_probs(model.predict_five_bucket(validation))
            scores[c_value].append(pd.DataFrame({
                "target_date": validation["target_date"].to_numpy(),
                "logloss": multiclass_logloss(pred, actual),
            }))
    totals = {
        c: date_equal_mean(pd.concat(parts, ignore_index=True), "logloss") if parts else math.nan
        for c, parts in scores.items()
    }
    finite = {c: score for c, score in totals.items() if math.isfinite(score)}
    selected = min(finite, key=lambda c: (finite[c], c)) if finite else DEFAULT_RECAL_C
    return selected, totals, used_dates


def run_market_recalibration_route(
    route: str,
    hourly_labeled: pd.DataFrame,
    exec_states: pd.DataFrame,
    train_window_dates: int | None = None,
) -> pd.DataFrame:
    """Expanding constrained market recalibration; no city/category terms."""
    include_path = route == "market_recal_path"
    frames = []
    hourly_dates = sorted(hourly_labeled["target_date"].unique())
    for date in sorted(exec_states["target_date"].unique()):
        train_dates = [candidate for candidate in hourly_dates if candidate < date]
        if train_window_dates is not None:
            train_dates = train_dates[-train_window_dates:]
        if len(train_dates) < MIN_TRAIN_DATES_OUTER:
            continue
        train = hourly_labeled[hourly_labeled["target_date"].isin(train_dates)].copy()
        train = train[train["market_score_ready"]]
        test = exec_states[(exec_states["target_date"] == date) & exec_states["market_score_ready"]].copy()
        if test.empty or train["actual_bucket"].nunique() < 2:
            continue
        c_value, _, inner_used = _select_recalibration_c(route, train, train_dates)
        model = ConstrainedMarketRecalibratorV3(include_path=include_path, c_value=c_value).fit(train)
        pred = model.predict_five_bucket(test)
        result = test[[
            "city", "target_date", "decision_hour_local", "decision_snapshot_ts_utc",
            "actual_bucket", "labeled", "market_score_ready", "unit",
        ]].copy()
        for bucket in BUCKETS:
            result[f"route_p_{bucket}"] = pred[f"p_{bucket}"].to_numpy(dtype=float)
        for source, target in (("h0", "hazard_reach_d1"), ("h1", "hazard_reach_d2"), ("h2", "hazard_reach_tail")):
            result[target] = pred[source].to_numpy(dtype=float)
        result["hazard_tail_continue"] = float(model.h_cont)
        result["route"] = route
        result["fusion_alpha"] = 0.0
        result["coherence_weight"] = 0.0
        result["calibrator_c"] = c_value
        result["inner_dates_used"] = inner_used
        result["h_cont"] = float(model.h_cont)
        result["train_through_date"] = model.train_through
        frames.append(result)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
