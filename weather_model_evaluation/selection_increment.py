"""Paired policy increment on one fixed executable opportunity universe.

Selected ROI is a ratio on a policy-created denominator.  It cannot answer
whether a probability model added value because two selectors can deploy
different capital or enter at different checkpoints.  These helpers keep the
opportunity universe fixed, score no-trade as zero, and bootstrap paired daily
increments across whole target dates.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd


def _require_columns(frame: pd.DataFrame, columns: Sequence[str]) -> None:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"missing required columns: {missing}")


def select_first_positive_policy(
    frame: pd.DataFrame,
    *,
    probability_column: str,
    cost_column: str,
    label_column: str = "label",
    time_column: str = "decision_ts_utc",
    group_columns: Sequence[str] = ("city", "target_date"),
    fee_column: str | None = None,
) -> pd.DataFrame:
    """Select the first positive-edge checkpoint per fixed policy group."""

    required = [
        probability_column,
        cost_column,
        label_column,
        time_column,
        *group_columns,
    ]
    if fee_column:
        required.append(fee_column)
    _require_columns(frame, required)
    work = frame.copy()
    work[time_column] = pd.to_datetime(work[time_column], utc=True, errors="raise")
    probability = pd.to_numeric(work[probability_column], errors="coerce")
    raw_cost = pd.to_numeric(work[cost_column], errors="coerce")
    fee = (
        pd.to_numeric(work[fee_column], errors="coerce")
        if fee_column
        else pd.Series(0.0, index=work.index)
    )
    effective_cost = raw_cost + fee
    eligible = (
        probability.notna()
        & effective_cost.gt(0.0)
        & effective_cost.le(1.0)
        & probability.gt(effective_cost)
    )
    selected = work.loc[eligible].copy()
    selected["policy_probability"] = probability.loc[selected.index]
    selected["raw_cost_per_share"] = raw_cost.loc[selected.index]
    selected["fee_per_share"] = fee.loc[selected.index]
    selected["cost_per_share"] = effective_cost.loc[selected.index]
    selected["pnl_per_share"] = (
        pd.to_numeric(selected[label_column], errors="raise")
        - selected["cost_per_share"]
    )
    selected["capital_return"] = (
        selected["pnl_per_share"] / selected["cost_per_share"]
    )
    return (
        selected.sort_values([*group_columns, time_column], kind="stable")
        .drop_duplicates(list(group_columns), keep="first")
        .reset_index(drop=True)
    )


def _policy_summary(
    selected: pd.DataFrame, *, date_column: str, label_column: str
) -> dict[str, Any]:
    if selected.empty:
        return {
            "entries": 0,
            "target_dates": 0,
            "wins": 0,
            "losses": 0,
            "cost_per_share": 0.0,
            "pnl_per_share": 0.0,
            "selected_roi": None,
        }
    cost = float(selected["cost_per_share"].sum())
    pnl = float(selected["pnl_per_share"].sum())
    wins = int(pd.to_numeric(selected[label_column], errors="raise").sum())
    return {
        "entries": int(len(selected)),
        "target_dates": int(selected[date_column].astype(str).nunique()),
        "wins": wins,
        "losses": int(len(selected) - wins),
        "cost_per_share": cost,
        "pnl_per_share": pnl,
        "selected_roi": pnl / cost if cost > 0 else None,
    }


def _bootstrap_daily_mean(
    values: np.ndarray,
    *,
    draws: int,
    seed: int,
) -> list[float | None]:
    if len(values) == 0:
        return [None, None]
    rng = np.random.default_rng(seed)
    sampled = values[
        rng.integers(0, len(values), size=(int(draws), len(values)))
    ].mean(axis=1)
    low, high = np.quantile(sampled, [0.025, 0.975])
    return [float(low), float(high)]


def paired_policy_increment(
    frame: pd.DataFrame,
    *,
    challenger_probability_column: str,
    baseline_probability_column: str,
    cost_column: str,
    label_column: str = "label",
    time_column: str = "decision_ts_utc",
    date_column: str = "target_date",
    group_columns: Sequence[str] = ("city", "target_date"),
    opportunity_id_column: str = "opportunity_id",
    fee_column: str | None = None,
    bootstrap_draws: int = 5_000,
    bootstrap_seed: int = 0,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Compare two frozen selectors as paired actions on one universe."""

    required = [
        challenger_probability_column,
        baseline_probability_column,
        cost_column,
        label_column,
        time_column,
        date_column,
        opportunity_id_column,
        *group_columns,
    ]
    _require_columns(frame, required)
    if frame.empty:
        raise ValueError("fixed executable opportunity universe must not be empty")
    universe = frame.copy()
    universe[date_column] = universe[date_column].astype(str)
    if universe[opportunity_id_column].astype(str).duplicated().any():
        raise ValueError(f"duplicate {opportunity_id_column} in fixed universe")
    if date_column not in group_columns:
        raise ValueError("date_column must be part of group_columns")

    challenger = select_first_positive_policy(
        universe,
        probability_column=challenger_probability_column,
        cost_column=cost_column,
        label_column=label_column,
        time_column=time_column,
        group_columns=group_columns,
        fee_column=fee_column,
    )
    baseline = select_first_positive_policy(
        universe,
        probability_column=baseline_probability_column,
        cost_column=cost_column,
        label_column=label_column,
        time_column=time_column,
        group_columns=group_columns,
        fee_column=fee_column,
    )

    keep = [
        *group_columns,
        opportunity_id_column,
        time_column,
        label_column,
        "cost_per_share",
        "pnl_per_share",
        "capital_return",
    ]
    paired = challenger[keep].merge(
        baseline[keep],
        on=list(group_columns),
        how="outer",
        suffixes=("_challenger", "_baseline"),
        validate="one_to_one",
    )
    for metric in ("cost_per_share", "pnl_per_share", "capital_return"):
        for side in ("challenger", "baseline"):
            column = f"{metric}_{side}"
            paired[column] = pd.to_numeric(paired[column], errors="coerce").fillna(0.0)
        paired[f"delta_{metric}"] = (
            paired[f"{metric}_challenger"] - paired[f"{metric}_baseline"]
        )
    challenger_present = paired[f"{opportunity_id_column}_challenger"].notna()
    baseline_present = paired[f"{opportunity_id_column}_baseline"].notna()
    same = (
        challenger_present
        & baseline_present
        & paired[f"{opportunity_id_column}_challenger"].astype(str).eq(
            paired[f"{opportunity_id_column}_baseline"].astype(str)
        )
    )
    paired["selection_relation"] = np.select(
        [
            same,
            challenger_present & baseline_present,
            challenger_present,
            baseline_present,
        ],
        ["same", "switched", "challenger_only", "baseline_only"],
        default="neither",
    )

    date_groups = (
        universe[list(group_columns)]
        .drop_duplicates()
        .groupby(date_column, sort=True)
        .size()
        .rename("fixed_groups")
    )
    date_opportunities = (
        universe.groupby(date_column, sort=True)
        .size()
        .rename("fixed_opportunities")
    )
    daily = paired.groupby(date_column, sort=True)[
        [
            "pnl_per_share_challenger",
            "pnl_per_share_baseline",
            "cost_per_share_challenger",
            "cost_per_share_baseline",
            "delta_pnl_per_share",
            "delta_cost_per_share",
            "delta_capital_return",
        ]
    ].sum()
    daily = (
        date_opportunities.to_frame()
        .join(date_groups, how="left")
        .join(daily, how="left")
        .fillna(0.0)
    )
    daily["fixed_universe_return_challenger"] = (
        daily["pnl_per_share_challenger"] / daily["fixed_opportunities"]
    )
    daily["fixed_universe_return_baseline"] = (
        daily["pnl_per_share_baseline"] / daily["fixed_opportunities"]
    )
    daily["delta_fixed_universe_return"] = (
        daily["fixed_universe_return_challenger"]
        - daily["fixed_universe_return_baseline"]
    )

    relation_counts = paired["selection_relation"].value_counts().to_dict()
    report = {
        "contract": "paired_selection_increment_v1",
        "fixed_universe": {
            "opportunities": int(len(universe)),
            "policy_groups": int(
                universe[list(group_columns)].drop_duplicates().shape[0]
            ),
            "target_dates": int(universe[date_column].nunique()),
        },
        "challenger": _policy_summary(
            challenger, date_column=date_column, label_column=label_column
        ),
        "baseline": _policy_summary(
            baseline, date_column=date_column, label_column=label_column
        ),
        "selection_relation": {
            "same": int(relation_counts.get("same", 0)),
            "switched": int(relation_counts.get("switched", 0)),
            "challenger_only": int(relation_counts.get("challenger_only", 0)),
            "baseline_only": int(relation_counts.get("baseline_only", 0)),
        },
        "paired_increment": {
            "pnl_per_share": float(daily["delta_pnl_per_share"].sum()),
            "cost_per_share": float(daily["delta_cost_per_share"].sum()),
            "date_equal_mean_pnl_per_share": float(
                daily["delta_pnl_per_share"].mean()
            ),
            "date_equal_mean_pnl_ci95": _bootstrap_daily_mean(
                daily["delta_pnl_per_share"].to_numpy(float),
                draws=bootstrap_draws,
                seed=bootstrap_seed,
            ),
            "date_equal_fixed_universe_return": float(
                daily["delta_fixed_universe_return"].mean()
            ),
            "date_equal_fixed_universe_return_ci95": _bootstrap_daily_mean(
                daily["delta_fixed_universe_return"].to_numpy(float),
                draws=bootstrap_draws,
                seed=bootstrap_seed + 1,
            ),
            "interpretation": (
                "primary paired increment uses every fixed-universe target date; "
                "policy-specific selected_roi is descriptive only"
            ),
        },
        "daily": daily.reset_index().to_dict("records"),
    }
    return report, paired.sort_values(list(group_columns), kind="stable").reset_index(
        drop=True
    )
