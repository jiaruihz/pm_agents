from __future__ import annotations

import numpy as np

from scripts.analysis.forecast_quality import research_d1_market_residual_tree_v3 as subject


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
