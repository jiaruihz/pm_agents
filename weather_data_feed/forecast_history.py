from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

from weather_data_feed.city_calendar import city_timezone_name
from weather_clock_contract import local_wall_time_to_utc, parse_utc_or_none


def forecast_hourly_daily_max_local(payload: dict[str, Any], *, city: str) -> dict[str, float]:
    """Aggregate an hourly forecast archive by the city's local calendar date.

    Open-Meteo archive caches may contain naive timestamps in GMT.  Their date
    prefix is therefore not a station-local date and must not be joined directly
    to WU ``date_local`` observations.
    """
    target_tz_name = city_timezone_name(city)
    if not target_tz_name:
        raise ValueError(f"missing city timezone for forecast archive: {city}")
    target_tz = ZoneInfo(target_tz_name)

    source_tz_name = str(payload.get("timezone") or "").strip()
    if source_tz_name:
        try:
            ZoneInfo(source_tz_name)
        except Exception as exc:
            raise ValueError(f"invalid forecast archive timezone={source_tz_name!r}") from exc
    else:
        raise ValueError("forecast archive requires an IANA source timezone")

    hourly = payload.get("hourly") if isinstance(payload.get("hourly"), dict) else {}
    times = hourly.get("time") or []
    temperatures = hourly.get("temperature_2m") or []
    daily: dict[str, float] = {}
    for value, temperature in zip(times, temperatures, strict=False):
        if temperature is None:
            continue
        try:
            observed_utc = parse_utc_or_none(
                value, field="forecast_archive_hourly_time"
            )
            if observed_utc is None:
                observed_utc = local_wall_time_to_utc(
                    str(value),
                    timezone_name=source_tz_name,
                    field="forecast_archive_hourly_local_time",
                )
            local_date = observed_utc.astimezone(target_tz).date().isoformat()
            temp = float(temperature)
        except (TypeError, ValueError):
            continue
        daily[local_date] = max(daily.get(local_date, float("-inf")), temp)
    return daily
