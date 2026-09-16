from __future__ import annotations

import math

import pytest

from weather_data_feed.forecast_hourly_curves import build_hourly_curve
from weather_data_feed.models import ObservationRecord
from weather_data_feed.observation_sources.fetchers import observation_path_features, one_hour_observation_changes
from weather_data_feed.physical_features import (
    forecast_window_features,
    metar_physical_features,
    observation_clock_features,
    physical_context_features,
    solar_geometry_features,
)
from weather_feature_layer.builders import build_weather_state_frame
from weather_feature_layer.contracts import WEATHER_PHYSICAL_FEATURE_FIELDS, WEATHER_STATE_VERSION


def test_metar_physical_parser_has_one_precip_cloud_wind_contract() -> None:
    parsed = metar_physical_features(
        "METAR KLAX 061853Z 24012KT 3SM -TSRA BKN018 OVC035 20/17 A2992"
    )

    assert parsed["precip_state"] == "thunderstorm"
    assert parsed["precip_intensity_code"] == 1
    assert parsed["thunderstorm_observed"] is True
    assert parsed["cloud_layer_count"] == 2
    assert parsed["lowest_cloud_base_ft_agl"] == 1800
    assert parsed["ceiling_ft_agl"] == 1800
    assert parsed["metar_wind_dir_deg"] == 240
    assert parsed["metar_wind_speed_kt"] == 12
    assert abs(parsed["pressure_hpa"] - 1013.207) < 0.001

    tokyo = metar_physical_features(
        "METAR RJTT 291600Z 28004KT CAVOK 30/19 Q1004 NOSIG"
    )
    assert tokyo["pressure_hpa"] == 1004.0


def test_solar_geometry_is_continuous_and_explicit_when_coordinates_missing() -> None:
    noon = solar_geometry_features(
        {
            "decision_snapshot_ts_utc": "2026-07-06T19:00:00Z",
            "latitude": 33.9416,
            "longitude": -118.4085,
        }
    )
    missing = solar_geometry_features({"decision_snapshot_ts_utc": "2026-07-06T19:00:00Z"})

    assert noon["solar_geometry_status"] == "ok"
    assert noon["solar_elevation_deg"] > 70
    assert noon["daylight_remaining_minutes"] > 300
    assert missing["solar_geometry_status"] == "missing_timestamp_or_coordinates"
    assert missing["solar_elevation_deg"] is None


def test_observation_clock_preserves_overdue_report_minutes() -> None:
    clock = observation_clock_features({"obs_age_minutes": 42, "expected_report_cadence": 30})
    assert clock["minutes_to_next_expected_obs"] == -12


def test_forecast_remaining_three_hours_survives_after_peak() -> None:
    features = forecast_window_features(
        {
            "decision_hour_local": 13.2,
            "forecast_peak_hour_local": 12,
            "hourly_curve": build_hourly_curve(
                [
                    "2026-07-14T13:00",
                    "2026-07-14T14:00",
                    "2026-07-14T15:00",
                    "2026-07-14T16:00",
                    "2026-07-14T17:00",
                ],
                [87, 86, 85, 84, 83],
                precipitation_probability_pct=[58, 72, 84, 91, 95],
                cloud_cover_pct=[72, 78, 84, 89, 95],
                wind_speed_10m_kt=[10, 11, 12, 12, 11],
                wind_direction_10m_deg=[215, 208, 204, 204, 206],
            ),
        }
    )

    assert features["forecast_weather_window_status"] == "forecast_peak_passed"
    assert features["forecast_precip_probability_to_peak_max_pct"] is None
    assert features["forecast_remaining_3h_status"] == "ok"
    assert features["forecast_remaining_3h_hour_count"] == 4
    assert features["forecast_precip_probability_remaining_3h_max_pct"] == 91
    assert features["forecast_cloud_cover_remaining_3h_mean_pct"] == pytest.approx((72 + 78 + 84 + 89) / 4)
    assert features["forecast_future_3h_hour_count"] == 3
    assert features["forecast_precip_probability_future_3h_max_pct"] == 91
    assert features["forecast_cloud_cover_future_3h_mean_pct"] == pytest.approx((78 + 84 + 89) / 3)
    assert features["forecast_temperature_at_decision_f"] == pytest.approx(86.8)
    assert features["forecast_remaining_max_f"] == pytest.approx(86.8)
    assert features["forecast_reheat_after_now_f"] == 0
    assert features["forecast_remaining_peak_hour_local"] == 13.2


def test_forecast_temperature_innovation_uses_interpolated_decision_minute() -> None:
    features = physical_context_features(
        {
            "unit": "C",
            "current_temp_native": 30.0,
            "decision_hour_local": 13.5,
            "forecast_peak_hour_local": 15,
            "hourly_curve": build_hourly_curve(
                ["2026-07-14T13:00", "2026-07-14T14:00", "2026-07-14T15:00"],
                [84.2, 86.0, 87.8],
            ),
        }
    )

    assert features["forecast_temperature_at_decision_f"] == pytest.approx(85.1)
    assert features["forecast_temperature_innovation_status"] == "ok"
    assert features["forecast_temperature_innovation_f"] == pytest.approx(0.9)
    assert features["forecast_temperature_innovation_native"] == pytest.approx(0.5)


def test_observation_change_features_share_the_same_record_history() -> None:
    common = {"source_key": "aviationweather_metar", "city": "LA", "target_date": "2026-07-06", "station_or_feed": "KLAX", "ingest_ts_utc": "2026-07-06T19:01:00Z", "temp_c": 20.0}
    prior = ObservationRecord(
        **common,
        obs_ts_utc="2026-07-06T18:00:00Z",
        wind_kt=6,
        sky_code="FEW",
        metadata={"ceiling_ft_agl": 5000},
    )
    latest = ObservationRecord(
        **common,
        obs_ts_utc="2026-07-06T19:00:00Z",
        wind_kt=12,
        sky_code="BKN",
        metadata={"ceiling_ft_agl": 1800},
    )

    changes = one_hour_observation_changes([prior, latest])
    assert changes == {
        "cloud_cover_change_1h_code": 2,
        "ceiling_change_1h_ft": -3200,
        "wind_speed_change_1h_kt": 6,
        "wind_dir_1h_prior_deg": None,
        "wind_dir_change_1h_deg": None,
        "dewpoint_change_1h_f": None,
        "relative_humidity_change_1h_pct": None,
    }


def test_equal_high_plateau_keeps_first_and_strict_high_clocks() -> None:
    common = {
        "source_key": "aviationweather_metar",
        "city": "Amsterdam",
        "target_date": "2026-07-16",
        "station_or_feed": "EHAM",
        "ingest_ts_utc": "2026-07-16T16:26:00Z",
    }
    records = [
        ObservationRecord(**common, obs_ts_utc="2026-07-16T10:55:00Z", temp_c=23, dewpoint_c=15, wind_kt=6, sky_code="CAVOK", raw_text="EHAM 161055Z 07006KT CAVOK 23/15"),
        ObservationRecord(**common, obs_ts_utc="2026-07-16T11:25:00Z", temp_c=24, dewpoint_c=15, wind_kt=6, sky_code="CAVOK", raw_text="EHAM 161125Z 04006KT CAVOK 24/15"),
        ObservationRecord(**common, obs_ts_utc="2026-07-16T12:55:00Z", temp_c=23, dewpoint_c=16, wind_kt=10, sky_code="FEW", raw_text="EHAM 161255Z 02010KT -RA FEW030 23/16"),
        ObservationRecord(**common, obs_ts_utc="2026-07-16T13:25:00Z", temp_c=24, dewpoint_c=16, wind_kt=8, sky_code="FEW", raw_text="EHAM 161325Z 02008KT FEW030 24/16"),
        ObservationRecord(**common, obs_ts_utc="2026-07-16T15:55:00Z", temp_c=24, dewpoint_c=13, wind_kt=12, sky_code="CAVOK", raw_text="EHAM 161555Z 04012KT CAVOK 24/13"),
        ObservationRecord(**common, obs_ts_utc="2026-07-16T16:25:00Z", temp_c=24, dewpoint_c=12, wind_kt=12, sky_code="CAVOK", raw_text="EHAM 161625Z 05012KT CAVOK 24/12"),
    ]

    features = observation_path_features(records, as_of_utc="2026-07-16T16:26:00Z")

    assert features["first_running_max_obs_utc"] == "2026-07-16T11:25:00+00:00"
    assert features["last_running_max_obs_utc"] == "2026-07-16T16:25:00+00:00"
    assert features["minutes_since_first_running_max"] == 301
    assert features["minutes_since_last_running_max"] == 1
    assert features["minutes_since_last_strict_new_high"] == 301
    assert features["same_running_max_obs_count"] == 4
    assert features["clear_sky_regime_minutes"] == 331
    assert features["precip_free_regime_minutes"] == 181
    assert features["first_precip_obs_utc"] == "2026-07-16T12:55:00+00:00"
    assert features["last_precip_obs_utc"] == "2026-07-16T12:55:00+00:00"
    assert features["precip_obs_count"] == 1
    assert features["minutes_since_last_precip_obs"] == 211


def test_observation_path_excludes_reports_not_visible_as_of() -> None:
    common = {
        "source_key": "aviationweather_metar",
        "city": "Amsterdam",
        "target_date": "2026-07-16",
        "station_or_feed": "EHAM",
    }
    records = [
        ObservationRecord(**common, obs_ts_utc="2026-07-16T10:00:00Z", ingest_ts_utc="2026-07-16T10:01:00Z", temp_c=23),
        ObservationRecord(**common, obs_ts_utc="2026-07-16T11:00:00Z", ingest_ts_utc="2026-07-16T11:01:00Z", temp_c=24),
        ObservationRecord(**common, obs_ts_utc="2026-07-16T12:00:00Z", ingest_ts_utc="2026-07-16T12:01:00Z", temp_c=25),
    ]

    features = observation_path_features(records, as_of_utc="2026-07-16T11:30:00Z")

    assert features["first_running_max_obs_utc"] == "2026-07-16T11:00:00+00:00"
    assert features["minutes_since_last_strict_new_high"] == 30
    assert features["same_running_max_obs_count"] == 1


def test_forecast_remaining_path_excludes_next_local_date() -> None:
    features = forecast_window_features(
        {
            "target_date": "2026-07-16",
            "decision_hour_local": 22.5,
            "forecast_peak_hour_local": 23,
            "hourly_curve": build_hourly_curve(
                ["2026-07-16T22:00", "2026-07-16T23:00", "2026-07-17T00:00"],
                [70, 69, 90],
            ),
        }
    )

    assert features["forecast_remaining_max_f"] == pytest.approx(69.5)
    assert features["forecast_remaining_peak_hour_local"] == 22.5


def test_shared_weather_state_v4_joins_pit_curve_and_physical_features() -> None:
    hourly_curve = build_hourly_curve(
        ["2026-07-06T12:00", "2026-07-06T13:00", "2026-07-06T14:00"],
        [70, 72, 73],
        precipitation_probability_pct=[20, 65, 40],
        cloud_cover_pct=[40, 80, 60],
        wind_speed_10m_kt=[8, 12, 10],
        wind_direction_10m_deg=[230, 240, 250],
    )
    frame = build_weather_state_frame(
        [
            {
                "city": "LA",
                "target_date": "2026-07-06",
                "snapshot_ts_utc": "2026-07-06T19:00:00Z",
                "decision_hour_local": 12,
                "unit": "F",
                "forecast_max_native": 73,
                "forecast_peak_hour_local": 14,
            }
        ],
        {
            "records": [
                {
                    "city": "LA",
                    "target_date": "2026-07-06",
                    "status": "ok",
                    "source": "aviationweather_metar",
                    "station": "KLAX",
                    "source_report_ts_utc": "2026-07-06T18:40:00Z",
                    "detect_ts_utc": "2026-07-06T18:43:00Z",
                    "cadence_min": 30,
                    "current_temp_c": 20,
                    "running_max_c": 21,
                    "raw_metar": "KLAX 061840Z 24012KT -RA BKN018 20/17 A2992",
                }
            ]
        },
        forecast_curve_rows=[
            {
                "city": "LA",
                "target_date": "2026-07-06",
                "snapshot_ts_utc": "2026-07-06T18:59:00Z",
                "available_at_utc": "2026-07-06T18:59:30Z",
                "latitude": 33.9416,
                "longitude": -118.4085,
                "hourly_curve": hourly_curve,
            },
            {
                "city": "LA",
                "target_date": "2026-07-06",
                "snapshot_ts_utc": "2026-07-06T19:01:00Z",
                "available_at_utc": "2026-07-06T19:01:30Z",
                "latitude": 0,
                "longitude": 0,
                "hourly_curve": [],
            },
        ],
        as_of_ts_utc="2026-07-06T19:00:00Z",
    )
    row = frame.iloc[0]

    assert frame.attrs["feature_metadata"]["feature_version_manifest"]["weather_state"] == WEATHER_STATE_VERSION
    assert WEATHER_STATE_VERSION == "weather_state_v4"
    assert all(field in frame.columns for field in WEATHER_PHYSICAL_FEATURE_FIELDS)
    assert row["precip_state"] == "rain_or_drizzle"
    assert row["ceiling_ft_agl"] == 1800
    assert row["wind_dir_deg"] == 240
    assert row["coastal_flow_state"] == "onshore_marine_flow"
    assert math.isclose(row["obs_age_minutes"], 20)
    assert math.isclose(row["obs_cadence_ratio"], 2 / 3)
    assert math.isclose(row["source_latency_minutes"], 3)
    assert row["forecast_weather_window_status"] == "ok"
    assert row["forecast_precip_probability_to_peak_max_pct"] == 65
    assert row["forecast_cloud_cover_to_peak_mean_pct"] == 60
    assert row["solar_geometry_status"] == "ok"
    assert row["latitude"] == 33.9416
