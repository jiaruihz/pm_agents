from __future__ import annotations

import json
from datetime import datetime, timezone

from scripts.ops.weather_data_feed_prod_health_check import (
    ACTIVE_RUNTIME_LIVE_ORDER_FILES,
    DEFAULT_SNAPSHOT_DIR,
    check_live_orders,
    overall_status,
)
from src.strategies.runtime.production import load_production_spec


def test_health_paths_come_from_production_spec_without_historical_fallback() -> None:
    spec = load_production_spec()
    assert DEFAULT_SNAPSHOT_DIR == spec.strategy_paper_snapshot_dir()
    assert ACTIVE_RUNTIME_LIVE_ORDER_FILES == spec.active_live_order_paths()
    assert "weather-predict" not in str(DEFAULT_SNAPSHOT_DIR)


def test_health_excludes_confirmed_zero_fill_retry_parent(tmp_path) -> None:
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    path = live_dir / "orders.jsonl"
    base = {
        "strategy_instance": "fast_source_prev_no_trial_v1",
        "city": "Singapore",
        "target_date": "2026-08-04",
        "token_id": "no-token",
        "signal_side": "BUY_NO",
        "order_side": "BUY",
        "child_order_role": "taker",
        "execution_policy": "depth_retry",
        "status": "cross_candidate",
    }
    rows = [
        {
            **base,
            "order_id": "parent",
            "exchange_order_status": "canceled",
            "immediate_cancel_confirmed": True,
            "actual_fill_shares": 0.0,
            "exchange_response": {"place": {"success": True, "status": "live"}},
        },
        {
            **base,
            "order_id": "retry",
            "exchange_order_status": "matched",
            "actual_fill_shares": 13.5,
            "exchange_response": {"place": {"success": True, "status": "matched"}},
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    report = check_live_orders(
        live_dir,
        tail_rows=10,
        extra_files=[path],
        now_utc=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )

    assert report["effective_current_or_future_rows"] == 1
    assert report["duplicate_current_strategy_city_token_count"] == 0


def test_historical_order_duplicates_are_informational_not_runtime_warning() -> None:
    sections = {
        "snapshot_parity": {"status": "ok"},
        "snapshot_duplicates": {"duplicate_record_count": 0, "snapshot_stale": False},
        "telemetry": [],
        "live_orders": {
            "parse_error_count": 0,
            "duplicate_order_id_count": 0,
            "duplicate_strategy_city_token_count": 50,
            "duplicate_current_strategy_city_token_count": 0,
            "current_yes_no_conflict_count": 0,
        },
        "summaries": [],
    }
    assert overall_status(sections) == "ok"
