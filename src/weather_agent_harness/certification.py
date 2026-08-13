"""Independent trace export and deterministic certification for Harness runs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import Field

from .contracts import (
    CompletionState,
    HarnessModel,
    RiskLevel,
    RunStatus,
    stable_hash,
    utc_now,
)
from .domains.base import DomainController
from .evidence import EvidenceStore
from .orchestration.contracts import OrchestrationState, WorkStatus
from .tools import ToolRegistry


CERTIFICATION_SCHEMA_VERSION = "weather_agent_harness_certification_v1"
TRACE_SCHEMA_VERSION = "weather_agent_harness_trace_v1"
MANIFEST_SCHEMA_VERSION = "weather_agent_harness_run_manifest_v1"
TERMINAL_STATES = {
    CompletionState.COMPLETE,
    CompletionState.COMPLETE_QUALIFIED,
    CompletionState.COMPLETE_FALSIFIED,
}
ALWAYS_EXPLICIT = {
    RiskLevel.PRODUCTION_CHANGE,
    RiskLevel.LIVE_FUNDS,
    RiskLevel.DESTRUCTIVE,
}


class TraceSpan(HarnessModel):
    span_id: str
    action_hash: str
    tool_name: str
    phase: str
    status: str
    risk: str | None = None
    start_sequence: int | None = None
    finish_sequence: int | None = None
    started_at_utc: str | None = None
    finished_at_utc: str | None = None
    evidence_refs: tuple[str, ...] = ()
    finding_ids: tuple[str, ...] = ()


class RunTrace(HarnessModel):
    schema_version: str = TRACE_SCHEMA_VERSION
    run_id: str
    task_hash: str
    generated_at_utc: str = Field(default_factory=utc_now)
    event_count: int
    spans: tuple[TraceSpan, ...]
    open_action_hashes: tuple[str, ...] = ()


class GradeResult(HarnessModel):
    grader: str
    passed: bool
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)


class RunCertification(HarnessModel):
    schema_version: str = CERTIFICATION_SCHEMA_VERSION
    run_id: str
    task_hash: str
    task_outcome: CompletionState
    harness_certified: bool
    generated_at_utc: str = Field(default_factory=utc_now)
    grades: tuple[GradeResult, ...]
    trace_ref: str


class ArtifactRecord(HarnessModel):
    path: str
    size_bytes: int
    sha256: str


class RunManifest(HarnessModel):
    schema_version: str = MANIFEST_SCHEMA_VERSION
    run_id: str
    task_hash: str
    task_type: str
    task_outcome: CompletionState
    harness_certified: bool
    generated_at_utc: str = Field(default_factory=utc_now)
    trace_ref: str
    certification_ref: str
    artifact_inventory: tuple[ArtifactRecord, ...]
    manifest_hash: str = ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    EvidenceStore._write_json_atomic(path, payload)


def _risk_for(registry: ToolRegistry, tool_name: str) -> RiskLevel | None:
    try:
        return registry.get(tool_name).contract.risk
    except KeyError:
        return None


def build_trace(store: EvidenceStore, registry: ToolRegistry) -> RunTrace:
    task = store.load_task()
    entries = store.entries()
    starts: dict[str, list[Any]] = {}
    completion_counts: dict[str, int] = {}
    spans: list[TraceSpan] = []
    for entry in entries:
        if entry.action is None:
            continue
        action_hash = entry.action.action_hash
        if entry.event_type in {"action_started", "external_action_prepared"}:
            starts.setdefault(action_hash, []).append(entry)
        if entry.event_type != "action_completed" or entry.result is None:
            continue
        completed_before = completion_counts.get(action_hash, 0)
        action_starts = starts.get(action_hash, [])
        start = (
            action_starts[completed_before]
            if completed_before < len(action_starts)
            else None
        )
        completion_counts[action_hash] = completed_before + 1
        risk = _risk_for(registry, entry.action.tool_name)
        spans.append(
            TraceSpan(
                span_id=f"action-{entry.sequence}-{action_hash[:12]}",
                action_hash=action_hash,
                tool_name=entry.action.tool_name,
                phase=entry.phase,
                status=entry.result.status,
                risk=risk.value if risk else None,
                start_sequence=start.sequence if start else None,
                finish_sequence=entry.sequence,
                started_at_utc=(
                    entry.result.started_at_utc
                    or (start.created_at_utc if start else None)
                ),
                finished_at_utc=entry.result.finished_at_utc,
                evidence_refs=entry.result.evidence_refs,
                finding_ids=tuple(item.finding_id for item in entry.result.findings),
            )
        )
    open_hashes = tuple(
        action_hash
        for action_hash, action_starts in starts.items()
        if len(action_starts) > completion_counts.get(action_hash, 0)
    )
    return RunTrace(
        run_id=task.run_id,
        task_hash=task.task_hash,
        event_count=len(entries),
        spans=tuple(spans),
        open_action_hashes=open_hashes,
    )


def _resolve_evidence_ref(store: EvidenceStore, reference: str) -> Path | None:
    candidate = Path(reference)
    choices = (
        (candidate if candidate.is_absolute() else store.run_dir / candidate),
        store.artifacts_dir / candidate,
    )
    for choice in choices:
        try:
            resolved = choice.resolve()
        except OSError:
            continue
        if resolved.is_file():
            return resolved
    return None


def _grade_ledger(store: EvidenceStore) -> GradeResult:
    passed, errors = store.verify_ledger()
    return GradeResult(
        grader="ledger_hash_chain",
        passed=passed,
        summary="append-only evidence hash chain is valid" if passed else "evidence ledger integrity failed",
        details={"errors": list(errors)},
    )


def _grade_terminal(store: EvidenceStore) -> GradeResult:
    state = store.load_state()
    terminal = state.completion_state in TERMINAL_STATES
    consistent = (terminal and state.status == RunStatus.COMPLETE) or (
        not terminal and state.status != RunStatus.COMPLETE
    )
    return GradeResult(
        grader="terminal_state_consistency",
        passed=consistent,
        summary="run status agrees with completion state" if consistent else "run status contradicts completion state",
    )


def _grade_acceptance(store: EvidenceStore) -> GradeResult:
    task = store.load_task()
    state = store.load_state()
    unmet = [item for item in task.acceptance if item not in state.completed_acceptance]
    passed = state.completion_state not in TERMINAL_STATES or not unmet
    return GradeResult(
        grader="acceptance_coverage",
        passed=passed,
        summary="terminal acceptance contract is complete" if passed else "terminal run has unmet acceptance conditions",
        details={"unmet_acceptance": unmet},
    )


def _grade_evidence_refs(store: EvidenceStore) -> GradeResult:
    missing: list[dict[str, Any]] = []
    checked = 0
    for entry in store.entries():
        if entry.event_type != "action_completed" or entry.result is None:
            continue
        for reference in entry.result.evidence_refs:
            checked += 1
            if _resolve_evidence_ref(store, reference) is None:
                missing.append({"sequence": entry.sequence, "reference": reference})
    orchestration_path = store.run_dir / "orchestration.json"
    if orchestration_path.is_file():
        orchestration = OrchestrationState.model_validate_json(
            orchestration_path.read_text(encoding="utf-8")
        )
        for result in orchestration.results:
            for reference in result.evidence_refs:
                checked += 1
                if _resolve_evidence_ref(store, reference) is None:
                    missing.append(
                        {
                            "work_order_id": result.work_order_id,
                            "attempt": result.attempt,
                            "reference": reference,
                        }
                    )
    passed = not missing
    return GradeResult(
        grader="evidence_reference_integrity",
        passed=passed,
        summary="all evidence references resolve" if passed else "one or more evidence references are missing",
        details={"checked": checked, "missing": missing},
    )


def _grade_authority(store: EvidenceStore, registry: ToolRegistry) -> GradeResult:
    entries = store.entries()
    granted: set[str] = set()
    violations: list[dict[str, Any]] = []
    for entry in entries:
        if entry.event_type == "authority_granted":
            granted.add(str(entry.payload.get("action_hash") or ""))
            continue
        if entry.event_type != "action_completed" or entry.action is None:
            continue
        risk = _risk_for(registry, entry.action.tool_name)
        if risk in ALWAYS_EXPLICIT and entry.action.action_hash not in granted:
            violations.append(
                {
                    "sequence": entry.sequence,
                    "tool_name": entry.action.tool_name,
                    "risk": risk.value,
                    "action_hash": entry.action.action_hash,
                }
            )
    passed = not violations
    return GradeResult(
        grader="authority_boundary",
        passed=passed,
        summary="irreversible actions have exact grants" if passed else "an irreversible action lacks a prior exact grant",
        details={"violations": violations},
    )


def _grade_verifier(
    store: EvidenceStore, domain: DomainController | None
) -> GradeResult:
    if domain is None:
        return GradeResult(
            grader="verifier_replay",
            passed=False,
            summary="domain verifier was not supplied",
        )
    task = store.load_task()
    state = store.load_state()
    decision = domain.verify(task, state)
    passed = decision.state == state.completion_state
    return GradeResult(
        grader="verifier_replay",
        passed=passed,
        summary="independent verifier replay matches persisted outcome" if passed else "persisted outcome does not match verifier replay",
        details={"replayed_state": decision.state.value, "reason": decision.reason},
    )


def _grade_inflight(store: EvidenceStore, trace: RunTrace) -> GradeResult:
    state = store.load_state()
    open_hashes = set(trace.open_action_hashes)
    if state.inflight_action is not None:
        open_hashes.add(state.inflight_action.action_hash)
    passed = not open_hashes
    return GradeResult(
        grader="no_unresolved_inflight_action",
        passed=passed,
        summary="no action is left half-recorded" if passed else "run has an unresolved in-flight action",
        details={"open_action_hashes": sorted(open_hashes)},
    )


def _grade_orchestration(store: EvidenceStore) -> GradeResult:
    path = store.run_dir / "orchestration.json"
    if not path.is_file():
        return GradeResult(
            grader="orchestration_terminal_consistency",
            passed=True,
            summary="run has no multi-agent orchestration state",
        )
    orchestration = OrchestrationState.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    run_state = store.load_state()
    unresolved = [
        item.work_order_id
        for item in orchestration.work_orders
        if item.status != WorkStatus.COMPLETE
    ]
    terminal = run_state.completion_state in TERMINAL_STATES
    passed = not terminal or not unresolved
    return GradeResult(
        grader="orchestration_terminal_consistency",
        passed=passed,
        summary=(
            "terminal run has no unresolved work orders"
            if passed
            else "terminal run still has unresolved work orders"
        ),
        details={"unresolved_work_orders": unresolved},
    )


def _inventory(store: EvidenceStore) -> tuple[ArtifactRecord, ...]:
    excluded = {"trace.json", "certification.json", "run_manifest.json"}
    records: list[ArtifactRecord] = []
    for path in sorted(item for item in store.run_dir.rglob("*") if item.is_file()):
        relative = path.relative_to(store.run_dir).as_posix()
        if relative in excluded:
            continue
        records.append(
            ArtifactRecord(
                path=relative,
                size_bytes=path.stat().st_size,
                sha256=_sha256(path),
            )
        )
    return tuple(records)


def certify_run(
    *,
    store: EvidenceStore,
    registry: ToolRegistry,
    domain: DomainController | None,
) -> RunCertification:
    """Export a trace, run deterministic graders, and seal a machine manifest."""

    task = store.load_task()
    state = store.load_state()
    trace = build_trace(store, registry)
    trace_path = store.run_dir / "trace.json"
    _write_json(trace_path, trace.model_dump(mode="json"))
    grades = (
        _grade_ledger(store),
        _grade_terminal(store),
        _grade_acceptance(store),
        _grade_evidence_refs(store),
        _grade_authority(store, registry),
        _grade_verifier(store, domain),
        _grade_inflight(store, trace),
        _grade_orchestration(store),
    )
    certification = RunCertification(
        run_id=task.run_id,
        task_hash=task.task_hash,
        task_outcome=state.completion_state,
        harness_certified=all(item.passed for item in grades),
        grades=grades,
        trace_ref=str(trace_path),
    )
    certification_path = store.run_dir / "certification.json"
    _write_json(certification_path, certification.model_dump(mode="json"))
    manifest = RunManifest(
        run_id=task.run_id,
        task_hash=task.task_hash,
        task_type=task.task_type,
        task_outcome=state.completion_state,
        harness_certified=certification.harness_certified,
        trace_ref=str(trace_path),
        certification_ref=str(certification_path),
        artifact_inventory=_inventory(store),
    )
    manifest.manifest_hash = stable_hash(
        manifest.model_dump(mode="json", exclude={"manifest_hash", "generated_at_utc"})
    )
    _write_json(
        store.run_dir / "run_manifest.json", manifest.model_dump(mode="json")
    )
    return certification


__all__ = [
    "ArtifactRecord",
    "GradeResult",
    "RunCertification",
    "RunManifest",
    "RunTrace",
    "TraceSpan",
    "build_trace",
    "certify_run",
]
