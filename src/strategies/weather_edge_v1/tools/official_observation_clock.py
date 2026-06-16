from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo


CITY_TIMEZONE: dict[str, str] = {
    "Amsterdam": "Europe/Amsterdam",
    "Ankara": "Europe/Istanbul",
    "Atlanta": "America/New_York",
    "Austin": "America/Chicago",
    "Beijing": "Asia/Shanghai",
    "BuenosAires": "America/Argentina/Buenos_Aires",
    "Busan": "Asia/Seoul",
    "CapeTown": "Africa/Johannesburg",
    "Chengdu": "Asia/Shanghai",
    "Chicago": "America/Chicago",
    "Chongqing": "Asia/Shanghai",
    "Dallas": "America/Chicago",
    "Denver": "America/Denver",
    "Guangzhou": "Asia/Shanghai",
    "Helsinki": "Europe/Helsinki",
    "HongKong": "Asia/Hong_Kong",
    "Houston": "America/Chicago",
    "Istanbul": "Europe/Istanbul",
    "Jakarta": "Asia/Jakarta",
    "Jeddah": "Asia/Riyadh",
    "Karachi": "Asia/Karachi",
    "KualaLumpur": "Asia/Kuala_Lumpur",
    "LA": "America/Los_Angeles",
    "Lagos": "Africa/Lagos",
    "London": "Europe/London",
    "Lucknow": "Asia/Kolkata",
    "Madrid": "Europe/Madrid",
    "Manila": "Asia/Manila",
    "MexicoCity": "America/Mexico_City",
    "Miami": "America/New_York",
    "Milan": "Europe/Rome",
    "Moscow": "Europe/Moscow",
    "Munich": "Europe/Berlin",
    "NYC": "America/New_York",
    "PanamaCity": "America/Panama",
    "Paris": "Europe/Paris",
    "SanFrancisco": "America/Los_Angeles",
    "SaoPaulo": "America/Sao_Paulo",
    "Seattle": "America/Los_Angeles",
    "Seoul": "Asia/Seoul",
    "Shanghai": "Asia/Shanghai",
    "Shenzhen": "Asia/Shanghai",
    "Singapore": "Asia/Singapore",
    "Taipei": "Asia/Taipei",
    "TelAviv": "Asia/Jerusalem",
    "Tokyo": "Asia/Tokyo",
    "Warsaw": "Europe/Warsaw",
    "Wellington": "Pacific/Auckland",
    "Wuhan": "Asia/Shanghai",
}


_NORMALIZED_CITY_TIMEZONE = {key.lower(): value for key, value in CITY_TIMEZONE.items()}


@dataclass(frozen=True)
class ObservationClockConfig:
    max_obs_age_min: float = 20.0
    pre_update_blackout_min: float = 6.0
    min_obs_asof: int = 6


def city_timezone_name(city_key: str | None) -> str | None:
    if not city_key:
        return None
    return CITY_TIMEZONE.get(city_key) or _NORMALIZED_CITY_TIMEZONE.get(str(city_key).lower())


def station_timezone(station: Any) -> ZoneInfo | timezone:
    timezone_name = (
        getattr(station, "timezone_name", None)
        or getattr(station, "timezone", None)
        or city_timezone_name(getattr(station, "city", None))
    )
    if timezone_name:
        try:
            return ZoneInfo(str(timezone_name))
        except Exception:
            pass
    return timezone(timedelta(hours=int(getattr(station, "utc_offset", 0))))


def timezone_label(tz: ZoneInfo | timezone) -> str:
    return getattr(tz, "key", None) or str(tz)


def asof_observations(obs: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    return sorted((row for row in obs if row.get("ts") <= now), key=lambda row: row["ts"])


def infer_observation_cadence_minutes(obs: list[dict[str, Any]]) -> float | None:
    times = sorted(row["ts"] for row in obs if isinstance(row.get("ts"), datetime))
    if len(times) < 3:
        return None
    gaps = [
        (b - a).total_seconds() / 60.0
        for a, b in zip(times, times[1:])
        if 5.0 <= (b - a).total_seconds() / 60.0 <= 90.0
    ]
    if not gaps:
        return None
    return float(median(gaps[-8:]))


def observation_clock_guard(
    obs: list[dict[str, Any]],
    now: datetime,
    *,
    station: Any,
    source: str,
    config: ObservationClockConfig,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    tz = station_timezone(station)
    asof = asof_observations(obs, now)
    if len(asof) < config.min_obs_asof:
        return (
            "insufficient_obs_asof",
            {
                "source": source,
                "n_obs": len(asof),
                "timezone": timezone_label(tz),
            },
            asof,
        )

    last = asof[-1]
    age_min = (now - last["ts"]).total_seconds() / 60.0
    cadence_min = infer_observation_cadence_minutes(asof)
    minutes_to_next = cadence_min - age_min if cadence_min is not None else None
    common = {
        "source": source,
        "n_obs": len(asof),
        "age_min": round(age_min, 1),
        "last_obs_utc": last["ts"].isoformat(),
        "timezone": timezone_label(tz),
        "cadence_min": round(cadence_min, 1) if cadence_min is not None else None,
        "minutes_to_next_obs": round(minutes_to_next, 1) if minutes_to_next is not None else None,
    }
    if age_min > config.max_obs_age_min:
        return "stale_obs", {"max_obs_age_min": config.max_obs_age_min, **common}, asof
    if minutes_to_next is not None and 0.0 <= minutes_to_next <= config.pre_update_blackout_min:
        return (
            "pre_metar_update_blackout",
            {"pre_update_blackout_min": config.pre_update_blackout_min, **common},
            asof,
        )
    return "ok", common, asof
