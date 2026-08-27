"""Synthetic-clock scheduling policy for offline Alpha research jobs.

This module owns timing, dedupe and budget decisions only.  It invokes the
released offline orchestrator to publish work orders, but does not poll, sleep,
fetch, execute a provider, start a daemon, or mutate any production owner.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

from ..artifacts import ArtifactConflictError, ArtifactPathError, ArtifactStore
from ..contracts import (
    BlindResearchPacket,
    MarketResearchPacket,
    ResearchJob,
    ResearchJobStatus,
    ResearchJobTransition,
    ResearchResultEnvelope,
    ResearchReturnDisposition,
    ResearchReturnReceipt,
    ResearchTransitionReason,
    RuleContract,
    bytes_sha256,
    canonical_datetime,
    canonical_json,
    stable_record_id,
)
from ..contracts.base import ensure_utc
from ..storage import AlphaRepository
from .automation import ResearchLeaseGrant, lease_research_job
from .orchestrator import PreparedResearchExecution, prepare_research_execution


SYNTHETIC_SCHEDULER_VERSION = "p1_a05_synthetic_scheduler_v1"
ResearchPacketValue = BlindResearchPacket | MarketResearchPacket


class SyntheticSchedulerError(ValueError):
    """Synthetic scheduling input or immutable receipt is invalid."""


class ScheduleDisposition(StrEnum):
    LEASED = "LEASED"
    DISABLED = "DISABLED"
    CLOCK_SKEW = "CLOCK_SKEW"
    NOT_DUE = "NOT_DUE"
    STALE = "STALE"
    DEDUPED = "DEDUPED"
    RUN_BUDGET_EXHAUSTED = "RUN_BUDGET_EXHAUSTED"
    DAY_JOB_BUDGET_EXHAUSTED = "DAY_JOB_BUDGET_EXHAUSTED"
    DAY_BYTE_BUDGET_EXHAUSTED = "DAY_BYTE_BUDGET_EXHAUSTED"
    PREPARE_FAILED = "PREPARE_FAILED"


@dataclass(frozen=True, slots=True)
class SyntheticSchedulerPolicy:
    scheduler_id: str
    policy_version: str
    enabled: bool
    max_jobs_per_tick: int
    max_jobs_per_day: int
    max_estimated_artifact_bytes_per_day: int
    max_attempts: int
    job_ttl: timedelta
    lease_duration: timedelta
    dedupe_window: timedelta
    max_clock_skew: timedelta

    def __post_init__(self) -> None:
        if not self.scheduler_id.strip() or not self.policy_version.strip():
            raise SyntheticSchedulerError("scheduler identity and policy version are required")
        counts = (
            self.max_jobs_per_tick,
            self.max_jobs_per_day,
            self.max_estimated_artifact_bytes_per_day,
        )
        if any(value < 0 for value in counts):
            raise SyntheticSchedulerError("scheduler budgets cannot be negative")
        if self.max_attempts < 1 or self.max_attempts > 10:
            raise SyntheticSchedulerError("max_attempts must be between 1 and 10")
        durations = (
            self.job_ttl,
            self.lease_duration,
            self.dedupe_window,
            self.max_clock_skew,
        )
        if any(value < timedelta(0) for value in durations):
            raise SyntheticSchedulerError("scheduler durations cannot be negative")
        if self.job_ttl <= timedelta(0) or self.lease_duration <= timedelta(0):
            raise SyntheticSchedulerError("job TTL and lease duration must be positive")

    @property
    def canonical_sha256(self) -> str:
        return bytes_sha256(canonical_json(_policy_payload(self)).encode("utf-8"))


@dataclass(frozen=True, slots=True)
class ResearchScheduleRequest:
    packet: ResearchPacketValue
    rule_contract: RuleContract
    due_at: datetime
    candidate_fresh_until: datetime
    priority: int
    estimated_artifact_bytes: int
    accepted_blind_result: ResearchResultEnvelope | None = None

    def __post_init__(self) -> None:
        due = ensure_utc(self.due_at)
        fresh = ensure_utc(self.candidate_fresh_until)
        object.__setattr__(self, "due_at", due)
        object.__setattr__(self, "candidate_fresh_until", fresh)
        if not isinstance(self.packet, (BlindResearchPacket, MarketResearchPacket)):
            raise SyntheticSchedulerError("schedule request requires a research packet")
        if not isinstance(self.rule_contract, RuleContract):
            raise SyntheticSchedulerError("schedule request requires a RuleContract")
        if fresh < due:
            raise SyntheticSchedulerError("candidate freshness cannot end before due_at")
        if self.priority < 0:
            raise SyntheticSchedulerError("priority cannot be negative")
        if self.estimated_artifact_bytes < 1:
            raise SyntheticSchedulerError("estimated artifact bytes must be positive")

    @property
    def request_id(self) -> str:
        return stable_record_id(
            "research_schedule_request",
            self.packet.record_id,
            self.packet.canonical_sha256,
            self.rule_contract.record_id,
            self.rule_contract.canonical_sha256,
            self.due_at,
            self.candidate_fresh_until,
            self.priority,
            self.estimated_artifact_bytes,
        )


@dataclass(frozen=True, slots=True)
class ScheduleItemReceipt:
    request_id: str
    packet_id: str
    disposition: ScheduleDisposition
    estimated_artifact_bytes: int
    job_id: str | None = None
    failure_code: str | None = None

    def payload(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "packet_id": self.packet_id,
            "disposition": self.disposition.value,
            "estimated_artifact_bytes": self.estimated_artifact_bytes,
            "job_id": self.job_id,
            "failure_code": self.failure_code,
        }


@dataclass(frozen=True, slots=True)
class SyntheticScheduleReceipt:
    receipt_id: str
    scheduler_id: str
    policy_sha256: str
    scheduled_for: datetime
    observed_at: datetime
    run_id: str
    items: tuple[ScheduleItemReceipt, ...]
    scheduler_version: str = SYNTHETIC_SCHEDULER_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "scheduled_for", ensure_utc(self.scheduled_for))
        object.__setattr__(self, "observed_at", ensure_utc(self.observed_at))

    def payload(self) -> dict[str, object]:
        return {
            "receipt_id": self.receipt_id,
            "scheduler_id": self.scheduler_id,
            "policy_sha256": self.policy_sha256,
            "scheduled_for": canonical_datetime(self.scheduled_for),
            "observed_at": canonical_datetime(self.observed_at),
            "run_id": self.run_id,
            "items": [item.payload() for item in self.items],
            "scheduler_version": self.scheduler_version,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.payload()).encode("utf-8")


@dataclass(frozen=True, slots=True)
class SyntheticScheduleRun:
    receipt: SyntheticScheduleReceipt
    receipt_locator: str
    prepared: tuple[PreparedResearchExecution, ...]


def _policy_payload(policy: SyntheticSchedulerPolicy) -> dict[str, object]:
    def microseconds(value: timedelta) -> int:
        return (
            value.days * 86_400_000_000
            + value.seconds * 1_000_000
            + value.microseconds
        )

    return {
        "scheduler_id": policy.scheduler_id,
        "policy_version": policy.policy_version,
        "enabled": policy.enabled,
        "max_jobs_per_tick": policy.max_jobs_per_tick,
        "max_jobs_per_day": policy.max_jobs_per_day,
        "max_estimated_artifact_bytes_per_day": policy.max_estimated_artifact_bytes_per_day,
        "max_attempts": policy.max_attempts,
        "job_ttl_microseconds": microseconds(policy.job_ttl),
        "lease_duration_microseconds": microseconds(policy.lease_duration),
        "dedupe_window_microseconds": microseconds(policy.dedupe_window),
        "max_clock_skew_microseconds": microseconds(policy.max_clock_skew),
    }


def run_synthetic_schedule_tick(
    *,
    repository: AlphaRepository,
    artifact_root: Path,
    requests: tuple[ResearchScheduleRequest, ...],
    policy: SyntheticSchedulerPolicy,
    scheduled_for: datetime,
    observed_at: datetime,
    prior_receipts: tuple[SyntheticScheduleReceipt, ...],
    provider_policy_id: str,
    source_policy_id: str,
    worker_id: str,
    run_id: str,
) -> SyntheticScheduleRun:
    """Evaluate exactly one caller-driven clock tick and seal its receipt."""

    scheduled_for, observed_at = map(ensure_utc, (scheduled_for, observed_at))
    if not run_id.strip() or not worker_id.strip():
        raise SyntheticSchedulerError("run_id and worker_id are required")
    receipt_id = stable_record_id(
        "synthetic_schedule_receipt",
        policy.scheduler_id,
        policy.canonical_sha256,
        scheduled_for,
    )
    same_day = tuple(
        receipt
        for receipt in prior_receipts
        if receipt.receipt_id != receipt_id
        and receipt.scheduler_id == policy.scheduler_id
        and receipt.scheduled_for.date() == scheduled_for.date()
        and receipt.scheduled_for < scheduled_for
    )
    day_leased = sum(
        item.disposition == ScheduleDisposition.LEASED
        for receipt in same_day
        for item in receipt.items
    )
    day_bytes = sum(
        item.estimated_artifact_bytes
        for receipt in same_day
        for item in receipt.items
        if item.disposition == ScheduleDisposition.LEASED
    )
    recent_request_ids = {
        item.request_id
        for receipt in same_day
        if receipt.scheduled_for >= scheduled_for - policy.dedupe_window
        for item in receipt.items
        if item.disposition == ScheduleDisposition.LEASED
    }
    recent_packet_ids = {
        item.packet_id
        for receipt in same_day
        if receipt.scheduled_for >= scheduled_for - policy.dedupe_window
        for item in receipt.items
        if item.disposition == ScheduleDisposition.LEASED
    }
    prepared: list[PreparedResearchExecution] = []
    items: list[ScheduleItemReceipt] = []
    seen_packets: set[tuple[str, str]] = set()
    clock_bad = abs(observed_at - scheduled_for) > policy.max_clock_skew
    ordered = sorted(requests, key=lambda item: (-item.priority, item.request_id))
    for request in ordered:
        disposition: ScheduleDisposition | None = None
        packet_key = (request.packet.record_id, request.packet.canonical_sha256)
        if not policy.enabled:
            disposition = ScheduleDisposition.DISABLED
        elif clock_bad:
            disposition = ScheduleDisposition.CLOCK_SKEW
        elif scheduled_for < request.due_at:
            disposition = ScheduleDisposition.NOT_DUE
        elif scheduled_for > request.candidate_fresh_until:
            disposition = ScheduleDisposition.STALE
        elif (
            packet_key in seen_packets
            or request.request_id in recent_request_ids
            or request.packet.record_id in recent_packet_ids
        ):
            disposition = ScheduleDisposition.DEDUPED
        elif len(prepared) >= policy.max_jobs_per_tick:
            disposition = ScheduleDisposition.RUN_BUDGET_EXHAUSTED
        elif day_leased + len(prepared) >= policy.max_jobs_per_day:
            disposition = ScheduleDisposition.DAY_JOB_BUDGET_EXHAUSTED
        elif (
            day_bytes
            + sum(item.estimated_artifact_bytes for item in items if item.disposition == ScheduleDisposition.LEASED)
            + request.estimated_artifact_bytes
            > policy.max_estimated_artifact_bytes_per_day
        ):
            disposition = ScheduleDisposition.DAY_BYTE_BUDGET_EXHAUSTED
        if disposition not in {
            ScheduleDisposition.DISABLED,
            ScheduleDisposition.CLOCK_SKEW,
            ScheduleDisposition.NOT_DUE,
            ScheduleDisposition.STALE,
        }:
            seen_packets.add(packet_key)
        if disposition is not None:
            items.append(
                ScheduleItemReceipt(
                    request.request_id,
                    request.packet.record_id,
                    disposition,
                    request.estimated_artifact_bytes,
                )
            )
            continue
        try:
            execution = prepare_research_execution(
                repository=repository,
                artifact_root=Path(artifact_root),
                packet=request.packet,
                rule_contract=request.rule_contract,
                provider_policy_id=provider_policy_id,
                source_policy_id=source_policy_id,
                max_attempts=policy.max_attempts,
                available_at=scheduled_for,
                expires_at=scheduled_for + policy.job_ttl,
                leased_at=scheduled_for,
                lease_duration=policy.lease_duration,
                worker_id=worker_id,
                run_id=run_id,
                created_at=scheduled_for,
                accepted_blind_result=request.accepted_blind_result,
            )
        except Exception as error:
            items.append(
                ScheduleItemReceipt(
                    request.request_id,
                    request.packet.record_id,
                    ScheduleDisposition.PREPARE_FAILED,
                    request.estimated_artifact_bytes,
                    failure_code=type(error).__name__,
                )
            )
            continue
        prepared.append(execution)
        items.append(
            ScheduleItemReceipt(
                request.request_id,
                request.packet.record_id,
                ScheduleDisposition.LEASED,
                request.estimated_artifact_bytes,
                job_id=execution.job.job_id,
            )
        )
    receipt = SyntheticScheduleReceipt(
        receipt_id=receipt_id,
        scheduler_id=policy.scheduler_id,
        policy_sha256=policy.canonical_sha256,
        scheduled_for=scheduled_for,
        observed_at=observed_at,
        run_id=run_id,
        items=tuple(items),
    )
    locator = (
        f"_sealed/research_schedule_receipts/{scheduled_for.date().isoformat()}/"
        f"{receipt_id}.json"
    )
    try:
        ArtifactStore(Path(artifact_root)).write_immutable(locator, receipt.canonical_bytes())
    except (ArtifactConflictError, ArtifactPathError) as error:
        raise SyntheticSchedulerError(f"immutable schedule receipt conflict: {error}") from error
    return SyntheticScheduleRun(receipt, locator, tuple(prepared))


def record_research_attempt_failure(
    *,
    repository: AlphaRepository,
    prepared: PreparedResearchExecution,
    failed_at: datetime,
    failure_code: str,
    run_id: str,
) -> tuple[ResearchReturnReceipt, ResearchJobTransition]:
    """Persist one bounded provider failure and derive retry/final state."""

    failed_at = ensure_utc(failed_at)
    if not failure_code.strip():
        raise SyntheticSchedulerError("failure_code is required")
    job, attempt, work = (
        prepared.job,
        prepared.lease.attempt,
        prepared.lease.work_order,
    )
    if failed_at < attempt.leased_at:
        raise SyntheticSchedulerError("attempt failure predates its lease")
    receipt_id = stable_record_id(
        "research_return_receipt", work.work_order_id, "failed", failure_code
    )
    receipt = ResearchReturnReceipt(
        record_id=receipt_id,
        run_id=run_id,
        created_at=failed_at,
        source="synthetic_research_scheduler",
        source_version=SYNTHETIC_SCHEDULER_VERSION,
        provenance=(),
        extensions={},
        return_receipt_id=receipt_id,
        job_id=job.job_id,
        job_sha256=job.canonical_sha256,
        attempt_id=attempt.attempt_id,
        attempt_sha256=attempt.canonical_sha256,
        work_order_id=work.work_order_id,
        work_order_sha256=work.canonical_sha256,
        disposition=ResearchReturnDisposition.FAILED,
        failure_code=failure_code,
        received_at=failed_at,
    )
    ttl_expired = failed_at >= job.expires_at
    has_retry = attempt.attempt_number < job.max_attempts and not ttl_expired
    if ttl_expired:
        reason = ResearchTransitionReason.JOB_TTL_EXPIRED
    elif has_retry:
        reason = ResearchTransitionReason.ATTEMPT_FAILED
    else:
        reason = ResearchTransitionReason.RETRY_EXHAUSTED
    to_status = ResearchJobStatus.RETRY_PENDING if has_retry else ResearchJobStatus.FAILED
    transition_id = stable_record_id(
        "research_job_transition", job.job_id, attempt.attempt_id, reason, receipt_id
    )
    transition = ResearchJobTransition(
        record_id=transition_id,
        run_id=run_id,
        created_at=failed_at,
        source="synthetic_research_scheduler",
        source_version=SYNTHETIC_SCHEDULER_VERSION,
        provenance=(),
        extensions={"job_ttl_expired": ttl_expired},
        transition_id=transition_id,
        job_id=job.job_id,
        job_sha256=job.canonical_sha256,
        attempt_id=attempt.attempt_id,
        from_status=ResearchJobStatus.LEASED,
        to_status=to_status,
        reason=reason,
        cause_record_id=(job.record_id if ttl_expired else receipt.record_id),
        cause_record_sha256=(job.canonical_sha256 if ttl_expired else receipt.canonical_sha256),
        effective_at=failed_at,
    )
    repository.save_contracts_atomic((receipt, transition))
    return receipt, transition


def lease_research_retry(
    *,
    repository: AlphaRepository,
    job: ResearchJob,
    attempt_number: int,
    worker_id: str,
    leased_at: datetime,
    lease_duration: timedelta,
    run_id: str,
) -> PreparedResearchExecution:
    """Lease exactly the next retry after the append-only failure transition."""

    grant: ResearchLeaseGrant = lease_research_job(
        job=job,
        attempt_number=attempt_number,
        worker_id=worker_id,
        leased_at=leased_at,
        lease_duration=lease_duration,
        from_status=ResearchJobStatus.RETRY_PENDING,
        run_id=run_id,
    )
    repository.save_contracts_atomic((grant.attempt, grant.work_order, grant.transition))
    return PreparedResearchExecution(
        job, grant, job.brief_artifact_locator, job.brief_bytes_sha256
    )


__all__ = [
    "SYNTHETIC_SCHEDULER_VERSION",
    "ResearchScheduleRequest",
    "ScheduleDisposition",
    "ScheduleItemReceipt",
    "SyntheticScheduleReceipt",
    "SyntheticScheduleRun",
    "SyntheticSchedulerError",
    "SyntheticSchedulerPolicy",
    "lease_research_retry",
    "record_research_attempt_failure",
    "run_synthetic_schedule_tick",
]
