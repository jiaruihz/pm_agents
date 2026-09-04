#!/usr/bin/env python3
"""Fail-closed regional preflight for irreversible weather order submission.

The TAG route controller owns the shared selector and publishes a short-lived
gate.  Live order transports take a shared lock, validate that gate, and then
ask Polymarket for the verdict of the exact execution proxy immediately before
posting.  Cancellation and reconciliation paths intentionally do not use this
gate.
"""

from __future__ import annotations

import fcntl
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from scripts.ops.tag_proxy_route_ctl import probe_polymarket_geoblock


GATE_SCHEMA_VERSION = "weather_live_entry_region_gate_v1"
DEFAULT_STATE_ROOT = (
    Path.home()
    / "Library/Application Support/pm_agents/tag_proxy_route_control"
)
DEFAULT_GATE_MAX_AGE_SEC = 300
DEFAULT_LOCK_WAIT_SEC = 15.0


class LiveEntryRegionBlocked(RuntimeError):
    """A typed, pre-submit refusal with no ambiguous exchange handoff."""

    def __init__(self, reason: str) -> None:
        self.reason = str(reason)
        super().__init__(f"live_entry_region_blocked:{self.reason}")


@contextmanager
def _shared_route_lock(path: Path, *, wait_seconds: float) -> Iterator[None]:
    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    try:
        handle = path.open("r", encoding="utf-8")
    except OSError as exc:
        raise LiveEntryRegionBlocked("route_lock_unavailable") from exc
    with handle:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LiveEntryRegionBlocked("route_switch_in_progress")
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_gate(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LiveEntryRegionBlocked("route_gate_unavailable") from exc
    if not isinstance(payload, dict):
        raise LiveEntryRegionBlocked("route_gate_invalid")
    return payload


@contextmanager
def live_entry_region_guard(
    proxy_url: str,
    *,
    state_root: Path = DEFAULT_STATE_ROOT,
    max_gate_age_sec: int = DEFAULT_GATE_MAX_AGE_SEC,
    lock_wait_sec: float = DEFAULT_LOCK_WAIT_SEC,
    now_epoch: float | None = None,
    geoblock_probe: Callable[..., dict[str, Any]] = probe_polymarket_geoblock,
) -> Iterator[dict[str, Any]]:
    """Hold route stability from exact geoblock proof through CLOB handoff."""

    if not str(proxy_url or "").strip():
        raise LiveEntryRegionBlocked("execution_proxy_missing")
    with _shared_route_lock(
        state_root / "maintain.lock", wait_seconds=lock_wait_sec
    ):
        gate = _read_gate(state_root / "live_entry_gate.json")
        if gate.get("schema_version") != GATE_SCHEMA_VERSION:
            raise LiveEntryRegionBlocked("route_gate_schema_mismatch")
        if gate.get("entry_allowed") is not True:
            reason = str(gate.get("reason") or "route_gate_closed")
            raise LiveEntryRegionBlocked(reason)
        updated = gate.get("updated_at_epoch")
        if not isinstance(updated, (int, float)):
            raise LiveEntryRegionBlocked("route_gate_timestamp_missing")
        now = time.time() if now_epoch is None else float(now_epoch)
        age = now - float(updated)
        if age < -5 or age > int(max_gate_age_sec):
            raise LiveEntryRegionBlocked("route_gate_stale")
        verdict = geoblock_probe(str(proxy_url))
        if not isinstance(verdict, dict) or verdict.get("trading_allowed") is not True:
            if isinstance(verdict, dict) and verdict.get("blocked") is True:
                reason = "polymarket_region_blocked"
            else:
                reason = "polymarket_geoblock_unverified"
            raise LiveEntryRegionBlocked(reason)
        yield {
            "gate": gate,
            "polymarket_geoblock": verdict,
        }


def assert_live_entry_region_allowed(
    proxy_url: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Standalone diagnostic form of :func:`live_entry_region_guard`."""

    with live_entry_region_guard(proxy_url, **kwargs) as evidence:
        return evidence
