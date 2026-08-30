from __future__ import annotations

import json

import pytest

from us_fast_weather_lab.metar_market_reaction import LEFT_CENSORED, analyze_metar_market_reaction


def _jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _inputs(tmp_path, *, epoch_start=50, frames=(), source="metar_ws_metar"):
    source_root = tmp_path / "source_events"
    _jsonl(source_root / "2026-08-30" / "sources.jsonl", [
        {
            "information_event_id": "official-seed", "city": "Miami", "station_id": "KMIA",
            "source": "aviationweather_metar", "target_date": "2026-08-30",
            "source_report_ts_utc": "2026-08-30T09:00:00Z",
            "transport_received_at_utc": "2026-08-30T09:01:00Z",
            "transport_received_monotonic_ns": 80_000_000_000, "temp_c": 29.0,
        },
        {
            "information_event_id": "event-1", "city": "Miami", "station_id": "KMIA", "source": source,
            "target_date": "2026-08-30", "source_report_ts_utc": "2026-08-30T10:00:00Z",
            "transport_received_at_utc": "2026-08-30T10:00:01Z",
            "transport_received_monotonic_ns": 100_000_000_000, "temp_c": 30.0, "pit_eligible": True,
        },
    ])
    demands = tmp_path / "market_capture_demands.jsonl"
    _jsonl(demands, [{
        "schema_version": "polymarket_capture_demand_v1", "strategy_key": "weather.metar_ws_event_repricing",
        "demand_id": "demand-1", "trigger_event_id": "event-1", "token_id": "yes", "condition_id": "condition-1",
        "metadata": {"city": "Miami", "bracket": "86", "outcome": "yes", "source": source,
                     "transport_received_monotonic_ns": 100_000_000_000},
    }])
    epochs = tmp_path / "epochs.jsonl"
    _jsonl(epochs, [{"schema_version": "weather_market_books_ws_subscription_epoch_v2", "subscription_epoch_id": "e1",
                     "started_at_utc": "2026-08-30T10:00:00Z", "started_wall_ns": epoch_start * 1_000_000_000,
                     "started_monotonic_ns": epoch_start * 1_000_000_000, "token_ids": ["yes"], "token_rows": {"yes": {}}}])
    raw = tmp_path / "raw.jsonl"; _jsonl(raw, frames)
    return source_root, demands, epochs, raw


def _frame(second, message, epoch="e1"):
    return {"subscription_epoch_id": epoch, "transport_received_monotonic_ns": second * 1_000_000_000,
            "received_at_utc": "2026-08-30T10:00:00Z", "message": message}


def _book(bid, ask):
    return {"event_type": "book", "asset_id": "yes", "bids": [{"price": bid, "size": 2}], "asks": [{"price": ask, "size": 2}]}


def test_pre_event_book_depth_only_then_top_change_and_public_trade(tmp_path):
    paths = _inputs(tmp_path, frames=[
        _frame(90, _book(.40, .60)),
        _frame(105, {"event_type": "price_change", "asset_id": "yes", "side": "BUY", "price": .35, "size": 5}),
        _frame(115, {"event_type": "price_change", "asset_id": "yes", "side": "BUY", "price": .45, "size": 5}),
        _frame(120, {"event_type": "last_trade_price", "asset_id": "yes", "price": .45, "size": 4, "side": "BUY"}),
    ])
    report = analyze_metar_market_reaction(*paths[:3], [paths[3]])
    row = report["rows"][0]
    assert row["status"] == "OBSERVED" and row["baseline_bid"] == .40
    assert row["first_depth_update_delta_sec"] == 5
    assert row["first_top_change_delta_sec"] == 15
    assert row["first_public_trade_print_delta_sec"] == 20
    assert row["public_trade_print_semantics"] == "public trade print != own fill"
    assert row["checkpoint_15s_top_changed"] is True
    assert row["running_max_f_before"] == pytest.approx(84.2) and row["running_max_f_after"] == 86.0
    assert row["is_new_running_max"] is True and row["bracket_transition"] is True
    assert report["summary"]["event_funnels"]["material_running_max_or_bracket_transition_events"]["event_count"] == 1


def test_post_event_first_subscription_is_left_censored(tmp_path):
    paths = _inputs(tmp_path, epoch_start=105, frames=[_frame(105, _book(.4, .6))])
    row = analyze_metar_market_reaction(*paths[:3], [paths[3]])["rows"][0]
    assert row["pre_event_subscription"] is False
    assert row["status"] == LEFT_CENSORED and row["first_top_change_delta_sec"] is None


def test_list_message_and_reconnect_book_is_not_top_change(tmp_path):
    paths = _inputs(tmp_path, frames=[
        _frame(90, [_book(.4, .6)]),
        _frame(110, [_book(.55, .65)], epoch="e2"),
        _frame(115, [{"event_type": "best_bid_ask", "asset_id": "yes", "best_bid": .57, "best_ask": .65}], epoch="e2"),
    ])
    # A reconnect epoch is declared after the initial epoch.
    _jsonl(paths[2], [
        {"schema_version": "weather_market_books_ws_subscription_epoch_v2", "subscription_epoch_id": "e1", "started_monotonic_ns": 50_000_000_000, "token_ids": ["yes"], "token_rows": {"yes": {}}},
        {"schema_version": "weather_market_books_ws_subscription_epoch_v2", "subscription_epoch_id": "e2", "started_monotonic_ns": 105_000_000_000, "token_ids": ["yes"], "token_rows": {"yes": {}}},
    ])
    row = analyze_metar_market_reaction(*paths[:3], [paths[3]])["rows"][0]
    assert row["first_top_change_delta_sec"] == 15  # not the e2 book at +10s


def test_no_change_remains_denominator_and_empty_sides_are_normalized(tmp_path):
    paths = _inputs(tmp_path, frames=[_frame(90, {"event_type": "book", "asset_id": "yes", "bids": [], "asks": []})])
    report = analyze_metar_market_reaction(*paths[:3], [paths[3]])
    row = report["rows"][0]
    assert row["baseline_bid"] == 0 and row["baseline_ask"] == 1
    assert row["first_top_change_delta_sec"] is None
    assert report["summary"]["event_token_denominator"] == 1


def test_real_nested_price_changes_use_authoritative_top(tmp_path):
    paths = _inputs(tmp_path, frames=[
        _frame(90, _book(.40, .60)),
        _frame(105, {
            "event_type": "price_change",
            "price_changes": [{
                "asset_id": "yes", "side": "BUY", "price": .35, "size": 5,
                "best_bid": .40, "best_ask": .60,
            }],
        }),
        _frame(115, {
            "event_type": "price_change",
            "price_changes": [{
                "asset_id": "yes", "side": "BUY", "price": .45, "size": 5,
                "best_bid": .45, "best_ask": .60,
            }],
        }),
    ])
    row = analyze_metar_market_reaction(*paths[:3], [paths[3]])["rows"][0]
    assert row["first_depth_update_delta_sec"] == 5
    assert row["first_top_change_delta_sec"] == 15
    assert row["checkpoint_15s_top_changed"] is True


def test_invalid_change_and_partial_best_bid_ask_do_not_create_fake_top(tmp_path):
    paths = _inputs(tmp_path, frames=[
        _frame(90, _book(.40, .60)),
        _frame(105, {"event_type": "price_change", "asset_id": "yes", "side": "BUY"}),
        _frame(110, {"event_type": "best_bid_ask", "asset_id": "yes", "best_bid": .45}),
    ])
    row = analyze_metar_market_reaction(*paths[:3], [paths[3]])["rows"][0]
    assert row["first_depth_update_delta_sec"] is None
    assert row["first_top_change_delta_sec"] is None


def test_reconnect_snapshot_difference_is_not_checkpoint_top_change(tmp_path):
    paths = _inputs(tmp_path, frames=[
        _frame(90, _book(.40, .60)),
        _frame(110, _book(.55, .65), epoch="e2"),
    ])
    _jsonl(paths[2], [
        {"schema_version": "weather_market_books_ws_subscription_epoch_v2", "subscription_epoch_id": "e1",
         "started_monotonic_ns": 50_000_000_000, "token_ids": ["yes"], "token_rows": {"yes": {}}},
        {"schema_version": "weather_market_books_ws_subscription_epoch_v2", "subscription_epoch_id": "e2",
         "started_monotonic_ns": 105_000_000_000, "token_ids": ["yes"], "token_rows": {"yes": {}}},
    ])
    row = analyze_metar_market_reaction(*paths[:3], [paths[3]])["rows"][0]
    assert row["checkpoint_15s_snapshot_differs_from_baseline"] is True
    assert row["checkpoint_15s_top_changed"] is False
    assert row["first_buy_1share_effective_deterioration_delta_sec"] is None


def test_full_depth_sweeps_use_sizes_and_weather_fee(tmp_path):
    paths = _inputs(tmp_path, frames=[
        _frame(90, {
            "event_type": "book", "asset_id": "yes",
            "bids": [{"price": .40, "size": 2}, {"price": .35, "size": 10}],
            "asks": [{"price": .60, "size": 2}, {"price": .65, "size": 10}],
        }),
    ])
    row = analyze_metar_market_reaction(*paths[:3], [paths[3]])["rows"][0]
    assert row["baseline_full_depth_valid"] is True
    assert row["baseline_buy_1share_fully_executable"] is True
    assert row["baseline_buy_5share_vwap"] == pytest.approx(0.63)
    assert row["baseline_sell_5share_vwap"] == pytest.approx(0.37)
    expected_buy_fee = 2 * .05 * .60 * .40 + 3 * .05 * .65 * .35
    assert abs(row["baseline_buy_5share_fee"] - expected_buy_fee) < 1e-12
    assert row["baseline_buy_10share_fully_executable"] is True


def test_first_size_aware_effective_deterioration(tmp_path):
    paths = _inputs(tmp_path, frames=[
        _frame(90, {
            "event_type": "book", "asset_id": "yes",
            "bids": [{"price": .40, "size": 20}],
            "asks": [{"price": .60, "size": 1}, {"price": .61, "size": 20}],
        }),
        _frame(105, {
            "event_type": "price_change", "asset_id": "yes", "side": "SELL",
            "price": .60, "size": 0, "best_bid": .40, "best_ask": .61,
        }),
    ])
    row = analyze_metar_market_reaction(*paths[:3], [paths[3]])["rows"][0]
    assert row["first_buy_1share_effective_deterioration_delta_sec"] == 5
    assert row["first_buy_5share_effective_deterioration_delta_sec"] == 5
    assert row["checkpoint_15s_buy_5share_fully_executable"] is True


def test_unknown_subscription_epoch_is_ignored(tmp_path):
    paths = _inputs(tmp_path, frames=[_frame(90, _book(.40, .60), epoch="unknown")])
    row = analyze_metar_market_reaction(*paths[:3], [paths[3]])["rows"][0]
    assert row["status"] == LEFT_CENSORED
    assert row["baseline_bid"] is None


def test_invalid_or_crossed_direct_top_is_ignored(tmp_path):
    paths = _inputs(tmp_path, frames=[
        _frame(90, _book(.40, .60)),
        _frame(105, {
            "event_type": "best_bid_ask", "asset_id": "yes",
            "best_bid": .70, "best_ask": .60,
        }),
    ])
    row = analyze_metar_market_reaction(*paths[:3], [paths[3]])["rows"][0]
    assert row["first_top_change_delta_sec"] is None
    assert row["checkpoint_15s_bid"] == .40
    assert row["checkpoint_15s_full_depth_valid"] is True


def test_direct_top_mismatch_invalidates_stale_depth(tmp_path):
    paths = _inputs(tmp_path, frames=[
        _frame(90, {
            "event_type": "book", "asset_id": "yes",
            "bids": [{"price": .40, "size": 20}],
            "asks": [{"price": .60, "size": 20}],
        }),
        _frame(105, {
            "event_type": "best_bid_ask", "asset_id": "yes",
            "best_bid": .45, "best_ask": .65,
        }),
    ])
    row = analyze_metar_market_reaction(*paths[:3], [paths[3]])["rows"][0]
    assert row["checkpoint_15s_bid"] == .45
    assert row["checkpoint_15s_full_depth_valid"] is False
    assert row["checkpoint_15s_buy_5share_fully_executable"] is False
    assert row["checkpoint_15s_buy_5share_effective_price"] is None


def test_equal_monotonic_frames_replay_deterministically(tmp_path):
    paths = _inputs(tmp_path, frames=[
        _frame(90, _book(.40, .60)),
        {
            **_frame(105, {
                "event_type": "price_change", "asset_id": "yes", "side": "BUY",
                "price": .50, "size": 5,
            }),
            "raw_frame_id": "a",
        },
    ])
    second_raw = tmp_path / "raw-second.jsonl"
    _jsonl(second_raw, [{
        **_frame(105, {
            "event_type": "price_change", "asset_id": "yes", "side": "BUY",
            "price": .50, "size": 0,
        }),
        "raw_frame_id": "b",
    }])
    forward = analyze_metar_market_reaction(*paths[:3], [paths[3], second_raw])
    reversed_inputs = analyze_metar_market_reaction(*paths[:3], [second_raw, paths[3]])
    assert forward == reversed_inputs
    assert forward["rows"][0]["checkpoint_15s_bid"] == .40


def test_chicago_is_explicit_control_not_primary_efficacy(tmp_path):
    paths = _inputs(tmp_path, frames=[_frame(90, _book(.40, .60))])
    source_path = paths[0] / "2026-08-30" / "sources.jsonl"
    rows = [json.loads(line) for line in source_path.read_text().splitlines()]
    rows[0]["city"] = "Chicago"; rows[0]["station_id"] = "KORD"
    rows[1]["city"] = "Chicago"; rows[1]["station_id"] = "KORD"
    source_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    demand_rows = [json.loads(line) for line in paths[1].read_text().splitlines()]
    demand_rows[0]["metadata"]["city"] = "Chicago"
    paths[1].write_text("".join(json.dumps(row) + "\n" for row in demand_rows))
    report = analyze_metar_market_reaction(*paths[:3], [paths[3]])
    assert report["rows"][0]["cohort_role"] == "basis_mismatch_control"
    assert report["rows"][0]["basis_status"] == "source_market_station_mismatch"
    assert report["summary"]["cohort"]["primary_event_token_denominator"] == 0
    assert report["summary"]["cohort"]["basis_mismatch_control_event_token_denominator"] == 1


def test_explicit_demand_city_conflict_fails_closed(tmp_path):
    paths = _inputs(tmp_path, frames=[_frame(90, _book(.40, .60))])
    demand_rows = [json.loads(line) for line in paths[1].read_text().splitlines()]
    demand_rows[0]["metadata"]["city"] = "Chicago"
    paths[1].write_text("".join(json.dumps(row) + "\n" for row in demand_rows))
    with pytest.raises(ValueError, match="conflicts with fixed cohort field: city"):
        analyze_metar_market_reaction(*paths[:3], [paths[3]])
