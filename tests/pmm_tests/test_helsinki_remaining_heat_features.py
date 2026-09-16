from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from weather_data_feed.helsinki_remaining_heat_features import (
    build_fmi_remaining_heat_features,
    build_forecast_remaining_heat_features,
)


def test_fmi_feature_builder_uses_fmi_path_not_metar_plateau() -> None:
    start = datetime(2026, 8, 12, 7, 0, tzinfo=timezone.utc)
    history = []
    for index, temp in enumerate((17.0, 17.4, 17.8, 17.8, 17.7, 17.9, 17.9)):
        history.append(
            {
                "observation_time_utc": (start + timedelta(minutes=10 * index)).isoformat(),
                "temp_c": temp,
                "relative_humidity_pct": 70 - index,
                "dewpoint_depression_c": 4 + index / 10,
                "wind_speed_ms": 2 + index / 10,
                "wind_gust_ms": 4 + index / 10,
                "wind_dir_deg": 270,
                "pressure_hpa": 1010 + index / 10,
                "cloud_cover_okta": 4,
                "global_radiation_wm2": 100 + 10 * index,
                "diffuse_radiation_wm2": 30,
                "longwave_in_wm2": 320,
                "longwave_out_wm2": 410,
                "reflected_radiation_wm2": 20,
                "sunshine_seconds": 60,
            }
        )

    features = build_fmi_remaining_heat_features(
        history, official_running_max_c=18.0
    )

    assert features["minutes_since_strict_high"] == 10.0
    assert features["plateau_duration_min"] == 10.0
    assert features["temp_delta_20m"] == pytest.approx(0.2)
    assert features["same_value_run_count"] == 2.0
    assert features["source_to_official_level_basis_c"] == pytest.approx(-0.1)
    assert features["global_radiation_mean_60m"] == 135.0
    assert features["relative_humidity_delta_30m"] == -3.0


def test_forecast_feature_builder_has_full_v2_v3_contract() -> None:
    decision = datetime(2026, 8, 12, 8, 30, tzinfo=timezone.utc)
    row = {
        "available_at_utc": "2026-08-12T08:00:00Z",
        "hourly_curve": [
            {
                "time_local": f"2026-08-12T{hour:02d}:00",
                "temperature_f": temp * 9 / 5 + 32,
                "shortwave_radiation_wm2": radiation,
                "cloud_cover_pct": 30,
                "wind_speed_10m_kt": 5,
            }
            for hour, temp, radiation in (
                (10, 17.0, 300), (11, 18.0, 400), (12, 19.0, 500),
                (13, 20.0, 450), (14, 19.0, 300), (15, 18.0, 100),
            )
        ],
    }

    features = build_forecast_remaining_heat_features(
        row,
        decision=decision,
        official_running_max_c=18.0,
        current_temp_c=18.2,
    )

    assert features["forecast_future_radiation_sum_kwhm2"] == 1.35
    assert features["forecast_first_boundary_cross_minutes"] == 0.0
    assert features["forecast_last_boundary_hold_minutes"] == 150.0
    assert features["forecast_warming_slope_1h_cph"] == 1.0
    assert features["forecast_future_positive_slope_hours"] == 1.5
