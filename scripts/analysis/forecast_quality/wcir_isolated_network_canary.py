#!/usr/bin/env python3
"""Run a bounded, public-read-only WCIR market WebSocket canary.

The canary never imports an order client, reads credentials, joins a production
consumer, or runs as a daemon.  It records a small append-only evidence stream,
performs real subscribe/baseline/ping-pong/reconnect checks, and separately runs
controlled fail-closed fault injections.  It is not a formal forward epoch.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any, Iterable, Mapping
import urllib.parse
import urllib.request

import websockets

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality.wcir_collector_clock_shadow import (
    BoundedFrameBuffer,
    CollectorClockShadow,
    ExpectedTokenDemand,
)


WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
OUTPUT = Path("reviews/wcir_next_print/collector_clock_amendment_v2_canary")
OFFICIAL_PROTOCOL = "https://docs.polymarket.com/api-reference/wss/market"


def now_text() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def write_jsonl_gz(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            for row in rows:
                zipped.write(canonical_bytes(row) + b"\n")
                count += 1
    return count


def messages(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        return [payload]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, Mapping)]
    return []


def event_type(row: Mapping[str, Any]) -> str:
    return str(row.get("event_type") or row.get("type") or "UNKNOWN")


def token_scoped(row: Mapping[str, Any], token_id: str) -> bool:
    if str(row.get("asset_id") or "") == token_id:
        return True
    changes = row.get("price_changes")
    return isinstance(changes, list) and any(isinstance(change, Mapping) and str(change.get("asset_id") or "") == token_id for change in changes)


def exchange_ts(row: Mapping[str, Any]) -> int | None:
    value = row.get("timestamp")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def verify_public_token_identity(token_id: str, condition_id: str) -> dict[str, Any]:
    url = "https://clob.polymarket.com/book?" + urllib.parse.urlencode({"token_id": token_id})
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "wcir-read-only-canary/2.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        payload_bytes = response.read()
    payload = json.loads(payload_bytes)
    token_match = str(payload.get("asset_id") or "") == token_id
    condition_match = str(payload.get("market") or "").lower() == condition_id.lower()
    result = {
        "verified_at_utc": now_text(),
        "endpoint": "https://clob.polymarket.com/book?token_id=<redacted-in-contract>",
        "public_read_only": True,
        "authentication_used": False,
        "expected_token_id": token_id,
        "observed_asset_id": payload.get("asset_id"),
        "expected_condition_id": condition_id,
        "observed_condition_id": payload.get("market"),
        "exchange_book_timestamp": payload.get("timestamp"),
        "exchange_book_hash": payload.get("hash"),
        "response_sha256": sha256_bytes(payload_bytes),
        "token_match": token_match,
        "condition_match": condition_match,
        "status": "PASS" if token_match and condition_match else "FAIL",
    }
    if result["status"] != "PASS":
        raise RuntimeError(f"public token identity mismatch: {result}")
    return result


async def receive_until(
    ws: Any,
    *,
    token_id: str,
    machine: CollectorClockShadow,
    timeout_seconds: float,
    max_frames: int,
    connection_phase: str,
    fault_mode: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    frames: list[dict[str, Any]] = []
    baseline_seen = False
    pong_seen = False
    ack_recorded = False
    parse_errors = 0
    frame_buffer = BoundedFrameBuffer(1 if fault_mode == "queue_overflow" else max(2, min(64, max_frames)))
    fault_result: dict[str, Any] | None = None
    ping_sent_at = time.monotonic_ns()
    await ws.send("PING")
    while time.monotonic() < deadline and len(frames) < max_frames:
        remaining = max(0.05, deadline - time.monotonic())
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        received_wall = time.time_ns()
        received_mono = time.monotonic_ns()
        raw_bytes = raw if isinstance(raw, bytes) else str(raw).encode()
        if raw_bytes.strip() == b"PONG":
            pong_seen = True
            frames.append({
                "phase": connection_phase,
                "received_at_wall_ns": received_wall,
                "received_at_monotonic_ns": received_mono,
                "wire_bytes": len(raw_bytes),
                "wire_sha256": sha256_bytes(raw_bytes),
                "event_types": ["PONG"],
                "token_scoped": False,
            })
            continue
        try:
            payload = json.loads(raw_bytes)
        except json.JSONDecodeError:
            parse_errors += 1
            frames.append({
                "phase": connection_phase,
                "received_at_wall_ns": received_wall,
                "received_at_monotonic_ns": received_mono,
                "wire_bytes": len(raw_bytes),
                "wire_sha256": sha256_bytes(raw_bytes),
                "event_types": ["UNPARSEABLE"],
                "token_scoped": False,
            })
            continue
        queued_frame = {"payload": payload, "wall": received_wall, "mono": received_mono, "raw": raw_bytes}
        frame_buffer.put(queued_frame)
        if fault_mode == "queue_overflow" and fault_result is None:
            try:
                frame_buffer.put(queued_frame)
            except RuntimeError as exc:
                fault_result = {
                    "fault": "queue_overflow",
                    "actively_induced_on_real_network_frame": True,
                    "wire_sha256": sha256_bytes(raw_bytes),
                    "exception": str(exc),
                    "fail_closed": frame_buffer.dropped_frames == 1,
                }
        queued = frame_buffer.get()
        parsed = messages(queued["payload"])
        scoped = [row for row in parsed if token_scoped(row, token_id)]
        types = [event_type(row) for row in parsed]
        frames.append({
            "phase": connection_phase,
            "received_at_wall_ns": received_wall,
            "received_at_monotonic_ns": received_mono,
            "wire_bytes": len(raw_bytes),
            "wire_sha256": sha256_bytes(raw_bytes),
            "event_types": types,
            "message_count": len(parsed),
            "token_scoped_message_count": len(scoped),
            "token_scoped": bool(scoped),
        })
        if scoped and not ack_recorded:
            # The public market protocol documents no standalone subscription ACK.
            # The first token-scoped server frame is the observable receipt.
            machine.acknowledge(token_id, wall_ns=received_wall, monotonic_ns=received_mono)
            ack_recorded = True
        for row in scoped:
            kind = event_type(row)
            if kind == "book" and not baseline_seen:
                machine.baseline(token_id, wall_ns=max(received_wall, machine._last_wall_ns or 0), monotonic_ns=max(received_mono, machine._last_monotonic_ns or 0))
                baseline_seen = True
                if fault_mode == "sequence_gap" and fault_result is None:
                    machine.open_gap(token_id, "CONTROLLED_SEQUENCE_GAP_ON_REAL_BASELINE_PATH")
                    try:
                        machine.delta(
                            token_id,
                            wall_ns=max(received_wall, machine._last_wall_ns or 0),
                            monotonic_ns=max(received_mono, machine._last_monotonic_ns or 0),
                            exchange_event_ts_ms=exchange_ts(row) or 1,
                        )
                    except RuntimeError as exc:
                        fault_result = {
                            "fault": "sequence_gap",
                            "actively_induced_on_real_network_baseline_path": True,
                            "wire_sha256": sha256_bytes(raw_bytes),
                            "exception": str(exc),
                            "reason": machine.tokens[token_id].gap_blocker,
                            "fail_closed": not machine.tokens[token_id].valid,
                            "exchange_sequence_is_protocol_unavailable": True,
                        }
            elif kind == "price_change" and baseline_seen:
                timestamp = exchange_ts(row)
                if timestamp is not None:
                    state = machine.tokens[token_id]
                    if state.last_exchange_event_ts_ms is None or timestamp >= state.last_exchange_event_ts_ms:
                        machine.delta(token_id, wall_ns=max(received_wall, machine._last_wall_ns or 0), monotonic_ns=max(received_mono, machine._last_monotonic_ns or 0), exchange_event_ts_ms=timestamp)
        if baseline_seen and pong_seen and (fault_mode is None or fault_result is not None):
            break
    if pong_seen and token_id in machine.tokens:
        machine.mark_liveness(token_id, "HEALTHY")
    return frames, {
        "baseline_seen": baseline_seen,
        "pong_seen": pong_seen,
        "ack_observed": ack_recorded,
        "ack_semantics": "FIRST_TOKEN_SCOPED_SERVER_FRAME_PROTOCOL_RECEIPT; NO_STANDALONE_ACK_DEFINED_BY_PUBLIC_MARKET_PROTOCOL",
        "parse_errors": parse_errors,
        "queue_drop_count": frame_buffer.dropped_frames,
        "ping_round_trip_ms_upper_bound": (time.monotonic_ns() - ping_sent_at) / 1e6 if pong_seen else None,
        "controlled_fault": fault_result,
    }


async def connection_phase(
    *, token_id: str, machine: CollectorClockShadow, phase: str, timeout_seconds: float, max_frames: int,
    fault_mode: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    request_wall = time.time_ns()
    request_mono = time.monotonic_ns()
    machine.request(token_id, wall_ns=request_wall, monotonic_ns=request_mono)
    request = {"assets_ids": [token_id], "type": "market", "initial_dump": True, "custom_feature_enabled": True}
    connected_at = None
    errors = []
    for attempt in range(1, 3):
        try:
            connected_at = now_text()
            async with websockets.connect(
                WS_URL,
                ping_interval=None,
                open_timeout=min(15, timeout_seconds),
                close_timeout=5,
                max_size=None,
            ) as ws:
                await ws.send(json.dumps(request, separators=(",", ":")))
                frames, receipt = await receive_until(
                    ws,
                    token_id=token_id,
                    machine=machine,
                    timeout_seconds=timeout_seconds,
                    max_frames=max_frames,
                    connection_phase=phase,
                    fault_mode=fault_mode,
                )
            break
        except (OSError, asyncio.TimeoutError, websockets.WebSocketException) as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            if attempt == 2:
                raise
            await asyncio.sleep(0.5)
    return frames, {
        "phase": phase,
        "connected_at_utc": connected_at,
        "subscription_request": request,
        "subscription_request_sha256": sha256_bytes(canonical_bytes(request)),
        "subscription_requested_at_wall_ns": request_wall,
        "connection_attempt_count": len(errors) + 1,
        "prior_connection_errors": errors,
        **receipt,
        "connection_closed_after_bounded_phase": True,
    }


def controlled_faults(
    real_reconnect_pass: bool,
    real_restart_pass: bool,
    real_queue_fault: Mapping[str, Any] | None,
    real_sequence_fault: Mapping[str, Any] | None,
) -> dict[str, Any]:
    rows = []

    def record(name: str, passed: bool, evidence: Mapping[str, Any]) -> None:
        rows.append({"fault": name, "actively_induced": True, "fail_closed": passed, **dict(evidence)})

    record("queue_overflow", bool(real_queue_fault and real_queue_fault.get("fail_closed")), {
        "evidence_origin": "ACTUAL_PUBLIC_WS_FRAME_TO_BOUNDED_BUFFER",
        **dict(real_queue_fault or {}),
    })
    record("sequence_gap", bool(real_sequence_fault and real_sequence_fault.get("fail_closed")), {
        "evidence_origin": "CONTROLLED_GAP_INJECTION_AFTER_ACTUAL_PUBLIC_WS_BASELINE",
        **dict(real_sequence_fault or {}),
    })

    wall, mono = 10_000_000_000, 10_000_000_000

    heartbeat = CollectorClockShadow(1, 10, "fault-e2", "fault-c2")
    heartbeat.request("t", wall_ns=wall, monotonic_ns=mono)
    heartbeat.acknowledge("t", wall_ns=wall + 1, monotonic_ns=mono + 1)
    heartbeat.baseline("t", wall_ns=wall + 2, monotonic_ns=mono + 2)
    heartbeat.heartbeat_timeout()
    record("heartbeat_timeout", not heartbeat.tokens["t"].valid, {"reason": heartbeat.tokens["t"].gap_blocker})

    scheduler = CollectorClockShadow(1, 10, "fault-e3", "fault-c3")
    scheduler.request("t", wall_ns=wall, monotonic_ns=mono)
    scheduler.acknowledge("t", wall_ns=wall + 1, monotonic_ns=mono + 1)
    scheduler.baseline("t", wall_ns=wall + 2, monotonic_ns=mono + 2)
    scheduler.scheduler_tick(monotonic_ns=mono + 3, maximum_gap_ns=100)
    try:
        scheduler.scheduler_tick(monotonic_ns=mono + 1000, maximum_gap_ns=100)
    except RuntimeError as exc:
        record("scheduler_stall", not scheduler.tokens["t"].valid, {"exception": str(exc), "reason": scheduler.tokens["t"].gap_blocker})
    else:
        record("scheduler_stall", False, {})

    exchange = CollectorClockShadow(1, 10, "fault-e4", "fault-c4")
    exchange.request("t", wall_ns=wall, monotonic_ns=mono)
    exchange.acknowledge("t", wall_ns=wall + 1, monotonic_ns=mono + 1)
    exchange.baseline("t", wall_ns=wall + 2, monotonic_ns=mono + 2)
    exchange.delta("t", wall_ns=wall + 3, monotonic_ns=mono + 3, exchange_event_ts_ms=100)
    try:
        exchange.delta("t", wall_ns=wall + 4, monotonic_ns=mono + 4, exchange_event_ts_ms=99)
    except RuntimeError as exc:
        record("exchange_timestamp_regression", not exchange.tokens["t"].valid, {"exception": str(exc), "reason": exchange.tokens["t"].gap_blocker})
    else:
        record("exchange_timestamp_regression", False, {})

    wall_clock = CollectorClockShadow(1, 10, "fault-e5", "fault-c5")
    wall_clock.request("t", wall_ns=wall, monotonic_ns=mono)
    try:
        wall_clock.request("t", wall_ns=wall - 1, monotonic_ns=mono + 1)
    except RuntimeError as exc:
        record("wall_clock_regression", True, {"exception": str(exc)})
    else:
        record("wall_clock_regression", False, {})

    record("hard_reconnect", real_reconnect_pass, {"real_network_connection": True, "new_connection_and_epoch_required": True})
    record("process_restart", real_restart_pass, {"real_network_connection": True, "fresh_machine_state_required_new_baseline": True})
    return {
        "fault_count": len(rows),
        "passed_count": sum(row["fail_closed"] for row in rows),
        "all_passed": len(rows) == 8 and all(row["fail_closed"] for row in rows),
        "rows": rows,
        "orders": 0,
        "fills": 0,
        "notional_usd": 0,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    args.output.mkdir(parents=True, exist_ok=True)
    journal = args.output / "evidence/STATE_TRANSITIONS.jsonl"
    if journal.exists():
        raise RuntimeError(f"append-only canary output already exists: {journal}")
    created = time.time_ns()
    demand = ExpectedTokenDemand(
        args.city,
        args.target_date,
        (args.condition_id,),
        (args.token_id,),
        (),
        tuple(args.neighbor_brackets),
        created,
        created,
        created + int(args.total_timeout_seconds * 1e9),
        "bounded isolated public-read-only canary",
        "operator supplied frozen token identity",
    )
    write_json(args.output / "EXPECTED_TOKEN_DEMAND.json", demand.to_dict())
    identity = await asyncio.to_thread(verify_public_token_identity, args.token_id, args.condition_id)
    identity.update({
        "city": args.city,
        "target_date": args.target_date,
        "neighbor_brackets": list(args.neighbor_brackets),
        "city_target_date_semantics": "OPERATOR_FROZEN_CANARY_METADATA; NOT A FORMAL FORWARD UNIVERSE CLAIM",
    })
    write_json(args.output / "TOKEN_IDENTITY_INPUT.json", identity)

    machine = CollectorClockShadow(
        1, 10, "network-epoch-1", "network-connection-1",
        journal_path=journal, process_instance_id="network-process-1",
    )
    frames1, phase1 = await connection_phase(token_id=args.token_id, machine=machine, phase="initial_connect", timeout_seconds=args.phase_timeout_seconds, max_frames=args.max_frames)
    machine.reconnect(new_connection_id="network-connection-2", new_epoch_id="network-epoch-2")
    invalidated_before_second_baseline = not machine.tokens[args.token_id].valid
    frames2, phase2 = await connection_phase(token_id=args.token_id, machine=machine, phase="hard_reconnect", timeout_seconds=args.phase_timeout_seconds, max_frames=args.max_frames)
    real_reconnect_pass = bool(invalidated_before_second_baseline and phase2["baseline_seen"] and phase2["pong_seen"])

    restarted = CollectorClockShadow(
        1, 10, "network-epoch-process-restart", "network-connection-process-restart",
        journal_path=journal, process_instance_id="network-process-2",
    )
    no_state_reused = args.token_id not in restarted.tokens
    frames3, phase3 = await connection_phase(token_id=args.token_id, machine=restarted, phase="process_restart", timeout_seconds=args.phase_timeout_seconds, max_frames=args.max_frames)
    real_restart_pass = bool(no_state_reused and phase3["baseline_seen"] and phase3["pong_seen"])
    queue_machine = CollectorClockShadow(
        1, 10, "network-epoch-queue-fault", "network-connection-queue-fault",
        journal_path=journal, process_instance_id="network-process-queue-fault",
    )
    frames4, phase4 = await connection_phase(
        token_id=args.token_id, machine=queue_machine, phase="real_frame_queue_overflow_fault",
        timeout_seconds=args.phase_timeout_seconds, max_frames=args.max_frames, fault_mode="queue_overflow",
    )
    sequence_machine = CollectorClockShadow(
        1, 10, "network-epoch-sequence-fault", "network-connection-sequence-fault",
        journal_path=journal, process_instance_id="network-process-sequence-fault",
    )
    frames5, phase5 = await connection_phase(
        token_id=args.token_id, machine=sequence_machine, phase="real_baseline_sequence_gap_fault",
        timeout_seconds=args.phase_timeout_seconds, max_frames=args.max_frames, fault_mode="sequence_gap",
    )
    frames = frames1 + frames2 + frames3 + frames4 + frames5
    frame_path = args.output / "evidence/NETWORK_FRAME_METADATA.jsonl.gz"
    write_jsonl_gz(frame_path, frames)
    faults = controlled_faults(real_reconnect_pass, real_restart_pass, phase4["controlled_fault"], phase5["controlled_fault"])
    write_json(args.output / "CONTROLLED_FAULT_MATRIX.json", faults)
    results = {
        "generated_at_utc": now_text(),
        "authorization_boundary": "BOUNDED_CANARY_ONLY; NOT_A_SHADOW_DEPLOYMENT; NOT_A_FORMAL_FORWARD_EPOCH",
        "formal_clean_forward_target_dates_counted": 0,
        "network": {
            "url": WS_URL,
            "official_protocol": OFFICIAL_PROTOCOL,
            "public_market_channel": True,
            "authentication_used": False,
            "order_or_user_channel_used": False,
            "phase_count": 5,
            "phases": [phase1, phase2, phase3, phase4, phase5],
            "frame_count": len(frames),
            "wire_bytes": sum(int(row["wire_bytes"]) for row in frames),
            "parse_error_count": sum(phase["parse_errors"] for phase in (phase1, phase2, phase3)),
            "queue_drop_count_normal_path": sum(phase["queue_drop_count"] for phase in (phase1, phase2, phase3)),
            "controlled_fault_phase_count": 2,
        },
        "request_coverage": {
            "expected_token_count": 1,
            "requested_token_count": 1,
            "requested_fraction": 1.0,
            "coverage_semantics": "SINGLE_FROZEN_CANARY_TOKEN_TRANSPORT_ONLY; NOT FUTURE EXPECTED-DEMAND COVERAGE",
            "demand_created_to_initial_request_ms": (phase1["subscription_requested_at_wall_ns"] - created) / 1e6,
            "formal_source_opportunity_trigger_observed": False,
            "protocol_receipt_phase_count": sum(phase["ack_observed"] for phase in (phase1, phase2, phase3)),
            "baseline_phase_count": sum(phase["baseline_seen"] for phase in (phase1, phase2, phase3)),
            "pong_phase_count": sum(phase["pong_seen"] for phase in (phase1, phase2, phase3)),
        },
        "controlled_faults_all_passed": faults["all_passed"],
        "production_consumer_joined": False,
        "production_config_modified": False,
        "daemon_started": False,
        "orders": 0,
        "fills": 0,
        "notional_usd": 0,
        "modeling_performed": False,
        "stage4_work_performed": False,
    }
    phases_ok = all(phase["ack_observed"] and phase["baseline_seen"] and phase["pong_seen"] and phase["parse_errors"] == 0 for phase in (phase1, phase2, phase3))
    network_faults_ok = all(
        phase["controlled_fault"] and phase["controlled_fault"].get("fail_closed")
        for phase in (phase4, phase5)
    )
    results["status"] = "PASS" if phases_ok and network_faults_ok and faults["all_passed"] else "FAIL"
    write_json(args.output / "NETWORK_CANARY_RESULTS.json", results)
    if results["status"] != "PASS":
        raise RuntimeError(f"network canary failed closed: {results}")
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-id", required=True)
    parser.add_argument("--condition-id", required=True)
    parser.add_argument("--city", required=True)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--neighbor-brackets", nargs="*", default=[])
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--phase-timeout-seconds", type=float, default=12.0)
    parser.add_argument("--total-timeout-seconds", type=float, default=45.0)
    parser.add_argument("--max-frames", type=int, default=50)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.phase_timeout_seconds <= 0 or args.total_timeout_seconds > 60 or args.max_frames <= 0 or args.max_frames > 200:
        raise SystemExit("invalid bounded canary limits")
    result = asyncio.run(asyncio.wait_for(run(args), timeout=args.total_timeout_seconds))
    print(json.dumps({"output": str(args.output), "status": result["status"], "frames": result["network"]["frame_count"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
