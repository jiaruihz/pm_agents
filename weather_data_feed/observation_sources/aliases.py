from __future__ import annotations

from typing import Iterable


SOURCE_ALIASES: dict[str, str] = {
    "aviationweather": "aviationweather_metar",
    "aviationweather_metar": "aviationweather_metar",
    "aviationweather_cache": "aviationweather_cache_csv",
    "aviationweather_cache_csv": "aviationweather_cache_csv",
    "awc_cache": "aviationweather_cache_csv",
    "checkwx": "checkwx_html",
    "checkwx_html": "checkwx_html",
    "iem": "iem_asos",
    "iem_asos": "iem_asos",
    "ldm": "ldm_metar",
    "ldm_metar": "ldm_metar",
    "noaa_tgftp": "noaa_tgftp_station_txt",
    "noaa_tgftp_station_txt": "noaa_tgftp_station_txt",
    "tgftp": "noaa_tgftp_station_txt",
    "nws_api": "weather_gov_latest",
    "nws_api_latest": "weather_gov_latest",
    "weather_gov": "weather_gov_latest",
    "weather_gov_latest": "weather_gov_latest",
    "synoptic": "synopticdata_timeseries",
    "synopticdata": "synopticdata_timeseries",
    "synopticdata_timeseries": "synopticdata_timeseries",
    "weather_gov_wrh": "synopticdata_timeseries",
    "wrh_timeseries": "synopticdata_timeseries",
}


def normalize_source_name(source_name: str) -> str:
    key = str(source_name or "").strip().lower()
    return SOURCE_ALIASES.get(key, key)


def expand_source_names(raw_names: Iterable[str], *, primary: str = "", fallback_sources: Iterable[str] = ()) -> list[str]:
    out: list[str] = []
    for raw_name in raw_names:
        if raw_name == "source_profiles":
            candidates = [primary, *fallback_sources]
        elif raw_name == "profile_primary":
            candidates = [primary]
        elif raw_name == "profile_fallbacks":
            candidates = list(fallback_sources)
        else:
            candidates = [raw_name]
        for candidate in candidates:
            normalized = normalize_source_name(candidate)
            if normalized and normalized not in out:
                out.append(normalized)
    return out
