from __future__ import annotations

import inspect
import json
import sqlite3
from dataclasses import replace
from decimal import Decimal
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from scripts.ops import weather_current_yes_core_carry_tiny_live_v2 as runner
from scripts.ops.weather_order_executor import (
    _current_yes_residual_maker_price,
    _current_yes_residual_taker_quote,
)
from src.strategies.runtime.sync import sync_instance_specs
from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical


def score_row() -> dict:
    return {
        "city": "Busan",
        "target_date": "2026-07-24",
        "token_id": "yes-token",
        "condition_id": "condition",
        "current_bracket": "30",
        "current_yes_bid": 0.80,
        "current_yes_ask": 0.84,
        "current_yes_tick_size": 0.01,
        "model_probability_hold": 0.91,
        "checkpoint_key": "Busan|2026-07-24|13",
        "decision_snapshot_ts_utc": "2026-07-24T04:30:00Z",
        "source_report_ts_utc": "2026-07-24T04:20:00Z",
        "artifact_hash": "hash",
    }


def test_entry_is_exactly_five_taker_plus_five_maker() -> None:
    plans = runner.build_entry_plans(
        score_row(),
        live_enabled=True,
        now=datetime(2026, 7, 24, 4, 31, tzinfo=timezone.utc),
        taker_shares=5,
        maker_shares=5,
        order_ttl_min=15,
    )
    assert [(row["child_order_role"], row["size"]) for row in plans] == [
        ("taker", 5.0),
        ("maker", 5.0),
    ]
    assert plans[0]["execution_policy"] == "current_yes_residual_carry_taker_v1"
    assert plans[1]["execution_policy"] == "current_yes_residual_carry_maker_v1"
    assert plans[1]["limit_price"] == pytest.approx(0.81)
    assert plans[1]["maker_price_cap"] == pytest.approx(0.82)
    assert plans[1]["model_token_probability"] == pytest.approx(0.91)


def test_shared_core_carry_comparator_has_no_split_entry_differences() -> None:
    legacy = runner.build_entry_plans(
        score_row(),
        live_enabled=False,
        now=datetime(2026, 7, 24, 4, 31, tzinfo=timezone.utc),
        taker_shares=5,
        maker_shares=5,
        order_ttl_min=15,
    )

    shared = runner.build_shared_core_carry_plan_parity(legacy)

    assert runner.compare_shared_core_carry_plan_parity(legacy, shared) == ()
    assert [child.child_role for child in shared.children] == ["taker", "maker"]
    assert [float(child.requested_shares) for child in shared.children] == [5.0, 5.0]
    assert shared.intents[0].comparison_group_id == shared.intents[1].comparison_group_id
    assert shared.intents[0].live_exposure_key == shared.intents[1].live_exposure_key
    assert shared.intents[1].execution_profile == legacy[1]["execution_profile"]
    assert shared.intents[1].metadata["legacy_profile_resolution"] == "opaque_unregistered_legacy_profile_v1"


def test_shared_core_carry_comparator_supports_taker_only_entry() -> None:
    legacy_all = runner.build_entry_plans(
        score_row(),
        live_enabled=False,
        now=datetime(2026, 7, 24, 4, 31, tzinfo=timezone.utc),
        taker_shares=5,
        maker_shares=0,
        order_ttl_min=15,
    )
    legacy = [plan for plan in legacy_all if float(plan["size"]) > 0]

    shared = runner.build_shared_core_carry_plan_parity(legacy)

    assert runner.compare_shared_core_carry_plan_parity(legacy, shared) == ()
    assert len(shared.children) == 1
    assert shared.children[0].child_role == "taker"
    assert shared.children[0].maker_only is False


def test_shared_core_carry_comparator_reports_deliberate_field_mismatch() -> None:
    legacy = runner.build_entry_plans(
        score_row(),
        live_enabled=False,
        now=datetime(2026, 7, 24, 4, 31, tzinfo=timezone.utc),
        taker_shares=5,
        maker_shares=5,
        order_ttl_min=15,
    )
    shared = runner.build_shared_core_carry_plan_parity(legacy)
    changed_taker = replace(shared.children[0], requested_shares=Decimal("6"))
    mismatched = replace(shared, children=(changed_taker, *shared.children[1:]))

    differences = runner.compare_shared_core_carry_plan_parity(legacy, mismatched)

    assert [(difference.plan_index, difference.field) for difference in differences] == [(0, "shares")]
    assert differences[0].legacy_value == "5"
    assert differences[0].standardized_value == "6"


def test_shared_core_carry_entry_bridge_rejects_lifecycle_plan() -> None:
    entry = runner.build_entry_plans(
        score_row(),
        live_enabled=False,
        now=datetime(2026, 7, 24, 4, 31, tzinfo=timezone.utc),
        taker_shares=5,
        maker_shares=5,
        order_ttl_min=15,
    )[1]
    lifecycle = runner.build_maker_lifecycle_plan(
        entry,
        action="core_carry_maker_reprice",
        limit_price=0.82,
        cancel_only=False,
        now=datetime(2026, 7, 24, 4, 32, tzinfo=timezone.utc),
        live_enabled=False,
    )

    with pytest.raises(ValueError, match="rejects lifecycle replacement/cancel plans"):
        runner.build_shared_core_carry_plan_parity([lifecycle])


def test_shared_core_carry_comparator_is_not_called_by_normal_run() -> None:
    assert "build_shared_core_carry_plan_parity" not in inspect.getsource(runner.run_once)
    assert "compare_shared_core_carry_plan_parity" not in inspect.getsource(runner.run_once)


def test_model_probability_can_be_the_lower_maker_cap() -> None:
    row = {**score_row(), "model_probability_hold": 0.815}
    plans = runner.build_entry_plans(
        row,
        live_enabled=False,
        now=datetime(2026, 7, 24, 4, 31, tzinfo=timezone.utc),
        taker_shares=5,
        maker_shares=5,
        order_ttl_min=15,
    )
    maker = next(plan for plan in plans if plan["child_order_role"] == "maker")
    assert maker["maker_price_cap"] == pytest.approx(0.815)
    assert maker["limit_price"] == pytest.approx(0.81)


def test_signal_id_is_stable_per_city_day() -> None:
    original = runner.signal_id(score_row())
    changed_book = runner.signal_id({**score_row(), "current_yes_ask": 0.90})
    assert original == changed_book


def test_frozen_split_rejects_other_size(tmp_path, monkeypatch) -> None:
    args = runner.parser().parse_args(
        [
            "run",
            "--output-dir",
            str(tmp_path),
            "--taker-shares",
            "5",
            "--maker-shares",
            "10",
        ]
    )
    with pytest.raises(RuntimeError, match="exactly 5 taker"):
        runner.run_once(args)


def test_publish_runtime_state_exposes_live_heartbeat(tmp_path) -> None:
    db_path = tmp_path / "weather.db"
    with sqlite3.connect(db_path) as conn:
        apply_schema_canonical(conn)
        sync_instance_specs(conn)

    output_dir = tmp_path / "runtime/weather_edge_v1/current_yes_core_carry_tiny_live_v2"
    output_dir.mkdir(parents=True)
    (output_dir / "paper_orders.jsonl").write_text("", encoding="utf-8")
    args = runner.parser().parse_args(["run", "--runtime-db", str(db_path)])
    summary = {
        "status": "ok",
        "generated_at_utc": "2026-07-24T08:45:00+00:00",
        "live_enabled": True,
        "entry_plans": 0,
        "maker_lifecycle_plans": 0,
    }
    runner.publish_runtime_state(
        args,
        output_dir,
        summary,
        {"generated_at_utc": "2026-07-24T08:44:58Z", "checkpoint_candidates": 11},
    )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT process_status, health_status, live_enabled, candidate_rows,
                   plan_rows, live_order_rows, summary_path
            FROM strategy_instance_runtime
            WHERE instance_id=?
            """,
            (runner.STRATEGY_INSTANCE,),
        ).fetchone()
    assert row == (
        "running",
        "healthy",
        1,
        11,
        0,
        0,
        str(output_dir / "latest_summary.json"),
    )


def test_publish_runtime_state_maps_executor_error_to_blocked(tmp_path) -> None:
    db_path = tmp_path / "weather.db"
    with sqlite3.connect(db_path) as conn:
        apply_schema_canonical(conn)
        sync_instance_specs(conn)
    output_dir = tmp_path / "runtime/weather_edge_v1/current_yes_core_carry_tiny_live_v2"
    output_dir.mkdir(parents=True)
    args = runner.parser().parse_args(["run", "--runtime-db", str(db_path)])

    runner.publish_runtime_state(
        args,
        output_dir,
        {
            "status": "executor_error",
            "generated_at_utc": "2026-07-24T08:45:00+00:00",
            "live_enabled": True,
        },
        {},
    )

    with sqlite3.connect(db_path) as conn:
        value = conn.execute(
            "SELECT health_status FROM strategy_instance_runtime WHERE instance_id=?",
            (runner.STRATEGY_INSTANCE,),
        ).fetchone()[0]
    assert value == "blocked"


def test_runtime_state_db_busy_is_deferred_without_failing_trading_loop(
    tmp_path,
    monkeypatch,
) -> None:
    args = runner.parser().parse_args(["run"])
    summary = {"status": "ok"}

    def locked(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(runner, "publish_runtime_state", locked)
    runner.publish_runtime_state_best_effort(args, tmp_path, summary, {})

    assert summary["status"] == "ok"
    assert summary["runtime_state_publish_status"] == "deferred_db_busy"
    assert "database is locked" in summary["runtime_state_publish_error"]
    assert "runtime_state_error" not in summary


def test_executor_rechecks_full_taker_ladder_and_fee() -> None:
    quote = _current_yes_residual_taker_quote(
        {
            "size": 5,
            "model_token_probability": 0.93,
            "required_quote_edge": 0.0,
        },
        {
            "asks": [
                {"price": "0.90", "size": "2"},
                {"price": "0.91", "size": "3"},
            ]
        },
        best_bid=0.89,
        best_ask=0.90,
        tick_size=0.01,
    )
    assert quote["accepted"] is True
    assert quote["order_price"] == pytest.approx(0.91)
    assert quote["effective_cost_per_share"] > quote["principal_vwap"]


def test_executor_maker_never_crosses_or_exceeds_cap() -> None:
    assert _current_yes_residual_maker_price(
        best_bid=0.80,
        best_ask=0.84,
        tick_size=0.01,
        price_cap=0.82,
    ) == pytest.approx(0.81)
    assert _current_yes_residual_maker_price(
        best_bid=0.82,
        best_ask=0.84,
        tick_size=0.01,
        price_cap=0.82,
    ) == 0.0
def test_loop_error_refreshes_latest_health_artifact(tmp_path, monkeypatch):
    args = SimpleNamespace(
        output_dir=str(tmp_path),
        runtime_db=str(tmp_path / "weather.db"),
        live=True,
        confirm_live=True,
    )
    monkeypatch.setattr(runner, "publish_runtime_state_best_effort", lambda *a, **k: None)

    row = runner.publish_loop_error(args, TimeoutError("CLOB handshake timed out"))

    latest = json.loads((tmp_path / "latest_summary.json").read_text(encoding="utf-8"))
    assert row["status"] == "error"
    assert latest["status"] == "error"
    assert latest["live_enabled"] is True
    assert "CLOB handshake timed out" in latest["error"]
