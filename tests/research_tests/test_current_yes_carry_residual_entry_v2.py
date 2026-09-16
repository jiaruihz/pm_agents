from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "research_current_yes_carry_residual_entry_v2",
    ROOT / "scripts/analysis/reheat_risk/research_current_yes_carry_residual_entry_v2.py",
)
assert SPEC is not None and SPEC.loader is not None
study = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(study)


def sample_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "city": "X",
                "target_date": "2026-07-01",
                "decision_snapshot_dt": pd.Timestamp("2026-07-01T05:30:00Z"),
                "decision_hour_local": 13,
                "current_bracket": "30",
                "current_yes_ask": 0.84,
                "current_yes_ask_size": 20,
                "taker_cost": 0.8467,
                "p_core": 0.83,
                "label": 1,
            },
            {
                "city": "X",
                "target_date": "2026-07-01",
                "decision_snapshot_dt": pd.Timestamp("2026-07-01T06:30:00Z"),
                "decision_hour_local": 14,
                "current_bracket": "30",
                "current_yes_ask": 0.86,
                "current_yes_ask_size": 20,
                "taker_cost": 0.8660,
                "p_core": 0.88,
                "label": 1,
            },
            {
                "city": "X",
                "target_date": "2026-07-01",
                "decision_snapshot_dt": pd.Timestamp("2026-07-01T07:30:00Z"),
                "decision_hour_local": 15,
                "current_bracket": "30",
                "current_yes_ask": 0.90,
                "current_yes_ask_size": 20,
                "taker_cost": 0.9045,
                "p_core": 0.93,
                "label": 1,
            },
        ]
    )


def test_core_is_continuous_and_does_not_include_legacy_path_or_cc() -> None:
    core = set(study.FEATURE_SETS["core"])

    assert "market_logit" in core
    assert "forecast_peak_delta_hours_local" in core
    assert "wind_speed_kt" in core
    assert "minutes_since_running_max_log" not in core
    assert "route_num" not in core


def test_first_positive_ev_uses_fee_adjusted_cost_and_first_checkpoint() -> None:
    selected = study.choose_first_positive_ev(sample_rows(), "p_core")

    assert len(selected) == 1
    assert int(selected.iloc[0]["decision_hour_local"]) == 14
    assert abs(float(selected.iloc[0]["model_edge_after_fee"]) - 0.014) < 1e-12


def test_confirmation_and_wait_are_later_same_bracket_counterfactuals() -> None:
    rows = sample_rows()
    first = study.choose_first_positive_ev(rows, "p_core")
    confirmed = study.two_consecutive_entries(rows, "p_core")
    waited = study.next_retained_hour(rows, first, "p_core")

    assert int(confirmed.iloc[0]["decision_hour_local"]) == 15
    assert int(waited.iloc[0]["decision_hour_local"]) == 15
    assert str(waited.iloc[0]["current_bracket"]) == str(first.iloc[0]["current_bracket"])
