"""Append-only evidence ledger and resumable run state."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any

import fcntl

from .contracts import (
    CompletionState,
    EvidenceEntry,
    RiskLevel,
    RunState,
    RunStatus,
    TaskSpec,
    utc_now,
)


class EvidenceStore:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir.resolve()
        self.task_path = self.run_dir / "task.json"
        self.state_path = self.run_dir / "state.json"
        self.ledger_path = self.run_dir / "evidence.jsonl"
        self.artifacts_dir = self.run_dir / "artifacts"

    def initialize(self, task: TaskSpec, *, initial_phase: str) -> RunState:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        if self.task_path.exists():
            existing = self.load_task()
            if existing.task_hash != task.task_hash:
                raise ValueError("run_id already exists with a different TaskSpec")
            return self.load_state()
        state = RunState(
            run_id=task.run_id,
            task_hash=task.task_hash,
            phase=initial_phase,
        )
        self._write_json_atomic(self.task_path, task.model_dump(mode="json"))
        self._write_json_atomic(self.state_path, state.model_dump(mode="json"))
        self.append("run_initialized", phase=initial_phase, payload={"task_hash": task.task_hash})
        return state

    def load_task(self) -> TaskSpec:
        return TaskSpec.model_validate_json(self.task_path.read_text(encoding="utf-8"))

    def load_state(self) -> RunState:
        return RunState.model_validate_json(self.state_path.read_text(encoding="utf-8"))

    def save_state(self, state: RunState) -> None:
        state.updated_at_utc = utc_now()
        self._write_json_atomic(self.state_path, state.model_dump(mode="json"))

    def grant_pending_authority(self, risk: RiskLevel, *, reason: str) -> RunState:
        """Persist an explicit, exact-risk grant and resume the same run."""

        task = self.load_task()
        state = self.load_state()
        pending = state.pending_authority or {}
        if state.status != RunStatus.REQUIRE_AUTHORITY:
            raise RuntimeError("run is not waiting for authority")
        if pending.get("risk") != risk.value:
            raise ValueError("grant does not match pending risk")
        if not reason.strip():
            raise ValueError("authority grant requires a reason")
        action_hash = str(pending.get("action_hash") or "")
        if not action_hash:
            raise ValueError("pending action has no fingerprint")
        action_grants = tuple(
            dict.fromkeys((*task.authority.explicit_action_grants, action_hash))
        )
        revised = task.model_copy(
            update={
                "authority": task.authority.model_copy(
                    update={"explicit_action_grants": action_grants}
                )
            }
        )
        old_hash = task.task_hash
        self._write_json_atomic(self.task_path, revised.model_dump(mode="json"))
        state.task_hash = revised.task_hash
        state.status = RunStatus.ACTIVE
        state.completion_state = CompletionState.CONTINUE
        state.pending_authority = None
        self.append(
            "authority_granted",
            phase=state.phase,
            payload={
                "risk": risk.value,
                "action_hash": action_hash,
                "reason": reason.strip(),
                "old_task_hash": old_hash,
                "new_task_hash": revised.task_hash,
            },
        )
        self.save_state(state)
        return state

    def append(
        self,
        event_type: str,
        *,
        phase: str,
        action: Any = None,
        result: Any = None,
        payload: dict[str, Any] | None = None,
    ) -> EvidenceEntry:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            prior = [line for line in handle.read().splitlines() if line.strip()]
            previous_hash = None
            if prior:
                previous_hash = EvidenceEntry.model_validate_json(prior[-1]).entry_hash
            entry = EvidenceEntry(
                sequence=len(prior) + 1,
                event_type=event_type,
                run_id=self.load_task().run_id if self.task_path.exists() else str((payload or {}).get("run_id") or "unknown"),
                phase=phase,
                action=action,
                result=result,
                payload=payload or {},
                previous_entry_hash=previous_hash,
            )
            entry.entry_hash = entry.calculated_hash
            handle.seek(0, os.SEEK_END)
            handle.write(entry.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return entry

    def entries(self) -> list[EvidenceEntry]:
        if not self.ledger_path.exists():
            return []
        return [
            EvidenceEntry.model_validate_json(line)
            for line in self.ledger_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def recent_entries(self, limit: int = 12) -> list[EvidenceEntry]:
        return self.entries()[-limit:]

    def verify_ledger(self) -> tuple[bool, tuple[str, ...]]:
        errors: list[str] = []
        previous_hash: str | None = None
        for expected_sequence, entry in enumerate(self.entries(), start=1):
            if entry.sequence != expected_sequence:
                errors.append(
                    f"sequence {entry.sequence} should be {expected_sequence}"
                )
            if entry.previous_entry_hash != previous_hash:
                errors.append(f"sequence {entry.sequence} previous hash mismatch")
            if not entry.entry_hash or entry.entry_hash != entry.calculated_hash:
                errors.append(f"sequence {entry.sequence} entry hash mismatch")
            previous_hash = entry.entry_hash
        return not errors, tuple(errors)

    def artifact_path(self, name: str) -> Path:
        candidate = (self.artifacts_dir / name).resolve()
        if not candidate.is_relative_to(self.artifacts_dir):
            raise ValueError("artifact name escapes run directory")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)


__all__ = ["EvidenceStore"]
