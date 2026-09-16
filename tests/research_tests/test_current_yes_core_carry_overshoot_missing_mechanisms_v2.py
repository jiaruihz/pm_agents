from __future__ import annotations

import pandas as pd

from scripts.analysis.reheat_risk import (
    research_current_yes_core_carry_overshoot_missing_mechanisms_v2 as research,
)


def test_native_lattice_geometry_uses_settlement_exit_boundary() -> None:
    celsius = research.lattice_geometry(
        pd.Series(
            {
                "current_bracket": "23",
                "unit": "C",
                "running_native": 22.7777777778,
            }
        )
    )
    fahrenheit = research.lattice_geometry(
        pd.Series(
            {
                "current_bracket": "68-69",
                "unit": "F",
                "running_native": 69.0,
            }
        )
    )

    assert celsius["exit_threshold_native"] == 23.5
    assert celsius["exit_ticks_required"] == 2.0
    assert fahrenheit["exit_threshold_native"] == 70.0
    assert fahrenheit["exit_ticks_required"] == 1.0


def test_carry_target_direction_distinguishes_upward_from_downward_leave() -> None:
    assert research.direction_from_brackets("68-69", "68-69") == "current_exact_hold"
    assert research.direction_from_brackets("68-69", "70-71") == "upward_leave"
    assert research.direction_from_brackets("68-69", "66-67") == "downward_leave"


def test_offset_model_preserves_frozen_core_when_feature_has_no_variation() -> None:
    train = pd.DataFrame(
        {
            "city": ["A", "B"],
            "target_date": ["2026-07-01", "2026-07-01"],
            "overshoot": [0, 1],
            "p_over_core": [0.1, 0.2],
            "base_offset_logit": [-2.197224577, -1.386294361],
            "constant": [1.0, 1.0],
        }
    )
    test = pd.DataFrame(
        {
            "city": ["C"],
            "target_date": ["2026-07-02"],
            "overshoot": [0],
            "p_over_core": [0.3],
            "base_offset_logit": [-0.8472978604],
            "constant": [1.0],
        }
    )

    probabilities, fit = research.fit_offset_residual(
        train, test, ["constant"]
    )

    assert probabilities.tolist() == [0.3]
    assert fit["coefficient_norm"] == 0.0


def test_mechanism_categories_are_mutually_exclusive_and_ordered() -> None:
    base = {
        "assigned_forecast_exit_margin_ticks": -1.0,
        "dual_forecast_max_margin_ticks": -1.0,
        "trend_1h_per_exit_tick": -0.1,
    }
    assert (
        research.mechanism_category(
            pd.Series(
                {
                    **base,
                    "assigned_forecast_exit_margin_ticks": 0.0,
                    "dual_forecast_max_margin_ticks": 2.0,
                    "trend_1h_per_exit_tick": 1.0,
                }
            )
        )
        == "assigned_forecast_cross"
    )
    assert (
        research.mechanism_category(
            pd.Series({**base, "dual_forecast_max_margin_ticks": 0.0})
        )
        == "alternate_forecast_cross_only"
    )
    assert (
        research.mechanism_category(
            pd.Series({**base, "trend_1h_per_exit_tick": 0.1})
        )
        == "fresh_path_continuation_only"
    )
    assert research.mechanism_category(pd.Series(base)) == "no_mechanism_warning"
