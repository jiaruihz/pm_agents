from __future__ import annotations

import math
from typing import Any


# Strategy code should import shared point-in-time context helpers through
# weather_feature_layer.state after the feature-layer cutover. This module stays
# physically in weather_data_feed during Phase 1 to preserve L1 -> L0 dependency
# direction for vendored data-feed deployments.
CITY_WIND_CONTEXT: dict[str, dict[str, Any]] = {
    "Amsterdam": {"geo": "coastal_marine", "onshore": [(210, 330)]},
    "Atlanta": {"geo": "inland_humid", "onshore": []},
    "Austin": {"geo": "inland_plain", "onshore": []},
    "Beijing": {"geo": "inland_plain", "onshore": []},
    "BuenosAires": {"geo": "coastal_marine", "onshore": [(30, 150)]},
    "Busan": {"geo": "coastal_humid", "onshore": [(90, 210)]},
    "CapeTown": {"geo": "coastal_marine", "onshore": [(150, 300)]},
    "Chengdu": {"geo": "basin_inland", "onshore": []},
    "Chongqing": {"geo": "basin_inland", "onshore": []},
    "Dallas": {"geo": "inland_plain", "onshore": []},
    "Denver": {"geo": "high_plain", "onshore": []},
    "Guangzhou": {"geo": "coastal_humid", "onshore": [(90, 210)]},
    "Helsinki": {"geo": "coastal_marine", "onshore": [(120, 240)]},
    "Houston": {"geo": "coastal_humid", "onshore": [(90, 210)]},
    "Istanbul": {"geo": "coastal_marine", "onshore": [(0, 120), (240, 360)]},
    "Jeddah": {"geo": "coastal_desert", "onshore": [(210, 330)]},
    "Karachi": {"geo": "coastal_desert", "onshore": [(180, 300)]},
    "LA": {"geo": "coastal_marine", "onshore": [(180, 300)]},
    "London": {"geo": "coastal_marine", "onshore": [(120, 300)]},
    "Lucknow": {"geo": "inland_plain", "onshore": []},
    "Madrid": {"geo": "inland_plateau", "onshore": []},
    "Manila": {"geo": "coastal_humid", "onshore": [(180, 330)]},
    "Miami": {"geo": "coastal_humid", "onshore": [(60, 180)]},
    "Munich": {"geo": "inland_alpine_edge", "onshore": []},
    "NYC": {"geo": "coastal_marine", "onshore": [(90, 210)]},
    "SanFrancisco": {"geo": "coastal_marine", "onshore": [(210, 330)]},
    "SaoPaulo": {"geo": "inland_plateau", "onshore": []},
    "Seattle": {"geo": "coastal_marine", "onshore": [(180, 300)]},
    "Shanghai": {"geo": "coastal_humid", "onshore": [(60, 180)]},
    "Singapore": {"geo": "coastal_humid", "onshore": [(120, 240)]},
    "Taipei": {"geo": "coastal_humid", "onshore": [(60, 180)]},
    "TelAviv": {"geo": "coastal_desert", "onshore": [(210, 330)]},
    "Tokyo": {"geo": "coastal_humid", "onshore": [(90, 210)]},
    "Warsaw": {"geo": "inland_plain", "onshore": []},
    "Wellington": {"geo": "coastal_marine", "onshore": [(120, 300)]},
    "Wuhan": {"geo": "inland_humid", "onshore": []},
}


def safe_float(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return math.nan
    return out if math.isfinite(out) else math.nan


def wind_direction_sector(deg: Any) -> str:
    value = safe_float(deg)
    if not math.isfinite(value):
        return "unknown"
    if value <= 0:
        return "calm_or_variable"
    sectors = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return sectors[int(((value % 360.0) + 22.5) // 45.0) % 8]


def _in_direction_ranges(deg: float, ranges: list[tuple[int, int]]) -> bool:
    for lo, hi in ranges:
        if lo <= hi and lo <= deg <= hi:
            return True
        if lo > hi and (deg >= lo or deg <= hi):
            return True
    return False


def city_wind_context(city: str, wind_dir_deg: Any) -> dict[str, Any]:
    ctx = CITY_WIND_CONTEXT.get(str(city), {"geo": "unknown_geo", "onshore": []})
    geo_context = str(ctx.get("geo") or "unknown_geo")
    coastal = geo_context.startswith("coastal")
    wind_dir = safe_float(wind_dir_deg)
    if not math.isfinite(wind_dir):
        flow_state = "flow_unknown"
    elif not coastal:
        flow_state = "non_coastal_flow"
    elif _in_direction_ranges(wind_dir % 360.0, list(ctx.get("onshore") or [])):
        flow_state = "onshore_marine_flow"
    else:
        flow_state = "offshore_or_parallel_flow"
    return {
        "geo_context": geo_context,
        "is_coastal_context": coastal,
        "wind_sector": wind_direction_sector(wind_dir),
        "coastal_flow_state": flow_state,
    }


def sky_state(sky_cover_code: Any) -> str:
    sky = safe_float(sky_cover_code)
    if not math.isfinite(sky):
        return "sky_unknown"
    if sky <= 1:
        return "clear_or_few"
    if sky <= 2:
        return "scattered_cloud"
    if sky <= 3:
        return "broken_cloud"
    return "overcast_or_vertical"


def moisture_state(relative_humidity_pct: Any, dewpoint_depression_f: Any) -> str:
    rh = safe_float(relative_humidity_pct)
    dew_dep = safe_float(dewpoint_depression_f)
    if not math.isfinite(rh) and not math.isfinite(dew_dep):
        return "moisture_unknown"
    if math.isfinite(rh) and rh >= 85:
        return "very_humid"
    if math.isfinite(rh) and rh >= 75:
        return "humid"
    if math.isfinite(dew_dep) and dew_dep >= 25:
        return "very_dry_deep_mixing"
    if math.isfinite(dew_dep) and dew_dep >= 15:
        return "dry_mixed_layer"
    return "mixed_moisture"


def warming_state(temp_trend_1h_f: Any, temp_trend_3h_f: Any) -> str:
    trend1 = safe_float(temp_trend_1h_f)
    trend3 = safe_float(temp_trend_3h_f)
    if not math.isfinite(trend1) and not math.isfinite(trend3):
        return "warming_unknown"
    primary = trend1 if math.isfinite(trend1) else trend3 / 3.0
    if primary <= -1.0:
        return "cooling"
    if primary < 0.5:
        return "flat"
    if primary < 2.0:
        return "warming"
    return "fast_warming"


def cloud_warming_interaction(sky_cover_code: Any, temp_trend_1h_f: Any, temp_trend_3h_f: Any) -> str:
    sky = safe_float(sky_cover_code)
    trend1 = safe_float(temp_trend_1h_f)
    trend3 = safe_float(temp_trend_3h_f)
    if not math.isfinite(sky) or (not math.isfinite(trend1) and not math.isfinite(trend3)):
        return "cloud_warming_unknown"
    trend = trend1 if math.isfinite(trend1) else trend3 / 3.0
    if sky >= 3 and trend >= 1.0:
        return "warming_through_cloud"
    if sky >= 3 and trend < 0.5:
        return "cloud_limited_flat_or_cooling"
    if sky <= 1 and trend >= 1.0:
        return "clear_solar_warming"
    if sky <= 1 and trend < 0.5:
        return "clear_but_not_warming"
    if trend >= 1.0:
        return "mixed_sky_warming"
    return "mixed_sky_flat_or_cooling"


def moisture_cloud_interaction(relative_humidity_pct: Any, dewpoint_depression_f: Any, sky_cover_code: Any) -> str:
    rh = safe_float(relative_humidity_pct)
    dew_dep = safe_float(dewpoint_depression_f)
    sky = safe_float(sky_cover_code)
    if not any(math.isfinite(value) for value in (rh, dew_dep, sky)):
        return "moisture_cloud_unknown"
    if math.isfinite(rh) and rh >= 80 and math.isfinite(sky) and sky >= 3:
        return "humid_cloud_suppression"
    if math.isfinite(rh) and rh >= 75:
        return "humid_convective_risk"
    if math.isfinite(sky) and sky >= 3:
        return "cloud_suppression"
    if math.isfinite(dew_dep) and dew_dep >= 25:
        return "dry_heat_inertia"
    return "mixed_moisture_cloud"


def wind_thermal_interaction(
    city: str,
    wind_speed_kt: Any,
    wind_dir_deg: Any = math.nan,
) -> dict[str, Any]:
    wind = safe_float(wind_speed_kt)
    context = city_wind_context(city, wind_dir_deg)
    flow = str(context["coastal_flow_state"])
    geo = str(context["geo_context"])
    if not math.isfinite(wind):
        wind_state = "wind_unknown"
    elif wind >= 18:
        wind_state = "windy_mixing_noise"
    elif wind >= 10:
        wind_state = "moderate_mixing"
    else:
        wind_state = "light_wind"

    if not math.isfinite(wind):
        marine_state = "marine_wind_unknown"
    elif flow == "onshore_marine_flow" and wind >= 10:
        marine_state = "onshore_marine_cooling_risk"
    elif flow == "offshore_or_parallel_flow" and wind >= 10:
        marine_state = "offshore_or_parallel_warming_risk"
    elif geo.startswith("coastal") and flow == "flow_unknown" and wind >= 10:
        marine_state = "coastal_direction_unknown_mixing"
    elif geo.startswith("coastal"):
        marine_state = "coastal_light_or_unclear_flow"
    else:
        marine_state = "inland_wind_mixing"
    return {**context, "wind_thermal_state": wind_state, "marine_thermal_state": marine_state}


def forecast_peak_clock_state(forecast_peak_delta_hours_local: Any) -> str:
    delta = safe_float(forecast_peak_delta_hours_local)
    if not math.isfinite(delta):
        return "forecast_peak_unknown"
    if delta <= -2:
        return "forecast_peak_2h_plus_ahead"
    if delta <= 0:
        return "forecast_peak_0_to_2h_ahead"
    if delta <= 1:
        return "forecast_peak_passed_0_to_1h"
    if delta <= 2:
        return "forecast_peak_passed_1_to_2h"
    return "forecast_peak_passed_2h_plus"


def temperature_context_features(record: dict[str, Any]) -> dict[str, Any]:
    city = str(record.get("city") or "")
    wind = wind_thermal_interaction(city, record.get("wind_speed_kt"), record.get("wind_dir_deg", math.nan))
    cloud_warming = cloud_warming_interaction(
        record.get("sky_cover_code"),
        record.get("temp_trend_1h_f"),
        record.get("temp_trend_3h_f"),
    )
    moisture_cloud = moisture_cloud_interaction(
        record.get("relative_humidity_pct"),
        record.get("dewpoint_depression_f"),
        record.get("sky_cover_code"),
    )
    warming = warming_state(record.get("temp_trend_1h_f"), record.get("temp_trend_3h_f"))
    peak_clock = forecast_peak_clock_state(record.get("forecast_peak_delta_hours_local"))
    return {
        **wind,
        "sky_state": sky_state(record.get("sky_cover_code")),
        "moisture_state": moisture_state(record.get("relative_humidity_pct"), record.get("dewpoint_depression_f")),
        "warming_state": warming,
        "cloud_warming_interaction": cloud_warming,
        "moisture_cloud_interaction": moisture_cloud,
        "forecast_peak_clock_state": peak_clock,
        "temperature_context_regime": " | ".join(
            [
                peak_clock,
                warming,
                cloud_warming,
                moisture_cloud,
                str(wind["marine_thermal_state"]),
            ]
        ),
    }


def temperature_context_multiplier(record: dict[str, Any], *, strength: str = "light") -> float:
    """Fixed temperature-context sizing overlay for regime-routed NO shadowing.

    Strategy-private compatibility shim. Do not expose this through
    weather_feature_layer.state.

    This is deliberately first-principles and pre-declared. It is not fit to
    realized payoff rows. The meaning of the same weather state depends on the
    route: current-bracket NO wants later pass-through, while capped d2 NO wants
    the higher bracket to remain unhit.
    """
    route = str(record.get("route_leg") or "")
    cloud = str(record.get("cloud_warming_interaction") or "")
    moisture = str(record.get("moisture_cloud_interaction") or "")
    marine = str(record.get("marine_thermal_state") or "")
    peak = str(record.get("forecast_peak_clock_state") or "")
    warming = str(record.get("warming_state") or "")

    if route == "runway_current_no":
        value = 1.0
        if peak == "forecast_peak_2h_plus_ahead":
            value *= 1.08
        elif peak == "forecast_peak_0_to_2h_ahead":
            value *= 1.03
        elif peak == "forecast_peak_passed_0_to_1h":
            value *= 0.72
        elif peak in {"forecast_peak_passed_1_to_2h", "forecast_peak_passed_2h_plus"}:
            value *= 0.50

        if cloud in {"clear_solar_warming", "mixed_sky_warming", "warming_through_cloud"}:
            value *= 1.08
        elif cloud in {"clear_but_not_warming", "cloud_limited_flat_or_cooling", "mixed_sky_flat_or_cooling"}:
            value *= 0.82

        if moisture in {"cloud_suppression", "humid_cloud_suppression"}:
            value *= 0.88
        elif moisture == "dry_heat_inertia":
            value *= 1.03

        if marine == "onshore_marine_cooling_risk":
            value *= 0.86
        elif marine == "offshore_or_parallel_warming_risk":
            value *= 1.05
    else:
        value = 1.0
        if peak == "forecast_peak_2h_plus_ahead":
            value *= 0.82
        elif peak == "forecast_peak_0_to_2h_ahead":
            value *= 0.94
        elif peak == "forecast_peak_passed_0_to_1h":
            value *= 1.04
        elif peak in {"forecast_peak_passed_1_to_2h", "forecast_peak_passed_2h_plus"}:
            value *= 1.08

        if cloud in {"clear_solar_warming", "mixed_sky_warming", "warming_through_cloud"}:
            value *= 0.86
        elif cloud in {"clear_but_not_warming", "cloud_limited_flat_or_cooling", "mixed_sky_flat_or_cooling"}:
            value *= 1.06

        if moisture in {"cloud_suppression", "humid_cloud_suppression"}:
            value *= 1.04
        elif moisture == "dry_heat_inertia" and warming in {"warming", "fast_warming"}:
            value *= 0.94

        if marine == "onshore_marine_cooling_risk":
            value *= 1.05
        elif marine == "offshore_or_parallel_warming_risk":
            value *= 0.94

    if strength == "medium":
        value = 1.0 + 1.55 * (value - 1.0)
        return max(0.35, min(1.20, float(value)))
    return max(0.55, min(1.12, float(value)))
