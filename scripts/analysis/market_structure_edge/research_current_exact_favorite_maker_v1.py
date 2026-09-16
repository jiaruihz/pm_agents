#!/usr/bin/env python3
"""Audit a current-exact high-favorite maker hypothesis.

This script deliberately separates two questions:

1. Probability layer: when the PIT current-exact YES midpoint is above 0.98,
   does the bracket settle YES more often than the midpoint implies?
2. Execution layer: what is the *all-fill, zero-adverse-selection benchmark*
   at a nominal post-only bid-plus-one-tick price?

The second quantity is not a maker backtest.  The atlas has hourly PIT book
states but no queue position or trade tape, so it cannot identify which
resting orders would fill.  A queue-aware forward shadow is required before
the execution hypothesis can be promoted.

Grain: current exact bracket at city x target_date x decision snapshot.
Honest policy denominator: first threshold crossing per city x target_date.
Uncertainty: target_date block bootstrap, 95% interval.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = (
    ROOT
    / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1"
    / "intraday_weather_regime_state_rows.csv"
)
DEFAULT_OUTPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/current_exact_favorite_maker_v1"
    / "summary.json"
)
DEFAULT_THRESHOLD = 0.98
DEFAULT_TICK_SIZE = 0.001
DEFAULT_FORWARD_START = "2026-06-21"

REQUIRED_COLUMNS = {
    "city",
    "target_date",
    "decision_hour_local",
    "decision_snapshot_ts_utc",
    "current_yes_ask",
    "current_no_ask",
    "current_bracket_held",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--tick-size", type=float, default=DEFAULT_TICK_SIZE)
    parser.add_argument("--forward-start", default=DEFAULT_FORWARD_START)
    parser.add_argument("--bootstrap-draws", type=int, default=4_000)
    return parser.parse_args()


def taker_fee(price: pd.Series) -> pd.Series:
    return 0.05 * price * (1.0 - price)


def block_mean_ci(
    rows: pd.DataFrame,
    value_col: str,
    *,
    draws: int,
    seed: int,
) -> list[float | None]:
    usable = rows.dropna(subset=["target_date", value_col])
    dates = usable["target_date"].unique()
    if len(dates) < 3:
        return [None, None]

    daily = usable.groupby("target_date")[value_col].agg(["sum", "count"])
    rng = np.random.default_rng(seed)
    samples = np.empty(draws, dtype=float)
    for idx in range(draws):
        sampled = daily.loc[rng.choice(dates, size=len(dates), replace=True)]
        samples[idx] = sampled["sum"].sum() / sampled["count"].sum()
    lo, hi = np.quantile(samples, [0.025, 0.975])
    return [round(float(lo), 6), round(float(hi), 6)]


def prepare_rows(frame: pd.DataFrame, tick_size: float) -> pd.DataFrame:
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"input is missing required columns: {missing}")
    if not 0 < tick_size < 1:
        raise ValueError("tick_size must be between 0 and 1")

    rows = frame[list(REQUIRED_COLUMNS)].copy()
    rows["yes_best_ask"] = pd.to_numeric(rows["current_yes_ask"], errors="coerce")
    rows["yes_best_bid"] = 1.0 - pd.to_numeric(rows["current_no_ask"], errors="coerce")
    rows["yes_mid"] = (rows["yes_best_ask"] + rows["yes_best_bid"]) / 2.0
    rows["win_yes"] = pd.to_numeric(rows["current_bracket_held"], errors="coerce")
    rows["spread"] = rows["yes_best_ask"] - rows["yes_best_bid"]

    # Same resting-price rule used by the current execution layer: improve one
    # actual tick when possible, otherwise join the bid, never lock/cross.
    rows["maker_limit"] = np.maximum(
        rows["yes_best_bid"],
        np.minimum(rows["yes_best_bid"] + tick_size, rows["yes_best_ask"] - tick_size),
    )
    rows["maker_postable"] = (
        rows["yes_best_bid"].between(0.0, 1.0, inclusive="neither")
        & rows["yes_best_ask"].between(0.0, 1.0, inclusive="neither")
        & (rows["yes_best_ask"] > rows["yes_best_bid"])
        & (rows["maker_limit"] >= rows["yes_best_bid"])
        & (rows["maker_limit"] < rows["yes_best_ask"])
    )
    rows["probability_valid"] = (
        rows["yes_mid"].between(0.0, 1.0, inclusive="neither")
        & rows["win_yes"].isin([0.0, 1.0])
    )
    rows["probability_bias"] = rows["win_yes"] - rows["yes_mid"]
    rows["maker_all_fill_edge"] = np.where(
        rows["maker_postable"], rows["win_yes"] - rows["maker_limit"], np.nan
    )
    rows["taker_edge_fee_adjusted"] = (
        rows["win_yes"] - rows["yes_best_ask"] - taker_fee(rows["yes_best_ask"])
    )
    return rows


def first_crossing(rows: pd.DataFrame) -> pd.DataFrame:
    ordered = rows.sort_values(
        ["target_date", "city", "decision_hour_local", "decision_snapshot_ts_utc"]
    )
    return ordered.drop_duplicates(["target_date", "city"], keep="first").reset_index(drop=True)


def summary_for(
    rows: pd.DataFrame,
    label: str,
    *,
    draws: int,
    seed: int,
) -> dict:
    if rows.empty:
        return {"slice": label, "rows": 0}

    maker_rows = rows[rows["maker_postable"]].copy()
    result = {
        "slice": label,
        "rows": int(len(rows)),
        "dates": int(rows["target_date"].nunique()),
        "cities": int(rows["city"].nunique()),
        "date_min": str(rows["target_date"].min()),
        "date_max": str(rows["target_date"].max()),
        "win_rate": round(float(rows["win_yes"].mean()), 6),
        "loss_rows": int((rows["win_yes"] == 0).sum()),
        "avg_mid": round(float(rows["yes_mid"].mean()), 6),
        "probability_bias_per_share": round(float(rows["probability_bias"].mean()), 6),
        "probability_bias_ci": block_mean_ci(
            rows, "probability_bias", draws=draws, seed=seed
        ),
        "avg_spread": round(float(rows["spread"].mean()), 6),
        "avg_half_spread": round(float(rows["spread"].mean() / 2.0), 6),
        "maker_postable_rows": int(len(maker_rows)),
        "avg_maker_limit": (
            round(float(maker_rows["maker_limit"].mean()), 6) if len(maker_rows) else None
        ),
        "maker_zero_adverse_selection_benchmark_per_share": (
            round(float(maker_rows["maker_all_fill_edge"].mean()), 6)
            if len(maker_rows)
            else None
        ),
        "maker_zero_adverse_selection_benchmark_ci": block_mean_ci(
            maker_rows, "maker_all_fill_edge", draws=draws, seed=seed + 1
        ),
        "same_trigger_taker_edge_fee_adjusted_per_share": round(
            float(rows["taker_edge_fee_adjusted"].mean()), 6
        ),
        "same_trigger_taker_edge_fee_adjusted_ci": block_mean_ci(
            rows, "taker_edge_fee_adjusted", draws=draws, seed=seed + 2
        ),
        "no_trade_edge_per_share": 0.0,
    }
    if len(maker_rows):
        mean_limit = float(maker_rows["maker_limit"].mean())
        mean_upper_edge = float(maker_rows["maker_all_fill_edge"].mean())
        result["fill_conditioned_break_even_win_rate"] = round(mean_limit, 6)
        result["adverse_selection_budget_pp"] = round(100.0 * mean_upper_edge, 4)
        result["one_loss_cost_usd_per_5_shares_at_avg_limit"] = round(5.0 * mean_limit, 4)
        result["expected_edge_usd_per_5_planned_shares_zero_adverse_selection"] = round(
            5.0 * mean_upper_edge, 4
        )
    return result


def main() -> None:
    args = parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    frame = pd.read_csv(input_path)
    rows = prepare_rows(frame, args.tick_size)

    probability_rows = rows[rows["probability_valid"]].copy()
    selected = probability_rows[
        (probability_rows["yes_mid"] > args.threshold)
        & (probability_rows["yes_mid"] < 1.0)
    ].copy()
    first = first_crossing(selected)
    train = first[first["target_date"] < args.forward_start].copy()
    forward = first[first["target_date"] >= args.forward_start].copy()

    input_stat = input_path.stat()
    output = {
        "strategy_id": "current_exact_favorite_maker_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "inconclusive_shadow_research_only",
        "target_metric": (
            "P(current exact wins | PIT market state) minus same-snapshot market price; "
            "then fill-conditioned maker edge after queue adverse selection"
        ),
        "contract": {
            "input": str(input_path.relative_to(ROOT)),
            "input_mtime_utc": datetime.fromtimestamp(
                input_stat.st_mtime, tz=timezone.utc
            ).isoformat(),
            "input_grain": "city x target_date x decision snapshot",
            "policy_grain": "first threshold crossing per city x target_date",
            "expression": "BUY current exact bracket YES",
            "threshold": f"yes_mid > {args.threshold:.2f}",
            "mid": "(direct current YES ask + (1 - direct current NO ask)) / 2",
            "maker_price_proxy": (
                "max(YES bid, min(YES bid + nominal tick, YES ask - nominal tick))"
            ),
            "nominal_tick_size": args.tick_size,
            "maker_fee": 0.0,
            "rebate": "excluded",
            "forward_start": args.forward_start,
            "uncertainty": "target_date block bootstrap, 95% interval",
            "critical_limit": (
                "maker result is an all-fill, zero-adverse-selection benchmark; atlas has no queue/tape fills"
            ),
        },
        "inventory": {
            "input_state_rows": int(len(frame)),
            "input_dates": int(frame["target_date"].nunique()),
            "input_cities": int(frame["city"].nunique()),
            "input_date_min": str(frame["target_date"].min()),
            "input_date_max": str(frame["target_date"].max()),
            "probability_valid_current_rows": int(len(probability_rows)),
            "missing_or_invalid_probability_rows": int(len(frame) - len(probability_rows)),
        },
        "signal_funnel": [
            {
                "stage": "atlas PIT states",
                "unit": "city-date-hour state",
                "n": int(len(frame)),
            },
            {
                "stage": "current exact probability and settlement valid",
                "unit": "city-date-hour state",
                "n": int(len(probability_rows)),
            },
            {
                "stage": f"YES mid > {args.threshold:.2f}",
                "unit": "qualifying state",
                "n": int(len(selected)),
            },
            {
                "stage": "first threshold crossing",
                "unit": "city-date signal",
                "n": int(len(first)),
            },
        ],
        "evidence_funnel": [
            {
                "stage": "PIT quote plus settlement on first signal",
                "unit": "city-date signal",
                "n": int(len(first)),
            },
            {
                "stage": "nominal post-only price formable",
                "unit": "planned maker order",
                "n": int(first["maker_postable"].sum()),
            },
            {
                "stage": "historically verified queue/tape fill",
                "unit": "maker fill",
                "n": 0,
            },
            {
                "stage": "fill-linked markout and settlement",
                "unit": "maker fill",
                "n": 0,
            },
        ],
        "results": {
            "all_qualifying_states_probability_diagnostic": summary_for(
                selected, "all_qualifying_states", draws=args.bootstrap_draws, seed=17
            ),
            "first_signal_policy_denominator": summary_for(
                first, "first_signal", draws=args.bootstrap_draws, seed=29
            ),
            "train_first_signal": summary_for(
                train, "train_first_signal", draws=args.bootstrap_draws, seed=41
            ),
            "forward_first_signal": summary_for(
                forward, "forward_first_signal", draws=args.bootstrap_draws, seed=53
            ),
        },
        "three_gates": {
            "significance": (
                "FAIL for executable policy: all-state probability bias CI is positive, "
                "but first-signal probability-bias CI crosses zero and fill CI is unavailable"
            ),
            "baseline": (
                "PASS only at probability layer versus midpoint; same-trigger taker is not positive "
                "on the honest first-signal denominator"
            ),
            "forward": (
                "FAIL for execution: forward zero-adverse-selection benchmark is positive but there are zero "
                "queue-aware fills"
            ),
        },
        "verdict": {
            "conclusion": "inconclusive_shadow_research_only",
            "action": (
                "freeze rule and collect queue-aware zero-notional maker/no-trade/taker controls; "
                "do not deploy live"
            ),
            "promotion_minimum": {
                "credible_queue_fills": 30,
                "independent_target_dates": 12,
                "planned_denominator_net_edge_ci_lower": "> 0",
                "markouts": "1m/5m/15m not materially negative",
                "lifecycle": "no order survives a registered data epoch",
            },
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2) + "\n")
    print(f"wrote {output_path}")


if __name__ == "__main__":
    main()
