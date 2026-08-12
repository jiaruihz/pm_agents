import json

import numpy as np
import pandas as pd

from weather_model_evaluation.d1_d2_probability import (
    CalibrationParameters,
    empirical_forecast_probability,
    fit_city_shifts,
    prepare_probability_rows,
    transform_probability,
)


def test_probability_transform_is_coherent_and_moves_location() -> None:
    source = np.asarray([0.1, 0.6, 0.3])
    shifted = transform_probability(
        source,
        CalibrationParameters(temperature=1.2, shift=0.5, diffusion=0.1, tail_floor=0.01),
    )
    assert np.isclose(shifted.sum(), 1.0)
    assert (shifted > 0).all()
    assert np.dot(np.arange(3), shifted) > np.dot(np.arange(3), source)


def test_prepare_rows_keeps_d1_and_d2_on_same_contract() -> None:
    frame = pd.DataFrame(
        [
            {
                "city": "Tokyo",
                "target_date": "2026-06-01",
                "lead_days": lead,
                "brackets_json": json.dumps(["29", "30", "31+"]),
                "model_probs_json": json.dumps([0.2, 0.6, 0.2]),
                "market_probs_json": json.dumps([0.3, 0.5, 0.2]),
                "winner_bracket": "30",
            }
            for lead in (1, 2)
        ]
    )
    rows = prepare_probability_rows(frame)
    assert rows["lead_days"].tolist() == [1, 2]
    assert rows["winner_index"].tolist() == [1, 1]


def test_city_bias_only_moves_center_and_is_shrunk() -> None:
    rows = pd.DataFrame(
        [
            {
                "source_index": index,
                "city": "Tokyo",
                "target_date": f"2026-06-{index + 1:02d}",
                "lead_days": 1,
                "winner_index": 2,
                "model_probs": np.asarray([0.1, 0.8, 0.1]),
                "market_probs": np.asarray([0.1, 0.8, 0.1]),
                "rung_count": 3,
            }
            for index in range(10)
        ]
    )
    params = {1: CalibrationParameters()}
    weak = fit_city_shifts(rows, params, shrinkage=10.0)[(1, "Tokyo")]
    strong = fit_city_shifts(rows, params, shrinkage=100.0)[(1, "Tokyo")]
    assert weak > strong > 0


def test_empirical_forecast_probability_respects_native_ladder() -> None:
    probability = empirical_forecast_probability(
        forecast_max_f=86.0,
        errors_f=np.asarray([-2.0, 0.0, 2.0]),
        brackets=["81", "82-83", "84-85", "86-87", "88+"],
    )
    assert np.isclose(probability.sum(), 1.0)
    assert probability.argmax() == 3
