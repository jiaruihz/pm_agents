from __future__ import annotations

import json

import pytest

from src.platform.execution_runtime import TradeIntent
from src.strategies.rule_lawyer.trade_intents import sync_zero_notional_trade_intents


def _write_jsonl(path, rows) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def test_selected_candidate_becomes_idempotent_zero_notional_intent(tmp_path) -> None:
    _write_jsonl(
        tmp_path / "snapshots.jsonl",
        [
            {
                "case_id": "case-1",
                "captured_at_utc": "2026-08-12T00:00:00Z",
                "condition_id": "condition-1",
                "book_captures": {
                    "no-token": {"book_capture_id": "book-1"}
                },
            }
        ],
    )
    _write_jsonl(
        tmp_path / "signals.jsonl",
        [
            {
                "candidate_id": "candidate-1",
                "case_id": "case-1",
                "market_id": "100",
                "policy_id": "policy-1",
                "opportunity_cluster_id": "cluster-1",
                "selected_token": "no-token",
                "selected_outcome": "No",
                "selected_ask_vwap": 0.4,
                "quantity": 25,
                "snapshot_ts_utc": "2026-08-12T00:00:00Z",
                "eligible_shadow": True,
                "zero_notional": True,
            }
        ],
    )
    first = sync_zero_notional_trade_intents(tmp_path)
    second = sync_zero_notional_trade_intents(tmp_path)
    assert first["new_trade_intents"] == 1
    assert second["new_trade_intents"] == 0
    row = json.loads((tmp_path / "trade_intents.jsonl").read_text())
    assert row["requested_size"] == 0
    assert row["mode"] == "zero_notional"
    assert row["execution_book_snapshot_id"] == "book-1"
    assert row["metadata"]["no_live_authority"] is True


def test_trade_intent_contract_cannot_grant_live_mode() -> None:
    with pytest.raises(ValueError, match="cannot grant live authority"):
        TradeIntent.create(
            candidate_id="candidate-1",
            strategy_key="strategy-1",
            policy_id="policy-1",
            condition_id="condition-1",
            token_id="token-1",
            venue_side="BUY",
            outcome_label="Yes",
            requested_size=1.0,
            sizing_profile="fixed",
            execution_profile="taker",
            dedupe_key="dedupe",
            exposure_bucket="bucket",
            mode="live",
            created_at_utc="2026-08-12T00:00:00Z",
        )


def test_trade_intent_uses_candidate_time_book_not_latest_case_book(tmp_path) -> None:
    _write_jsonl(
        tmp_path / "snapshots.jsonl",
        [
            {
                "case_id": "case-1",
                "captured_at_utc": "2026-08-12T00:00:00Z",
                "condition_id": "condition-1",
                "book_captures": {"no-token": {"book_capture_id": "book-at-candidate"}},
            },
            {
                "case_id": "case-1",
                "captured_at_utc": "2026-08-12T01:00:00Z",
                "condition_id": "condition-1",
                "book_captures": {"no-token": {"book_capture_id": "book-later"}},
            },
        ],
    )
    _write_jsonl(
        tmp_path / "signals.jsonl",
        [{
            "candidate_id": "candidate-1",
            "case_id": "case-1",
            "policy_id": "policy-1",
            "selected_token": "no-token",
            "selected_outcome": "No",
            "selected_ask_vwap": 0.4,
            "quantity": 25,
            "snapshot_ts_utc": "2026-08-12T00:00:00Z",
            "eligible_shadow": True,
            "zero_notional": True,
        }],
    )
    sync_zero_notional_trade_intents(tmp_path)
    row = json.loads((tmp_path / "trade_intents.jsonl").read_text())
    assert row["execution_book_snapshot_id"] == "book-at-candidate"
    assert row["metadata"]["price_lineage_status"] == "candidate_time_book"


def test_clarification_candidate_enters_same_zero_notional_intent_chain(tmp_path) -> None:
    _write_jsonl(
        tmp_path / "snapshots.jsonl",
        [{
            "case_id": "case-clarification",
            "captured_at_utc": "2026-08-12T00:00:00Z",
            "condition_id": "condition-clarification",
        }],
    )
    _write_jsonl(
        tmp_path / "clarification_signals.jsonl",
        [{
            "candidate_id": "candidate-clarification",
            "case_id": "case-clarification",
            "market_id": "101",
            "policy_id": "official_clarification_court_v1",
            "opportunity_cluster_id": "cluster-clarification",
            "selected_token": "yes-token",
            "selected_outcome": "Yes",
            "ask_vwap": 0.35,
            "quantity": 25,
            "quote_observed_at_utc": "2026-08-12T00:01:00Z",
            "execution_book_snapshot_id": "clarification-book-1",
            "eligible_shadow": True,
            "zero_notional": True,
        }],
    )
    result = sync_zero_notional_trade_intents(tmp_path)
    assert result["new_trade_intents"] == 1
    row = json.loads((tmp_path / "trade_intents.jsonl").read_text())
    assert row["max_cost"] == 0.35
    assert row["execution_book_snapshot_id"] == "clarification-book-1"
    assert row["requested_size"] == 0
