from __future__ import annotations

from weather_feature_layer.transitions import forecast_transition_timing_features


def enrichment() -> dict:
    return {
        "snapshot_ts_utc": "2026-07-21T05:10:00Z",
        "taf": {
            "signal": {
                "available": True,
                "issue_time": "2026-07-21T05:00:00Z",
                "valid_time_from": "2026-07-21T05:00:00Z",
                "valid_time_to": "2026-07-22T05:00:00Z",
                "transition_windows": [
                    {
                        "change_type": "PROB30 TEMPO",
                        "event_types": ["precipitation", "convection"],
                        "probability": 0.3,
                        "start_utc": "2026-07-21T08:00:00Z",
                        "end_utc": "2026-07-21T11:00:00Z",
                    }
                ],
            }
        },
    }


def test_transition_is_future_uncertainty_before_window() -> None:
    features = forecast_transition_timing_features(
        enrichment(),
        {},
        as_of_ts_utc="2026-07-21T06:00:00Z",
    )

    assert features["transition_timing_state"] == "not_due"
    assert features["minutes_to_transition_window_start"] == 120
    assert 0 < features["transition_exposure_score"] < features["transition_probability"]
    assert features["regime_predictability_score"] == 1 - features["transition_exposure_score"]
    assert features["taf_issue_time_utc"] == "2026-07-21T05:00:00+00:00"
    assert features["taf_validity_state"] == "current"
    assert features["taf_issue_age_minutes"] == 60
    assert features["taf_capture_age_minutes"] == 50


def test_transition_becomes_overdue_without_observed_onset() -> None:
    features = forecast_transition_timing_features(
        enrichment(),
        {},
        as_of_ts_utc="2026-07-21T11:30:00Z",
    )

    assert features["transition_timing_state"] == "overdue_not_realized"
    assert features["minutes_to_transition_window_end"] == -30
    assert features["transition_exposure_score"] > 0


def test_transition_records_early_realization_only_after_it_is_visible() -> None:
    features = forecast_transition_timing_features(
        enrichment(),
        {"first_precip_obs_utc": "2026-07-21T07:30:00Z"},
        as_of_ts_utc="2026-07-21T07:40:00Z",
    )

    assert features["transition_timing_state"] == "realized_early"
    assert features["transition_timing_offset_minutes"] == -30
    assert features["transition_exposure_score"] == 0


def test_future_enrichment_is_not_leaked_into_pit_features() -> None:
    features = forecast_transition_timing_features(
        enrichment(),
        {},
        as_of_ts_utc="2026-07-21T05:00:00Z",
    )

    assert features["taf_transition_available"] is False
    assert features["taf_transition_coverage_status"] == "forecast_enrichment_after_asof"


def test_available_clock_takes_precedence_over_earlier_snapshot_clock() -> None:
    row = enrichment()
    row["available_at_utc"] = "2026-07-21T05:20:00Z"

    features = forecast_transition_timing_features(
        row,
        {},
        as_of_ts_utc="2026-07-21T05:15:00Z",
    )

    assert features["taf_transition_available"] is False
    assert features["taf_transition_coverage_status"] == "forecast_enrichment_after_asof"


def test_future_observation_onset_is_not_used_as_realized_transition() -> None:
    features = forecast_transition_timing_features(
        enrichment(),
        {"first_precip_obs_utc": "2026-07-21T08:30:00Z"},
        as_of_ts_utc="2026-07-21T07:40:00Z",
    )

    assert features["transition_timing_state"] == "not_due"
    assert features["transition_timing_offset_minutes"] is None


def test_expired_taf_keeps_lineage_but_cannot_emit_transition_risk() -> None:
    row = enrichment()
    row["taf"]["signal"]["valid_time_to"] = "2026-07-21T05:30:00Z"

    features = forecast_transition_timing_features(
        row,
        {},
        as_of_ts_utc="2026-07-21T06:00:00Z",
    )

    assert features["taf_transition_available"] is False
    assert features["taf_transition_coverage_status"] == "taf_expired"
    assert features["taf_validity_state"] == "expired"
    assert features["taf_issue_time_utc"] == "2026-07-21T05:00:00+00:00"
    assert features["taf_valid_to_utc"] == "2026-07-21T05:30:00+00:00"
    assert features["temperature_transition_risk_score"] is None


def test_issued_future_taf_is_labeled_and_can_describe_future_transition() -> None:
    row = enrichment()
    row["taf"]["signal"]["valid_time_from"] = "2026-07-21T07:00:00Z"

    features = forecast_transition_timing_features(
        row,
        {},
        as_of_ts_utc="2026-07-21T06:00:00Z",
    )

    assert features["taf_transition_available"] is True
    assert features["taf_transition_coverage_status"] == "ok"
    assert features["taf_validity_state"] == "issued_future_validity"
