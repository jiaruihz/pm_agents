from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.strategies.weather_edge_v1.tools.official_observation_clock import (
    ObservationClockConfig,
    observation_clock_guard,
    station_timezone,
    timezone_label,
)


@dataclass(frozen=True)
class StationStub:
    city: str
    utc_offset: int
    timezone_name: str | None = None


def test_city_timezone_mapping_handles_non_helsinki_dst_city():
    station = StationStub(city="NYC", utc_offset=-5)
    local = datetime(2026, 6, 16, 17, 30, tzinfo=timezone.utc).astimezone(station_timezone(station))

    assert timezone_label(station_timezone(station)) == "America/New_York"
    assert local.hour == 13
    assert local.utcoffset() == timedelta(hours=-4)


def test_observation_clock_guard_uses_asof_rows_before_blackout():
    now = datetime(2026, 6, 16, 13, 18, tzinfo=timezone.utc)
    start = datetime(2026, 6, 16, 9, 50, tzinfo=timezone.utc)
    obs = [{"ts": start + timedelta(minutes=30 * i)} for i in range(8)]
    assert obs[-1]["ts"] == datetime(2026, 6, 16, 13, 20, tzinfo=timezone.utc)

    status, meta, asof = observation_clock_guard(
        obs,
        now,
        station=StationStub(city="Tokyo", utc_offset=9),
        source="test",
        config=ObservationClockConfig(max_obs_age_min=40, pre_update_blackout_min=6),
    )

    assert status == "pre_metar_update_blackout"
    assert asof[-1]["ts"] == datetime(2026, 6, 16, 12, 50, tzinfo=timezone.utc)
    assert meta["minutes_to_next_obs"] == 2.0
