#!/usr/bin/env python3
"""Live safety controller for the bounded Core Carry near-Core maker sleeve.

The process consumes the shared public market-books WS health and owns one
authenticated Polymarket user-channel connection.  It never reprices an order.
If either control input becomes stale, it cancels only open near-Core orders.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import websockets

from scripts.ops.weather_polymarket_live_transport import build_live_transport
from src.strategies.weather_edge_v1.execution import near_core_maker_probe


TERMINAL = {"cancelled", "canceled", "filled", "matched", "expired", "settled"}
USER_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"


def positive_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return parsed


def validate_args(args: argparse.Namespace) -> None:
    if not bool(args.live and args.confirm_live):
        raise SystemExit("near-Core controller requires --live --confirm-live")
    if args.heartbeat_ms <= 0 or args.heartbeat_ms > 500:
        raise SystemExit("--heartbeat-ms must be in 1..500")
    if (
        args.readiness_publish_interval_ms <= 0
        or args.readiness_publish_interval_ms > 1000
    ):
        raise SystemExit("--readiness-publish-interval-ms must be in 1..1000")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_utc(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(value), handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(value), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def near_core_open_orders(path: Path) -> dict[str, dict[str, Any]]:
    heads: dict[str, dict[str, Any]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return heads
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, Mapping):
            continue
        if str(row.get("strategy_instance") or "") != near_core_maker_probe.STRATEGY_INSTANCE:
            continue
        client_id = str(row.get("client_order_id") or "")
        if not client_id.startswith(near_core_maker_probe.CLIENT_ORDER_PREFIX):
            continue
        order_id = str(
            row.get("venue_order_id")
            or row.get("order_id")
            or row.get("root_order_id")
            or ""
        )
        if order_id:
            heads[order_id] = dict(row)
    return {
        order_id: row
        for order_id, row in heads.items()
        if str(row.get("status") or "").lower() not in TERMINAL
    }


def cancel_response_is_terminal(response: Mapping[str, Any], order_id: str) -> bool:
    payload = response.get("cancel")
    if not isinstance(payload, Mapping):
        payload = response
    for key in ("canceled", "cancelled"):
        canceled = payload.get(key)
        if isinstance(canceled, (list, tuple, set)) and order_id in {
            str(value) for value in canceled
        }:
            return True
        if canceled is True or str(canceled or "") == order_id:
            return True
    not_canceled = payload.get("not_canceled")
    if not isinstance(not_canceled, Mapping):
        return False
    reason = str(not_canceled.get(order_id) or "").lower()
    return "already canceled" in reason or "already cancelled" in reason or (
        "already" in reason and "matched" in reason
    )


def terminal_safety_cancel_order_ids(path: Path) -> set[str]:
    terminal: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return terminal
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, Mapping):
            continue
        order_id = str(row.get("order_id") or "")
        authoritative = row.get("authoritative_order_state")
        authoritative_status = (
            str(authoritative.get("status") or "").lower()
            if isinstance(authoritative, Mapping)
            else ""
        )
        response = row.get("response")
        if order_id and (
            str(row.get("status") or "").lower() == "cancel_confirmed"
            or authoritative_status in TERMINAL
            or (
                isinstance(response, Mapping)
                and cancel_response_is_terminal(response, order_id)
            )
        ):
            terminal.add(order_id)
    return terminal


class NearCoreOrderTail:
    """Incrementally maintain near-Core order heads from an append-only journal."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.identity: tuple[int, int] | None = None
        self.heads: dict[str, dict[str, Any]] = {}

    def refresh(self) -> dict[str, dict[str, Any]]:
        try:
            stat = self.path.stat()
        except OSError:
            return {}
        identity = (stat.st_dev, stat.st_ino)
        if self.identity != identity or stat.st_size < self.offset:
            self.offset = 0
            self.heads = {}
            self.identity = identity
        with self.path.open("r", encoding="utf-8") as handle:
            handle.seek(self.offset)
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, Mapping):
                    continue
                if str(row.get("strategy_instance") or "") != near_core_maker_probe.STRATEGY_INSTANCE:
                    continue
                client_id = str(row.get("client_order_id") or "")
                if not client_id.startswith(near_core_maker_probe.CLIENT_ORDER_PREFIX):
                    continue
                order_id = str(row.get("venue_order_id") or row.get("order_id") or row.get("root_order_id") or "")
                if order_id:
                    self.heads[order_id] = dict(row)
            self.offset = handle.tell()
        return {
            order_id: row
            for order_id, row in self.heads.items()
            if str(row.get("status") or "").lower() not in TERMINAL
        }


class Controller:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.output_dir = Path(args.output_dir)
        self.readiness_path = self.output_dir / "near_core_ws_control_readiness.json"
        self.raw_user_path = self.output_dir / "near_core_private_order_updates.jsonl"
        self.cancel_path = self.output_dir / "near_core_safety_cancels.jsonl"
        self.started_at = utc_now()
        self.private_connected = False
        self.ws_auth_assured = False
        self.authenticated_rest_probe = False
        self.last_private_rx: datetime | None = None
        self.last_private_pong: datetime | None = None
        self.last_error = ""
        self.transport = None
        self.cancel_attempt_monotonic: dict[str, float] = {}
        self.terminal_safety_cancel_order_ids = terminal_safety_cancel_order_ids(
            self.cancel_path
        )
        self.order_tail = NearCoreOrderTail(Path(args.live_orders))
        self.last_publish_latency_ms = 0.0
        self.last_publisher_lag_ms = 0.0

    def market_state(self, now: datetime) -> tuple[bool, dict[str, Any], list[str]]:
        payload = read_json(Path(self.args.market_ws_health))
        failures: list[str] = []
        clock = parse_utc(payload.get("generated_at_utc") or payload.get("available_at_utc"))
        age = (now - clock).total_seconds() if clock else float("inf")
        if str(payload.get("status") or "") != "ok":
            failures.append("market_ws_status_not_ok")
        if not bool(payload.get("connected")):
            failures.append("market_ws_disconnected")
        if age < 0 or age > float(self.args.market_health_max_age_sec):
            failures.append("market_ws_health_stale")
        if not str(payload.get("archive_path") or ""):
            failures.append("market_ws_archive_missing")
        if not str(payload.get("subscription_epoch_id") or ""):
            failures.append("market_ws_subscription_epoch_missing")
        return not failures, payload, failures

    def readiness(self) -> dict[str, Any]:
        now = utc_now()
        market_ok, market, failures = self.market_state(now)
        private_fresh = bool(
            self.private_connected
            and self.last_private_rx
            and 0 <= (now - self.last_private_rx).total_seconds()
            <= float(self.args.private_max_silence_sec)
        )
        if not private_fresh:
            failures.append("private_channel_stale")
        if not self.authenticated_rest_probe:
            failures.append("authenticated_rest_probe_failed")
        if not self.ws_auth_assured:
            failures.append("private_ws_auth_not_assured")
        if self.last_publish_latency_ms > 1000:
            failures.append("readiness_publish_slower_than_1s")
        if self.last_publisher_lag_ms > 1000:
            failures.append("readiness_publisher_lag_over_1s")
        ready = not failures
        return {
            "schema_version": near_core_maker_probe.WS_CONTROL_READINESS_SCHEMA_VERSION,
            "status": "ready" if ready else "not_ready",
            "live_enabled": bool(self.args.live and self.args.confirm_live),
            "generated_at_utc": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "controller_pid": os.getpid(),
            "controller_started_at_utc": self.started_at.isoformat(),
            "raw_ingestion": "continuous_event_driven",
            "policy_heartbeat_ms": int(self.args.heartbeat_ms),
            "readiness_publish_interval_ms": int(
                self.args.readiness_publish_interval_ms
            ),
            "economic_signal_owner": "current_yes_core_carry_tiny_live_v2",
            "economic_signal_rescore_mode": "continuous_market_reprice_15s",
            "private_order_updates": bool(
                private_fresh and self.authenticated_rest_probe and self.ws_auth_assured
            ),
            "private_channel_connected": bool(self.private_connected),
            "authenticated_rest_probe": bool(self.authenticated_rest_probe),
            "private_ws_auth_assured": bool(self.ws_auth_assured),
            "private_ws_auth_evidence": "authenticated_rest_same_creds_plus_post_auth_pong_and_stability_window",
            "last_private_rx_utc": self.last_private_rx.isoformat() if self.last_private_rx else None,
            "last_private_pong_utc": self.last_private_pong.isoformat() if self.last_private_pong else None,
            "market_ws_connected": bool(market_ok),
            "market_ws_health_path": str(self.args.market_ws_health),
            "market_ws_subscription_epoch_id": market.get("subscription_epoch_id"),
            "market_ws_archive_path": market.get("archive_path"),
            "staleness_action": "safety_cancel",
            "window_pre_sec": 60,
            "window_post_sec": 1800,
            "economic_cancel_or_reprice_enabled": False,
            "last_publish_latency_ms": round(self.last_publish_latency_ms, 3),
            "last_publisher_lag_ms": round(self.last_publisher_lag_ms, 3),
            "fail_reasons": failures,
            "last_error": self.last_error or None,
        }

    async def safety_cancel_if_needed(self, ready: Mapping[str, Any]) -> None:
        if str(ready.get("status")) == "ready":
            return
        orders = await asyncio.to_thread(self.order_tail.refresh)
        if not orders:
            return
        if self.transport is None:
            self.transport, _ = build_live_transport(market_proxy=self.args.market_proxy)
        for order_id, row in orders.items():
            if order_id in self.terminal_safety_cancel_order_ids:
                continue
            prior = self.cancel_attempt_monotonic.get(order_id, 0.0)
            if time.monotonic() - prior < float(self.args.cancel_retry_sec):
                continue
            self.cancel_attempt_monotonic[order_id] = time.monotonic()
            record = {
                "record_type": "near_core_safety_cancel",
                "created_at_utc": utc_now().isoformat(),
                "order_id": order_id,
                "client_order_id": row.get("client_order_id"),
                "reason": "ws_control_not_ready",
                "control_fail_reasons": list(ready.get("fail_reasons") or ()),
            }
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(self.transport.cancel_order, order_id),
                    timeout=float(self.args.cancel_rpc_timeout_sec),
                )
                record["response"] = dict(response)
                authoritative = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.transport.fetch_order,
                        order_id,
                        str(row.get("client_order_id") or ""),
                    ),
                    timeout=float(self.args.cancel_rpc_timeout_sec),
                )
                record["authoritative_order_state"] = authoritative
                terminal = (
                    str((authoritative or {}).get("status") or "").lower()
                    in TERMINAL
                    or cancel_response_is_terminal(response, order_id)
                )
                record["status"] = "cancel_confirmed" if terminal else "cancel_requested"
                if terminal:
                    self.terminal_safety_cancel_order_ids.add(order_id)
            except asyncio.TimeoutError:
                record["status"] = "cancel_timeout"
                record["error"] = "authenticated cancel or verification timed out"
            except Exception as exc:  # noqa: BLE001
                record["status"] = "cancel_error"
                record["error"] = f"{type(exc).__name__}: {exc}"
            append_jsonl(self.cancel_path, record)

    async def publisher(self) -> None:
        interval = float(self.args.readiness_publish_interval_ms) / 1000.0
        next_publish = time.monotonic()
        while True:
            started = time.monotonic()
            self.last_publisher_lag_ms = max(0.0, (started - next_publish) * 1000.0)
            value = self.readiness()
            await asyncio.to_thread(atomic_json, self.readiness_path, value)
            self.last_publish_latency_ms = (time.monotonic() - started) * 1000.0
            next_publish += interval
            await asyncio.sleep(max(0.0, next_publish - time.monotonic()))

    async def safety_watchdog(self) -> None:
        while True:
            await self.safety_cancel_if_needed(self.readiness())
            await asyncio.sleep(float(self.args.heartbeat_ms) / 1000.0)

    async def authenticated_probe(self) -> None:
        if self.transport is None:
            self.transport, _ = build_live_transport(market_proxy=self.args.market_proxy)
        await asyncio.to_thread(self.transport.client.get_open_orders)
        self.authenticated_rest_probe = True

    async def user_channel_once(self) -> None:
        await self.authenticated_probe()
        creds = self.transport.client.creds
        auth = {
            "apiKey": creds.api_key,
            "secret": creds.api_secret,
            "passphrase": creds.api_passphrase,
        }
        async with websockets.connect(
            self.args.user_ws_url,
            ping_interval=None,
            close_timeout=5,
            open_timeout=10,
        ) as ws:
            await ws.send(json.dumps({"auth": auth, "type": "user"}))
            self.private_connected = True
            self.last_private_rx = utc_now()
            connected_at = time.monotonic()
            next_ping = time.monotonic()
            while True:
                timeout = max(0.05, next_ping - time.monotonic())
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    await ws.send("PING")
                    next_ping = time.monotonic() + float(self.args.ping_interval_sec)
                    continue
                self.last_private_rx = utc_now()
                if str(raw).strip().upper() == "PONG":
                    self.last_private_pong = self.last_private_rx
                    if time.monotonic() - connected_at >= float(self.args.auth_grace_sec):
                        self.ws_auth_assured = True
                    continue
                lowered = str(raw).lower()
                if "auth" in lowered and any(
                    marker in lowered for marker in ("error", "invalid", "unauthorized", "forbidden")
                ):
                    raise RuntimeError("user WS rejected authentication")
                try:
                    payload = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    payload = {"raw": str(raw)}
                append_jsonl(
                    self.raw_user_path,
                    {
                        "record_type": "polymarket_private_user_ws_event",
                        "received_at_utc": self.last_private_rx.isoformat(),
                        "payload": payload,
                    },
                )

    async def user_channel(self) -> None:
        while True:
            try:
                await self.user_channel_once()
                self.last_error = "user_channel_closed"
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"{type(exc).__name__}: {exc}"
            finally:
                self.private_connected = False
                self.authenticated_rest_probe = False
                self.ws_auth_assured = False
            await asyncio.sleep(float(self.args.reconnect_sec))

    async def run(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.gather(self.publisher(), self.safety_watchdog(), self.user_channel())


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--live-orders", required=True)
    ap.add_argument("--market-ws-health", required=True)
    ap.add_argument("--market-proxy", default=None)
    ap.add_argument("--user-ws-url", default=USER_WS_URL)
    ap.add_argument("--heartbeat-ms", type=int, default=250)
    ap.add_argument("--readiness-publish-interval-ms", type=int, default=500)
    # The market WS owner publishes its health heartbeat every 10 seconds.
    # Allow three missed publications here; executable book quotes retain their
    # own much tighter direct-fetch freshness check in the Core runner.
    ap.add_argument(
        "--market-health-max-age-sec",
        type=positive_finite_float,
        default=30.0,
    )
    ap.add_argument("--private-max-silence-sec", type=float, default=15.0)
    ap.add_argument("--ping-interval-sec", type=float, default=10.0)
    ap.add_argument("--reconnect-sec", type=float, default=1.0)
    ap.add_argument("--cancel-retry-sec", type=float, default=10.0)
    ap.add_argument("--cancel-rpc-timeout-sec", type=float, default=5.0)
    ap.add_argument("--auth-grace-sec", type=float, default=10.0)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--confirm-live", action="store_true")
    return ap


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    except ModuleNotFoundError:
        pass
    args = parser().parse_args()
    validate_args(args)
    asyncio.run(Controller(args).run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
