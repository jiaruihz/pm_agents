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
    configured_icao: str = ""
    official_source: str = ""
    alignment_source: str = ""
    downstream_action: str = ""
    alignment_days: int | None = None
    alignment_matches: int | None = None
    alignment_rate: float | None = None
    candidate_rows: int = 0
    settled_candidate_rows: int = 0
    candidate_dates: int = 0
    settled_dates: int = 0
    primary_source: str = ""
    fallback_sources: tuple[str, ...] = field(default_factory=tuple)
    rules_recheck_required: bool = True
    blocked_reason: str = ""
    live_eligible: bool = True
    source_profile_note: str = ""


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
    metadata: dict[str, Any] = field(default_factory=dict)


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


@dataclass(frozen=True)
class CityConfig:
    city: str
    slug: str
    unit: str
    timezone_name: str
    official_icao: str
    settlement_source_class: str
    settlement_source: str
    live_observation_source: str
    fallback_sources: tuple[str, ...]
    mapping_rule: str
    registry_class: str
    alignment_days: int
    alignment_rate: float
    rules_recheck_required: bool
    source_profile_note: str = ""


@dataclass(frozen=True)
class MarketDateContext:
    city: str
    timezone_name: str
    now_utc: str
    city_local_date: str
    scan_dates: tuple[str, ...]


@dataclass(frozen=True)
class MarketSnapshotRecord:
    city: str
    target_date: str
    market_local_date: str
    city_local_date_at_snapshot: str
    snapshot_ts_utc: str
    market_id: str = ""
    condition_id: str = ""
    token_id: str = ""
    bracket: str = ""
    unit: str = ""
