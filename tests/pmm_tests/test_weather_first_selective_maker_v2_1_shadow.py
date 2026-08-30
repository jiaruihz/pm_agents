import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path


SPEC = importlib.util.spec_from_file_location("mm_shadow", Path("scripts/ops/weather_first_selective_maker_v2_1_shadow.py"))
assert SPEC and SPEC.loader
shadow = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(shadow)


def fixture(tmp_path: Path, *, clock="2026-08-31T00:00:30Z", book_time="2026-08-31T00:00:29Z"):
    actions, packets, books, output = (tmp_path / x for x in ("a.jsonl", "p.jsonl", "b.json", "out"))
    actions.write_text(json.dumps({"event_kind":"first_positive","stage":"t30","signal_id":"s","event_id":"e","signal_event_at_utc":"2026-08-31T00:00:00Z","stage_due_at_utc":clock,"observed_at_utc":"2026-08-31T00:01:00Z","policy":{"action":"place_probe_maker","reason_codes":["post_trigger_acute_window_admitted_probe"],"information_horizon_seconds":30,"requested_shares":1}})+"\n")
    packets.write_text(json.dumps({"packet_id":"e","trigger":{"payload":{"created_at_utc":"2026-08-31T00:00:00Z","model_probability_hold":"0.70","current_yes_token_id":"t","current_condition_id":"c"}}})+"\n")
    books.write_text(json.dumps({"records":[{"token_id":"t","available_at_utc":book_time,"book_capture_id":"book-1","request_batch_capture_id":"batch-1","producer_build_id":"producer-1","raw_payload_hash":"raw-1","exchange_book_hash":"book-hash","status":"ok","raw":{"asset_id":"t","bids":[{"price":"0.60","size":"10"}],"asks":[{"price":"0.62","size":"10"}],"tick_size":"0.01","min_order_size":"1","hash":"book-hash"}}]}))
    return actions, packets, books, output


def run(paths):
    return shadow.run_once(*paths, now=datetime(2026, 8, 31, 0, 1, tzinfo=timezone.utc))


def test_warming_before_formal_start_does_not_backfill(tmp_path):
    paths=fixture(tmp_path, clock="2026-08-30T23:59:59Z"); result=run(paths)
    assert result["counts"]["warming"] == 1
    assert not (paths[-1] / "decisions.jsonl").exists()


def test_candidate_has_three_arms_but_maker_profit_route_is_blocked(tmp_path):
    paths=fixture(tmp_path); run(paths); out=paths[-1]
    decision=json.loads((out/"decisions.jsonl").read_text()); arms=json.loads((out/"route_arms.jsonl").read_text())
    assert decision["candidate_state"] == "transient_dislocation_candidate"
    assert {"maker","taker","skip"} <= set(arms)
    assert decision["selected_route"] != "maker"
    assert "maker:transition_hazard_missing" in decision["route"]["blockers"]


def test_future_or_incomplete_book_is_retained_as_blocker(tmp_path):
    paths=fixture(tmp_path, book_time="2026-08-31T00:00:31Z"); run(paths)
    decision=json.loads((paths[-1]/"decisions.jsonl").read_text())
    assert "pit_book_missing_or_future_only" in decision["blockers"]


def test_packet_revision_conflict_fails_closed(tmp_path):
    paths=fixture(tmp_path); paths[1].write_text(paths[1].read_text()+json.dumps({"packet_id":"e","trigger":{"payload":{"model_probability_hold":"0.80","current_yes_token_id":"t","condition_id":"c"}}})+"\n")
    run(paths); d=json.loads((paths[-1]/"decisions.jsonl").read_text())
    assert "packet_revision_conflict" in d["blockers"] and d["selected_route"] == "skip"


def test_restart_and_capture_demand_are_idempotent_and_safety_is_absolute(tmp_path):
    paths=fixture(tmp_path); run(paths); result=run(paths); out=paths[-1]
    assert result["counts"]["decisions_written"] == 0
    assert len((out/"capture_demands.jsonl").read_text().splitlines()) == 1
    decision=json.loads((out/"decisions.jsonl").read_text())
    assert decision["actual_notional"] == 0 and not decision["live_authority"]
    assert decision["trade_intents"] == decision["orders"] == decision["fills"] == decision["exchange_calls"] == 0
    demand=json.loads((out/"capture_demands.jsonl").read_text())
    assert demand["schema_version"] == "polymarket_capture_demand_v1"
    assert demand["requested_checkpoints_seconds"] == [0,10,30,60,120,300]
    assert len((out/"book_snapshots.jsonl").read_text().splitlines()) == 1


def test_t120_revision_does_not_replace_t30_action(tmp_path):
    paths=fixture(tmp_path)
    t120={"event_kind":"first_positive","stage":"t120","signal_id":"s","event_id":"e","signal_event_at_utc":"2026-08-31T00:00:00Z","stage_due_at_utc":"2026-08-31T00:02:00Z","observed_at_utc":"2026-08-31T00:02:30Z","policy":{"action":"skip_maker","reason_codes":["t120_state_unavailable"],"information_horizon_seconds":120,"requested_shares":0}}
    paths[0].write_text(paths[0].read_text()+json.dumps(t120)+"\n")
    run(paths)
    decision=json.loads((paths[-1]/"decisions.jsonl").read_text())
    assert decision["candidate_state"] == "transient_dislocation_candidate"
    assert decision["route_clock_utc"] == "2026-08-31T00:00:30Z"


def test_packet_arrival_creates_capture_before_t30_action(tmp_path):
    paths=fixture(tmp_path)
    paths[0].write_text("")
    result=run(paths)
    assert result["counts"]["demands_written"] == 1
    assert result["counts"]["book_snapshots_written"] == 1
    assert not (paths[-1]/"decisions.jsonl").exists()


def test_missing_route_clock_is_retained_without_crashing(tmp_path):
    paths=fixture(tmp_path)
    action=json.loads(paths[0].read_text())
    action.pop("stage_due_at_utc")
    action.pop("signal_event_at_utc")
    paths[0].write_text(json.dumps(action)+"\n")
    run(paths)
    decision=json.loads((paths[-1]/"decisions.jsonl").read_text())
    assert decision["route_clock_utc"] is None
    assert "route_clock_missing" in decision["blockers"]
