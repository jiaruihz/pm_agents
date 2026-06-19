from __future__ import annotations

from typing import Any

from weather_data_feed.city_calendar import CITY_TIMEZONE
from weather_data_feed.models import CityConfig
from weather_data_feed.source_registry import load_source_profiles


MIN_ALIGNMENT_DAYS = 20
MIN_ALIGNMENT_RATE = 0.97
SUPPORTED_LIVE_SOURCES = {"aviationweather_metar", "aviationweather"}

SPECIAL_SLUGS = {
    "BuenosAires": "buenos-aires",
    "CapeTown": "cape-town",
    "HongKong": "hong-kong",
    "KualaLumpur": "kuala-lumpur",
    "LA": "los-angeles",
    "MexicoCity": "mexico-city",
    "NYC": "nyc",
    "PanamaCity": "panama-city",
    "SanFrancisco": "san-francisco",
    "SaoPaulo": "sao-paulo",
    "TelAviv": "tel-aviv",
}


def city_slug(city: str) -> str:
    if city in SPECIAL_SLUGS:
        return SPECIAL_SLUGS[city]
    out = []
    for i, ch in enumerate(city):
        if i > 0 and ch.isupper() and city[i - 1].islower():
            out.append("-")
        out.append(ch.lower())
    return "".join(out)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def source_profile_row(profile: Any, *, status: str, reason: str = "", registry_class: str = "") -> dict[str, Any]:
    return {
        "city": profile.city,
        "status": status,
        "reason": reason,
        "unit": profile.unit,
        "timezone_name": profile.timezone_name,
        "settlement_source_class": profile.settlement_source_class,
        "settlement_source": profile.official_source,
        "live_observation_source": profile.primary_source,
        "fallback_sources": list(profile.fallback_sources),
        "station_or_feed": profile.official_station_or_feed,
        "configured_icao": profile.configured_icao,
        "mapping_rule": profile.mapping_rule,
        "rules_recheck_required": profile.rules_recheck_required,
        "live_eligible": profile.live_eligible,
        "blocked_reason": profile.blocked_reason,
        "alignment_days": profile.alignment_days,
        "alignment_matches": profile.alignment_matches,
        "alignment_rate": profile.alignment_rate,
        "candidate_dates": profile.candidate_dates,
        "settled_dates": profile.settled_dates,
        "registry_class": registry_class,
        "source_profile_note": profile.source_profile_note,
    }


def build_city_policy(*, include_station_diff: bool, only_cities: set[str] | None = None) -> dict[str, Any]:
    profiles = load_source_profiles()
    configs: list[CityConfig] = []
    rejected: list[dict[str, Any]] = []
    for city, profile in sorted(profiles.items()):
        if only_cities and city not in only_cities:
            continue
        if not profile.live_eligible:
            detail = profile.blocked_reason or "source_profile_live_eligible_false"
            rejected.append(source_profile_row(profile, status="blocked", reason=f"{profile.settlement_source_class}:{detail}"))
            continue
        if profile.primary_source not in SUPPORTED_LIVE_SOURCES:
            rejected.append(source_profile_row(profile, status="blocked", reason=f"unsupported_live_observation_source:{profile.primary_source}"))
            continue
        registry_class = ""
        alignment_days = 0
        alignment_rate = 0.0
        if profile.settlement_source_class == "default_wu_station_by_rules":
            if profile.official_station_or_feed != profile.configured_icao:
                rejected.append(
                    source_profile_row(
                        profile,
                        status="blocked",
                        reason="default_wu_station_not_same_as_configured_icao",
                        registry_class="same_station_source_profile",
                    )
                )
                continue
            alignment_days = max(profile.candidate_dates, profile.settled_dates)
            alignment_rate = 1.0
            registry_class = "same_station_source_profile"
            if alignment_days < MIN_ALIGNMENT_DAYS or alignment_rate < 1.0:
                rejected.append(source_profile_row(profile, status="blocked", reason="same_station_alignment_below_gate", registry_class=registry_class))
                continue
        elif profile.settlement_source_class == "official_station_diff_confirmed":
            if not include_station_diff:
                rejected.append(source_profile_row(profile, status="second_phase", reason="station_diff_requires_rules_recheck_and_explicit_include_flag"))
                continue
            alignment_days = safe_int(profile.alignment_days)
            alignment_rate = safe_float(profile.alignment_rate)
            if alignment_days >= MIN_ALIGNMENT_DAYS and alignment_rate >= MIN_ALIGNMENT_RATE:
                registry_class = "official_station_diff_aligned"
            else:
                rejected.append(source_profile_row(profile, status="blocked", reason="official_station_diff_alignment_below_gate"))
                continue
        else:
            rejected.append(source_profile_row(profile, status="blocked", reason="source_profile_class_not_supported_for_metar_cross"))
            continue
        tz_name = profile.timezone_name or CITY_TIMEZONE.get(city)
        if not tz_name:
            rejected.append(source_profile_row(profile, status="blocked", reason="missing_city_timezone", registry_class=registry_class))
            continue
        configs.append(
            CityConfig(
                city=city,
                slug=city_slug(city),
                unit=profile.unit,
                timezone_name=tz_name,
                official_icao=profile.official_station_or_feed,
                settlement_source_class=profile.settlement_source_class,
                settlement_source=profile.official_source,
                live_observation_source=profile.primary_source,
                fallback_sources=profile.fallback_sources,
                mapping_rule=profile.mapping_rule,
                registry_class=registry_class,
                alignment_days=alignment_days,
                alignment_rate=alignment_rate,
                rules_recheck_required=profile.rules_recheck_required,
                source_profile_note=profile.source_profile_note,
            )
        )
    configs = sorted(configs, key=lambda item: item.city)
    return {
        "policy": {
            "source": "source_profiles",
            "min_alignment_days": MIN_ALIGNMENT_DAYS,
            "min_alignment_rate": MIN_ALIGNMENT_RATE,
            "same_station_requires_exact_match": True,
            "include_station_diff": include_station_diff,
            "supported_live_sources": sorted(SUPPORTED_LIVE_SOURCES),
        },
        "allowed": [cfg.__dict__ for cfg in configs],
        "rejected": sorted(rejected, key=lambda item: item["city"]),
    }


def load_city_configs(*, include_station_diff: bool, only_cities: set[str] | None = None) -> list[CityConfig]:
    policy = build_city_policy(include_station_diff=include_station_diff, only_cities=only_cities)
    return [CityConfig(**row) for row in policy["allowed"]]
