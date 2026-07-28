from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "scripts/analysis/market_structure_edge/"
    "research_d1_full_ladder_no_source_confidence_city_v2.py"
)
SPEC = importlib.util.spec_from_file_location("d1_source_city_v2", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_market_probability_normalizes_yes_ladder_mass() -> None:
    rows = pd.DataFrame(
        [
            {
                "rung_index": 0,
                "yes_best_bid": 0.19,
                "yes_best_ask": 0.21,
            },
            {
                "rung_index": 1,
                "yes_best_bid": 0.29,
                "yes_best_ask": 0.31,
            },
            {
                "rung_index": 2,
                "yes_best_bid": 0.49,
                "yes_best_ask": 0.51,
            },
        ]
    )
    probabilities = MODULE.market_probabilities(rows)
    assert probabilities[0] == 0.8
    assert probabilities[1] == 0.7
    assert probabilities[2] == 0.5


def test_source_probability_uses_empirical_residual_distribution() -> None:
    probability = MODULE.source_probability(
        forecast_max_f=70.0,
        residuals_f=np.array([-1.0, 0.0, 1.0]),
        unit="F",
        bracket_label="70",
        question="Will the highest temperature be 70°F?",
    )
    assert probability == pytest.approx(2.0 / 3.0)


def test_bh_qvalues_are_monotone_in_rank() -> None:
    pvalues = pd.Series([0.001, 0.02, 0.04, 0.5])
    qvalues = MODULE.bh_qvalues(pvalues)
    ordered = qvalues.iloc[np.argsort(pvalues.to_numpy())].to_numpy()
    assert np.all(np.diff(ordered) >= 0)
    assert qvalues.iloc[0] == 0.004


def test_select_best_uses_edge_then_lower_cost_tiebreak() -> None:
    candidates = pd.DataFrame(
        [
            {
                "snapshot_key": "x",
                "policy_edge": 0.03,
                "cost": 0.90,
                "bracket": "low",
            },
            {
                "snapshot_key": "x",
                "policy_edge": 0.03,
                "cost": 0.80,
                "bracket": "high",
            },
        ]
    )
    selected = MODULE.select_best(candidates)
    assert selected.iloc[0]["bracket"] == "high"
