import numpy as np
import pandas as pd

from scripts.analysis.forecast_quality import research_d1_legacy_weather_only_v2 as subject


def _history() -> pd.DataFrame:
    rows = []
    for month in range(1, 13):
        rows.append(
            {
                "city": "Alpha",
                "model": "gfs",
                "date": f"2025-{month:02d}-15",
                "month_num": month,
                "is_best_model": True,
                "error_f_actual_minus_forecast": float(month),
            }
        )
        rows.append(
            {
                "city": "Beta",
                "model": "ecmwf",
                "date": f"2025-{month:02d}-16",
                "month_num": month,
                "is_best_model": False,
                "error_f_actual_minus_forecast": float(-month),
            }
        )
    return pd.DataFrame(rows)


def test_history_policies_expand_without_using_non_assigned_models() -> None:
    history = _history()

    summer, summer_seasonal = subject.prepare_history(history, "2026-01-01", "summer_best")
    all_season, all_seasonal = subject.prepare_history(
        history, "2026-01-01", "all_season_best"
    )
    harmonic, harmonic_seasonal = subject.prepare_history(
        history, "2026-01-01", "harmonic_all_season_best"
    )
    all_models, all_models_seasonal = subject.prepare_history(
        history, "2026-01-01", "all_season_all_models"
    )

    assert len(summer) == 4
    assert len(all_season) == 12
    assert len(harmonic) == 12
    assert len(all_models) == 24
    assert set(all_season["model"]) == {"gfs"}
    assert set(all_models["model"]) == {"gfs", "ecmwf"}
    assert summer_seasonal is False
    assert all_seasonal is False
    assert harmonic_seasonal is True
    assert all_models_seasonal is False


def test_harmonic_season_center_recovers_known_cycle() -> None:
    months = np.tile(np.arange(1, 13), 3)
    design = subject._harmonic_design(months)
    expected_coefficients = np.asarray([1.5, 2.0, -0.75, 0.5, 0.25])
    frame = pd.DataFrame(
        {
            "model": "gfs",
            "month_num": months,
            "error_f_actual_minus_forecast": design @ expected_coefficients,
        }
    )

    fitted = subject.fit_season_coefficients(frame)

    np.testing.assert_allclose(fitted["gfs"], expected_coefficients, atol=1e-10)
    for month in range(1, 13):
        expected = float(subject._harmonic_design(np.asarray([month]))[0] @ expected_coefficients)
        assert abs(subject.season_center("gfs", month, fitted) - expected) < 1e-10


def test_enrichment_history_uses_all_rows_but_shrinks_by_city_model() -> None:
    rows = []
    for day in range(1, 9):
        for city, city_bias in (("Alpha", 0.5), ("Beta", -0.5)):
            for model, model_bias in (("m1", 0.25), ("m2", -0.25)):
                rows.append(
                    {
                        "city": city,
                        "model_key": model,
                        "target_date": f"2026-05-{day:02d}",
                        "error_f": city_bias + model_bias + 0.1 * (day % 3),
                    }
                )
    history = pd.DataFrame(rows)

    fitted = subject.fit_enrichment_history(history)

    assert fitted["rows"] == 32
    assert fitted["dates"] == 8
    assert fitted["cities"] == 2
    assert fitted["models"] == 2
    assert set(fitted["specs"]) == {
        ("Alpha", "m1"),
        ("Alpha", "m2"),
        ("Beta", "m1"),
        ("Beta", "m2"),
    }


def test_archive_multimodel_is_equal_source_weight_and_available_only_after_archive_date() -> None:
    brackets = [
        subject.base.Bracket("29 or below", None, 29.0, True, False),
        subject.base.Bracket("30", 30.0, 30.0, False, False),
        subject.base.Bracket("31 or above", 31.0, None, False, True),
    ]
    state = {
        "city": "Alpha",
        "target_date": "2026-07-16",
        "market_unit": "F",
        "brackets": brackets,
        "forecast_max_f": 30.0,
        "ensemble_median_f": 30.5,
        "model_spread_f": 1.0,
        "model_values_f": {"m1": 30.0, "m2": 31.0},
        "market_probs": np.asarray([0.2, 0.5, 0.3]),
    }
    errors_1 = np.asarray([-1.0, 0.0, 1.0])
    errors_2 = np.asarray([-0.5, 0.5])
    enrichment = {
        "specs": {
            ("Alpha", "m1"): {"errors": errors_1},
            ("Alpha", "m2"): {"errors": errors_2},
        },
        "global_errors": {},
    }
    expected = np.mean(
        np.vstack(
            [
                subject.empirical_vector(
                    state, 30.0 + errors_1, kernel_sd_f=subject.KERNEL_SD_F
                ),
                subject.empirical_vector(
                    state, 31.0 + errors_2, kernel_sd_f=subject.KERNEL_SD_F
                ),
            ]
        ),
        axis=0,
    )

    np.testing.assert_allclose(subject.enrichment_vector(state, enrichment), expected)

    climate = pd.DataFrame(
        {
            "city": ["Alpha"] * 20,
            "month": [7] * 20,
            "actual_max_f": np.linspace(28.0, 32.0, 20),
        }
    )
    fitted = {
        "climatology": climate,
        "specs": {
            "Alpha": {
                "model": "m1",
                "global_mean": 0.0,
                "global_sd": 1.0,
                "global_errors": np.asarray([-1.0, 0.0, 1.0]),
                "partial_center": 0.0,
                "partial_errors": np.asarray([-1.0, 0.0, 1.0]),
                "season_coefficients": None,
            }
        },
    }
    after = subject.model_vectors(
        state,
        fitted,
        ensemble_weight=0.5,
        spread_beta=0.0,
        enrichment_fitted=enrichment,
    )
    before_state = dict(state, target_date="2026-07-08")
    before = subject.model_vectors(
        before_state,
        fitted,
        ensemble_weight=0.5,
        spread_beta=0.0,
        enrichment_fitted=enrichment,
    )
    assert "H_archive_known_multimodel" in after
    assert "H_archive_known_multimodel" not in before
