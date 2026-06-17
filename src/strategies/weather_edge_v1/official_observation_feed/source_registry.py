from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.strategies.weather_edge_v1.official_observation_feed.models import SourceProfile
from src.strategies.weather_edge_v1.tools.official_observation_clock import city_timezone_name


ROOT = Path(__file__).resolve().parents[4]
DEFAULT_REGISTRY_JSON = ROOT / "docs/analysis/2026-06/2026-06-14-settlement-source-registry-v0.json"


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _default_primary_source(row: dict[str, Any]) -> str:
    cls = _clean(row.get("settlement_source_class"))
    feed = _clean(row.get("official_station_or_feed"))
    if cls in {"default_wu_station_by_rules", "official_station_diff_confirmed"} and feed:
        return "aviationweather_metar"
    if cls == "special_source_confirmed" and feed == "HKO":
        return ""
    if cls == "non_wu_source_by_rules":
        return ""
    return ""


def _default_fallback_sources(row: dict[str, Any]) -> tuple[str, ...]:
    cls = _clean(row.get("settlement_source_class"))
    if cls in {"default_wu_station_by_rules", "official_station_diff_confirmed"}:
        return ("iem_asos",)
    return ()


def source_profile_from_registry_row(row: dict[str, Any]) -> SourceProfile:
    city = _clean(row.get("city"))
    cls = _clean(row.get("settlement_source_class"))
    blocked_reason = ""
    if cls in {"blocked_unresolved_settlement_basis", "no_recent_market_or_unknown_rules"}:
        blocked_reason = _clean(row.get("downstream_action")) or cls
    return SourceProfile(
        city=city,
        unit=(_clean(row.get("unit")) or "C").upper(),
        timezone_name=city_timezone_name(city) or "UTC",
        settlement_source_class=cls,
        official_station_or_feed=_clean(row.get("official_station_or_feed")),
        mapping_rule=_clean(row.get("mapping_rule")),
        primary_source=_default_primary_source(row),
        fallback_sources=_default_fallback_sources(row),
        rules_recheck_required=cls in {"default_wu_station_by_rules", "official_station_diff_confirmed"},
        blocked_reason=blocked_reason,
    )


def load_source_profiles(path: Path | str = DEFAULT_REGISTRY_JSON) -> dict[str, SourceProfile]:
    registry_path = Path(path)
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    rows = payload.get("registry") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"registry rows missing in {registry_path}")
    profiles: dict[str, SourceProfile] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        profile = source_profile_from_registry_row(row)
        if profile.city:
            profiles[profile.city] = profile
    return profiles


def source_profile_for_city(city: str, path: Path | str = DEFAULT_REGISTRY_JSON) -> SourceProfile | None:
    return load_source_profiles(path).get(city)
