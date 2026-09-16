"""Reusable routing contract for low-latency observation producer lanes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")


@dataclass(frozen=True)
class ObservationFastLane:
    name: str
    source: str
    cities: tuple[str, ...]
    interval_sec: float


@dataclass(frozen=True)
class ObservationFastLanePaths:
    output_dir: Path
    state_path: Path
    latest_path: Path
    notify_path: Path


def parse_observation_fast_lane(value: str) -> ObservationFastLane:
    name, name_sep, route = value.strip().partition("=")
    source_cities, interval_sep, interval_raw = route.rpartition("@")
    source, source_sep, cities_raw = source_cities.partition(":")
    if not name_sep or not interval_sep or not source_sep:
        raise ValueError(
            f"invalid fast lane {value!r}; expected name=source:City,City@seconds"
        )
    if not _NAME_RE.fullmatch(name):
        raise ValueError(f"invalid fast lane name {name!r}")
    if not _SOURCE_RE.fullmatch(source):
        raise ValueError(f"invalid fast lane source {source!r}")
    cities = tuple(dict.fromkeys(
        city.strip() for city in cities_raw.split(",") if city.strip()
    ))
    if not cities:
        raise ValueError(f"fast lane has no cities: {value!r}")
    interval_sec = float(interval_raw)
    if interval_sec <= 0:
        raise ValueError(f"fast lane interval must be positive: {value!r}")
    return ObservationFastLane(name, source, cities, interval_sec)


def observation_fast_lane_paths(
    output_dir: Path, notify_path: Path, lane_name: str
) -> ObservationFastLanePaths:
    lane_output = output_dir / "fast_lanes" / lane_name
    notify_suffix = notify_path.suffix or ".json"
    lane_notify = notify_path.with_name(
        f"{notify_path.stem}.{lane_name}{notify_suffix}"
    )
    return ObservationFastLanePaths(
        output_dir=lane_output,
        state_path=lane_output / "state.json",
        latest_path=lane_output / "latest.json",
        notify_path=lane_notify,
    )
