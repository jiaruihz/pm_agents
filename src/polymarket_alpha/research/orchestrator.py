"""Crash-replayable offline orchestration for controlled Alpha research.

The orchestrator composes existing packet, brief, work-order, compiler and
importer owners.  It has no provider client, queue daemon, network transport,
browser, signing path, or order capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path

from pydantic import ValidationError

from ..artifacts import ArtifactConflictError, ArtifactPathError, ArtifactStore
from ..contracts import (
    BlindResearchPacket,
    MarketResearchPacket,
    PacketStage,
    ResearchImportReceipt,
    ResearchImportStatus,
    ResearchJob,
    ResearchJobStatus,
    ResearchJobTransition,
    ResearchResultEnvelope,
    ResearchReturnDisposition,
    ResearchReturnReceipt,
    ResearchTransitionReason,
    RuleContract,
    bytes_sha256,
    canonical_json,
    stable_record_id,
)
from ..contracts.base import ensure_utc
from ..storage import AlphaRepository
from .automation import (
    ProviderReturnPayload,
    ResearchLeaseGrant,
    SealedProviderReturn,
    adapt_provider_return,
    build_research_job,
    lease_research_job,
)
from .brief import build_research_brief
from .draft import (
    ActualSourceBytes,
    CompiledResearchDraft,
    ResearchDraft,
    ResearchDraftError,
    compile_research_draft,
)
from .importer import ResearchImportOutcome, import_research_result


RESEARCH_ORCHESTRATOR_VERSION = "p1_a04_offline_orchestrator_v1"
ResearchPacketValue = BlindResearchPacket | MarketResearchPacket


class ResearchOrchestrationError(ValueError):
    """One controlled orchestration step cannot be completed safely."""


class ResearchBaselineError(ResearchOrchestrationError):
    """A Market job lacks the exact accepted Blind baseline."""


class ResearchResumeError(ResearchOrchestrationError):
    """A sealed provider return does not bind the active execution."""


@dataclass(frozen=True, slots=True)
class PreparedResearchExecution:
    job: ResearchJob
    lease: ResearchLeaseGrant
    brief_locator: str
    brief_bytes_sha256: str


@dataclass(frozen=True, slots=True)
class ResearchExecutionOutcome:
    sealed_return: SealedProviderReturn
    draft: ResearchDraft | None
    compiled: CompiledResearchDraft | None
    import_outcome: ResearchImportOutcome | None
    terminal_receipt: ResearchReturnReceipt | ResearchImportReceipt
    transition: ResearchJobTransition


def _validate_market_baseline(
    packet: ResearchPacketValue,
    accepted_blind_result: ResearchResultEnvelope | None,
) -> None:
    if isinstance(packet, BlindResearchPacket):
        if accepted_blind_result is not None:
            raise ResearchBaselineError("Blind execution cannot accept a prior Blind result")
        return
    if not isinstance(accepted_blind_result, ResearchResultEnvelope):
        raise ResearchBaselineError("Market execution requires the exact accepted Blind result")
    try:
        frozen = ResearchResultEnvelope.model_validate(
            accepted_blind_result.model_dump(mode="python")
        )
    except (TypeError, ValueError) as error:
        raise ResearchBaselineError("accepted Blind result cannot be replayed") from error
    refs = tuple(ref for ref in packet.provenance if ref.relation == "accepted_blind_result")
    if (
        frozen.canonical_sha256 != accepted_blind_result.canonical_sha256
        or frozen.packet_stage != PacketStage.BLIND
        or packet.blind_result_id != frozen.result_id
        or packet.blind_packet_id != frozen.packet_id
        or len(refs) != 1
        or refs[0].source_artifact_id != frozen.result_id
        or refs[0].content_sha256 != frozen.canonical_sha256
        or frozen.evidence != packet.blind_evidence
        or frozen.completed_at > packet.created_at
    ):
        raise ResearchBaselineError("Market packet does not bind the accepted Blind result")


def prepare_research_execution(
    *,
    repository: AlphaRepository,
    artifact_root: Path,
    packet: ResearchPacketValue,
    rule_contract: RuleContract,
    provider_policy_id: str,
    source_policy_id: str,
    max_attempts: int,
    available_at: datetime,
    expires_at: datetime,
    leased_at: datetime,
    lease_duration: timedelta,
    worker_id: str,
    run_id: str,
    created_at: datetime,
    accepted_blind_result: ResearchResultEnvelope | None = None,
) -> PreparedResearchExecution:
    """Seal a controlled brief and atomically publish attempt-one lease facts."""

    _validate_market_baseline(packet, accepted_blind_result)
    created_at = ensure_utc(created_at)
    brief_bytes = build_research_brief(
        packet,
        created_at=created_at,
        accepted_blind_result=accepted_blind_result,
    ).canonical_bytes()
    brief_hash = bytes_sha256(brief_bytes)
    brief_locator = f"_sealed/research_briefs/{packet.record_id}/{brief_hash}.json"
    try:
        ArtifactStore(Path(artifact_root)).write_immutable(brief_locator, brief_bytes)
    except (ArtifactConflictError, ArtifactPathError) as error:
        raise ResearchOrchestrationError(f"cannot seal controlled brief: {error}") from error
    job = build_research_job(
        packet=packet,
        brief_bytes=brief_bytes,
        brief_artifact_locator=brief_locator,
        rule_contract=rule_contract,
        provider_policy_id=provider_policy_id,
        source_policy_id=source_policy_id,
        max_attempts=max_attempts,
        available_at=available_at,
        expires_at=expires_at,
        run_id=run_id,
        created_at=created_at,
    )
    lease = lease_research_job(
        job=job,
        attempt_number=1,
        worker_id=worker_id,
        leased_at=leased_at,
        lease_duration=lease_duration,
        run_id=run_id,
    )
    repository.save_contracts_atomic(
        (job, lease.attempt, lease.work_order, lease.transition)
    )
    return PreparedResearchExecution(job, lease, brief_locator, brief_hash)


def _bind_sealed_draft(
    *, store: ArtifactStore, sealed: SealedProviderReturn
) -> tuple[ResearchDraft, tuple[ActualSourceBytes, ...]]:
    receipt = sealed.receipt
    if receipt.returned_artifact_locator is None or receipt.returned_bytes_sha256 is None:
        raise ResearchResumeError("sealed return lacks draft artifact binding")
    try:
        store.read_verified(
            sealed.manifest_locator, expected_sha256=sealed.manifest_bytes_sha256
        )
        draft_bytes = store.read_verified(
            receipt.returned_artifact_locator,
            expected_sha256=receipt.returned_bytes_sha256,
        )
        raw = json.loads(draft_bytes)
        draft = ResearchDraft.model_validate(raw)
    except (ArtifactConflictError, ArtifactPathError) as error:
        raise ResearchResumeError(f"sealed return artifact verification failed: {error}") from error
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError, ValueError) as error:
        raise ResearchDraftError(f"provider draft validation failed: {error}") from error
    captured_keys = {
        source.source_key
        for source in draft.sources
        if source.capture_scope.value != "REFERENCE_ONLY"
    }
    if captured_keys != set(sealed.source_artifacts):
        raise ResearchDraftError("sealed source bytes must cover captured draft sources exactly")
    actual: list[ActualSourceBytes] = []
    replaced_sources = []
    for source in draft.sources:
        if source.source_key not in captured_keys:
            replaced_sources.append(source)
            continue
        binding = sealed.source_artifacts[source.source_key]
        try:
            content = store.read_verified(
                binding.locator, expected_sha256=binding.bytes_sha256
            )
        except (ArtifactConflictError, ArtifactPathError) as error:
            raise ResearchResumeError(
                f"sealed source artifact verification failed: {source.source_key}"
            ) from error
        if len(content) != binding.byte_length:
            raise ResearchResumeError("sealed source artifact length mismatch")
        replaced_sources.append(source.model_copy(update={"artifact_locator": binding.locator}))
        actual.append(ActualSourceBytes(source_key=source.source_key, content=content))
    return draft.model_copy(update={"sources": tuple(replaced_sources)}), tuple(actual)


def _transition(
    *,
    job: ResearchJob,
    attempt_id: str,
    to_status: ResearchJobStatus,
    reason: ResearchTransitionReason,
    cause: ResearchReturnReceipt | ResearchImportReceipt,
    effective_at: datetime,
    run_id: str,
) -> ResearchJobTransition:
    transition_id = stable_record_id(
        "research_job_transition", job.job_id, attempt_id, reason, cause.record_id
    )
    return ResearchJobTransition(
        record_id=transition_id,
        run_id=run_id,
        created_at=effective_at,
        source="research_offline_orchestrator",
        source_version=RESEARCH_ORCHESTRATOR_VERSION,
        provenance=(),
        extensions={},
        transition_id=transition_id,
        job_id=job.job_id,
        job_sha256=job.canonical_sha256,
        attempt_id=attempt_id,
        from_status=ResearchJobStatus.LEASED,
        to_status=to_status,
        reason=reason,
        cause_record_id=cause.record_id,
        cause_record_sha256=cause.canonical_sha256,
        effective_at=effective_at,
    )


def _quarantine_return(
    sealed: SealedProviderReturn, *, processed_at: datetime, run_id: str
) -> ResearchReturnReceipt:
    returned = sealed.receipt
    receipt_id = stable_record_id(
        "research_return_receipt",
        returned.work_order_id,
        returned.returned_bytes_sha256,
        "DRAFT_VALIDATION_FAILED",
    )
    return ResearchReturnReceipt(
        record_id=receipt_id,
        run_id=run_id,
        created_at=processed_at,
        source="research_offline_orchestrator",
        source_version=RESEARCH_ORCHESTRATOR_VERSION,
        provenance=(),
        extensions={"provider_return_receipt_id": returned.record_id},
        return_receipt_id=receipt_id,
        job_id=returned.job_id,
        job_sha256=returned.job_sha256,
        attempt_id=returned.attempt_id,
        attempt_sha256=returned.attempt_sha256,
        work_order_id=returned.work_order_id,
        work_order_sha256=returned.work_order_sha256,
        disposition=ResearchReturnDisposition.QUARANTINED,
        returned_artifact_locator=returned.returned_artifact_locator,
        returned_bytes_sha256=returned.returned_bytes_sha256,
        returned_byte_length=returned.returned_byte_length,
        failure_code="DRAFT_VALIDATION_FAILED",
        received_at=returned.received_at,
    )


def resume_research_execution(
    *,
    repository: AlphaRepository,
    artifact_root: Path,
    packet: ResearchPacketValue,
    rule_contract: RuleContract,
    prepared: PreparedResearchExecution,
    payload: ProviderReturnPayload,
    received_at: datetime,
    processed_at: datetime,
    run_id: str,
    accepted_blind_result: ResearchResultEnvelope | None = None,
) -> ResearchExecutionOutcome:
    """Seal, compile, import, and atomically terminalize one provider return."""

    _validate_market_baseline(packet, accepted_blind_result)
    received_at = ensure_utc(received_at)
    processed_at = ensure_utc(processed_at)
    if processed_at < received_at:
        raise ResearchResumeError("processed_at cannot precede provider received_at")
    job, lease = prepared.job, prepared.lease
    if (
        (job.packet_id, job.packet_sha256) != (packet.record_id, packet.canonical_sha256)
        or (job.rule_contract_id, job.rule_contract_sha256)
        != (rule_contract.record_id, rule_contract.canonical_sha256)
    ):
        raise ResearchResumeError("resume packet or RuleContract differs from the sealed job")
    sealed = adapt_provider_return(
        artifact_root=Path(artifact_root),
        job=job,
        attempt=lease.attempt,
        work_order=lease.work_order,
        payload=payload,
        received_at=received_at,
        run_id=run_id,
    )
    try:
        draft, actual_sources = _bind_sealed_draft(
            store=ArtifactStore(Path(artifact_root)), sealed=sealed
        )
        compiled = compile_research_draft(
            packet=packet,
            draft=draft,
            actual_sources=actual_sources,
            run_id=run_id,
            created_at=processed_at,
            accepted_blind_result=accepted_blind_result,
        )
    except ResearchDraftError:
        quarantined = _quarantine_return(
            sealed, processed_at=processed_at, run_id=run_id
        )
        transition = _transition(
            job=job,
            attempt_id=lease.attempt.attempt_id,
            to_status=ResearchJobStatus.QUARANTINED,
            reason=ResearchTransitionReason.RESULT_QUARANTINED,
            cause=quarantined,
            effective_at=processed_at,
            run_id=run_id,
        )
        repository.save_contracts_atomic((quarantined, transition))
        return ResearchExecutionOutcome(
            sealed, None, None, None, quarantined, transition
        )

    submitted_bytes = canonical_json(compiled.result).encode("utf-8")
    submitted_hash = bytes_sha256(submitted_bytes)
    submitted_locator = (
        f"_sealed/research_imports/{lease.work_order.work_order_id}/"
        f"{submitted_hash}.json"
    )
    try:
        ArtifactStore(Path(artifact_root)).write_immutable(
            submitted_locator, submitted_bytes
        )
    except (ArtifactConflictError, ArtifactPathError) as error:
        raise ResearchResumeError(f"cannot seal compiled result: {error}") from error
    imported = import_research_result(
        packet=packet,
        submitted_bytes=submitted_bytes,
        source_contents=compiled.source_contents,
        imported_at=processed_at,
        run_id=run_id,
        submitted_artifact_locator=submitted_locator,
        expected_submitted_sha256=submitted_hash,
    )
    accepted = imported.receipt.status == ResearchImportStatus.ACCEPTED
    transition = _transition(
        job=job,
        attempt_id=lease.attempt.attempt_id,
        to_status=(ResearchJobStatus.COMPLETED if accepted else ResearchJobStatus.QUARANTINED),
        reason=(ResearchTransitionReason.RESULT_ACCEPTED if accepted else ResearchTransitionReason.RESULT_QUARANTINED),
        cause=imported.receipt,
        effective_at=processed_at,
        run_id=run_id,
    )
    records = [sealed.receipt, imported.submitted_artifact]
    if imported.result is not None:
        records.append(imported.result)
    records.extend((imported.receipt, transition))
    repository.save_contracts_atomic(tuple(records))
    return ResearchExecutionOutcome(
        sealed, draft, compiled, imported, imported.receipt, transition
    )


__all__ = [
    "RESEARCH_ORCHESTRATOR_VERSION",
    "PreparedResearchExecution",
    "ResearchBaselineError",
    "ResearchExecutionOutcome",
    "ResearchOrchestrationError",
    "ResearchResumeError",
    "prepare_research_execution",
    "resume_research_execution",
]
