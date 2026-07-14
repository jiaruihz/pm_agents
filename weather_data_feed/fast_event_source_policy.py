from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_FAST_EVENT_SOURCE_PROFILES_JSON = Path(__file__).resolve().with_name("fast_event_source_profiles.json")


@dataclass(frozen=True)
class FastEventSourceProfile:
    city: str
    source: str
    source_station_or_feed: str
    source_icao: str
    timezone_name: str
    market_unit: str
    bracket_rounding: str
    settlement_source_class: str
    official_station_or_feed: str
    source_basis_class: str
    collector_enabled: bool
    live_eligible: bool
    calibration_status: str
    blocked_reason: str
    note: str = ""


def _profile_from_row(row: dict[str, Any]) -> FastEventSourceProfile:
    return FastEventSourceProfile(
        city=str(row.get("city") or ""),
        source=str(row.get("source") or ""),
        source_station_or_feed=str(row.get("source_station_or_feed") or ""),
        source_icao=str(row.get("source_icao") or ""),
        timezone_name=str(row.get("timezone_name") or "UTC"),
        market_unit=str(row.get("market_unit") or "C").upper(),
        bracket_rounding=str(row.get("bracket_rounding") or "arithmetic_round"),
        settlement_source_class=str(row.get("settlement_source_class") or ""),
        official_station_or_feed=str(row.get("official_station_or_feed") or ""),
        source_basis_class=str(row.get("source_basis_class") or "unclassified"),
        collector_enabled=bool(row.get("collector_enabled", False)),
        live_eligible=bool(row.get("live_eligible", False)),
        calibration_status=str(row.get("calibration_status") or "unclassified"),
        blocked_reason=str(row.get("blocked_reason") or ""),
        note=str(row.get("note") or ""),
    )


def load_fast_event_source_profiles(
    path: Path | str = DEFAULT_FAST_EVENT_SOURCE_PROFILES_JSON,
) -> dict[tuple[str, str], FastEventSourceProfile]:
    profile_path = Path(path)
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    rows = payload.get("profiles")
    if not isinstance(rows, list):
        raise ValueError(f"fast event source profile rows missing in {profile_path}")
    out: dict[tuple[str, str], FastEventSourceProfile] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        profile = _profile_from_row(row)
        if profile.city and profile.source:
            out[(profile.city, profile.source)] = profile
    return out


def fast_event_source_profile_for(
    city: str,
    source: str,
    profiles: dict[tuple[str, str], FastEventSourceProfile] | None = None,
) -> FastEventSourceProfile | None:
    registry = profiles if profiles is not None else load_fast_event_source_profiles()
    return registry.get((city, source))


def market_value_from_temp_c(temp_c: float, profile: FastEventSourceProfile) -> int:
    value = float(temp_c) * 9.0 / 5.0 + 32.0 if profile.market_unit == "F" else float(temp_c)
    if profile.bracket_rounding == "floor":
        return int(value // 1)
    if profile.bracket_rounding != "arithmetic_round":
        raise ValueError(f"unsupported bracket_rounding={profile.bracket_rounding!r}")
    return int((value + 0.5) // 1)
