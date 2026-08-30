import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "mm_shadow", Path("scripts/ops/weather_first_selective_maker_v2_1_shadow.py")
)
assert SPEC and SPEC.loader
shadow = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shadow)


def fixture(
    tmp_path: Path,
    *,
    clock="2026-08-31T00:00:30Z",
    observed="2026-08-31T00:01:00Z",
    book_time="2026-08-31T00:00:29Z",
    receipt_gap_status="best_quote_parity_checked_sequence_unavailable",
    fee_metadata=None,
):
    actions, packets, books, output = (
        tmp_path / value for value in ("a.jsonl", "p.jsonl", "b.json", "out")
    )
    epochs = tmp_path / "subscription_epochs"
    evidence = tmp_path / "public_books"
    epochs.mkdir()
    (evidence / "2026-08-31").mkdir(parents=True)
    actions.write_text(
        json.dumps(
            {
                "event_kind": "first_positive",
                "stage": "t30",
                "signal_id": "s",
                "event_id": "e",
                "signal_event_at_utc": "2026-08-31T00:00:00Z",
                "stage_due_at_utc": clock,
                "observed_at_utc": observed,
                "policy": {
                    "action": "place_probe_maker",
                    "reason_codes": ["post_trigger_acute_window_admitted_probe"],
                    "information_horizon_seconds": 30,
                    "requested_shares": 1,
                },
            }
        )
        + "\n"
    )
    packet = {
        "packet_id": "e",
        "trigger": {
            "payload": {
                "created_at_utc": "2026-08-31T00:00:00Z",
                "model_probability_hold": "0.70",
                "current_yes_token_id": "t",
                "current_condition_id": "c",
            }
        },
    }
    packets.write_text(json.dumps(packet) + "\n")
    book = {
        "schema_version": "weather_orderbook_capture_v3",
        "token_id": "t",
        "available_at_utc": book_time,
        "fetched_at_utc": book_time,
        "book_capture_id": "book-1",
        "request_batch_capture_id": "batch-1",
        "producer_build_id": "producer-1",
        "raw_payload_hash": "raw-1",
        "exchange_book_hash": "book-hash",
        "status": "ok",
        "source_lineage_status": "collector_exact_orderbook_response_v3",
        "clock_lineage_status": "collector_exact_response_clock",
        "raw": {
            "asset_id": "t",
            "bids": [{"price": "0.60", "size": "10"}],
            "asks": [{"price": "0.62", "size": "10"}],
            "tick_size": "0.01",
            "min_order_size": "1",
            "hash": "book-hash",
        },
    }
    if fee_metadata is not None:
        book["fee_metadata"] = fee_metadata
    books.write_text(json.dumps({"records": [book]}))

    demand = shadow.CaptureDemand.create(
        consumer_id="weather_first_selective_maker_v2_1",
        strategy_key="weather_first_selective_maker_v2_1",
        condition_id="c",
        token_id="t",
        reason="v2_1_pit_route_clock_capture",
        priority="P1",
        requested_at_utc="2026-08-31T00:00:00Z",
        expires_at_utc="2026-08-31T00:10:00Z",
        desired_transport="REST_WS",
        requested_checkpoints_seconds=(0, 10, 30, 60, 120, 300),
        trigger_event_id="e",
        metadata={"route_clock_utc": "2026-08-31T00:00:30Z"},
    ).to_dict()
    resolved = {
        **demand,
        "resolution_status": "resolved_direct_token",
        "resolved_token_ids": ["t"],
        "resolved_token_count": 1,
    }
    epoch = {
        "schema_version": "weather_market_books_ws_subscription_epoch_v2",
        "producer": "weather_data_feed_service.market_books_ws",
        "producer_build_id": "market-books-release",
        "selector_version": "selector-v1",
        "subscription_epoch_id": "epoch-1",
        "started_at_utc": "2026-08-31T00:00:01Z",
        "token_map_id": "token-map-1",
        "capture_policy_id": "capture-policy-1",
        "subscription_set_id": "subscription-set-1",
        "token_ids": ["t"],
        "token_rows": {"t": {"capture_demand_ids": [demand["demand_id"]]}},
        "capture_demands": [resolved],
    }
    (epochs / "subscription_epochs_2026-08-31.jsonl").write_text(
        json.dumps(epoch) + "\n"
    )
    checkpoint_basis = {
        "demand_id": demand["demand_id"],
        "token_id": "t",
        "offset_seconds": 30,
        "due_at_utc": "2026-08-31T00:00:30Z",
    }
    checkpoint_id = hashlib.sha256(
        shadow.canonical(checkpoint_basis).encode()
    ).hexdigest()
    raw_ref = {
        "archive_path": "/raw/ws.jsonl",
        "frame_id": "frame-1",
        "line_number": 7,
        "producer_build_id": "market-books-release",
        "received_at_utc": "2026-08-31T00:00:29Z",
    }
    receipt = {
        "schema_version": "weather_public_book_evidence_v1",
        "evidence_id": "evidence-1",
        "evidence_reason": "requested_strategy_checkpoint",
        "subscription_epoch_id": "epoch-1",
        "token_id": "t",
        "book_observed_at_utc": "2026-08-31T00:00:31Z",
        "sequence_status": "exchange_sequence_unavailable",
        "gap_detection_status": receipt_gap_status,
        "token_map_id": "token-map-1",
        "capture_policy_id": "capture-policy-1",
        "subscription_set_id": "subscription-set-1",
        "producer_build_id": "market-books-release",
        "selector_version": "selector-v1",
        "raw_lineage_id": "raw-lineage-1",
        "baseline_raw_frame_ref": raw_ref,
        "delta_last_raw_frame_ref": None,
        "checkpoint_ref": {
            "checkpoint_id": checkpoint_id,
            "demand_id": demand["demand_id"],
            "strategy_key": "weather_first_selective_maker_v2_1",
            "trigger_event_id": "e",
            "offset_seconds": 30,
            "requested_checkpoint_at_utc": "2026-08-31T00:00:30Z",
            "materialization_lag_ms": 1000.0,
        },
    }
    (evidence / "2026-08-31" / "public_books_20260831_00_fixture.jsonl").write_text(
        json.dumps(receipt) + "\n"
    )
    return actions, packets, books, output, epochs, evidence


def run(paths, *, now=datetime(2026, 8, 31, 0, 1, tzinfo=timezone.utc)):
    return shadow.run_once(
        *paths[:4],
        subscription_epochs_root=paths[4],
        execution_evidence_root=paths[5],
        now=now,
    )


def test_warming_before_formal_start_does_not_backfill(tmp_path):
    paths = fixture(tmp_path, clock="2026-08-30T23:59:59Z")
    result = run(paths)
    assert result["counts"]["warming"] == 1
    assert not (paths[3] / "decisions.jsonl").exists()


def test_candidate_has_three_arms_but_maker_profit_route_is_blocked(tmp_path):
    paths = fixture(tmp_path)
    run(paths)
    decision = json.loads((paths[3] / "decisions.jsonl").read_text())
    arms = json.loads((paths[3] / "route_arms.jsonl").read_text())
    assert decision["candidate_state"] == "transient_dislocation_candidate"
    assert {"maker", "taker", "skip"} <= set(arms)
    assert decision["selected_route"] != "maker"
    assert "maker:transition_hazard_missing" in decision["route"]["blockers"]
    assert decision["capture_receipt"]["capture_succeeded"] is True
    assert decision["capture_checkpoint_receipt"]["checkpoint_id"]
    assert decision["taker_fee_forecast"]["source"] == (
        "official_weather_category_forecast_fallback"
    )


def test_future_or_incomplete_book_is_retained_as_blocker(tmp_path):
    paths = fixture(tmp_path, book_time="2026-08-31T00:00:31Z")
    run(paths)
    decision = json.loads((paths[3] / "decisions.jsonl").read_text())
    assert "pit_book_missing_or_future_only" in decision["blockers"]


def test_packet_revision_conflict_fails_closed(tmp_path):
    paths = fixture(tmp_path)
    paths[1].write_text(
        paths[1].read_text()
        + json.dumps(
            {
                "packet_id": "e",
                "trigger": {
                    "payload": {
                        "model_probability_hold": "0.80",
                        "current_yes_token_id": "t",
                        "condition_id": "c",
                    }
                },
            }
        )
        + "\n"
    )
    run(paths)
    decision = json.loads((paths[3] / "decisions.jsonl").read_text())
    assert "packet_revision_conflict" in decision["blockers"]
    assert decision["selected_route"] == "skip"


def test_restart_and_capture_demand_are_idempotent_and_safety_is_absolute(tmp_path):
    paths = fixture(tmp_path)
    run(paths)
    result = run(paths)
    out = paths[3]
    assert result["counts"]["decisions_written"] == 0
    assert len((out / "capture_demands.jsonl").read_text().splitlines()) == 1
    decision = json.loads((out / "decisions.jsonl").read_text())
    assert decision["actual_notional"] == 0 and not decision["live_authority"]
    assert (
        decision["trade_intents"]
        == decision["orders"]
        == decision["fills"]
        == decision["exchange_calls"]
        == 0
    )
    demand = json.loads((out / "capture_demands.jsonl").read_text())
    assert demand["schema_version"] == "polymarket_capture_demand_v1"
    assert demand["requested_checkpoints_seconds"] == [0, 10, 30, 60, 120, 300]
    assert len((out / "book_snapshots.jsonl").read_text().splitlines()) == 1


def test_t120_revision_does_not_replace_t30_action(tmp_path):
    paths = fixture(tmp_path)
    t120 = {
        "event_kind": "first_positive",
        "stage": "t120",
        "signal_id": "s",
        "event_id": "e",
        "signal_event_at_utc": "2026-08-31T00:00:00Z",
        "stage_due_at_utc": "2026-08-31T00:02:00Z",
        "observed_at_utc": "2026-08-31T00:02:30Z",
        "policy": {
            "action": "skip_maker",
            "reason_codes": ["t120_state_unavailable"],
            "information_horizon_seconds": 120,
            "requested_shares": 0,
        },
    }
    paths[0].write_text(paths[0].read_text() + json.dumps(t120) + "\n")
    run(paths)
    decision = json.loads((paths[3] / "decisions.jsonl").read_text())
    assert decision["candidate_state"] == "transient_dislocation_candidate"
    assert decision["route_clock_utc"] == "2026-08-31T00:00:30Z"


def test_packet_arrival_creates_capture_before_t30_action(tmp_path):
    paths = fixture(tmp_path)
    paths[0].write_text("")
    result = run(paths)
    assert result["counts"]["demands_written"] == 1
    assert result["counts"]["book_snapshots_written"] == 1
    assert not (paths[3] / "decisions.jsonl").exists()


def test_missing_route_clock_is_retained_without_crashing(tmp_path):
    paths = fixture(tmp_path)
    action = json.loads(paths[0].read_text())
    action.pop("stage_due_at_utc")
    action.pop("signal_event_at_utc")
    paths[0].write_text(json.dumps(action) + "\n")
    run(paths)
    decision = json.loads((paths[3] / "decisions.jsonl").read_text())
    assert decision["route_clock_utc"] is None
    assert "route_clock_missing" in decision["blockers"]


def test_missing_capture_receipt_forces_skip_and_retains_denominator(tmp_path):
    paths = fixture(tmp_path)
    next(paths[4].glob("*.jsonl")).write_text("")
    run(paths)
    decision = json.loads((paths[3] / "decisions.jsonl").read_text())
    assert "capture_receipt_missing" in decision["blockers"]
    assert decision["selected_route"] == "skip"
    assert len((paths[3] / "denominator.jsonl").read_text().splitlines()) == 1


def test_invalid_checkpoint_gap_or_parity_forces_skip(tmp_path):
    paths = fixture(tmp_path, receipt_gap_status="parity_mismatch")
    run(paths)
    decision = json.loads((paths[3] / "decisions.jsonl").read_text())
    assert "capture_checkpoint_gap_or_parity_invalid" in decision["blockers"]
    assert decision["selected_route"] == "skip"


def test_future_action_is_not_admitted_to_denominator(tmp_path):
    paths = fixture(tmp_path, observed="2026-08-31T00:02:00Z")
    result = run(paths)
    assert result["counts"]["future_actions"] == 1
    assert result["counts"]["denominator"] == 0
    assert not (paths[3] / "decisions.jsonl").exists()


def test_decision_waits_for_checkpoint_grace_without_freezing_missing_evidence(tmp_path):
    paths = fixture(tmp_path, observed="2026-08-31T00:00:35Z")
    result = run(
        paths,
        now=datetime(2026, 8, 31, 0, 0, 40, tzinfo=timezone.utc),
    )
    assert result["counts"]["denominator"] == 1
    assert result["counts"]["evidence_pending"] == 1
    assert not (paths[3] / "decisions.jsonl").exists()


def test_captured_per_market_fee_parameters_override_category_fallback(tmp_path):
    paths = fixture(
        tmp_path,
        fee_metadata={
            "feesEnabled": True,
            "fd": {"r": "0.03", "e": "2", "to": True},
        },
    )
    run(paths)
    decision = json.loads((paths[3] / "decisions.jsonl").read_text())
    forecast = decision["taker_fee_forecast"]
    assert forecast["source"] == "captured_clob_market_info.fd"
    assert forecast["rate"] == "0.03"
    assert forecast["exponent"] == "2"


def test_explicit_but_incomplete_market_fee_metadata_fails_closed(tmp_path):
    paths = fixture(tmp_path, fee_metadata={"feesEnabled": True})
    run(paths)
    decision = json.loads((paths[3] / "decisions.jsonl").read_text())
    assert "per_market_fee_metadata_incomplete" in decision["blockers"]
    assert decision["selected_route"] == "skip"
