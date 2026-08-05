from __future__ import annotations

import numpy as np
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality import research_d1_market_residual_tournament_v1 as target


def test_zero_residual_returns_market_exactly() -> None:
    market = np.asarray([0.05, 0.25, 0.50, 0.20], dtype=float)
    design = np.asarray(
        [[-2.0, 1.0], [-1.0, -1.0], [0.5, 2.0], [3.0, -4.0]], dtype=float
    )

    actual = target.softmax_offset(market, design, np.zeros(2))

    assert np.array_equal(actual, market)


def test_prepared_labels_preserve_open_native_tails() -> None:
    brackets = target._brackets_from_labels(["77", "78-79", "80-81", "82+"])

    assert brackets[0].bottom is True
    assert brackets[0].high == 77.0
    assert brackets[1].low == 78.0
    assert brackets[1].high == 79.0
    assert brackets[-1].top is True
    assert brackets[-1].low == 82.0


def test_version_budget_is_exactly_v01_to_v04() -> None:
    assert list(target.FEATURE_SETS) == [
        "V01_weather_logratio",
        "V02_ordinal_location_scale",
        "V03_structured_weather",
        "V04_revision_spread_bias",
    ]
