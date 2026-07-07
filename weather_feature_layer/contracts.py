"""Version and metadata contracts for weather feature frames."""

from __future__ import annotations

FEATURE_FRAME_SCHEMA_VERSION = "feature_frame_v1"
WEATHER_STATE_VERSION = "weather_state_v1"
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
}
