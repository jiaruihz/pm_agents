from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceProfile:
    city: str
    unit: str
    timezone_name: str
    settlement_source_class: str
    official_station_or_feed: str
    mapping_rule: str
    primary_source: str = ""
    fallback_sources: tuple[str, ...] = field(default_factory=tuple)
    rules_recheck_required: bool = True
    blocked_reason: str = ""

    @property
    def live_eligible(self) -> bool:
        blocked = {
            "blocked_unresolved_settlement_basis",
            "no_recent_market_or_unknown_rules",
        }
        if self.settlement_source_class in blocked:
            return False
        if not self.primary_source:
            return False
        return True


@dataclass(frozen=True)
class ObservationRecord:
    source_key: str
    city: str
    target_date: str
    station_or_feed: str
    obs_ts_utc: str
    ingest_ts_utc: str
    temp_c: float
    dewpoint_c: float | None = None
    relh: float | None = None
    wind_kt: float | None = None
    sky_code: str = ""
    raw_text: str = ""
    source_latency_ms: float | None = None
    fetch_status: str = "ok"
    quality_flags: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RunningMaxState:
    city: str
    target_date: str
    source_key: str
    running_max_c: float
    current_temp_c: float
    decline_c: float
    running_market_value: int
    current_market_value: int
    last_obs_utc: str
    obs_age_min: float
    cadence_min: float | None
    minutes_to_next_obs: float | None
    source_profile_class: str
    source_verified_at_utc: str = ""


@dataclass(frozen=True)
class CrossingEvent:
    city: str
    target_date: str
    source_key: str
    previous_running_value: int | None
    new_running_value: int
    crossed_brackets: tuple[int, ...]
    event_key: str
    emitted_at_utc: str
