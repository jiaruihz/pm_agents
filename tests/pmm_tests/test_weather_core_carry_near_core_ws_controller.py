from __future__ import annotations

import json
from datetime import datetime, timezone

from scripts.ops.weather_core_carry_near_core_ws_controller import (
    NearCoreOrderTail,
    near_core_open_orders,
)
from src.strategies.weather_edge_v1.execution import near_core_maker_probe


def test_near_core_open_orders_is_strictly_scoped_and_terminal_aware(tmp_path) -> None:
    path = tmp_path / "live_orders.jsonl"
    rows = [
        {
            "strategy_instance": near_core_maker_probe.STRATEGY_INSTANCE,
            "client_order_id": "pmc_ccnc_one",
            "venue_order_id": "near-open",
            "status": "submitted",
        },
        {
            "strategy_instance": "current_yes_core_carry_tiny_live_v2",
            "client_order_id": "pmc_core",
            "venue_order_id": "core-open",
            "status": "submitted",
        },
        {
            "strategy_instance": near_core_maker_probe.STRATEGY_INSTANCE,
            "client_order_id": "",
            "venue_order_id": "unscoped-open",
            "status": "submitted",
        },
        {
            "strategy_instance": near_core_maker_probe.STRATEGY_INSTANCE,
            "client_order_id": "pmc_ccnc_done",
            "venue_order_id": "near-done",
            "status": "submitted",
        },
        {
            "strategy_instance": near_core_maker_probe.STRATEGY_INSTANCE,
            "client_order_id": "pmc_ccnc_done",
            "venue_order_id": "near-done",
            "status": "cancelled",
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert list(near_core_open_orders(path)) == ["near-open"]

    tail = NearCoreOrderTail(path)
    assert list(tail.refresh()) == ["near-open"]
    with path.open("a") as handle:
        handle.write(
            json.dumps(
                {
                    "strategy_instance": near_core_maker_probe.STRATEGY_INSTANCE,
                    "client_order_id": "pmc_ccnc_one",
                    "venue_order_id": "near-open",
                    "status": "filled",
                }
            )
            + "\n"
        )
    assert tail.refresh() == {}


def test_readiness_rejects_stale_or_unproved_channels() -> None:
    now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    payload = {
        "schema_version": near_core_maker_probe.WS_CONTROL_READINESS_SCHEMA_VERSION,
        "status": "ready",
        "generated_at_utc": "2026-09-03T11:59:50Z",
        "raw_ingestion": "continuous_event_driven",
        "policy_heartbeat_ms": 250,
        "feature_snapshot_max_interval_ms": 500,
        "private_order_updates": True,
        "private_channel_connected": True,
        "authenticated_rest_probe": True,
        "private_ws_auth_assured": True,
        "market_ws_connected": True,
        "staleness_action": "safety_cancel",
        "window_pre_sec": 60,
        "window_post_sec": 1800,
    }
    failures = near_core_maker_probe.ws_control_readiness_failures(payload, now=now)
    assert failures == ["ws_control_manifest_stale"]

    payload["generated_at_utc"] = now.isoformat()
    payload["authenticated_rest_probe"] = False
    failures = near_core_maker_probe.ws_control_readiness_failures(payload, now=now)
    assert failures == ["authenticated_rest_probe_failed"]
