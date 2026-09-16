from __future__ import annotations

import pandas as pd

from scripts.analysis.reheat_risk import (
    research_current_yes_core_carry_overshoot_risk_sizing_overlay_v1 as overlay,
)


def _states() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "city": "TestCity",
                "target_date": "2026-07-01",
                "decision_snapshot_dt": pd.Timestamp("2026-07-01T12:30:00Z"),
                "p_core_no_obs_age": 0.90,
                "p_hold_adjusted": 0.90,
                "five_share_cost_per_share": 0.85,
                "ten_share_cost_per_share": 0.92,
                "kelly_scale_train": 0.10,
                "edge_q33_train": 0.01,
                "edge_q67_train": 0.03,
            },
            {
                "city": "TestCity",
                "target_date": "2026-07-01",
                "decision_snapshot_dt": pd.Timestamp("2026-07-01T13:30:00Z"),
                "p_core_no_obs_age": 0.90,
                "p_hold_adjusted": 0.91,
                "five_share_cost_per_share": 0.85,
                "ten_share_cost_per_share": 0.88,
                "kelly_scale_train": 0.10,
                "edge_q33_train": 0.01,
                "edge_q67_train": 0.03,
            },
        ]
    )


def test_current_baseline_uses_full_ten_share_entry_cost() -> None:
    policies = overlay.policy_frames(
        _states(), actual_taker_shares=10.0, actual_maker_shares=5.0
    )

    baseline = policies[overlay.POLICIES[0]]
    probability_only = policies[overlay.POLICIES[1]]
    assert baseline["decision_snapshot_dt"].tolist() == [
        pd.Timestamp("2026-07-01T13:30:00Z")
    ]
    assert probability_only["decision_snapshot_dt"].tolist() == [
        pd.Timestamp("2026-07-01T13:30:00Z")
    ]
    assert baseline["taker_shares_desired"].tolist() == [10.0]
    assert baseline["maker_shares_desired"].tolist() == [5.0]


def test_challenger_sizes_respect_fifteen_share_cap() -> None:
    policies = overlay.policy_frames(
        _states(), actual_taker_shares=10.0, actual_maker_shares=5.0
    )

    for policy in overlay.POLICIES[2:]:
        frame = policies[policy]
        total = (
            frame["taker_shares_desired"]
            + frame["maker_shares_desired"]
        )
        assert total.le(15.0).all()
        assert frame["taker_shares_desired"].ge(0).all()
        assert frame["maker_shares_desired"].ge(0).all()


def test_adverse_maker_scenario_preserves_observed_fill_rate() -> None:
    selected = pd.DataFrame({"label": [1] * 8 + [0] * 2})

    q_win, q_loss = overlay.maker_probabilities(
        "adverse_same_overall_rate", selected, observed_rate=0.5
    )

    expected_fills = 8 * q_win + 2 * q_loss
    assert q_loss == 1.0
    assert expected_fills == 5.0
