from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.ops import weather_order_executor as executor
from src.strategies.weather_edge_v1.tools.execution_pipeline import ExecutorConfig, execute_trade_plans
from src.strategies.weather_edge_v1.execution.engine import build_d1_legacy_plan_compatibility
from src.strategies.weather_edge_v1.runtime.non_live import execute_legacy_compatibility_paper
ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/ops/d1_yes_high_mid_shadow_v1.py"
SPEC = importlib.util.spec_from_file_location("d1_yes_high_mid_shadow_v1_test", MODULE_PATH)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def snapshot_file(root: Path, stamp: str, *, complete: bool, age_sec: float = 0.0) -> Path:
    path = root / "orderbook_snapshots" / stamp[:8] / f"orderbook_snapshot_{stamp}.jsonl.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    if complete:
        marker = root / "paper_snapshots" / f"snapshot_{stamp}.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("{}")
    ts = time.time() - age_sec
    os.utime(path, (ts, ts))
    return path


def test_full_ladder_ignores_newer_incomplete_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "full_ladder_output"
    completed = snapshot_file(root, "20260715_1502", complete=True, age_sec=60)
    snapshot_file(root, "20260715_1533", complete=False, age_sec=0)

    assert runner._latest_in_dir(root / "orderbook_snapshots", require_complete=True) == completed


def test_priority_prefers_completed_full_ladder_over_fresher_targeted(tmp_path: Path) -> None:
    full_root = tmp_path / "full_ladder_output"
    targeted_root = tmp_path / "targeted_output"
    completed = snapshot_file(full_root, "20260715_1502", complete=True, age_sec=60)
    snapshot_file(targeted_root, "20260715_1533", complete=False, age_sec=0)

    assert runner.latest_orderbook_file(
        [full_root / "orderbook_snapshots", targeted_root / "orderbook_snapshots"]
    ) == completed


def test_stale_full_ladder_falls_back_to_targeted(tmp_path: Path) -> None:
    full_root = tmp_path / "full_ladder_output"
    targeted_root = tmp_path / "targeted_output"
    snapshot_file(full_root, "20260715_1400", complete=True, age_sec=2500)
    targeted = snapshot_file(targeted_root, "20260715_1533", complete=False, age_sec=30)

    assert runner.latest_orderbook_file(
        [full_root / "orderbook_snapshots", targeted_root / "orderbook_snapshots"]
    ) == targeted


def test_all_stale_sources_fail_closed(tmp_path: Path) -> None:
    full_root = tmp_path / "full_ladder_output"
    targeted_root = tmp_path / "targeted_output"
    snapshot_file(full_root, "20260715_1400", complete=True, age_sec=2500)
    snapshot_file(targeted_root, "20260715_1401", complete=False, age_sec=2500)

    assert runner.latest_orderbook_file(
        [full_root / "orderbook_snapshots", targeted_root / "orderbook_snapshots"]
    ) is None


def test_coverage_requires_36_book_cities_for_target_dates(tmp_path: Path) -> None:
    full = snapshot_file(tmp_path / "full_ladder_output", "20260715_1502", complete=True)
    targeted = snapshot_file(tmp_path / "targeted_output", "20260715_1533", complete=False)

    assert runner.coverage_note(full, 35) == "full_ladder_partial"
    assert runner.coverage_note(full, 36) == "full_ladder"
    assert runner.coverage_note(targeted, 36) == "narrow_targeted_coverage"


def quote(token: str) -> dict[str, object]:
    return {"token_id": token, "ask": 0.5, "bid": 0.49}


def test_fahrenheit_current_is_anchored_before_selecting_d1() -> None:
    ladder = {
        "92-93": {"yes": quote("y92"), "no": quote("n92")},
        "94-95": {"yes": quote("y94"), "no": quote("n94")},
        "96-97": {"yes": quote("y96"), "no": quote("n96")},
    }

    current, d1, d1_no, d1_yes = runner.find_current_and_d1(ladder, 93.92, "F")

    assert current == "94-95"
    assert d1 == "96-97"
    assert d1_no == ladder["96-97"]["no"]
    assert d1_yes == ladder["96-97"]["yes"]


def test_missing_current_bracket_fails_closed() -> None:
    ladder = {"38": {"yes": quote("y38"), "no": quote("n38")}}

    assert runner.find_current_and_d1(ladder, 36.1, "C") == (None, None, None, None)


def test_missing_intermediate_bracket_does_not_turn_d2_into_d1() -> None:
    ladder = {
        "37": {"yes": quote("y37"), "no": quote("n37")},
        "39+": {"yes": quote("y39"), "no": quote("n39")},
    }

    assert runner.find_current_and_d1(ladder, 37.1, "C") == ("37", None, None, None)


def test_exact_celsius_current_selects_immediate_higher_bracket() -> None:
    ladder = {
        "37": {"yes": quote("y37"), "no": quote("n37")},
        "38": {"yes": quote("y38"), "no": quote("n38")},
    }

    current, d1, _, d1_yes = runner.find_current_and_d1(ladder, 37.1, "C")

    assert current == "37"
    assert d1 == "38"
    assert d1_yes == ladder["38"]["yes"]


def test_observation_invariant_blocks_station_day_running_max_regression() -> None:
    previous = {
        "city": "Singapore",
        "target_date": "2026-07-19",
        "station": "WSSS",
        "running_max_c": 32.0,
        "last_obs_utc": "2026-07-19T07:00:00Z",
    }
    rec = {
        "city": "Singapore",
        "target_date": "2026-07-19",
        "station": "WSSS",
        "current_temp_c": 31.0,
        "running_max_c": 31.0,
        "last_obs_utc": "2026-07-19T08:00:00Z",
    }

    reason, state, is_new = runner.advance_observation_invariant("Singapore", rec, previous)

    assert reason == "station_day_running_max_regressed"
    assert state["running_max_c"] == 32.0
    assert is_new is True


def test_observation_invariant_resets_on_new_local_date() -> None:
    previous = {
        "city": "Singapore",
        "target_date": "2026-07-19",
        "station": "WSSS",
        "running_max_c": 32.0,
    }
    rec = {
        "city": "Singapore",
        "target_date": "2026-07-20",
        "station": "WSSS",
        "current_temp_c": 27.0,
        "running_max_c": 27.0,
        "last_obs_utc": "2026-07-19T16:00:00Z",
    }

    reason, state, is_new = runner.advance_observation_invariant("Singapore", rec, previous)

    assert reason == ""
    assert state["running_max_c"] == 27.0
    assert is_new is False


def test_observation_cache_freshness_rejects_stalled_or_future_cache() -> None:
    now = datetime(2026, 7, 20, 2, 0, tzinfo=timezone.utc)

    fresh_age, fresh = runner.observation_cache_freshness("2026-07-20T01:55:00Z", now, 10.0)
    stale_age, stale = runner.observation_cache_freshness("2026-07-20T01:49:00Z", now, 10.0)
    future_age, future = runner.observation_cache_freshness("2026-07-20T02:01:00Z", now, 10.0)

    assert fresh_age == 5.0 and fresh is True
    assert stale_age == 11.0 and stale is False
    assert future_age == -1.0 and future is False


def test_successful_live_orders_excludes_failed_submission(tmp_path: Path) -> None:
    path = tmp_path / "live_orders.jsonl"
    rows = [
        {"status": "submitted", "exchange_response": {"place": {"success": True, "orderID": "ok"}}},
        {"status": "submitted", "exchange_response": {"place": {"success": False, "orderID": "bad"}}},
        {"status": "failed", "exchange_response": {"place": {"success": True, "orderID": "no"}}},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    order_ids = [
        row["exchange_response"]["place"]["orderID"]
        for row in runner.successful_live_orders(path)
    ]
    assert order_ids == ["ok"]


def test_daily_cap_counts_distinct_city_days_not_split_or_reprice_children(
    tmp_path: Path, monkeypatch
) -> None:
    cycle = datetime(2026, 7, 17, 6, 0, tzinfo=timezone.utc)
    path = tmp_path / "live_orders.jsonl"
    rows = [
        {
            "status": "submitted",
            "created_at_utc": "2026-07-17T05:00:00Z",
            "city": "Amsterdam",
            "target_date": "2026-07-17",
            "child_order_role": "taker",
            "posted_notional": 4.5,
        },
        {
            "status": "submitted",
            "created_at_utc": "2026-07-17T05:00:01Z",
            "city": "Amsterdam",
            "target_date": "2026-07-17",
            "child_order_role": "maker",
            "posted_notional": 4.4,
        },
        {
            "status": "submitted",
            "created_at_utc": "2026-07-17T05:01:00Z",
            "city": "Amsterdam",
            "target_date": "2026-07-17",
            "execution_action": "d1_maker_reprice",
            "posted_notional": 4.45,
        },
        {
            "status": "submitted",
            "created_at_utc": "2026-07-17T05:02:00Z",
            "city": "Jeddah",
            "target_date": "2026-07-17",
            "child_order_role": "taker",
            "posted_notional": 4.0,
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    monkeypatch.setattr(runner, "LIVE_OUT", path)

    city_days, root_cost = runner.live_daily_usage(cycle)

    assert city_days == 2
    assert root_cost == 12.9


def test_apply_live_fill_basis_scales_pnl_to_actual_shares() -> None:
    position = {"execution_mode": "tiny_live_taker_5shares"}
    order = {
        "exchange_response": {
            "place": {
                "success": True,
                "status": "matched",
                "orderID": "clob-1",
                "takingAmount": "5",
                "makingAmount": "4.2",
            }
        }
    }
    runner.apply_live_fill_basis(position, order)
    assert position["position_shares"] == 5.0
    assert position["fill_price"] == 0.84
    assert position["entry_cost_usd"] == 4.2
    assert position["entry_cost_with_fee"] == 4.2336
    assert round(5.0 - position["entry_cost_with_fee"], 6) == 0.7664


def test_apply_live_fill_basis_does_not_invent_fill_for_resting_order() -> None:
    position = {"execution_mode": "tiny_live_split_5_taker_5_maker"}
    order = {
        "size": 5.0,
        "posted_notional": 4.5,
        "exchange_response": {
            "place": {
                "success": True,
                "status": "live",
                "orderID": "clob-resting",
            }
        },
    }

    runner.apply_live_fill_basis(position, order)

    assert position["position_shares"] == 0.0
    assert position["entry_cost_usd"] == 0.0
    assert position["pnl_basis"] == "canonical_fill_reconcile_required_non_immediate"


def test_reconcile_aggregates_taker_and_delayed_maker_fill_once() -> None:
    position = {
        "city": "Madrid",
        "target_date": "2026-07-18",
        "d1_bracket": "36",
        "execution_mode": "tiny_live_split_5_taker_5_maker",
    }
    taker = {
        "status": "submitted",
        "city": "Madrid",
        "target_date": "2026-07-18",
        "bracket": "36",
        "signal_side": "BUY_YES",
        "child_order_role": "taker",
        "exchange_response": {"place": {"status": "matched", "orderID": "taker-1", "takingAmount": "5", "makingAmount": "4.05"}},
    }
    maker = {
        "status": "submitted",
        "city": "Madrid",
        "target_date": "2026-07-18",
        "bracket": "36",
        "signal_side": "BUY_YES",
        "child_order_role": "maker",
        "maker_only": True,
        "exchange_response": {"place": {"status": "live", "orderID": "maker-1"}},
    }
    terminal_probe = {
        "status": "blocked",
        "city": "Madrid",
        "target_date": "2026-07-18",
        "bracket": "36",
        "signal_side": "BUY_YES",
        "execution_action": "d1_maker_cancel_after_observation",
        "source_order_id": "maker-1",
        "exchange_response": {
            "error_classification": "cancel_only_not_confirmed",
            "pre_place_cancel_response": {
                "order_after_cancel": {
                    "id": "maker-1",
                    "status": "MATCHED",
                    "original_size": "5",
                    "size_matched": "5",
                    "price": "0.8",
                }
            },
        },
    }

    assert runner.reconcile_live_positions(position_map := {"madrid": position}, [taker, maker, terminal_probe]) == 1
    assert position_map["madrid"]["position_shares"] == 10.0
    assert position_map["madrid"]["entry_cost_usd"] == 8.05
    assert position_map["madrid"]["fill_price"] == 0.805
    assert position_map["madrid"]["estimated_fee_usd"] == round(5 * runner.fee(0.81), 6)
    assert position_map["madrid"]["clob_order_ids"] == ["maker-1", "taker-1"]
    assert runner.reconcile_live_positions(position_map, [taker, maker, terminal_probe]) == 0


def test_terminal_maker_cancel_probe_is_handled_not_retried() -> None:
    row = {
        "execution_action": "d1_maker_cancel_after_observation",
        "source_order_id": "maker-1",
        "exchange_response": {
            "error_classification": "cancel_only_not_confirmed",
            "pre_place_cancel_response": {
                "order_after_cancel": {"id": "maker-1", "status": "MATCHED"}
            },
        },
    }

    assert runner.handled_maker_source_order_ids([row]) == {"maker-1"}


def test_live_signal_expands_to_five_taker_plus_five_maker() -> None:
    args = Namespace(
        shares=5.0,
        maker_shares=5.0,
        order_ttl_min=45.0,
        mid_threshold=0.80,
        maker_cancel_buffer_sec=90,
        execution_profile=runner.DEFAULT_D1_EXECUTION_PROFILE,
    )
    event = {
        "city": "Amsterdam",
        "target_date": "2026-07-16",
        "d1_bracket": "25",
        "d1_yes_token_id": "yes-25",
        "d1_yes_direct_ask": 0.93,
        "d1_yes_direct_bid": 0.90,
        "d1_yes_mid": 0.915,
        "obs_cadence_min": 30.0,
        "obs_source": "aviationweather_metar",
        "obs_station": "EHAM",
        "last_obs_utc": "2026-07-16T10:00:00Z",
    }
    cycle = datetime(2026, 7, 16, 10, 5, tzinfo=timezone.utc)

    plans = runner.build_live_plans(event, args, cycle)

    assert [(plan["child_order_role"], plan["size"]) for plan in plans] == [
        ("taker", 5.0),
        ("maker", 5.0),
    ]
    assert plans[0]["signal_id"] == plans[1]["signal_id"]
    assert plans[0]["comparison_group_id"] == plans[1]["comparison_group_id"]
    assert plans[0]["plan_id"] != plans[1]["plan_id"]
    assert plans[0]["maker_only"] is False
    assert plans[1]["maker_only"] is True
    assert plans[0]["execution_profile"] == runner.DEFAULT_D1_EXECUTION_PROFILE
    assert plans[1]["execution_profile"] == runner.DEFAULT_D1_EXECUTION_PROFILE
    assert plans[1]["execution_policy"] == "d1_yes_high_mid_maker_v1"
    assert plans[1]["limit_price"] == 0.901
    assert plans[1]["order_ttl_min"] == 23.5
    assert plans[1]["order_lifecycle_policy"] == "maker_until_data_update"
    assert plans[1]["maker_price_cap"] == 0.915
    assert plans[1]["max_live_price"] == 0.915
    assert plans[1]["maker_reprice_policy"] == "follow_best_bid"
    assert plans[1]["maker_max_reprices"] == 3
    assert plans[1]["maker_lifecycle_root_observation_utc"] == "2026-07-16T10:00:00Z"
    assert plans[1]["data_epoch_ts_utc"] == "2026-07-16T10:00:00+00:00"
    assert plans[1]["next_data_update_due_utc"] == "2026-07-16T10:30:00+00:00"
    assert plans[1]["cancel_before_data_update_utc"] == "2026-07-16T10:28:30+00:00"
    assert plans[1]["expires_at_utc"] == plans[1]["cancel_before_data_update_utc"]
    assert event["maker_plan_status"] == "planned"


def test_pre_update_blackout_keeps_taker_and_blocks_only_maker() -> None:
    args = Namespace(
        shares=5.0,
        maker_shares=5.0,
        order_ttl_min=45.0,
        mid_threshold=0.80,
        maker_cancel_buffer_sec=90,
        execution_profile=runner.DEFAULT_D1_EXECUTION_PROFILE,
    )
    event = {
        "city": "Wellington",
        "target_date": "2026-07-17",
        "d1_bracket": "15",
        "d1_yes_token_id": "yes-15",
        "d1_yes_direct_ask": 0.93,
        "d1_yes_direct_bid": 0.87,
        "d1_yes_mid": 0.90,
        "obs_cadence_min": 30.0,
        "obs_source": "aviationweather_metar",
        "obs_station": "NZWN",
        "last_obs_utc": "2026-07-17T00:30:00Z",
    }

    plans = runner.build_live_plans(
        event,
        args,
        datetime(2026, 7, 17, 1, 2, tzinfo=timezone.utc),
    )

    assert [plan["child_order_role"] for plan in plans] == ["taker"]
    assert event["maker_plan_status"] == "blocked"
    assert "pre-data-update blackout" in event["maker_plan_blocker"]


def test_taker_only_profile_disables_maker_without_changing_taker() -> None:
    args = Namespace(
        shares=5.0,
        maker_shares=5.0,
        order_ttl_min=45.0,
        mid_threshold=0.80,
        maker_cancel_buffer_sec=90,
        execution_profile="d1_taker_only_v1",
    )
    event = {
        "city": "Amsterdam",
        "target_date": "2026-07-16",
        "d1_bracket": "25",
        "d1_yes_token_id": "yes-25",
        "d1_yes_direct_ask": 0.93,
        "d1_yes_direct_bid": 0.90,
        "d1_yes_mid": 0.915,
    }

    plans = runner.build_live_plans(
        event,
        args,
        datetime(2026, 7, 16, 10, 5, tzinfo=timezone.utc),
    )

    assert [plan["child_order_role"] for plan in plans] == ["taker"]
    assert event["maker_plan_status"] == "disabled"
    assert event["maker_plan_blocker"] == "execution_profile_has_no_maker_leg"


def test_shared_d1_engine_parity_preserves_split_child_contracts_from_legacy_plans() -> None:
    args = Namespace(
        shares=5.0,
        maker_shares=5.0,
        order_ttl_min=45.0,
        mid_threshold=0.80,
        maker_cancel_buffer_sec=90,
        execution_profile=runner.DEFAULT_D1_EXECUTION_PROFILE,
    )
    event = {
        "city": "Amsterdam",
        "target_date": "2026-07-16",
        "d1_bracket": "25",
        "d1_yes_token_id": "yes-25",
        "d1_yes_direct_ask": 0.93,
        "d1_yes_direct_bid": 0.90,
        "d1_yes_mid": 0.915,
        "obs_cadence_min": 30.0,
        "obs_source": "aviationweather_metar",
        "obs_station": "EHAM",
        "last_obs_utc": "2026-07-16T10:00:00Z",
    }
    cycle = datetime(2026, 7, 16, 10, 5, tzinfo=timezone.utc)

    legacy = runner.build_live_plans(event, args, cycle)
    shared = runner.build_shared_d1_plan_parity(legacy, event)

    assert len(shared.children) == len(legacy) == 2
    assert shared.legacy_plan_ids == tuple(plan["plan_id"] for plan in legacy)
    for legacy_plan, intent, child in zip(legacy, shared.intents, shared.children):
        assert child.child_role == legacy_plan["child_order_role"]
        assert float(child.requested_shares) == legacy_plan["size"]
        assert child.maker_only is legacy_plan["maker_only"]
        assert child.execution_policy == legacy_plan["execution_policy"]
        assert intent.metadata["legacy_plan_id"] == legacy_plan["plan_id"]
        assert intent.execution_profile == legacy_plan["execution_profile"]
        assert intent.resolved_execution_profile == legacy_plan["execution_profile"]
        assert intent.venue_side == legacy_plan["order_side"] == "BUY"
        assert intent.outcome_side == "YES"
        assert float(intent.strategy_price_cap) == (
            legacy_plan["maker_price_cap"] if legacy_plan["maker_only"] else legacy_plan["limit_price"]
        )
        assert intent.constraints.deadline_utc == legacy_plan["expires_at_utc"]
        assert intent.metadata["legacy_order_ttl_min"] == str(legacy_plan["order_ttl_min"])
    assert shared.children[1].requested_shares == shared.intents[1].total_shares
    assert shared.intents[1].data_epoch_ref == legacy[1]["data_epoch_ref"]
    assert shared.intents[1].next_data_update_due_utc == legacy[1]["next_data_update_due_utc"]


def test_shared_d1_engine_parity_keeps_taker_only_and_maker_disabled_legacy_shapes() -> None:
    event = {
        "city": "Amsterdam",
        "target_date": "2026-07-16",
        "d1_bracket": "25",
        "d1_yes_token_id": "yes-25",
        "d1_yes_direct_ask": 0.93,
        "d1_yes_direct_bid": 0.90,
        "d1_yes_mid": 0.915,
        "obs_cadence_min": 30.0,
        "obs_source": "aviationweather_metar",
        "obs_station": "EHAM",
        "last_obs_utc": "2026-07-16T10:00:00Z",
    }
    cycle = datetime(2026, 7, 16, 10, 5, tzinfo=timezone.utc)
    for execution_profile, maker_shares in (("d1_taker_only_v1", 5.0), (runner.DEFAULT_D1_EXECUTION_PROFILE, 0.0)):
        args = Namespace(
            shares=5.0,
            maker_shares=maker_shares,
            order_ttl_min=45.0,
            mid_threshold=0.80,
            maker_cancel_buffer_sec=90,
            execution_profile=execution_profile,
        )
        legacy_event = dict(event)
        legacy = runner.build_live_plans(legacy_event, args, cycle)
        shared = runner.build_shared_d1_plan_parity(legacy, legacy_event)

        assert [child.child_role for child in shared.children] == ["taker"]
        assert [float(child.requested_shares) for child in shared.children] == [5.0]
        assert shared.intents[0].strategy_price_cap == shared.intents[0].constraints.price_cap
        assert shared.intents[0].execution_profile == execution_profile
        assert shared.intents[0].venue_side == "BUY"


def test_d1_non_live_uses_shared_runtime_and_restart_dedupe(tmp_path: Path) -> None:
    args = Namespace(shares=5.0, maker_shares=5.0, order_ttl_min=45.0, mid_threshold=0.80)
    event = {
        "city": "Amsterdam",
        "target_date": "2026-07-16",
        "d1_bracket": "25",
        "d1_yes_token_id": "yes-25",
        "d1_yes_direct_ask": 0.93,
        "d1_yes_direct_bid": 0.90,
        "d1_yes_mid": 0.915,
        "obs_source": "knmi",
        "obs_cadence_min": 10.0,
        "minutes_to_next_obs": 12.0,
        "last_obs_utc": "2026-07-16T10:00:00Z",
    }
    plans = runner.build_live_plans(
        event,
        args,
        datetime(2026, 7, 16, 10, 1, tzinfo=timezone.utc),
    )
    compatibility = build_d1_legacy_plan_compatibility(legacy_plans=plans, event=event)
    kwargs = {
        "compatibility": compatibility,
        "legacy_plans": {str(plan["plan_id"]): plan for plan in plans},
        "journal_path": tmp_path / "execution.jsonl",
        "strategy_instance": runner.STRATEGY_ID,
        "code_commit": "test",
    }

    first = execute_legacy_compatibility_paper(
        **kwargs, generated_at_utc="2026-08-02T10:00:00Z"
    )
    second = execute_legacy_compatibility_paper(
        **kwargs, generated_at_utc="2026-08-02T10:01:00Z"
    )

    assert first["submitted"] == first["venue_calls"] == 2
    assert second["deduped"] == 2
    assert second["venue_calls"] == 0


def _resting_maker(now: datetime) -> dict:
    return {
        "status": "submitted",
        "strategy_instance": runner.STRATEGY_ID,
        "city": "Amsterdam",
        "target_date": "2026-07-16",
        "bracket": "25",
        "token_id": "yes-25",
        "signal_id": "signal-1",
        "comparison_group_id": "group-1",
        "maker_only": True,
        "size": 5.0,
        "posted_price": 0.901,
        "maker_price_cap": 0.915,
        "execution_profile": runner.DEFAULT_D1_EXECUTION_PROFILE,
        "order_lifecycle_policy": "maker_until_data_update",
        "data_epoch_ref": "aviationweather_metar:eham:2026-07-16T10:00:00Z",
        "data_epoch_ts_utc": "2026-07-16T10:00:00Z",
        "next_data_update_due_utc": (now + timedelta(minutes=42)).isoformat(),
        "cancel_before_data_update_utc": (now + timedelta(minutes=40)).isoformat(),
        "cancel_buffer_sec": 120,
        "cancel_reason": "pre_data_update",
        "created_at_utc": (now - timedelta(minutes=2)).isoformat(),
        "expires_at_utc": (now + timedelta(minutes=40)).isoformat(),
        "exchange_response": {
            "place": {"success": True, "status": "live", "orderID": "maker-1"}
        },
    }


def _lifecycle_args() -> Namespace:
    return Namespace(
        maker_shares=5.0,
        maker_reprice_refresh_sec=60.0,
        mid_threshold=0.80,
        execution_profile=runner.DEFAULT_D1_EXECUTION_PROFILE,
    )


def test_chase_profile_reprices_upward_but_stops_at_initial_mid() -> None:
    now = datetime(2026, 7, 16, 10, 5, tzinfo=timezone.utc)
    state = {
        "state_valid": True,
        "triggered": True,
        "observation_epoch_utc": "2026-07-16T10:00:00Z",
        "d1_yes_token_id": "yes-25",
        "d1_yes_mid": 0.925,
        "d1_yes_direct_bid": 0.92,
        "d1_yes_direct_ask": 0.93,
        "d1_yes_ask_size": 10.0,
    }

    plans, decisions = runner.maker_lifecycle_plans(
        live_rows=[_resting_maker(now)],
        latest_states={"Amsterdam|2026-07-16": state},
        args=_lifecycle_args(),
        cycle_dt=now,
    )

    assert decisions[0]["action"] == "d1_maker_reprice"
    assert decisions[0]["next_price"] == 0.915
    assert plans[0]["limit_price"] == 0.915
    assert plans[0]["maker_only"] is True
    assert plans[0]["cancel_only"] is False
    assert plans[0]["replacement_requires_order_state"] is True


def test_static_profile_rests_without_chasing() -> None:
    now = datetime(2026, 7, 16, 10, 5, tzinfo=timezone.utc)
    order = _resting_maker(now)
    order["execution_profile"] = "d1_taker_plus_maker_static_v1"
    state = {
        "state_valid": True,
        "triggered": True,
        "observation_epoch_utc": "2026-07-16T10:00:00Z",
        "d1_yes_token_id": "yes-25",
        "d1_yes_mid": 0.925,
        "d1_yes_direct_bid": 0.92,
        "d1_yes_direct_ask": 0.93,
    }

    plans, decisions = runner.maker_lifecycle_plans(
        live_rows=[order],
        latest_states={"Amsterdam|2026-07-16": state},
        args=_lifecycle_args(),
        cycle_dt=now,
    )

    assert decisions[0]["action"] == ""
    assert decisions[0]["maker_reprice_policy"] == "none"
    assert decisions[0]["blocker"] == "d1_maker_resting_until_pre_data_update_deadline"
    assert plans == []


def test_chase_reprice_cancel_replace_preserves_maker_size(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    state = {
        "state_valid": True,
        "triggered": True,
        "observation_epoch_utc": "2026-07-16T10:00:00Z",
        "d1_yes_token_id": "yes-25",
        "d1_yes_mid": 0.925,
        "d1_yes_direct_bid": 0.92,
        "d1_yes_direct_ask": 0.93,
    }
    plans, _ = runner.maker_lifecycle_plans(
        live_rows=[_resting_maker(now)],
        latest_states={"Amsterdam|2026-07-16": state},
        args=_lifecycle_args(),
        cycle_dt=now,
    )
    plan_path = tmp_path / "plans.jsonl"
    plan_path.write_text(json.dumps(plans[0]) + "\n", encoding="utf-8")
    placed: list[dict] = []

    result = execute_trade_plans(
        plan_path=plan_path,
        paper_out=tmp_path / "paper.jsonl",
        live_out=tmp_path / "live.jsonl",
        config=ExecutorConfig(live=True, confirm_live=True),
        live_place_fn=lambda child: placed.append(child) or {"order_id": "maker-2"},
        live_cancel_fn=lambda order_id: {
            "cancel": {"canceled": [order_id], "not_canceled": {}},
            "order_after_cancel": {"original_size": "5", "size_matched": "0"},
        },
    )

    assert result["live_guard_blocks"] == 0
    assert len(placed) == 1
    assert placed[0]["size"] == 5.0
    assert placed[0]["maker_only"] is True
    assert placed[0]["execution_action"] == "d1_maker_reprice"


def test_new_observation_cancels_unfilled_maker_without_taker_fallback() -> None:
    now = datetime(2026, 7, 16, 10, 35, tzinfo=timezone.utc)
    state = {
        "state_valid": True,
        "triggered": True,
        "observation_epoch_utc": "2026-07-16T10:30:00Z",
        "d1_yes_token_id": "yes-25",
        "d1_yes_mid": 0.92,
        "d1_yes_direct_bid": 0.915,
        "d1_yes_direct_ask": 0.925,
        "d1_yes_ask_size": 10.0,
    }

    plans, decisions = runner.maker_lifecycle_plans(
        live_rows=[_resting_maker(now)],
        latest_states={"Amsterdam|2026-07-16": state},
        args=_lifecycle_args(),
        cycle_dt=now,
    )

    assert decisions[0]["action"] == "d1_maker_cancel_after_observation"
    assert decisions[0]["blocker"] == "data_epoch_changed_no_taker_fallback"
    assert plans[0]["maker_only"] is True
    assert plans[0]["cancel_only"] is True
    assert plans[0]["limit_price"] == 0.0
    assert plans[0]["replacement_requires_order_state"] is False


def test_new_observation_plan_only_cancels_and_never_places_taker(tmp_path: Path) -> None:
    now = datetime(2026, 7, 16, 10, 35, tzinfo=timezone.utc)
    state = {
        "state_valid": True,
        "triggered": True,
        "observation_epoch_utc": "2026-07-16T10:30:00Z",
        "d1_yes_token_id": "yes-25",
        "d1_yes_mid": 0.92,
        "d1_yes_direct_bid": 0.915,
        "d1_yes_direct_ask": 0.925,
        "d1_yes_ask_size": 10.0,
    }
    plans, _ = runner.maker_lifecycle_plans(
        live_rows=[_resting_maker(now)],
        latest_states={"Amsterdam|2026-07-16": state},
        args=_lifecycle_args(),
        cycle_dt=now,
    )
    plan_path = tmp_path / "plans.jsonl"
    plan_path.write_text(json.dumps(plans[0]) + "\n", encoding="utf-8")
    placed: list[dict] = []
    cancelled: list[str] = []

    result = execute_trade_plans(
        plan_path=plan_path,
        paper_out=tmp_path / "paper.jsonl",
        live_out=tmp_path / "live.jsonl",
        config=ExecutorConfig(live=True, confirm_live=True),
        live_place_fn=lambda child: placed.append(child) or {"order_id": "fallback-1"},
        live_cancel_fn=lambda order_id: (
            cancelled.append(order_id)
            or {"cancel": {"canceled": [order_id], "not_canceled": {}}}
        ),
    )

    assert result["live_guard_blocks"] == 0
    assert placed == []
    assert cancelled == ["maker-1"]


def test_new_observation_cancels_without_fallback_when_signal_changes() -> None:
    now = datetime(2026, 7, 16, 10, 35, tzinfo=timezone.utc)
    state = {
        "state_valid": True,
        "triggered": False,
        "observation_epoch_utc": "2026-07-16T10:30:00Z",
        "d1_yes_token_id": "yes-26",
        "d1_yes_mid": 0.70,
        "d1_yes_direct_bid": 0.69,
        "d1_yes_direct_ask": 0.71,
        "d1_yes_ask_size": 20.0,
    }

    plans, decisions = runner.maker_lifecycle_plans(
        live_rows=[_resting_maker(now)],
        latest_states={"Amsterdam|2026-07-16": state},
        args=_lifecycle_args(),
        cycle_dt=now,
    )

    assert decisions[0]["action"] == "d1_maker_cancel_after_observation"
    assert plans[0]["cancel_only"] is True
    assert plans[0]["replacement_requires_order_state"] is False


def test_same_observation_cancels_when_market_signal_disappears() -> None:
    now = datetime(2026, 7, 16, 10, 5, tzinfo=timezone.utc)
    state = {
        "state_valid": True,
        "triggered": False,
        "observation_epoch_utc": "2026-07-16T10:00:00Z",
        "d1_yes_token_id": "yes-25",
        "d1_yes_mid": 0.79,
        "d1_yes_direct_bid": 0.78,
        "d1_yes_direct_ask": 0.80,
        "d1_yes_ask_size": 20.0,
    }

    plans, decisions = runner.maker_lifecycle_plans(
        live_rows=[_resting_maker(now)],
        latest_states={"Amsterdam|2026-07-16": state},
        args=_lifecycle_args(),
        cycle_dt=now,
    )

    assert decisions[0]["action"] == "d1_maker_cancel_stale_signal"
    assert plans[0]["cancel_only"] is True


def test_completed_lifecycle_child_prevents_replaying_same_source_order() -> None:
    now = datetime(2026, 7, 16, 10, 5, tzinfo=timezone.utc)
    root = _resting_maker(now)
    completed_reprice = {
        "status": "submitted",
        "execution_action": "d1_maker_reprice",
        "source_order_id": "maker-1",
        "exchange_response": {"place": {"success": True, "status": "live", "orderID": "maker-2"}},
    }
    state = {
        "state_valid": True,
        "triggered": True,
        "observation_epoch_utc": "2026-07-16T10:00:00Z",
        "d1_yes_token_id": "yes-25",
        "d1_yes_mid": 0.925,
        "d1_yes_direct_bid": 0.92,
        "d1_yes_direct_ask": 0.93,
        "d1_yes_ask_size": 10.0,
    }

    plans, _ = runner.maker_lifecycle_plans(
        live_rows=[root, completed_reprice],
        latest_states={"Amsterdam|2026-07-16": state},
        args=_lifecycle_args(),
        cycle_dt=now,
    )

    assert plans == []


def test_d1_maker_price_improves_bid_without_crossing() -> None:
    assert executor._d1_yes_maker_price(best_bid=0.90, best_ask=0.93, tick_size=0.001) == 0.901
    assert executor._d1_yes_maker_price(best_bid=0.90, best_ask=0.901, tick_size=0.001) == 0.90
    assert executor._d1_yes_maker_price(best_bid=0.0, best_ask=0.93, tick_size=0.001) == 0.0
