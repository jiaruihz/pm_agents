from __future__ import annotations

import json

import pytest

from scripts.analysis.live_performance.weather_cross_no_city_trigger_attribution_v1 import (
    load_event_sequences,
    load_order_sequences,
    volume_conversion_decomposition,
)


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_event_sequence_uses_later_book_without_inflating_signal(tmp_path):
    path = tmp_path / "events.jsonl"
    base = {
        "status": "cross_candidate",
        "target_date": "2026-08-07",
        "city": "Seoul",
        "condition_id": "condition-1",
        "t_minus_1_no_bracket_c": 30,
        "mode": "live",
        "live_requested": True,
        "max_no_ask": 0.94,
        "min_taker_shares": 5,
        "source_cross_policy": "persistent_candidate_margin_v5",
    }
    write_jsonl(
        path,
        [
            {
                **base,
                "ts_utc": "2026-08-06T23:00:00Z",
                "best_ask": None,
                "ask_size": None,
                "live_blockers": ["missing_best_ask"],
            },
            {
                **base,
                "ts_utc": "2026-08-06T23:00:03Z",
                "best_ask": 0.9,
                "ask_size": 10,
                "live_blockers": [],
            },
        ],
    )

    result = load_event_sequences(path, "2026-08-07", "2026-08-07")[
        "condition|condition-1"
    ]
    assert result["event_rows"] == 2
    assert result["ever_book_present"] == 1
    assert result["ever_basic_price_eligible"] == 1
    assert result["ever_execution_ready"] == 1
    assert result["seconds_to_first_execution_ready"] == pytest.approx(3.0)


def test_order_sequence_separates_entry_fail_match_and_exit(tmp_path):
    path = tmp_path / "orders.jsonl"
    base = {
        "target_date": "2026-08-07",
        "city": "Busan",
        "condition_id": "condition-1",
        "t_minus_1_no_bracket_c": 34,
        "order_side": "BUY",
    }
    write_jsonl(
        path,
        [
            {
                **base,
                "child_order_role": "taker",
                "live_submit_status": "submitted",
                "order_id": "order-1",
                "exchange_response": {"place": {"status": "matched"}},
            },
            {
                **base,
                "child_order_role": "maker",
                "live_submit_status": "submit_failed",
                "error": "ask_above_max",
            },
            {
                **base,
                "order_side": "SELL",
                "child_order_role": "busan_first_routine_nonconfirmation_exit",
                "live_submit_status": "submitted",
                "order_id": "exit-1",
            },
        ],
    )

    result = load_order_sequences(path, "2026-08-07", "2026-08-07")[
        "condition|condition-1"
    ]
    assert result["raw_entry_order_records"] == 2
    assert result["submitted_entry_order_records"] == 1
    assert result["matched_entry_order_records"] == 1
    assert result["failed_entry_order_records"] == 1
    assert result["submitted_entry_order_ids"] == ["order-1"]
    assert result["exit_order_records"] == 1


def test_fill_decomposition_sums_to_observed_delta():
    earlier = {
        "settled_signals": 34,
        "signal_to_fill_conversion": 13 / 34,
        "actual_fill_expressions": 13,
    }
    later = {
        "settled_signals": 46,
        "signal_to_fill_conversion": 22 / 46,
        "actual_fill_expressions": 22,
    }
    result = volume_conversion_decomposition(earlier, later)
    assert result["fill_expression_delta"] == 9
    assert result["trigger_volume_component"] + result["fill_conversion_component"] == pytest.approx(9)
