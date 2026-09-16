from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "scripts/analysis/market_structure_edge/"
    "research_d1_europe_mechanism_decomposition_v4.py"
)
SPEC = importlib.util.spec_from_file_location("d1_europe_v4", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_group_name_separates_family_europe_and_units() -> None:
    rows = pd.DataFrame(
        [
            {"city_family": "europe_cloud_break", "region": "EU", "market_unit": "C"},
            {"city_family": "unmapped", "region": "EU", "market_unit": "C"},
            {"city_family": "unmapped", "region": "US", "market_unit": "F"},
            {"city_family": "unmapped", "region": "AS", "market_unit": "C"},
        ]
    )
    assert MODULE.group_name(rows).tolist() == [
        "europe_family",
        "europe_other",
        "us_f",
        "non_europe_c",
    ]


def test_bracket_center_handles_exact_and_range() -> None:
    assert MODULE.bracket_center(
        "28", "Will the highest temperature be 28°C?"
    ) == 28.0
    assert MODULE.bracket_center(
        "92-93", "Will the highest temperature be 92-93°F?"
    ) == 92.5


def test_shapley_components_sum_to_roi_difference() -> None:
    p_left, cost_left = 0.96, 0.90
    p_right, cost_right = 0.91, 0.93
    risk, price = MODULE.shapley_roi_components(
        p_left, cost_left, p_right, cost_right
    )
    expected = MODULE.roi_from_probability_cost(
        p_left, cost_left
    ) - MODULE.roi_from_probability_cost(p_right, cost_right)
    assert risk + price == pytest.approx(expected)
