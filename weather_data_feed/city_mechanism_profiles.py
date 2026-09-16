"""Descriptive Tmax V2 city mechanism profiles.

Profiles are research metadata for hierarchical grouping and attribution.  They
are intentionally separate from execution configuration and contain no
permission or allowlist semantics.
"""

from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from weather_data_feed.city_family import CITY_FAMILY_ATLAS_V1, CITY_FAMILY_CURRENT_BRACKET_NO_V1
from weather_data_feed.source_registry import load_source_profiles


PROFILE_SCHEMA_VERSION = "city_mechanism_profiles_v2"
DEFAULT_CITY_MECHANISM_PROFILES_JSON = Path(__file__).with_name("city_mechanism_profiles_v2.json")
AXES = ("geography", "terrain", "coastal", "source_topology", "forecast_topology")
FORBIDDEN_PERMISSION_FIELDS = frozenset(
    {
        "live_eligible",
        "trading_permission",
        "trade_allowed",
        "allowed_sides",
        "city_pool",
    }
)


def _provenance(source: str, *, review_required: bool, note: str) -> dict[str, Any]:
    return {"source": source, "review_required": review_required, "note": note}


@lru_cache(maxsize=8)
def load_city_mechanism_profiles(path: str | Path = DEFAULT_CITY_MECHANISM_PROFILES_JSON) -> dict[str, Any]:
    """Load the checked-in static profile schema and legacy context hints."""

    profile_path = Path(path)
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"city mechanism profile payload must be an object: {profile_path}")
    if payload.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise ValueError(f"unsupported city mechanism profile schema in {profile_path}")
    if not isinstance(payload.get("axis_schema"), dict):
        raise ValueError(f"missing axis_schema in {profile_path}")
    if not isinstance(payload.get("city_overrides"), dict):
        raise ValueError(f"missing city_overrides in {profile_path}")
    return payload


def _legacy_context(city: str, raw: dict[str, Any]) -> str:
    context = raw.get("legacy_context")
    return str(context).strip() if context else "unknown"


def _is_coastal_context(context: str) -> bool:
    return context.startswith("coastal_")


def _source_topology(city: str, source_profiles_path: str | Path | None) -> tuple[dict[str, Any], bool]:
    profiles = load_source_profiles(source_profiles_path) if source_profiles_path else load_source_profiles()
    source_profile = profiles.get(city)
    if source_profile is None:
        return (
            {
                "settlement_source_class": "unknown",
                "primary_observation_source": "unknown",
                "fallback_observation_sources": [],
                "provenance": _provenance(
                    "weather_data_feed/source_profiles.json",
                    review_required=True,
                    note="city has no source profile",
                ),
            },
            False,
        )
    return (
        {
            "settlement_source_class": source_profile.settlement_source_class or "unknown",
            "primary_observation_source": source_profile.primary_source or "unknown",
            "fallback_observation_sources": list(source_profile.fallback_sources),
            "provenance": _provenance(
                "weather_data_feed/source_profiles.json",
                review_required=bool(source_profile.rules_recheck_required),
                note="settlement and observation topology copied without execution eligibility fields",
            ),
        },
        True,
    )


def unknown_city_mechanism_profile(city: str) -> dict[str, Any]:
    """Return an explicit unknown profile, never another city's profile."""

    city_name = str(city).strip()
    provenance = _provenance("none", review_required=True, note="city is not present in the V2 profile or source registry")
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "city": city_name,
        "profile_status": "unknown_city",
        "geography": {"legacy_context": "unknown", "latitude": None, "longitude": None, "hemisphere": "unknown", "provenance": provenance},
        "terrain": {"classification": "unknown", "elevation_m": None, "legacy_hint": "unknown", "provenance": deepcopy(provenance)},
        "coastal": {
            "classification": "unknown",
            "coast_normal_bearing_deg": None,
            "orientation_status": "not_recorded",
            "provenance": deepcopy(provenance),
        },
        "source_topology": {
            "settlement_source_class": "unknown",
            "primary_observation_source": "unknown",
            "fallback_observation_sources": [],
            "provenance": deepcopy(provenance),
        },
        "forecast_topology": {
            "assigned_model": "unknown",
            "regional_model_availability": "unknown",
            "provenance": deepcopy(provenance),
        },
        "legacy_classifications": {
            "current_bracket_no_v1": "unknown",
            "atlas_v1": "unknown",
            "provenance": deepcopy(provenance),
        },
    }


def city_mechanism_profile(
    city: str,
    *,
    profiles_path: str | Path = DEFAULT_CITY_MECHANISM_PROFILES_JSON,
    source_profiles_path: str | Path | None = None,
) -> dict[str, Any]:
    """Resolve one city into independent mechanism axes.

    Coordinate, elevation, and coastal-bearing values stay ``None`` until a
    separately sourced geography registry is introduced.  The legacy labels are
    retained only as review-required diagnostic hints.
    """

    city_name = str(city).strip()
    if not city_name:
        return unknown_city_mechanism_profile(city_name)
    payload = load_city_mechanism_profiles(profiles_path)
    overrides = payload["city_overrides"]
    raw = overrides.get(city_name)
    source_topology, has_source_profile = _source_topology(city_name, source_profiles_path)
    if not isinstance(raw, dict) and not has_source_profile:
        return unknown_city_mechanism_profile(city_name)

    raw = raw if isinstance(raw, dict) else {}
    context = _legacy_context(city_name, raw)
    legacy_provenance = _provenance(
        "weather_data_feed/weather_context.py:CITY_WIND_CONTEXT",
        review_required=True,
        note="legacy descriptive context; not a surveyed geography record",
    )
    family_provenance = _provenance(
        "weather_data_feed/city_family.py",
        review_required=True,
        note="legacy research family retained for diagnostic migration only",
    )
    terrain_hint = context if context in {"basin_inland", "high_plain", "inland_plateau", "inland_alpine_edge"} else "unknown"
    coastal_context = _is_coastal_context(context)
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "city": city_name,
        "profile_status": "known_partial",
        "geography": {
            "legacy_context": context,
            "latitude": None,
            "longitude": None,
            "hemisphere": "unknown",
            "provenance": legacy_provenance,
        },
        "terrain": {
            "classification": "unknown",
            "elevation_m": None,
            "legacy_hint": terrain_hint,
            "provenance": deepcopy(legacy_provenance),
        },
        "coastal": {
            "classification": "legacy_coastal_context" if coastal_context else "not_classified_from_legacy_context",
            "coast_normal_bearing_deg": None,
            "orientation_status": "not_recorded",
            "provenance": deepcopy(legacy_provenance),
        },
        "source_topology": source_topology,
        "forecast_topology": {
            "assigned_model": "unknown",
            "regional_model_availability": "unknown",
            "provenance": _provenance(
                "not_materialized_in_shared_data_layer",
                review_required=True,
                note="do not infer an assigned model from a historical result or city label",
            ),
        },
        "legacy_classifications": {
            "current_bracket_no_v1": CITY_FAMILY_CURRENT_BRACKET_NO_V1.get(city_name, "unknown"),
            "atlas_v1": CITY_FAMILY_ATLAS_V1.get(city_name, "unknown"),
            "provenance": family_provenance,
        },
    }


def get_city_mechanism_profile(city: str, **kwargs: Any) -> dict[str, Any]:
    """Alias kept for callers that prefer a getter-style API."""

    return city_mechanism_profile(city, **kwargs)


def validate_city_mechanism_profile(profile: dict[str, Any]) -> list[dict[str, str]]:
    """Return schema violations without modifying the supplied profile."""

    violations: list[dict[str, str]] = []
    if profile.get("schema_version") != PROFILE_SCHEMA_VERSION:
        violations.append({"code": "schema_version_mismatch", "detail": "unexpected schema_version"})
    for axis in AXES:
        value = profile.get(axis)
        if not isinstance(value, dict):
            violations.append({"code": "axis_not_mapping", "axis": axis, "detail": "axis must be a mapping"})
        elif not isinstance(value.get("provenance"), dict):
            violations.append({"code": "missing_provenance", "axis": axis, "detail": "axis provenance is required"})
    forbidden = sorted(FORBIDDEN_PERMISSION_FIELDS.intersection(profile))
    for field in forbidden:
        violations.append({"code": "forbidden_permission_field", "field": field, "detail": "mechanism profiles are descriptive only"})
    return violations
