"""Offline provider boundary for immutable Alpha research work orders.

This module intentionally does not own a queue, provider client, browser, or
draft compilation.  It translates released contracts into deterministic work
facts and seals caller-supplied provider bytes before the draft compiler sees
them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
from typing import Mapping
from types import MappingProxyType

from ..artifacts import ArtifactConflictError, ArtifactPathError, ArtifactStore
from ..contracts import (
    BlindResearchPacket, MarketResearchPacket, PacketStage, ResearchAttempt,
    ResearchJob, ResearchJobStatus, ResearchJobTransition,
    ResearchReturnDisposition, ResearchReturnReceipt, ResearchTransitionReason,
    ResearchWorkOrder, RuleContract, blind_leak_reasons, bytes_sha256,
    canonical_json, stable_record_id,
)
from ..contracts.base import ensure_utc
from .brief import build_research_brief


AUTOMATION_ADAPTER_VERSION = "p1_a03_work_order_v1"
ResearchPacketValue = BlindResearchPacket | MarketResearchPacket


class ResearchAutomationError(ValueError):
    """Base typed failure for the offline work-order boundary."""


class ResearchBindingError(ResearchAutomationError):
    """A packet, job, attempt, policy, or hash binding is inconsistent."""


class ResearchLeaseError(ResearchAutomationError):
    """A requested lease is outside the released job window."""


class ResearchReturnError(ResearchAutomationError):
    """Caller-supplied provider return bytes cannot be accepted."""


class ResearchReturnLateError(ResearchReturnError):
    """A return arrived after its attempt or job expiry."""


class BlindWorkOrderLeakError(ResearchBindingError):
    """Blind work-order input contains market, side, or wallet semantics."""


class FakeExecutorPolicyError(ResearchAutomationError):
    """The deterministic executor was invoked outside its fixture policy."""


@dataclass(frozen=True, slots=True)
class ResearchLeaseGrant:
    attempt: ResearchAttempt
    work_order: ResearchWorkOrder
    transition: ResearchJobTransition


@dataclass(frozen=True, slots=True)
class ProviderReturnPayload:
    """Untrusted provider-shaped bytes; never canonical Alpha result bytes."""

    provider_draft_bytes: bytes
    source_bytes: Mapping[str, bytes]


@dataclass(frozen=True, slots=True)
class SealedProviderSource:
    locator: str
    bytes_sha256: str
    byte_length: int


@dataclass(frozen=True, slots=True)
class SealedProviderReturn:
    receipt: ResearchReturnReceipt
    source_artifacts: Mapping[str, SealedProviderSource]
    manifest_locator: str
    manifest_bytes_sha256: str


_BLIND_WORK_ORDER_EXTRA = re.compile(
    r"\b(?:trading|prediction)\s+venue\b|\b(?:yes|no)\s+(?:side|token|share)\b",
    re.IGNORECASE,
)


def _envelope(record_id: str, *, run_id: str, created_at: datetime) -> dict[str, object]:
    return {
        "record_id": record_id,
        "run_id": run_id,
        "created_at": created_at,
        "source": "research_work_order_adapter",
        "source_version": AUTOMATION_ADAPTER_VERSION,
        "provenance": (),
        "extensions": {},
    }


def _freeze_packet(packet: ResearchPacketValue) -> ResearchPacketValue:
    if not isinstance(packet, (BlindResearchPacket, MarketResearchPacket)):
        raise ResearchBindingError("expected a released BlindResearchPacket or MarketResearchPacket")
    try:
        frozen = type(packet).model_validate(packet.model_dump(mode="python"))
    except (TypeError, ValueError) as error:
        raise ResearchBindingError(f"research packet is invalid: {error}") from error
    if frozen.canonical_sha256 != packet.canonical_sha256:
        raise ResearchBindingError("research packet canonical replay mismatch")
    return frozen


def _freeze_rule(rule_contract: RuleContract) -> RuleContract:
    if not isinstance(rule_contract, RuleContract):
        raise ResearchBindingError("expected a released RuleContract")
    try:
        frozen = RuleContract.model_validate(rule_contract.model_dump(mode="python"))
    except (TypeError, ValueError) as error:
        raise ResearchBindingError(f"RuleContract is invalid: {error}") from error
    if frozen.canonical_sha256 != rule_contract.canonical_sha256:
        raise ResearchBindingError("RuleContract canonical replay mismatch")
    return frozen


def _blind_brief_is_allowlist_only(packet: BlindResearchPacket, brief_bytes: bytes) -> None:
    """Require the exact controlled Blind brief, then scan its packet boundary.

    The controlled brief contains explanatory instructions that deliberately use
    words such as ``venue`` and ``wallet`` to prohibit them.  Scanning that prose
    would create false positives, so we first require byte-identical rendering
    and scan only the packet payload that may carry data across the boundary.
    """

    try:
        value = json.loads(brief_bytes)
        created_at = ensure_utc(datetime.fromisoformat(str(value["created_at"]).replace("Z", "+00:00")))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise BlindWorkOrderLeakError("Blind brief is not a canonical controlled brief") from error
    expected = build_research_brief(packet, created_at=created_at).canonical_bytes()
    if brief_bytes != expected:
        raise BlindWorkOrderLeakError("Blind work order accepts only controlled allowlist brief bytes")
    payload = value.get("packet_payload")
    reasons = list(blind_leak_reasons(payload))
    rendered = canonical_json(payload)
    if _BLIND_WORK_ORDER_EXTRA.search(rendered):
        reasons.append("$.packet_payload:work_order_market_semantics")
    if reasons:
        raise BlindWorkOrderLeakError(f"Blind work order leaks forbidden semantics: {tuple(reasons)}")


def _validate_brief(packet: ResearchPacketValue, brief_bytes: bytes) -> None:
    if not isinstance(brief_bytes, bytes) or not brief_bytes:
        raise ResearchBindingError("brief_bytes must be non-empty immutable bytes")
    if isinstance(packet, BlindResearchPacket):
        _blind_brief_is_allowlist_only(packet, brief_bytes)
        return
    try:
        value = json.loads(brief_bytes)
    except (TypeError, json.JSONDecodeError) as error:
        raise ResearchBindingError("brief bytes must be JSON rendered by ResearchBrief") from error
    if not isinstance(value, dict) or (
        value.get("packet_stage") != packet.packet_stage.value
        or value.get("packet_id") != packet.record_id
        or value.get("packet_sha256") != packet.canonical_sha256
        or value.get("packet_bytes_sha256") != bytes_sha256(canonical_json(packet).encode("utf-8"))
        or value.get("packet_payload") != json.loads(canonical_json(packet))
    ):
        raise ResearchBindingError("brief bytes do not bind the supplied frozen packet")


def build_research_job(
    *, packet: ResearchPacketValue, brief_bytes: bytes, brief_artifact_locator: str,
    rule_contract: RuleContract, provider_policy_id: str, source_policy_id: str,
    max_attempts: int, available_at: datetime, expires_at: datetime,
    run_id: str, created_at: datetime,
) -> ResearchJob:
    """Build one deterministic QUEUED job from frozen in-process inputs only."""

    frozen_packet = _freeze_packet(packet)
    frozen_rule = _freeze_rule(rule_contract)
    if isinstance(frozen_packet, BlindResearchPacket):
        # Blind packets intentionally redact the market-bearing RuleContract id;
        # their released BlindRuleView binds the immutable rule revision hash.
        rule_matches = frozen_packet.blind_rule.rule_hash == frozen_rule.rule_hash
    else:
        rule_matches = (
            frozen_packet.rule_contract.record_id == frozen_rule.record_id
            and frozen_packet.rule_contract.canonical_sha256 == frozen_rule.canonical_sha256
        )
    if not rule_matches:
        raise ResearchBindingError("packet does not bind the supplied RuleContract")
    _validate_brief(frozen_packet, brief_bytes)
    available_at, expires_at, created_at = map(ensure_utc, (available_at, expires_at, created_at))
    if created_at > available_at:
        raise ResearchBindingError("job creation cannot follow availability")
    job_id = stable_record_id(
        "research_job", frozen_packet.record_id, frozen_packet.canonical_sha256,
        frozen_rule.record_id, frozen_rule.canonical_sha256, brief_artifact_locator,
        bytes_sha256(brief_bytes), provider_policy_id, source_policy_id, max_attempts,
        available_at, expires_at,
    )
    try:
        return ResearchJob(
            **_envelope(job_id, run_id=run_id, created_at=created_at), job_id=job_id,
            packet_stage=frozen_packet.packet_stage, packet_id=frozen_packet.record_id,
            packet_sha256=frozen_packet.canonical_sha256, rule_contract_id=frozen_rule.record_id,
            rule_contract_sha256=frozen_rule.canonical_sha256, brief_artifact_locator=brief_artifact_locator,
            brief_bytes_sha256=bytes_sha256(brief_bytes), provider_policy_id=provider_policy_id,
            source_policy_id=source_policy_id, max_attempts=max_attempts,
            available_at=available_at, expires_at=expires_at,
        )
    except (TypeError, ValueError) as error:
        raise ResearchBindingError(f"cannot build ResearchJob: {error}") from error


def lease_research_job(
    *, job: ResearchJob, attempt_number: int, worker_id: str, leased_at: datetime,
    lease_duration: timedelta, from_status: ResearchJobStatus = ResearchJobStatus.QUEUED,
    run_id: str,
) -> ResearchLeaseGrant:
    """Render an attempt, work order, and LEASE_GRANTED transition atomically."""

    if not isinstance(job, ResearchJob):
        raise ResearchBindingError("expected a ResearchJob")
    leased_at = ensure_utc(leased_at)
    if from_status not in {ResearchJobStatus.QUEUED, ResearchJobStatus.RETRY_PENDING}:
        raise ResearchLeaseError("lease may begin only from QUEUED or RETRY_PENDING")
    if attempt_number < 1 or attempt_number > job.max_attempts:
        raise ResearchLeaseError("attempt_number is outside job max_attempts")
    if from_status == ResearchJobStatus.QUEUED and attempt_number != 1:
        raise ResearchLeaseError("QUEUED job can lease only attempt one")
    if from_status == ResearchJobStatus.RETRY_PENDING and attempt_number < 2:
        raise ResearchLeaseError("RETRY_PENDING job requires a later attempt")
    if leased_at < job.available_at or leased_at >= job.expires_at:
        raise ResearchLeaseError("lease is outside job availability window")
    if lease_duration <= timedelta(0):
        raise ResearchLeaseError("lease_duration must be positive")
    lease_expires_at = min(leased_at + lease_duration, job.expires_at)
    attempt_id = stable_record_id("research_attempt", job.job_id, attempt_number)
    work_order_id = stable_record_id("research_work_order", attempt_id)
    transition_id = stable_record_id("research_job_transition", job.job_id, "lease", attempt_number)
    try:
        attempt = ResearchAttempt(
            **_envelope(attempt_id, run_id=run_id, created_at=leased_at), attempt_id=attempt_id,
            job_id=job.job_id, job_sha256=job.canonical_sha256, attempt_number=attempt_number,
            worker_id=worker_id, leased_at=leased_at, lease_expires_at=lease_expires_at,
        )
        work_order = ResearchWorkOrder(
            **_envelope(work_order_id, run_id=run_id, created_at=leased_at), work_order_id=work_order_id,
            job_id=job.job_id, job_sha256=job.canonical_sha256, attempt_id=attempt.attempt_id,
            attempt_sha256=attempt.canonical_sha256, packet_stage=job.packet_stage,
            packet_id=job.packet_id, packet_sha256=job.packet_sha256,
            brief_artifact_locator=job.brief_artifact_locator, brief_bytes_sha256=job.brief_bytes_sha256,
            provider_policy_id=job.provider_policy_id, source_policy_id=job.source_policy_id,
            issued_at=leased_at, expires_at=lease_expires_at,
        )
        transition = ResearchJobTransition(
            **_envelope(transition_id, run_id=run_id, created_at=leased_at), transition_id=transition_id,
            job_id=job.job_id, job_sha256=job.canonical_sha256, attempt_id=attempt.attempt_id,
            from_status=from_status, to_status=ResearchJobStatus.LEASED,
            reason=ResearchTransitionReason.LEASE_GRANTED, cause_record_id=attempt.record_id,
            cause_record_sha256=attempt.canonical_sha256, effective_at=leased_at,
        )
    except (TypeError, ValueError) as error:
        raise ResearchLeaseError(f"cannot create lease facts: {error}") from error
    return ResearchLeaseGrant(attempt, work_order, transition)


def _validate_return_bindings(job: ResearchJob, attempt: ResearchAttempt, work_order: ResearchWorkOrder) -> None:
    if not all(isinstance(item, expected) for item, expected in ((job, ResearchJob), (attempt, ResearchAttempt), (work_order, ResearchWorkOrder))):
        raise ResearchBindingError("return adapter requires ResearchJob, ResearchAttempt, and ResearchWorkOrder")
    if (attempt.job_id, attempt.job_sha256) != (job.job_id, job.canonical_sha256):
        raise ResearchBindingError("attempt does not bind job")
    if (work_order.job_id, work_order.job_sha256, work_order.attempt_id, work_order.attempt_sha256) != (job.job_id, job.canonical_sha256, attempt.attempt_id, attempt.canonical_sha256):
        raise ResearchBindingError("work order does not bind job and attempt")
    if (work_order.packet_stage, work_order.packet_id, work_order.packet_sha256, work_order.brief_artifact_locator, work_order.brief_bytes_sha256, work_order.provider_policy_id, work_order.source_policy_id) != (job.packet_stage, job.packet_id, job.packet_sha256, job.brief_artifact_locator, job.brief_bytes_sha256, job.provider_policy_id, job.source_policy_id):
        raise ResearchBindingError("work order does not bind the released job policy")


def adapt_provider_return(
    *, artifact_root: Path, job: ResearchJob, attempt: ResearchAttempt, work_order: ResearchWorkOrder,
    payload: ProviderReturnPayload, received_at: datetime, run_id: str,
    returned_artifact_locator: str | None = None,
) -> SealedProviderReturn:
    """Seal raw provider bytes and return only a contract receipt.

    This does not parse a draft or create a ``ResearchResultEnvelope``.  The
    caller must subsequently invoke the existing draft compiler/importer.
    """

    _validate_return_bindings(job, attempt, work_order)
    received_at = ensure_utc(received_at)
    if received_at < attempt.leased_at or received_at > attempt.lease_expires_at or received_at > job.expires_at:
        raise ResearchReturnLateError("provider return is late or outside its lease")
    if not isinstance(payload, ProviderReturnPayload) or not isinstance(payload.provider_draft_bytes, bytes) or not payload.provider_draft_bytes:
        raise ResearchReturnError("provider return requires non-empty raw draft bytes")
    if not isinstance(payload.source_bytes, Mapping):
        raise ResearchReturnError("provider source bytes must be a mapping")
    validated_sources: list[tuple[str, bytes]] = []
    for key, source_bytes in payload.source_bytes.items():
        if not isinstance(key, str) or not key.strip() or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", key):
            raise ResearchReturnError("provider source keys must be bounded safe names")
        if not isinstance(source_bytes, bytes) or not source_bytes:
            raise ResearchReturnError("provider source values must be non-empty bytes")
        validated_sources.append((key, source_bytes))
    if len({key for key, _value in validated_sources}) != len(validated_sources):
        raise ResearchReturnError("provider source keys must be unique")
    validated_sources.sort(key=lambda item: item[0])
    store = ArtifactStore(Path(artifact_root))
    draft_hash = bytes_sha256(payload.provider_draft_bytes)
    root_locator = f"_sealed/research_returns/{work_order.work_order_id}"
    locator = returned_artifact_locator or f"{root_locator}/provider_draft.json"
    try:
        store.write_immutable(locator, payload.provider_draft_bytes)
        source_artifacts: dict[str, SealedProviderSource] = {}
        for key, source_bytes in validated_sources:
            source_hash = bytes_sha256(source_bytes)
            source_locator = f"{root_locator}/sources/{key}.bin"
            store.write_immutable(source_locator, source_bytes)
            source_artifacts[key] = SealedProviderSource(
                locator=source_locator,
                bytes_sha256=source_hash,
                byte_length=len(source_bytes),
            )
        manifest_locator = f"{root_locator}/manifest.json"
        manifest_bytes = canonical_json(
            {
                "adapter_version": AUTOMATION_ADAPTER_VERSION,
                "job_id": job.job_id,
                "attempt_id": attempt.attempt_id,
                "work_order_id": work_order.work_order_id,
                "provider_policy_id": work_order.provider_policy_id,
                "source_policy_id": work_order.source_policy_id,
                "draft_locator": store.normalize_locator(locator),
                "draft_bytes_sha256": draft_hash,
                "draft_byte_length": len(payload.provider_draft_bytes),
                "sources": {
                    key: {
                        "locator": source.locator,
                        "bytes_sha256": source.bytes_sha256,
                        "byte_length": source.byte_length,
                    }
                    for key, source in source_artifacts.items()
                },
            }
        ).encode("utf-8")
        store.write_immutable(manifest_locator, manifest_bytes)
    except (ArtifactConflictError, ArtifactPathError) as error:
        raise ResearchReturnError(f"immutable provider return conflict: {error}") from error
    manifest_hash = bytes_sha256(manifest_bytes)
    receipt_id = stable_record_id(
        "research_return_receipt", work_order.work_order_id, manifest_hash
    )
    try:
        receipt = ResearchReturnReceipt(
            **{
                **_envelope(receipt_id, run_id=run_id, created_at=received_at),
                "extensions": {
                    "provider_return_manifest": {
                        "locator": manifest_locator,
                        "bytes_sha256": manifest_hash,
                    }
                },
            }, return_receipt_id=receipt_id,
            job_id=job.job_id, job_sha256=job.canonical_sha256, attempt_id=attempt.attempt_id,
            attempt_sha256=attempt.canonical_sha256, work_order_id=work_order.work_order_id,
            work_order_sha256=work_order.canonical_sha256, disposition=ResearchReturnDisposition.RETURNED,
            returned_artifact_locator=store.normalize_locator(locator), returned_bytes_sha256=draft_hash,
            returned_byte_length=len(payload.provider_draft_bytes), received_at=received_at,
        )
    except (TypeError, ValueError) as error:
        raise ResearchReturnError(f"cannot create ResearchReturnReceipt: {error}") from error
    return SealedProviderReturn(
        receipt=receipt,
        source_artifacts=MappingProxyType(source_artifacts),
        manifest_locator=manifest_locator,
        manifest_bytes_sha256=manifest_hash,
    )


@dataclass(frozen=True, slots=True)
class DeterministicFakeExecutor:
    """Fixture-only external port: caller supplies every returned byte."""

    provider_policy_id: str
    source_policy_id: str

    def execute(self, *, work_order: ResearchWorkOrder, provider_draft_bytes: bytes, source_bytes: Mapping[str, bytes]) -> ProviderReturnPayload:
        if not isinstance(work_order, ResearchWorkOrder):
            raise FakeExecutorPolicyError("fake executor requires a ResearchWorkOrder")
        if (work_order.provider_policy_id, work_order.source_policy_id) != (self.provider_policy_id, self.source_policy_id):
            raise FakeExecutorPolicyError("fake executor is restricted to its explicit fixture policy")
        if not isinstance(provider_draft_bytes, bytes) or not provider_draft_bytes:
            raise FakeExecutorPolicyError("fixture provider draft bytes must be non-empty")
        if not isinstance(source_bytes, Mapping):
            raise FakeExecutorPolicyError("fixture source bytes must be a mapping")
        copied: dict[str, bytes] = {}
        for key, value in source_bytes.items():
            if not isinstance(key, str) or not isinstance(value, bytes):
                raise FakeExecutorPolicyError("fixture source bytes must be str-to-bytes")
            copied[key] = value
        return ProviderReturnPayload(provider_draft_bytes=provider_draft_bytes, source_bytes=MappingProxyType(copied))


__all__ = [
    "AUTOMATION_ADAPTER_VERSION", "BlindWorkOrderLeakError", "DeterministicFakeExecutor",
    "FakeExecutorPolicyError", "ProviderReturnPayload", "ResearchAutomationError",
    "ResearchBindingError", "ResearchLeaseError", "ResearchLeaseGrant", "ResearchReturnError",
    "ResearchReturnLateError", "SealedProviderReturn", "SealedProviderSource", "adapt_provider_return",
    "build_research_job", "lease_research_job",
]
