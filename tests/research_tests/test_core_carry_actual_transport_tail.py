from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/analysis/reheat_risk/core_carry_actual_transport_tail.py"
SPEC = importlib.util.spec_from_file_location("core_carry_actual_transport_tail", SCRIPT)
assert SPEC and SPEC.loader
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)


def parent_row() -> pd.Series:
    return pd.Series(
        {
            "assigned_forecast_exit_margin_ticks": 1.0,
            "solar_elevation_deg": 60.0,
            "daylight_remaining_minutes": 240.0,
            "minutes_since_last_strict_new_high": 20.0,
        }
    )


def test_actual_transition_uses_only_asof_observations_and_orients_protection() -> None:
    history = pd.DataFrame(
        {
            "valid_utc": pd.to_datetime(
                [
                    "2026-07-01T09:00:00Z",
                    "2026-07-01T11:00:00Z",
                    "2026-07-01T12:00:00Z",
                    "2026-07-01T12:10:00Z",
                ],
                utc=True,
            ),
            "tmpf": [80.0, 82.0, 81.0, 100.0],
            "dwpf": [70.0, 72.0, 73.0, 90.0],
            "drct": [180.0, 180.0, 180.0, 180.0],
            "sknt": [15.0, 15.0, 15.0, 40.0],
            "sky_level": [1.0, 2.0, 4.0, 0.0],
            "wxcodes": ["", "", "RA", ""],
            "p01i": [0.0, 0.0, 0.1, 0.0],
            "gust": [np.nan, np.nan, 25.0, 50.0],
            "alti": [30.0, 29.95, 29.90, 29.80],
        }
    )

    features = research.actual_transition_row(
        history,
        pd.Timestamp("2026-07-01T12:00:00Z"),
        40.0,
        parent_row(),
    )

    assert features["actual_temp_1h_f"] == -1.0
    assert features["actual_dewpoint_1h_f"] == 1.0
    assert features["actual_precip_1h"] == 1.0
    assert features["warm_moist_transport"] > 0
    assert features["cloud_cooling_protection"] < 0
    assert features["rain_cooling_protection"] < 0


def test_monotone_fit_never_assigns_negative_feature_coefficients() -> None:
    rows = []
    for index in range(24):
        risk = index / 23
        row = {
            "city": f"C{index % 4}",
            "target_date": f"2026-07-{1 + index // 4:02d}",
            "current_bracket": str(20 + index % 2),
            "overshoot": int(risk > 0.7),
            "p_over_core": 0.1,
            "base_logit": np.log(0.1 / 0.9),
        }
        row.update({feature: risk for feature in research.FEATURES})
        rows.append(row)
    frame = pd.DataFrame(rows)
    _, fit = research.fit_predict(frame.iloc[:20], frame.iloc[20:])

    assert all(
        coefficient >= 0
        for name, coefficient in fit["coefficient_map"].items()
        if name != "calibration_intercept"
    )


def test_selected_policy_reports_capital_saved_minus_winner_profit() -> None:
    frame = pd.DataFrame(
        {
            "target_date": ["2026-07-01", "2026-07-01"],
            "frozen_baseline_selected": [True, True],
            "ten_share_cost_per_share": [0.9, 0.9],
            "label": [0, 1],
            "p_challenger": [0.3, 0.3],
            "challenger_threshold": [0.2, 0.2],
            "p_over_core": [0.3, 0.1],
            "core_threshold": [0.2, 0.2],
        }
    )

    result = research.selected_policy(frame)

    assert result["challenger_overlay"]["loss_capital_saved"] == 4.5
    assert np.isclose(result["challenger_overlay"]["winner_profit_sacrificed"], 0.5)
    assert np.isclose(result["challenger_overlay"]["net_tail_value"], 4.0)


def test_absolute_lift_without_core_increment_is_not_material() -> None:
    tail = {"lift": 2.0, "overshoots_captured": 7}
    policy = {"downsize_losses": 1, "net_tail_value": 1.0}

    checks = research.materiality_checks(tail, tail.copy(), policy, policy.copy())

    assert checks["absolute_tail_pass"] is True
    assert checks["incremental_tail_vs_core_pass"] is False
    assert checks["incremental_policy_vs_core_pass"] is False
