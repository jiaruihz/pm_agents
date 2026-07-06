"""Canonical feature-layer entrypoint for weather context state.

Phase 1 intentionally re-exports the existing ``weather_data_feed`` helpers.
The dependency direction is L1 -> L0: ``weather_data_feed`` must not import this
package.
"""

from __future__ import annotations

from weather_data_feed.weather_context import (
    CITY_WIND_CONTEXT,
    city_wind_context,
    cloud_warming_interaction,
    forecast_peak_clock_state,
    moisture_cloud_interaction,
    moisture_state,
    safe_float,
    sky_state,
    temperature_context_features,
    warming_state,
    wind_direction_sector,
    wind_thermal_interaction,
)

__all__ = [
    "CITY_WIND_CONTEXT",
    "city_wind_context",
    "cloud_warming_interaction",
    "forecast_peak_clock_state",
    "moisture_cloud_interaction",
    "moisture_state",
    "safe_float",
    "sky_state",
    "temperature_context_features",
    "warming_state",
    "wind_direction_sector",
    "wind_thermal_interaction",
]
