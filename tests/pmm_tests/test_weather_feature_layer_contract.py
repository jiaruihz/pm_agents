from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd
import pytest

from src.strategies.weather_edge_v1.tools import low_price_yes_tail_telemetry as tail_telemetry
from src.strategies.weather_edge_v1.tools import regime_routed_no_stable as stable_regime
from weather_data_feed import weather_context
from weather_feature_layer import bias, market, regimes, state
from weather_feature_layer.builders import build_weather_state_frame, build_weather_state_frame_with_audits
from weather_feature_layer.contracts import (
    FEATURE_FRAME_REQUIRED_METADATA,
    PIT_PROVENANCE_ARCHIVE_RECONSTRUCTION,
    PIT_PROVENANCE_LIVE_CAPTURE,
)


def test_state_reexports_weather_context_without_private_multiplier() -> None:
    record = {
        "city": "LA",
        "wind_speed_kt": 12,
        "wind_dir_deg": 240,
        "sky_cover_code": 0,
        "temp_trend_1h_f": 1.5,
        "temp_trend_3h_f": 2.0,
        "relative_humidity_pct": 55,
        "dewpoint_depression_f": 18,
        "forecast_peak_delta_hours_local": -1,
    }

    assert state.temperature_context_features(record) == weather_context.temperature_context_features(record)
    assert state.cloud_warming_interaction(0, 1.5, 2.0) == weather_context.cloud_warming_interaction(0, 1.5, 2.0)
    assert not hasattr(state, "temperature_context_multiplier")


def test_feature_frame_metadata_contract_includes_pit_provenance() -> None:
    assert "pit_provenance" in FEATURE_FRAME_REQUIRED_METADATA
    assert PIT_PROVENANCE_LIVE_CAPTURE == "live_capture"
    assert PIT_PROVENANCE_ARCHIVE_RECONSTRUCTION == "archive_reconstruction"


def test_regime_labels_cover_f_and_c_city_without_unit_drift() -> None:
    rows = pd.DataFrame(
        [
            {
                "city": "LA",
                "unit": "F",
                "decision_hour_local": 10,
                "forecast_gap_to_running_native": 3.0,
                "relative_humidity_pct": 82,
                "sky_cover_code": 3,
                "dewpoint_depression_f": 8,
                "wind_speed_kt": 11,
                "minutes_since_running_max": 30,
                "decline_native": 0.2,
                "temp_trend_1h_f": 1.2,
                "temp_trend_3h_f": 2.4,
            },
            {
                "city": "London",
                "unit": "C",
                "decision_hour_local": 14,
                "forecast_gap_to_running_native": 1.5,
                "relative_humidity_pct": 70,
                "sky_cover_code": 1,
                "dewpoint_depression_f": 26,
                "wind_speed_kt": 8,
                "minutes_since_running_max": 90,
                "decline_native": 0.2,
                "temp_trend_1h_f": 0.2,
                "temp_trend_3h_f": -0.1,
            },
        ]
    )

    shared = regimes.add_regime_labels(rows)
    stable = stable_regime.add_regime_labels(rows)
    pd.testing.assert_frame_equal(shared, stable)

    assert shared.loc[0, "day_regime"] == "day_open_runway"
    assert shared.loc[0, "moisture_cloud_regime"] == "humid_overcast_suppression"
    assert shared.loc[1, "day_regime"] == "day_open_runway"
    assert shared.loc[1, "running_max_state"] == "near_high_plateau"


def test_market_geometry_preserves_stable_and_tail_bracket_semantics() -> None:
    parsed = market.parse_bracket("36+")
    assert parsed is not None
    assert parsed.low == 36
    assert parsed.high is None
    assert market.bracket_contains(parsed, 42)
    assert stable_regime.parse_bracket("36+") == parsed

    assert market.parse_bracket_bounds("36+") == (36.0, 36.0)
    assert tail_telemetry.parse_bracket_bounds("36+") == (36.0, 36.0)
    assert market.bracket_distance_features({"bracket": "36+", "forecast_max_native": 37.2}) == (
        tail_telemetry.bracket_distance_features({"bracket": "36+", "forecast_max_native": 37.2})
    )


@dataclass
class _Resources:
    bias_index: dict[tuple[str, str], list[tuple[str, float]]]


def test_bias_asof_features_match_tail_telemetry_contract() -> None:
    resources = _Resources(
        {
            ("LA", "gfs"): [
                ("2026-05-01", 1.0),
                ("2026-05-02", -2.0),
                ("2026-05-04", 3.0),
                ("2026-05-08", 9.0),
            ]
        }
    )

    shared = bias.asof_bias_features(resources, city="LA", forecast_model="gfs", target_date="2026-05-05")
    legacy = tail_telemetry.asof_bias_features(resources, city="LA", forecast_model="gfs", target_date="2026-05-05")
    assert shared == legacy
    assert shared["bias_n_asof"] == 3
    assert math.isclose(shared["bias_mean_asof"], 0.666667)
    assert math.isclose(shared["hot_tail_pct_asof"], 2 / 3, abs_tol=1e-6)


def test_city_source_bias_classifier_contract() -> None:
    assert (
        bias.classify_city_source_bias(
            {
                "bias": 0.8,
                "p90": 2.5,
                "p10": -0.5,
                "pct_actual_ge_forecast_plus_1": 0.45,
                "pct_forecast_ge_actual_plus_1": 0.10,
                "mae": 1.4,
            }
        )
        == "hot_underforecast_clean"
    )
    assert (
        bias.classify_city_source_bias(
            {
                "bias": -0.6,
                "p90": 0.5,
                "p10": -2.0,
                "pct_actual_ge_forecast_plus_1": 0.10,
                "pct_forecast_ge_actual_plus_1": 0.40,
                "mae": 1.2,
            }
        )
        == "cold_overforecast_clean"
    )


def test_weather_state_frame_builder_carries_metadata_and_unit_contract() -> None:
    snapshot_rows = [
        {
            "city": "LA",
            "target_date": "2026-07-06",
            "snapshot_ts_utc": "2026-07-06T19:00:00Z",
            "unit": "F",
            "timezone_name": "America/Los_Angeles",
            "forecast_source": "gfs",
            "forecast_max_native": 72.8,
            "forecast_peak_hour_local": 14,
            "forecast_peak_delta_hours_local": -2,
        },
        {
            "city": "London",
            "target_date": "2026-07-06",
            "snapshot_ts_utc": "2026-07-06T13:00:00Z",
            "unit": "C",
            "timezone_name": "Europe/London",
            "forecast_source": "ecmwf",
            "forecast_max_native": 22.5,
            "forecast_peak_hour_local": 16,
            "forecast_peak_delta_hours_local": -2,
        },
    ]
    observation_cache = {
        "records": [
            {
                "city": "LA",
                "target_date": "2026-07-06",
                "status": "ok",
                "source": "aviationweather_metar",
                "station": "KLAX",
                "last_obs_utc": "2026-07-06T18:20:00Z",
                "cadence_min": 60,
                "current_temp_c": 20,
                "running_max_c": 21,
                "tmpf_now": 68,
                "dwpf_now": 55,
                "dewpoint_depression_f": 13,
                "relative_humidity_pct": 55,
                "wind_speed_kt": 12,
                "wind_dir_deg": 240,
                "sky_now": "FEW",
                "temp_trend_1h_f": 1.4,
                "temp_trend_3h_f": 2.2,
                "minutes_since_running_max": 35,
                "running_max_obs_utc": "2026-07-06T18:00:00Z",
            },
            {
                "city": "London",
                "target_date": "2026-07-06",
                "status": "ok",
                "source": "aviationweather_metar",
                "station": "EGLL",
                "last_obs_utc": "2026-07-06T12:30:00Z",
                "cadence_min": 60,
                "current_temp_c": 20,
                "running_max_c": 21,
                "relative_humidity_pct": 82,
                "wind_speed_kt": 8,
                "wind_dir_deg": 180,
                "sky_cover_code": 3,
                "temp_trend_1h_f": 0.2,
                "temp_trend_3h_f": -0.1,
                "minutes_since_running_max": 90,
            },
        ]
    }

    frame, audits = build_weather_state_frame_with_audits(
        snapshot_rows,
        observation_cache,
        as_of_ts_utc="2026-07-06T19:00:00Z",
        source_profile_id="mac_weather_data_feed_v1",
        input_snapshot_id="fixture-snapshot",
    )

    assert [audit.status for audit in audits] == ["included", "included"]
    assert set(frame["city"]) == {"LA", "London"}
    assert frame.attrs["feature_metadata"]["pit_provenance"] == PIT_PROVENANCE_ARCHIVE_RECONSTRUCTION
    for key in FEATURE_FRAME_REQUIRED_METADATA:
        assert key in frame.columns

    la = frame.set_index("city").loc["LA"]
    assert math.isclose(la["current_native"], 68.0)
    assert math.isclose(la["running_native"], 69.8)
    assert math.isclose(la["forecast_gap_to_running_native"], 3.0)
    assert la["decision_hour_local"] == 12
    assert la["station_gap_state"] == "within_expected_cadence"
    assert la["sky_cover_code"] == 1
    assert la["warming_state"] == "warming"
    assert la["solar_window"] == "solar_peak_window"
    assert la["pit_provenance"] == PIT_PROVENANCE_ARCHIVE_RECONSTRUCTION

    london = frame.set_index("city").loc["London"]
    assert math.isclose(london["current_native"], 20.0)
    assert math.isclose(london["running_native"], 21.0)
    assert math.isclose(london["forecast_gap_to_running_native"], 1.5)
    assert math.isclose(london["forecast_max_f"], 72.5)
    assert london["station_gap_state"] == "within_expected_cadence"
    assert london["moisture_cloud_regime"] == "humid_overcast_suppression"


def test_weather_state_frame_builder_rejects_invalid_pit_provenance() -> None:
    snapshot_rows = [
        {
            "city": "LA",
            "target_date": "2026-07-06",
            "snapshot_ts_utc": "2026-07-06T19:00:00Z",
            "unit": "F",
        }
    ]
    observation_cache = {
        "records": [
            {
                "city": "LA",
                "target_date": "2026-07-06",
                "status": "ok",
                "source": "aviationweather_metar",
                "station": "KLAX",
                "current_temp_c": 20,
                "running_max_c": 21,
            }
        ]
    }

    with pytest.raises(ValueError, match="invalid pit_provenance"):
        build_weather_state_frame(
            snapshot_rows,
            observation_cache,
            as_of_ts_utc="2026-07-06T19:00:00Z",
            pit_provenance="detect_time_only",
        )
