"""Compatibility imports for the shared weather_data_feed package."""

from weather_data_feed import (
    CityConfig,
    CrossingEvent,
    ObservationRecord,
    RunningMaxState,
    SourceProfile,
    bracket_contains,
    build_city_policy,
    load_city_configs,
    load_source_profiles,
    parse_label_dict,
    parse_market_bracket,
    source_profile_for_city,
)

__all__ = [
    "CityConfig",
    "CrossingEvent",
    "ObservationRecord",
    "RunningMaxState",
    "SourceProfile",
    "bracket_contains",
    "build_city_policy",
    "load_city_configs",
    "load_source_profiles",
    "parse_label_dict",
    "parse_market_bracket",
    "source_profile_for_city",
]
