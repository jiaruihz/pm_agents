from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/analysis/forecast_quality/d1_market_residual_tree_challenger.py"
SPEC = importlib.util.spec_from_file_location("d1_market_residual_tree_challenger", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
subject = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subject)


def test_zero_blend_returns_market_exactly() -> None:
    market = np.asarray([0.1, 0.3, 0.6], dtype=float)
    tree = np.asarray([0.8, 0.1, 0.1], dtype=float)

    actual = subject.blend_market(market, tree, 0.0)

    assert np.array_equal(actual, market)


def test_tree_versions_are_v09_and_v10() -> None:
    assert list(subject.TREE_SPECS) == [
        "V09_shallow_gradient_boosting",
        "V10_interaction_gradient_boosting",
    ]
    assert 0.0 in subject.ALPHA_GRID
