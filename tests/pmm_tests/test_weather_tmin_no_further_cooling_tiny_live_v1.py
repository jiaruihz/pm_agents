from datetime import datetime, timezone
import json

import pytest

from scripts.ops import weather_tmin_no_further_cooling_tiny_live_v1 as runner
from src.strategies.weather_edge_v1.tools.execution_pipeline import (
    ExecutorConfig,
    build_live_order_record,
    execute_trade_plans,
)


NOW = datetime(2026, 9, 4, 0, 0, tzinfo=timezone.utc)


def selected_row(**updates):
    row = {
        "strategy_key": runner.STRATEGY_KEY,
        "policy_id": runner.SOURCE_POLICY_ID,
        "model_id": runner.MODEL_ID,
        "model_artifact_id": runner.MODEL_ARTIFACT_ID,
        "side": "YES",
        "candidate_status": "scored",
        "selected": True,
        "blocker_reason": None,
        "market_evidence_status": "available",
        "candidate_id": "candidate-1",
        "city": "Tokyo",
        "target_date": "2026-09-04",
        "condition_id": "condition",
        "market_id": "market",
        "token_id": "token",
        "bracket": "22",
        "p_model": 0.88,
        "executable_cost": 0.80,
        "decision_ts_utc": "2026-09-04T00:00:00Z",
        "execution_book_snapshot_id": "snapshot",
        "metadata": {"first_positive_edge_once_per_city_date": True},
    }
    row.update(updates)
    return row


def test_new_state_seals_at_existing_source_eof(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps(selected_row()) + "\n", encoding="utf-8")
    state = runner.load_state(tmp_path / "state.json", source)
    assert state["source_cursor_bytes"] == source.stat().st_size


def test_partial_jsonl_row_is_not_consumed(tmp_path):
    source = tmp_path / "source.jsonl"
    first = json.dumps(selected_row()) + "\n"
    source.write_text(first + '{"partial":', encoding="utf-8")
    rows, cursor = runner.read_complete_lines(source, 0)
    assert len(rows) == 1
    assert cursor == len(first.encode())


def test_exact_frozen_selected_contract_builds_five_share_plan():
    plans, audits = runner.choose_plans(
        [selected_row()],
        now=NOW,
        max_age_sec=180,
        ttl_min=15,
        daily_cost_cap=35,
        live_enabled=True,
        prior_signal_ids=set(),
    )
    assert audits[0]["status"] == "planned"
    assert plans[0]["size"] == 5.0
    assert plans[0]["execution_policy"] == "current_yes_residual_carry_taker_v1"
    assert plans[0]["model_token_probability"] == 0.88
    assert plans[0]["live_enabled"] is True


def test_historical_or_duplicate_candidate_cannot_order():
    old = selected_row(decision_ts_utc="2026-09-03T23:50:00Z")
    plans, audits = runner.choose_plans(
        [old, selected_row()],
        now=NOW,
        max_age_sec=180,
        ttl_min=15,
        daily_cost_cap=35,
        live_enabled=True,
        prior_signal_ids={runner.signal_id_for_candidate("candidate-1")},
    )
    assert plans == []
    assert [row["reason"] for row in audits] == [
        "candidate_age_outside_live_window",
        "already_in_live_journal",
    ]


def test_wrong_artifact_and_nonselected_are_ignored():
    plans, audits = runner.choose_plans(
        [selected_row(model_artifact_id="wrong"), selected_row(candidate_id="two", selected=False)],
        now=NOW,
        max_age_sec=180,
        ttl_min=15,
        daily_cost_cap=35,
        live_enabled=True,
        prior_signal_ids=set(),
    )
    assert plans == []
    assert [row["reason"] for row in audits] == [
        "source_contract_mismatch:model_artifact_id",
        "not_selected_by_frozen_v1",
    ]


def test_daily_cost_cap_is_fail_closed():
    plans, audits = runner.choose_plans(
        [selected_row(executable_cost=0.80)],
        now=NOW,
        max_age_sec=180,
        ttl_min=15,
        daily_cost_cap=3.99,
        live_enabled=True,
        prior_signal_ids=set(),
    )
    assert plans == []
    assert audits[0]["reason"] == "daily_cost_cap"


def test_cost_cap_is_cumulative_by_target_date():
    plans, audits = runner.choose_plans(
        [selected_row(p_model=0.88, executable_cost=0.20)],
        now=NOW,
        max_age_sec=180,
        ttl_min=15,
        daily_cost_cap=35,
        live_enabled=True,
        prior_signal_ids=set(),
        committed_costs={"2026-09-04": 31.0},
    )
    assert plans == []
    assert audits[0]["reason"] == "daily_cost_cap"


def test_committed_cost_uses_submitted_model_cap(tmp_path):
    journal = tmp_path / "live.jsonl"
    journal.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "status": "submitted",
                        "target_date": "2026-09-04",
                        "order_notional_cap": 4.4,
                    }
                ),
                json.dumps(
                    {
                        "status": "error",
                        "target_date": "2026-09-04",
                        "order_notional_cap": 4.9,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert runner.committed_cost_by_target_date(journal) == {"2026-09-04": 4.4}


def test_trade_plan_lineage_is_append_only_and_deduplicated(tmp_path):
    path = tmp_path / "trade_plans.jsonl"
    one = {"plan_id": "one", "source_candidate_id": "candidate-1"}
    two = {"plan_id": "two", "source_candidate_id": "candidate-2"}
    assert runner.append_new_plans(path, [one]) == 1
    assert runner.append_new_plans(path, [one, two]) == 1
    assert [row["plan_id"] for row in runner.order_runtime.read_jsonl(path)] == [
        "one",
        "two",
    ]


def test_exclusive_run_lock_rejects_overlapping_consumer(tmp_path):
    path = tmp_path / "run.lock"
    with runner.exclusive_run_lock(path):
        with pytest.raises(RuntimeError, match="holds the execution lock"):
            with runner.exclusive_run_lock(path):
                pass


def test_live_record_preserves_source_lineage():
    plan = runner.build_plan(
        selected_row(), now=NOW, ttl_min=15, live_enabled=True
    )
    record = build_live_order_record(
        plan,
        {"posted_price": 0.81, "quote_status": "accepted"},
        status="submitted",
    )
    assert record["source_candidate_id"] == "candidate-1"
    assert record["source_policy_id"] == runner.SOURCE_POLICY_ID
    assert record["model_artifact_id"] == runner.MODEL_ARTIFACT_ID


def test_uncertain_executor_error_is_journaled_and_never_retried(tmp_path):
    plan = runner.build_plan(
        selected_row(), now=NOW, ttl_min=15, live_enabled=True
    )
    plan_path = tmp_path / "plans.jsonl"
    runner.order_runtime.write_jsonl(plan_path, [plan])
    live_out = tmp_path / "live.jsonl"

    def fail_after_uncertain_submit(_plan):
        raise RuntimeError("connection dropped after exchange request")

    result = execute_trade_plans(
        plan_path=plan_path,
        paper_out=tmp_path / "paper.jsonl",
        live_out=live_out,
        config=ExecutorConfig(live=True, confirm_live=True),
        live_place_fn=fail_after_uncertain_submit,
    )
    rows = runner.order_runtime.read_jsonl(live_out)
    assert result["live_errors"] == 1
    assert rows[0]["status"] == "error"
    assert rows[0]["source_candidate_id"] == "candidate-1"
    assert runner.prior_live_signal_ids(live_out) == {plan["signal_id"]}
