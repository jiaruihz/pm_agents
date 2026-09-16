#!/usr/bin/env python3
"""Tokyo current-NO market-offset residual with a frozen collector holdout.

Historical CLOB prices are midpoint-like proxies observed after the weather
checkpoint's assumed availability time.  They train only the fixed-offset
probability correction; they are never treated as bid/ask, depth, or fills.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Sequence

import joblib
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge import (  # noqa: E402
    research_tokyo_continuous_ladder_probability_v1 as v1,
)
from scripts.analysis.market_structure_edge import (  # noqa: E402
    research_tokyo_continuous_ladder_probability_v2 as v2,
)
from scripts.analysis.market_structure_edge import (  # noqa: E402
    research_tokyo_continuous_ladder_forward_v3 as v3,
)


FEATURE_ROWS = (
    ROOT / "docs/analysis/2026-07/generated/tokyo_continuous_ladder_probability_v1"
    / "continuous_feature_rows.csv.gz"
)
PROXY_ROWS = (
    ROOT / "docs/analysis/2026-07/generated/tokyo_clob_price_history_coverage_v8"
    / "historical_checkpoint_price_proxy.csv.gz"
)
HOLDOUT_ROWS = (
    ROOT / "docs/analysis/2026-07/generated/tokyo_current_break_binary_v5"
    / "market_join_rows.csv.gz"
)
CONFIRMATION_ROWS = (
    ROOT / "docs/analysis/2026-07/generated/tokyo_jma_multivariate_path_v1"
    / "predictions.csv.gz"
)
DEFAULT_OUT = ROOT / "docs/analysis/2026-08/generated/tokyo_overshoot_market_residual_v2"
VALIDATION_END = "2026-07-15"
FROZEN_START = "2026-07-16"
FROZEN_END = "2026-07-30"
EPS = 1e-6
LOGIT_FLOOR = 0.005
EDGE_THRESHOLD = 0.02
SHARES = 5.0
BOOTSTRAP_DRAWS = 5000
MIN_OOF_TRAIN_DATES = 30
OOF_BLOCKS = 5

FEATURE_FAMILIES: dict[str, tuple[str, ...]] = {
    "compact": (
        "distance_to_next_jma_lattice_c",
        "jma_temp_slope_60m_cph",
        "minutes_since_jma_strict_high",
        "remaining_to_18h",
    ),
    "stacked_confirmation": (
        "distance_to_next_jma_lattice_c",
        "jma_temp_slope_60m_cph",
        "minutes_since_jma_strict_high",
        "remaining_to_18h",
        "q_confirm_30m_logit",
        "phase_t13",
        "phase_t3",
    ),
}
RIDGES = (20.0, 100.0, 500.0, 2000.0)


def logit(value: float) -> float:
    clipped = min(max(float(value), LOGIT_FLOOR), 1.0 - LOGIT_FLOOR)
    return math.log(clipped / (1.0 - clipped))


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def date_weights(frame: pd.DataFrame) -> np.ndarray:
    counts = frame.groupby("target_date")["target_date"].transform("size")
    weights = 1.0 / counts.to_numpy(dtype=float)
    return weights * len(weights) / weights.sum()


def fit_offset(
    frame: pd.DataFrame,
    features: Sequence[str],
    ridge: float,
    *,
    offset_column: str = "market_no_logit",
) -> dict[str, Any]:
    raw = frame[list(features)].to_numpy(dtype=float)
    median = np.nanmedian(raw, axis=0)
    median = np.where(np.isfinite(median), median, 0.0)
    filled = np.where(np.isfinite(raw), raw, median)
    mean = filled.mean(axis=0)
    scale = filled.std(axis=0)
    scale = np.where(scale > 0, scale, 1.0)
    matrix = np.column_stack([np.ones(len(frame)), (filled - mean) / scale])
    labels = frame["y_no"].to_numpy(dtype=float)
    offset = frame[offset_column].to_numpy(dtype=float)
    weights = date_weights(frame)

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        probability = np.clip(expit(offset + matrix @ beta), EPS, 1 - EPS)
        loss = np.average(
            -labels * np.log(probability) - (1 - labels) * np.log(1 - probability),
            weights=weights,
        )
        penalty = float(ridge) * float(beta @ beta) / len(frame)
        gradient = matrix.T @ (weights * (probability - labels)) / weights.sum()
        gradient += 2.0 * float(ridge) * beta / len(frame)
        return float(loss + penalty), gradient

    result = minimize(
        lambda beta: objective(beta),
        np.zeros(matrix.shape[1]),
        method="L-BFGS-B",
        jac=True,
    )
    if not result.success:
        raise RuntimeError(result.message)
    return {
        "features": list(features),
        "ridge": float(ridge),
        "median": median.tolist(),
        "mean": mean.tolist(),
        "scale": scale.tolist(),
        "beta": result.x.tolist(),
        "market_logit_fixed_offset": True,
        "offset_column": offset_column,
        "train_rows": int(len(frame)),
        "train_dates": sorted(frame["target_date"].astype(str).unique().tolist()),
    }


def predict_offset(artifact: dict[str, Any], frame: pd.DataFrame) -> np.ndarray:
    raw = frame[list(artifact["features"])].to_numpy(dtype=float)
    median = np.asarray(artifact["median"], dtype=float)
    filled = np.where(np.isfinite(raw), raw, median)
    matrix = (filled - np.asarray(artifact["mean"])) / np.asarray(artifact["scale"])
    matrix = np.column_stack([np.ones(len(frame)), matrix])
    return expit(
        frame[str(artifact.get("offset_column", "market_no_logit"))].to_numpy(dtype=float)
        + matrix @ np.asarray(artifact["beta"], dtype=float)
    )


def fit_market_calibrator(frame: pd.DataFrame) -> dict[str, Any]:
    """Fit an intercept/slope correction using only prior target dates."""
    labels = frame["y_no"].to_numpy(dtype=float)
    raw_logit = frame["market_no_logit"].to_numpy(dtype=float)
    matrix = np.column_stack([np.ones(len(frame)), raw_logit])
    weights = date_weights(frame)

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        probability = np.clip(expit(matrix @ beta), EPS, 1 - EPS)
        loss = np.average(
            -labels * np.log(probability) - (1 - labels) * np.log(1 - probability),
            weights=weights,
        )
        gradient = matrix.T @ (weights * (probability - labels)) / weights.sum()
        return float(loss), gradient

    result = minimize(
        lambda beta: objective(beta),
        np.asarray([0.0, 1.0]),
        method="L-BFGS-B",
        jac=True,
    )
    if not result.success:
        raise RuntimeError(result.message)
    return {
        "intercept": float(result.x[0]),
        "slope": float(result.x[1]),
        "train_rows": int(len(frame)),
        "train_dates": sorted(frame["target_date"].astype(str).unique().tolist()),
    }


def predict_market_calibrator(artifact: dict[str, Any], frame: pd.DataFrame) -> np.ndarray:
    return expit(
        float(artifact["intercept"])
        + float(artifact["slope"]) * frame["market_no_logit"].to_numpy(dtype=float)
    )


def score(frame: pd.DataFrame, probability: np.ndarray) -> dict[str, Any]:
    scored = frame[["target_date", "y_no"]].copy()
    scored["p"] = np.clip(probability, EPS, 1 - EPS)
    scored["brier"] = (scored["p"] - scored["y_no"]) ** 2
    scored["logloss"] = -(
        scored["y_no"] * np.log(scored["p"])
        + (1 - scored["y_no"]) * np.log(1 - scored["p"])
    )
    daily = scored.groupby("target_date", sort=True)
    return {
        "rows": int(len(scored)),
        "target_dates": int(scored["target_date"].nunique()),
        "brier": float(daily["brier"].mean().mean()),
        "logloss": float(daily["logloss"].mean().mean()),
        "accuracy": float(((scored["p"] >= 0.5) == scored["y_no"].astype(bool)).mean()),
        "date_equal_predicted_rate": float(daily["p"].mean().mean()),
        "date_equal_actual_rate": float(daily["y_no"].mean().mean()),
    }


def bootstrap_delta(
    frame: pd.DataFrame, candidate: np.ndarray, baseline: np.ndarray, metric: str
) -> dict[str, float]:
    labels = frame["y_no"].to_numpy(dtype=float)
    candidate = np.clip(candidate, EPS, 1 - EPS)
    baseline = np.clip(baseline, EPS, 1 - EPS)
    if metric == "brier":
        delta = (candidate - labels) ** 2 - (baseline - labels) ** 2
    else:
        delta = (
            -labels * np.log(candidate) - (1 - labels) * np.log(1 - candidate)
            + labels * np.log(baseline) + (1 - labels) * np.log(1 - baseline)
        )
    work = pd.DataFrame({"target_date": frame["target_date"].astype(str), "delta": delta})
    blocks = work.groupby("target_date")["delta"].mean().to_numpy()
    rng = np.random.default_rng(20260802)
    draws = np.asarray([
        rng.choice(blocks, size=len(blocks), replace=True).mean()
        for _ in range(BOOTSTRAP_DRAWS)
    ])
    return {
        "delta": float(blocks.mean()),
        "ci_low": float(np.quantile(draws, 0.025)),
        "ci_high": float(np.quantile(draws, 0.975)),
    }


def canonical_timestamp(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, utc=True).dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def load_confirmation(path: Path) -> pd.DataFrame:
    rows = pd.read_csv(path)
    selected = rows[
        (rows["target_id"] == "next_routine_metar_confirms_jma_lattice_30m")
        & (rows["model_id"] == "jma_metar_hgb_v1")
    ][["target_date", "decision_ts_utc", "p_model", "label", "split"]].copy()
    selected["target_date"] = selected["target_date"].astype(str)
    selected["decision_ts_utc"] = canonical_timestamp(selected["decision_ts_utc"])
    return selected.rename(columns={
        "p_model": "q_confirm_30m",
        "label": "q_confirm_30m_label",
        "split": "q_confirm_split",
    })


def load_features(
    path: Path,
    confirmation_path: Path = CONFIRMATION_ROWS,
) -> pd.DataFrame:
    raw = v1.read_rows(path)
    continuous = v2.annotate_grains(v1.build_continuous_rows(raw))
    frame = pd.DataFrame(continuous)
    frame["target_date"] = frame["target_date"].astype(str)
    frame["decision_ts_utc"] = canonical_timestamp(frame["decision_ts_utc"])
    frame["current_bracket"] = frame["current_bracket"].astype(int)
    frame = frame.merge(
        load_confirmation(confirmation_path),
        on=["target_date", "decision_ts_utc"],
        how="left",
        validate="many_to_one",
    )
    clipped = frame["q_confirm_30m"].clip(0.01, 0.99)
    frame["q_confirm_30m_logit"] = np.log(clipped / (1.0 - clipped))
    minute = pd.to_datetime(frame["decision_ts_utc"], utc=True).dt.minute
    frame["phase_t13"] = minute.isin([10, 40]).astype(int)
    frame["phase_t3"] = minute.isin([20, 50]).astype(int)
    frame["archive_current_cross_proxy"] = (
        frame["jma_current_minus_current_bracket"].astype(float) >= 0.7
    ).astype(int)
    return frame


def historical_frame(features: pd.DataFrame, path: Path) -> pd.DataFrame:
    proxy = pd.read_csv(path)
    proxy["target_date"] = proxy["target_date"].astype(str)
    point_ts = pd.to_datetime(proxy["after_point_ts"], unit="s", utc=True)
    available_ts = pd.to_datetime(
        proxy["assumed_availability_ts_utc"], utc=True
    )
    proxy = proxy[
        proxy["after_price"].notna()
        & proxy["after_delay_min"].between(0.0, 15.0, inclusive="both")
        & (point_ts >= available_ts)
        & (proxy["target_date"] <= VALIDATION_END)
    ].copy()
    proxy["decision_ts_utc"] = canonical_timestamp(proxy["decision_ts_utc"])
    proxy["current_bracket"] = proxy["current_bracket"].astype(int)
    joined = proxy.merge(
        features,
        on=["target_date", "decision_ts_utc", "current_bracket"],
        how="inner",
        validate="one_to_one",
    )
    joined["market_p_no"] = 1.0 - joined["after_price"].astype(float)
    joined["market_no_logit"] = joined["market_p_no"].map(logit)
    joined["y_no"] = joined["binary_leave_current"].astype(int)
    return joined


def frozen_frame(features: pd.DataFrame, path: Path) -> pd.DataFrame:
    books = pd.read_csv(path)
    books = books[
        books["target_date"].astype(str).between(FROZEN_START, FROZEN_END)
        & (books["settlement_lower_bound_violation"].astype(int) == 0)
    ].copy()
    books["target_date"] = books["target_date"].astype(str)
    books["decision_ts_utc"] = canonical_timestamp(books["decision_ts_utc"])
    books["current_bracket"] = books["current_bracket"].astype(int)
    joined = books.merge(
        features,
        on=["target_date", "decision_ts_utc", "current_bracket"],
        how="inner",
        suffixes=("", "_feature"),
        validate="many_to_one",
    )
    mids: list[float] = []
    bids: list[float] = []
    for row in joined.to_dict("records"):
        quote = json.loads(str(row["quotes_json"])).get(str(row["current_bracket"])) or {}
        mids.append(float(quote.get("mid", np.nan)))
        bids.append(float(quote.get("bid", np.nan)))
    joined["yes_mid"] = mids
    joined["yes_bid"] = bids
    joined = joined[joined["yes_mid"].notna()].copy()
    joined["market_p_no"] = 1.0 - joined["yes_mid"]
    joined["market_no_logit"] = joined["market_p_no"].map(logit)
    joined["y_no"] = (
        joined["winning_bracket"].astype(str) != joined["current_bracket"].astype(str)
    ).astype(int)
    return joined.sort_values(["target_date", "availability_ts_utc"])


def expanding_oof(
    history: pd.DataFrame,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Score block-expanding OOF predictions; each block sees prior dates only."""
    dates = sorted(history["target_date"].astype(str).unique().tolist())
    if len(dates) <= MIN_OOF_TRAIN_DATES:
        raise ValueError("not enough target dates for expanding OOF")
    test_blocks = [
        block.tolist()
        for block in np.array_split(np.asarray(dates[MIN_OOF_TRAIN_DATES:]), OOF_BLOCKS)
        if len(block)
    ]
    outputs: list[pd.DataFrame] = []
    manifests: list[dict[str, Any]] = []
    for fold, test_dates in enumerate(test_blocks, start=1):
        first_test = str(test_dates[0])
        train = history[history["target_date"] < first_test].copy()
        test = history[history["target_date"].isin(test_dates)].copy()
        calibrator = fit_market_calibrator(train)
        train["market_calibrated_p_no"] = predict_market_calibrator(calibrator, train)
        test["market_calibrated_p_no"] = predict_market_calibrator(calibrator, test)
        train["market_calibrated_logit"] = train["market_calibrated_p_no"].map(logit)
        test["market_calibrated_logit"] = test["market_calibrated_p_no"].map(logit)
        for family, names in FEATURE_FAMILIES.items():
            for ridge in RIDGES:
                key = f"p_{family}_ridge_{int(ridge)}"
                artifact = fit_offset(
                    train,
                    names,
                    ridge,
                    offset_column="market_calibrated_logit",
                )
                test[key] = predict_offset(artifact, test)
        test["oof_fold"] = fold
        outputs.append(test)
        manifests.append({
            "fold": fold,
            "train_start": min(calibrator["train_dates"]),
            "train_end": max(calibrator["train_dates"]),
            "train_dates": len(calibrator["train_dates"]),
            "test_start": str(test_dates[0]),
            "test_end": str(test_dates[-1]),
            "test_dates": len(test_dates),
            "market_calibrator": calibrator,
        })
    return pd.concat(outputs, ignore_index=True), manifests


def candidate_report(
    frame: pd.DataFrame,
    probability: np.ndarray,
    calibrated_market: np.ndarray,
) -> dict[str, Any]:
    raw_market = frame["market_p_no"].to_numpy(dtype=float)
    return {
        "score": score(frame, probability),
        "brier_delta_vs_raw_market": bootstrap_delta(
            frame, probability, raw_market, "brier"
        ),
        "logloss_delta_vs_raw_market": bootstrap_delta(
            frame, probability, raw_market, "logloss"
        ),
        "brier_delta_vs_calibrated_market": bootstrap_delta(
            frame, probability, calibrated_market, "brier"
        ),
        "logloss_delta_vs_calibrated_market": bootstrap_delta(
            frame, probability, calibrated_market, "logloss"
        ),
    }


def exact_cross_archive_coverage(
    features: pd.DataFrame,
    frozen: pd.DataFrame,
) -> dict[str, Any]:
    window = features[
        features["target_date"].between(FROZEN_START, FROZEN_END)
        & (features["archive_current_cross_proxy"] == 1)
    ].sort_values(["target_date", "current_bracket", "decision_ts_utc"])
    first = window.groupby(["target_date", "current_bracket"], sort=True).head(1).copy()
    keys = set(zip(frozen["target_date"], frozen["decision_ts_utc"], frozen["current_bracket"]))
    first["covered_by_market_holdout"] = [
        (str(row.target_date), str(row.decision_ts_utc), int(row.current_bracket)) in keys
        for row in first.itertuples()
    ]
    false = first[first["binary_leave_current"].astype(int) == 0]
    false_rows = [{
        "target_date": str(row.target_date),
        "decision_ts_utc": str(row.decision_ts_utc),
        "current_bracket": int(row.current_bracket),
        "jma_temp_c": float(row.jma_temp_c),
        "final_bracket": int(row.final_bracket),
        "covered_by_market_holdout": bool(row.covered_by_market_holdout),
    } for row in false.itertuples()]
    return {
        "definition": "first state with jma_current_minus_current_bracket >= 0.7 per target_date/current_bracket; archive proxy, not runner event identity",
        "signals": int(len(first)),
        "target_dates": int(first["target_date"].nunique()),
        "terminal_false_signals": int(len(false)),
        "market_holdout_covered_signals": int(first["covered_by_market_holdout"].sum()),
        "market_holdout_covered_false_signals": int(false["covered_by_market_holdout"].sum()),
        "false_signal_rows": false_rows,
    }


def slice_score(
    frame: pd.DataFrame,
    candidate: np.ndarray,
    calibrated_market: np.ndarray,
    mask: pd.Series,
) -> dict[str, Any]:
    selected = np.flatnonzero(mask.to_numpy(dtype=bool))
    if len(selected) == 0:
        return {"rows": 0, "target_dates": 0}
    sliced = frame.iloc[selected]
    return candidate_report(sliced, candidate[selected], calibrated_market[selected])


def calibration_bands(
    frame: pd.DataFrame,
    probability: np.ndarray,
) -> list[dict[str, Any]]:
    work = frame[["target_date", "y_no"]].copy()
    work["p"] = probability
    bands = (
        (0.00, 0.45, "00_45"),
        (0.45, 0.55, "45_55"),
        (0.55, 0.75, "55_75"),
        (0.75, 0.85, "75_85"),
        (0.85, 1.01, "85_100"),
    )
    output: list[dict[str, Any]] = []
    for lower, upper, label in bands:
        selected = work[(work["p"] >= lower) & (work["p"] < upper)]
        if selected.empty:
            continue
        by_date = selected.groupby("target_date", sort=True)
        output.append({
            "band": label,
            "rows": int(len(selected)),
            "target_dates": int(selected["target_date"].nunique()),
            "mean_probability": float(by_date["p"].mean().mean()),
            "realized_rate": float(by_date["y_no"].mean().mean()),
        })
    return output


def taker_friction_summary(frame: pd.DataFrame) -> dict[str, Any]:
    work = frame[frame["yes_bid"].notna()].copy()
    work["no_ask"] = 1.0 - work["yes_bid"].astype(float)
    work = work[work["no_ask"].between(0.0, 1.0, inclusive="neither")].copy()
    work["half_spread"] = work["no_ask"] - work["market_p_no"]
    work["fee_per_share"] = work["no_ask"].map(v3.official_fee_per_share)
    work["total_taker_friction"] = work["half_spread"] + work["fee_per_share"]
    return {
        "rows": int(len(work)),
        "target_dates": int(work["target_date"].nunique()),
        "mean_half_spread": float(work["half_spread"].mean()),
        "median_half_spread": float(work["half_spread"].median()),
        "mean_fee_per_share": float(work["fee_per_share"].mean()),
        "mean_total_taker_friction": float(work["total_taker_friction"].mean()),
        "median_total_taker_friction": float(work["total_taker_friction"].median()),
        "p90_total_taker_friction": float(work["total_taker_friction"].quantile(0.9)),
    }


def rolling_no(frame: pd.DataFrame, probability: np.ndarray) -> pd.DataFrame:
    work = frame.copy()
    work["p_no"] = probability
    work["no_ask"] = 1.0 - work["yes_bid"]
    work["fee_per_share"] = work["no_ask"].map(v3.official_fee_per_share)
    work["fee_adjusted_edge"] = work["p_no"] - work["no_ask"] - work["fee_per_share"]
    eligible = work[
        work["no_ask"].between(0.0, 1.0, inclusive="neither")
        & (work["fee_adjusted_edge"] >= EDGE_THRESHOLD)
    ].copy()
    trades = eligible.groupby(["target_date", "current_bracket"], sort=True).head(1).copy()
    trades["shares"] = SHARES
    trades["entry_cost_usd"] = SHARES * (trades["no_ask"] + trades["fee_per_share"])
    trades["fee_adjusted_pnl_usd"] = SHARES * trades["y_no"] - trades["entry_cost_usd"]
    trades["position_state_at_entry"] = "one_unresolved_current_no"
    trades["trade_class"] = "research_quote_level_counterfactual"
    return trades


def trade_summary(trades: pd.DataFrame, denominator_dates: Sequence[str]) -> dict[str, Any]:
    cost = float(trades["entry_cost_usd"].sum()) if len(trades) else 0.0
    pnl = float(trades["fee_adjusted_pnl_usd"].sum()) if len(trades) else 0.0
    daily = pd.DataFrame({"target_date": list(denominator_dates)}).merge(
        trades.groupby("target_date", as_index=False).agg(
            trades=("target_date", "size"),
            cost=("entry_cost_usd", "sum"),
            pnl=("fee_adjusted_pnl_usd", "sum"),
        ),
        on="target_date",
        how="left",
    ).fillna(0)
    blocks = daily["pnl"].to_numpy(dtype=float)
    rng = np.random.default_rng(20260802)
    draws = np.asarray([
        rng.choice(blocks, size=len(blocks), replace=True).sum()
        for _ in range(BOOTSTRAP_DRAWS)
    ])
    return {
        "trades": int(len(trades)),
        "target_dates": int(trades["target_date"].nunique()) if len(trades) else 0,
        "denominator_dates": int(len(denominator_dates)),
        "wins": int(trades["y_no"].sum()) if len(trades) else 0,
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": None if cost == 0 else pnl / cost,
        "pnl_date_block_ci_low": float(np.quantile(draws, 0.025)),
        "pnl_date_block_ci_high": float(np.quantile(draws, 0.975)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, default=FEATURE_ROWS)
    parser.add_argument("--proxy", type=Path, default=PROXY_ROWS)
    parser.add_argument("--holdout", type=Path, default=HOLDOUT_ROWS)
    parser.add_argument("--confirmation-predictions", type=Path, default=CONFIRMATION_ROWS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--run-id", default="tokyo_final_settlement_stack_20260806")
    parser.add_argument("--clean-forward-start", default="2026-08-06")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    features = load_features(args.features, args.confirmation_predictions)
    history = historical_frame(features, args.proxy)
    frozen = frozen_frame(features, args.holdout)
    if history["q_confirm_30m"].isna().any():
        missing = history.loc[history["q_confirm_30m"].isna(), "target_date"].nunique()
        raise ValueError(f"confirmation probability missing from {missing} historical dates")
    if frozen["q_confirm_30m"].isna().any():
        missing = frozen.loc[frozen["q_confirm_30m"].isna(), "target_date"].nunique()
        raise ValueError(f"confirmation probability missing from {missing} frozen dates")

    oof, fold_manifest = expanding_oof(history)
    calibrated_oof = oof["market_calibrated_p_no"].to_numpy(dtype=float)
    candidates: list[dict[str, Any]] = []
    for family, names in FEATURE_FAMILIES.items():
        for ridge in RIDGES:
            key = f"p_{family}_ridge_{int(ridge)}"
            probability = oof[key].to_numpy(dtype=float)
            candidates.append({
                "family": family,
                "ridge": ridge,
                "prediction_column": key,
                **candidate_report(oof, probability, calibrated_oof),
            })
    calibrated_market_score = score(oof, calibrated_oof)
    raw_market_score = score(oof, oof["market_p_no"].to_numpy(dtype=float))
    eligible = [
        row for row in candidates
        if row["score"]["brier"] < calibrated_market_score["brier"]
        and row["score"]["logloss"] < calibrated_market_score["logloss"]
    ]
    selected = min(
        eligible or candidates,
        key=lambda row: (row["score"]["brier"], row["score"]["logloss"]),
    )
    selected_oof = oof[str(selected["prediction_column"])].to_numpy(dtype=float)
    probability_gate = {
        "minimum_oof_target_dates": 50,
        "oof_target_dates": int(oof["target_date"].nunique()),
        "brier_ci_upper_below_zero_vs_calibrated_market": bool(
            selected["brier_delta_vs_calibrated_market"]["ci_high"] < 0
        ),
        "logloss_ci_upper_below_zero_vs_calibrated_market": bool(
            selected["logloss_delta_vs_calibrated_market"]["ci_high"] < 0
        ),
    }
    probability_gate["model_training_ok"] = bool(
        probability_gate["oof_target_dates"] >= probability_gate["minimum_oof_target_dates"]
        and probability_gate["brier_ci_upper_below_zero_vs_calibrated_market"]
        and probability_gate["logloss_ci_upper_below_zero_vs_calibrated_market"]
    )

    final_market_calibrator = fit_market_calibrator(history)
    history = history.copy()
    history["market_calibrated_p_no"] = predict_market_calibrator(
        final_market_calibrator, history
    )
    history["market_calibrated_logit"] = history["market_calibrated_p_no"].map(logit)
    final_artifact = fit_offset(
        history,
        FEATURE_FAMILIES[str(selected["family"])],
        float(selected["ridge"]),
        offset_column="market_calibrated_logit",
    )
    final_artifact.update({
        "schema_version": "tokyo_overshoot_market_residual_v2",
        "model_id": "tokyo_overshoot_market_residual_v2",
        "run_id": args.run_id,
        "target_id": "leave_current_exact_bracket_probability",
        "market_probability_semantics": "one_minus_current_exact_yes_midpoint",
        "market_logit_floor": LOGIT_FLOOR,
        "market_calibrator": final_market_calibrator,
        "confirmation_feature": {
            "target_id": "next_routine_metar_confirms_jma_lattice_30m",
            "model_id": "jma_metar_hgb_v1",
            "semantics": "feature_only_not_hard_gate",
        },
        "training_clock_class": "archive_reconstructed_plus_15m_price_proxy",
        "training_end": VALIDATION_END,
        "post_audit_start": FROZEN_START,
        "clean_forward_start": args.clean_forward_start,
        "strict_pit_forecast_available": False,
        "input_hashes": {
            "features": file_hash(args.features),
            "proxy": file_hash(args.proxy),
            "holdout": file_hash(args.holdout),
            "confirmation_predictions": file_hash(args.confirmation_predictions),
        },
    })
    artifact_path = args.out / "tokyo_overshoot_market_residual_v2.joblib"
    joblib.dump(final_artifact, artifact_path, compress=3)
    spec_path = args.out / "tokyo_overshoot_market_residual_v2.spec.json"
    spec = {
        key: value
        for key, value in final_artifact.items()
        if key not in {"median", "mean", "scale", "beta"}
    }
    spec_path.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    frozen = frozen.copy()
    frozen["market_calibrated_p_no"] = predict_market_calibrator(
        final_market_calibrator, frozen
    )
    frozen["market_calibrated_logit"] = frozen["market_calibrated_p_no"].map(logit)
    frozen_model = predict_offset(final_artifact, frozen)
    frozen_market = frozen["market_p_no"].to_numpy(dtype=float)
    frozen_calibrated_market = frozen["market_calibrated_p_no"].to_numpy(dtype=float)
    trades = rolling_no(frozen, frozen_model)
    denominator_dates = sorted(frozen["target_date"].unique().tolist())

    best_stacked = min(
        (row for row in candidates if row["family"] == "stacked_confirmation"),
        key=lambda row: (row["score"]["brier"], row["score"]["logloss"]),
    )
    compact_match = next(
        row for row in candidates
        if row["family"] == "compact" and row["ridge"] == best_stacked["ridge"]
    )
    stacked_oof = oof[str(best_stacked["prediction_column"])].to_numpy(dtype=float)
    compact_oof = oof[str(compact_match["prediction_column"])].to_numpy(dtype=float)
    q_incremental = {
        "selected_family": selected["family"],
        "best_stacked": best_stacked,
        "same_ridge_compact": compact_match,
        "brier_delta_stacked_vs_compact": bootstrap_delta(
            oof, stacked_oof, compact_oof, "brier"
        ),
        "logloss_delta_stacked_vs_compact": bootstrap_delta(
            oof, stacked_oof, compact_oof, "logloss"
        ),
    }

    archive_coverage = exact_cross_archive_coverage(features, frozen)
    summary = {
        "model_id": final_artifact["model_id"],
        "run_id": args.run_id,
        "artifact": str(artifact_path),
        "artifact_sha256": file_hash(artifact_path),
        "spec": str(spec_path),
        "spec_sha256": file_hash(spec_path),
        "selection": selected,
        "probability_gate": probability_gate,
        "confirmation_incremental_value": q_incremental,
        "market_calibrator": final_market_calibrator,
        "evidence_funnel": {
            "historical_proxy_rows": int(len(history)),
            "historical_proxy_dates": int(history["target_date"].nunique()),
            "expanding_oof_rows": int(len(oof)),
            "expanding_oof_dates": int(oof["target_date"].nunique()),
            "frozen_collector_rows": int(len(frozen)),
            "frozen_collector_dates": int(frozen["target_date"].nunique()),
        },
        "expanding_oof": {
            "folds": fold_manifest,
            "raw_market": raw_market_score,
            "calibrated_market": calibrated_market_score,
            "raw_market_calibration_bands": calibration_bands(
                oof, oof["market_p_no"].to_numpy(dtype=float)
            ),
            "calibrated_market_calibration_bands": calibration_bands(
                oof, calibrated_oof
            ),
            "selected_model_calibration_bands": calibration_bands(
                oof, selected_oof
            ),
            "candidates": candidates,
            "selected_slices": {
                "state_entry": slice_score(
                    oof,
                    selected_oof,
                    calibrated_oof,
                    oof["is_state_entry"].astype(int) == 1,
                ),
                "phase_t13": slice_score(
                    oof, selected_oof, calibrated_oof, oof["phase_t13"] == 1
                ),
                "phase_t3": slice_score(
                    oof, selected_oof, calibrated_oof, oof["phase_t3"] == 1
                ),
                "archive_cross_proxy": slice_score(
                    oof,
                    selected_oof,
                    calibrated_oof,
                    oof["archive_current_cross_proxy"] == 1,
                ),
            },
        },
        "post_audit_probability_2026_07_16_to_30": {
            "model": score(frozen, frozen_model),
            "raw_market": score(frozen, frozen_market),
            "calibrated_market": score(frozen, frozen_calibrated_market),
            "brier_delta_vs_calibrated_market": bootstrap_delta(
                frozen, frozen_model, frozen_calibrated_market, "brier"
            ),
            "logloss_delta_vs_calibrated_market": bootstrap_delta(
                frozen, frozen_model, frozen_calibrated_market, "logloss"
            ),
            "brier_delta_vs_raw_market": bootstrap_delta(
                frozen, frozen_model, frozen_market, "brier"
            ),
            "logloss_delta_vs_raw_market": bootstrap_delta(
                frozen, frozen_model, frozen_market, "logloss"
            ),
        },
        "post_audit_rolling_no": trade_summary(trades, denominator_dates),
        "post_audit_taker_friction": taker_friction_summary(frozen),
        "exact_cross_archive_coverage": archive_coverage,
        "classification": {
            "probability": (
                "training_gate_pass" if probability_gate["model_training_ok"]
                else "training_gate_fail_or_inconclusive"
            ),
            "strategy": "quote_level_counterfactual_not_fill_or_capacity_evidence",
            "deployment": "no_live_change_clean_forward_required",
        },
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    frozen_out = frozen[[
        "state_id", "target_date", "decision_ts_utc", "availability_ts_utc",
        "availability_clock_class", "current_bracket", "winning_bracket", "y_no",
        "market_p_no", "market_calibrated_p_no", "yes_mid", "yes_bid",
        "q_confirm_30m", "phase_t13", "phase_t3", "is_state_entry",
        "archive_current_cross_proxy",
    ]].copy()
    frozen_out["p_no_model"] = frozen_model
    frozen_out.to_csv(args.out / "frozen_predictions.csv.gz", index=False, compression="gzip")
    oof.to_csv(args.out / "expanding_oof_predictions.csv.gz", index=False, compression="gzip")
    trades.to_csv(args.out / "rolling_no_trades.csv", index=False)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
