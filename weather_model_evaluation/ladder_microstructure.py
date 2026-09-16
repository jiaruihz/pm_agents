"""Reusable exact-ladder structure features for city probability research.

The feature clock is the caller-supplied PIT book checkpoint.  The helpers keep
all event-rung rows: edge rungs and the first checkpoint of a date/source carry
missing neighbor/dynamic values for the model pipeline to impute, rather than
silently shrinking the denominator.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


EPSILON = 1e-5
EVENT_KEYS = ["target_date", "event_source", "event_id"]

LADDER_STATIC_FEATURES = [
    "signed_mode_distance",
    "left_neighbor_log_ratio",
    "right_neighbor_log_ratio",
    "neighbor_curvature",
]

LADDER_DYNAMIC_FEATURES = [
    "weather_shock",
    "rung_relative_markout",
    "neighbor_propagation",
    "neighbor_lead_lag",
    "shock_x_mode_distance",
    "shock_x_neighbor_propagation",
    "shock_x_mode_x_neighbor_propagation",
]

LADDER_FEATURES = LADDER_STATIC_FEATURES + LADDER_DYNAMIC_FEATURES


def add_ladder_microstructure_features(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Add mode-relative, neighbor, and checkpoint-transition features.

    ``rung_relative_markout`` is the rung YES-probability move from the prior
    checkpoint minus the median move of that ladder. ``neighbor_propagation``
    is the mean relative move of the immediately adjacent rungs.  No max/min
    selector, validation-derived city rule, or execution eligibility filter is
    introduced here.
    """

    required = {
        "target_date",
        "event_source",
        "event_id",
        "event_decision_ts_utc",
        "quote_ts_utc",
        "bracket",
        "market_no_probability",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"missing ladder feature columns: {missing}")

    work = frame.copy()
    work["event_source"] = work["event_source"].astype(str).str.lower()
    work["event_decision_ts_utc"] = pd.to_datetime(
        work["event_decision_ts_utc"], utc=True, errors="coerce", format="mixed"
    )
    work["quote_ts_utc"] = pd.to_datetime(
        work["quote_ts_utc"], utc=True, errors="coerce", format="mixed"
    )
    work["market_yes_probability"] = 1.0 - pd.to_numeric(
        work["market_no_probability"], errors="coerce"
    )

    if "bracket_value" in work.columns:
        work["_native_bracket_value"] = pd.to_numeric(
            work["bracket_value"], errors="coerce"
        )
    else:
        work["_native_bracket_value"] = pd.to_numeric(
            work["bracket"], errors="coerce"
        )

    # Stable rung order is preferable to treating native temperature distance
    # as universal: cities may use different units or tail labels.
    work = work.sort_values(
        EVENT_KEYS
        + ["_native_bracket_value", "bracket", "quote_ts_utc"],
        kind="mergesort",
        na_position="last",
    ).reset_index(drop=True)
    event_group = work.groupby(EVENT_KEYS, sort=False, dropna=False)
    work["ladder_rank"] = event_group.cumcount().astype(float)

    mode_indexes = event_group["market_yes_probability"].idxmax()
    modes = work.loc[mode_indexes, EVENT_KEYS + ["ladder_rank"]].rename(
        columns={"ladder_rank": "market_mode_rank"}
    )
    work = work.merge(modes, on=EVENT_KEYS, how="left", validate="many_to_one")
    work["signed_mode_distance"] = work["ladder_rank"] - work["market_mode_rank"]

    event_group = work.groupby(EVENT_KEYS, sort=False, dropna=False)
    center = work["market_yes_probability"]
    left = event_group["market_yes_probability"].shift(1)
    right = event_group["market_yes_probability"].shift(-1)
    work["left_neighbor_log_ratio"] = np.log(
        (left.clip(lower=0.0) + EPSILON) / (center.clip(lower=0.0) + EPSILON)
    )
    work["right_neighbor_log_ratio"] = np.log(
        (right.clip(lower=0.0) + EPSILON) / (center.clip(lower=0.0) + EPSILON)
    )
    work["neighbor_curvature"] = (left + right) / 2.0 - center

    # Previous checkpoint is source-local and date-local.  It is never sourced
    # from a later event and therefore remains PIT at the current decision.
    work = work.sort_values(
        [
            "target_date",
            "event_source",
            "bracket",
            "event_decision_ts_utc",
            "quote_ts_utc",
            "event_id",
        ],
        kind="mergesort",
    )
    rung_group = work.groupby(
        ["target_date", "event_source", "bracket"],
        sort=False,
        dropna=False,
    )
    work["prior_checkpoint_yes_probability"] = rung_group[
        "market_yes_probability"
    ].shift(1)
    work["rung_markout"] = (
        work["market_yes_probability"] - work["prior_checkpoint_yes_probability"]
    )
    event_group = work.groupby(EVENT_KEYS, sort=False, dropna=False)
    work["ladder_median_markout"] = event_group["rung_markout"].transform("median")
    work["rung_relative_markout"] = (
        work["rung_markout"] - work["ladder_median_markout"]
    )

    work = work.sort_values(
        EVENT_KEYS + ["ladder_rank", "quote_ts_utc"], kind="mergesort"
    )
    event_group = work.groupby(EVENT_KEYS, sort=False, dropna=False)
    left_relative = event_group["rung_relative_markout"].shift(1)
    right_relative = event_group["rung_relative_markout"].shift(-1)
    work["neighbor_propagation"] = pd.concat(
        [left_relative, right_relative], axis=1
    ).mean(axis=1, skipna=True)
    work["neighbor_lead_lag"] = (
        work["neighbor_propagation"] - work["rung_relative_markout"]
    )

    work["weather_shock"] = np.nan
    if "current_x" in work.columns:
        event_state = (
            work[EVENT_KEYS + ["event_decision_ts_utc", "quote_ts_utc", "current_x"]]
            .drop_duplicates(EVENT_KEYS, keep="first")
            .sort_values(
                [
                    "target_date",
                    "event_source",
                    "event_decision_ts_utc",
                    "quote_ts_utc",
                    "event_id",
                ],
                kind="mergesort",
            )
        )
        event_state["current_x"] = pd.to_numeric(
            event_state["current_x"], errors="coerce"
        )
        event_state["weather_shock"] = event_state.groupby(
            ["target_date", "event_source"], sort=False, dropna=False
        )["current_x"].diff()
        work = work.drop(columns=["weather_shock"]).merge(
            event_state[EVENT_KEYS + ["weather_shock"]],
            on=EVENT_KEYS,
            how="left",
            validate="many_to_one",
        )

    work["shock_x_mode_distance"] = (
        work["weather_shock"] * work["signed_mode_distance"]
    )
    work["shock_x_neighbor_propagation"] = (
        work["weather_shock"] * work["neighbor_propagation"]
    )
    work["shock_x_mode_x_neighbor_propagation"] = (
        work["weather_shock"]
        * work["signed_mode_distance"]
        * work["neighbor_propagation"]
    )

    event_count = int(work["event_id"].nunique())
    coverage = {
        "feature_contract": "weather_ladder_microstructure_v1",
        "feature_clock": "decision_current_periodic_checkpoint",
        "rows": int(len(work)),
        "events": event_count,
        "target_dates": int(work["target_date"].nunique()),
        "static_complete_rows": int(work[LADDER_STATIC_FEATURES].notna().all(axis=1).sum()),
        "dynamic_complete_rows": int(work[LADDER_DYNAMIC_FEATURES].notna().all(axis=1).sum()),
        "events_with_prior_checkpoint": int(
            work.loc[work["rung_relative_markout"].notna(), "event_id"].nunique()
        ),
        "events_with_weather_shock": int(
            work.loc[work["weather_shock"].notna(), "event_id"].nunique()
        ),
        "denominator_policy": "retain all rows; missing edge/first-checkpoint features are imputed inside each training fold",
        "not_claimed": [
            "exact first-seen WS dynamics",
            "trade prints or queue position",
            "max/min rung selector",
        ],
    }
    return work.drop(columns=["_native_bracket_value"]), coverage
