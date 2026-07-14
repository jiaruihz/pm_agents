from __future__ import annotations

import math

from weather_data_feed.forecast_hourly_curves import build_hourly_curve
from weather_data_feed.models import ObservationRecord
from weather_data_feed.observation_sources.fetchers import one_hour_observation_changes
from weather_data_feed.physical_features import metar_physical_features, solar_geometry_features
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
    }


def test_shared_weather_state_v2_joins_pit_curve_and_physical_features() -> None:
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
    assert WEATHER_STATE_VERSION == "weather_state_v2"
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
