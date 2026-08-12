import json

import numpy as np
import pandas as pd

from scripts.analysis.forecast_quality import research_d1_d2_weather_only_v2 as shared_runner
from scripts.analysis.forecast_quality import research_d1_legacy_weather_only_robust_tail as legacy_w0
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


def test_locked_w0_prior_uses_only_prior_d1_multimodel_batch(monkeypatch) -> None:
    climatology = pd.DataFrame(
        {
            "city": ["Tokyo"] * 20,
            "month": [6] * 20,
            "actual_max_f": np.linspace(82.0, 91.0, 20),
        }
    )
    fitted = {
        "specs": {
            "Tokyo": {
                "model": "gfs",
                "partial_center": 0.5,
                "partial_errors": np.asarray([-1.5, -0.5, 0.5, 1.5, 2.5]),
            }
        },
        "climatology": climatology,
    }
    monkeypatch.setattr(
        shared_runner.legacy_weather,
        "fit_legacy_history_slice",
        lambda *args, **kwargs: fitted,
    )
    raw = pd.DataFrame(
        [
            {
                "city": "Tokyo",
                "target_date": "2026-06-02",
                "snapshot_ts_utc": "2026-06-01T11:00:00Z",
                "lead_days": lead,
                "brackets_json": json.dumps(["29", "30", "31+"]),
            }
            for lead in (1, 2)
        ]
    )
    models = {
        "ecmwf_aifs025_single": 85.0,
        "ecmwf_ifs025": 85.5,
        "gfs_global": 86.0,
        "icon_seamless": 86.5,
        "jma_seamless": 87.0,
    }
    forecast_rows = []
    for snapshot_key, decision, bump in (
        ("prior", "2026-06-01T10:00:00Z", 0.0),
        ("future", "2026-06-01T12:00:00Z", 20.0),
    ):
        for model_key, value in models.items():
            forecast_rows.append(
                {
                    "snapshot_key": snapshot_key,
                    "city": "Tokyo",
                    "target_date": "2026-06-02",
                    "decision_time_utc": decision,
                    "model_key": model_key,
                    "forecast_max_f": value + bump,
                }
            )
    history = pd.DataFrame(
        [{"date": "2025-06-01", "is_best_model": True}]
    )
    attached = shared_runner.attach_locked_w0_robust_tail_prior(
        raw,
        pd.DataFrame(forecast_rows),
        history,
    )
    brackets, unit = shared_runner._locked_w0_brackets(["29", "30", "31+"])
    expected = legacy_w0.robust_tail_vector(
        {
            "city": "Tokyo",
            "target_date": "2026-06-02",
            "market_unit": unit,
            "brackets": brackets,
            "forecast_max_f": models["gfs_global"],
            "ensemble_mean_f": float(np.mean(list(models.values()))),
        },
        fitted,
        ensemble_weight=0.875,
        scale_temperature=1.25,
        climate_mix=0.02,
        consensus_stat="mean",
        bias_multiplier=1.0,
    )
    actual = np.asarray(json.loads(attached.loc[0, "w0_robust_tail_probs_json"]))
    assert np.allclose(actual, expected)
    assert pd.isna(attached.loc[1, "w0_robust_tail_probs_json"])
    assert attached.attrs["locked_w0"]["median_asof_lag_hours"] == 1.0
    assert attached.attrs["locked_w0"]["selection_dates"][0] == "2026-06-17"
    assert attached.attrs["locked_w0"]["previously_viewed_secondary_dates"][-1] == "2026-07-23"
