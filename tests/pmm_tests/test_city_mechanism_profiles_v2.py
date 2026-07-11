from __future__ import annotations

from weather_data_feed.city_mechanism_profiles import (
    AXES,
    FORBIDDEN_PERMISSION_FIELDS,
    PROFILE_SCHEMA_VERSION,
    city_mechanism_profile,
    load_city_mechanism_profiles,
    validate_city_mechanism_profile,
)


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_all_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value)) if value else set()
    return set()


def test_checked_in_profile_schema_declares_all_multi_axis_dimensions() -> None:
    payload = load_city_mechanism_profiles()

    assert payload["schema_version"] == PROFILE_SCHEMA_VERSION
    assert set(payload["axis_schema"]) == set(AXES)
    assert all(schema["provenance_required"] for schema in payload["axis_schema"].values())


def test_known_city_profile_has_provenance_without_fabricated_coordinates() -> None:
    profile = city_mechanism_profile("SanFrancisco")

    assert profile["profile_status"] == "known_partial"
    assert set(AXES) <= set(profile)
    assert profile["geography"]["latitude"] is None
    assert profile["geography"]["longitude"] is None
    assert profile["terrain"]["elevation_m"] is None
    assert profile["coastal"]["coast_normal_bearing_deg"] is None
    assert all(profile[axis]["provenance"]["review_required"] for axis in ("geography", "terrain", "coastal"))
    assert validate_city_mechanism_profile(profile) == []


def test_unknown_city_returns_explicit_unknown_profile_without_city_fallback() -> None:
    profile = city_mechanism_profile("Neverland")

    assert profile["city"] == "Neverland"
    assert profile["profile_status"] == "unknown_city"
    assert profile["geography"]["legacy_context"] == "unknown"
    assert profile["source_topology"]["settlement_source_class"] == "unknown"
    assert validate_city_mechanism_profile(profile) == []


def test_profiles_contain_no_trading_permission_fields() -> None:
    profile = city_mechanism_profile("Amsterdam")

    assert not (_all_keys(profile) & FORBIDDEN_PERMISSION_FIELDS)
