from __future__ import annotations

import gzip
import json
from pathlib import Path

from weather_model_evaluation.korea_comparison_ledger import (
    build_korea_comparison_ledger,
)


def _write_predictions(path: Path, rows: list[dict]) -> Path:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    return path


def _busan(**overrides: object) -> dict:
    row = {
        "city": "Busan",
        "source_event_key": "Busan|2026-08-12|event",
        "checkpoint_id": "checkpoint-1",
        "decision_ts_utc": "2026-08-12T02:00:00Z",
        "source_observation_ts_utc": "2026-08-12T01:58:00Z",
        "source_first_seen_ts_utc": "2026-08-12T01:59:00Z",
        "source_available_at_utc": "2026-08-12T01:59:01Z",
        "collector_emitted_at_utc": "2026-08-12T01:59:02Z",
        "feature_book_available_at_utc": "2026-08-12T01:59:03Z",
        "target_date": "2026-08-12",
        "condition_id": "condition-1",
        "market_id": "market-1",
        "token_id": "token-1",
        "bracket": "29",
        "side": "NO",
        "model_p_no": 0.7,
        "market_p_no": 0.6,
        "label_no": 1,
        "same_row_score_status": "scorable",
    }
    row.update(overrides)
    return row


def test_keeps_missing_rule_evidence_without_fabricating_rule_probability(tmp_path: Path) -> None:
    rows, summary = build_korea_comparison_ledger([_write_predictions(tmp_path / "busan.gz", [_busan()])])

    assert len(rows) == 1
    assert rows[0]["model_market_status"] == "scorable"
    assert rows[0]["three_arm_status"] == "blocked_missing_incumbent_rule_evidence"
    assert rows[0]["incumbent_rule_probability"] is None
    assert rows[0]["incumbent_rule_decision"] is None
    assert summary["rule_evidence_status_counts"] == {"missing_exact_rule_evidence": 1}


def test_blocks_duplicate_keys_and_market_time_or_token_mismatch(tmp_path: Path) -> None:
    duplicate = _busan()
    time_and_token_mismatch = _busan(
        checkpoint_id="checkpoint-2",
        market_decision_ts_utc="2026-08-12T02:00:01Z",
        market_token_id="other-token",
    )
    rows, _ = build_korea_comparison_ledger(
        [_write_predictions(tmp_path / "busan.gz", [_busan(), duplicate, time_and_token_mismatch])]
    )

    assert "duplicate_or_conflicting_comparison_key" in rows[0]["model_market_blockers"]
    assert "duplicate_or_conflicting_comparison_key" in rows[1]["model_market_blockers"]
    assert "market_decision_time_mismatch" in rows[2]["model_market_blockers"]
    assert "market_token_identity_mismatch" in rows[2]["model_market_blockers"]


def test_seoul_probability_targets_are_isolated_and_identity_gap_is_retained(tmp_path: Path) -> None:
    seoul = {
        "city": "Seoul",
        "source_event_key": "Seoul|2026-08-12|event",
        "checkpoint_id": "checkpoint-1",
        "decision_ts_utc": "2026-08-12T02:00:00Z",
        "source_observation_ts_utc": "2026-08-12T01:58:00Z",
        "source_first_seen_ts_utc": "2026-08-12T01:59:00Z",
        "source_available_at_utc": "2026-08-12T01:59:01Z",
        "collector_emitted_at_utc": "2026-08-12T01:59:02Z",
        "feature_book_available_at_utc": "2026-08-12T01:59:03Z",
        "target_date": "2026-08-12",
        "posterior_distribution": [0.1, 0.2, 0.3, 0.2, 0.2],
        "market_distribution": [0.2, 0.2, 0.2, 0.2, 0.2],
        "label_index": 2,
        "market_join_status": "scorable",
    }
    rows, _ = build_korea_comparison_ledger([_write_predictions(tmp_path / "seoul.gz", [seoul])])

    assert len(rows) == 5
    assert {row["expression"] for row in rows} == {
        "remaining_heat:negative", "remaining_heat:zero", "remaining_heat:plus1",
        "remaining_heat:plus2", "remaining_heat:plus3",
    }
    assert [row["settlement_label"] for row in rows] == [0.0, 0.0, 1.0, 0.0, 0.0]
    assert all(row["model_market_status"] == "blocked" for row in rows)
    assert all(
        any(blocker.startswith("missing_market_expression_identity") for blocker in row["model_market_blockers"])
        for row in rows
    )


def test_seoul_distribution_uses_ladder_identity_not_single_token(tmp_path):
    row = {**_busan(), "city": "Seoul", "market_decision_id": "ladder-decision",
           "feature_book_snapshot_id_distribution": "ladder-snapshot", "routine_rung": 20,
           "market_join_status": "scorable", "posterior_distribution": [0.1, 0.2, 0.3, 0.2, 0.2],
           "market_distribution": [0.2] * 5, "label_index": 2}
    for key in ("condition_id", "market_id", "token_id", "bracket", "side"):
        row.pop(key)
    rows, _ = build_korea_comparison_ledger([_write_predictions(tmp_path / "seoul.gz", [row])])
    assert len(rows) == 5
    assert all(r["model_market_status"] == "scorable" for r in rows)
    assert all(r["three_arm_status"] != "scorable" for r in rows)


def test_future_book_invalid_label_and_horizon_never_score(tmp_path):
    source = _write_predictions(tmp_path / "busan.gz", [
        _busan(feature_book_available_at_utc="2026-08-12T02:01:00Z"),
        _busan(checkpoint_id="label", label_no=0.5),
        _busan(checkpoint_id="horizon", horizon="next_official_print"),
    ])
    rows, _ = build_korea_comparison_ledger([source])
    assert "noncausal_source_or_book_clock" in rows[0]["model_market_blockers"]
    assert "settlement_label_missing" in rows[1]["model_market_blockers"]
    assert "probability_target_or_horizon_mismatch" in rows[2]["model_market_blockers"]


def test_rule_join_requires_exact_opportunity_and_policy_identity(tmp_path):
    source = _write_predictions(tmp_path / "busan.gz", [_busan()])
    rows, _ = build_korea_comparison_ledger([source])
    rule = {**rows[0], "incumbent_rule_decision": "abstain", "policy_artifact_sha256": "a" * 64,
            "rule_evidence_id": "rule-1"}
    matched, _ = build_korea_comparison_ledger([source], rule_records=[rule])
    assert matched[0]["three_arm_status"] == "scorable"
    assert matched[0]["incumbent_rule_probability"] is None
    mismatch, _ = build_korea_comparison_ledger([source], rule_records=[{**rule, "token_id": "other"}])
    assert mismatch[0]["incumbent_rule_decision"] is None
    conflict, _ = build_korea_comparison_ledger([source], rule_records=[rule, rule])
    assert conflict[0]["incumbent_rule_status"] == "conflicting_or_incomplete_rule_evidence"


def test_market_checkpoint_uses_previous_completed_weather_state():
    import pandas as pd
    from weather_model_evaluation.korea_city_distribution import join_market_decision_panel
    source = pd.DataFrame([
        {"target_date": "2026-08-12", "checkpoint_id": "earlier", "routine_rung": 20,
         "source_available_at_utc": "2026-08-12T10:23:00Z", "decision_ts_utc": "2026-08-12T10:23:01Z",
         "collector_emitted_at_utc": "2026-08-12T10:23:01Z"},
        {"target_date": "2026-08-12", "checkpoint_id": "unfinished", "routine_rung": 20,
         "source_available_at_utc": "2026-08-12T10:24:00Z", "decision_ts_utc": "2026-08-12T10:25:01Z",
         "collector_emitted_at_utc": "2026-08-12T10:25:01Z"},
    ])
    source["source_available_at_utc"] = pd.to_datetime(source.source_available_at_utc)
    books = pd.DataFrame([{"target_date": "2026-08-12", "market_available_at_utc": pd.Timestamp("2026-08-12T10:25:00Z"), "outcomes": None}])
    result = join_market_decision_panel(source, books)
    assert len(result) == 1
    assert result.iloc[0].checkpoint_id == "earlier"
    assert result.iloc[0].decision_ts_utc == books.iloc[0].market_available_at_utc
    source["collector_emitted_at_utc"] = None
    missing = join_market_decision_panel(source, books)
    assert len(missing) == 1
    assert missing.iloc[0].market_join_status == "missing_recent_amos_state"
