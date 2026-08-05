from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.analysis.forecast_quality import (
    d1_city_cohort_market_residual as audit,
)


def test_calibrated_posterior_has_exact_market_null() -> None:
    market = np.asarray([0.2, 0.3, 0.5])
    weather = np.asarray([0.6, 0.3, 0.1])
    result = audit.calibrated_posterior_vector(
        market, weather, market_temperature=1.0, beta=0.0
    )
    np.testing.assert_allclose(result, market)


def test_weather_beta_changes_distribution_but_keeps_simplex() -> None:
    market = np.asarray([0.2, 0.3, 0.5])
    weather = np.asarray([0.6, 0.3, 0.1])
    result = audit.calibrated_posterior_vector(
        market, weather, market_temperature=1.1, beta=0.2
    )
    assert not np.allclose(result, market)
    assert np.all(result > 0)
    assert np.isclose(result.sum(), 1.0)


def test_state_artifact_is_exact_primary_city_date_grain() -> None:
    states = audit.load_state_artifact(audit.DEFAULT_STATE_ARTIFACT)
    primary = [state for state in states if state["policy"] == "D-1_18_24_first"]
    keys = [(state["city"], state["target_date"]) for state in primary]
    assert len(primary) == 279
    assert len(keys) == len(set(keys))
    assert len({state["target_date"] for state in primary}) == 27
    assert all(np.isclose(state["market_probs"].sum(), 1.0) for state in primary)


def test_cohorts_are_derived_without_evaluation_labels() -> None:
    states = [
        {"city": "A", "model_key": "gfs_global"},
        {"city": "B", "model_key": "ecmwf_ifs025"},
        {"city": "C", "model_key": "gfs_global"},
    ]
    history = pd.DataFrame(
        {
            "city": ["A", "B", "C"],
            "model": ["gfs", "ecmwf", "gfs"],
            "rows": [100, 100, 100],
            "dates": [100, 100, 100],
            "mae_f": [1.0, 3.0, 2.0],
            "bias_f": [0.0, 0.0, 0.0],
        }
    )
    cohorts = audit.build_cohorts(states, history)
    assert cohorts["V05_all_cities_market_offset"] == {"A", "B", "C"}
    assert cohorts["V06_gfs_lower_source_mae"] == {"A", "C"}
    assert cohorts["V07_lower_half_city_mae"] == {"A", "C"}
