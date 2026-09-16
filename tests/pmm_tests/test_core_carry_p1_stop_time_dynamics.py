from __future__ import annotations

import numpy as np

from scripts.analysis.reheat_risk import core_carry_p1_stop_time_dynamics as p1


def test_weighted_simplex_projection_is_nonnegative_and_conserves_mass() -> None:
    projected = p1.weighted_simplex_projection(
        [0.05, 0.30, 0.52, 0.21],
        [0.02, 0.03, 0.02, 0.10],
    )

    assert np.all(projected >= 0)
    np.testing.assert_allclose(projected.sum(), 1.0, atol=1e-10)


def test_ladder_state_reports_q_up_q1_and_alpha1() -> None:
    batch = {
        "source_path": "fixture.jsonl",
        "available_at_utc": "2026-08-01T04:00:00Z",
        "rungs": [
            {"bracket": "28", "coordinate": 28, "midpoint": 0.05, "spread": 0.02, "direct_two_sided": True, "token_id": "t28"},
            {"bracket": "29", "coordinate": 29, "midpoint": 0.20, "spread": 0.02, "direct_two_sided": True, "token_id": "t29"},
            {"bracket": "30", "coordinate": 30, "midpoint": 0.55, "spread": 0.02, "direct_two_sided": True, "token_id": "t30"},
            {"bracket": "31+", "coordinate": 31, "midpoint": 0.20, "spread": 0.02, "direct_two_sided": True, "token_id": "t31"},
        ],
    }

    state = p1.ladder_state(batch, "29")

    assert state["status"] == "scorable"
    np.testing.assert_allclose(state["q_up"], 0.75, atol=1e-8)
    np.testing.assert_allclose(state["q1"], 0.55, atol=1e-8)
    np.testing.assert_allclose(state["alpha1"], 0.55 / 0.75, atol=1e-8)


def test_quote_driven_definition_requires_old_cost_counterfactual_to_fail() -> None:
    artifact = {
        "numeric_features": ["market_logit", "decision_hour_local", "dewpoint_depression_f", "wind_speed_kt"],
        "numeric_medians": [1.0, 14.0, 10.0, 8.0],
        "numeric_means": [0.0, 14.0, 10.0, 8.0],
        "numeric_scales": [1.0, 1.0, 1.0, 1.0],
        "coef": [0.5, 0.0, 0.0, 0.0],
        "intercept": 0.0,
    }
    prior = {
        "created_at_utc": "2026-08-01T03:30:00Z",
        "artifact_hash": "a",
        "model_probability_hold": 0.80,
        "current_yes_bid": 0.82,
        "current_yes_ask": 0.84,
        "taker_ladder": {"effective_cost_per_share": 0.85},
        "decision_hour_local": 14,
        "dewpoint_depression_f": 10,
        "wind_speed_kt": 8,
        "eligible": False,
    }
    trigger = {
        **prior,
        "created_at_utc": "2026-08-01T04:30:00Z",
        "model_probability_hold": 0.84,
        "current_yes_bid": 0.78,
        "current_yes_ask": 0.80,
        "taker_ladder": {"effective_cost_per_share": 0.81},
        "eligible": True,
    }

    row = p1.decompose_signal(
        {"signal_id": "s", "city": "Busan", "target_date": "2026-08-01", "bracket_canonical": "30"},
        trigger,
        prior,
        prior,
        artifact,
    )

    assert row["decomposition_status"] == "scorable"
    assert row["quote_driven_candidate"] is True
    assert abs(row["decomposition_residual"]) < 1e-10
