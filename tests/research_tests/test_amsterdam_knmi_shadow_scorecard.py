import pytest

from scripts.analysis.forecast_quality import (
    research_amsterdam_knmi_shadow_scorecard as scorecard,
)


def test_probability_metrics_and_market_delta_helpers_are_deterministic():
    metrics = scorecard.probability_metrics([0.1, 0.8], [0, 1])

    assert metrics["rows"] == 2
    assert metrics["direction_accuracy_0_5"] == 1.0
    assert metrics["brier"] == pytest.approx(0.025)
    assert metrics["auc"] == 1.0

    bootstrap = scorecard.bootstrap_date_means(
        {"2026-08-06": 0.1, "2026-08-07": 0.2}, samples=100, seed=7
    )
    assert bootstrap["target_dates"] == 2
    assert bootstrap["mean"] == pytest.approx(0.15)
    assert bootstrap["ci95"] == [0.1, 0.2]


def test_knmi_lattice_rounding_and_weather_fee_contract():
    assert scorecard.arithmetic_round(21.49) == 21
    assert scorecard.arithmetic_round(21.5) == 22
    assert scorecard.bracket_equal("22", 22.0)
    assert scorecard.taker_fee(5.0, 0.75) == 0.04688
