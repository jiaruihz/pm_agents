from __future__ import annotations

import json

import pytest

from src.platform.execution_runtime import PaperFill, PaperOrder, PaperPlan, execution_bundle
from src.strategies.rule_lawyer.paper_execution import sync_paper_execution


def _write(path, rows) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_zero_notional_intent_materializes_idempotent_paper_chain(tmp_path) -> None:
    _write(
        tmp_path / "signals.jsonl",
        [{
            "candidate_id": "candidate-1",
            "selected_ask_vwap": 0.4,
            "quantity": 25,
            "modeled_fee_per_share": 0.01,
        }],
    )
    _write(
        tmp_path / "trade_intents.jsonl",
        [{
            "intent_id": "intent-1",
            "candidate_id": "candidate-1",
            "mode": "zero_notional",
            "requested_size": 0,
            "token_id": "token-1",
            "venue_side": "BUY",
            "created_at_utc": "2026-08-13T00:00:00Z",
            "execution_book_snapshot_id": "book-1",
            "metadata": {"intended_observation_quantity": 25},
        }],
    )
    first = sync_paper_execution(tmp_path)
    second = sync_paper_execution(tmp_path)
    assert first["new_fills"] == 1
    assert second["new_fills"] == 0
    plan = json.loads((tmp_path / "paper_plans.jsonl").read_text())
    order = json.loads((tmp_path / "paper_orders.jsonl").read_text())
    fill = json.loads((tmp_path / "paper_fills.jsonl").read_text())
    assert order["plan_id"] == plan["plan_id"]
    assert fill["order_id"] == order["order_id"]
    assert fill["intent_id"] == "intent-1"
    assert fill["actual_cost"] == fill["actual_shares"] == 0
    assert fill["hypothetical_shares"] == 25


def test_paper_contracts_reject_real_execution_values() -> None:
    plan = PaperPlan.create(
        intent_id="intent-1",
        candidate_id="candidate-1",
        token_id="token-1",
        side="BUY",
        hypothetical_shares=25,
        limit_price=0.4,
        book_snapshot_id="book-1",
        created_at_utc="2026-08-13T00:00:00Z",
    )
    with pytest.raises(ValueError, match="zero actual notional"):
        PaperOrder(**{**PaperOrder.from_plan(plan).to_dict(), "actual_notional": 1})
    order = PaperOrder.from_plan(plan)
    with pytest.raises(ValueError, match="must not contain real execution"):
        PaperFill(**{
            **PaperFill.from_plan(plan, order, hypothetical_fee_per_share=0).to_dict(),
            "actual_cost": 1,
        })


def test_partial_paper_stream_recovery_does_not_duplicate_plan_or_order(tmp_path) -> None:
    candidate = {
        "candidate_id": "candidate-1",
        "selected_ask_vwap": 0.4,
        "quantity": 25,
    }
    intent = {
        "intent_id": "intent-1",
        "candidate_id": "candidate-1",
        "mode": "zero_notional",
        "requested_size": 0,
        "token_id": "token-1",
        "venue_side": "BUY",
        "created_at_utc": "2026-08-13T00:00:00Z",
        "metadata": {"intended_observation_quantity": 25},
    }
    plan, order, _ = execution_bundle(intent=intent, candidate=candidate)
    _write(tmp_path / "signals.jsonl", [candidate])
    _write(tmp_path / "trade_intents.jsonl", [intent])
    _write(tmp_path / "paper_plans.jsonl", [plan.to_dict()])
    _write(tmp_path / "paper_orders.jsonl", [order.to_dict()])
    result = sync_paper_execution(tmp_path)
    assert result["new_plans"] == result["new_orders"] == 0
    assert result["new_fills"] == 1
    assert len((tmp_path / "paper_plans.jsonl").read_text().splitlines()) == 1
    assert len((tmp_path / "paper_orders.jsonl").read_text().splitlines()) == 1


def test_clarification_candidate_uses_common_paper_execution_boundary(tmp_path) -> None:
    _write(
        tmp_path / "clarification_signals.jsonl",
        [{
            "candidate_id": "clarification-1",
            "ask_vwap": 0.3,
            "quantity": 25,
            "modeled_fee_per_share": 0.01,
        }],
    )
    _write(
        tmp_path / "trade_intents.jsonl",
        [{
            "intent_id": "intent-clarification-1",
            "candidate_id": "clarification-1",
            "mode": "zero_notional",
            "requested_size": 0,
            "token_id": "yes-token",
            "venue_side": "BUY",
            "created_at_utc": "2026-08-13T00:00:00Z",
            "execution_book_snapshot_id": "clarification-book-1",
            "metadata": {"intended_observation_quantity": 25},
        }],
    )
    result = sync_paper_execution(tmp_path)
    assert result["new_fills"] == 1
    fill = json.loads((tmp_path / "paper_fills.jsonl").read_text())
    assert fill["fill_price"] == 0.3
    assert fill["book_snapshot_id"] == "clarification-book-1"
    assert fill["actual_cost"] == 0
