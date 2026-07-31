from __future__ import annotations

import json

import numpy as np

from scripts.analysis.market_structure_edge import (
    research_tokyo_current_break_binary_v5 as binary,
)


def test_binary_temperature_preserves_probability_simplex() -> None:
    values = np.asarray([0.01, 0.25, 0.5, 0.9])

    calibrated = binary.apply_binary_temperature(values, 1.2)

    assert calibrated.shape == values.shape
    assert np.all((calibrated > 0) & (calibrated < 1))
    assert calibrated[2] == 0.5


def test_binary_loss_uses_break_current_label() -> None:
    rows = [
        {"target_date": "2026-07-20", "binary_leave_current": 0},
        {"target_date": "2026-07-21", "binary_leave_current": 1},
    ]

    perfect = binary.date_equal_binary_loss(
        rows, np.asarray([0.0, 1.0]), metric="brier"
    )
    inverted = binary.date_equal_binary_loss(
        rows, np.asarray([1.0, 0.0]), metric="brier"
    )

    assert perfect < 1e-12
    assert inverted > 0.999999


def test_model_features_exclude_end_of_day_labels() -> None:
    forbidden = {
        "final_metar_max_c",
        "final_metar_rounded_c",
        "final_bracket",
        "remaining_rise_class",
        "binary_leave_current",
    }

    assert forbidden.isdisjoint(binary.v1.MODEL_FEATURES)


def test_current_contract_candidates_exclude_next_exact() -> None:
    joined = [
        {
            "state_id": "Tokyo:2026-07-20:state",
            "target_date": "2026-07-20",
            "current_bracket": 31,
            "winning_bracket": "32",
            "actual_delta": 1,
            "settlement_lower_bound_violation": 0,
            "quotes_json": json.dumps(
                {
                    "31": {"ask": 0.4, "bid": 0.35, "mid": 0.375},
                    "32": {"ask": 0.5, "bid": 0.45, "mid": 0.475},
                }
            ),
            "binary_checkpoint_hgb_v5_distribution_json": json.dumps(
                [0.3, 0.7]
            ),
            "binary_multigrain_hgb_v5_distribution_json": json.dumps(
                [0.25, 0.75]
            ),
        }
    ]

    candidates = binary.current_contract_candidates(joined)

    assert len(candidates) == 4
    assert {row["expression_bracket"] for row in candidates} == {"31"}
    assert {row["expression_delta"] for row in candidates} == {0}
    checkpoint = [
        row
        for row in candidates
        if row["model"] == "binary_checkpoint_hgb_v5"
    ]
    assert {row["side"] for row in checkpoint} == {"YES", "NO"}
    assert {
        row["side"]: row["p_win"] for row in checkpoint
    } == {"YES": 0.3, "NO": 0.7}
