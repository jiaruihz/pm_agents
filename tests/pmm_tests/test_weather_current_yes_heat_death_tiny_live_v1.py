from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.ops import weather_current_yes_heat_death_tiny_live_v1 as live


def _select_h2() -> None:
    live.ACTIVE_HEAD = "h2_early_dislocation"
    live.STRATEGY_INSTANCE = live.HEADS[live.ACTIVE_HEAD]["instance"]


def _row(*, city: str = "Busan", ask: float = 0.84, ask_size: float = 20.0) -> dict:
    return {
        "city": city,
        "target_date": "2026-07-14",
        "decision_snapshot_ts_utc": "2026-07-14T04:08:00Z",
        "physical_confirmation_strong": True,
        "current_bracket": "30",
        "current_market_id": "m30",
        "current_question": "Will Busan be 30C?",
        "current_yes_token_id": f"yes-{city}",
        "shadow_decision_id": f"shadow-{city}",
        "fresh_current_yes_ask": ask,
        "fresh_current_yes_ask_size": ask_size,
        "fresh_current_yes_bid": ask - 0.01,
        "fresh_current_yes_book_status": "ok",
        "fresh_current_yes_book_fetched_at_utc": "2026-07-14T04:08:05Z",
        "physical_support_count": 3,
    }


def test_signal_id_dedupes_snapshot_and_bracket() -> None:
    _select_h2()
    left = _row()
    right = {**left, "snapshot_file": "new.json", "current_bracket": "31"}
    assert live.signal_id(left) == live.signal_id(right)


def test_build_plan_is_fixed_ten_share_current_yes_probe() -> None:
    _select_h2()
    plan = live.build_plan(_row(), shares=10, live_enabled=True, ttl_min=15)
    assert plan["record_type"] == "weather_edge_trade_plan"
    assert plan["signal_side"] == "BUY_YES"
    assert plan["size"] == 10
    assert plan["notional"] == 8.4
    assert plan["live_enabled"] is True
    assert plan["risk_status"] == "passed"


def test_choose_plans_applies_depth_dedup_and_daily_cap(tmp_path: Path) -> None:
    _select_h2()
    live_orders = tmp_path / "live.jsonl"
    now = datetime(2026, 7, 14, 4, 10, tzinfo=timezone.utc)
    submitted = live.build_plan(_row(city="Busan"), shares=10, live_enabled=True, ttl_min=15)
    live_orders.write_text(
        json.dumps({**submitted, "status": "submitted", "created_at_utc": "2026-07-14T04:09:00Z"}) + "\n",
        encoding="utf-8",
    )
    rows = [
        _row(city="Busan"),
        _row(city="Jeddah", ask=0.88, ask_size=8),
        _row(city="PanamaCity", ask=0.98, ask_size=30),
        _row(city="Ankara", ask=0.991, ask_size=100),
        _row(city="CapeTown", ask=0.95, ask_size=30),
        _row(city="Atlanta", ask=0.96, ask_size=30),
    ]
    plans, counts = live.choose_plans(
        rows,
        live_orders=live_orders,
        shares=10,
        min_ask=0.50,
        max_ask=0.99,
        min_top_ask_shares=10,
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
