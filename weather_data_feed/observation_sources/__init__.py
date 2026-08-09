"""Official observation source adapters and parsers.

This package is intentionally data-only. It should not contain strategy
selection, orderbook logic, sizing, or live order submission.
"""

from weather_data_feed.observation_sources.aliases import SOURCE_ALIASES, expand_source_names, normalize_source_name
from weather_data_feed.observation_sources.aviationweather import (
    parse_aviationweather_records,
    parse_awc_cache_csv_records,
)
from weather_data_feed.observation_sources.fetchers import (
    FetchSettings,
    ObservationFetchError,
    fetch_observation_source,
    infer_cadence_min,
    observation_path_features,
    parse_metar_rmk_temp_c,
    snapshot_observation_source,
    source_station_id,
    stable_hash,
    synoptic_obs_lists,
)
from weather_data_feed.observation_sources.iem import (
    IEM_ASOS_API,
    build_iem_asos_params,
    build_iem_local_day_params,
    iem_request_dates,
    parse_iem_asos_records,
    parse_iem_asos_temperature_obs,
)
from weather_data_feed.observation_sources.metar import (
    parse_metar_report_time,
    parse_metar_temp_c,
    parse_tgftp_header_time,
)
from weather_data_feed.observation_sources.router import (
    ObservationSourceAdapter,
    ObservationSourceError,
    ObservationSourceRequest,
    ObservationSourceResult,
    SourceRouter,
)

__all__ = [
    "ObservationSourceAdapter",
    "ObservationSourceError",
    "ObservationSourceRequest",
    "ObservationSourceResult",
    "FetchSettings",
    "IEM_ASOS_API",
    "ObservationFetchError",
    "SOURCE_ALIASES",
    "SourceRouter",
    "build_iem_asos_params",
    "build_iem_local_day_params",
    "expand_source_names",
    "fetch_observation_source",
    "iem_request_dates",
    "infer_cadence_min",
    "observation_path_features",
    "normalize_source_name",
    "parse_aviationweather_records",
    "parse_awc_cache_csv_records",
    "parse_iem_asos_records",
    "parse_iem_asos_temperature_obs",
    "parse_metar_report_time",
    "parse_metar_rmk_temp_c",
    "parse_metar_temp_c",
    "parse_tgftp_header_time",
    "snapshot_observation_source",
    "source_station_id",
    "stable_hash",
    "synoptic_obs_lists",
]
