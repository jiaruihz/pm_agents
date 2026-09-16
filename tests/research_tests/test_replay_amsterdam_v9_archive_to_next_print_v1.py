"""Focused contract tests for the legacy V9 → next-print bridge."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd


MODULE = Path(__file__).parents[2] / "scripts/analysis/forecast_quality/replay_amsterdam_v9_archive_to_next_print_v1.py"
SPEC = importlib.util.spec_from_file_location("v9_bridge", MODULE)
bridge = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(bridge)


def _metar(raw: str, *, temp: float, observed: str, available: str, kind: str = "METAR") -> dict:
    return {"station": "EHAM", "raw_metar": raw, "temp_c": temp, "source_report_ts_utc": observed, "first_seen_at_utc": available, "report_kind": kind}


def test_speci_excluded_and_same_value_is_valid_delta_zero() -> None:
    rows = [
        _metar("SPECI EHAM 301000Z 00000KT 20/10 Q1010", temp=20, observed="2026-07-30T10:00:00Z", available="2026-07-30T10:01:00Z", kind="SPECI"),
        _metar("METAR EHAM 301030Z 00000KT 20/10 Q1010", temp=20, observed="2026-07-30T10:30:00Z", available="2026-07-30T10:31:00Z"),
    ]
    result = bridge.next_routine_label(rows, target_date="2026-07-30", decision_at="2026-07-30T09:55:00Z", last_official=20.0)
    assert result["label"] == 0
    assert result["official_observed_at"].startswith("2026-07-30T10:30")


def test_identical_duplicate_collapses_but_conflict_fails_closed() -> None:
    identical = _metar("METAR EHAM 301030Z 00000KT 20/10 Q1010", temp=20, observed="2026-07-30T10:30:00Z", available="2026-07-30T10:31:00Z")
    assert bridge.next_routine_label([identical, dict(identical)], target_date="2026-07-30", decision_at="2026-07-30T10:00:00Z", last_official=19)["label"] == 1
    conflict = dict(identical, temp_c=21)
    assert bridge.next_routine_label([identical, conflict], target_date="2026-07-30", decision_at="2026-07-30T10:00:00Z", last_official=19)["reason"] == "AMBIGUOUS_OFFICIAL_PRINT"


def test_late_source_cannot_jump_to_a_far_later_official_print() -> None:
    later = _metar(
        "METAR EHAM 301230Z 00000KT 25/10 Q1010",
        temp=25,
        observed="2026-07-30T12:30:00Z",
        available="2026-07-30T12:31:00Z",
    )
    result = bridge.next_routine_label(
        [later],
        target_date="2026-07-30",
        decision_at="2026-07-30T12:00:00Z",
        source_observed_at="2026-07-30T03:00:00Z",
        last_official=16,
    )
    assert result["label"] is None
    assert result["reason"] == "NEXT_ROUTINE_OUTSIDE_FROZEN_MATCH_WINDOW"


def test_next_print_already_known_at_decision_is_not_a_label() -> None:
    next_print = _metar(
        "METAR EHAM 301030Z 00000KT 20/10 Q1010",
        temp=20,
        observed="2026-07-30T10:30:00Z",
        available="2026-07-30T10:31:00Z",
    )
    result = bridge.next_routine_label(
        [next_print],
        target_date="2026-07-30",
        decision_at="2026-07-30T10:40:00Z",
        source_observed_at="2026-07-30T10:20:00Z",
        last_official=19,
    )
    assert result["label"] is None
    assert result["reason"] == "NEXT_ROUTINE_ALREADY_AVAILABLE_AT_DECISION"


def test_prior_official_state_is_rebuilt_from_raw_asof_rows() -> None:
    rows = [
        _metar("METAR EHAM 301000Z 00000KT 18/10 Q1010", temp=18, observed="2026-07-30T10:00:00Z", available="2026-07-30T10:01:00Z"),
        _metar("METAR EHAM 301030Z 00000KT 21/10 Q1010", temp=21, observed="2026-07-30T10:30:00Z", available="2026-07-30T10:31:00Z"),
        _metar("METAR EHAM 301100Z 00000KT 20/10 Q1010", temp=20, observed="2026-07-30T11:00:00Z", available="2026-07-30T11:01:00Z"),
    ]
    events, reason = bridge._official_events(rows, "2026-07-30")
    assert reason is None
    prior, reason = bridge.prior_official_state(
        events,
        decision_at="2026-07-30T11:05:00Z",
        source_observed_at="2026-07-30T11:10:00Z",
    )
    assert reason is None
    assert prior["last_official_native_value"] == 20.0
    assert prior["official_running_max"] == 21.0


def test_future_knmi_row_is_not_in_causal_path() -> None:
    rows = [
        {"observation_time_utc": "2026-07-29T22:00:00Z", "knmi_first_seen_at_utc": "2026-07-29T22:01:00Z", "temp_c": 18, "payload_hash": "a"},
        {"observation_time_utc": "2026-07-29T22:10:00Z", "knmi_first_seen_at_utc": "2026-07-29T22:11:00Z", "temp_c": 19, "payload_hash": "b"},
        {"observation_time_utc": "2026-07-29T22:20:00Z", "knmi_first_seen_at_utc": "2026-07-29T22:21:00Z", "temp_c": 99, "payload_hash": "future"},
    ]
    path, reason = bridge._source_path(rows, target_date="2026-07-30", decision_at=bridge._ts("2026-07-29T22:15:00Z"), observed_at=bridge._ts("2026-07-29T22:10:00Z"), last_official=18, running_max=18)
    assert reason is None
    assert path.latest_fast_native_value.tolist() == [18.0, 19.0]


def test_missing_ten_minute_knmi_interval_fails_closed() -> None:
    rows = [
        {"observation_time_utc": "2026-07-29T22:00:00Z", "knmi_first_seen_at_utc": "2026-07-29T22:01:00Z", "temp_c": 18, "payload_hash": "a"},
        {"observation_time_utc": "2026-07-29T22:20:00Z", "knmi_first_seen_at_utc": "2026-07-29T22:21:00Z", "temp_c": 19, "payload_hash": "b"},
    ]
    _, reason = bridge._source_path(
        rows,
        target_date="2026-07-30",
        decision_at=bridge._ts("2026-07-29T22:25:00Z"),
        observed_at=bridge._ts("2026-07-29T22:20:00Z"),
        last_official=18,
        running_max=18,
    )
    assert reason == "INCOMPLETE_CAPTURED_SOURCE_PATH"


def test_exact_token_and_source_relative_horizon_coverage(tmp_path: Path) -> None:
    record = {"condition_id": "c", "bracket": 25, "yes_token_id": "yes", "no_token_id": "no", "yes_best_ask": 0.4, "yes_best_bid": 0.3, "yes_ask_size": 10, "yes_bid_size": 8}
    t0 = tmp_path / "snapshot_20260730_020000_000000_x_t000_2026-07-30.json"
    t0.write_text(json.dumps({"records": [record]}))
    t15 = tmp_path / "snapshot_20260730_020000_000000_x_t015_2026-07-30.json"
    t15.write_text(json.dumps({"records": [record]}))
    coverage = bridge.horizon_book_coverage(t0, condition_id="c", token_id="yes")
    assert coverage["book_t0_source_relative"]["ask"] == 0.4
    assert coverage["book_t015_source_relative"]["bid"] == 0.3
    assert coverage["book_t030_source_relative"] is None
    assert "source_relative" in next(key for key in coverage if key.startswith("book_t015"))
    condition, token, reason = bridge.checkpoint_token_identity({"snapshot_path": str(t0), "current_bracket_c": 25.0, "selected_side": "yes"})
    assert (condition, token, reason) == ("c", "yes", None)

    incomplete = tmp_path / "snapshot_20260730_030000_000000_y_t000_2026-07-30.json"
    incomplete.write_text(json.dumps({"capture_status": "partial", "records": [record]}))
    incomplete_coverage = bridge.horizon_book_coverage(incomplete, condition_id="c", token_id="yes")
    assert incomplete_coverage["book_t0_source_relative"] is None
    assert incomplete_coverage["book_t0_reason"] == "SNAPSHOT_CAPTURE_INCOMPLETE"

    duplicate = dict(record, bracket=26)
    book, reason = bridge.exact_token_books(
        {"records": [record, duplicate]}, condition_id="c", token_id="yes"
    )
    assert book is None
    assert reason == "TOKEN_IDENTITY_AMBIGUOUS"


def test_summary_uses_manifest_support_and_never_invents_roi() -> None:
    frame = pd.DataFrame([{
        "status": "OK",
        "target_date": "2026-07-30",
        "official_print_group_id": "report-1",
        "opportunity_matched": True,
        "next_official_delta_native_tick": 0,
        "B2_argmax_delta_native_tick": 0,
        "M1_argmax_delta_native_tick": 0,
        "M2_argmax_delta_native_tick": 0,
        "B2_pmf": "[0.1,0.8,0.1]",
        "M1_pmf": "[0.1,0.8,0.1]",
        "M2_pmf": "[0.1,0.8,0.1]",
    }])
    manifests = {name: {"support": [-1, 0, 1]} for name in bridge.MODELS}
    summary = bridge.summarize(frame, manifests)
    assert summary["B2"]["accuracy"] == 1.0
    assert summary["B2"]["official_print_groups"] == 1
    assert summary["opportunity_matched_cohort"]["weather_label_rows"] == 1
    assert summary["baselines"]["always_zero_next_print"]["accuracy"] == 1.0
    assert summary["pairwise_target_date_bootstrap"]["M1_minus_B2"]["candidate_minus_baseline_rps"] == 0.0
    assert summary["model_trade_signals"] is None
    assert summary["model_roi"] is None
    markdown = bridge.summary_markdown(summary)
    assert "| B2 | 1 | 1 | 1 |" in markdown
    assert "ROI/PnL is intentionally absent" in markdown
