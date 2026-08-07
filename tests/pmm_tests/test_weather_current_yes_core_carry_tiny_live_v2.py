from __future__ import annotations

import sqlite3
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

import pytest

from scripts.ops import weather_current_yes_core_carry_tiny_live_v2 as runner
from scripts.ops.weather_order_executor import (
    _current_yes_residual_maker_price,
    _current_yes_residual_taker_quote,
)
from src.strategies.runtime.sync import sync_instance_specs
from src.strategies.weather_edge_v1.tools.execution_pipeline import build_live_order_record
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
        "observation_cadence_min": 30.0,
        "artifact_hash": "hash",
    }


def test_runtime_contract_proves_pit_clock_and_clean_deployment() -> None:
    runner.assert_runtime_contract()
    assert runner.DEPLOYMENT_METADATA["deployed_repo_sha"]
    assert runner.DEPLOYMENT_METADATA["critical_source_dirty"] is False
    assert (
        runner.DEPLOYMENT_METADATA["deployment_contract_version"]
        == "core_carry_v3_shared_order_runtime_10t5m_edge_cap_v2"
    )


def test_live_runner_uses_no_peak_clock_v3_artifact() -> None:
    artifact = runner.load_artifact(runner.ARTIFACT_PATH)

    assert artifact["artifact_version"] == "current_yes_core_carry_model_v3_no_peak_clock"
    assert "forecast_peak_delta_hours_local" not in artifact["numeric_features"]
    assert artifact["entry_policy"]["forecast_peak_clock_probability_feature"] is False
    assert artifact["entry_policy"]["taker_shares"] == 10
    assert artifact["entry_policy"]["cost_input"] == (
        "full ten-share ask-ladder principal plus official per-level fee"
    )


def test_chengdu_midnight_peak_alias_no_longer_creates_positive_ev() -> None:
    artifact = runner.load_artifact(runner.ARTIFACT_PATH)
    state = {
        **score_row(),
        "city": "Chengdu",
        "target_date": "2026-07-27",
        "current_bracket": "29",
        "current_yes_bid": 0.91,
        "current_yes_ask": 0.92,
        "decision_hour_local": 17,
        "forecast_peak_delta_hours_local": 17.5,
        "forecast_effective_peak_delta_hours_local": 0.5,
        "dewpoint_depression_f": 10.8,
        "wind_speed_kt": 6.0,
        "checkpoint_eligible": True,
    }
    result = runner.evaluate_entry(
        state,
        [{"price": 0.92, "size": 3.62}, {"price": 0.93, "size": 20}],
        artifact,
    )

    assert result["model_probability_hold"] == pytest.approx(0.9146531794536291)
    assert result["taker_ladder"]["effective_cost_per_share"] > 0.92
    assert result["eligible"] is False
    assert result["reasons"] == ["non_positive_taker_ev"]


def test_entry_is_exactly_ten_taker_plus_five_maker() -> None:
    plans = runner.build_entry_plans(
        score_row(),
        live_enabled=True,
        now=datetime(2026, 7, 24, 4, 31, tzinfo=timezone.utc),
        taker_shares=10,
        maker_shares=5,
        order_ttl_min=15,
    )
    assert [(row["child_order_role"], row["size"]) for row in plans] == [
        ("taker", 10.0),
        ("maker", 5.0),
    ]
    assert plans[0]["execution_policy"] == "current_yes_residual_carry_taker_v1"
    assert plans[1]["execution_policy"] == "current_yes_residual_carry_maker_v2"
    assert plans[1]["limit_price"] == pytest.approx(0.81)
    assert plans[1]["maker_price_cap"] == pytest.approx(0.83)
    assert plans[1]["model_token_probability"] == pytest.approx(0.91)
    assert plans[1]["cancel_before_data_update_utc"] == "2026-07-24T04:48:30+00:00"
    assert plans[1]["expires_at_utc"] == "2026-07-24T04:46:00+00:00"
    assert plans[1]["cancel_buffer_sec"] == 90
    assert all(
        plan["resolved_execution_profile"]
        == "split_taker_maker_edge_capped_no_fallback_v2"
        for plan in plans
    )
    assert plans[0]["execution_config_id"] == plans[1]["execution_config_id"]
    assert plans[0]["live_exposure_key"] == plans[1]["live_exposure_key"]
    assert plans[0]["plan_dedupe_key"] != plans[1]["plan_dedupe_key"]


def test_live_parser_defaults_match_frozen_ten_plus_five_contract() -> None:
    args = runner.parser().parse_args(["run"])

    assert args.taker_shares == runner.FROZEN_TAKER_SHARES == 10
    assert args.maker_shares == runner.FROZEN_MAKER_SHARES == 5
    assert args.summary_filename == "signal_latest_summary.json"
    assert args.summary_history_filename == "signal_summary_history.jsonl"
    assert runner.CONFIG_ID.endswith("split_10_taker_5_maker_edge_cap_v2")


def test_market_above_frozen_training_support_is_not_eligible() -> None:
    artifact = runner.load_artifact(runner.ARTIFACT_PATH)
    result = runner.evaluate_entry(
        {
            **score_row(),
            "current_yes_bid": 0.999,
            "current_yes_ask": 0.999,
            "decision_hour_local": 15,
            "dewpoint_depression_f": 18,
            "wind_speed_kt": 9,
            "checkpoint_eligible": True,
        },
        [{"price": 0.999, "size": 10}],
        artifact,
    )

    assert result["eligible"] is False
    assert "outside_frozen_market_mid_support" in result["reasons"]


def test_missing_live_weather_input_is_explicit_and_not_eligible() -> None:
    artifact = runner.load_artifact(runner.ARTIFACT_PATH)
    result = runner.evaluate_entry(
        {
            **score_row(),
            "decision_hour_local": 15,
            "dewpoint_depression_f": None,
            "wind_speed_kt": 9,
            "checkpoint_eligible": True,
        },
        [{"price": 0.84, "size": 10}],
        artifact,
    )

    assert result["model_probability_hold"] is not None
    assert result["model_input_support_status"] == "missing_model_inputs"
    assert result["model_input_support_missing_features"] == ["dewpoint_depression_f"]
    assert result["eligible"] is False
    assert "missing_required_model_feature:dewpoint_depression_f" in result["reasons"]


def test_outside_frozen_training_support_is_visible_and_not_traded() -> None:
    artifact = runner.load_artifact(runner.ARTIFACT_PATH)
    result = runner.evaluate_entry(
        {
            **score_row(),
            "decision_hour_local": 15,
            "dewpoint_depression_f": 18,
            "wind_speed_kt": 50,
            "checkpoint_eligible": True,
        },
        [{"price": 0.84, "size": 10}],
        artifact,
    )

    assert result["model_input_support_status"] == "outside_training_support"
    assert result["model_input_outside_training_support_features"] == ["wind_speed_kt"]
    assert result["eligible"] is False
    assert "outside_frozen_model_support:wind_speed_kt" in result["reasons"]
    assert result["probability_status"] == "scored_by_current_yes_core_artifact"
    assert result["current_yes_effective_cost"] == result["taker_ladder"][
        "effective_cost_per_share"
    ]


def test_retained_model_edge_can_be_the_lower_maker_cap() -> None:
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
    assert maker["maker_price_cap"] == pytest.approx(0.80)
    assert maker["limit_price"] == pytest.approx(0.80)


def test_one_tick_spread_joins_best_bid_instead_of_dropping_maker() -> None:
    row = {
        **score_row(),
        "current_yes_bid": 0.93,
        "current_yes_ask": 0.94,
        "model_probability_hold": 0.98,
    }
    plans = runner.build_entry_plans(
        row,
        live_enabled=True,
        now=datetime(2026, 7, 24, 4, 31, tzinfo=timezone.utc),
        taker_shares=5,
        maker_shares=5,
        order_ttl_min=15,
    )
    maker = next(plan for plan in plans if plan["child_order_role"] == "maker")
    assert maker["limit_price"] == pytest.approx(0.93)
    assert maker["maker_price_cap"] == pytest.approx(0.93)


def test_live_order_preserves_observation_and_snapshot_lineage() -> None:
    maker = runner.build_entry_plans(
        {**score_row(), "snapshot_file": "snapshot_20260724_1230.json"},
        live_enabled=True,
        now=datetime(2026, 7, 24, 4, 31, tzinfo=timezone.utc),
        taker_shares=5,
        maker_shares=5,
        order_ttl_min=15,
    )[1]
    order = build_live_order_record(
        maker,
        {
            "posted_price": 0.81,
            "maker_only": True,
            "place": {"orderID": "maker-order-1", "status": "live"},
        },
        status="submitted",
    )
    assert order["source_report_ts_utc"] == "2026-07-24T04:20:00Z"
    assert order["source_snapshot_file"] == "snapshot_20260724_1230.json"
    assert order["source_snapshot_path"] == "snapshot_20260724_1230.json"


def _live_maker_order(*, created: datetime, source_epoch: str = "2026-07-24T04:20:00Z") -> dict:
    maker = runner.build_entry_plans(
        score_row(),
        live_enabled=True,
        now=created,
        taker_shares=5,
        maker_shares=5,
        order_ttl_min=15,
    )[1]
    return build_live_order_record(
        maker,
        {
            "posted_price": 0.81,
            "maker_only": True,
            "place": {"orderID": "maker-order-1", "status": "live"},
        },
        status="submitted",
    ) | {
        "created_at_utc": created.isoformat(timespec="seconds"),
        "source_report_ts_utc": source_epoch,
    }


def _lifecycle_args(tmp_path) -> object:
    return runner.parser().parse_args(
        ["run", "--output-dir", str(tmp_path), "--live", "--confirm-live"]
    )


def _write_lifecycle_state(
    tmp_path,
    *,
    source_epoch: str,
    token_id: str = "yes-token",
) -> None:
    runner.write_jsonl(
        tmp_path / "state_decisions.jsonl",
        [
            {
                "city": "Busan",
                "target_date": "2026-07-24",
                "decision_snapshot_ts_utc": "2026-07-24T04:31:00Z",
                "source_report_ts_utc": source_epoch,
                "current_yes_token_id": token_id,
                "current_bracket": "30",
                "current_question": "Will the highest temperature be 30°C?",
                "obs_status": "ok",
                "station_gap_state": "within_expected_cadence",
                "obs_age_min": 1.0,
                "observation_cadence_min": 30.0,
            }
        ],
    )


def test_same_observation_keeps_queue_when_own_order_is_best_bid(
    tmp_path,
    monkeypatch,
) -> None:
    now = datetime(2026, 7, 24, 4, 32, tzinfo=timezone.utc)
    order = _live_maker_order(created=now - timedelta(minutes=1))
    runner.write_jsonl(tmp_path / "live_orders.jsonl", [order])
    _write_lifecycle_state(tmp_path, source_epoch="2026-07-24T04:20:00Z")
    monkeypatch.setattr(runner, "market_httpx_client", lambda *_args, **_kwargs: nullcontext(object()))
    monkeypatch.setattr(
        runner.weather_state,
        "_fetch_token_book",
        lambda *_args, **_kwargs: {
            "book_status": "ok",
            "bid": 0.81,
            "ask": 0.84,
            "tick_size": 0.01,
        },
    )

    plans, decisions = runner.maker_lifecycle_plans(
        _lifecycle_args(tmp_path),
        tmp_path,
        now=now,
    )

    assert plans == []
    assert decisions[0]["action"] == ""
    assert decisions[0]["blocker"] == "own_or_same_level_best_bid_keep_queue"


def test_same_observation_reprices_only_after_external_bid_moves_above_order(
    tmp_path,
    monkeypatch,
) -> None:
    now = datetime(2026, 7, 24, 4, 32, tzinfo=timezone.utc)
    order = _live_maker_order(created=now - timedelta(minutes=1))
    runner.write_jsonl(tmp_path / "live_orders.jsonl", [order])
    _write_lifecycle_state(tmp_path, source_epoch="2026-07-24T04:20:00Z")
    monkeypatch.setattr(runner, "market_httpx_client", lambda *_args, **_kwargs: nullcontext(object()))
    monkeypatch.setattr(
        runner.weather_state,
        "_fetch_token_book",
        lambda *_args, **_kwargs: {
            "book_status": "ok",
            "bid": 0.82,
            "ask": 0.85,
            "tick_size": 0.01,
        },
    )

    plans, decisions = runner.maker_lifecycle_plans(
        _lifecycle_args(tmp_path),
        tmp_path,
        now=now,
    )

    assert decisions[0]["action"] == "core_carry_maker_reprice"
    assert plans[0]["cancel_before_order_id"] == "maker-order-1"
    assert plans[0]["replacement_requires_order_state"] is True
    assert plans[0]["limit_price"] == pytest.approx(0.83)
    assert plans[0]["source_report_ts_utc"] == "2026-07-24T04:20:00Z"


def test_true_new_observation_cancels_even_when_same_bracket(
    tmp_path,
    monkeypatch,
) -> None:
    now = datetime(2026, 7, 24, 4, 32, tzinfo=timezone.utc)
    order = _live_maker_order(created=now - timedelta(minutes=1))
    runner.write_jsonl(tmp_path / "live_orders.jsonl", [order])
    _write_lifecycle_state(tmp_path, source_epoch="2026-07-24T04:30:00Z")
    monkeypatch.setattr(runner, "market_httpx_client", lambda *_args, **_kwargs: nullcontext(object()))
    plans, decisions = runner.maker_lifecycle_plans(
        _lifecycle_args(tmp_path),
        tmp_path,
        now=now,
    )

    assert decisions[0]["action"] == "core_carry_maker_cancel_new_observation"
    assert decisions[0]["blocker"] == "new_observation_requires_fresh_entry"
    assert plans[0]["cancel_only"] is True
    assert plans[0]["cancel_before_order_id"] == "maker-order-1"
    assert plans[0]["replacement_requires_order_state"] is False
    assert plans[0]["source_report_ts_utc"] == "2026-07-24T04:20:00Z"


def test_true_new_observation_cancels_when_current_bracket_changed(tmp_path) -> None:
    now = datetime(2026, 7, 24, 4, 32, tzinfo=timezone.utc)
    order = _live_maker_order(created=now - timedelta(minutes=1))
    runner.write_jsonl(tmp_path / "live_orders.jsonl", [order])
    _write_lifecycle_state(
        tmp_path,
        source_epoch="2026-07-24T04:30:00Z",
        token_id="new-bracket-token",
    )

    plans, decisions = runner.maker_lifecycle_plans(
        _lifecycle_args(tmp_path),
        tmp_path,
        now=now,
    )

    assert decisions[0]["action"] == "core_carry_maker_cancel_new_observation"
    assert decisions[0]["blocker"] == "new_observation_requires_fresh_entry"
    assert plans[0]["cancel_only"] is True
    assert plans[0]["cancel_before_order_id"] == "maker-order-1"


def test_true_new_observation_cancels_when_freshness_is_invalid(tmp_path) -> None:
    now = datetime(2026, 7, 24, 4, 32, tzinfo=timezone.utc)
    order = _live_maker_order(created=now - timedelta(minutes=1))
    runner.write_jsonl(tmp_path / "live_orders.jsonl", [order])
    runner.write_jsonl(
        tmp_path / "state_decisions.jsonl",
        [
            {
                "city": "Busan",
                "target_date": "2026-07-24",
                "decision_snapshot_ts_utc": "2026-07-24T04:31:00Z",
                "source_report_ts_utc": "2026-07-24T04:30:00Z",
                "current_yes_token_id": "yes-token",
                "obs_status": "ok",
                "station_gap_state": "beyond_expected_cadence",
                "obs_age_min": 121.0,
                "observation_cadence_min": 30.0,
            }
        ],
    )

    plans, decisions = runner.maker_lifecycle_plans(
        _lifecycle_args(tmp_path),
        tmp_path,
        now=now,
    )

    assert decisions[0]["action"] == "core_carry_maker_cancel_new_observation"
    assert decisions[0]["blocker"] == "new_observation_requires_fresh_entry"
    assert plans[0]["cancel_only"] is True


def test_maker_reprices_to_midpoint_after_five_minutes(
    tmp_path,
    monkeypatch,
) -> None:
    now = datetime(2026, 7, 24, 4, 38, tzinfo=timezone.utc)
    order = _live_maker_order(created=now - timedelta(minutes=6))
    runner.write_jsonl(tmp_path / "live_orders.jsonl", [order])
    _write_lifecycle_state(tmp_path, source_epoch="2026-07-24T04:20:00Z")
    monkeypatch.setattr(
        runner,
        "market_httpx_client",
        lambda *_args, **_kwargs: nullcontext(object()),
    )
    monkeypatch.setattr(
        runner.weather_state,
        "_fetch_token_book",
        lambda *_args, **_kwargs: {
            "book_status": "ok",
            "bid": 0.81,
            "ask": 0.84,
            "tick_size": 0.01,
        },
    )

    plans, decisions = runner.maker_lifecycle_plans(
        _lifecycle_args(tmp_path),
        tmp_path,
        now=now,
    )

    assert decisions[0]["reprice_stage"] == "midpoint"
    assert decisions[0]["next_price"] == pytest.approx(0.82)
    assert plans[0]["limit_price"] == pytest.approx(0.82)


def test_maker_reprices_to_one_tick_below_ask_after_ten_minutes(
    tmp_path,
    monkeypatch,
) -> None:
    now = datetime(2026, 7, 24, 4, 42, tzinfo=timezone.utc)
    order = _live_maker_order(created=now - timedelta(minutes=11))
    runner.write_jsonl(tmp_path / "live_orders.jsonl", [order])
    _write_lifecycle_state(tmp_path, source_epoch="2026-07-24T04:20:00Z")
    monkeypatch.setattr(
        runner,
        "market_httpx_client",
        lambda *_args, **_kwargs: nullcontext(object()),
    )
    monkeypatch.setattr(
        runner.weather_state,
        "_fetch_token_book",
        lambda *_args, **_kwargs: {
            "book_status": "ok",
            "bid": 0.81,
            "ask": 0.84,
            "tick_size": 0.01,
        },
    )

    plans, decisions = runner.maker_lifecycle_plans(
        _lifecycle_args(tmp_path),
        tmp_path,
        now=now,
    )

    assert decisions[0]["reprice_stage"] == "near_ask"
    assert decisions[0]["next_price"] == pytest.approx(0.83)
    assert plans[0]["limit_price"] == pytest.approx(0.83)


def test_maker_is_not_created_inside_pre_update_blackout() -> None:
    plans = runner.build_entry_plans(
        score_row(),
        live_enabled=True,
        now=datetime(2026, 7, 24, 4, 49, tzinfo=timezone.utc),
        taker_shares=5,
        maker_shares=5,
        order_ttl_min=15,
    )

    assert [plan["child_order_role"] for plan in plans] == ["taker"]


def test_maker_cancels_at_pre_update_deadline(tmp_path) -> None:
    now = datetime(2026, 7, 24, 4, 48, 31, tzinfo=timezone.utc)
    order = _live_maker_order(created=now - timedelta(minutes=5))
    order["maker_lifecycle_deadline_utc"] = "2026-07-24T04:48:30+00:00"
    order["expires_at_utc"] = "2026-07-24T04:48:30+00:00"
    order["cancel_before_data_update_utc"] = "2026-07-24T04:48:30+00:00"
    runner.write_jsonl(tmp_path / "live_orders.jsonl", [order])
    _write_lifecycle_state(tmp_path, source_epoch="2026-07-24T04:20:00Z")

    plans, decisions = runner.maker_lifecycle_plans(
        _lifecycle_args(tmp_path),
        tmp_path,
        now=now,
    )

    assert decisions[0]["action"] == "core_carry_maker_cancel_pre_data_update"
    assert plans[0]["cancel_only"] is True
    assert plans[0]["cancel_before_order_id"] == "maker-order-1"


def test_definitive_post_failure_retries_without_recancelling(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 7, 24, 4, 32, tzinfo=timezone.utc)
    failed = _live_maker_order(created=now - timedelta(minutes=1))
    failed = {
        **failed,
        "status": "error",
        "child_order_role": "core_carry_maker_reprice",
        "execution_action": "core_carry_maker_reprice",
        "source_order_id": "maker-order-1",
        "exchange_response": {
            "error_classification": "current_yes_residual_maker_no_resting_price",
            "pre_place_cancel_status": "cancel_confirmed_replacement_not_posted",
        },
    }
    runner.write_jsonl(tmp_path / "live_orders.jsonl", [failed])
    _write_lifecycle_state(tmp_path, source_epoch="2026-07-24T04:20:00Z")
    monkeypatch.setattr(runner, "market_httpx_client", lambda *_args, **_kwargs: nullcontext(object()))
    monkeypatch.setattr(
        runner.weather_state,
        "_fetch_token_book",
        lambda *_args, **_kwargs: {
            "book_status": "ok",
            "bid": 0.81,
            "ask": 0.84,
            "tick_size": 0.01,
        },
    )

    plans, decisions = runner.maker_lifecycle_plans(
        _lifecycle_args(tmp_path),
        tmp_path,
        now=now,
    )

    assert decisions[0]["action"] == "core_carry_maker_repost"
    assert plans[0]["cancel_before_order_id"] == ""
    assert plans[0]["replacement_requires_order_state"] is False
    assert plans[0]["source_order_id"] == "maker-order-1"
    assert plans[0]["size"] == 5.0


def test_signal_id_is_stable_per_city_day() -> None:
    original = runner.signal_id(score_row())
    changed_book = runner.signal_id({**score_row(), "current_yes_ask": 0.90})
    assert original == changed_book


def test_frozen_split_rejects_other_size(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runner, "assert_runtime_contract", lambda: None)
    args = runner.parser().parse_args(
        [
            "run",
            "--output-dir",
            str(tmp_path),
            "--taker-shares",
            "5",
            "--maker-shares",
            "5",
        ]
    )
    with pytest.raises(RuntimeError, match=r"exactly 10 taker \+ 5 maker"):
        runner.run_once(args)


def test_default_split_and_cost_caps_track_ten_plus_five() -> None:
    args = runner.parser().parse_args(["run"])
    assert args.taker_shares == 10.0
    assert args.maker_shares == 5.0
    assert runner.entry_cost_reservation(args, {"current_yes_ask": 0.84}) == pytest.approx(12.6)
    assert runner.max_live_child_notional_usd(args) == pytest.approx(10.0)


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


def test_journal_cancel_recovers_terminal_maker_projection_once(tmp_path) -> None:
    live_order = {
        "record_type": "weather_edge_live_order",
        "strategy_instance": runner.STRATEGY_INSTANCE,
        "signal_id": "signal-1",
        "comparison_group_id": "comparison-1",
        "maker_lifecycle_root_created_at_utc": "2026-07-29T04:43:46+00:00",
        "created_at_utc": "2026-07-29T04:49:46+00:00",
        "status": "submitted",
        "maker_only": True,
        "venue_order_id": "order-replacement",
        "execution_id": "submitted-execution",
        "plan_id": "submitted-plan",
        "exchange_response": {
            "place": {"orderID": "order-replacement", "status": "live"}
        },
    }
    runner.append_jsonl(tmp_path / "live_orders.jsonl", live_order)
    runner.append_jsonl(
        tmp_path / "execution_journal.jsonl",
        {
            "event_type": "outcome",
            "recorded_at_utc": "2026-07-29T04:50:15+00:00",
            "payload": {
                "kind": "cancel_before_replacement",
                "status": "cancelled",
                "raw_response": {
                    "canceled": ["order-replacement"],
                    "not_canceled": {},
                },
            },
        },
    )

    assert runner.recover_journal_terminal_makers(tmp_path) == 1
    assert runner.recover_journal_terminal_makers(tmp_path) == 0
    head = runner.maker_lifecycle_heads(tmp_path / "live_orders.jsonl")[0]
    assert head["status"] == "cancelled"
    assert head["execution_action"] == "core_carry_maker_terminal"
    assert (
        head["exchange_response"]["quote_reason"]
        == "execution_journal_terminal_recovery"
    )


def test_planned_attempt_without_order_does_not_permanently_consume_signal(
    tmp_path,
) -> None:
    runner.append_jsonl(
        tmp_path / "entry_attempts.jsonl",
        {"signal_id": "planned-only", "status": "planned"},
    )
    runner.append_jsonl(
        tmp_path / "entry_attempts.jsonl",
        {"signal_id": "business-blocked", "status": "blocked"},
    )

    attempted = runner.attempted_signal_ids(tmp_path)

    assert "planned-only" not in attempted
    assert "business-blocked" in attempted


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
    ) == pytest.approx(0.82)


def test_executor_replacement_rejects_price_that_falls_back_to_source_level() -> None:
    assert _current_yes_residual_maker_price(
        best_bid=0.82,
        best_ask=0.86,
        tick_size=0.01,
        price_cap=0.84,
        source_posted_price=0.81,
    ) == pytest.approx(0.83)
    assert _current_yes_residual_maker_price(
        best_bid=0.80,
        best_ask=0.86,
        tick_size=0.01,
        price_cap=0.84,
        source_posted_price=0.81,
    ) == 0.0
