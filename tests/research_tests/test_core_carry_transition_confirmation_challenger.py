from __future__ import annotations

import importlib.util
import numpy as np
import pandas as pd
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / (
    "scripts/analysis/reheat_risk/"
    "core_carry_transition_confirmation_challenger.py"
)
SPEC = importlib.util.spec_from_file_location(
    "core_carry_transition_confirmation_challenger", SCRIPT
)
assert SPEC and SPEC.loader
challenger = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(challenger)


def test_upper_exit_geometry_uses_settlement_native_lattice() -> None:
    # 23C exits upward only at the integer-F observation corresponding to 24C.
    celsius = challenger.upper_exit_geometry("C", "23", 22.7777777778, 24.0)
    fahrenheit = challenger.upper_exit_geometry("F", "68-69", 69.0, 70.5)

    assert celsius == (2.0, 0.20000000000000284)
    assert fahrenheit == (1.0, 0.5)


def test_state_entry_collapse_keeps_distinct_rebrackets() -> None:
    frame = pd.DataFrame(
        {
            "city": ["A", "A", "A"],
            "target_date": ["2026-07-01"] * 3,
            "current_bracket": ["20", "20", "21"],
            "label": [0, 0, 1],
            "probability": [0.8, 0.6, 0.9],
        }
    )

    result = challenger.collapse_state_entries(frame, "probability")

    assert len(result) == 2
    assert result.set_index("current_bracket").loc["20", "probability"] == 0.7
    assert result.set_index("current_bracket").loc["21", "label"] == 1


def test_training_weights_equalize_dates_states_and_checkpoints() -> None:
    frame = pd.DataFrame(
        {
            "city": ["A", "A", "B", "C"],
            "target_date": ["2026-07-01", "2026-07-01", "2026-07-01", "2026-07-02"],
            "current_bracket": ["20", "20", "21", "22"],
            "label": [1, 1, 0, 1],
        }
    )
    weights = challenger.state_entry_weights(frame)
    weighted = frame.assign(weight=weights)

    assert np.allclose(
        weighted.groupby("target_date")["weight"].sum().to_numpy(), [0.5, 0.5]
    )
    first_date = weighted[weighted["target_date"] == "2026-07-01"]
    by_state = first_date.groupby(["city", "current_bracket"])["weight"].sum()
    assert np.allclose(by_state.to_numpy(), [0.25, 0.25])


def test_transition_features_do_not_depend_on_settlement_label() -> None:
    base = pd.DataFrame(
        {
            "unit": ["C"],
            "current_bracket": ["28"],
            "running_native": [28.0],
            "forecast_max_native": [29.1],
            "daylight_remaining_minutes": [283.0],
            "solar_elevation_deg": [66.0],
            "minutes_since_last_strict_new_high": [20.0],
            "temp_trend_1h_f": [-1.0],
            "temp_trend_3h_f": [2.0],
            "temp_curve_acceleration_f": [1.0],
            "reheating_transition_num": [0.0],
            "forecast_reheat_after_now_f": [1.5],
            "forecast_peak_delta_hours_local": [1.0],
            "same_running_max_obs_count": [2.0],
            "p_core_no_obs_age": [0.9192],
            "p_core_no_wind": [0.89],
        }
    )
    with_zero = challenger.add_transition_features(base.assign(label=0))
    with_one = challenger.add_transition_features(base.assign(label=1))

    assert np.allclose(
        with_zero[challenger.FEATURE_COLUMNS],
        with_one[challenger.FEATURE_COLUMNS],
        equal_nan=True,
    )
    assert with_zero.loc[0, "forecast_upper_exit_margin_ticks"] > 0
    assert with_zero.loc[0, "wind_boost_transition_risk"] > 0
