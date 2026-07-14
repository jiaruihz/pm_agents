"""Version and metadata contracts for weather feature frames."""

from __future__ import annotations

FEATURE_FRAME_SCHEMA_VERSION = "feature_frame_v1"
WEATHER_STATE_VERSION = "weather_state_v2"
WEATHER_REGIME_VERSION = "weather_regime_v1"
WEATHER_BIAS_VERSION = "weather_bias_v1"
MARKET_GEOMETRY_VERSION = "market_geometry_v1"
EXECUTION_FEATURE_VERSION = "execution_features_v1"

PIT_PROVENANCE_LIVE_CAPTURE = "live_capture"
PIT_PROVENANCE_ARCHIVE_RECONSTRUCTION = "archive_reconstruction"
PIT_PROVENANCE_VALUES = {
    PIT_PROVENANCE_LIVE_CAPTURE,
    PIT_PROVENANCE_ARCHIVE_RECONSTRUCTION,
}

DEFAULT_FEATURE_VERSION_MANIFEST = {
    "weather_state": WEATHER_STATE_VERSION,
    "weather_regime": WEATHER_REGIME_VERSION,
    "weather_bias": WEATHER_BIAS_VERSION,
    "market_geometry": MARKET_GEOMETRY_VERSION,
    "execution_features": EXECUTION_FEATURE_VERSION,
}

FEATURE_FRAME_REQUIRED_METADATA = (
    "feature_schema_version",
    "feature_grain",
    "as_of_ts_utc",
    "source_profile_id",
    "feature_version_manifest",
    "pit_provenance",
    "builder_version",
    "input_snapshot_id",
)

UNIT_SPACE_F = "F"
UNIT_SPACE_NATIVE = "native"

FIELD_UNIT_CONTRACT = {
    "temp_trend_1h_f": UNIT_SPACE_F,
    "temp_trend_3h_f": UNIT_SPACE_F,
    "dewpoint_depression_f": UNIT_SPACE_F,
    "current_temp_native": UNIT_SPACE_NATIVE,
    "running_max_native": UNIT_SPACE_NATIVE,
    "decline_from_running_max_native": UNIT_SPACE_NATIVE,
    "forecast_gap_to_running_native": UNIT_SPACE_NATIVE,
    "bracket_distance_native": UNIT_SPACE_NATIVE,
    "wind_dir_deg": "degree",
    "wind_speed_kt": "knot",
    "lowest_cloud_base_ft_agl": "foot_agl",
    "ceiling_ft_agl": "foot_agl",
    "obs_age_minutes": "minute",
    "expected_report_cadence": "minute",
    "solar_elevation_deg": "degree",
    "daylight_remaining_minutes": "minute",
    "forecast_precip_probability_to_peak_max_pct": "percent",
    "forecast_precip_probability_remaining_3h_max_pct": "percent",
    "forecast_cloud_cover_remaining_3h_mean_pct": "percent",
    "forecast_wind_speed_remaining_3h_max_kt": "knot",
    "forecast_wind_direction_remaining_3h_mean_deg": "degree",
}

WEATHER_PHYSICAL_FEATURE_FIELDS = (
    "precip_state",
    "precip_intensity_code",
    "present_weather_codes",
    "cloud_layer_count",
    "lowest_cloud_base_ft_agl",
    "ceiling_ft_agl",
    "cloud_cover_change_1h_code",
    "wind_dir_deg",
    "wind_dir_sin",
    "wind_dir_cos",
    "wind_speed_change_1h_kt",
    "obs_age_minutes",
    "expected_report_cadence",
    "obs_cadence_ratio",
    "minutes_to_next_expected_obs",
    "source_latency_minutes",
    "solar_elevation_deg",
    "solar_elevation_2h_deg",
    "solar_elevation_delta_2h_deg",
    "daylight_remaining_minutes",
    "solar_heating_potential",
    "forecast_precip_probability_to_peak_max_pct",
    "forecast_cloud_cover_to_peak_mean_pct",
    "forecast_wind_speed_to_peak_max_kt",
    "forecast_wind_direction_to_peak_mean_deg",
    "forecast_remaining_3h_status",
    "forecast_remaining_3h_hour_count",
    "forecast_precip_probability_remaining_3h_max_pct",
    "forecast_cloud_cover_remaining_3h_mean_pct",
    "forecast_wind_speed_remaining_3h_max_kt",
    "forecast_wind_direction_remaining_3h_mean_deg",
)
