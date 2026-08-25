from __future__ import annotations

import argparse
import json
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
        "current_yes_bid": 0.90,
        "current_yes_ask": 0.95,
        "current_yes_tick_size": 0.01,
        "model_probability_hold": 0.97,
        "checkpoint_key": "Busan|2026-07-24|13",
        "decision_snapshot_ts_utc": "2026-07-24T04:30:00Z",
        "source_report_ts_utc": "2026-07-24T04:20:00Z",
        "obs_source": "aviationweather_metar",
        "observation_cadence_min": 30.0,
        "forecast_source": "open_meteo_live_ecmwf",
        "hourly_curve": [
            {"time_local": "2026-07-24T14:00", "temperature_f": 86.0}
        ],
        "artifact_hash": "hash",
    }


def test_runtime_contract_proves_pit_clock_and_clean_deployment() -> None:
    runner.assert_runtime_contract()
    assert runner.DEPLOYMENT_METADATA["deployed_repo_sha"]
    assert runner.DEPLOYMENT_METADATA["critical_source_dirty"] is False
    assert (
        runner.DEPLOYMENT_METADATA["deployment_contract_version"]
        == "core_carry_v3_10t5m_shared_staged_pullback_v7"
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


def test_entry_is_exactly_ten_taker_plus_one_shared_five_share_maker() -> None:
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
        ("maker_staged", 5.0),
    ]
    assert plans[0]["execution_policy"] == "current_yes_residual_carry_taker_v1"
    assert plans[1]["execution_policy"] == "current_yes_residual_carry_shared_maker_v1"
    assert plans[1]["limit_price"] == pytest.approx(0.91)
    assert plans[1]["maker_price_cap"] == pytest.approx(0.94)
    assert plans[1]["model_token_probability"] == pytest.approx(0.97)
    assert plans[1]["cancel_before_data_update_utc"] == "2026-07-24T04:48:30+00:00"
    assert plans[1]["expires_at_utc"] == "2026-07-24T04:46:00+00:00"
    assert plans[1]["cancel_buffer_sec"] == 90
    assert plans[1]["maker_live_eligible"] is True
    assert plans[1]["maker_post_update_live_rearm"] is True
    assert plans[1]["post_update_reprice_required"] is False
    assert all(
        plan["resolved_execution_profile"]
        == "split_taker_shared_maker_staged_to_pullback_v7"
        for plan in plans
    )
    assert len({plan["execution_config_id"] for plan in plans}) == 1
    assert len({plan["live_exposure_key"] for plan in plans}) == 1
    assert len({plan["plan_dedupe_key"] for plan in plans}) == 2


def test_live_parser_defaults_match_frozen_ten_plus_shared_five_contract() -> None:
    args = runner.parser().parse_args(["run"])

    assert args.taker_shares == runner.FROZEN_TAKER_SHARES == 10
    assert args.maker_shares == runner.FROZEN_MAKER_SHARES == 5
    assert (
        args.pullback_maker_shares
        == runner.FROZEN_PULLBACK_MAKER_SHARES
        == 0
    )
    assert args.summary_filename == "signal_latest_summary.json"
    assert args.summary_history_filename == "signal_summary_history.jsonl"
    assert runner.CONFIG_ID.endswith("10_taker_5_shared_maker_v7")


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
    row = {**score_row(), "model_probability_hold": 0.915}
    plans = runner.build_entry_plans(
        row,
        live_enabled=False,
        now=datetime(2026, 7, 24, 4, 31, tzinfo=timezone.utc),
        taker_shares=5,
        maker_shares=5,
        order_ttl_min=15,
    )
    maker = next(
        plan for plan in plans if plan["child_order_role"] == "maker_staged"
    )
    assert maker["maker_price_cap"] == pytest.approx(0.90)
    assert maker["limit_price"] == pytest.approx(0.90)


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
    maker = next(
        plan for plan in plans if plan["child_order_role"] == "maker_staged"
    )
    assert maker["limit_price"] == pytest.approx(0.93)
    assert maker["maker_price_cap"] == pytest.approx(0.93)


def test_shared_maker_has_one_lifecycle_root_and_five_share_budget() -> None:
    plans = runner.build_entry_plans(
        score_row(),
        live_enabled=True,
        now=datetime(2026, 7, 24, 4, 31, tzinfo=timezone.utc),
        taker_shares=10,
        maker_shares=5,
        order_ttl_min=15,
    )
    makers = [plan for plan in plans if plan["maker_only"]]
    assert len(makers) == 1
    assert makers[0]["size"] == 5.0
    assert len({runner.maker_lifecycle_root(plan) for plan in makers}) == 1


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
    forecast_temp_f: float = 86.0,
) -> None:
    runner.write_jsonl(
        tmp_path / "state_decisions.jsonl",
        [
            {
                "city": "Busan",
                "target_date": "2026-07-24",
                "decision_snapshot_ts_utc": "2026-07-24T04:31:00Z",
                "source_report_ts_utc": source_epoch,
                "obs_source": "aviationweather_metar",
                "current_yes_token_id": token_id,
                "current_bracket": "30",
                "current_question": "Will the highest temperature be 30°C?",
                "obs_status": "ok",
                "station_gap_state": "within_expected_cadence",
                "obs_age_min": 1.0,
                "observation_cadence_min": 30.0,
                "forecast_source": "open_meteo_live_ecmwf",
                "hourly_curve": [
                    {
                        "time_local": "2026-07-24T14:00",
                        "temperature_f": forecast_temp_f,
                    }
                ],
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
    assert decisions[0]["blocker"] == "shared_maker_staged_queue_window"


def test_queue_stage_does_not_reprice_after_external_bid_moves_above_order(
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

    assert plans == []
    assert decisions[0]["action"] == ""
    assert decisions[0]["blocker"] == "shared_maker_staged_queue_window"


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

    assert decisions[0]["action"] == "core_carry_maker_cancel_weather_state"
    assert decisions[0]["blocker"] == "weather_state_changed_requires_fresh_score"
    assert plans[0]["cancel_only"] is True
    assert plans[0]["cancel_before_order_id"] == "maker-order-1"
    assert plans[0]["replacement_requires_order_state"] is False
    assert plans[0]["source_report_ts_utc"] == "2026-07-24T04:20:00Z"


def test_forecast_revision_cancels_before_any_reprice(
    tmp_path,
) -> None:
    now = datetime(2026, 7, 24, 4, 32, tzinfo=timezone.utc)
    order = _live_maker_order(created=now - timedelta(minutes=1))
    runner.write_jsonl(tmp_path / "live_orders.jsonl", [order])
    _write_lifecycle_state(
        tmp_path,
        source_epoch="2026-07-24T04:20:00Z",
        forecast_temp_f=87.0,
    )

    plans, decisions = runner.maker_lifecycle_plans(
        _lifecycle_args(tmp_path),
        tmp_path,
        now=now,
    )

    assert decisions[0]["action"] == "core_carry_maker_cancel_weather_state"
    assert decisions[0]["weather_state_transition_types"] == [
        "forecast_revision"
    ]
    assert plans[0]["cancel_only"] is True


def test_live_order_projection_persists_reprice_stage() -> None:
    created = datetime(2026, 7, 24, 4, 38, tzinfo=timezone.utc)
    order = _live_maker_order(created=created - timedelta(minutes=6))
    plan = runner.build_maker_lifecycle_plan(
        order,
        action="core_carry_maker_reprice",
        limit_price=0.82,
        cancel_only=False,
        cancel_source_order=True,
        reprice_stage="midpoint",
        now=created,
        live_enabled=True,
    )
    projected = build_live_order_record(
        plan,
        {
            "posted_price": 0.82,
            "maker_only": True,
            "place": {"orderID": "maker-order-2", "status": "live"},
        },
        status="submitted",
    )

    assert projected["maker_lifecycle_reprice_count"] == 1
    assert projected["maker_last_reprice_stage"] == "midpoint"


def test_lifecycle_plan_preserves_existing_profile_policy_during_rollout() -> None:
    now = datetime(2026, 7, 24, 4, 38, tzinfo=timezone.utc)
    order = _live_maker_order(created=now - timedelta(minutes=6))
    order["execution_profile"] = "split_taker_maker_edge_capped_no_fallback_v3"
    order["resolved_execution_profile"] = (
        "split_taker_maker_edge_capped_no_fallback_v3"
    )
    order["order_lifecycle_policy"] = (
        "maker_staged_chase_until_pre_data_update_or_ttl_v2"
    )

    plan = runner.build_maker_lifecycle_plan(
        order,
        action="core_carry_maker_cancel_ttl",
        limit_price=0.0,
        cancel_only=True,
        cancel_source_order=True,
        now=now,
        live_enabled=True,
    )

    assert plan["order_lifecycle_policy"] == (
        "maker_staged_chase_until_pre_data_update_or_ttl_v2"
    )


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

    assert decisions[0]["action"] == "core_carry_maker_cancel_weather_state"
    assert decisions[0]["blocker"] == "weather_state_changed_requires_fresh_score"
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
                "obs_source": "aviationweather_metar",
                "current_yes_token_id": "yes-token",
                "current_bracket": "30",
                "obs_status": "ok",
                "station_gap_state": "beyond_expected_cadence",
                "obs_age_min": 121.0,
                "observation_cadence_min": 30.0,
                "forecast_source": "open_meteo_live_ecmwf",
                "hourly_curve": [
                    {
                        "time_local": "2026-07-24T14:00",
                        "temperature_f": 86.0,
                    }
                ],
            }
        ],
    )

    plans, decisions = runner.maker_lifecycle_plans(
        _lifecycle_args(tmp_path),
        tmp_path,
        now=now,
    )

    assert decisions[0]["action"] == "core_carry_maker_cancel_weather_state"
    assert decisions[0]["blocker"] == "weather_state_changed_requires_fresh_score"
    assert plans[0]["cancel_only"] is True


def test_shared_maker_hands_off_to_pullback_after_five_minutes(
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
            "ask": 0.87,
            "tick_size": 0.01,
        },
    )

    plans, decisions = runner.maker_lifecycle_plans(
        _lifecycle_args(tmp_path),
        tmp_path,
        now=now,
    )

    assert decisions[0]["reprice_stage"] == "pullback_handoff"
    assert decisions[0]["next_price"] == pytest.approx(0.85)
    assert plans[0]["limit_price"] == pytest.approx(0.85)
    assert plans[0]["cancel_before_order_id"] == "maker-order-1"
    assert plans[0]["replacement_requires_order_state"] is True
    assert plans[0]["maker_lifecycle_reprice_count"] == 1
    assert plans[0]["maker_last_reprice_stage"] == "pullback_handoff"


def test_shared_maker_handoff_occurs_at_most_once(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 7, 24, 4, 38, tzinfo=timezone.utc)
    order = _live_maker_order(created=now - timedelta(minutes=6))
    order["posted_price"] = 0.82
    order["maker_lifecycle_reprice_count"] = 1
    order["maker_last_reprice_stage"] = "pullback_handoff"
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
            "bid": 0.82,
            "ask": 0.85,
            "tick_size": 0.01,
        },
    )

    plans, decisions = runner.maker_lifecycle_plans(
        _lifecycle_args(tmp_path), tmp_path, now=now
    )

    assert plans == []
    assert decisions[0]["blocker"] == "shared_maker_pullback_handoff_already_used"


def test_shared_maker_still_uses_static_pullback_price_after_ten_minutes(
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
            "ask": 0.87,
            "tick_size": 0.01,
        },
    )

    plans, decisions = runner.maker_lifecycle_plans(
        _lifecycle_args(tmp_path),
        tmp_path,
        now=now,
    )

    assert decisions[0]["reprice_stage"] == "pullback_handoff"
    assert decisions[0]["next_price"] == pytest.approx(0.85)
    assert plans[0]["limit_price"] == pytest.approx(0.85)


def test_shared_maker_stops_after_one_handoff(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 7, 24, 4, 42, tzinfo=timezone.utc)
    order = _live_maker_order(created=now - timedelta(minutes=11))
    order["posted_price"] = 0.82
    order["maker_lifecycle_reprice_count"] = 1
    order["maker_last_reprice_stage"] = "pullback_handoff"
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
            "bid": 0.82,
            "ask": 0.86,
            "tick_size": 0.01,
        },
    )

    plans, decisions = runner.maker_lifecycle_plans(
        _lifecycle_args(tmp_path), tmp_path, now=now
    )

    assert plans == []
    assert decisions[0]["maker_max_reprices"] == 1
    assert decisions[0]["blocker"] == "shared_maker_pullback_handoff_already_used"


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

    clock = runner.maker_clock_assessment(
        score_row(),
        now=datetime(2026, 7, 24, 4, 49, tzinfo=timezone.utc),
        order_ttl_min=15,
    )
    assert clock["maker_clock_basis"] == (
        "next_source_report_not_collector_availability"
    )
    assert clock["maker_clock_status"] == "pre_source_report_blackout"
    assert clock["maker_live_eligible"] is False
    assert clock["maker_live_skip_reason"] == "source_report_deadline_elapsed"
    assert clock["maker_post_update_live_rearm"] is True
    assert clock["maker_post_update_shadow_revalidation"] is True
    assert clock["maker_shadow_policy"] == (
        "first_post_update_positive_ev_replay_only_v1"
    )


def test_maker_clock_does_not_use_our_late_collector_availability() -> None:
    row = {
        **score_row(),
        "source_report_ts_utc": "2026-08-10T10:55:00Z",
        "observation_cadence_min": 30.0,
        "available_at_utc": "2026-08-10T11:32:00Z",
    }
    clock = runner.maker_clock_assessment(
        row,
        now=datetime(2026, 8, 10, 11, 31, 49, tzinfo=timezone.utc),
        order_ttl_min=15,
    )

    assert clock["next_source_report_due_utc"] == "2026-08-10T11:25:00+00:00"
    assert clock["cancel_before_source_report_utc"] == "2026-08-10T11:23:30+00:00"
    assert clock["seconds_to_next_source_report"] == pytest.approx(-409.0)
    assert clock["maker_live_eligible"] is False


def test_late_epoch_entry_records_terminal_live_maker_and_shadow_counterfactual(
    tmp_path,
) -> None:
    row = score_row()
    runner.write_jsonl(tmp_path / "pre_live_scores.jsonl", [row])
    runner.write_jsonl(
        tmp_path / "would_orders.jsonl",
        [
            {
                "checkpoint_key": row["checkpoint_key"],
                "family_city_day_conflict": False,
            }
        ],
    )
    args = runner.parser().parse_args(
        ["run", "--output-dir", str(tmp_path), "--max-daily-cost-usd", "100"]
    )

    plans, attempts = runner.new_entry_plans(
        args,
        tmp_path,
        now=datetime(2026, 7, 24, 4, 49, tzinfo=timezone.utc),
    )

    assert [plan["child_order_role"] for plan in plans] == ["taker"]
    assert runner.entry_plan_cost_reservation(plans) == pytest.approx(9.5)
    assert attempts[0]["status"] == "planned"
    assert attempts[0]["maker_clock_status"] == "pre_source_report_blackout"
    assert attempts[0]["maker_live_action"] == "defer_post_update_rearm"
    assert attempts[0]["maker_planned_shares"] == 0.0
    assert attempts[0]["maker_shadow_revalidation_shares"] == 5.0
    assert attempts[0]["maker_post_update_live_rearm"] is True


def test_deferred_makers_rearm_on_new_positive_ev_weather_epoch(
    tmp_path, monkeypatch
) -> None:
    row = score_row()
    sid = runner.signal_id(row)
    runner.write_jsonl(tmp_path / "pre_live_scores.jsonl", [row])
    runner.write_jsonl(
        tmp_path / "would_orders.jsonl",
        [{"checkpoint_key": row["checkpoint_key"], "family_city_day_conflict": False}],
    )
    args = runner.parser().parse_args(
        ["run", "--output-dir", str(tmp_path), "--max-daily-cost-usd", "100"]
    )

    initial_plans, initial_attempts = runner.new_entry_plans(
        args,
        tmp_path,
        now=datetime(2026, 7, 24, 4, 49, tzinfo=timezone.utc),
    )
    assert [plan["child_order_role"] for plan in initial_plans] == ["taker"]
    runner.write_jsonl(tmp_path / "entry_attempts.jsonl", initial_attempts)
    runner.write_jsonl(
        tmp_path / "live_orders.jsonl",
        [
            {
                "signal_id": sid,
                "child_order_role": "taker",
                "status": "submitted",
                "city": row["city"],
                "target_date": row["target_date"],
                "created_at_utc": "2026-07-24T04:49:00+00:00",
                "posted_notional": 8.4,
            }
        ],
    )
    newer = {
        **row,
        "decision_snapshot_ts_utc": "2026-07-24T04:51:00Z",
        "source_report_ts_utc": "2026-07-24T04:50:00Z",
        "obs_status": "ok",
        "station_gap_state": "within_expected_cadence",
        "obs_age_min": 1.0,
    }
    runner.write_jsonl(tmp_path / "state_decisions.jsonl", [newer])
    monkeypatch.setattr(
        runner,
        "market_httpx_client",
        lambda *_args, **_kwargs: nullcontext(object()),
    )
    monkeypatch.setattr(
        runner.signal_runner,
        "fetch_full_book",
        lambda *_args, **_kwargs: {
            "status": "ok",
            "bid": 0.88,
            "ask": 0.92,
            "bid_size": 20,
            "ask_size": 20,
            "tick_size": 0.01,
            "asks": [{"price": 0.92, "size": 20}],
            "fetched_at_utc": "2026-07-24T04:51:00Z",
        },
    )
    monkeypatch.setattr(
        runner,
        "evaluate_entry",
        lambda enriched, _asks, _artifact: {
            "eligible": True,
            "reasons": [],
            "model_probability_hold": 0.95,
            "model_edge_after_fee_and_depth": 0.02,
        },
    )

    plans, attempts = runner.new_entry_plans(
        args,
        tmp_path,
        now=datetime(2026, 7, 24, 4, 51, 5, tzinfo=timezone.utc),
    )

    assert [plan["child_order_role"] for plan in plans] == [
        "maker_staged",
    ]
    assert plans[0]["limit_price"] == pytest.approx(0.89)
    assert all(plan["limit_price"] < row["current_yes_ask"] for plan in plans)
    assert all(plan["maker_rearm_model_edge_after_fee_and_depth"] == 0.02 for plan in plans)
    assert attempts[0]["maker_live_action"] == "post"
    assert attempts[0]["maker_rearm_status"] == "eligible"


def test_post_update_rearm_keeps_makers_off_when_core_net_ev_is_not_positive(
    tmp_path, monkeypatch
) -> None:
    row = score_row()
    sid = runner.signal_id(row)
    deferred = {
        "signal_id": sid,
        "created_at_utc": "2026-07-24T04:49:00+00:00",
        "maker_live_action": "defer_post_update_rearm",
        "data_epoch_ref": runner.weather_state_epoch_ref(row),
    }
    runner.write_jsonl(tmp_path / "pre_live_scores.jsonl", [row])
    runner.write_jsonl(
        tmp_path / "would_orders.jsonl",
        [{"checkpoint_key": row["checkpoint_key"], "family_city_day_conflict": False}],
    )
    runner.write_jsonl(tmp_path / "entry_attempts.jsonl", [deferred])
    runner.write_jsonl(
        tmp_path / "live_orders.jsonl",
        [{"signal_id": sid, "child_order_role": "taker", "status": "submitted"}],
    )
    newer = {
        **row,
        "decision_snapshot_ts_utc": "2026-07-24T04:51:00Z",
        "source_report_ts_utc": "2026-07-24T04:50:00Z",
        "obs_status": "ok",
        "station_gap_state": "within_expected_cadence",
        "obs_age_min": 1.0,
    }
    runner.write_jsonl(tmp_path / "state_decisions.jsonl", [newer])
    monkeypatch.setattr(runner, "market_httpx_client", lambda *_a, **_k: nullcontext(object()))
    monkeypatch.setattr(
        runner.signal_runner,
        "fetch_full_book",
        lambda *_a, **_k: {
            "status": "ok",
            "bid": 0.90,
            "ask": 0.98,
            "tick_size": 0.01,
            "asks": [{"price": 0.98, "size": 20}],
        },
    )
    monkeypatch.setattr(
        runner,
        "evaluate_entry",
        lambda *_a, **_k: {
            "eligible": False,
            "reasons": ["non_positive_taker_ev"],
            "model_probability_hold": 0.93,
            "model_edge_after_fee_and_depth": -0.05,
        },
    )
    args = runner.parser().parse_args(["run", "--output-dir", str(tmp_path)])

    plans, attempts = runner.new_entry_plans(
        args,
        tmp_path,
        now=datetime(2026, 7, 24, 4, 51, 5, tzinfo=timezone.utc),
    )

    assert plans == []
    assert attempts[0]["maker_live_action"] == "defer_post_update_rearm"
    assert attempts[0]["maker_rearm_status"] == "not_eligible"
    assert attempts[0]["maker_rearm_reason"] == "non_positive_taker_ev"


def test_forecast_revision_alone_does_not_rearm_source_blackout_makers(
    tmp_path,
) -> None:
    row = score_row()
    sid = runner.signal_id(row)
    deferred = {
        "signal_id": sid,
        "created_at_utc": "2026-07-24T04:49:00+00:00",
        "maker_live_action": "defer_post_update_rearm",
        "data_epoch_ref": runner.weather_state_epoch_ref(row),
        "data_epoch_ts_utc": row["source_report_ts_utc"],
    }
    runner.write_jsonl(tmp_path / "pre_live_scores.jsonl", [row])
    runner.write_jsonl(
        tmp_path / "would_orders.jsonl",
        [{"checkpoint_key": row["checkpoint_key"], "family_city_day_conflict": False}],
    )
    runner.write_jsonl(tmp_path / "entry_attempts.jsonl", [deferred])
    runner.write_jsonl(
        tmp_path / "live_orders.jsonl",
        [{"signal_id": sid, "child_order_role": "taker", "status": "submitted"}],
    )
    forecast_only = {
        **row,
        "decision_snapshot_ts_utc": "2026-07-24T04:51:00Z",
        "hourly_curve": [
            {"time_local": "2026-07-24T14:00", "temperature_f": 87.0}
        ],
    }
    runner.write_jsonl(tmp_path / "state_decisions.jsonl", [forecast_only])
    args = runner.parser().parse_args(["run", "--output-dir", str(tmp_path)])

    plans, attempts = runner.new_entry_plans(
        args,
        tmp_path,
        now=datetime(2026, 7, 24, 4, 51, 5, tzinfo=timezone.utc),
    )

    assert plans == []
    assert attempts == []


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
    assert plans[0]["maker_lifecycle_reprice_count"] == failed[
        "maker_lifecycle_reprice_count"
    ]


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
    with pytest.raises(
        RuntimeError,
        match=r"exactly 10 taker \+ one shared 5-share maker budget",
    ):
        runner.run_once(args)


def test_default_split_and_cost_caps_track_ten_plus_shared_five() -> None:
    args = runner.parser().parse_args(["run"])
    assert args.taker_shares == 10.0
    assert args.maker_shares == 5.0
    assert args.pullback_maker_shares == 0.0
    assert runner.entry_cost_reservation(args, {"current_yes_ask": 0.84}) == pytest.approx(12.6)
    assert runner.entry_plan_cost_reservation(
        [
            {"order_notional_cap": 8.4},
            {"order_notional_cap": 4.0},
        ]
    ) == pytest.approx(12.4)
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


def test_completed_taker_only_recovers_missing_maker_child(tmp_path) -> None:
    row = score_row()
    sid = runner.signal_id(row)
    runner.write_jsonl(tmp_path / "pre_live_scores.jsonl", [row])
    runner.write_jsonl(
        tmp_path / "would_orders.jsonl",
        [{"checkpoint_key": row["checkpoint_key"], "family_city_day_conflict": True}],
    )
    runner.write_jsonl(
        tmp_path / "live_orders.jsonl",
        [
            {
                "signal_id": sid,
                "child_order_role": "taker",
                "status": "submitted",
                "city": row["city"],
                "target_date": row["target_date"],
                "created_at_utc": "2026-07-24T04:31:00+00:00",
                "posted_notional": 4.2,
            }
        ],
    )
    args = runner.parser().parse_args(
        ["run", "--output-dir", str(tmp_path), "--max-daily-cost-usd", "100"]
    )

    plans, attempts = runner.new_entry_plans(
        args,
        tmp_path,
        now=datetime(2026, 7, 24, 4, 32, tzinfo=timezone.utc),
    )

    assert [plan["child_order_role"] for plan in plans] == [
        "maker_staged",
    ]
    assert attempts[0]["maker_live_action"] == "post"
    assert attempts[0]["maker_planned_shares"] == 5.0


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


def test_loop_error_refreshes_latest_health_artifact(tmp_path, monkeypatch) -> None:
    args = argparse.Namespace(
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


def test_low_price_band_halt_skips_maker_keeps_taker_and_shadow() -> None:
    row = {
        **score_row(),
        "current_yes_bid": 0.80,
        "current_yes_ask": 0.84,
        "model_probability_hold": 0.90,
    }
    plans = runner.build_entry_plans(
        row,
        live_enabled=True,
        now=datetime(2026, 7, 24, 4, 31, tzinfo=timezone.utc),
        taker_shares=10,
        maker_shares=5,
        order_ttl_min=15,
    )
    assert [plan["child_order_role"] for plan in plans] == ["taker"]

    now = datetime(2026, 7, 24, 4, 31, tzinfo=timezone.utc)
    staged = runner.base_plan_fields(
        row,
        child_order_role="maker_staged",
        shares=5,
        live_enabled=True,
        now=now,
        order_ttl_min=15,
    )
    for fields, would_be in ((staged, 0.81),):
        assert fields["maker_live_eligible"] is False
        assert fields["maker_live_skip_reason"] == "low_price_band_halt_shadow_only"
        assert fields["maker_low_price_band_halt"] is True
        assert fields["maker_low_price_band_would_be_price"] == pytest.approx(would_be)


def test_low_price_band_halt_disabled_when_profile_min_is_zero(monkeypatch) -> None:
    monkeypatch.setattr(runner, "maker_low_price_band_halt_min", lambda: 0.0)
    row = {
        **score_row(),
        "current_yes_bid": 0.80,
        "current_yes_ask": 0.84,
        "model_probability_hold": 0.90,
    }
    plans = runner.build_entry_plans(
        row,
        live_enabled=True,
        now=datetime(2026, 7, 24, 4, 31, tzinfo=timezone.utc),
        taker_shares=10,
        maker_shares=5,
        order_ttl_min=15,
    )
    assert [plan["child_order_role"] for plan in plans] == [
        "taker",
        "maker_staged",
    ]
def _write_positive_taker_ev_signal(tmp_path, *, trigger_patch=None, prior_rows=()):
    trigger = {
        **score_row(),
        "eligible": True,
        "created_at_utc": "2026-07-24T04:30:01Z",
        "as_of_ts_utc": "2026-07-24T04:30:00Z",
        "current_bracket": "30",
        "current_condition_id": "condition",
        "current_yes_token_id": "yes-token",
        "artifact_hash": "artifact-hash",
        **(trigger_patch or {}),
    }
    runner.write_jsonl(tmp_path / "pre_live_scores.jsonl", [*prior_rows, trigger])
    runner.write_jsonl(
        tmp_path / "would_orders.jsonl",
        [{"checkpoint_key": trigger["checkpoint_key"]}],
    )
    return trigger


def test_decision_packet_writes_self_contained_first_positive_signal(tmp_path) -> None:
    prior = {
        **score_row(),
        "checkpoint_key": "Busan|2026-07-24|12",
        "decision_snapshot_ts_utc": "2026-07-24T03:30:00Z",
        "created_at_utc": "2026-07-24T03:30:01Z",
        "eligible": False,
        "reasons": ["non_positive_taker_ev"],
        "current_yes_bid": 0.77,
        "current_yes_ask": 0.81,
        "model_probability_hold": 0.80,
    }
    trigger = _write_positive_taker_ev_signal(
        tmp_path,
        prior_rows=[prior],
        trigger_patch={
            "observation_history_id": "obs-1",
            "obs_ingested_at_utc": "2026-07-24T04:21:00Z",
            "current_yes_book_exchange_ts_utc": "2026-07-24T04:29:58Z",
            "book_capture_id": "book-1",
            "forecast_values_hash": "forecast-hash",
            "forecast_run_lineage_status": "ok",
            "forecast_curve_archive_path": "/raw/forecast.jsonl",
            "book_snapshot_id": "book-snapshot-1",
            "snapshot_capture_id": "snapshot-capture-1",
        },
    )

    result = runner.write_new_decision_packets(tmp_path)

    packet = list(runner.iter_jsonl(tmp_path / "decision_packets.jsonl"))[0]
    assert result["written"] == 1
    assert result["alerts"] == []
    assert [row["packet_id"] for row in result["written_packets"]] == [packet["packet_id"]]
    assert packet["schema_version"] == runner.DECISION_PACKET_SCHEMA_VERSION
    assert packet["packet_id"] == runner.decision_packet_id(trigger)
    assert packet["trigger"]["payload"] == trigger
    assert packet["prior_checkpoints"]["last_checkpoint"]["checkpoint"]["checkpoint_key"] == prior["checkpoint_key"]
    assert packet["prior_checkpoints"]["last_informative_quote_usable_negative_checkpoint"]["status"] == "available"
    assert packet["event_references"]["observation"]["row_id"]["value"] == "obs-1"
    assert packet["event_references"]["book"]["capture_id"]["value"] == "book-1"
    assert packet["event_references"]["book"]["snapshot_id"]["value"] == "book-snapshot-1"
    assert packet["event_references"]["forecast"]["archive_path"]["value"] == "/raw/forecast.jsonl"
    assert packet["event_references"]["forecast"]["lineage_status"]["value"] == "ok"
    assert packet["snapshot_references"]["snapshot_capture_id"]["value"] == "snapshot-capture-1"
    assert packet["order_linkage"]["execution_ids"] == []


def test_decision_packet_is_idempotent_across_repeat_checkpoint_or_restart(tmp_path) -> None:
    _write_positive_taker_ev_signal(tmp_path)

    assert runner.write_new_decision_packets(tmp_path)["written"] == 1
    assert runner.write_new_decision_packets(tmp_path)["written"] == 0
    assert len(list(runner.iter_jsonl(tmp_path / "decision_packets.jsonl"))) == 1


def test_decision_packet_marks_degraded_last_negative_and_missing_lineage(tmp_path) -> None:
    degenerate_negative = {
        **score_row(),
        "checkpoint_key": "Busan|2026-07-24|12",
        "decision_snapshot_ts_utc": "2026-07-24T03:30:00Z",
        "eligible": False,
        "current_yes_ask": None,
        "model_probability_hold": 0.80,
    }
    _write_positive_taker_ev_signal(tmp_path, prior_rows=[degenerate_negative])

    runner.write_new_decision_packets(tmp_path)

    packet = list(runner.iter_jsonl(tmp_path / "decision_packets.jsonl"))[0]
    assert packet["prior_checkpoints"]["last_negative_checkpoint"]["checkpoint"]["quote_usable"] is False
    usable = packet["prior_checkpoints"]["last_informative_quote_usable_negative_checkpoint"]
    assert usable["status"] == "unavailable"
    assert usable["reason"] == "no_prior_informative_quote_usable_negative_checkpoint"
    assert packet["event_references"]["forecast"]["values_hash"] == {
        "status": "unavailable",
        "source_field": None,
        "value": None,
        "reason": "missing_in_decision_row",
    }


def test_decision_packet_write_failure_records_alert_without_changing_entry_decision(
    tmp_path, monkeypatch
) -> None:
    _write_positive_taker_ev_signal(tmp_path)
    args = runner.parser().parse_args(["run", "--output-dir", str(tmp_path)])
    expected_plans, expected_attempts = runner.new_entry_plans(
        args, tmp_path, now=datetime(2026, 7, 24, 4, 31, tzinfo=timezone.utc)
    )
    original_append = runner.append_decision_packet

    def fail_packet(path, row):
        if path.name == "decision_packets.jsonl":
            raise OSError("journal read-only")
        return original_append(path, row)

    monkeypatch.setattr(runner, "append_decision_packet", fail_packet)
    result = runner.write_new_decision_packets(tmp_path)
    actual_plans, actual_attempts = runner.new_entry_plans(
        args, tmp_path, now=datetime(2026, 7, 24, 4, 31, tzinfo=timezone.utc)
    )

    assert result["written"] == 0
    assert result["alerts"][0]["reason"] == "decision_packet_write_failed"
    assert actual_plans == expected_plans
    assert actual_attempts == expected_attempts


def test_first_positive_signal_requests_bounded_full_ladder_ws_tape(tmp_path) -> None:
    _write_positive_taker_ev_signal(
        tmp_path,
        trigger_patch={
            "full_ladder_yes_tokens": [
                {"bracket": "29", "condition_id": "c29", "market_id": "m29", "token_id": "yes29"},
                {"bracket": "30", "condition_id": "c30", "market_id": "m30", "token_id": "yes30"},
                {"bracket": "31+", "condition_id": "c31", "market_id": "m31", "token_id": "yes31"},
            ],
        },
    )
    packets = runner.write_new_decision_packets(tmp_path)["written_packets"]

    result = runner.write_capture_demands(tmp_path, packets=packets)
    repeat = runner.write_capture_demands(tmp_path, packets=packets)
    rows = list(runner.iter_jsonl(tmp_path / "capture_demands.jsonl"))

    assert result == {"written": 3, "alerts": []}
    assert repeat == {"written": 0, "alerts": []}
    assert {row["token_id"] for row in rows} == {"yes29", "yes30", "yes31"}
    assert {row["priority"] for row in rows} == {"P0"}
    assert {row["desired_transport"] for row in rows} == {"WS"}
    assert {row["strategy_key"] for row in rows} == {"reheat_risk.current_yes"}
    assert {row["metadata"]["ladder_scope"] for row in rows} == {"all_yes_outcome_tokens"}
    requested = datetime.fromisoformat(rows[0]["requested_at_utc"])
    expires = datetime.fromisoformat(rows[0]["expires_at_utc"])
    assert expires - requested == timedelta(minutes=30)


def test_full_ladder_capture_demand_fails_closed_above_token_budget(tmp_path) -> None:
    _write_positive_taker_ev_signal(
        tmp_path,
        trigger_patch={
            "full_ladder_yes_tokens": [
                {"bracket": str(i), "condition_id": f"c{i}", "token_id": f"yes{i}"}
                for i in range(runner.CAPTURE_DEMAND_MAX_LADDER_TOKENS + 1)
            ]
        },
    )
    packets = runner.write_new_decision_packets(tmp_path)["written_packets"]

    result = runner.write_capture_demands(tmp_path, packets=packets)

    assert result["written"] == 0
    assert result["alerts"][0]["reason"] == "capture_demand_full_ladder_token_budget_exceeded"
    assert not (tmp_path / "capture_demands.jsonl").exists()


def test_candidate_capture_is_bounded_and_gives_full_ladder_only_to_top_edge(tmp_path) -> None:
    for index in range(10):
        runner.append_jsonl(
            tmp_path / "pre_live_scores.jsonl",
            {
                "created_at_utc": f"2026-08-22T10:{index:02d}:00Z",
                "checkpoint_key": f"City{index}|2026-08-22|15",
                "city": f"City{index}",
                "target_date": "2026-08-22",
                "current_bracket": "30",
                "current_condition_id": f"current-condition-{index}",
                "current_market_id": f"current-market-{index}",
                "current_yes_token_id": f"current-token-{index}",
                "model_edge_after_fee_and_depth": -0.010 + index * 0.001,
                "decision_status": "not_eligible",
                "reasons": ["non_positive_taker_ev"],
                "full_ladder_yes_tokens": [
                    {
                        "bracket": str(bracket),
                        "condition_id": f"ladder-condition-{index}-{bracket}",
                        "market_id": f"ladder-market-{index}-{bracket}",
                        "token_id": f"ladder-token-{index}-{bracket}",
                    }
                    for bracket in range(3)
                ],
            },
        )
    runner.append_jsonl(
        tmp_path / "pre_live_scores.jsonl",
        {
            "created_at_utc": "2026-08-22T10:59:00Z",
            "checkpoint_key": "Blocked|2026-08-22|15",
            "current_condition_id": "blocked-condition",
            "current_yes_token_id": "blocked-token",
            "model_edge_after_fee_and_depth": -0.0001,
            "reasons": ["non_positive_taker_ev", "outside_carry_market_mid_domain"],
        },
    )

    now = datetime(2026, 8, 22, 10, 10, tzinfo=timezone.utc)
    result = runner.write_candidate_capture_demands(tmp_path, now=now)
    repeat = runner.write_candidate_capture_demands(tmp_path, now=now)
    rows = list(runner.iter_jsonl(tmp_path / "capture_demands.jsonl"))
    current = [row for row in rows if row["reason"] == "core_carry_candidate_current_token_tape"]
    ladder = [row for row in rows if row["reason"] == "core_carry_candidate_full_ladder_tape"]

    assert result == {"written": 11, "alerts": [], "candidate_count": 8}
    assert repeat == {"written": 0, "alerts": [], "candidate_count": 8}
    assert len(current) == runner.CANDIDATE_CAPTURE_MAX_CURRENT_TOKENS
    assert "blocked-token" not in {row["token_id"] for row in rows}
    assert {row["priority"] for row in rows} == {"P1"}
    assert {row["metadata"]["research_only"] for row in rows} == {True}
    assert {row["metadata"]["city"] for row in ladder} == {"City9"}
    assert len({row["trigger_event_id"] for row in ladder}) == 1


def test_candidate_capture_does_not_declare_expired_historical_window(tmp_path) -> None:
    runner.append_jsonl(
        tmp_path / "pre_live_scores.jsonl",
        {
            "created_at_utc": "2026-08-22T09:00:00Z",
            "checkpoint_key": "Expired|2026-08-22|14",
            "current_condition_id": "expired-condition",
            "current_yes_token_id": "expired-token",
            "model_edge_after_fee_and_depth": -0.001,
            "reasons": ["non_positive_taker_ev"],
        },
    )

    result = runner.write_candidate_capture_demands(
        tmp_path, now=datetime(2026, 8, 22, 10, 0, tzinfo=timezone.utc)
    )

    assert result == {"written": 0, "alerts": [], "candidate_count": 0}
    assert not (tmp_path / "capture_demands.jsonl").exists()


def test_latest_weather_epochs_uses_incremental_persistent_index(tmp_path) -> None:
    source = tmp_path / "state_decisions.jsonl"
    first = {
        "city": "Busan",
        "target_date": "2026-08-26",
        "decision_snapshot_ts_utc": "2026-08-25T16:00:00Z",
        "marker": "first",
    }
    newer = {
        **first,
        "decision_snapshot_ts_utc": "2026-08-25T16:05:00Z",
        "marker": "newer",
    }
    older = {
        **first,
        "decision_snapshot_ts_utc": "2026-08-25T15:55:00Z",
        "marker": "older",
    }
    runner.write_jsonl(source, [first])

    assert runner.latest_weather_epochs(source)[("Busan", "2026-08-26")]["marker"] == "first"
    index_path = runner.weather_epoch_index_path(source)
    assert index_path.exists()
    with sqlite3.connect(index_path) as conn:
        offset_before = int(
            conn.execute(
                "SELECT value FROM index_metadata WHERE key = 'indexed_offset'"
            ).fetchone()[0]
        )
    assert offset_before == source.stat().st_size

    runner.append_jsonl(source, newer)
    runner.append_jsonl(source, older)
    latest = runner.latest_weather_epochs(source)
    assert latest[("Busan", "2026-08-26")]["marker"] == "newer"


def test_latest_weather_epochs_waits_for_complete_jsonl_record(tmp_path) -> None:
    source = tmp_path / "state_decisions.jsonl"
    first = {
        "city": "Busan",
        "target_date": "2026-08-26",
        "decision_snapshot_ts_utc": "2026-08-25T16:00:00Z",
        "marker": "first",
    }
    partial = {
        **first,
        "decision_snapshot_ts_utc": "2026-08-25T16:05:00Z",
        "marker": "complete-after-newline",
    }
    runner.write_jsonl(source, [first])
    runner.latest_weather_epochs(source)
    encoded = json.dumps(partial).encode("utf-8")
    with source.open("ab") as handle:
        handle.write(encoded)

    assert runner.latest_weather_epochs(source)[("Busan", "2026-08-26")]["marker"] == "first"
    with source.open("ab") as handle:
        handle.write(b"\n")
    assert (
        runner.latest_weather_epochs(source)[("Busan", "2026-08-26")]["marker"]
        == "complete-after-newline"
    )


def test_latest_weather_epochs_rebuilds_after_source_truncation(tmp_path) -> None:
    source = tmp_path / "state_decisions.jsonl"
    runner.write_jsonl(
        source,
        [
            {
                "city": "Busan",
                "target_date": "2026-08-26",
                "decision_snapshot_ts_utc": "2026-08-25T16:00:00Z",
                "padding": "x" * 500,
            }
        ],
    )
    runner.latest_weather_epochs(source)
    runner.write_jsonl(
        source,
        [
            {
                "city": "Tokyo",
                "target_date": "2026-08-26",
                "decision_snapshot_ts_utc": "2026-08-25T16:10:00Z",
            }
        ],
    )

    assert set(runner.latest_weather_epochs(source)) == {("Tokyo", "2026-08-26")}
