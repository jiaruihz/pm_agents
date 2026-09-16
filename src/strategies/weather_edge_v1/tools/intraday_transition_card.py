"""Versioned weather-only packets for the intraday transition-card challenger."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Literal

from pydantic import BaseModel, Field


CARD_SCHEMA_VERSION = "intraday_state_transition_card_v1"
PROMPT_VERSION = "core_carry_llm_transition_card_v1"


class IntradayTransitionCard(BaseModel):
    thermal_stage: Literal[
        "fresh_runway", "active_warming", "plateau", "pullback", "fade", "unclear"
    ]
    next_state: Literal["upward_exit", "current_high_holds", "fade", "unclear"]
    transition_window: Literal["0_1h", "1_3h", "3h_plus", "none", "unclear"]
    advection_state: Literal[
        "warm_advection", "cold_advection", "mixing_maintenance", "neutral", "unclear"
    ]
    wind_role: Literal[
        "warming_transport", "cooling_transport", "mixing_only", "variable", "unclear"
    ]
    cloud_precip_transition: Literal[
        "clearing", "clouding", "rain_onset", "rain_persistence", "dry_stable", "unclear"
    ]
    moisture_transition: Literal[
        "dewpoint_rising", "dewpoint_falling", "stable", "unclear"
    ]
    reheat_risk: Literal["low", "moderate", "high", "unclear"]
    second_heat_lobe: Literal["none", "possible", "likely", "unclear"]
    source_conflict: bool
    evidence_quality: Literal["good", "partial", "poor"]
    supporting_facts: list[str] = Field(default_factory=list, max_length=8)
    conflicting_facts: list[str] = Field(default_factory=list, max_length=8)
    missing_evidence: list[str] = Field(default_factory=list, max_length=8)
    invalidation_signals: list[str] = Field(default_factory=list, max_length=8)
    summary: str = Field(max_length=500)


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _compact_hourly_curve(row: dict[str, Any]) -> list[dict[str, Any]]:
    decision_hour = _finite(row.get("decision_hour_local"))
    curve = row.get("hourly_curve")
    if decision_hour is None or not isinstance(curve, list):
        return []
    start = int(decision_hour)
    selected: list[dict[str, Any]] = []
    for item in curve:
        if not isinstance(item, dict):
            continue
        text = str(item.get("time_local") or "")
        try:
            hour = int(text[11:13])
        except (TypeError, ValueError):
            continue
        if start <= hour <= min(23, start + 8):
            selected.append(
                {
                    "time_local": text,
                    "temperature_f": _finite(item.get("temperature_f")),
                    "cloud_cover_pct": _finite(item.get("cloud_cover_pct")),
                    "precipitation_probability_pct": _finite(
                        item.get("precipitation_probability_pct")
                    ),
                    "wind_speed_10m_kt": _finite(item.get("wind_speed_10m_kt")),
                    "wind_direction_10m_deg": _finite(
                        item.get("wind_direction_10m_deg")
                    ),
                }
            )
    return selected


def _compact_metar_sequence(row: dict[str, Any]) -> list[dict[str, Any]]:
    sequence = row.get("metar_sequence")
    if not isinstance(sequence, list):
        return []
    output: list[dict[str, Any]] = []
    for item in sequence[-16:]:
        if not isinstance(item, dict) or not item.get("raw_metar"):
            continue
        output.append(
            {
                "report_ts_utc": item.get("last_obs_utc"),
                "first_seen_utc": item.get("fetched_at_utc"),
                "raw_metar": item.get("raw_metar"),
                "temperature_c": _finite(item.get("current_temp_c")),
                "dewpoint_f": _finite(item.get("dwpf_now")),
                "wind_speed_kt": _finite(item.get("wind_speed_kt")),
                "wind_direction_deg": _finite(item.get("wind_dir_deg")),
                "sky_cover_code": item.get("sky_cover_code"),
                "precip_state": item.get("precip_state"),
                "running_max_c": _finite(item.get("running_max_c")),
                "temp_change_1h_f": _finite(item.get("d_tmpf_1h")),
                "temp_change_3h_f": _finite(item.get("d_tmpf_3h")),
                "dewpoint_change_3h_f": _finite(item.get("d_dwpf_3h")),
            }
        )
    return output


def build_transition_packet(row: dict[str, Any]) -> dict[str, Any]:
    """Build a PIT weather packet that intentionally excludes market/model/label."""
    missing: list[str] = []
    for field in (
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "dewpoint_depression_f",
        "wind_speed_kt",
        "wind_dir_deg",
        "forecast_peak_delta_hours_local",
    ):
        if _finite(row.get(field)) is None:
            missing.append(field)
    if _finite(row.get("pressure_trend_3h_hpa")) is None:
        missing.append("pressure_trend_or_upstream_station_network")
    if not row.get("taf_transition_signal"):
        missing.append("taf_transition_signal")

    packet = {
        "schema_version": CARD_SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "identity": {
            "city": row.get("city"),
            "target_date": row.get("target_date"),
            "decision_ts_utc": row.get("decision_snapshot_ts_utc"),
            "decision_hour_local": _finite(row.get("decision_hour_local")),
            "timezone": row.get("timezone"),
            "unit": row.get("unit"),
            "current_bracket": row.get("current_bracket"),
            "feature_frame_ref": row.get("feature_frame_ref"),
        },
        "site_baseline": {
            "city_family": row.get("city_family"),
            "geo_context": row.get("geo_context"),
            "coastal_flow_state": row.get("coastal_flow_state"),
            "marine_thermal_state": row.get("marine_thermal_state"),
        },
        "observed_state": {
            "raw_metar": row.get("raw_metar"),
            "observation_source": row.get("obs_source"),
            "observation_age_min": _finite(row.get("obs_age_min")),
            "source_report_ts_utc": row.get("source_report_ts_utc"),
            "current_temp_native": _finite(row.get("current_temp_native")),
            "running_max_native": _finite(row.get("running_max_native")),
            "minutes_since_last_strict_new_high": _finite(
                row.get("minutes_since_last_strict_new_high")
            ),
            "temp_trend_1h_f": _finite(row.get("temp_trend_1h_f")),
            "temp_trend_3h_f": _finite(row.get("temp_trend_3h_f")),
            "dewpoint_depression_f": _finite(row.get("dewpoint_depression_f")),
            "relative_humidity_pct": _finite(row.get("relative_humidity_pct")),
            "wind_speed_kt": _finite(row.get("wind_speed_kt")),
            "wind_direction_deg": _finite(row.get("wind_dir_deg")),
            "wind_speed_change_1h_kt": _finite(row.get("wind_speed_change_1h_kt")),
            "sky_state": row.get("sky_state"),
            "lowest_cloud_base_ft_agl": _finite(row.get("lowest_cloud_base_ft_agl")),
            "precip_observed": bool(row.get("precip_observed")),
            "present_weather_codes": row.get("present_weather_codes"),
            "intraday_state": row.get("intraday_state"),
            "warming_state": row.get("warming_state"),
            "running_max_state": row.get("running_max_state"),
            "metar_sequence": _compact_metar_sequence(row),
        },
        "forecast_state": {
            "source": row.get("forecast_source"),
            "forecast_max_native": _finite(row.get("forecast_max_native")),
            "gap_to_running_native": _finite(row.get("forecast_gap_to_running_native")),
            "peak_delta_hours_local": _finite(row.get("forecast_peak_delta_hours_local")),
            "future_max_delta_hours_local": _finite(
                row.get("forecast_future_max_delta_hours_local")
            ),
            "future_peak_relation": row.get("forecast_future_peak_relation"),
            "cloud_cover_remaining_3h_mean_pct": _finite(
                row.get("forecast_cloud_cover_remaining_3h_mean_pct")
            ),
            "precip_probability_remaining_3h_max_pct": _finite(
                row.get("forecast_precip_probability_remaining_3h_max_pct")
            ),
            "wind_speed_remaining_3h_max_kt": _finite(
                row.get("forecast_wind_speed_remaining_3h_max_kt")
            ),
            "wind_direction_remaining_3h_mean_deg": _finite(
                row.get("forecast_wind_direction_remaining_3h_mean_deg")
            ),
            "solar_elevation_deg": _finite(row.get("solar_elevation_deg")),
            "solar_elevation_delta_2h_deg": _finite(
                row.get("solar_elevation_delta_2h_deg")
            ),
            "daylight_remaining_minutes": _finite(row.get("daylight_remaining_minutes")),
            "next_8h_curve": _compact_hourly_curve(row),
        },
        "known_missing_evidence": sorted(set(missing)),
    }
    canonical = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    packet["input_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
    return packet


def transition_card_prompt(packet: dict[str, Any]) -> str:
    return (
        f"PROMPT_VERSION: {PROMPT_VERSION}\n"
        "You are a meteorological state-transition analyst. Read only the PIT weather packet below. "
        "Classify the physical process affecting whether the current exact daily-high bracket survives. "
        "Separate boundary-layer mixing that merely prevents cooling from genuine warm/cold advection. "
        "Do not infer advection from compass direction alone: hemisphere, terrain, upstream temperature, "
        "pressure gradient, and source gaps matter. Rain/cloud can cap heating or mark a transition, but "
        "their effect is conditional on timing. If evidence is absent, use unclear and list it. "
        "Do not output a win probability, price, EV, trade decision, or order instruction. "
        "Return strict JSON matching the supplied schema.\n\n"
        f"PIT_WEATHER_PACKET:\n{json.dumps(packet, ensure_ascii=False, sort_keys=True)}"
    )
