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
        "current_yes_bid": 0.80,
        "current_yes_ask": 0.84,
        "current_yes_tick_size": 0.01,
        "model_probability_hold": 0.91,
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
        == "core_carry_v3_shared_order_runtime_10t5m5m_dual_maker_v5"
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


def test_entry_is_exactly_ten_taker_plus_two_five_share_makers() -> None:
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
        ("maker_pullback", 5.0),
    ]
    assert plans[0]["execution_policy"] == "current_yes_residual_carry_taker_v1"
    assert plans[1]["execution_policy"] == "current_yes_residual_carry_staged_maker_v3"
    assert plans[2]["execution_policy"] == "current_yes_residual_carry_pullback_maker_v1"
    assert plans[1]["limit_price"] == pytest.approx(0.81)
    assert plans[2]["limit_price"] == pytest.approx(0.82)
    assert plans[1]["maker_price_cap"] == pytest.approx(0.83)
    assert plans[1]["model_token_probability"] == pytest.approx(0.91)
    assert plans[1]["cancel_before_data_update_utc"] == "2026-07-24T04:48:30+00:00"
    assert plans[1]["expires_at_utc"] == "2026-07-24T04:46:00+00:00"
    assert plans[1]["cancel_buffer_sec"] == 90
    assert plans[1]["maker_live_eligible"] is True
    assert plans[1]["maker_post_update_live_rearm"] is False
    assert plans[1]["post_update_reprice_required"] is False
    assert all(
        plan["resolved_execution_profile"]
        == "split_taker_two_maker_event_validated_no_fallback_v5"
        for plan in plans
    )
    assert len({plan["execution_config_id"] for plan in plans}) == 1
    assert len({plan["live_exposure_key"] for plan in plans}) == 1
    assert len({plan["plan_dedupe_key"] for plan in plans}) == 3


def test_live_parser_defaults_match_frozen_ten_plus_five_plus_five_contract() -> None:
    args = runner.parser().parse_args(["run"])

    assert args.taker_shares == runner.FROZEN_TAKER_SHARES == 10
    assert args.maker_shares == runner.FROZEN_MAKER_SHARES == 5
    assert (
        args.pullback_maker_shares
        == runner.FROZEN_PULLBACK_MAKER_SHARES
        == 5
    )
    assert args.summary_filename == "signal_latest_summary.json"
    assert args.summary_history_filename == "signal_summary_history.jsonl"
    assert runner.CONFIG_ID.endswith("split_10_taker_5_staged_5_pullback_v5")


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
    maker = next(
        plan for plan in plans if plan["child_order_role"] == "maker_staged"
    )
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
    maker = next(
        plan for plan in plans if plan["child_order_role"] == "maker_staged"
    )
    assert maker["limit_price"] == pytest.approx(0.93)
    assert maker["maker_price_cap"] == pytest.approx(0.93)


def test_pullback_maker_is_exactly_entry_ask_minus_two_cents() -> None:
    plans = runner.build_entry_plans(
        score_row(),
        live_enabled=True,
        now=datetime(2026, 7, 24, 4, 31, tzinfo=timezone.utc),
        taker_shares=10,
        maker_shares=5,
        pullback_maker_shares=5,
        order_ttl_min=15,
    )
    pullback = next(
        plan for plan in plans if plan["child_order_role"] == "maker_pullback"
    )
    assert pullback["maker_arm"] == "pullback"
    assert pullback["limit_price"] == pytest.approx(0.82)
    assert pullback["quote_mode"] == "entry_ask_minus_2c_static_post_only_edge_capped"
    assert pullback["maker_experiment_id"] == (
        "core_carry_staged_vs_pullback_maker_ab_20260813"
    )


def test_two_maker_arms_have_independent_lifecycle_roots() -> None:
    plans = runner.build_entry_plans(
        score_row(),
        live_enabled=True,
        now=datetime(2026, 7, 24, 4, 31, tzinfo=timezone.utc),
        taker_shares=10,
        maker_shares=5,
        pullback_maker_shares=5,
        order_ttl_min=15,
    )
    makers = [plan for plan in plans if plan["maker_only"]]
    assert len({runner.maker_lifecycle_root(plan) for plan in makers}) == 2


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
    assert decisions[0]["blocker"] == "queue_preserving_stage"


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
    assert decisions[0]["blocker"] == "queue_preserving_stage"


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
    assert plans[0]["maker_lifecycle_reprice_count"] == 1
    assert plans[0]["maker_last_reprice_stage"] == "midpoint"


def test_maker_reprices_at_most_once_per_stage(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 7, 24, 4, 38, tzinfo=timezone.utc)
    order = _live_maker_order(created=now - timedelta(minutes=6))
    order["posted_price"] = 0.82
    order["maker_lifecycle_reprice_count"] = 1
    order["maker_last_reprice_stage"] = "midpoint"
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
    assert decisions[0]["blocker"] == "maker_reprice_stage_already_used"


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


def test_maker_stops_after_two_reprices(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 7, 24, 4, 42, tzinfo=timezone.utc)
    order = _live_maker_order(created=now - timedelta(minutes=11))
    order["posted_price"] = 0.82
    order["maker_lifecycle_reprice_count"] = 2
    order["maker_last_reprice_stage"] = "midpoint"
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
    assert decisions[0]["maker_max_reprices"] == 2
    assert decisions[0]["blocker"] == "maker_reprice_limit_reached"


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
    assert clock["maker_post_update_live_rearm"] is False
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
    assert runner.entry_plan_cost_reservation(plans) == pytest.approx(8.4)
    assert attempts[0]["status"] == "planned"
    assert attempts[0]["maker_clock_status"] == "pre_source_report_blackout"
    assert attempts[0]["maker_live_action"] == "skip_terminal"
    assert attempts[0]["maker_planned_shares"] == 0.0
    assert attempts[0]["maker_shadow_revalidation_shares"] == 10.0
    assert attempts[0]["maker_post_update_live_rearm"] is False


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


def test_pullback_maker_never_reprices_before_ttl(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 7, 24, 4, 37, tzinfo=timezone.utc)
    order = runner.build_entry_plans(
        score_row(),
        live_enabled=True,
        now=now - timedelta(minutes=6),
        taker_shares=10,
        maker_shares=5,
        pullback_maker_shares=5,
        order_ttl_min=15,
    )[2]
    order.update(
        {
            "status": "submitted",
            "created_at_utc": (now - timedelta(minutes=6)).isoformat(),
            "posted_price": order["limit_price"],
            "venue_order_id": "pullback-order-1",
            "exchange_response": {"place": {"orderID": "pullback-order-1"}},
        }
    )
    runner.write_jsonl(tmp_path / "live_orders.jsonl", [order])
    _write_lifecycle_state(tmp_path, source_epoch="2026-07-24T04:20:00Z")
    monkeypatch.setattr(
        runner,
        "market_httpx_client",
        lambda *_args, **_kwargs: nullcontext(object()),
    )

    plans, decisions = runner.maker_lifecycle_plans(
        _lifecycle_args(tmp_path), tmp_path, now=now
    )

    assert plans == []
    assert decisions[0]["maker_arm"] == "pullback"
    assert decisions[0]["maker_max_reprices"] == 0
    assert decisions[0]["blocker"] == "pullback_static_resting_no_reprice"


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
        match=r"exactly 10 taker \+ 5 staged maker \+ 5 pullback maker",
    ):
        runner.run_once(args)


def test_default_split_and_cost_caps_track_ten_plus_five_plus_five() -> None:
    args = runner.parser().parse_args(["run"])
    assert args.taker_shares == 10.0
    assert args.maker_shares == 5.0
    assert args.pullback_maker_shares == 5.0
    assert runner.entry_cost_reservation(args, {"current_yes_ask": 0.84}) == pytest.approx(16.8)
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
        "maker_pullback",
    ]
    assert attempts[0]["maker_live_action"] == "post"
    assert attempts[0]["maker_planned_shares"] == 10.0


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
