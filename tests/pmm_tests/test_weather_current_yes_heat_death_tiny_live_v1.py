from __future__ import annotations

import inspect
import json
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.ops import weather_current_yes_heat_death_tiny_live_v1 as live
from src.strategies.weather_edge_v1.execution.engine import (
    build_heat_death_legacy_plan_compatibility,
)
from src.strategies.weather_edge_v1.runtime.non_live import (
    execute_legacy_compatibility_paper,
)
from src.strategies.weather_edge_v1.tools.execution_pipeline import ExecutorConfig, execute_trade_plans


def _select_h2() -> None:
    live.ACTIVE_HEAD = "h2_early_dislocation"
    live.STRATEGY_INSTANCE = live.HEADS[live.ACTIVE_HEAD]["instance"]


def _select_h1() -> None:
    live.ACTIVE_HEAD = "h1_late_carry"
    live.STRATEGY_INSTANCE = live.HEADS[live.ACTIVE_HEAD]["instance"]


def _row(*, city: str = "Busan", ask: float = 0.84, ask_size: float = 20.0) -> dict:
    return {
        "city": city,
        "target_date": "2026-07-14",
        "decision_snapshot_ts_utc": "2026-07-14T04:08:00Z",
        "obs_status": "ok",
        "source_report_ts_utc": "2026-07-14T04:00:00Z",
        "obs_age_minutes": 8.0,
        "expected_report_cadence": 30.0,
        "station_gap_state": "within_expected_cadence",
        "physical_confirmation_strong": True,
        "current_bracket": "30",
        "current_market_id": "m30",
        "current_question": "Will Busan be 30C?",
        "current_yes_token_id": f"yes-{city}",
        "shadow_decision_id": f"shadow-{city}",
        "fresh_current_yes_ask": ask,
        "fresh_current_yes_ask_size": ask_size,
        "fresh_current_yes_bid": ask - 0.01,
        "fresh_current_yes_tick_size": 0.001,
        "fresh_current_yes_book_status": "ok",
        "fresh_current_yes_book_fetched_at_utc": "2026-07-14T04:08:05Z",
        "physical_support_count": 3,
    }


def _submitted_h1_maker(*, now: datetime, posted_price: float = 0.961, reprice_count: int = 0) -> dict:
    plans = live.build_opportunity_plans(
        _row(ask=0.97),
        taker_shares=5,
        maker_shares=5,
        live_enabled=True,
        ttl_min=15,
        maker_chase_window_min=3,
    )
    maker = plans[1]
    return {
        **maker,
        "status": "submitted",
        "created_at_utc": (now - timedelta(seconds=60)).isoformat(),
        "posted_price": posted_price,
        "maker_price_cap": 0.97,
        "maker_lifecycle_root_created_at_utc": (now - timedelta(seconds=60)).isoformat(),
        "maker_lifecycle_deadline_utc": (now + timedelta(minutes=2)).isoformat(),
        "maker_lifecycle_reprice_count": reprice_count,
        "exchange_response": {"place": {"orderID": "maker-order-1"}},
    }


def test_signal_id_dedupes_snapshot_and_bracket() -> None:
    _select_h2()
    left = _row()
    right = {**left, "snapshot_file": "new.json", "current_bracket": "31"}
    assert live.signal_id(left) == live.signal_id(right)


def test_h2_builds_five_taker_plus_five_post_only_maker() -> None:
    _select_h2()
    plans = live.build_opportunity_plans(
        _row(ask=0.92),
        taker_shares=5,
        maker_shares=5,
        live_enabled=True,
        ttl_min=15,
    )
    assert live.HEADS["h2_early_dislocation"]["total_shares"] == 10
    assert [(plan["child_order_role"], plan["size"]) for plan in plans] == [("taker", 5), ("maker", 5)]
    taker, maker = plans
    assert taker["limit_price"] == 0.92
    assert taker["maker_only"] is False
    assert maker["limit_price"] == 0.911
    assert maker["maker_only"] is True
    assert taker["execution_policy"] == "current_yes_heat_death_taker_probe_v1"
    assert maker["execution_policy"] == "current_yes_heat_death_maker_probe_v1"
    assert taker["execution_profile"] == "split_taker_maker_chase_v1"
    assert maker["execution_profile"] == "split_taker_maker_chase_v1"
    assert taker["comparison_group_id"] == maker["comparison_group_id"]
    assert taker["order_lifecycle_policy"] == "taker_now"
    assert maker["order_lifecycle_policy"] == "maker_chase_then_taker_fallback_v1"
    assert maker["maker_price_cap"] == 0.92
    assert taker["signal_id"] == maker["signal_id"]
    assert taker["allow_duplicate_signal_id"] is True
    assert maker["allow_duplicate_signal_id"] is True


def test_h2_non_live_uses_shared_runtime_and_restart_dedupe(tmp_path: Path) -> None:
    _select_h2()
    plans = live.build_opportunity_plans(
        _row(ask=0.92),
        taker_shares=5,
        maker_shares=5,
        live_enabled=False,
        ttl_min=15,
    )
    compatibility = build_heat_death_legacy_plan_compatibility(legacy_plans=plans)
    kwargs = {
        "compatibility": compatibility,
        "legacy_plans": {str(plan["plan_id"]): plan for plan in plans},
        "journal_path": tmp_path / "execution.jsonl",
        "strategy_instance": live.STRATEGY_INSTANCE,
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

def test_h1_builds_five_taker_plus_five_post_only_maker() -> None:
    _select_h1()
    plans = live.build_opportunity_plans(
        _row(ask=0.97),
        taker_shares=5,
        maker_shares=5,
        live_enabled=True,
        ttl_min=15,
    )
    assert [(plan["child_order_role"], plan["size"]) for plan in plans] == [("taker", 5), ("maker", 5)]
    taker, maker = plans
    assert taker["limit_price"] == 0.97
    assert taker["maker_only"] is False
    assert maker["limit_price"] == 0.961
    assert maker["maker_only"] is True
    assert taker["execution_profile"] == "split_taker_maker_chase_v1"
    assert maker["execution_profile"] == "split_taker_maker_chase_v1"
    assert taker["comparison_group_id"] == maker["comparison_group_id"]
    assert maker["execution_policy"] == "current_yes_heat_death_maker_probe_v1"
    assert maker["order_lifecycle_policy"] == "maker_chase_then_taker_fallback_v1"
    assert maker["maker_price_cap"] == 0.97
    assert maker["maker_lifecycle_reprice_count"] == 0
    assert maker["maker_lifecycle_deadline_utc"]
    assert taker["signal_id"] == maker["signal_id"]
    assert taker["opportunity_id"] == maker["opportunity_id"]
    assert taker["plan_id"] != maker["plan_id"]
    assert taker["allow_duplicate_signal_id"] is True
    assert maker["allow_duplicate_signal_id"] is True


def test_shared_heat_death_engine_parity_preserves_split_entry_children() -> None:
    _select_h1()
    legacy = live.build_opportunity_plans(
        _row(ask=0.97),
        taker_shares=5,
        maker_shares=5,
        live_enabled=False,
        ttl_min=15,
        maker_chase_window_min=3,
    )
    legacy_before = [dict(plan) for plan in legacy]

    shared = live.build_shared_heat_death_plan_parity(legacy)

    assert legacy == legacy_before
    assert shared.legacy_plan_ids == tuple(plan["plan_id"] for plan in legacy)
    assert [child.child_role for child in shared.children] == ["taker", "maker"]
    assert [float(child.requested_shares) for child in shared.children] == [5.0, 5.0]
    for legacy_plan, intent, child in zip(legacy, shared.intents, shared.children):
        assert child.maker_only is legacy_plan["maker_only"]
        assert child.execution_policy == legacy_plan["execution_policy"]
        assert child.order_lifecycle_policy == legacy_plan["order_lifecycle_policy"]
        assert intent.execution_profile == legacy_plan["execution_profile"]
        assert intent.resolved_execution_profile == legacy_plan["execution_profile"]
        assert intent.venue_side == legacy_plan["order_side"] == "BUY"
        assert intent.outcome_side == "YES"
        assert intent.signal_side == legacy_plan["signal_side"] == "BUY_YES"
        assert intent.metadata["legacy_plan_id"] == legacy_plan["plan_id"]
        assert intent.constraints.deadline_utc == legacy_plan["expires_at_utc"]
    maker_intent = shared.intents[1]
    assert float(maker_intent.constraints.price_cap) == legacy[1]["maker_price_cap"] == 0.97
    assert maker_intent.metadata["legacy_maker_lifecycle_deadline_utc"] == legacy[1]["maker_lifecycle_deadline_utc"]
    assert maker_intent.metadata["legacy_maker_fallback_eligible"] is True
    assert float(maker_intent.metadata["legacy_maker_fallback_price_cap"]) == 0.97
    assert shared.intents[0].metadata["legacy_maker_fallback_eligible"] is False
    assert shared.intents[0].metadata["legacy_maker_fallback_price_cap"] is None


def test_shared_heat_death_engine_parity_preserves_taker_only_entry() -> None:
    _select_h2()
    legacy = live.build_opportunity_plans(
        _row(ask=0.84),
        taker_shares=5,
        maker_shares=0,
        live_enabled=False,
        ttl_min=15,
    )

    shared = live.build_shared_heat_death_plan_parity(legacy)

    assert len(shared.children) == 1
    assert shared.children[0].child_role == legacy[0]["child_order_role"] == "single"
    assert shared.children[0].maker_only is False
    assert float(shared.children[0].requested_shares) == legacy[0]["size"] == 5.0
    assert shared.intents[0].execution_profile == "split_taker_maker_chase_v1"
    assert shared.intents[0].metadata["legacy_maker_fallback_eligible"] is False
    assert shared.intents[0].metadata["legacy_maker_fallback_price_cap"] is None


def test_shared_heat_death_entry_bridge_rejects_lifecycle_replacement_plan() -> None:
    _select_h1()
    lifecycle = live.build_h1_maker_lifecycle_plan(
        _submitted_h1_maker(now=datetime(2026, 7, 16, 6, 0, tzinfo=timezone.utc)),
        action="h1_maker_reprice",
        limit_price=0.969,
        maker_only=True,
        source_order_id="maker-order-1",
        live_enabled=False,
        now=datetime(2026, 7, 16, 6, 0, tzinfo=timezone.utc),
    )

    with pytest.raises(ValueError, match="rejects lifecycle replacement/cancel plans"):
        live.build_shared_heat_death_plan_parity([lifecycle])


def test_shared_heat_death_parity_bridge_is_not_called_by_normal_runner() -> None:
    assert "build_shared_heat_death_plan_parity" not in inspect.getsource(live.run_once)
    assert "build_shared_heat_death_plan_parity" not in inspect.getsource(live.execute_plans)


def test_h1_maker_chase_has_no_reprice_count_limit_and_never_exceeds_initial_ask(
    tmp_path: Path, monkeypatch
) -> None:
    _select_h1()
    now = datetime(2026, 7, 16, 6, 0, tzinfo=timezone.utc)
    order = _submitted_h1_maker(now=now, reprice_count=99)
    live_orders = tmp_path / "live.jsonl"
    live_orders.write_text(json.dumps(order) + "\n", encoding="utf-8")
    monkeypatch.setattr(live.shadow, "market_httpx_client", lambda *_args, **_kwargs: nullcontext(object()))
    monkeypatch.setattr(
        live.shadow,
        "_fetch_token_book",
        lambda *_args, **_kwargs: {
            "book_status": "ok",
            "bid": 0.975,
            "ask": 0.98,
            "ask_size": 20.0,
            "tick_size": 0.001,
        },
    )

    plans, decisions = live.h1_maker_lifecycle_plans(
        live_orders=live_orders,
        latest_rows={("Busan", "2026-07-14"): _row(ask=0.97)},
        live_enabled=True,
        refresh_sec=30,
        proxy=None,
        timeout_sec=5,
        now=now,
    )

    assert decisions[0]["action"] == "h1_maker_reprice"
    assert plans[0]["limit_price"] == 0.97
    assert plans[0]["maker_lifecycle_reprice_count"] == 100
    assert plans[0]["maker_price_cap"] == 0.97


def test_h1_maker_chase_falls_back_to_taker_after_window_when_price_not_worse(
    tmp_path: Path, monkeypatch
) -> None:
    _select_h1()
    now = datetime(2026, 7, 16, 6, 0, tzinfo=timezone.utc)
    order = {
        **_submitted_h1_maker(now=now),
        "maker_lifecycle_deadline_utc": (now - timedelta(seconds=1)).isoformat(),
    }
    live_orders = tmp_path / "live.jsonl"
    live_orders.write_text(json.dumps(order) + "\n", encoding="utf-8")
    monkeypatch.setattr(live.shadow, "market_httpx_client", lambda *_args, **_kwargs: nullcontext(object()))
    monkeypatch.setattr(
        live.shadow,
        "_fetch_token_book",
        lambda *_args, **_kwargs: {
            "book_status": "ok",
            "bid": 0.96,
            "ask": 0.965,
            "ask_size": 10.0,
            "tick_size": 0.001,
        },
    )

    plans, decisions = live.h1_maker_lifecycle_plans(
        live_orders=live_orders,
        latest_rows={("Busan", "2026-07-14"): _row(ask=0.97)},
        live_enabled=True,
        refresh_sec=30,
        proxy=None,
        timeout_sec=5,
        now=now,
    )

    assert decisions[0]["action"] == "h1_maker_taker_fallback"
    assert plans[0]["maker_only"] is False
    assert plans[0]["limit_price"] == 0.965
    assert plans[0]["cancel_before_order_id"] == "maker-order-1"


def test_h2_maker_chase_does_not_fallback_above_initial_ask_cap(tmp_path: Path, monkeypatch) -> None:
    _select_h2()
    now = datetime(2026, 7, 18, 6, 0, tzinfo=timezone.utc)
    maker = live.build_opportunity_plans(
        _row(city="KualaLumpur", ask=0.92),
        taker_shares=5,
        maker_shares=5,
        live_enabled=True,
        ttl_min=15,
        maker_chase_window_min=3,
    )[1]
    order = {
        **maker,
        "status": "submitted",
        "created_at_utc": (now - timedelta(minutes=4)).isoformat(),
        "posted_price": 0.911,
        "maker_lifecycle_deadline_utc": (now - timedelta(minutes=1)).isoformat(),
        "exchange_response": {"place": {"orderID": "h2-maker-order-1"}},
    }
    live_orders = tmp_path / "live.jsonl"
    live_orders.write_text(json.dumps(order) + "\n", encoding="utf-8")
    monkeypatch.setattr(live.shadow, "market_httpx_client", lambda *_args, **_kwargs: nullcontext(object()))
    monkeypatch.setattr(
        live.shadow,
        "_fetch_token_book",
        lambda *_args, **_kwargs: {
            "book_status": "ok",
            "bid": 0.93,
            "ask": 0.94,
            "ask_size": 20.0,
            "tick_size": 0.001,
        },
    )

    plans, decisions = live.h1_maker_lifecycle_plans(
        live_orders=live_orders,
        latest_rows={("KualaLumpur", "2026-07-14"): _row(city="KualaLumpur", ask=0.92)},
        live_enabled=True,
        refresh_sec=30,
        proxy=None,
        timeout_sec=5,
        now=now,
    )

    assert plans == []
    assert decisions[0]["action"] == ""
    assert decisions[0]["blocker"] == "h2_maker_fallback_price_or_depth_not_allowed"


def test_h1_maker_chase_cancel_replace_blocks_dust_after_partial_fill(tmp_path: Path) -> None:
    _select_h1()
    now = datetime(2026, 7, 16, 6, 0, tzinfo=timezone.utc)
    plan = live.build_h1_maker_lifecycle_plan(
        _submitted_h1_maker(now=now),
        action="h1_maker_taker_fallback",
        limit_price=0.965,
        maker_only=False,
        source_order_id="maker-order-1",
        live_enabled=True,
        now=now,
    )
    plans_path = tmp_path / "plans.jsonl"
    plans_path.write_text(json.dumps(plan) + "\n", encoding="utf-8")
    placed: list[dict] = []

    result = execute_trade_plans(
        plan_path=plans_path,
        paper_out=tmp_path / "paper.jsonl",
        live_out=tmp_path / "live.jsonl",
        config=ExecutorConfig(live=True, confirm_live=True),
        live_place_fn=lambda child: placed.append(child) or {"order_id": "replacement-1"},
        live_cancel_fn=lambda order_id: {
            "cancel": {"canceled": [order_id], "not_canceled": {}},
            "order_after_cancel": {"original_size": "5", "size_matched": "2"},
        },
    )

    assert result["live_guard_blocks"] == 1
    assert placed == []
    row = json.loads((tmp_path / "live.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["exchange_response"]["error_classification"] == "replacement_remaining_below_minimum_after_cancel"
    assert row["exchange_response"]["replacement_shares"] == 3.0


def test_h1_maker_chase_cancel_replace_places_authoritative_remaining_five(tmp_path: Path) -> None:
    _select_h1()
    now = datetime(2026, 7, 16, 6, 0, tzinfo=timezone.utc)
    plan = live.build_h1_maker_lifecycle_plan(
        _submitted_h1_maker(now=now),
        action="h1_maker_reprice",
        limit_price=0.969,
        maker_only=True,
        source_order_id="maker-order-1",
        live_enabled=True,
        now=now,
    )
    plans_path = tmp_path / "plans.jsonl"
    plans_path.write_text(json.dumps(plan) + "\n", encoding="utf-8")
    placed: list[dict] = []

    result = execute_trade_plans(
        plan_path=plans_path,
        paper_out=tmp_path / "paper.jsonl",
        live_out=tmp_path / "live.jsonl",
        config=ExecutorConfig(live=True, confirm_live=True),
        live_place_fn=lambda child: placed.append(child) or {"order_id": "replacement-1"},
        live_cancel_fn=lambda order_id: {
            "cancel": {"canceled": [order_id], "not_canceled": {}},
            "order_after_cancel": {"original_size": "5", "size_matched": "0"},
        },
    )

    assert result["live_guard_blocks"] == 0
    assert len(placed) == 1
    assert placed[0]["size"] == 5.0
    assert placed[0]["limit_price"] == 0.969


def test_h1_two_children_both_pass_executor_signal_dedupe(tmp_path: Path) -> None:
    _select_h1()
    plans = live.build_opportunity_plans(
        _row(ask=0.97),
        taker_shares=5,
        maker_shares=5,
        live_enabled=True,
        ttl_min=15,
    )
    plans_path = tmp_path / "plans.jsonl"
    plans_path.write_text("".join(json.dumps(plan) + "\n" for plan in plans), encoding="utf-8")
    placed_roles: list[str] = []

    def _place(plan: dict) -> dict:
        placed_roles.append(str(plan["child_order_role"]))
        return {"place": {"status": "live"}, "maker_only": bool(plan["maker_only"])}

    result = execute_trade_plans(
        plan_path=plans_path,
        paper_out=tmp_path / "paper.jsonl",
        live_out=tmp_path / "live.jsonl",
        config=ExecutorConfig(
            live=True,
            confirm_live=True,
            max_live_order_notional_usd=5.0,
            max_live_batch_notional_usd=10.0,
        ),
        live_place_fn=_place,
    )

    assert placed_roles == ["taker", "maker"]
    assert result["live_written"] == 2
    assert result["live_skipped_existing_signal"] == 0
    assert result["live_skipped_existing_opportunity"] == 0
    live_rows = [json.loads(line) for line in (tmp_path / "live.jsonl").read_text(encoding="utf-8").splitlines()]
    maker_row = next(row for row in live_rows if row["child_order_role"] == "maker")
    assert maker_row["config_id"] == live.HEADS["h1_late_carry"]["config_id"]
    assert maker_row["maker_price_cap"] == 0.97
    assert maker_row["maker_lifecycle_deadline_utc"]


def test_choose_plans_applies_depth_dedup_and_daily_cap(tmp_path: Path) -> None:
    _select_h2()
    live_orders = tmp_path / "live.jsonl"
    now = datetime(2026, 7, 14, 4, 10, tzinfo=timezone.utc)
    submitted = live.build_plan(
        _row(city="Busan"), shares=5, child_order_role="single", live_enabled=True, ttl_min=15
    )
    live_orders.write_text(
        json.dumps({**submitted, "status": "submitted", "created_at_utc": "2026-07-14T04:09:00Z"}) + "\n",
        encoding="utf-8",
    )
    rows = [
        _row(city="Busan"),
        _row(city="Jeddah", ask=0.88, ask_size=4),
        _row(city="PanamaCity", ask=0.98, ask_size=30),
        _row(city="Ankara", ask=0.991, ask_size=100),
        _row(city="CapeTown", ask=0.95, ask_size=30),
        _row(city="Atlanta", ask=0.96, ask_size=30),
    ]
    plans, counts = live.choose_plans(
        rows,
        live_orders=live_orders,
        taker_shares=5,
        maker_shares=0,
        min_ask=0.50,
        max_ask=0.99,
        min_top_ask_shares=5,
        max_orders_per_utc_day=3,
        live_enabled=True,
        ttl_min=15,
        now=now,
    )
    assert [row["city"] for row in plans] == ["CapeTown", "Atlanta"]
    assert counts["already_submitted_city_days"] == 1
    assert counts["insufficient_top_ask_depth"] == 1
    assert counts["ask_above_cap"] == 1
    assert counts["daily_cap"] == 1


def test_h2_legacy_order_counts_for_city_day_dedup_and_daily_cap(tmp_path: Path) -> None:
    _select_h2()
    live_orders = tmp_path / "h2_live.jsonl"
    legacy_orders = tmp_path / "legacy_live.jsonl"
    legacy_orders.write_text(
        json.dumps(
            {
                "record_type": "weather_edge_live_order",
                "strategy_instance": "current_yes_heat_death_tiny_live_v1",
                "status": "submitted",
                "city": "Guangzhou",
                "target_date": "2026-07-15",
                "created_at_utc": "2026-07-15T06:24:16Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    plans, counts = live.choose_plans(
        [
            {**_row(city="Guangzhou"), "target_date": "2026-07-15"},
            {**_row(city="Busan", ask=0.85), "target_date": "2026-07-15"},
        ],
        live_orders=live_orders,
        dedupe_live_orders=[legacy_orders],
        daily_cap_live_orders=[legacy_orders],
        taker_shares=5,
        maker_shares=0,
        min_ask=0.50,
        max_ask=0.93,
        min_top_ask_shares=5,
        max_orders_per_utc_day=2,
        live_enabled=True,
        ttl_min=15,
        now=datetime(2026, 7, 15, 7, 0, tzinfo=timezone.utc),
    )

    assert [row["city"] for row in plans] == ["Busan"]
    assert counts["already_submitted_city_days"] == 1
    assert counts["daily_cap"] == 0


def test_h1_pair_counts_as_one_daily_opportunity_and_h2_blocks_same_city_day(tmp_path: Path) -> None:
    _select_h1()
    h1_orders = tmp_path / "h1_live.jsonl"
    h2_orders = tmp_path / "h2_live.jsonl"
    existing_pair = live.build_opportunity_plans(
        {**_row(city="Munich", ask=0.97), "target_date": "2026-07-15"},
        taker_shares=5,
        maker_shares=5,
        live_enabled=True,
        ttl_min=15,
    )
    h1_orders.write_text(
        "".join(
            json.dumps({**plan, "status": "submitted", "created_at_utc": "2026-07-15T15:01:00Z"}) + "\n"
            for plan in existing_pair
        ),
        encoding="utf-8",
    )
    h2_orders.write_text(
        json.dumps(
            {
                "status": "submitted",
                "city": "Busan",
                "target_date": "2026-07-15",
                "created_at_utc": "2026-07-15T14:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    plans, counts = live.choose_plans(
        [
            {**_row(city="Busan", ask=0.97), "target_date": "2026-07-15"},
            {**_row(city="CapeTown", ask=0.98), "target_date": "2026-07-15"},
            {**_row(city="Atlanta", ask=0.98), "target_date": "2026-07-15"},
            {**_row(city="Milan", ask=0.98), "target_date": "2026-07-15"},
        ],
        live_orders=h1_orders,
        dedupe_live_orders=[h2_orders],
        taker_shares=5,
        maker_shares=5,
        min_ask=0.95,
        max_ask=0.99,
        min_top_ask_shares=5,
        max_orders_per_utc_day=3,
        live_enabled=True,
        ttl_min=15,
        now=datetime(2026, 7, 15, 16, 0, tzinfo=timezone.utc),
    )

    assert sorted({plan["city"] for plan in plans}) == ["Atlanta", "CapeTown"]
    assert len(plans) == 4
    assert counts["already_submitted_city_days"] == 1
    assert counts["daily_cap"] == 1


def test_latest_strong_rows_keeps_latest_fresh_city_day(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    rows = [
        {**_row(), "decision_snapshot_ts_utc": "2026-07-14T04:00:00Z"},
        {**_row(), "decision_snapshot_ts_utc": "2026-07-14T04:08:00Z"},
        {**_row(city="Old"), "decision_snapshot_ts_utc": "2026-07-14T03:00:00Z"},
        {**_row(city="Weak"), "physical_confirmation_strong": False},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    selected = live.latest_strong_rows(
        path,
        max_snapshot_age_min=20,
        now=datetime(2026, 7, 14, 4, 10, tzinfo=timezone.utc),
    )
    assert [(row["city"], row["decision_snapshot_ts_utc"]) for row in selected] == [
        ("Busan", "2026-07-14T04:08:00Z")
    ]


def test_decision_state_index_only_parses_appended_rows(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "decisions.jsonl"
    first = {**_row(city="Busan"), "decision_snapshot_ts_utc": "2026-07-14T04:08:00Z"}
    second = {**_row(city="Tokyo"), "decision_snapshot_ts_utc": "2026-07-14T04:09:00Z"}
    path.write_text(json.dumps(first) + "\n", encoding="utf-8")
    loads = live.json.loads
    parsed = 0

    def counting_loads(payload):
        nonlocal parsed
        parsed += 1
        return loads(payload)

    monkeypatch.setattr(live.json, "loads", counting_loads)
    index = live.DecisionStateIndex()
    fresh, strong = index.refresh(
        path,
        max_snapshot_age_min=20,
        now=datetime(2026, 7, 14, 4, 10, tzinfo=timezone.utc),
    )
    assert set(fresh) == {("Busan", "2026-07-14")}
    assert [row["city"] for row in strong] == ["Busan"]
    assert parsed == 1

    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(second) + "\n")
    fresh, strong = index.refresh(
        path,
        max_snapshot_age_min=20,
        now=datetime(2026, 7, 14, 4, 10, tzinfo=timezone.utc),
    )
    assert set(fresh) == {("Busan", "2026-07-14"), ("Tokyo", "2026-07-14")}
    assert sorted(row["city"] for row in strong) == ["Busan", "Tokyo"]
    assert parsed == 2


def test_decision_state_index_restarts_after_file_replacement(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    path.write_text(json.dumps(_row(city="Busan")) + "\n", encoding="utf-8")
    index = live.DecisionStateIndex()
    index.refresh(
        path,
        max_snapshot_age_min=20,
        now=datetime(2026, 7, 14, 4, 10, tzinfo=timezone.utc),
    )

    replacement = tmp_path / "replacement.jsonl"
    replacement.write_text(json.dumps(_row(city="Tokyo")) + "\n", encoding="utf-8")
    replacement.replace(path)
    fresh, _ = index.refresh(
        path,
        max_snapshot_age_min=20,
        now=datetime(2026, 7, 14, 4, 10, tzinfo=timezone.utc),
    )

    assert set(fresh) == {("Tokyo", "2026-07-14")}


def test_latest_strong_rows_rejects_fresh_snapshot_with_stale_observation(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    rows = [
        {**_row(city="Good"), "decision_snapshot_ts_utc": "2026-07-14T04:08:00Z"},
        {
            **_row(city="Stale"),
            "decision_snapshot_ts_utc": "2026-07-14T04:08:00Z",
            "station_gap_state": "beyond_expected_cadence",
        },
        {
            **_row(city="Failed"),
            "decision_snapshot_ts_utc": "2026-07-14T04:08:00Z",
            "obs_status": "fetch_failed",
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    selected = live.latest_strong_rows(
        path,
        max_snapshot_age_min=20,
        now=datetime(2026, 7, 14, 4, 10, tzinfo=timezone.utc),
    )

    assert [row["city"] for row in selected] == ["Good"]


def test_legacy_terminal_cancel_failure_is_handled_once(tmp_path: Path) -> None:
    path = tmp_path / "live.jsonl"
    path.write_text(
        json.dumps(
            {
                "source_order_id": "maker-order-1",
                "execution_action": "h1_maker_cancel_stale_thesis",
                "exchange_response": {
                    "error_classification": "cancel_only_not_confirmed",
                    "error_reason": "not_canceled:order can't be found - already canceled or matched",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert live.handled_maker_source_order_ids(path) == {"maker-order-1"}
