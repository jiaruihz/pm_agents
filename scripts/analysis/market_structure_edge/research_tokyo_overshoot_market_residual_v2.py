#!/usr/bin/env python3
"""Tokyo current-NO market-offset residual with a frozen collector holdout.

Historical CLOB prices are midpoint-like proxies observed after the weather
checkpoint's assumed availability time.  They train only the fixed-offset
probability correction; they are never treated as bid/ask, depth, or fills.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
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
DEFAULT_OUT = ROOT / "docs/analysis/2026-08/generated/tokyo_overshoot_market_residual_v2"
DEVELOPMENT_END = "2026-06-30"
VALIDATION_START = "2026-07-01"
VALIDATION_END = "2026-07-15"
FROZEN_START = "2026-07-16"
FROZEN_END = "2026-07-30"
EPS = 1e-6
LOGIT_FLOOR = 0.005
EDGE_THRESHOLD = 0.02
SHARES = 5.0
BOOTSTRAP_DRAWS = 5000

FEATURE_FAMILIES: dict[str, tuple[str, ...]] = {
    "compact": (
        "distance_to_next_jma_lattice_c",
        "jma_temp_slope_60m_cph",
        "minutes_since_jma_strict_high",
        "remaining_to_18h",
    ),
    "physical": (
        "distance_to_next_jma_lattice_c",
        "jma_temp_slope_30m_cph",
        "jma_temp_slope_60m_cph",
        "minutes_since_jma_strict_high",
        "jma_warming_run_count",
        "jma_pullback_from_running_max_c",
        "remaining_to_18h",
        "solar_elevation_deg",
        "jma_minus_prior_metar_c",
        "prior_metar_age_min",
        "metar_dewpoint_depression_c",
        "metar_wind_speed_kt",
        "metar_cloud_cover_fraction",
        "metar_precipitating",
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


def fit_offset(frame: pd.DataFrame, features: Sequence[str], ridge: float) -> dict[str, Any]:
    raw = frame[list(features)].to_numpy(dtype=float)
    median = np.nanmedian(raw, axis=0)
    median = np.where(np.isfinite(median), median, 0.0)
    filled = np.where(np.isfinite(raw), raw, median)
    mean = filled.mean(axis=0)
    scale = filled.std(axis=0)
    scale = np.where(scale > 0, scale, 1.0)
    matrix = np.column_stack([np.ones(len(frame)), (filled - mean) / scale])
    labels = frame["y_no"].to_numpy(dtype=float)
    offset = frame["market_no_logit"].to_numpy(dtype=float)
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
        frame["market_no_logit"].to_numpy(dtype=float)
        + matrix @ np.asarray(artifact["beta"], dtype=float)
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


def load_features(path: Path) -> pd.DataFrame:
    raw = v1.read_rows(path)
    continuous = v1.build_continuous_rows(raw)
    frame = pd.DataFrame(continuous)
    frame["target_date"] = frame["target_date"].astype(str)
    frame["decision_ts_utc"] = frame["decision_ts_utc"].astype(str)
    frame["current_bracket"] = frame["current_bracket"].astype(int)
    return frame


def historical_frame(features: pd.DataFrame, path: Path) -> pd.DataFrame:
    proxy = pd.read_csv(path)
    proxy = proxy[
        proxy["after_price"].notna()
        & proxy["after_delay_min"].between(0.0, 15.0, inclusive="both")
    ].copy()
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
    books["decision_ts_utc"] = books["decision_ts_utc"].astype(str)
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
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    features = load_features(args.features)
    history = historical_frame(features, args.proxy)
    development = history[history["target_date"] <= DEVELOPMENT_END].copy()
    validation = history[history["target_date"].between(VALIDATION_START, VALIDATION_END)].copy()
    frozen = frozen_frame(features, args.holdout)

    candidates: list[dict[str, Any]] = []
    market_validation = validation["market_p_no"].to_numpy(dtype=float)
    for family, names in FEATURE_FAMILIES.items():
        for ridge in RIDGES:
            artifact = fit_offset(development, names, ridge)
            probability = predict_offset(artifact, validation)
            candidates.append({
                "family": family,
                "ridge": ridge,
                "score": score(validation, probability),
                "brier_delta_vs_market": bootstrap_delta(
                    validation, probability, market_validation, "brier"
                ),
                "logloss_delta_vs_market": bootstrap_delta(
                    validation, probability, market_validation, "logloss"
                ),
            })
    market_score = score(validation, market_validation)
    eligible = [
        row for row in candidates
        if row["score"]["brier"] < market_score["brier"]
        and row["score"]["logloss"] < market_score["logloss"]
    ]
    selected = min(
        eligible or candidates,
        key=lambda row: (row["score"]["brier"], row["score"]["logloss"]),
    )
    final_artifact = fit_offset(
        history[history["target_date"] <= VALIDATION_END],
        FEATURE_FAMILIES[str(selected["family"])],
        float(selected["ridge"]),
    )
    final_artifact.update({
        "schema_version": "tokyo_overshoot_market_residual_v2",
        "model_id": "tokyo_overshoot_market_residual_v2",
        "target_id": "leave_current_exact_bracket_probability",
        "market_probability_semantics": "one_minus_current_exact_yes_midpoint",
        "market_logit_floor": LOGIT_FLOOR,
        "training_clock_class": "archive_reconstructed_plus_15m_price_proxy",
        "training_end": VALIDATION_END,
        "clean_frozen_start": FROZEN_START,
        "strict_pit_forecast_available": False,
        "input_hashes": {
            "features": file_hash(args.features),
            "proxy": file_hash(args.proxy),
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

    frozen_model = predict_offset(final_artifact, frozen)
    frozen_market = frozen["market_p_no"].to_numpy(dtype=float)
    trades = rolling_no(frozen, frozen_model)
    denominator_dates = sorted(frozen["target_date"].unique().tolist())
    summary = {
        "model_id": final_artifact["model_id"],
        "artifact": str(artifact_path.relative_to(ROOT)),
        "artifact_sha256": file_hash(artifact_path),
        "spec": str(spec_path.relative_to(ROOT)),
        "spec_sha256": file_hash(spec_path),
        "selection": selected,
        "evidence_funnel": {
            "historical_proxy_rows": int(len(history)),
            "historical_proxy_dates": int(history["target_date"].nunique()),
            "development_rows": int(len(development)),
            "development_dates": int(development["target_date"].nunique()),
            "validation_rows": int(len(validation)),
            "validation_dates": int(validation["target_date"].nunique()),
            "frozen_collector_rows": int(len(frozen)),
            "frozen_collector_dates": int(frozen["target_date"].nunique()),
        },
        "validation": {
            "market": market_score,
            "candidates": candidates,
        },
        "frozen_probability": {
            "model": score(frozen, frozen_model),
            "market": score(frozen, frozen_market),
            "brier_delta_vs_market": bootstrap_delta(
                frozen, frozen_model, frozen_market, "brier"
            ),
            "logloss_delta_vs_market": bootstrap_delta(
                frozen, frozen_model, frozen_market, "logloss"
            ),
        },
        "frozen_rolling_no": trade_summary(trades, denominator_dates),
        "classification": {
            "probability": "frozen_forward_collector_evaluation",
            "strategy": "quote_level_counterfactual_not_fill_or_capacity_evidence",
            "deployment": "zero_notional_telemetry_only_until_forward_gate",
        },
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    frozen_out = frozen[[
        "state_id", "target_date", "decision_ts_utc", "availability_ts_utc",
        "availability_clock_class", "current_bracket", "winning_bracket", "y_no",
        "market_p_no", "yes_mid", "yes_bid",
    ]].copy()
    frozen_out["p_no_model"] = frozen_model
    frozen_out.to_csv(args.out / "frozen_predictions.csv.gz", index=False, compression="gzip")
    trades.to_csv(args.out / "rolling_no_trades.csv", index=False)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
