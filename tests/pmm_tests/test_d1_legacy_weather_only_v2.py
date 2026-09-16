import json

import numpy as np
import pandas as pd

from scripts.analysis.forecast_quality import research_d1_cross_city_hierarchy_v1 as base
from scripts.analysis.forecast_quality import research_d1_legacy_weather_only_v2 as subject


def _state() -> dict:
    return {
        "market_unit": "F",
        "brackets": [
            base.Bracket("69 or below", None, 69.0, True, False),
            base.Bracket("70", 70.0, 70.0, False, False),
            base.Bracket("71+", 71.0, None, False, True),
        ],
    }


def test_empirical_vector_is_coherent_on_native_ladder() -> None:
    vector = subject.empirical_vector(_state(), np.array([69.0, 70.0, 71.0]), kernel_sd_f=0.75)

    assert vector.shape == (3,)
    assert np.all(vector > 0)
    assert np.isclose(vector.sum(), 1.0)


def test_probability_diagnostics_uses_rung_outcomes_and_tail_indices() -> None:
    frame = pd.DataFrame(
        [
            {
                "arm": "model",
                "snapshot_key": "a",
                "probabilities_json": json.dumps([0.1, 0.6, 0.3]),
                "winner_index": 1,
                "winner_probability": 0.6,
            },
            {
                "arm": "model",
                "snapshot_key": "b",
                "probabilities_json": json.dumps([0.7, 0.2, 0.1]),
                "winner_index": 0,
                "winner_probability": 0.7,
            },
        ]
    )

    result = subject.probability_diagnostics(frame).iloc[0]

    assert np.isclose(result.bottom_predicted, 0.4)
    assert result.bottom_actual == 0.5
    assert np.isclose(result.top_predicted, 0.2)
    assert result.top_actual == 0.0
    assert result.high_probability_rungs == 2
    assert result.high_probability_actual == 1.0
    assert result.winner_probability_le_001 == 0


def test_calibration_expands_each_state_to_rung_binary_outcomes() -> None:
    frame = pd.DataFrame(
        [
            {
                "arm": "model",
                "snapshot_key": "a",
                "probabilities_json": json.dumps([0.1, 0.6, 0.3]),
                "winner_index": 1,
            }
        ]
    )

    result = subject.calibration(frame)

    assert result.rungs.sum() == 3
    assert result.actual_frequency.mul(result.rungs).sum() == 1
