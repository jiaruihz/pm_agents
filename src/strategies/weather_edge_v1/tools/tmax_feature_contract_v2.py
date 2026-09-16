"""Point-in-time feature contract for the Tmax V2 research state.

The contract deliberately models provenance next to every field value.  It is
not a state builder and it does not decide whether an expression may trade.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


FEATURE_CONTRACT_VERSION = "tmax_feature_contract_v2"
PROVENANCE_FIELDS = (
    "value",
    "source_system",
    "source_report_ts_utc",
    "first_seen_at_utc",
    "available_at_utc",
    "missing_reason",
    "feature_version",
)

REQUIRED_NOW = (
    "city",
    "target_date",
    "decision_ts_utc",
    "station_or_feed",
    "settlement_source",
    "unit",
    "current_temperature",
    "running_max_temperature",
    "running_max_observed_at_utc",
    "observation_age_minutes",
    "observation_cadence_minutes",
    "temperature_trend_1h",
    "temperature_trend_3h",
    "temperature_decline",
    "assigned_forecast_model",
    "forecast_run_at_utc",
    "forecast_age_minutes",
    "forecast_hourly_temperature_curve",
    "forecast_peak_clock_local",
    "dewpoint_temperature",
    "relative_humidity_pct",
    "wind_speed",
    "wind_direction",
    "sky_or_cloud_cover",
    "settlement_ladder",
    "ladder_quote_book",
    "quote_timestamp_utc",
    "quote_spread",
    "quote_depth",
    "base_probability_version",
    "calibrator_version",
    "feature_artifact_version",
)

COLLECT_FORWARD = (
    "high_frequency_observation_history",
    "forecast_run_history",
    "quote_event_history",
    "station_source_basis_estimate",
    "pressure",
    "wind_gust",
    "precipitation_forecast",
    "cloud_forecast_change",
    "forecast_observation_mismatch",
    "first_running_max_observed_at_utc",
    "plateau_duration_minutes",
    "pullback_depth",
    "repeated_high_count",
    "full_ladder_executable_depth",
)

RESEARCH_ONLY = (
    "full_window_best_model",
    "full_window_city_bias",
    "reconstructed_forecast_without_asof_timestamp",
    "archive_filled_future_wind_cloud_source",
    "raw_city_one_hot_performance_effect",
    "observed_final_maximum",
    "settlement_outcome_label",
    "actual_overshoot_slice",
    "actual_tail_slice",
)

FEATURE_TIERS = {
    **{field: "required_now" for field in REQUIRED_NOW},
    **{field: "collect_forward" for field in COLLECT_FORWARD},
    **{field: "research_only" for field in RESEARCH_ONLY},
}

# Missing feature values are not, by themselves, PIT violations.  Live data
# may legitimately lack wind, sky, humidity, or a secondary source; those
# values must remain explicit unknowns instead of being archive-filled.  These
# two sets separate row identity from model-head readiness without turning the
# contract into an execution gate.
IDENTITY_VALUE_FIELDS = (
    "city",
    "target_date",
    "decision_ts_utc",
    "settlement_source",
    "unit",
)

MODEL_CRITICAL_VALUE_FIELDS = (
    *IDENTITY_VALUE_FIELDS,
    "station_or_feed",
    "current_temperature",
    "running_max_temperature",
    "running_max_observed_at_utc",
    "observation_age_minutes",
    "assigned_forecast_model",
    "forecast_hourly_temperature_curve",
    "settlement_ladder",
    "ladder_quote_book",
    "quote_timestamp_utc",
    "base_probability_version",
    "feature_artifact_version",
)

FEATURE_CONTRACT_V2 = {
    "contract_version": FEATURE_CONTRACT_VERSION,
    "field_envelope": {
        "required_keys": list(PROVENANCE_FIELDS),
        "semantics": {
            "source_system": "system that produced the value",
            "source_report_ts_utc": "UTC timestamp reported by the source",
            "first_seen_at_utc": "first local detection of this exact value",
            "available_at_utc": "earliest UTC time this value was usable",
            "missing_reason": "required when value is null or empty",
            "feature_version": "producer or transform version",
        },
    },
    "tiers": {
        "required_now": list(REQUIRED_NOW),
        "collect_forward": list(COLLECT_FORWARD),
        "research_only": list(RESEARCH_ONLY),
    },
}


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _violation(code: str, *, field: str | None = None, detail: str = "", tier: str | None = None) -> dict[str, str]:
    out = {"code": code, "detail": detail}
    if field is not None:
        out["field"] = field
    if tier is not None:
        out["tier"] = tier
    return out


def feature_envelope(
    value: Any,
    *,
    source_system: str,
    source_report_ts_utc: str | None,
    first_seen_at_utc: str | None,
    available_at_utc: str | None,
    missing_reason: str | None = None,
    feature_version: str = FEATURE_CONTRACT_VERSION,
) -> dict[str, Any]:
    """Create an explicit field envelope; callers still own real timestamps."""

    return {
        "value": value,
        "source_system": source_system,
        "source_report_ts_utc": source_report_ts_utc,
        "first_seen_at_utc": first_seen_at_utc,
        "available_at_utc": available_at_utc,
        "missing_reason": missing_reason,
        "feature_version": feature_version,
    }


def validate_feature_payload(
    payload: Mapping[str, Any],
    *,
    require_collect_forward: bool = False,
    allow_research_only: bool = False,
) -> dict[str, Any]:
    """Validate one payload without I/O or implicit imputation.

    ``valid`` means the row has a complete, point-in-time lineage.  It does not
    mean every weather source produced a value and it is not permission to
    trade.  ``model_ready`` separately reports whether the primary physical
    head has its critical values.  Numeric zero is a normal value; only
    ``None`` and blank strings are missing.
    """

    violations: list[dict[str, str]] = []
    if not isinstance(payload, Mapping):
        return {
            "contract_version": FEATURE_CONTRACT_VERSION,
            "valid": False,
            "violations": [_violation("payload_not_mapping", detail="payload must be a mapping")],
        }

    version = payload.get("contract_version")
    if version != FEATURE_CONTRACT_VERSION:
        violations.append(
            _violation(
                "contract_version_mismatch",
                detail=f"expected {FEATURE_CONTRACT_VERSION!r}, got {version!r}",
            )
        )

    features = payload.get("features")
    if not isinstance(features, Mapping):
        return {
            "contract_version": FEATURE_CONTRACT_VERSION,
            "valid": False,
            "violations": [
                *violations,
                _violation("features_not_mapping", detail="payload.features must be a mapping"),
            ],
        }

    required = list(REQUIRED_NOW)
    if require_collect_forward:
        required.extend(COLLECT_FORWARD)
    for field in required:
        if field not in features:
            violations.append(
                _violation(
                    "missing_required_feature",
                    field=field,
                    tier=FEATURE_TIERS[field],
                    detail="field is absent; no default was applied",
                )
            )

    decision_envelope = features.get("decision_ts_utc")
    decision_ts = None
    if isinstance(decision_envelope, Mapping):
        decision_ts = _parse_utc(decision_envelope.get("value"))
    if decision_ts is None:
        violations.append(
            _violation(
                "invalid_decision_timestamp",
                field="decision_ts_utc",
                tier="required_now",
                detail="decision_ts_utc.value must be an offset-aware ISO-8601 UTC timestamp",
            )
        )

    missing_features: list[str] = []
    missing_critical_features: list[str] = []
    for field, envelope in features.items():
        field_name = str(field)
        tier = FEATURE_TIERS.get(field_name, "unknown")
        if tier == "research_only" and not allow_research_only:
            violations.append(
                _violation(
                    "research_only_feature_not_allowed",
                    field=field_name,
                    tier=tier,
                    detail="field has no proven PIT contract",
                )
            )
        if tier == "unknown":
            violations.append(
                _violation(
                    "unknown_feature_field",
                    field=field_name,
                    tier=tier,
                    detail="field is not declared by the V2 contract",
                )
            )
            continue
        if not isinstance(envelope, Mapping):
            violations.append(
                _violation("feature_envelope_not_mapping", field=field_name, tier=tier, detail="field payload must be a mapping"))
            continue

        for key in PROVENANCE_FIELDS:
            if key not in envelope:
                violations.append(
                    _violation("missing_provenance_key", field=field_name, tier=tier, detail=f"missing {key}"))

        value = envelope.get("value")
        missing_reason = envelope.get("missing_reason")
        if _is_missing(value):
            missing_features.append(field_name)
            if field_name in MODEL_CRITICAL_VALUE_FIELDS:
                missing_critical_features.append(field_name)
            if _is_missing(missing_reason):
                violations.append(
                    _violation("missing_reason_required", field=field_name, tier=tier, detail="missing values need an explicit reason"))
            if field_name in IDENTITY_VALUE_FIELDS:
                violations.append(
                    _violation("identity_value_missing", field=field_name, tier=tier, detail="row identity values cannot be missing"))
        elif not _is_missing(missing_reason):
            violations.append(
                _violation("missing_reason_with_value", field=field_name, tier=tier, detail="clear missing_reason when value is present"))

        for key in ("source_system", "feature_version"):
            if _is_missing(envelope.get(key)):
                violations.append(
                    _violation("missing_provenance_value", field=field_name, tier=tier, detail=f"{key} is required"))

        timestamps: dict[str, datetime] = {}
        for key in ("source_report_ts_utc", "first_seen_at_utc", "available_at_utc"):
            raw_timestamp = envelope.get(key)
            parsed = _parse_utc(raw_timestamp)
            # An unavailable source cannot truthfully provide report or
            # first-seen timestamps.  Explicit nulls plus missing_reason are
            # valid lineage; populated features still require all timestamps.
            if parsed is None and (not _is_missing(value) or not _is_missing(raw_timestamp)):
                violations.append(
                    _violation("invalid_provenance_timestamp", field=field_name, tier=tier, detail=f"{key} must be offset-aware ISO-8601"))
            elif parsed is not None:
                timestamps[key] = parsed

        report_ts = timestamps.get("source_report_ts_utc")
        first_seen_ts = timestamps.get("first_seen_at_utc")
        available_ts = timestamps.get("available_at_utc")
        if report_ts and available_ts and available_ts < report_ts:
            violations.append(
                _violation("available_before_source_report", field=field_name, tier=tier, detail="available_at precedes source_report"))
        if first_seen_ts and available_ts and available_ts < first_seen_ts:
            violations.append(
                _violation("available_before_first_seen", field=field_name, tier=tier, detail="available_at precedes first_seen"))
        if decision_ts:
            for key, timestamp in timestamps.items():
                if timestamp > decision_ts:
                    code = "first_seen_after_decision" if key == "first_seen_at_utc" else "timestamp_after_decision"
                    violations.append(
                        _violation(code, field=field_name, tier=tier, detail=f"{key} is after decision_ts_utc"))

    valid = not violations
    return {
        "contract_version": FEATURE_CONTRACT_VERSION,
        "valid": valid,
        "model_ready": valid and not missing_critical_features,
        "missing_features": sorted(missing_features),
        "missing_critical_features": sorted(missing_critical_features),
        "violations": violations,
    }
