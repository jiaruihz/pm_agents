"""Internal-disk continuity evidence for long-running weather strategies.

The trading journals remain canonical and append-only on the configured runtime
root.  This module is deliberately *not* a second trading state store.  It only
keeps the process alive and makes an external-volume failure observable when the
primary runtime root cannot even accept its own error report.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


DEFAULT_CONTINUITY_ROOT = (
    Path.home()
    / "Library/Application Support/pm_agents/weather_runtime_continuity"
)
SAFE_RETRY_PHASES = frozenset({"pre_execution", "complete"})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _state_lock(instance_root: Path):
    """Serialize state read/modify/write across runtime and operator processes.

    Keep a separate stable inode: latest.json is atomically replaced.  Never
    remove this lock file, since waiters may still hold its inode open.
    """
    instance_root.mkdir(parents=True, exist_ok=True)
    with (instance_root / ".state.lock").open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def classify_exception(exc: BaseException) -> str:
    if isinstance(exc, PermissionError) or getattr(exc, "errno", None) in {
        errno.EACCES,
        errno.EPERM,
        errno.EROFS,
    }:
        return "storage_permission"
    if isinstance(exc, OSError):
        return "storage_or_io"
    return "runtime_error"


@dataclass
class RuntimeContinuity:
    """Track one process-local outage without becoming execution truth."""

    instance_id: str
    execution_mode: str
    live_enabled: bool
    root: Path = DEFAULT_CONTINUITY_ROOT
    phase: str = "pre_execution"
    consecutive_failures: int = 0
    outage_started_utc: str | None = None
    suspended: bool = False

    def __post_init__(self) -> None:
        try:
            previous = json.loads(
                (self.instance_root / "latest.json").read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            return  # A first start has no continuity incident.
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            self._suspend_unreadable_state()
            return
        if not isinstance(previous, dict) or previous.get("instance_id") != self.instance_id:
            self._suspend_unreadable_state()
            return
        if previous.get("active") is False:
            if not (
                previous.get("status") in {"recovered", "reconciliation_acknowledged"}
                and previous.get("retry_safe") is True
                and previous.get("suspended") is False
            ):
                self._suspend_unreadable_state()
            return
        if previous.get("active") is not True or not isinstance(previous.get("retry_safe"), bool):
            self._suspend_unreadable_state()
            return
        self.outage_started_utc = str(
            previous.get("outage_started_utc") or previous.get("updated_at_utc") or ""
        ) or None
        try:
            self.consecutive_failures = max(1, int(previous.get("consecutive_failures") or 1))
        except (TypeError, ValueError, OverflowError):
            self._suspend_unreadable_state()
            return
        self.suspended = previous.get("retry_safe") is False or previous.get("suspended") is True
        self.phase = str(previous.get("phase") or "pre_execution")

    def _suspend_unreadable_state(self) -> None:
        # Missing on first start is normal; present but unreadable is not proof
        # that a previous execution was reconciled. Preserve it for inspection.
        self.suspended = True
        self.phase = "continuity_state_unreadable"
        self.consecutive_failures = 1
        self.outage_started_utc = utc_now()

    @property
    def instance_root(self) -> Path:
        return self.root / self.instance_id

    def begin_cycle(self) -> None:
        self.phase = "pre_execution"

    def mark_execution_started(self) -> None:
        self.phase = "execution_started"

    def mark_execution_persisted(self) -> None:
        self.phase = "post_execution_persist"

    def mark_complete(self) -> None:
        self.phase = "complete"

    def record_failure(
        self,
        exc: BaseException,
        *,
        primary_error: BaseException | None = None,
    ) -> dict[str, Any]:
        with _state_lock(self.instance_root):
            now = utc_now()
            self.consecutive_failures += 1
            self.outage_started_utc = self.outage_started_utc or now
            retry_safe = self.phase in SAFE_RETRY_PHASES
            if not retry_safe:
                self.suspended = True
            payload: dict[str, Any] = {
                "schema_version": "weather_runtime_continuity_v1",
                "status": "degraded_retrying" if retry_safe else "reconciliation_required",
                "active": True,
                "process_alive": True,
                "instance_id": self.instance_id,
                "execution_mode": self.execution_mode,
                "live_enabled": self.live_enabled,
                "phase": self.phase,
                "retry_safe": retry_safe,
                "suspended": self.suspended,
                "failure_class": classify_exception(exc),
                "error": f"{type(exc).__name__}: {exc}"[:1000],
                "primary_error_publish_error": (
                    f"{type(primary_error).__name__}: {primary_error}"[:1000]
                    if primary_error is not None
                    else None
                ),
                "consecutive_failures": self.consecutive_failures,
                "outage_started_utc": self.outage_started_utc,
                "updated_at_utc": now,
            }
            _atomic_write_json(self.instance_root / "latest.json", payload)
            _append_jsonl(self.instance_root / "events.jsonl", payload)
            return payload

    def record_recovery(self) -> dict[str, Any] | None:
        with _state_lock(self.instance_root):
            if not self.outage_started_utc:
                self.mark_complete()
                return None
            now = utc_now()
            payload = {
                "schema_version": "weather_runtime_continuity_v1",
                "status": "recovered",
                "active": False,
                "process_alive": True,
                "instance_id": self.instance_id,
                "execution_mode": self.execution_mode,
                "live_enabled": self.live_enabled,
                "phase": "complete",
                "retry_safe": True,
                "suspended": False,
                "failure_class": None,
                "error": None,
                "primary_error_publish_error": None,
                "consecutive_failures": 0,
                "outage_started_utc": self.outage_started_utc,
                "recovered_at_utc": now,
                "updated_at_utc": now,
            }
            _atomic_write_json(self.instance_root / "latest.json", payload)
            _append_jsonl(self.instance_root / "events.jsonl", payload)
            self.consecutive_failures = 0
            self.outage_started_utc = None
            self.suspended = False
            self.mark_complete()
            return payload

    def record_suspended_heartbeat(self) -> dict[str, Any]:
        """Keep a fail-closed ambiguous-execution incident externally fresh."""

        with _state_lock(self.instance_root):
            now = utc_now()
            latest_path = self.instance_root / "latest.json"
            try:
                payload = json.loads(latest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            if (
                payload.get("status") == "reconciliation_acknowledged"
                and payload.get("instance_id") == self.instance_id
                and payload.get("active") is False
                and payload.get("retry_safe") is True
                and payload.get("suspended") is False
                and payload.get("acknowledgement_reason")
            ):
                # An acknowledgement permits a *new* process to start. The old
                # process remains suspended until the controller restarts it.
                return payload
            payload.update(
                {
                    "schema_version": "weather_runtime_continuity_v1",
                    "status": "reconciliation_required",
                    "active": True,
                    "process_alive": True,
                    "instance_id": self.instance_id,
                    "execution_mode": self.execution_mode,
                    "live_enabled": self.live_enabled,
                    "retry_safe": False,
                    "suspended": True,
                    "updated_at_utc": now,
                }
            )
            _atomic_write_json(latest_path, payload)
            return payload

    def retry_delay(self, base_seconds: float, *, maximum_seconds: float = 60.0) -> float:
        exponent = max(0, min(self.consecutive_failures - 1, 5))
        return max(float(base_seconds), min(float(maximum_seconds), float(base_seconds) * (2**exponent)))


def publish_primary_error_best_effort(
    publisher: Callable[[], Any],
) -> BaseException | None:
    try:
        publisher()
    except Exception as exc:  # noqa: BLE001 - continuity evidence must survive it.
        return exc
    return None


def emit_json_best_effort(payload: Mapping[str, Any]) -> None:
    """A JRS-backed stdout can fail during the same incident; do not exit for it."""

    try:
        print(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True), flush=True)
    except (BrokenPipeError, OSError):
        pass


def continuity_status(
    instance_id: str, root: Path = DEFAULT_CONTINUITY_ROOT
) -> dict[str, Any]:
    try:
        payload = json.loads((root / instance_id / "latest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def acknowledge_reconciliation(
    instance_id: str,
    *,
    reason: str,
    root: Path = DEFAULT_CONTINUITY_ROOT,
) -> dict[str, Any]:
    if not reason.strip():
        raise ValueError("reconciliation acknowledgement requires a reason")
    with _state_lock(root / instance_id):
        previous = continuity_status(instance_id, root)
        now = utc_now()
        payload = {
            **previous,
            "schema_version": "weather_runtime_continuity_v1",
            "status": "reconciliation_acknowledged",
            "active": False,
            "instance_id": instance_id,
            "retry_safe": True,
            "suspended": False,
            "acknowledged_at_utc": now,
            "acknowledgement_reason": reason.strip(),
            "updated_at_utc": now,
        }
        path = root / instance_id
        _atomic_write_json(path / "latest.json", payload)
        _append_jsonl(path / "events.jsonl", payload)
        return payload
