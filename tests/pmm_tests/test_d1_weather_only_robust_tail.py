import numpy as np
import pandas as pd

from scripts.analysis.forecast_quality import research_d1_cross_city_hierarchy_v1 as base
from scripts.analysis.forecast_quality import research_d1_legacy_weather_only_robust_tail as subject


def _state() -> dict:
    return {
        "city": "TestCity",
        "target_date": "2026-07-01",
        "market_unit": "F",
        "forecast_max_f": 70.0,
        "ensemble_median_f": 71.0,
        "brackets": [
            base.Bracket("69 or below", None, 69.0, True, False),
            base.Bracket("70", 70.0, 70.0, False, False),
            base.Bracket("71+", 71.0, None, False, True),
        ],
    }


def _fitted() -> dict:
    errors = np.array([-1.0, 0.0, 1.0])
    return {
        "specs": {
            "TestCity": {
                "partial_center": 0.0,
                "partial_errors": errors,
            }
        },
        "climatology": pd.DataFrame(
            {
                "city": ["TestCity"] * 3,
                "month": [7, 7, 7],
                "actual_max_f": [68.0, 69.0, 70.0],
            }
        ),
    }


def test_robust_tail_vector_is_a_coherent_distribution() -> None:
    vector = subject.robust_tail_vector(
        _state(),
        _fitted(),
        ensemble_weight=0.75,
        scale_temperature=1.25,
        climate_mix=0.05,
    )

    assert np.all(vector > 0)
    assert np.isclose(vector.sum(), 1.0)


def test_climate_mix_is_exact_convex_probability_mixture() -> None:
    weather = subject.robust_tail_vector(
        _state(),
        _fitted(),
        ensemble_weight=0.75,
        scale_temperature=1.25,
        climate_mix=0.0,
    )
    climate = subject.v2.empirical_vector(
        _state(),
        np.array([68.0, 69.0, 70.0]),
        kernel_sd_f=subject.v2.KERNEL_SD_F,
    )
    mixed = subject.robust_tail_vector(
        _state(),
        _fitted(),
        ensemble_weight=0.75,
        scale_temperature=1.25,
        climate_mix=0.05,
    )

    assert np.allclose(mixed, 0.95 * weather + 0.05 * climate)


def test_market_offset_beta_zero_is_exact_market_baseline() -> None:
    market = np.array([0.1, 0.3, 0.6])
    weather = np.array([0.4, 0.4, 0.2])

    posterior = subject.market_offset_vector(market, weather, beta=0.0)

    assert np.array_equal(posterior, market)
    assert posterior is not market


def test_market_offset_is_coherent_and_beta_one_matches_weather() -> None:
    market = np.array([0.1, 0.3, 0.6])
    weather = np.array([0.4, 0.4, 0.2])

    posterior = subject.market_offset_vector(market, weather, beta=1.0)

    assert np.all(posterior > 0)
    assert np.isclose(posterior.sum(), 1.0)
    assert np.allclose(posterior, weather)
