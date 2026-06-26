from __future__ import annotations

import math
from typing import Any


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
