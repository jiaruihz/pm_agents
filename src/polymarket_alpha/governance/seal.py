"""Deterministic, offline-only Alpha evidence seals.

This module deliberately consumes the Harness public ``WorkOrder``,
``DependencyResolver`` and ``EvidenceRecord`` contracts without owning their
state machine or storage.  It has no scheduler, transport or filesystem API:
callers supply already-frozen bytes and persist the returned manifest/receipt
where their coordinator chooses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any, Iterable, Literal

from pydantic import Field, field_validator, model_validator

from src.weather_agent_harness.orchestration import (
    DependencyResolver,
    EvidenceRecord,
    WorkOrder,
    WorkStatus,
)

from ..contracts.base import AlphaContract, CommonEnvelope, content_sha256, ensure_utc


OFFLINE_IMPLEMENTATION_ONLY = "OFFLINE_IMPLEMENTATION_ONLY"
_ALLOWED_RISKS = {"read_only", "derived_data_write"}
_ESCALATION_MARKERS = (
    "OPERATIONAL_PILOT",
    "READ_ONLY_OPERATIONAL",
    "READ_ONLY_LIVE",
    "PRODUCTION",
    "LIVE_",
    "LIVE_ORDER",
    "LIVE_FUNDS",
    "REAL_MONEY",
    "SIGNING",
    "PRIVATE_KEY",
)


class AlphaGovernanceError(ValueError):
    """Raised when an offline seal cannot establish complete integrity."""


@dataclass(frozen=True)
class AlphaEvidencePayload:
    """Caller-owned frozen bytes used to create a Harness EvidenceRecord."""

    uri: str
    media_type: str
    producer_work_order_id: str
    producer_attempt: int
    content: bytes
    created_at_utc: str
    snapshot_uri: str | None = None

    def to_record(self) -> EvidenceRecord:
        return EvidenceRecord(
            uri=self.uri,
            snapshot_uri=self.snapshot_uri,
            sha256=sha256(self.content).hexdigest(),
            size_bytes=len(self.content),
            media_type=self.media_type,
            producer_work_order_id=self.producer_work_order_id,
            producer_attempt=self.producer_attempt,
            created_at_utc=self.created_at_utc,
        )


class AlphaWorkOrderEntry(AlphaContract):
    work_order_id: str
    work_order_hash: str
    role: str
    risk: str
    depends_on: tuple[str, ...]
    write_owners: tuple[str, ...]
    status: str
    work_order_payload: dict[str, Any]


class AlphaSealManifest(CommonEnvelope):
    """Hash-bound, domain-neutral import of frozen Harness facts."""

    seal_version: Literal["alpha_p0_governance_v1"] = "alpha_p0_governance_v1"
    run_id: str
    readiness_scope: Literal["OFFLINE_IMPLEMENTATION_ONLY"]
    domain_artifact_ids: tuple[str, ...]
    work_orders: tuple[AlphaWorkOrderEntry, ...]
    evidence_records: tuple[EvidenceRecord, ...]
    telemetry: dict[str, int]
    manifest_sha256: str

    @field_validator("run_id")
    @classmethod
    def nonblank_run_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("run_id must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def hash_binds_exact_content(self) -> "AlphaSealManifest":
        expected = content_sha256(self._hash_payload())
        if self.manifest_sha256 != expected:
            raise ValueError("manifest_sha256 does not bind manifest content")
        return self

    def _hash_payload(self) -> dict[str, object]:
        return {
            "seal_version": self.seal_version,
            "run_id": self.run_id,
            "readiness_scope": self.readiness_scope,
            "domain_artifact_ids": self.domain_artifact_ids,
            "work_orders": self.work_orders,
            "evidence_records": self.evidence_records,
            "telemetry": self.telemetry,
        }


class AlphaCoordinatorCertification(CommonEnvelope):
    """A coordinator-only certification bound to an unsigned manifest."""

    certifier_work_order_id: str
    coordinator_principal: str
    certified_at: datetime
    manifest_sha256: str
    readiness_scope: Literal["OFFLINE_IMPLEMENTATION_ONLY"]
    certified: Literal[True] = True

    @field_validator("certified_at")
    @classmethod
    def certification_time_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("certifier_work_order_id", "coordinator_principal")
    @classmethod
    def nonblank_certification_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("certification text must not be blank")
        return value.strip()


class AlphaCompletionReceipt(CommonEnvelope):
    """Terminal adapter receipt; it never changes Harness or Candidate state."""

    receipt_version: Literal["alpha_p0_governance_receipt_v1"] = "alpha_p0_governance_receipt_v1"
    run_id: str
    readiness_scope: Literal["OFFLINE_IMPLEMENTATION_ONLY"]
    manifest_sha256: str
    certification_sha256: str
    coordinator_certified: Literal[True] = True
    completion_status: Literal["CERTIFIED_OFFLINE"] = "CERTIFIED_OFFLINE"
    receipt_sha256: str

    @model_validator(mode="after")
    def receipt_hash_binds_exact_content(self) -> "AlphaCompletionReceipt":
        expected = content_sha256({
            "receipt_version": self.receipt_version,
            "run_id": self.run_id,
            "readiness_scope": self.readiness_scope,
            "manifest_sha256": self.manifest_sha256,
            "certification_sha256": self.certification_sha256,
            "coordinator_certified": self.coordinator_certified,
            "completion_status": self.completion_status,
        })
        if self.receipt_sha256 != expected:
            raise ValueError("receipt_sha256 does not bind receipt content")
        return self


def _contains_escalation(value: object) -> bool:
    if isinstance(value, str):
        upper = value.upper()
        return any(marker in upper for marker in _ESCALATION_MARKERS)
    if isinstance(value, dict):
        return any(_contains_escalation(key) or _contains_escalation(item) for key, item in value.items())
    if isinstance(value, (tuple, list, set)):
        return any(_contains_escalation(item) for item in value)
    return False


def _validate_work_orders(work_orders: tuple[WorkOrder, ...]) -> None:
    if not work_orders:
        raise AlphaGovernanceError("offline seal requires at least one WorkOrder")
    try:
        DependencyResolver().validate(work_orders)
    except ValueError as error:
        raise AlphaGovernanceError(str(error)) from error
    owners: dict[str, str] = {}
    coordinator_ids = [
        item.work_order_id
        for item in work_orders
        if item.role == "integration_coordinator"
    ]
    if len(coordinator_ids) != 1:
        raise AlphaGovernanceError(
            "offline seal requires exactly one integration_coordinator WorkOrder"
        )
    for order in work_orders:
        if order.risk.value not in _ALLOWED_RISKS:
            raise AlphaGovernanceError(f"{order.work_order_id} has non-offline risk")
        if _contains_escalation(order.scope):
            raise AlphaGovernanceError(f"{order.work_order_id} attempts readiness escalation")
        for owner in order.write_owners:
            prior = owners.setdefault(owner, order.work_order_id)
            if prior != order.work_order_id:
                raise AlphaGovernanceError(
                    f"write owner overlap: {owner} owned by {prior} and {order.work_order_id}"
                )


def _validate_evidence(
    work_orders: tuple[WorkOrder, ...], payloads: tuple[AlphaEvidencePayload, ...]
) -> tuple[EvidenceRecord, ...]:
    if not payloads:
        raise AlphaGovernanceError("offline seal requires evidence payloads")
    by_id = {order.work_order_id: order for order in work_orders}
    records: list[EvidenceRecord] = []
    seen_uri: set[str] = set()
    covered: set[str] = set()
    for payload in payloads:
        if not payload.uri.strip() or payload.uri in seen_uri:
            raise AlphaGovernanceError("evidence URI must be nonblank and unique")
        seen_uri.add(payload.uri)
        order = by_id.get(payload.producer_work_order_id)
        if order is None:
            raise AlphaGovernanceError("evidence producer is not a WorkOrder")
        if payload.producer_attempt < 1 or payload.producer_attempt != order.attempt:
            raise AlphaGovernanceError("evidence attempt must equal the completed WorkOrder attempt")
        if not payload.media_type.strip():
            raise AlphaGovernanceError("evidence media type must not be blank")
        if not payload.content:
            raise AlphaGovernanceError("evidence content must not be empty")
        record = payload.to_record()
        records.append(record)
        covered.add(order.work_order_id)
    missing = sorted(set(by_id) - covered)
    if missing:
        raise AlphaGovernanceError(f"missing evidence for WorkOrders: {missing}")
    return tuple(sorted(records, key=lambda item: item.uri))


def _work_order_entries(work_orders: Iterable[WorkOrder]) -> tuple[AlphaWorkOrderEntry, ...]:
    return tuple(
        AlphaWorkOrderEntry(
            work_order_id=order.work_order_id,
            work_order_hash=order.work_order_hash,
            role=order.role,
            risk=order.risk.value,
            depends_on=tuple(sorted(order.depends_on)),
            write_owners=tuple(sorted(order.write_owners)),
            status=order.status.value,
            work_order_payload=order.model_dump(mode="json"),
        )
        for order in sorted(work_orders, key=lambda item: item.work_order_id)
    )


def _telemetry(entries: tuple[AlphaWorkOrderEntry, ...], records: tuple[EvidenceRecord, ...]) -> dict[str, int]:
    """Small observable counters; zero egress/scheduler are intentional P0 facts."""

    return {
        "work_order_count": len(entries),
        "evidence_record_count": len(records),
        "unique_write_owner_count": len({owner for entry in entries for owner in entry.write_owners}),
        "network_requests": 0,
        "schedulers_created": 0,
    }


def build_offline_seal(
    *,
    run_id: str,
    work_orders: tuple[WorkOrder, ...],
    evidence_payloads: tuple[AlphaEvidencePayload, ...],
    domain_artifact_ids: tuple[str, ...],
    certification: AlphaCoordinatorCertification,
) -> tuple[AlphaSealManifest, AlphaCompletionReceipt]:
    """Build an independently verifiable offline-only manifest and receipt.

    The certifier must be a distinct Harness WorkOrder with role
    ``integration_coordinator``.  Thus a worker setting its own status to
    COMPLETE cannot turn into a completed Alpha seal.
    """

    _validate_work_orders(work_orders)
    if (
        not domain_artifact_ids
        or any(not item.strip() for item in domain_artifact_ids)
        or len(domain_artifact_ids) != len(set(domain_artifact_ids))
    ):
        raise AlphaGovernanceError("domain artifact ids must be nonblank and unique")
    records = _validate_evidence(work_orders, evidence_payloads)
    by_id = {item.work_order_id: item for item in work_orders}
    certifier = by_id.get(certification.certifier_work_order_id)
    if certifier is None or certifier.role != "integration_coordinator":
        raise AlphaGovernanceError("completion requires a dedicated integration_coordinator WorkOrder")
    expected_dependencies = set(by_id) - {certifier.work_order_id}
    if set(certifier.depends_on) != expected_dependencies:
        raise AlphaGovernanceError(
            "integration coordinator must depend directly on every certified WorkOrder"
        )
    if any(item.status != WorkStatus.COMPLETE for item in work_orders):
        raise AlphaGovernanceError("completion requires all WorkOrders to be COMPLETE")
    if certification.readiness_scope != OFFLINE_IMPLEMENTATION_ONLY:
        raise AlphaGovernanceError("only OFFLINE_IMPLEMENTATION_ONLY can be certified")
    if (
        certification.run_id != run_id
        or certification.created_at != certification.certified_at
    ):
        raise AlphaGovernanceError("certification run/clock binding mismatch")

    entries = _work_order_entries(work_orders)
    base = {
        "seal_version": "alpha_p0_governance_v1",
        "run_id": run_id,
        "readiness_scope": OFFLINE_IMPLEMENTATION_ONLY,
        "domain_artifact_ids": tuple(sorted(domain_artifact_ids)),
        "work_orders": entries,
        "evidence_records": records,
        "telemetry": _telemetry(entries, records),
    }
    manifest_hash = content_sha256(base)
    if certification.manifest_sha256 != manifest_hash:
        raise AlphaGovernanceError("coordinator certification is not bound to this manifest")
    manifest = AlphaSealManifest(
        record_id=f"governance_manifest:{manifest_hash}",
        created_at=certification.certified_at,
        source="polymarket_alpha.governance",
        source_version="p0_10_v1",
        **base,
        manifest_sha256=manifest_hash,
    )
    certification_hash = certification.canonical_sha256
    receipt_base = {
        "receipt_version": "alpha_p0_governance_receipt_v1",
        "run_id": run_id,
        "readiness_scope": OFFLINE_IMPLEMENTATION_ONLY,
        "manifest_sha256": manifest.manifest_sha256,
        "certification_sha256": certification_hash,
        "coordinator_certified": True,
        "completion_status": "CERTIFIED_OFFLINE",
    }
    receipt_hash = content_sha256(receipt_base)
    receipt = AlphaCompletionReceipt(
        record_id=f"governance_receipt:{receipt_hash}",
        created_at=certification.certified_at,
        source="polymarket_alpha.governance",
        source_version="p0_10_v1",
        **receipt_base,
        receipt_sha256=receipt_hash,
    )
    return manifest, receipt


def verify_offline_seal(
    manifest: AlphaSealManifest,
    receipt: AlphaCompletionReceipt,
    *,
    evidence_payloads: tuple[AlphaEvidencePayload, ...],
    certification: AlphaCoordinatorCertification,
) -> None:
    """Fail closed if any manifest, receipt, certification or bytes changed."""

    if manifest.readiness_scope != OFFLINE_IMPLEMENTATION_ONLY or receipt.readiness_scope != OFFLINE_IMPLEMENTATION_ONLY:
        raise AlphaGovernanceError("readiness scope escalation is forbidden")
    if receipt.manifest_sha256 != manifest.manifest_sha256:
        raise AlphaGovernanceError("receipt manifest binding mismatch")
    if receipt.run_id != manifest.run_id:
        raise AlphaGovernanceError("receipt run binding mismatch")
    if receipt.certification_sha256 != certification.canonical_sha256:
        raise AlphaGovernanceError("receipt certification binding mismatch")
    if certification.manifest_sha256 != manifest.manifest_sha256:
        raise AlphaGovernanceError("certification manifest binding mismatch")
    if (
        certification.readiness_scope != OFFLINE_IMPLEMENTATION_ONLY
        or certification.run_id != manifest.run_id
        or certification.created_at != certification.certified_at
    ):
        raise AlphaGovernanceError("certification scope/run/clock binding mismatch")
    try:
        sealed_orders = tuple(
            WorkOrder.model_validate(item.work_order_payload)
            for item in manifest.work_orders
        )
    except ValueError as error:
        raise AlphaGovernanceError("sealed WorkOrder payload is invalid") from error
    for entry, order in zip(manifest.work_orders, sealed_orders, strict=True):
        if (
            entry.work_order_id != order.work_order_id
            or entry.work_order_hash != order.work_order_hash
            or entry.role != order.role
            or entry.risk != order.risk.value
            or entry.depends_on != tuple(sorted(order.depends_on))
            or entry.write_owners != tuple(sorted(order.write_owners))
            or entry.status != order.status.value
        ):
            raise AlphaGovernanceError("sealed WorkOrder summary does not match payload")
    _validate_work_orders(sealed_orders)
    if any(item.status != WorkStatus.COMPLETE for item in sealed_orders):
        raise AlphaGovernanceError("sealed WorkOrder is not complete")
    certifier = next(
        (item for item in sealed_orders if item.work_order_id == certification.certifier_work_order_id),
        None,
    )
    if certifier is None or certifier.role != "integration_coordinator":
        raise AlphaGovernanceError("sealed certification lacks integration coordinator")
    if set(certifier.depends_on) != {
        item.work_order_id for item in sealed_orders if item.work_order_id != certifier.work_order_id
    }:
        raise AlphaGovernanceError(
            "sealed coordinator does not depend on every certified WorkOrder"
        )
    provided = _validate_evidence(sealed_orders, evidence_payloads)
    if tuple(item.model_dump(mode="json") for item in provided) != tuple(
        item.model_dump(mode="json") for item in manifest.evidence_records
    ):
        raise AlphaGovernanceError("evidence bytes do not match sealed evidence records")
    # Model validators independently recompute both canonical content hashes.
    AlphaSealManifest.model_validate(manifest.model_dump(mode="python"))
    AlphaCompletionReceipt.model_validate(receipt.model_dump(mode="python"))


__all__ = [
    "OFFLINE_IMPLEMENTATION_ONLY",
    "AlphaCompletionReceipt",
    "AlphaCoordinatorCertification",
    "AlphaEvidencePayload",
    "AlphaGovernanceError",
    "AlphaSealManifest",
    "build_offline_seal",
    "verify_offline_seal",
]
