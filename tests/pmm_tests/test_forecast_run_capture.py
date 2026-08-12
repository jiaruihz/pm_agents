from __future__ import annotations

from datetime import datetime, timezone

from weather_data_feed_service.forecast_run_capture import (
    build_d1_market_capture_demands,
    latest_cycle_candidate,
    materialize_capture,
    upgrade_first_seen_state_from_rows,
)


def _response() -> dict[str, object]:
    return {
        "hourly": {
            "time": [
                "2026-08-06T12:00",
                "2026-08-06T13:00",
                "2026-08-07T12:00",
                "2026-08-07T13:00",
            ],
            "temperature_2m": [86.0, 88.0, 87.0, 89.0],
        }
    }


def _response_clock_metadata(*, raw_hash: str, request_key: str) -> dict[str, object]:
    return {
        "raw_hash": raw_hash,
        "request_key": request_key,
        "source_fetch_start_utc": "2026-08-05T02:59:59Z",
        "source_fetch_end_utc": "2026-08-05T03:00:00Z",
        "source_fetch_clock_status": "response_complete",
    }


def test_latest_cycle_candidate_is_request_only() -> None:
    assert latest_cycle_candidate(datetime(2026, 8, 5, 5, 59, tzinfo=timezone.utc)) == "2026-08-05T00:00"
    assert latest_cycle_candidate(datetime(2026, 8, 4, 17, 59, tzinfo=timezone.utc)) == "2026-08-04T12:00"


def test_capture_materializes_d1_d2_and_missing_models() -> None:
    rows, batches, state = materialize_capture(
        run="2026-08-04T12:00",
        captured_at_utc=datetime(2026, 8, 5, 3, tzinfo=timezone.utc),
        city_inputs=[{"city": "Tokyo", "timezone_name": "Asia/Tokyo"}],
        responses_by_model={"gfs_global": [_response()]},
        metadata_by_model={"gfs_global": {"raw_hash": "raw", "request_key": "request"}},
        expected_models=["gfs_global", "ecmwf_ifs025"],
    )
    assert [row["horizon_days_local"] for row in rows] == [1, 2]
    assert all(row["forecast_run_lineage_status"] == "identified" for row in rows)
    assert all(row["run_first_observation"] is True for row in rows)
    assert all(batch["missing_model_keys"] == ["ecmwf_ifs025"] for batch in batches)
    assert state["latest_by_model_city_target"]
    assert state["run_history_by_model_city_target"]


def test_run_transition_and_same_run_revision_are_separate() -> None:
    base_args = {
        "captured_at_utc": datetime(2026, 8, 5, 3, tzinfo=timezone.utc),
        "city_inputs": [{"city": "Tokyo", "timezone_name": "Asia/Tokyo"}],
        "responses_by_model": {"gfs_global": [_response()]},
        "metadata_by_model": {"gfs_global": {"raw_hash": "raw-1", "request_key": "request-1"}},
        "expected_models": ["gfs_global"],
    }
    first, _, state = materialize_capture(run="2026-08-04T12:00", **base_args)
    changed_response = _response()
    changed_response["hourly"]["temperature_2m"][1] = 88.5
    revised, _, state = materialize_capture(
        run="2026-08-04T12:00",
        **{**base_args, "captured_at_utc": datetime(2026, 8, 5, 3, 10, tzinfo=timezone.utc), "responses_by_model": {"gfs_global": [changed_response]}, "metadata_by_model": {"gfs_global": {"raw_hash": "raw-2", "request_key": "request-1"}}, "previous_state": state},
    )
    d1_revision = next(row for row in revised if row["horizon_days_local"] == 1)
    assert d1_revision["content_revision_delta_f"] == 0.5
    assert d1_revision["run_to_run_delta_f"] is None
    transitioned, _, _ = materialize_capture(
        run="2026-08-04T18:00",
        **{**base_args, "captured_at_utc": datetime(2026, 8, 5, 3, 20, tzinfo=timezone.utc), "metadata_by_model": {"gfs_global": {"raw_hash": "raw-3", "request_key": "request-2"}}, "previous_state": state},
    )
    d1_transition = next(row for row in transitioned if row["horizon_days_local"] == 1)
    assert d1_transition["run_to_run_delta_f"] == -0.5
    assert d1_transition["previous_content_hash"] is None


def test_same_run_poll_preserves_run_and_content_first_seen() -> None:
    base_args = {
        "city_inputs": [{"city": "Tokyo", "timezone_name": "Asia/Tokyo"}],
        "responses_by_model": {"gfs_global": [_response()]},
        "expected_models": ["gfs_global"],
    }
    first, _, state = materialize_capture(
        run="2026-08-04T12:00",
        captured_at_utc=datetime(2026, 8, 5, 3, tzinfo=timezone.utc),
        metadata_by_model={
            "gfs_global": {"raw_hash": "volatile-raw-1", "request_key": "request"}
        },
        **base_args,
    )
    repeated, _, _ = materialize_capture(
        run="2026-08-04T12:00",
        captured_at_utc=datetime(2026, 8, 5, 3, 30, tzinfo=timezone.utc),
        metadata_by_model={
            "gfs_global": {"raw_hash": "volatile-raw-2", "request_key": "request"}
        },
        previous_state=state,
        **base_args,
    )
    first_d1 = next(row for row in first if row["horizon_days_local"] == 1)
    repeated_d1 = next(row for row in repeated if row["horizon_days_local"] == 1)
    assert repeated_d1["run_first_seen_at_utc"] == first_d1["run_first_seen_at_utc"]
    assert repeated_d1["content_first_seen_at_utc"] == first_d1["content_first_seen_at_utc"]
    assert repeated_d1["content_hash"] == first_d1["content_hash"]
    assert repeated_d1["previous_content_hash"] is None


def test_same_run_real_content_change_keeps_run_first_seen_separate() -> None:
    base_args = {
        "city_inputs": [{"city": "Tokyo", "timezone_name": "Asia/Tokyo"}],
        "expected_models": ["gfs_global"],
    }
    first, _, state = materialize_capture(
        run="2026-08-04T12:00",
        captured_at_utc=datetime(2026, 8, 5, 3, tzinfo=timezone.utc),
        responses_by_model={"gfs_global": [_response()]},
        metadata_by_model={
            "gfs_global": {"raw_hash": "raw-1", "request_key": "request"}
        },
        **base_args,
    )
    changed = _response()
    changed["hourly"]["temperature_2m"][1] = 88.5
    revised, _, _ = materialize_capture(
        run="2026-08-04T12:00",
        captured_at_utc=datetime(2026, 8, 5, 3, 30, tzinfo=timezone.utc),
        responses_by_model={"gfs_global": [changed]},
        metadata_by_model={
            "gfs_global": {"raw_hash": "raw-2", "request_key": "request"}
        },
        previous_state=state,
        **base_args,
    )
    first_d1 = next(row for row in first if row["horizon_days_local"] == 1)
    revised_d1 = next(row for row in revised if row["horizon_days_local"] == 1)
    assert revised_d1["run_first_seen_at_utc"] == first_d1["run_first_seen_at_utc"]
    assert revised_d1["content_first_seen_at_utc"] != first_d1["content_first_seen_at_utc"]
    assert revised_d1["content_revision_delta_f"] == 0.5


def test_legacy_state_upgrade_recovers_earliest_observed_run_clock() -> None:
    state = upgrade_first_seen_state_from_rows(
        {},
        [
            {
                "model_key": "gfs_global",
                "city": "Tokyo",
                "target_date": "2026-08-06",
                "forecast_run_at_utc": "2026-08-04T12:00:00Z",
                "first_seen_at_utc": "2026-08-05T03:30:00Z",
            },
            {
                "model_key": "gfs_global",
                "city": "Tokyo",
                "target_date": "2026-08-06",
                "forecast_run_at_utc": "2026-08-04T12:00:00Z",
                "first_seen_at_utc": "2026-08-05T03:00:00Z",
            },
        ],
    )
    key = "gfs_global|Tokyo|2026-08-06|2026-08-04T12:00:00Z"
    assert state["first_seen_by_run"][key] == "2026-08-05T03:00:00Z"
    assert state["first_seen_status_by_run"][key] == "legacy_earliest_observed"


def test_polling_an_older_run_never_creates_a_backward_transition() -> None:
    base_args = {
        "city_inputs": [{"city": "Tokyo", "timezone_name": "Asia/Tokyo"}],
        "responses_by_model": {"gfs_global": [_response()]},
        "metadata_by_model": {
            "gfs_global": {"raw_hash": "raw-1", "request_key": "request-1"}
        },
        "expected_models": ["gfs_global"],
    }
    _, _, state = materialize_capture(
        run="2026-08-04T12:00",
        captured_at_utc=datetime(2026, 8, 5, 3, tzinfo=timezone.utc),
        **base_args,
    )
    _, _, state = materialize_capture(
        run="2026-08-04T18:00",
        captured_at_utc=datetime(2026, 8, 5, 3, 10, tzinfo=timezone.utc),
        previous_state=state,
        **base_args,
    )
    repeated_old, _, state = materialize_capture(
        run="2026-08-04T12:00",
        captured_at_utc=datetime(2026, 8, 5, 3, 20, tzinfo=timezone.utc),
        previous_state=state,
        **base_args,
    )
    d1 = next(row for row in repeated_old if row["horizon_days_local"] == 1)
    assert d1["previous_run_ts"] is None
    assert d1["run_to_run_delta_f"] is None
    assert d1["previous_content_hash"] is None
    latest = state["latest_by_model_city_target"]["gfs_global|Tokyo|2026-08-06"]
    assert latest["forecast_run_at_utc"] == "2026-08-04T18:00:00.000000Z"


def test_assigned_model_is_materialized_into_batch_summary() -> None:
    rows, batches, _ = materialize_capture(
        run="2026-08-04T12:00",
        captured_at_utc=datetime(2026, 8, 5, 3, tzinfo=timezone.utc),
        city_inputs=[{"city": "Tokyo", "timezone_name": "Asia/Tokyo"}],
        responses_by_model={"gfs_global": [_response()]},
        metadata_by_model={"gfs_global": _response_clock_metadata(raw_hash="raw", request_key="request")},
        expected_models=["gfs_global"],
    )
    assert all(row["assigned_model"] is True for row in rows)
    assert all(batch["assigned_model_value_f"] is not None for batch in batches)
    assert all(batch["assigned_minus_consensus_f"] == 0 for batch in batches)


def test_new_d1_run_emits_bounded_market_capture_demand_once() -> None:
    first, _, state = materialize_capture(
        run="2026-08-04T12:00",
        captured_at_utc=datetime(2026, 8, 5, 3, tzinfo=timezone.utc),
        city_inputs=[{"city": "Tokyo", "timezone_name": "Asia/Tokyo"}],
        responses_by_model={"gfs_global": [_response()]},
        metadata_by_model={
            "gfs_global": {"raw_hash": "raw", "request_key": "request"}
        },
        expected_models=["gfs_global"],
    )
    assert build_d1_market_capture_demands(first, ttl_minutes=120) == []
    changed = _response()
    changed["hourly"]["temperature_2m"][1] = 89.0
    transitioned, _, state = materialize_capture(
        run="2026-08-04T18:00",
        captured_at_utc=datetime(2026, 8, 5, 3, 20, tzinfo=timezone.utc),
        city_inputs=[{"city": "Tokyo", "timezone_name": "Asia/Tokyo"}],
        responses_by_model={"gfs_global": [changed]},
        metadata_by_model={"gfs_global": _response_clock_metadata(raw_hash="raw-2", request_key="request-2")},
        expected_models=["gfs_global"],
        previous_state=state,
    )
    demands = build_d1_market_capture_demands(transitioned, ttl_minutes=120)
    assert len(demands) == 1
    assert demands[0]["city"] == "Tokyo"
    assert demands[0]["target_date"] == "2026-08-06"
    assert demands[0]["reason"] == "d1_provider_run_first_seen"
    assert demands[0]["ladder_scope"] == "revision_path_plus_one_neighbor_yes_no"
    assert demands[0]["native_unit"] == "C"
    assert demands[0]["consensus_after_native"] > demands[0]["consensus_before_native"]
    assert demands[0]["expires_at_utc"] == "2026-08-05T05:00:00Z"

    repeated, _, _ = materialize_capture(
        run="2026-08-04T18:00",
        captured_at_utc=datetime(2026, 8, 5, 3, 30, tzinfo=timezone.utc),
        city_inputs=[{"city": "Tokyo", "timezone_name": "Asia/Tokyo"}],
        responses_by_model={"gfs_global": [changed]},
        metadata_by_model={"gfs_global": _response_clock_metadata(raw_hash="raw-2", request_key="request")},
        expected_models=["gfs_global"],
        previous_state=state,
    )
    assert build_d1_market_capture_demands(repeated) == []
    assert all(row["run_first_observation"] is False for row in repeated)


def test_market_capture_demand_respects_explicit_city_and_run_age_bounds() -> None:
    _, _, state = materialize_capture(
        run="2026-08-04T12:00",
        captured_at_utc=datetime(2026, 8, 5, 3, tzinfo=timezone.utc),
        city_inputs=[{"city": "Tokyo", "timezone_name": "Asia/Tokyo"}],
        responses_by_model={"gfs_global": [_response()]},
        metadata_by_model={"gfs_global": _response_clock_metadata(raw_hash="raw", request_key="request")},
        expected_models=["gfs_global"],
    )
    changed = _response()
    changed["hourly"]["temperature_2m"][1] = 89.0
    rows, _, _ = materialize_capture(
        run="2026-08-04T18:00",
        captured_at_utc=datetime(2026, 8, 5, 3, 20, tzinfo=timezone.utc),
        city_inputs=[{"city": "Tokyo", "timezone_name": "Asia/Tokyo"}],
        responses_by_model={"gfs_global": [changed]},
        metadata_by_model={"gfs_global": _response_clock_metadata(raw_hash="raw-2", request_key="request-2")},
        expected_models=["gfs_global"],
        previous_state=state,
    )
    assert build_d1_market_capture_demands(rows, allowed_cities=set()) == []
    assert build_d1_market_capture_demands(rows, allowed_cities={"Amsterdam"}) == []
    assert build_d1_market_capture_demands(
        rows,
        allowed_cities={"Tokyo"},
        max_model_run_age_hours=8,
    ) == []
    assert len(
        build_d1_market_capture_demands(
            rows,
            allowed_cities={"Tokyo"},
            max_model_run_age_hours=12,
        )
    ) == 1
