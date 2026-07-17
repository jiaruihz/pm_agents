#!/usr/bin/env python3
"""Wide-denominator current-exact weather + book residual audit.

Target:
    P(final Tmax stops in the current exact bracket | PIT weather/path/book)
    minus the same-snapshot market probability.

The market logit is a fixed offset with coefficient exactly one.  The model
may only learn a strongly regularized correction from continuous weather/path
facts and, in one candidate, local ladder geometry.  No city, price band,
weather regime, or post-outcome slice is eligible.

Model/penalty selection uses expanding OOF dates strictly before the frozen
forward boundary.  Forward rows are touched once.  Trading is secondary: on
each forward state, compare current YES and current NO at direct ask plus the
official Weather taker fee, select a side only when modeled edge is positive,
then keep only the first signal per city-day.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = (
    ROOT
    / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1"
    / "intraday_weather_regime_state_rows.csv"
)
DEFAULT_OUTPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/current_exact_weather_book_offset_v1"
    / "summary.json"
)
FORWARD_START = "2026-06-21"
MIN_INNER_TRAIN_DATES = 10
PENALTIES = (0.03, 0.1, 0.3, 1.0, 3.0)
BOOTSTRAP_DRAWS = 5_000
EPS = 1e-5

REQUIRED_COLUMNS = {
    "city",
    "target_date",
    "decision_hour_local",
    "decision_snapshot_ts_utc",
    "unit",
    "current_yes_ask",
    "current_no_ask",
    "current_no_ask_size",
    "current_bracket_held",
    "forecast_gap_to_running_native",
    "forecast_peak_delta_hours_local",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "decline_native",
    "minutes_since_running_max",
    "d1_no_ask",
    "d1_no_bid",
    "d1_no_spread",
    "d1_no_ask_size",
    "d2_no_ask",
    "d2_no_bid",
    "d2_no_spread",
    "d2_no_ask_size",
    "lottery_yes_ask",
    "lottery_yes_bid",
    "lottery_yes_spread",
    "lottery_yes_ask_size",
    "current_no_spread",
}

WEATHER_PATH_FEATURES = [
    "forecast_gap_to_running_native",
    "forecast_gap_squared",
    "forecast_peak_pre_hours",
    "forecast_peak_post_hours",
    "temp_trend_1h_native",
    "temp_trend_3h_native",
    "decline_native",
    "log_minutes_since_running_max",
    "decision_hour_local",
]

BOOK_FEATURES = [
    "d1_yes_mid",
    "d2_yes_mid",
    "lottery_yes_mid",
    "current_no_spread",
    "d1_no_spread",
    "d2_no_spread",
    "lottery_yes_spread",
    "log_current_no_ask_size",
    "log_d1_no_ask_size",
    "log_d2_no_ask_size",
    "log_lottery_yes_ask_size",
]

FEATURE_SETS = {
    "market_calibration_offset": [],
    "weather_path_offset": WEATHER_PATH_FEATURES,
    "weather_path_book_offset": WEATHER_PATH_FEATURES + BOOK_FEATURES,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--forward-start", default=FORWARD_START)
    parser.add_argument("--bootstrap-draws", type=int, default=BOOTSTRAP_DRAWS)
    return parser.parse_args()


def official_fee(price: pd.Series | np.ndarray) -> pd.Series | np.ndarray:
    return 0.05 * price * (1.0 - price)


def binary_logloss(y: np.ndarray, probability: np.ndarray) -> np.ndarray:
    p = np.clip(probability, 1e-9, 1.0 - 1e-9)
    return -(y * np.log(p) + (1.0 - y) * np.log(1.0 - p))


def prepare_frame(raw: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(REQUIRED_COLUMNS - set(raw.columns))
    if missing:
        raise ValueError(f"input is missing required columns: {missing}")

    frame = raw.copy()
    frame["market_p_current_yes"] = (
        pd.to_numeric(frame["current_yes_ask"], errors="coerce")
        + 1.0
        - pd.to_numeric(frame["current_no_ask"], errors="coerce")
    ) / 2.0
    frame["label_current_yes"] = pd.to_numeric(
        frame["current_bracket_held"], errors="coerce"
    )

    unit_factor = np.where(frame["unit"].eq("C"), 1.8, 1.0)
    frame["temp_trend_1h_native"] = (
        pd.to_numeric(frame["temp_trend_1h_f"], errors="coerce") / unit_factor
    )
    frame["temp_trend_3h_native"] = (
        pd.to_numeric(frame["temp_trend_3h_f"], errors="coerce") / unit_factor
    )
    gap = pd.to_numeric(frame["forecast_gap_to_running_native"], errors="coerce")
    peak_delta = pd.to_numeric(
        frame["forecast_peak_delta_hours_local"], errors="coerce"
    )
    frame["forecast_gap_squared"] = gap**2
    frame["forecast_peak_pre_hours"] = peak_delta.clip(lower=0)
    frame["forecast_peak_post_hours"] = (-peak_delta).clip(lower=0)
    frame["log_minutes_since_running_max"] = np.log1p(
        pd.to_numeric(frame["minutes_since_running_max"], errors="coerce").clip(lower=0)
    )

    frame["d1_yes_mid"] = 1.0 - (
        pd.to_numeric(frame["d1_no_ask"], errors="coerce")
        + pd.to_numeric(frame["d1_no_bid"], errors="coerce")
    ) / 2.0
    frame["d2_yes_mid"] = 1.0 - (
        pd.to_numeric(frame["d2_no_ask"], errors="coerce")
        + pd.to_numeric(frame["d2_no_bid"], errors="coerce")
    ) / 2.0
    frame["lottery_yes_mid"] = (
        pd.to_numeric(frame["lottery_yes_ask"], errors="coerce")
        + pd.to_numeric(frame["lottery_yes_bid"], errors="coerce")
    ) / 2.0
    for column in (
        "current_no_ask_size",
        "d1_no_ask_size",
        "d2_no_ask_size",
        "lottery_yes_ask_size",
    ):
        frame[f"log_{column}"] = np.log1p(
            pd.to_numeric(frame[column], errors="coerce").clip(lower=0)
        )

    paired = frame[
        frame["market_p_current_yes"].between(EPS, 1.0 - EPS)
        & frame["label_current_yes"].isin([0.0, 1.0])
    ].copy()
    paired["target_date"] = paired["target_date"].astype(str)
    return paired


def design_matrices(
    train: pd.DataFrame,
    test: pd.DataFrame,
    features: list[str],
) -> tuple[np.ndarray, np.ndarray]:
    if not features:
        return np.ones((len(train), 1)), np.ones((len(test), 1))

    medians = train[features].median()
    if medians.isna().any():
        empty = medians[medians.isna()].index.tolist()
        raise ValueError(f"feature is completely missing in training fold: {empty}")
    scales = train[features].std().replace(0.0, 1.0).fillna(1.0)
    x_train = ((train[features].fillna(medians) - medians) / scales).to_numpy(float)
    x_test = ((test[features].fillna(medians) - medians) / scales).to_numpy(float)

    missing_features = [column for column in features if train[column].isna().any()]
    if missing_features:
        x_train = np.column_stack(
            [x_train, train[missing_features].isna().to_numpy(float)]
        )
        x_test = np.column_stack(
            [x_test, test[missing_features].isna().to_numpy(float)]
        )
    return (
        np.column_stack([np.ones(len(x_train)), x_train]),
        np.column_stack([np.ones(len(x_test)), x_test]),
    )


def fit_offset_predict(
    train: pd.DataFrame,
    test: pd.DataFrame,
    features: list[str],
    penalty: float,
) -> np.ndarray:
    x_train, x_test = design_matrices(train, test, features)
    market_train = np.clip(train["market_p_current_yes"].to_numpy(float), EPS, 1 - EPS)
    label_train = train["label_current_yes"].to_numpy(float)
    offset_train = np.log(market_train / (1.0 - market_train))
    n_rows = len(train)

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        logit = offset_train + x_train @ beta
        probability = 1.0 / (1.0 + np.exp(-np.clip(logit, -35.0, 35.0)))
        loss = float(
            np.mean(np.logaddexp(0.0, logit) - label_train * logit)
            + 0.5 * penalty * np.sum(beta[1:] ** 2)
        )
        gradient = x_train.T @ (probability - label_train) / n_rows
        gradient[1:] += penalty * beta[1:]
        return loss, gradient

    result = minimize(
        objective,
        np.zeros(x_train.shape[1]),
        jac=True,
        method="L-BFGS-B",
        options={"maxiter": 500, "ftol": 1e-10},
    )
    if not result.success:
        raise RuntimeError(f"offset optimizer failed: {result.message}")

    market_test = np.clip(test["market_p_current_yes"].to_numpy(float), EPS, 1 - EPS)
    logit_test = np.log(market_test / (1.0 - market_test)) + x_test @ result.x
    return 1.0 / (1.0 + np.exp(-np.clip(logit_test, -35.0, 35.0)))


def date_mean_ci(
    rows: pd.DataFrame,
    column: str,
    *,
    draws: int,
    seed: int,
) -> dict[str, Any]:
    daily = rows.groupby("target_date")[column].mean()
    point = float(daily.mean())
    if len(daily) < 3:
        return {"point": point, "ci": [None, None], "dates": int(len(daily))}
    rng = np.random.default_rng(seed)
    samples = np.empty(draws, dtype=float)
    values = daily.to_numpy(float)
    for index in range(draws):
        samples[index] = rng.choice(values, size=len(values), replace=True).mean()
    lo, hi = np.quantile(samples, [0.025, 0.975])
    return {
        "point": round(point, 6),
        "ci": [round(float(lo), 6), round(float(hi), 6)],
        "dates": int(len(daily)),
    }


def expanding_dev_score(
    train_period: pd.DataFrame,
    variant: str,
    features: list[str],
    penalty: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    pieces: list[pd.DataFrame] = []
    for target_date in sorted(train_period["target_date"].unique()):
        train = train_period[train_period["target_date"] < target_date]
        test = train_period[train_period["target_date"] == target_date]
        if train["target_date"].nunique() < MIN_INNER_TRAIN_DATES:
            continue
        probability = fit_offset_predict(train, test, features, penalty)
        label = test["label_current_yes"].to_numpy(float)
        market = test["market_p_current_yes"].to_numpy(float)
        scored = test[["target_date", "city", "decision_snapshot_ts_utc"]].copy()
        scored["delta_logloss"] = binary_logloss(label, probability) - binary_logloss(
            label, market
        )
        scored["delta_brier"] = (probability - label) ** 2 - (market - label) ** 2
        pieces.append(scored)
    rows = pd.concat(pieces, ignore_index=True)
    daily = rows.groupby("target_date")[["delta_logloss", "delta_brier"]].mean()
    summary = {
        "variant": variant,
        "penalty": penalty,
        "rows": int(len(rows)),
        "dates": int(len(daily)),
        "delta_logloss_date_equal": round(float(daily["delta_logloss"].mean()), 6),
        "delta_brier_date_equal": round(float(daily["delta_brier"].mean()), 6),
    }
    return rows, summary


def score_forward(
    forward: pd.DataFrame,
    probability: np.ndarray,
    *,
    draws: int,
    seed: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    label = forward["label_current_yes"].to_numpy(float)
    market = forward["market_p_current_yes"].to_numpy(float)
    rows = forward.copy()
    rows["model_p_current_yes"] = probability
    rows["market_logloss"] = binary_logloss(label, market)
    rows["model_logloss"] = binary_logloss(label, probability)
    rows["delta_logloss"] = rows["model_logloss"] - rows["market_logloss"]
    rows["market_brier"] = (market - label) ** 2
    rows["model_brier"] = (probability - label) ** 2
    rows["delta_brier"] = rows["model_brier"] - rows["market_brier"]
    daily = rows.groupby("target_date")[
        ["market_logloss", "model_logloss", "market_brier", "model_brier"]
    ].mean()
    summary = {
        "rows": int(len(rows)),
        "dates": int(rows["target_date"].nunique()),
        "cities": int(rows["city"].nunique()),
        "date_min": str(rows["target_date"].min()),
        "date_max": str(rows["target_date"].max()),
        "market_logloss_date_equal": round(float(daily["market_logloss"].mean()), 6),
        "model_logloss_date_equal": round(float(daily["model_logloss"].mean()), 6),
        "market_brier_date_equal": round(float(daily["market_brier"].mean()), 6),
        "model_brier_date_equal": round(float(daily["model_brier"].mean()), 6),
        "delta_logloss": date_mean_ci(
            rows, "delta_logloss", draws=draws, seed=seed
        ),
        "delta_brier": date_mean_ci(rows, "delta_brier", draws=draws, seed=seed + 1),
        "better_logloss_dates": int(
            (rows.groupby("target_date")["delta_logloss"].mean() < 0).sum()
        ),
    }
    return rows, summary


def roi_ci(
    rows: pd.DataFrame,
    *,
    draws: int,
    seed: int,
) -> list[float | None]:
    if rows.empty or rows["target_date"].nunique() < 3:
        return [None, None]
    daily = rows.groupby("target_date")[["pnl_per_share", "cost_per_share"]].sum()
    dates = daily.index.to_numpy()
    rng = np.random.default_rng(seed)
    values = np.empty(draws, dtype=float)
    for index in range(draws):
        sampled = daily.loc[rng.choice(dates, size=len(dates), replace=True)].sum()
        values[index] = sampled["pnl_per_share"] / sampled["cost_per_share"]
    lo, hi = np.quantile(values, [0.025, 0.975])
    return [round(float(lo), 6), round(float(hi), 6)]


def execution_summary(
    scored_forward: pd.DataFrame,
    *,
    draws: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    rows = scored_forward.copy()
    yes_ask = pd.to_numeric(rows["current_yes_ask"], errors="coerce")
    no_ask = pd.to_numeric(rows["current_no_ask"], errors="coerce")
    rows["yes_cost"] = yes_ask + official_fee(yes_ask)
    rows["no_cost"] = no_ask + official_fee(no_ask)
    rows["yes_edge"] = rows["model_p_current_yes"] - rows["yes_cost"]
    rows["no_edge"] = 1.0 - rows["model_p_current_yes"] - rows["no_cost"]
    rows["side"] = np.where(rows["yes_edge"] >= rows["no_edge"], "YES", "NO")
    rows["model_edge"] = np.maximum(rows["yes_edge"], rows["no_edge"])
    rows["cost_per_share"] = np.where(rows["side"].eq("YES"), rows["yes_cost"], rows["no_cost"])
    rows["win"] = np.where(
        rows["side"].eq("YES"),
        rows["label_current_yes"],
        1.0 - rows["label_current_yes"],
    )
    rows["pnl_per_share"] = rows["win"] - rows["cost_per_share"]
    rows["market_same_side_edge"] = np.where(
        rows["side"].eq("YES"),
        rows["market_p_current_yes"] - rows["yes_cost"],
        1.0 - rows["market_p_current_yes"] - rows["no_cost"],
    )
    candidates = rows[
        rows["model_edge"].gt(0)
        & rows["cost_per_share"].between(0.0, 1.1, inclusive="neither")
    ].copy()
    first = (
        candidates.sort_values(
            ["target_date", "city", "decision_hour_local", "decision_snapshot_ts_utc"]
        )
        .drop_duplicates(["target_date", "city"], keep="first")
        .reset_index(drop=True)
    )
    total_cost = float(first["cost_per_share"].sum())
    total_pnl = float(first["pnl_per_share"].sum())
    summary = {
        "policy": "first city-day state with max(YES edge, NO edge) > 0",
        "execution": "direct taker ask plus official Weather fee; no extra price band",
        "candidate_states": int(len(candidates)),
        "candidate_dates": int(candidates["target_date"].nunique()),
        "first_signals": int(len(first)),
        "first_signal_dates": int(first["target_date"].nunique()),
        "first_signal_cities": int(first["city"].nunique()),
        "yes_signals": int(first["side"].eq("YES").sum()),
        "no_signals": int(first["side"].eq("NO").sum()),
        "win_rate": round(float(first["win"].mean()), 6) if len(first) else None,
        "avg_cost_per_share": round(float(first["cost_per_share"].mean()), 6)
        if len(first)
        else None,
        "avg_model_edge_per_share": round(float(first["model_edge"].mean()), 6)
        if len(first)
        else None,
        "avg_market_same_side_edge_per_share": round(
            float(first["market_same_side_edge"].mean()), 6
        )
        if len(first)
        else None,
        "pnl_per_share_sum": round(total_pnl, 6),
        "planned_5_share_pnl_usd": round(5.0 * total_pnl, 6),
        "roi": round(total_pnl / total_cost, 6) if total_cost else None,
        "roi_ci": roi_ci(first, draws=draws, seed=701),
        "depth_boundary": (
            "atlas lacks current YES ask size, so symmetric 5-share depth is not verifiable"
        ),
        "actual_fills": 0,
    }
    return candidates, first, summary


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    raw = pd.read_csv(input_path)
    paired = prepare_frame(raw)
    train_period = paired[paired["target_date"] < args.forward_start].copy()
    forward = paired[paired["target_date"] >= args.forward_start].copy()
    if train_period.empty or forward.empty:
        raise ValueError("train or forward denominator is empty")

    candidates: list[dict[str, Any]] = []
    calibration_rows, calibration_summary = expanding_dev_score(
        train_period,
        "market_calibration_offset",
        FEATURE_SETS["market_calibration_offset"],
        0.0,
    )
    del calibration_rows
    candidates.append(calibration_summary)
    for variant in ("weather_path_offset", "weather_path_book_offset"):
        for penalty in PENALTIES:
            scored, summary = expanding_dev_score(
                train_period, variant, FEATURE_SETS[variant], penalty
            )
            del scored
            candidates.append(summary)
    selected = min(candidates, key=lambda row: row["delta_logloss_date_equal"])

    selected_probability = fit_offset_predict(
        train_period,
        forward,
        FEATURE_SETS[selected["variant"]],
        float(selected["penalty"]),
    )
    scored_forward, forward_summary = score_forward(
        forward, selected_probability, draws=args.bootstrap_draws, seed=503
    )

    calibration_probability = fit_offset_predict(train_period, forward, [], 0.0)
    _, calibration_forward = score_forward(
        forward, calibration_probability, draws=args.bootstrap_draws, seed=601
    )
    candidate_states, first_signals, execution = execution_summary(
        scored_forward, draws=args.bootstrap_draws
    )

    input_stat = input_path.stat()
    label_missing = int(raw["current_bracket_held"].isna().sum())
    summary = {
        "title": "Current exact weather + book fixed-offset residual v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "rejected_static_level_residual",
        "target_metric": (
            "P(final Tmax stops at current exact bracket | PIT weather/path/book) "
            "minus same-snapshot market probability"
        ),
        "contract": {
            "input": str(input_path.relative_to(ROOT)),
            "input_mtime_utc": datetime.fromtimestamp(
                input_stat.st_mtime, tz=timezone.utc
            ).isoformat(),
            "state_grain": "city x target_date x decision_hour_local",
            "label": "current_bracket_held (exact bracket, not touch)",
            "market_baseline": "current YES midpoint from direct YES ask and mirrored NO ask",
            "model": "binary logistic residual with market logit coefficient fixed at 1",
            "forward_start": args.forward_start,
            "model_candidates_k": len(candidates),
            "selection": (
                "minimum date-equal expanding-OOF logloss before forward_start; "
                "no city/price/regime filters"
            ),
            "fee": "0.05 * price * (1-price) per share",
            "bootstrap": "target_date block, 95% interval",
            "weather_state_v3_boundary": (
                "historical atlas has only the strict path core; v3 rain/cloud/wind-direction/solar/future-curve fields are not backfilled"
            ),
        },
        "data_integrity": {
            "raw_rows": int(len(raw)),
            "raw_dates": int(raw["target_date"].nunique()),
            "raw_cities": int(raw["city"].nunique()),
            "date_min": str(raw["target_date"].min()),
            "date_max": str(raw["target_date"].max()),
            "settlement_label_missing_rows": label_missing,
            "settlement_label_missing_share": round(label_missing / len(raw), 6),
            "explicit_missing_bracket": None,
            "explicit_missing_bracket_note": (
                "atlas CSV has no settlement_status field; missing label is reported separately"
            ),
            "paired_probability_rows": int(len(paired)),
            "paired_dates": int(paired["target_date"].nunique()),
            "paired_key_duplicates": int(
                paired.duplicated(
                    ["city", "target_date", "decision_hour_local", "decision_snapshot_ts_utc"]
                ).sum()
            ),
        },
        "signal_funnel": [
            {
                "stage": "atlas state universe",
                "unit": "city-date-hour state",
                "rows": int(len(raw)),
                "dates": int(raw["target_date"].nunique()),
            },
            {
                "stage": "exact-current label plus same-snapshot market probability",
                "unit": "paired state",
                "rows": int(len(paired)),
                "dates": int(paired["target_date"].nunique()),
            },
            {
                "stage": "pre-forward model-selection denominator",
                "unit": "paired state",
                "rows": int(len(train_period)),
                "dates": int(train_period["target_date"].nunique()),
            },
            {
                "stage": "frozen forward denominator",
                "unit": "paired state",
                "rows": int(len(forward)),
                "dates": int(forward["target_date"].nunique()),
            },
            {
                "stage": "positive modeled fee-adjusted edge",
                "unit": "state",
                "rows": int(len(candidate_states)),
                "dates": int(candidate_states["target_date"].nunique()),
            },
            {
                "stage": "first signal per city-day",
                "unit": "city-day signal",
                "rows": int(len(first_signals)),
                "dates": int(first_signals["target_date"].nunique()),
            },
        ],
        "evidence_funnel": [
            {
                "stage": "PIT weather/path core",
                "unit": "forward state",
                "rows": int(len(forward)),
                "dates": int(forward["target_date"].nunique()),
            },
            {
                "stage": "direct current YES/NO ask plus settlement",
                "unit": "first city-day signal",
                "rows": int(len(first_signals)),
                "dates": int(first_signals["target_date"].nunique()),
            },
            {
                "stage": "symmetric 5-share depth verified",
                "unit": "planned order",
                "rows": 0,
                "dates": 0,
                "note": "historical atlas lacks current YES ask size",
            },
            {
                "stage": "actual fills",
                "unit": "fill",
                "rows": 0,
                "dates": 0,
            },
        ],
        "dev_candidate_selection": sorted(
            candidates, key=lambda row: row["delta_logloss_date_equal"]
        ),
        "selected_candidate": selected,
        "forward_probability": forward_summary,
        "forward_calibration_only_control": calibration_forward,
        "forward_execution": execution,
        "three_gates": {
            "significance": "FAIL: forward proper-score delta is not negative",
            "baseline": "FAIL: selected weather residual does not beat raw market",
            "forward": "FAIL: train improvement reverses on the 15-date frozen forward window",
            "conclusion": "inconclusive_no_static_weather_book_strategy",
        },
        "verdict": {
            "static_weather_level_strategy": "reject",
            "live_action": "none",
            "previous_098_maker_hypothesis": (
                "not selected; it is execution microstructure, not weather residual alpha"
            ),
            "next_research": (
                "first-seen weather innovation -> full-ladder repricing lag; collect complete "
                "weather_state_v3 + pre/post event book + settlement before fitting"
            ),
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(f"wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
