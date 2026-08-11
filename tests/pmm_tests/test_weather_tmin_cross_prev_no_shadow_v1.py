from __future__ import annotations

import json
from pathlib import Path

from scripts.ops.weather_tmin_cross_prev_no_shadow_v1 import _new_state, run_cycle


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def test_first_tmin_cross_emits_one_blocked_no_candidate(tmp_path: Path) -> None:
    events = tmp_path / "input" / "events.jsonl"
    quotes = tmp_path / "input" / "quotes.jsonl"
    output = tmp_path / "output"
    event = {
        "event_key": "Tokyo|2026-08-11|jma_amedas|23",
        "city": "Tokyo",
        "target_date": "2026-08-11",
        "source": "jma_amedas",
        "source_basis_class": "same_airport_alternate_sensor",
        "source_calibration_status": "alignment_and_repricing_pending",
        "source_live_eligible": False,
        "source_blocked_reason": "requires_source_to_settlement_alignment",
        "source_obs_ts_utc": "2026-08-11T11:50:00Z",
        "source_detect_ts_utc": "2026-08-11T11:57:28Z",
        "created_at_utc": "2026-08-11T11:57:29Z",
        "extreme_kind": "min",
        "reference_running_extreme_round_c": 24,
        "current_bracket_value": 23,
        "previous_market_bracket": "24",
    }
    quote = {
        "event_key": event["event_key"],
        "ts_utc": "2026-08-11T11:57:31Z",
        "quotes": {
            "t_minus_1": {
                "no": {
                    "fresh_status": "ok",
                    "fresh_best_bid": 0.70,
                    "fresh_best_ask": 0.74,
                    "condition_id": "condition-24",
                    "market_id": "market-24",
                    "token_id": "no-token-24",
                }
            }
        },
    }
    _append(events, event)
    _append(quotes, quote)
    args = type("Args", (), {"events": str(events), "quotes": str(quotes), "output_dir": str(output)})()
    state = _new_state()

    summary = run_cycle(args, state)
    candidate = json.loads((output / "signal_candidates.jsonl").read_text().strip())
    model = json.loads((output / "model_outputs.jsonl").read_text().strip())

    assert summary["signal_funnel"]["canonical_signal_candidates"] == 1
    assert summary["evidence_funnel"]["market_evidence_available"] == 1
    assert summary["trade_intent_count"] == summary["order_count"] == 0
    assert candidate["side"] == "NO"
    assert candidate["bracket"] == "24"
    assert candidate["executable_cost"] == 0.74
    assert candidate["candidate_status"] == "blocked"
    assert candidate["blocker_reason"] == "next_colder_no_model_not_frozen"
    assert candidate["selected"] is False
    assert model["scorable_status"] == "not_scorable"

    run_cycle(args, state)
    assert len((output / "signal_candidates.jsonl").read_text().splitlines()) == 1


def test_non_cross_and_tmax_rows_do_not_enter_signal_funnel(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    _append(
        events,
        {
            "event_key": "not-a-cross",
            "extreme_kind": "min",
            "current_bracket_value": 24,
            "reference_running_extreme_round_c": 24,
            "previous_market_bracket": "24",
        },
    )
    _append(
        events,
        {
            "event_key": "tmax-cross",
            "extreme_kind": "max",
            "current_bracket_value": 25,
            "reference_running_extreme_round_c": 24,
            "previous_market_bracket": "24",
        },
    )
    args = type("Args", (), {"events": str(events), "quotes": str(quotes), "output_dir": str(tmp_path / "out")})()
    summary = run_cycle(args, _new_state())
    assert summary["signal_funnel"]["raw_cross_events"] == 0
    assert summary["signal_funnel"]["canonical_signal_candidates"] == 0
    assert summary["order_count"] == 0
