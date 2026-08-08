from __future__ import annotations

import pandas as pd

from src.strategies.weather_edge_v1.research import (
    core_carry_mid_floor_forward as study,
)


def test_lower_floor_changes_only_first_positive_selection() -> None:
    rows = pd.DataFrame(
        [
            {
                "city": "X",
                "target_date": "2026-08-01",
                "decision_snapshot_dt": pd.Timestamp("2026-08-01T01:30:00Z"),
                "market_mid": 0.76,
                "model_probability_hold": 0.84,
                "current_yes_effective_cost": 0.80,
                "edge_after_cost": 0.04,
            },
            {
                "city": "X",
                "target_date": "2026-08-01",
                "decision_snapshot_dt": pd.Timestamp("2026-08-01T02:30:00Z"),
                "market_mid": 0.82,
                "model_probability_hold": 0.88,
                "current_yes_effective_cost": 0.85,
                "edge_after_cost": 0.03,
            },
            {
                "city": "Y",
                "target_date": "2026-08-01",
                "decision_snapshot_dt": pd.Timestamp("2026-08-01T02:30:00Z"),
                "market_mid": 0.79,
                "model_probability_hold": 0.80,
                "current_yes_effective_cost": 0.81,
                "edge_after_cost": -0.01,
            },
        ]
    )

    floor_075 = study.select_first_positive(rows, floor=0.75, ceiling=0.9895)
    floor_080 = study.select_first_positive(rows, floor=0.80, ceiling=0.9895)

    assert len(floor_075) == len(floor_080) == 1
    assert float(floor_075.iloc[0]["market_mid"]) == 0.76
    assert float(floor_080.iloc[0]["market_mid"]) == 0.82


def test_exact_bounded_excludes_open_ended_brackets() -> None:
    rows = pd.DataFrame(
        [
            {"current_bracket": "31", "current_question": "Exactly 31 C?"},
            {"current_bracket": "32+", "current_question": "32 C or higher?"},
            {"current_bracket": "20", "current_question": "20 C or below?"},
        ]
    )

    assert study.exact_bounded(rows).tolist() == [True, False, False]
