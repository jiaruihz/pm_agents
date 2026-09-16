from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "scripts/analysis/market_structure_edge/"
    "research_d1_multisource_consensus_residual_v1.py"
)
SPEC = importlib.util.spec_from_file_location("consensus_residual_v1", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_score_rows_selects_high_no_when_assigned_is_hotter() -> None:
    baskets = pd.DataFrame(
        [
            {
                "snapshot_key": "s1",
                "policy": "D-1_12_18_first",
                "city": "Amsterdam",
                "target_date": "2026-07-23",
                "low_no_ask": 0.98,
                "high_no_ask": 0.96,
                "low_win": 0.0,
                "high_win": 1.0,
            }
        ]
    )
    pit = pd.DataFrame(
        [
            {
                "snapshot_key": "s1",
                "policy": "D-1_12_18_first",
                "city": "Amsterdam",
                "target_date": "2026-07-23",
                "models": '{"ECMWF": 90, "GFS": 85, "ICON": 86}',
            }
        ]
    )
    calibration = {
        ("Amsterdam", "ECMWF"): {"bias_correction_f": 0},
        ("Amsterdam", "GFS"): {"bias_correction_f": 0},
        ("Amsterdam", "ICON"): {"bias_correction_f": 0},
    }
    scored = MODULE.score_rows(baskets, pit, calibration)
    row = scored.iloc[0]
    assert row["selected_leg"] == "high_no"
    assert row["assigned_minus_consensus_f"] == 4.0
    assert row["selected_payout"] == 0.0
    assert row["opposite_payout"] == 1.0


def test_score_rows_applies_city_model_bias_before_consensus() -> None:
    baskets = pd.DataFrame(
        [
            {
                "snapshot_key": "s1",
                "policy": "D-1_12_18_first",
                "city": "Amsterdam",
                "target_date": "2026-07-23",
                "low_no_ask": 0.98,
                "high_no_ask": 0.98,
                "low_win": 0.0,
                "high_win": 0.0,
            }
        ]
    )
    pit = pd.DataFrame(
        [
            {
                "snapshot_key": "s1",
                "policy": "D-1_12_18_first",
                "city": "Amsterdam",
                "target_date": "2026-07-23",
                "models": '{"ECMWF": 85, "GFS": 88, "ICON": 91}',
            }
        ]
    )
    calibration = {
        ("Amsterdam", "ECMWF"): {"bias_correction_f": 0},
        ("Amsterdam", "GFS"): {"bias_correction_f": 0},
        ("Amsterdam", "ICON"): {"bias_correction_f": -4},
    }
    scored = MODULE.score_rows(baskets, pit, calibration)
    row = scored.iloc[0]
    assert row["consensus_corrected_f"] == 87.0
    assert row["selected_leg"] == "low_no"
