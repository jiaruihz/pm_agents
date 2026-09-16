from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Any

from weather_data_feed.city_calendar import station_timezone, timezone_label


@dataclass(frozen=True)
class ObservationClockConfig:
    max_obs_age_min: float = 20.0
    pre_update_blackout_min: float = 6.0
    min_obs_asof: int = 6


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
