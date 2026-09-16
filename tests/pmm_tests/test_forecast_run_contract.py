from __future__ import annotations

import pytest

from weather_data_feed.forecast_run_contract import (
    EXACT_SINGLE_RUN_ENDPOINT,
    build_forecast_row,
    materialize_full_ladder_checkpoint,
    run_lineage_evidence,
    summarize_forecast_batch,
)


BASE = {
    "model_key": "gfs_global",
    "city": "Shanghai",
    "target_date": "2026-08-07",
    "forecast_max_f": 88.0,
    "source_fetched_at_utc": "2026-08-05T00:00:01Z",
    "detected_at_utc": "2026-08-05T00:00:02Z",
    "first_seen_at_utc": "2026-08-05T00:00:02Z",
    "available_at_utc": "2026-08-05T00:00:03Z",
    "raw_payload_hash": "raw-1",
    "producer_build_identity": "fixture-build",
    "capture_id": "capture-gfs",
    "batch_capture_id": "batch-1",
    "horizon_days_local": 2,
    "forecast_run_at_utc": "2026-08-04T18:00:00Z",
    "forecast_run_evidence": "exact_single_run_request",
    "forecast_run_lineage_status": "identified",
}


def test_exact_single_run_request_is_accepted_as_run_evidence() -> None:
    evidence = run_lineage_evidence(
        request_run_at_utc="2026-08-04T18:00:00Z",
        request_endpoint=EXACT_SINGLE_RUN_ENDPOINT,
        request_succeeded=True,
        fallback_applied=False,
        raw_payload_hash="raw",
        request_hash="request",
    )
    assert evidence["forecast_run_lineage_status"] == "identified"
    assert evidence["forecast_run_at_utc"] == "2026-08-04T18:00:00.000000Z"


@pytest.mark.parametrize(
    "override,reason",
    [
        ({"request_endpoint": "https://api.open-meteo.com/v1/forecast"}, "endpoint_not_exact_single_run"),
        ({"request_succeeded": False}, "request_not_successful"),
        ({"fallback_applied": True}, "fallback_applied"),
        ({"raw_payload_hash": None}, "missing_raw_payload_hash"),
        ({"request_hash": None}, "missing_request_hash"),
    ],
)
def test_unverified_or_fallback_request_is_blocked(override: dict[str, object], reason: str) -> None:
    args = {
        "request_run_at_utc": "2026-08-04T18:00:00Z",
        "request_endpoint": EXACT_SINGLE_RUN_ENDPOINT,
        "request_succeeded": True,
        "fallback_applied": False,
        "raw_payload_hash": "raw",
        "request_hash": "request",
    }
    args.update(override)
    evidence = run_lineage_evidence(**args)
    assert evidence["forecast_run_at_utc"] is None
    assert reason in evidence["blocker"]["reasons"]


def test_clock_constraints_and_lead_run_age() -> None:
    row = build_forecast_row(**BASE)
    assert row["schema_version"] == "weather_forecast_run_row_v3"
    assert row["run_first_seen_at_utc"] == row["first_seen_at_utc"]
    assert row["content_first_seen_at_utc"] == row["first_seen_at_utc"]
    assert row["lead_hours"] == pytest.approx(47.999167, abs=1e-6)
    assert row["model_run_age_hours"] == pytest.approx(6.000833, abs=1e-6)
    with pytest.raises(ValueError, match="first_seen <= available"):
        build_forecast_row(**{**BASE, "first_seen_at_utc": "2026-08-05T00:00:04Z"})


def test_response_complete_request_clock_is_preserved_and_ordered() -> None:
    row = build_forecast_row(
        **{
            **BASE,
            "source_fetched_at_utc": "2026-08-05T00:00:01Z",
            "detected_at_utc": "2026-08-05T00:00:01Z",
            "first_seen_at_utc": "2026-08-05T00:00:01Z",
            "available_at_utc": "2026-08-05T00:00:01Z",
            "request_started_at_utc": "2026-08-05T00:00:00Z",
            "response_received_at_utc": "2026-08-05T00:00:01Z",
        }
    )
    assert row["request_started_at_utc"] == "2026-08-05T00:00:00.000000Z"
    assert row["response_received_at_utc"] == row["available_at_utc"]
    with pytest.raises(ValueError, match="request <= response"):
        build_forecast_row(
            **{
                **BASE,
                "request_started_at_utc": "2026-08-05T00:00:02Z",
                "response_received_at_utc": "2026-08-05T00:00:01Z",
            }
        )


def test_normalized_content_hash_ignores_volatile_raw_payload_hash() -> None:
    first = build_forecast_row(
        **BASE,
        normalized_content_hash="stable-normalized-content",
    )
    second = build_forecast_row(
        **{
            **BASE,
            "raw_payload_hash": "volatile-raw-2",
            "capture_id": "capture-gfs-2",
        },
        normalized_content_hash="stable-normalized-content",
        previous_content_hash=first["content_hash"],
        previous_content_forecast_max_f=88.0,
        revision_of_content_id=first["capture_id"],
    )
    assert second["content_hash"] == first["content_hash"]
    assert second["previous_content_hash"] is None


def test_provider_run_and_same_run_content_revision_never_mix() -> None:
    run_change = build_forecast_row(
        **BASE,
        previous_run_ts="2026-08-04T12:00:00Z",
        previous_run_forecast_max_f=86.5,
        previous_content_hash="older-content",
        previous_content_forecast_max_f=87.0,
        revision_of_content_id="old-capture",
    )
    assert run_change["run_to_run_delta_f"] == 1.5
    assert run_change["previous_content_hash"] is None
    same_run = build_forecast_row(
        **BASE,
        previous_run_ts="2026-08-04T18:00:00Z",
        previous_run_forecast_max_f=87.0,
        previous_content_hash="older-content",
        previous_content_forecast_max_f=87.25,
        revision_of_content_id="old-capture",
    )
    assert same_run["run_to_run_delta_f"] is None
    assert same_run["content_revision_delta_f"] == 0.75
    assert same_run["revision_of_content_id"] == "old-capture"


def test_batch_summary_is_order_invariant_and_lists_missing_models() -> None:
    first = build_forecast_row(**{**BASE, "assigned_model": True})
    second = build_forecast_row(
        **{
            **BASE,
            "model_key": "ecmwf_ifs025",
            "forecast_max_f": 86.0,
            "capture_id": "capture-ecmwf",
            "raw_payload_hash": "raw-2",
        }
    )
    expected = ["gfs_global", "ecmwf_ifs025", "icon_seamless"]
    left = summarize_forecast_batch([first, second], expected_model_keys=expected)
    right = summarize_forecast_batch([second, first], expected_model_keys=reversed(expected))
    assert left["batch_content_hash"] == right["batch_content_hash"]
    assert left["missing_model_keys"] == ["icon_seamless"]
    assert left["model_count"] == 2
    assert left["assigned_minus_consensus_f"] == 1.0


def _ladder_rows() -> list[dict[str, object]]:
    return [
        {"bracket": "28", "question": "28 or below", "yes_best_bid": 0.09, "yes_best_ask": 0.11},
        {"bracket": "29", "yes_best_bid": 0.19, "yes_best_ask": 0.21},
        {"bracket": "30", "yes_best_bid": 0.39, "yes_best_ask": 0.41},
        {"bracket": "31+", "question": "31 or higher", "yes_best_bid": 0.29, "yes_best_ask": 0.31},
    ]


def test_native_lattice_and_market_distribution_contract() -> None:
    result = materialize_full_ladder_checkpoint(
        _ladder_rows(),
        city="Shanghai",
        target_date="2026-08-06",
        event_id="event-1",
        checkpoint_ts_utc="2026-08-05T08:00:00Z",
        feature_book_snapshot_id="book-1",
        horizon_days=1,
    )
    assert result["evidence_status"] == "complete"
    assert result["rung_completeness"] is True
    assert result["market_distribution_complete"] is True
    assert sum(row["normalized_market_probability"] for row in result["rung_manifest"]) == pytest.approx(1.0)


def test_missing_rung_and_d2_market_are_explicit_blockers() -> None:
    rows = _ladder_rows()
    rows.pop(2)
    result = materialize_full_ladder_checkpoint(
        rows,
        city="Shanghai",
        target_date="2026-08-07",
        event_id="event-2",
        checkpoint_ts_utc="2026-08-05T08:00:00Z",
        feature_book_snapshot_id=None,
        horizon_days=2,
    )
    codes = {row["code"] for row in result["evidence_blockers"]}
    assert "native_lattice_gap" in codes
    assert "d2_market_ladder_unavailable" in codes
    assert result["evidence_status"] == "blocked"
