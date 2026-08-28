#!/usr/bin/env python3
"""Isolated WCIR collector-clock state machine.

It is intentionally not imported by production collectors and has no order,
fill, database, credential, or production-config dependency.  A separate
bounded public-read-only canary may drive it with real market-channel frames.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from collections import deque
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Mapping


SCHEMA_VERSION = "wcir_collector_clock_shadow_v1"


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class ExpectedTokenDemand:
    city: str
    target_date: str
    condition_ids: tuple[str, ...]
    yes_token_ids: tuple[str, ...]
    no_token_ids: tuple[str, ...]
    neighbor_brackets: tuple[str, ...]
    demand_created_at_wall_ns: int
    demand_effective_from_wall_ns: int
    demand_effective_to_wall_ns: int
    reason: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["market_identity_hash"] = stable_hash({
            "city": self.city, "target_date": self.target_date,
            "condition_ids": self.condition_ids, "yes": self.yes_token_ids,
            "no": self.no_token_ids, "neighbors": self.neighbor_brackets,
        })
        payload["schema_version"] = SCHEMA_VERSION
        return payload


@dataclass
class TokenSubscriptionState:
    token_id: str
    requested_at_wall_ns: int | None = None
    acknowledged_at_wall_ns: int | None = None
    initial_baseline_received_at_wall_ns: int | None = None
    active_epoch_id: str | None = None
    connection_id: str | None = None
    last_frame_received_at_wall_ns: int | None = None
    last_frame_received_at_monotonic_ns: int | None = None
    last_exchange_event_ts_ms: int | None = None
    heartbeat_liveness: str = "UNPROVEN"
    gap_blocker: str | None = "NO_VERIFIED_BASELINE"
    unsubscribe_end_reason: str | None = None
    valid: bool = False


@dataclass
class CollectorClockShadow:
    max_active_tokens: int
    subscription_rate_per_second: int
    active_epoch_id: str
    connection_id: str
    host_clock_sync_status: str = "UNKNOWN"
    journal_path: Path | None = None
    process_instance_id: str = "process-unknown"
    tokens: dict[str, TokenSubscriptionState] = field(default_factory=dict)
    _last_wall_ns: int | None = None
    _last_monotonic_ns: int | None = None
    _request_monotonic_ns: deque[int] = field(default_factory=deque)
    journal_sequence: int = 0
    last_scheduler_tick_monotonic_ns: int | None = None

    def __post_init__(self) -> None:
        if self.journal_path is None or not self.journal_path.exists():
            return
        expected = 0
        with self.journal_path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                row = json.loads(raw)
                expected += 1
                if int(row.get("sequence", -1)) != expected:
                    raise RuntimeError("append-only journal sequence drift")
        self.journal_sequence = expected

    def _journal(self, event: str, payload: Mapping[str, Any]) -> None:
        self.journal_sequence += 1
        row = {
            "sequence": self.journal_sequence,
            "journal_event_id": stable_hash({"process_instance_id": self.process_instance_id, "sequence": self.journal_sequence}),
            "process_instance_id": self.process_instance_id,
            "event": event,
            "active_epoch_id": self.active_epoch_id,
            "connection_id": self.connection_id,
            "payload": dict(payload),
        }
        if self.journal_path is not None:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with self.journal_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()

    def _clock(self, wall_ns: int, monotonic_ns: int) -> None:
        if wall_ns <= 0 or monotonic_ns <= 0:
            raise ValueError("missing clock")
        if self._last_wall_ns is not None and wall_ns < self._last_wall_ns:
            raise RuntimeError("wall clock regression")
        if self._last_monotonic_ns is not None and monotonic_ns < self._last_monotonic_ns:
            raise RuntimeError("monotonic clock regression")
        self._last_wall_ns, self._last_monotonic_ns = wall_ns, monotonic_ns

    def request(self, token_id: str, *, wall_ns: int, monotonic_ns: int) -> None:
        self._clock(wall_ns, monotonic_ns)
        cutoff = monotonic_ns - 1_000_000_000
        while self._request_monotonic_ns and self._request_monotonic_ns[0] <= cutoff:
            self._request_monotonic_ns.popleft()
        if len(self._request_monotonic_ns) >= self.subscription_rate_per_second:
            raise RuntimeError("subscription rate limit exceeded")
        active_count = sum(state.unsubscribe_end_reason is None and state.requested_at_wall_ns is not None for state in self.tokens.values())
        current = self.tokens.get(token_id)
        if (current is None or current.unsubscribe_end_reason is not None) and active_count >= self.max_active_tokens:
            raise RuntimeError("maximum active token capacity exceeded")
        state = self.tokens.setdefault(token_id, TokenSubscriptionState(token_id))
        state.unsubscribe_end_reason = None
        state.requested_at_wall_ns = wall_ns
        state.active_epoch_id = self.active_epoch_id
        state.connection_id = self.connection_id
        self._request_monotonic_ns.append(monotonic_ns)
        self._journal("SUBSCRIPTION_REQUESTED", {"token_id": token_id, "wall_ns": wall_ns, "monotonic_ns": monotonic_ns})

    def acknowledge(self, token_id: str, *, wall_ns: int, monotonic_ns: int) -> None:
        self._clock(wall_ns, monotonic_ns)
        state = self.tokens[token_id]
        if state.requested_at_wall_ns is None:
            raise RuntimeError("ack before request")
        state.acknowledged_at_wall_ns = wall_ns
        self._journal("SUBSCRIPTION_ACKNOWLEDGED", {"token_id": token_id, "wall_ns": wall_ns, "monotonic_ns": monotonic_ns})

    def baseline(self, token_id: str, *, wall_ns: int, monotonic_ns: int) -> None:
        self._clock(wall_ns, monotonic_ns)
        state = self.tokens[token_id]
        if state.acknowledged_at_wall_ns is None:
            raise RuntimeError("baseline before acknowledgement")
        if state.active_epoch_id != self.active_epoch_id or state.connection_id != self.connection_id:
            raise RuntimeError("baseline belongs to stale connection/epoch")
        state.initial_baseline_received_at_wall_ns = wall_ns
        state.last_frame_received_at_wall_ns = wall_ns
        state.last_frame_received_at_monotonic_ns = monotonic_ns
        state.gap_blocker = None
        state.valid = True
        self._journal("VERIFIED_BASELINE", {"token_id": token_id, "wall_ns": wall_ns, "monotonic_ns": monotonic_ns})

    def delta(self, token_id: str, *, wall_ns: int, monotonic_ns: int, exchange_event_ts_ms: int | None) -> None:
        self._clock(wall_ns, monotonic_ns)
        state = self.tokens[token_id]
        if exchange_event_ts_ms is None:
            state.valid = False
            state.gap_blocker = "MISSING_EXCHANGE_EVENT_CLOCK"
            raise RuntimeError("missing exchange event clock")
        if state.last_exchange_event_ts_ms is not None and exchange_event_ts_ms < state.last_exchange_event_ts_ms:
            state.valid = False
            state.gap_blocker = "EXCHANGE_EVENT_CLOCK_REGRESSION"
            self._journal("GAP_OPENED", {"token_id": token_id, "reason": state.gap_blocker})
            raise RuntimeError("exchange event clock regression")
        if not state.valid or state.initial_baseline_received_at_wall_ns is None:
            raise RuntimeError("delta without valid baseline")
        state.last_frame_received_at_wall_ns = wall_ns
        state.last_frame_received_at_monotonic_ns = monotonic_ns
        state.last_exchange_event_ts_ms = exchange_event_ts_ms
        self._journal("DELTA_APPLIED", {"token_id": token_id, "exchange_event_ts_ms": exchange_event_ts_ms, "wall_ns": wall_ns, "monotonic_ns": monotonic_ns})

    def mark_liveness(self, token_id: str, state: str) -> None:
        if state not in {"HEALTHY", "UNPROVEN", "DEAD"}:
            raise ValueError("invalid liveness state")
        token = self.tokens[token_id]
        token.heartbeat_liveness = state
        if state != "HEALTHY":
            token.valid = False
            token.gap_blocker = f"CONNECTION_LIVENESS_{state}"
        self._journal("LIVENESS_CHANGED", {"token_id": token_id, "state": state})

    def heartbeat_timeout(self, *, reason: str = "HEARTBEAT_TIMEOUT") -> None:
        for token in self.tokens.values():
            token.heartbeat_liveness = "DEAD"
            token.valid = False
            token.gap_blocker = reason
        self._journal("HEARTBEAT_TIMEOUT", {"reason": reason, "token_count": len(self.tokens)})

    def scheduler_tick(self, *, monotonic_ns: int, maximum_gap_ns: int) -> None:
        if monotonic_ns <= 0 or maximum_gap_ns <= 0:
            raise ValueError("invalid scheduler clock or gap")
        prior = self.last_scheduler_tick_monotonic_ns
        self.last_scheduler_tick_monotonic_ns = monotonic_ns
        if prior is not None and monotonic_ns - prior > maximum_gap_ns:
            for token in self.tokens.values():
                token.valid = False
                token.gap_blocker = "SCHEDULER_STALL"
            self._journal("SCHEDULER_STALL", {"gap_ns": monotonic_ns - prior, "maximum_gap_ns": maximum_gap_ns})
            raise RuntimeError("scheduler stall")
        self._journal("SCHEDULER_TICK", {"monotonic_ns": monotonic_ns, "maximum_gap_ns": maximum_gap_ns})

    def open_gap(self, token_id: str, reason: str) -> None:
        state = self.tokens[token_id]
        state.valid = False
        state.gap_blocker = reason
        self._journal("GAP_OPENED", {"token_id": token_id, "reason": reason})

    def unsubscribe(self, token_id: str, reason: str) -> None:
        state = self.tokens[token_id]
        state.valid = False
        state.unsubscribe_end_reason = reason
        state.gap_blocker = "UNSUBSCRIBED"
        self._journal("UNSUBSCRIBED", {"token_id": token_id, "reason": reason})

    def rest_parity_observation(self, token_id: str, payload: Mapping[str, Any]) -> str:
        # Returns an evidence hash and deliberately never mutates WS state.
        before = asdict(self.tokens[token_id])
        digest = stable_hash({"token_id": token_id, "rest_payload": payload})
        if asdict(self.tokens[token_id]) != before:
            raise RuntimeError("REST mutated WS state")
        return digest

    def reconnect(self, *, new_connection_id: str, new_epoch_id: str) -> None:
        if new_connection_id == self.connection_id or new_epoch_id == self.active_epoch_id:
            raise RuntimeError("reconnect requires new connection and epoch identities")
        self.connection_id = new_connection_id
        self.active_epoch_id = new_epoch_id
        for state in self.tokens.values():
            state.valid = False
            state.gap_blocker = "RECONNECT_REQUIRES_NEW_BASELINE"
            state.requested_at_wall_ns = None
            state.acknowledged_at_wall_ns = None
            state.initial_baseline_received_at_wall_ns = None
            state.active_epoch_id = new_epoch_id
            state.connection_id = new_connection_id
            state.last_exchange_event_ts_ms = None
        self._journal("RECONNECT_INVALIDATED_ALL", {"token_count": len(self.tokens)})

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "active_epoch_id": self.active_epoch_id,
            "connection_id": self.connection_id,
            "host_clock_sync_status": self.host_clock_sync_status,
            "tokens": {key: asdict(value) for key, value in sorted(self.tokens.items())},
            "orders": 0, "fills": 0, "notional_usd": 0,
        }


@dataclass
class BoundedFrameBuffer:
    capacity: int
    frames: deque[dict[str, Any]] = field(default_factory=deque)
    dropped_frames: int = 0

    def put(self, frame: Mapping[str, Any]) -> None:
        if len(self.frames) >= self.capacity:
            self.dropped_frames += 1
            raise RuntimeError("backpressure frame drop")
        self.frames.append(dict(frame))

    def get(self) -> dict[str, Any]:
        if not self.frames:
            raise RuntimeError("buffer empty")
        return self.frames.popleft()


def synthetic_capacity_probe(
    token_count: int = 120,
    frames_per_token: int = 100,
    journal_path: Path | None = None,
) -> dict[str, Any]:
    start = time.perf_counter_ns()
    machine = CollectorClockShadow(
        token_count, token_count, "epoch-synthetic-1", "connection-synthetic-1",
        journal_path=journal_path,
    )
    buffer = BoundedFrameBuffer(max(1, token_count))
    wall = 1_800_000_000_000_000_000
    mono = 1_000_000_000
    for index in range(token_count):
        token = f"token-{index:04d}"
        machine.request(token, wall_ns=wall, monotonic_ns=mono); wall += 1; mono += 1
        machine.acknowledge(token, wall_ns=wall, monotonic_ns=mono); wall += 1; mono += 1
        machine.baseline(token, wall_ns=wall, monotonic_ns=mono); wall += 1; mono += 1
    for frame in range(frames_per_token):
        for index in range(token_count):
            token = f"token-{index:04d}"
            buffer.put({"token_id": token, "exchange_event_ts_ms": frame})
            queued = buffer.get()
            machine.delta(queued["token_id"], wall_ns=wall, monotonic_ns=mono, exchange_event_ts_ms=queued["exchange_event_ts_ms"])
            wall += 1; mono += 1
    elapsed = max(1, time.perf_counter_ns() - start)
    frames = token_count * frames_per_token
    return {
        "synthetic_only": True, "token_count": token_count, "delta_frames": frames,
        "elapsed_seconds": elapsed / 1e9, "frames_per_second": frames / (elapsed / 1e9),
        "backpressure_drop_count": buffer.dropped_frames,
        "archive_bytes_written": journal_path.stat().st_size if journal_path and journal_path.exists() else 0,
        "archive_append_events": machine.journal_sequence,
        "orders": 0, "fills": 0, "notional_usd": 0,
    }
