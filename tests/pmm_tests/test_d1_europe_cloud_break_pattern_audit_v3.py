from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "scripts/analysis/market_structure_edge/"
    "research_d1_europe_cloud_break_pattern_audit_v3.py"
)
SPEC = importlib.util.spec_from_file_location("d1_family_audit_v3", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_group_masks_keep_family_and_eu_control_disjoint() -> None:
    rows = pd.DataFrame(
        [
            {
                "city_family": "europe_cloud_break",
                "region": "EU",
                "market_unit": "C",
            },
            {
                "city_family": "unmapped",
                "region": "EU",
                "market_unit": "C",
            },
            {
                "city_family": "unmapped",
                "region": "US",
                "market_unit": "F",
            },
        ]
    )
    masks = MODULE.group_masks(rows)
    assert masks["europe_cloud_break"].tolist() == [True, False, False]
    assert masks["eu_non_family"].tolist() == [False, True, False]
    assert not (masks["europe_cloud_break"] & masks["eu_non_family"]).any()


def test_paired_roi_delta_resamples_shared_target_dates() -> None:
    left = pd.DataFrame(
        {
            "target_date": ["a", "b"],
            "pnl": [1.0, 0.0],
            "cost": [1.0, 1.0],
        }
    )
    right = pd.DataFrame(
        {
            "target_date": ["a", "b"],
            "pnl": [0.0, 1.0],
            "cost": [1.0, 1.0],
        }
    )
    samples = MODULE.paired_roi_delta_samples(
        left, right, draws=500, seed=7
    )
    assert set(np.unique(samples)).issubset({-1.0, 0.0, 1.0})
    assert abs(float(samples.mean())) < 0.1


def test_normalize_bracket_removes_csv_numeric_suffix_only() -> None:
    values = pd.Series(["28.0", "30-31", "32 or below"])
    assert MODULE.normalize_bracket(values).tolist() == [
        "28",
        "30-31",
        "32 or below",
    ]


def test_attach_equal_all5_scores_uses_equal_probability_mean() -> None:
    row = {"no_win": 1.0, "market_p_no": 0.8}
    for index, model in enumerate(MODULE.MODEL_KEYS):
        row[f"p_no_{model}"] = 0.6 + 0.1 * (index % 2)
    scored = MODULE.attach_equal_all5_scores(pd.DataFrame([row]))
    expected = np.mean([row[f"p_no_{model}"] for model in MODULE.MODEL_KEYS])
    assert scored.iloc[0]["policy_p_no"] == expected
    assert scored.iloc[0]["brier"] == (1.0 - expected) ** 2
