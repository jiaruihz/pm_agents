"""Point-in-time forecast-transition timing features.

These features describe whether a forecast regime change is still ahead,
currently due, realized, or overdue.  They are strategy-neutral: the feature
layer does not decide whether rain, clearing, or a wind shift helps a specific
temperature bracket.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from weather_data_feed.forecast_sources import build_taf_signal
from weather_clock_contract import parse_utc_or_none


def _utc(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return parse_utc_or_none(value, field="forecast_transition_timestamp")


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _minutes(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds() / 60.0


def _realized_at(
    obs: Mapping[str, Any],
    event_types: set[str],
    *,
    as_of: datetime,
) -> datetime | None:
    candidates: list[datetime] = []
    if event_types & {"precipitation", "convection"}:
        value = _utc(obs.get("first_precip_obs_utc"))
        if value is not None and value <= as_of:
            candidates.append(value)
    if "convection" in event_types:
        value = _utc(obs.get("first_thunderstorm_obs_utc"))
        if value is not None and value <= as_of:
            candidates.append(value)
    return min(candidates) if candidates else None


def _timing_state(
    *,
    as_of: datetime,
    issue: datetime | None,
    start: datetime,
    end: datetime,
    realized: datetime | None,
) -> tuple[str, float | None, float]:
    if realized is not None:
        offset = _minutes(realized, start)
        if issue is not None and realized < issue:
            return "already_realized_at_issue", offset, 0.0
        if realized < start:
            return "realized_early", offset, 0.0
        if realized <= end:
            return "realized_in_window", offset, 0.0
        return "realized_late", offset, 0.0
    if as_of < start:
        return "not_due", None, 0.0
    if as_of <= end:
        elapsed = _minutes(as_of, start) / max(_minutes(end, start), 1.0)
        return "pending_in_window", None, min(1.0, max(0.0, elapsed))
    return "overdue_not_realized", None, 1.0


def forecast_transition_timing_features(
    forecast_enrichment: Mapping[str, Any] | None,
    observation: Mapping[str, Any],
    *,
    as_of_ts_utc: str,
) -> dict[str, Any]:
    """Build continuous PIT transition exposure from TAF and visible METARs."""

    empty = {
        "taf_transition_available": False,
        "taf_transition_coverage_status": "missing_forecast_enrichment",
        "transition_window_count": 0,
        "transition_event_types": "",
        "transition_change_type": "",
        "transition_window_start_utc": "",
        "transition_window_end_utc": "",
        "transition_window_minutes": None,
        "transition_probability": None,
        "minutes_to_transition_window_start": None,
        "minutes_to_transition_window_end": None,
        "transition_timing_state": "unavailable",
        "transition_timing_offset_minutes": None,
        "transition_window_elapsed_fraction": None,
        "transition_hazard_score": None,
        "transition_timing_uncertainty_score": None,
        "transition_exposure_score": None,
        "transition_temperature_impact_weight": None,
        "temperature_transition_risk_score": None,
        "regime_predictability_score": None,
        "taf_issue_time_utc": "",
        "taf_first_seen_utc": "",
        "taf_valid_from_utc": "",
        "taf_valid_to_utc": "",
        "taf_issue_age_minutes": None,
        "taf_capture_age_minutes": None,
        "taf_validity_state": "unavailable",
    }
    as_of = _utc(as_of_ts_utc)
    if as_of is None or not forecast_enrichment:
        return empty
    captured = _utc(
        forecast_enrichment.get("available_at_utc")
        or forecast_enrichment.get("snapshot_ts_utc")
        or forecast_enrichment.get("forecast_first_seen_utc")
    )
    if captured is None or captured > as_of:
        return {**empty, "taf_transition_coverage_status": "forecast_enrichment_after_asof"}
    taf = forecast_enrichment.get("taf") if isinstance(forecast_enrichment.get("taf"), Mapping) else {}
    signal = taf.get("signal") if isinstance(taf.get("signal"), Mapping) else {}
    payload = taf.get("payload") if isinstance(taf.get("payload"), Mapping) else {}
    issue = _utc(signal.get("issue_time") or payload.get("issue_time"))
    valid_from = _utc(signal.get("valid_time_from") or payload.get("valid_time_from"))
    valid_to = _utc(signal.get("valid_time_to") or payload.get("valid_time_to"))
    lineage = {
        "taf_issue_time_utc": issue.isoformat() if issue else "",
        "taf_first_seen_utc": captured.isoformat(),
        "taf_valid_from_utc": valid_from.isoformat() if valid_from else "",
        "taf_valid_to_utc": valid_to.isoformat() if valid_to else "",
        "taf_issue_age_minutes": round(_minutes(as_of, issue), 3) if issue else None,
        "taf_capture_age_minutes": round(_minutes(as_of, captured), 3),
        "taf_validity_state": (
            "issued_future_validity"
            if valid_from is not None and as_of < valid_from
            else "expired"
            if valid_to is not None and as_of >= valid_to
            else "current"
            if valid_from is not None and valid_to is not None
            else "missing_validity"
        ),
    }
    if not bool(signal.get("available")):
        return {**empty, **lineage, "taf_transition_coverage_status": "taf_unavailable"}
    if issue is None:
        return {**empty, **lineage, "taf_transition_coverage_status": "taf_missing_issue_time"}
    if issue > as_of:
        return {**empty, **lineage, "taf_transition_coverage_status": "taf_issue_after_asof"}
    if valid_from is None or valid_to is None:
        return {**empty, **lineage, "taf_transition_coverage_status": "taf_missing_validity"}
    if as_of >= valid_to:
        return {**empty, **lineage, "taf_transition_coverage_status": "taf_expired"}
    windows = signal.get("transition_windows") if isinstance(signal.get("transition_windows"), list) else []
    reparsed = False
    if not windows:
        if payload.get("raw_taf"):
            signal = build_taf_signal(
                dict(payload),
                target_date=str(forecast_enrichment.get("target_date") or ""),
                utc_offset_seconds=0,
                first_peak_hour=12,
                last_peak_hour=13,
                timezone_name=str(forecast_enrichment.get("timezone_name") or ""),
            )
            windows = signal.get("transition_windows") if isinstance(signal.get("transition_windows"), list) else []
            reparsed = bool(windows)
    if not windows:
        return {
            **empty,
            **lineage,
            "taf_transition_available": True,
            "taf_transition_coverage_status": "no_change_window",
        }

    candidates: list[dict[str, Any]] = []
    for raw in windows:
        if not isinstance(raw, Mapping):
            continue
        start = _utc(raw.get("start_utc"))
        end = _utc(raw.get("end_utc"))
        if start is None or end is None or end <= start:
            continue
        event_types = {str(item) for item in raw.get("event_types") or [] if str(item)}
        if not event_types:
            continue
        probability = _finite(raw.get("probability"))
        probability = min(1.0, max(0.0, probability if probability is not None else 1.0))
        realized = _realized_at(observation, event_types, as_of=as_of)
        state, offset, elapsed = _timing_state(
            as_of=as_of,
            issue=issue,
            start=start,
            end=end,
            realized=realized,
        )
        width = max(1.0, _minutes(end, start))
        change_type = str(raw.get("change_type") or "")
        timing_uncertainty = 0.15 + min(width, 360.0) / 360.0 * 0.45
        if change_type.startswith("PROB"):
            timing_uncertainty += 0.20
        elif change_type == "TEMPO":
            timing_uncertainty += 0.12
        if "convection" in event_types:
            timing_uncertainty += 0.18
        timing_uncertainty = min(1.0, timing_uncertainty)

        if state.startswith("realized") or state == "already_realized_at_issue":
            exposure = 0.0
        elif state == "not_due":
            lead = max(0.0, _minutes(start, as_of))
            exposure = probability * timing_uncertainty * math.exp(-lead / 180.0)
        elif state == "pending_in_window":
            exposure = probability * timing_uncertainty
        else:
            overdue = max(0.0, _minutes(as_of, end))
            exposure = probability * timing_uncertainty * math.exp(-overdue / 180.0)
        candidates.append(
            {
                "event_types": event_types,
                "change_type": change_type,
                "start": start,
                "end": end,
                "width": width,
                "probability": probability,
                "state": state,
                "offset": offset,
                "elapsed": elapsed,
                "timing_uncertainty": timing_uncertainty,
                "exposure": min(1.0, max(0.0, exposure)),
            }
        )
    if not candidates:
        return {
            **empty,
            **lineage,
            "taf_transition_available": True,
            "taf_transition_coverage_status": "invalid_change_windows",
        }

    selected = max(candidates, key=lambda row: (row["exposure"], -abs(_minutes(row["start"], as_of))))
    event_types = selected["event_types"]
    temperature_impact = max(
        ({"convection": 1.0, "precipitation": 0.9, "clearing": 0.9, "low_cloud": 0.75, "wind_regime": 0.45}.get(event, 0.5) for event in event_types),
        default=0.5,
    )
    temperature_risk = selected["exposure"] * temperature_impact
    return {
        "taf_transition_available": True,
        "taf_transition_coverage_status": "ok_reparsed_raw_taf" if reparsed else "ok",
        "transition_window_count": len(candidates),
        "transition_event_types": "|".join(sorted(event_types)),
        "transition_change_type": selected["change_type"],
        "transition_window_start_utc": selected["start"].isoformat(),
        "transition_window_end_utc": selected["end"].isoformat(),
        "transition_window_minutes": round(selected["width"], 3),
        "transition_probability": round(selected["probability"], 4),
        "minutes_to_transition_window_start": round(_minutes(selected["start"], as_of), 3),
        "minutes_to_transition_window_end": round(_minutes(selected["end"], as_of), 3),
        "transition_timing_state": selected["state"],
        "transition_timing_offset_minutes": None if selected["offset"] is None else round(selected["offset"], 3),
        "transition_window_elapsed_fraction": round(selected["elapsed"], 4),
        "transition_hazard_score": round(selected["probability"], 4),
        "transition_timing_uncertainty_score": round(selected["timing_uncertainty"], 4),
        "transition_exposure_score": round(selected["exposure"], 4),
        "transition_temperature_impact_weight": round(temperature_impact, 4),
        "temperature_transition_risk_score": round(temperature_risk, 4),
        "regime_predictability_score": round(1.0 - selected["exposure"], 4),
        **lineage,
    }
