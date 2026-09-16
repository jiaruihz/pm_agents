from __future__ import annotations

from copy import deepcopy

from src.strategies.weather_edge_v1.tools.tmax_feature_contract_v2 import (
    COLLECT_FORWARD,
    FEATURE_CONTRACT_VERSION,
    REQUIRED_NOW,
    RESEARCH_ONLY,
    feature_envelope,
    validate_feature_payload,
)


DECISION_TS = "2026-07-11T12:00:00Z"
SOURCE_TS = "2026-07-11T10:00:00Z"


def _valid_payload() -> dict[str, object]:
    features = {
        field: feature_envelope(
            "value",
            source_system="fixture_source",
            source_report_ts_utc=SOURCE_TS,
            first_seen_at_utc=SOURCE_TS,
            available_at_utc=SOURCE_TS,
            feature_version="fixture_v1",
        )
        for field in REQUIRED_NOW
    }
    features["decision_ts_utc"]["value"] = DECISION_TS
    features["quote_depth"]["value"] = 0
    return {"contract_version": FEATURE_CONTRACT_VERSION, "features": features}


def test_contract_separates_required_forward_and_research_only_fields() -> None:
    assert "current_temperature" in REQUIRED_NOW
    assert "forecast_run_history" in COLLECT_FORWARD
    assert "observed_final_maximum" in RESEARCH_ONLY
    assert not (set(REQUIRED_NOW) & set(COLLECT_FORWARD))
    assert not (set(REQUIRED_NOW) & set(RESEARCH_ONLY))


def test_valid_required_now_payload_is_pit_and_preserves_zero() -> None:
    result = validate_feature_payload(_valid_payload())

    assert result["valid"] is True
    assert result["model_ready"] is True
    assert result["violations"] == []


def test_future_first_seen_is_a_structured_pit_violation() -> None:
    payload = _valid_payload()
    payload["features"]["current_temperature"]["first_seen_at_utc"] = "2026-07-11T12:01:00Z"
    payload["features"]["current_temperature"]["available_at_utc"] = "2026-07-11T12:01:00Z"

    result = validate_feature_payload(payload)

    assert result["valid"] is False
    assert {item["code"] for item in result["violations"]} >= {"first_seen_after_decision", "timestamp_after_decision"}


def test_missing_provenance_and_missing_reason_are_not_imputed() -> None:
    payload = _valid_payload()
    del payload["features"]["wind_speed"]["source_system"]
    payload["features"]["quote_depth"] = feature_envelope(
        None,
        source_system="fixture_source",
        source_report_ts_utc=SOURCE_TS,
        first_seen_at_utc=SOURCE_TS,
        available_at_utc=SOURCE_TS,
        missing_reason=None,
        feature_version="fixture_v1",
    )

    result = validate_feature_payload(payload)

    codes = {item["code"] for item in result["violations"]}
    assert {"missing_provenance_key", "missing_provenance_value", "missing_reason_required"} <= codes


def test_missing_optional_weather_value_stays_unknown_without_invalidating_pit() -> None:
    payload = _valid_payload()
    payload["features"]["wind_direction"] = feature_envelope(
        None,
        source_system="fixture_source",
        source_report_ts_utc=SOURCE_TS,
        first_seen_at_utc=SOURCE_TS,
        available_at_utc=SOURCE_TS,
        missing_reason="source_did_not_publish",
        feature_version="fixture_v1",
    )

    result = validate_feature_payload(payload)

    assert result["valid"] is True
    assert result["model_ready"] is True
    assert result["missing_features"] == ["wind_direction"]


def test_missing_optional_source_does_not_require_fabricated_timestamps() -> None:
    payload = _valid_payload()
    payload["features"]["sky_or_cloud_cover"] = feature_envelope(
        None,
        source_system="aviationweather_attempt",
        source_report_ts_utc=None,
        first_seen_at_utc=None,
        available_at_utc=None,
        missing_reason="source_did_not_publish",
        feature_version="fixture_v1",
    )

    result = validate_feature_payload(payload)

    assert result["valid"] is True
    assert result["model_ready"] is True
    assert result["missing_features"] == ["sky_or_cloud_cover"]


def test_missing_critical_value_is_valid_lineage_but_not_model_ready() -> None:
    payload = _valid_payload()
    payload["features"]["current_temperature"] = feature_envelope(
        None,
        source_system="fixture_source",
        source_report_ts_utc=SOURCE_TS,
        first_seen_at_utc=SOURCE_TS,
        available_at_utc=SOURCE_TS,
        missing_reason="no_observation_yet",
        feature_version="fixture_v1",
    )

    result = validate_feature_payload(payload)

    assert result["valid"] is True
    assert result["model_ready"] is False
    assert result["missing_critical_features"] == ["current_temperature"]


def test_unknown_forecast_run_time_is_diagnostic_not_model_blocking() -> None:
    payload = _valid_payload()
    payload["features"]["forecast_run_at_utc"] = feature_envelope(
        None,
        source_system="open_meteo_live_ecmwf",
        source_report_ts_utc=None,
        first_seen_at_utc=None,
        available_at_utc=None,
        missing_reason="source_response_does_not_expose_run_timestamp",
    )

    result = validate_feature_payload(payload)

    assert result["valid"] is True
    assert result["model_ready"] is True
    assert result["missing_critical_features"] == []
    assert result["missing_features"] == ["forecast_run_at_utc"]


def test_research_only_field_requires_explicit_opt_in() -> None:
    payload = deepcopy(_valid_payload())
    payload["features"]["observed_final_maximum"] = feature_envelope(
        31,
        source_system="settlement",
        source_report_ts_utc=SOURCE_TS,
        first_seen_at_utc=SOURCE_TS,
        available_at_utc=SOURCE_TS,
        feature_version="fixture_v1",
    )

    assert validate_feature_payload(payload)["valid"] is False
    assert validate_feature_payload(payload, allow_research_only=True)["valid"] is True
