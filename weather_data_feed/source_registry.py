from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from weather_data_feed.city_calendar import city_timezone_name
from weather_data_feed.models import SourceProfile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_PROFILES_JSON = Path(__file__).resolve().with_name("source_profiles.json")
DEFAULT_RESEARCH_REGISTRY_JSON = ROOT / "docs/analysis/2026-06/2026-06-14-settlement-source-registry-v0.json"
SYNOPTIC_VERIFIED_PRIMARY_CITIES = {"Austin", "Dallas", "Houston"}


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        if str(value).lower() == "nan":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if str(value).lower() == "nan":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _default_primary_source(row: dict[str, Any]) -> str:
    cls = _clean(row.get("settlement_source_class"))
    feed = _clean(row.get("official_station_or_feed"))
    city = _clean(row.get("city"))
    if city in SYNOPTIC_VERIFIED_PRIMARY_CITIES and cls == "default_wu_station_by_rules" and feed:
        return "synopticdata_timeseries"
    if cls in {"default_wu_station_by_rules", "default_source_watchlist", "official_station_diff_confirmed"} and feed:
        return "aviationweather_metar"
    if cls == "special_source_confirmed" and feed == "HKO":
        return ""
    if cls == "non_wu_source_by_rules":
        source = f"{feed} {_clean(row.get('official_source'))}".lower()
        return "synopticdata_timeseries" if "weather.gov/wrh" in source else ""
    return ""


def _default_fallback_sources(row: dict[str, Any]) -> tuple[str, ...]:
    cls = _clean(row.get("settlement_source_class"))
    city = _clean(row.get("city"))
    if city in SYNOPTIC_VERIFIED_PRIMARY_CITIES and cls == "default_wu_station_by_rules":
        return ("aviationweather_metar", "iem_asos_madishf_latest", "iem_asos")
    if cls in {"default_wu_station_by_rules", "default_source_watchlist", "official_station_diff_confirmed"}:
        return ("iem_asos",)
    if cls == "non_wu_source_by_rules" and "weather.gov/wrh" in (
        f"{_clean(row.get('official_station_or_feed'))} {_clean(row.get('official_source'))}".lower()
    ):
        return ("aviationweather_metar", "noaa_tgftp_station_txt")
    return ()


def _default_live_eligible(row: dict[str, Any], *, primary_source: str, blocked_reason: str) -> bool:
    cls = _clean(row.get("settlement_source_class"))
    blocked = {
        "blocked_unresolved_settlement_basis",
        "default_source_watchlist",
        "no_recent_market_or_unknown_rules",
        "non_wu_source_by_rules",
    }
    return bool(primary_source) and not blocked_reason and cls not in blocked


def source_profile_from_registry_row(row: dict[str, Any]) -> SourceProfile:
    city = _clean(row.get("city"))
    cls = _clean(row.get("settlement_source_class"))
    blocked_reason = ""
    if cls in {"blocked_unresolved_settlement_basis", "default_source_watchlist", "no_recent_market_or_unknown_rules"}:
        blocked_reason = _clean(row.get("downstream_action")) or cls
    primary_source = _default_primary_source(row)
    fallback_sources = _default_fallback_sources(row)
    return SourceProfile(
        city=city,
        unit=(_clean(row.get("unit")) or "C").upper(),
        timezone_name=city_timezone_name(city) or "UTC",
        settlement_source_class=cls,
        official_station_or_feed=_clean(row.get("official_station_or_feed")),
        mapping_rule=_clean(row.get("mapping_rule")),
        configured_icao=_clean(row.get("configured_icao")),
        official_source=_clean(row.get("official_source")),
        alignment_source=_clean(row.get("alignment_source")),
        downstream_action=_clean(row.get("downstream_action")),
        alignment_days=_int_or_none(row.get("alignment_days")),
        alignment_matches=_int_or_none(row.get("alignment_matches")),
        alignment_rate=_float_or_none(row.get("alignment_rate")),
        candidate_rows=_int_or_none(row.get("candidate_rows")) or 0,
        settled_candidate_rows=_int_or_none(row.get("settled_candidate_rows")) or 0,
        candidate_dates=_int_or_none(row.get("candidate_dates")) or 0,
        settled_dates=_int_or_none(row.get("settled_dates")) or 0,
        primary_source=primary_source,
        fallback_sources=fallback_sources,
        rules_recheck_required=cls in {"default_wu_station_by_rules", "official_station_diff_confirmed"},
        blocked_reason=blocked_reason,
        live_eligible=_default_live_eligible(row, primary_source=primary_source, blocked_reason=blocked_reason),
        source_profile_note=_clean(row.get("source_profile_note")),
    )


def _source_profile_from_profile_row(row: dict[str, Any]) -> SourceProfile:
    return SourceProfile(
        city=_clean(row.get("city")),
        unit=(_clean(row.get("unit")) or "C").upper(),
        timezone_name=_clean(row.get("timezone_name")) or "UTC",
        settlement_source_class=_clean(row.get("settlement_source_class")),
        official_station_or_feed=_clean(row.get("official_station_or_feed")),
        mapping_rule=_clean(row.get("mapping_rule")),
        configured_icao=_clean(row.get("configured_icao")),
        official_source=_clean(row.get("official_source")),
        alignment_source=_clean(row.get("alignment_source")),
        downstream_action=_clean(row.get("downstream_action")),
        alignment_days=_int_or_none(row.get("alignment_days")),
        alignment_matches=_int_or_none(row.get("alignment_matches")),
        alignment_rate=_float_or_none(row.get("alignment_rate")),
        candidate_rows=_int_or_none(row.get("candidate_rows")) or 0,
        settled_candidate_rows=_int_or_none(row.get("settled_candidate_rows")) or 0,
        candidate_dates=_int_or_none(row.get("candidate_dates")) or 0,
        settled_dates=_int_or_none(row.get("settled_dates")) or 0,
        primary_source=_clean(row.get("primary_source")),
        fallback_sources=tuple(row.get("fallback_sources") or ()),
        rules_recheck_required=bool(row.get("rules_recheck_required", True)),
        blocked_reason=_clean(row.get("blocked_reason")),
        live_eligible=bool(row.get("live_eligible", False)),
        source_profile_note=_clean(row.get("source_profile_note")),
    )


def load_source_profiles(path: Path | str = DEFAULT_SOURCE_PROFILES_JSON) -> dict[str, SourceProfile]:
    registry_path = Path(path)
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"source profile payload must be an object in {registry_path}")
    rows = payload.get("source_profiles")
    parser = _source_profile_from_profile_row
    if rows is None:
        rows = payload.get("registry")
        parser = source_profile_from_registry_row
    if not isinstance(rows, list):
        raise ValueError(f"source profile rows missing in {registry_path}")
    profiles: dict[str, SourceProfile] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        profile = parser(row)
        if profile.city:
            profiles[profile.city] = profile
    return profiles


def source_profile_for_city(city: str, path: Path | str = DEFAULT_SOURCE_PROFILES_JSON) -> SourceProfile | None:
    return load_source_profiles(path).get(city)
