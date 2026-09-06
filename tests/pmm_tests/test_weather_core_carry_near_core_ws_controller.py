from __future__ import annotations

import asyncio
import json
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.ops import weather_core_carry_near_core_ws_controller as controller_module
from scripts.ops.weather_core_carry_near_core_ws_controller import (
    Controller,
    NearCoreOrderTail,
    cancel_response_is_terminal,
    near_core_open_orders,
    parser,
    terminal_safety_cancel_order_ids,
    validate_args,
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


def test_already_canceled_exchange_response_is_persistently_terminal(tmp_path) -> None:
    order_id = "near-canceled"
    response = {
        "canceled": [],
        "not_canceled": {order_id: "order can't be found - already canceled or matched"},
        "status": "unknown",
    }
    assert cancel_response_is_terminal(response, order_id) is True
    path = tmp_path / "near_core_safety_cancels.jsonl"
    path.write_text(
        json.dumps(
            {
                "order_id": order_id,
                "status": "cancel_requested",
                "response": response,
                "authoritative_order_state": {"status": "unknown"},
            }
        )
        + "\n"
    )
    assert terminal_safety_cancel_order_ids(path) == {order_id}


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"cancelled": ["near-canceled"]}, True),
        ({"cancel": {"canceled": "near-canceled"}}, True),
        ({"not_canceled": {"near-canceled": "order is still live"}}, False),
        ({"not_canceled": ["near-canceled"]}, False),
    ],
)
def test_cancel_terminal_response_variants(response, expected) -> None:
    assert cancel_response_is_terminal(response, "near-canceled") is expected


@pytest.mark.asyncio
async def test_safety_cancel_does_not_retry_terminal_exchange_response(tmp_path) -> None:
    order_id = "near-canceled"
    live_orders = tmp_path / "live_orders.jsonl"
    live_orders.write_text(
        json.dumps(
            {
                "strategy_instance": near_core_maker_probe.STRATEGY_INSTANCE,
                "client_order_id": "pmc_ccnc_one",
                "venue_order_id": order_id,
                "status": "submitted",
            }
        )
        + "\n"
    )
    controller = Controller(
        Namespace(
            output_dir=str(tmp_path),
            live_orders=str(live_orders),
            market_ws_health=str(tmp_path / "collector_health.json"),
            cancel_retry_sec=0.0,
            cancel_rpc_timeout_sec=1.0,
            market_proxy=None,
        )
    )

    class Transport:
        def __init__(self) -> None:
            self.cancel_calls = 0

        def cancel_order(self, requested_order_id):
            self.cancel_calls += 1
            return {
                "canceled": [],
                "not_canceled": {
                    requested_order_id: "order can't be found - already canceled or matched"
                },
                "status": "unknown",
            }

        def fetch_order(self, _order_id, _client_order_id):
            return {"status": "unknown"}

    transport = Transport()
    controller.transport = transport
    not_ready = {"status": "not_ready", "fail_reasons": ["market_ws_disconnected"]}
    await controller.safety_cancel_if_needed(not_ready)
    await controller.safety_cancel_if_needed(not_ready)

    assert transport.cancel_calls == 1
    rows = [
        json.loads(line)
        for line in controller.cancel_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 1
    assert rows[0]["status"] == "cancel_confirmed"
    restarted = Controller(controller.args)
    restarted.transport = transport
    await restarted.safety_cancel_if_needed(not_ready)
    assert transport.cancel_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        (asyncio.TimeoutError(), "cancel_timeout"),
        (RuntimeError("temporary exchange failure"), "cancel_error"),
    ],
)
async def test_safety_cancel_failures_remain_retryable(
    tmp_path, failure, expected_status
) -> None:
    live_orders = tmp_path / "live_orders.jsonl"
    live_orders.write_text(
        json.dumps(
            {
                "strategy_instance": near_core_maker_probe.STRATEGY_INSTANCE,
                "client_order_id": "pmc_ccnc_retry",
                "venue_order_id": "near-retry",
                "status": "submitted",
            }
        )
        + "\n"
    )
    controller = Controller(
        Namespace(
            output_dir=str(tmp_path),
            live_orders=str(live_orders),
            market_ws_health=str(tmp_path / "collector_health.json"),
            cancel_retry_sec=0.0,
            cancel_rpc_timeout_sec=1.0,
            market_proxy=None,
        )
    )

    class FailingTransport:
        def __init__(self) -> None:
            self.cancel_calls = 0

        def cancel_order(self, _order_id):
            self.cancel_calls += 1
            raise failure

    transport = FailingTransport()
    controller.transport = transport
    not_ready = {"status": "not_ready", "fail_reasons": ["market_ws_disconnected"]}
    await controller.safety_cancel_if_needed(not_ready)
    await controller.safety_cancel_if_needed(not_ready)

    assert transport.cancel_calls == 2
    assert controller.terminal_safety_cancel_order_ids == set()
    rows = [
        json.loads(line)
        for line in controller.cancel_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["status"] for row in rows] == [expected_status, expected_status]


def test_readiness_rejects_stale_or_unproved_channels() -> None:
    now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    payload = {
        "schema_version": near_core_maker_probe.WS_CONTROL_READINESS_SCHEMA_VERSION,
        "status": "ready",
        "generated_at_utc": "2026-09-03T11:59:50Z",
        "raw_ingestion": "continuous_event_driven",
        "policy_heartbeat_ms": 250,
        "readiness_publish_interval_ms": 500,
        "economic_signal_owner": "current_yes_core_carry_tiny_live_v2",
        "economic_signal_rescore_mode": "continuous_market_reprice_15s",
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


def test_market_health_age_matches_ten_second_producer_heartbeat(tmp_path) -> None:
    now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    health_path = tmp_path / "collector_health.json"
    args = Namespace(
        output_dir=str(tmp_path),
        live_orders=str(tmp_path / "live_orders.jsonl"),
        market_ws_health=str(health_path),
        market_health_max_age_sec=30.0,
    )
    controller = Controller(args)
    payload = {
        "status": "ok",
        "connected": True,
        "generated_at_utc": "2026-09-04T11:59:40Z",
        "archive_path": "/tmp/ws.jsonl",
        "subscription_epoch_id": "epoch-1",
    }
    health_path.write_text(json.dumps(payload))
    assert controller.market_state(now)[2] == []

    payload["generated_at_utc"] = "2026-09-04T11:59:29Z"
    health_path.write_text(json.dumps(payload))
    assert controller.market_state(now)[2] == ["market_ws_health_stale"]

    payload["generated_at_utc"] = "2026-09-04T12:00:01Z"
    health_path.write_text(json.dumps(payload))
    assert controller.market_state(now)[2] == ["market_ws_health_stale"]


def test_ready_health_explicitly_reports_live_enabled(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(controller_module, "utc_now", lambda: now)
    health_path = tmp_path / "collector_health.json"
    health_path.write_text(
        json.dumps(
            {
                "status": "ok",
                "connected": True,
                "generated_at_utc": now.isoformat(),
                "archive_path": "/tmp/ws.jsonl",
                "subscription_epoch_id": "epoch-1",
            }
        )
    )
    controller = Controller(
        Namespace(
            output_dir=str(tmp_path),
            live_orders=str(tmp_path / "live_orders.jsonl"),
            market_ws_health=str(health_path),
            market_health_max_age_sec=30.0,
            private_max_silence_sec=15.0,
            heartbeat_ms=250,
            readiness_publish_interval_ms=500,
            live=True,
            confirm_live=True,
        )
    )
    controller.private_connected = True
    controller.last_private_rx = now
    controller.authenticated_rest_probe = True
    controller.ws_auth_assured = True
    readiness = controller.readiness()
    assert readiness["status"] == "ready"
    assert readiness["live_enabled"] is True


def test_market_health_age_default_and_launcher_contract() -> None:
    args = parser().parse_args(
        [
            "--output-dir",
            "/tmp/output",
            "--live-orders",
            "/tmp/live_orders.jsonl",
            "--market-ws-health",
            "/tmp/collector_health.json",
            "--live",
            "--confirm-live",
        ]
    )
    validate_args(args)
    assert args.market_health_max_age_sec == 30.0
    launcher = (
        Path(__file__).resolve().parents[2]
        / "scripts/ops/start_weather_core_carry_near_core_ws_controller.sh"
    ).read_text()
    assert "--market-health-max-age-sec 30" in launcher
    assert "--live --confirm-live" in launcher


@pytest.mark.parametrize("flags", [[], ["--live"], ["--confirm-live"]])
def test_controller_requires_both_live_confirmations(flags: list[str]) -> None:
    args = parser().parse_args(
        [
            "--output-dir",
            "/tmp/output",
            "--live-orders",
            "/tmp/live_orders.jsonl",
            "--market-ws-health",
            "/tmp/collector_health.json",
            *flags,
        ]
    )
    with pytest.raises(SystemExit, match="requires --live --confirm-live"):
        validate_args(args)


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "0"])
def test_market_health_age_rejects_non_positive_or_non_finite(value: str) -> None:
    with pytest.raises(SystemExit):
        parser().parse_args(
            [
                "--output-dir",
                "/tmp/output",
                "--live-orders",
                "/tmp/live_orders.jsonl",
                "--market-ws-health",
                "/tmp/collector_health.json",
                "--market-health-max-age-sec",
                value,
            ]
        )
