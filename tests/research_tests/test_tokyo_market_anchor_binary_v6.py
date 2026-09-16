from __future__ import annotations

import json
import math

import numpy as np

from scripts.analysis.market_structure_edge import (
    research_tokyo_market_anchor_binary_v6 as v6,
)


def test_zero_offset_correction_returns_market_probability() -> None:
    probability = 0.07
    artifact = {
        "features": ["weather_market_logit_gap"],
        "median": [0.0],
        "mean": [0.0],
        "scale": [1.0],
        "beta": [0.0, 0.0],
    }

    predicted = v6.predict_offset(
        artifact,
        [
            {
                "market_logit": v6.logit(probability),
                "weather_market_logit_gap": 4.0,
            }
        ],
    )

    assert np.isclose(predicted[0], probability)


def test_expression_rows_keep_low_price_as_telemetry() -> None:
    rows = [
        {
            "state_id": "Tokyo:2026-07-23:state",
            "target_date": "2026-07-23",
            "current_bracket": "34",
            "winning_bracket": "35",
            "quotes_json": json.dumps(
                {"34": {"ask": 0.001, "bid": 0.001, "mid": 0.001}}
            ),
            "y_stay": 0,
            "p_market_prior_v6": 0.001,
        }
    ]

    expressions = v6.expression_rows(rows, ["market_prior_v6"])

    assert len(expressions) == 2
    yes = next(row for row in expressions if row["side"] == "YES")
    assert yes["selected_side_ask"] == 0.001
    assert yes["expression_bracket"] == "34"
    assert yes["expression_delta"] == 0


def test_champion_requires_brier_and_logloss_improvement() -> None:
    base = {
        "split": "expanding_oof_after_five_market_dates",
        "grain": "checkpoint",
    }
    scores = [
        {**base, "model": "market_prior_v6", "brier": 0.02, "logloss": 0.08},
        {
            **base,
            "model": "offset_compact_ridge1_v6",
            "brier": 0.01,
            "logloss": 0.09,
        },
        {
            **base,
            "model": "offset_physical_ridge1_v6",
            "brier": 0.015,
            "logloss": 0.07,
        },
    ]

    assert v6.choose_champion(scores) == "offset_physical_ridge1_v6"


def test_v6_features_exclude_labels_and_future_outcomes() -> None:
    forbidden = {
        "final_metar_max_c",
        "final_metar_rounded_c",
        "final_bracket",
        "remaining_rise_class",
        "binary_leave_current",
        "winning_bracket",
        "y_stay",
    }

    assert forbidden.isdisjoint(v6.PHYSICAL_FEATURES)
    assert math.isfinite(v6.logit(0.0))
    assert math.isfinite(v6.logit(1.0))
