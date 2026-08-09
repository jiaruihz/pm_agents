from __future__ import annotations

import math
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from strategies.weather_edge_v1.tools.heada_terminal_distribution import (  # noqa: E402
    kelly_fraction,
    source_confidence_multiplier,
    terminal_bracket_distribution,
)


def test_terminal_distribution_is_coherent() -> None:
    result = terminal_bracket_distribution(
        {
            "bracket_width_f": 1.0,
            "raw_dist_br": 0.0,
            "bias_p50_asof": 0.0,
            "bias_p90_asof": 1.2,
            "bias_mean_asof": 0.1,
        }
    )
    assert result.degraded is False
    assert result.p_below + result.p_win_exact + result.p_overshoot == pytest.approx(
        1.0
    )
    assert min(result.p_below, result.p_win_exact, result.p_overshoot) >= 0.0


def test_missing_geometry_is_explicitly_degraded() -> None:
    result = terminal_bracket_distribution({})
    assert result.degraded is True
    assert result.reason == "missing_geometry_or_bias"
    assert math.isnan(result.p_win_exact)


def test_source_quality_changes_sizing_not_probability_contract() -> None:
    assert source_confidence_multiplier({"source_quality_tier_v1": "low"}) == 0.6
    assert kelly_fraction(0.60, 0.50) == pytest.approx(0.20)
