"""Final, offline-only capability-proof aggregation for P0-11.

This module deliberately does not open a socket, inspect the live process
environment, or start a child process.  Callers collect those observations in
their sandbox-specific test/runner and submit immutable receipts here.  A
missing, skipped, unknown, or non-denied receipt makes the proof fail closed.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
import hashlib
from pathlib import Path
from typing import Iterable, Literal

from pydantic import field_validator, model_validator

from src.polymarket_alpha.contracts import (
    ALPHA_CONTRACT_VERSION,
    AlphaContract,
    CommonEnvelope,
    content_sha256,
    stable_record_id,
)
from src.polymarket_alpha.contracts.base import ensure_utc, validate_sha256

from .wave0 import SourceTreeAudit, audit_source_tree


class CanaryOutcome(StrEnum):
    """The only outcome that can contribute to an offline PASS is ``DENIED``."""

    DENIED = "DENIED"
    ALLOWED = "ALLOWED"
    ERROR = "ERROR"
    NOT_RUN = "NOT_RUN"
    SKIPPED = "SKIPPED"
    UNKNOWN = "UNKNOWN"


class ProofDecision(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


READINESS_SCOPE = "OFFLINE_IMPLEMENTATION_ONLY"

# Each named capability is independently evidenced.  In particular, generic
# HTTP clients are not treated as interchangeable: a later import of one must
# not be hidden by a passing result for another.
REQUIRED_CAPABILITIES = frozenset(
    {
        "aiohttp",
        "builtin_import",
        "eval",
        "exec",
        "httpx",
        "importlib",
        "known_execution_import",
        "network_canary",
        "os_system",
        "process_canary",
        "raw_socket",
        "requests",
        "runpy",
        "shell",
        "signing_import",
        "subprocess",
        "websocket",
    }
)

_DANGEROUS_ENV_MARKERS = (
    "ALL_PROXY",
    "API_KEY",
    "AUTH",
    "COOKIE",
    "CREDENTIAL",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "PASSWORD",
    "PRIVATE_KEY",
    "SECRET",
    "SIGNATURE",
    "TOKEN",
    "WALLET",
)


def _digest_model(model: AlphaContract, field: str) -> str:
    return content_sha256(model.model_dump(mode="python", exclude={field}))


class CapabilityCanaryReceipt(AlphaContract):
    """A caller-observed adversarial attempt, without any secret payload."""

    capability: str
    outcome: CanaryOutcome
    detail: str
    observed_at: datetime
    receipt_sha256: str

    @field_validator("capability", "detail")
    @classmethod
    def required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("canary receipt text must not be blank")
        return value

    @field_validator("observed_at")
    @classmethod
    def observed_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("receipt_sha256")
    @classmethod
    def receipt_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def receipt_hash_matches_contents(self) -> "CapabilityCanaryReceipt":
        if self.receipt_sha256 != _digest_model(self, "receipt_sha256"):
            raise ValueError("canary receipt_sha256 does not match immutable contents")
        return self

    @classmethod
    def observed(
        cls,
        *,
        capability: str,
        outcome: CanaryOutcome,
        detail: str,
        observed_at: datetime,
    ) -> "CapabilityCanaryReceipt":
        payload = {
            "capability": capability,
            "outcome": outcome,
            "detail": detail,
            "observed_at": observed_at,
        }
        return cls(**payload, receipt_sha256=content_sha256(payload))


class EnvironmentPolicyReceipt(AlphaContract):
    """Key-only environment observation; values are never retained in proof."""

    allowed_keys: tuple[str, ...]
    observed_keys: tuple[str, ...]
    observed_at: datetime
    receipt_sha256: str

    @field_validator("allowed_keys", "observed_keys")
    @classmethod
    def sorted_unique_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("environment keys must not be blank")
        if tuple(sorted(normalized)) != normalized or len(normalized) != len(set(normalized)):
            raise ValueError("environment keys must be sorted and unique")
        return normalized

    @field_validator("observed_at")
    @classmethod
    def observed_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("receipt_sha256")
    @classmethod
    def receipt_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def receipt_hash_matches_contents(self) -> "EnvironmentPolicyReceipt":
        if self.receipt_sha256 != _digest_model(self, "receipt_sha256"):
            raise ValueError("environment receipt_sha256 does not match immutable contents")
        return self

    @classmethod
    def observed(
        cls,
        *,
        allowed_keys: Iterable[str],
        observed_keys: Iterable[str],
        observed_at: datetime,
    ) -> "EnvironmentPolicyReceipt":
        payload = {
            "allowed_keys": tuple(sorted(set(allowed_keys))),
            "observed_keys": tuple(sorted(set(observed_keys))),
            "observed_at": observed_at,
        }
        return cls(**payload, receipt_sha256=content_sha256(payload))

    @property
    def passed(self) -> bool:
        observed_upper = tuple(item.upper() for item in self.observed_keys)
        return (
            set(self.observed_keys) <= set(self.allowed_keys)
            and not any(
                marker in key for key in (*map(str.upper, self.allowed_keys), *observed_upper)
                for marker in _DANGEROUS_ENV_MARKERS
            )
        )


class SourceAuditReceipt(AlphaContract):
    root: str
    files: tuple[tuple[str, str], ...]
    violation_count: int
    audit_sha256: str
    observed_at: datetime

    @field_validator("root")
    @classmethod
    def root_is_present(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("audit root must not be blank")
        return value

    @field_validator("files")
    @classmethod
    def files_are_sorted_and_hashed(cls, value: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        if not value:
            raise ValueError("source audit must enumerate at least one Python file")
        if tuple(sorted(value)) != value or len(value) != len(set(value)):
            raise ValueError("source audit files must be sorted and unique")
        for path, digest in value:
            if not path.strip():
                raise ValueError("source audit file path must not be blank")
            validate_sha256(digest)
        return value

    @field_validator("violation_count")
    @classmethod
    def violation_count_is_valid(cls, value: int) -> int:
        if value < 0:
            raise ValueError("violation_count must not be negative")
        return value

    @field_validator("audit_sha256")
    @classmethod
    def audit_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("observed_at")
    @classmethod
    def observed_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def audit_hash_matches_contents(self) -> "SourceAuditReceipt":
        if self.audit_sha256 != _digest_model(self, "audit_sha256"):
            raise ValueError("source audit_sha256 does not match immutable contents")
        return self

    @property
    def passed(self) -> bool:
        return self.violation_count == 0


def source_audit_receipt(root: str | Path, *, observed_at: datetime) -> SourceAuditReceipt:
    """Run the static policy across the supplied Alpha tree and freeze file bytes."""

    resolved = Path(root).resolve()
    audit: SourceTreeAudit = audit_source_tree(resolved)
    files = tuple(
        (str(path.relative_to(resolved)), hashlib.sha256(path.read_bytes()).hexdigest())
        for path in sorted(resolved.rglob("*.py"))
        if path.is_file()
    )
    payload = {
        "root": str(resolved),
        "files": files,
        "violation_count": len(audit.violations),
        "observed_at": observed_at,
    }
    return SourceAuditReceipt(**payload, audit_sha256=content_sha256(payload))


class FinalCapabilityProof(CommonEnvelope):
    readiness_scope: Literal["OFFLINE_IMPLEMENTATION_ONLY"] = READINESS_SCOPE
    decision: ProofDecision
    failure_reasons: tuple[str, ...]
    source_audit: SourceAuditReceipt
    environment: EnvironmentPolicyReceipt
    canaries: tuple[CapabilityCanaryReceipt, ...]
    proof_sha256: str

    @field_validator("failure_reasons")
    @classmethod
    def failure_reasons_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if tuple(sorted(value)) != value or len(value) != len(set(value)):
            raise ValueError("failure_reasons must be sorted and unique")
        return value

    @field_validator("canaries")
    @classmethod
    def canaries_are_sorted_and_unique(
        cls, value: tuple[CapabilityCanaryReceipt, ...]
    ) -> tuple[CapabilityCanaryReceipt, ...]:
        names = tuple(item.capability for item in value)
        if tuple(sorted(names)) != names or len(names) != len(set(names)):
            raise ValueError("canary capabilities must be sorted and unique")
        return value

    @field_validator("proof_sha256")
    @classmethod
    def proof_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def proof_is_self_consistent(self) -> "FinalCapabilityProof":
        if self.proof_sha256 != _digest_model(self, "proof_sha256"):
            raise ValueError("proof_sha256 does not match immutable contents")
        should_pass = not self.failure_reasons
        if (self.decision == ProofDecision.PASS) != should_pass:
            raise ValueError("proof decision must exactly match failure_reasons")
        return self

    @property
    def passed(self) -> bool:
        return self.decision == ProofDecision.PASS


def _revalidated_canary(receipt: CapabilityCanaryReceipt) -> CapabilityCanaryReceipt | None:
    """Detect pydantic ``model_copy`` tampering that bypasses validation."""

    try:
        return CapabilityCanaryReceipt.model_validate(receipt.model_dump(mode="python"))
    except ValueError:
        return None


def _revalidated_environment(receipt: EnvironmentPolicyReceipt) -> EnvironmentPolicyReceipt | None:
    try:
        return EnvironmentPolicyReceipt.model_validate(receipt.model_dump(mode="python"))
    except ValueError:
        return None


def _revalidated_audit(receipt: SourceAuditReceipt) -> SourceAuditReceipt | None:
    try:
        return SourceAuditReceipt.model_validate(receipt.model_dump(mode="python"))
    except ValueError:
        return None


def build_final_offline_proof(
    *,
    run_id: str,
    created_at: datetime,
    source_version: str,
    source_audit: SourceAuditReceipt,
    environment: EnvironmentPolicyReceipt,
    canaries: Iterable[CapabilityCanaryReceipt],
) -> FinalCapabilityProof:
    """Aggregate immutable observations into a fail-closed offline proof.

    This function never raises merely because a security check failed.  It
    returns a signed ``FAIL`` receipt so a coordinator can retain the evidence
    and block the gate.  Malformed envelope inputs still fail validation.
    """

    supplied = tuple(
        sorted(canaries, key=lambda item: (item.capability, item.receipt_sha256))
    )
    reasons: set[str] = set()
    audit = _revalidated_audit(source_audit)
    env = _revalidated_environment(environment)
    if audit is None:
        reasons.add("SOURCE_AUDIT_RECEIPT_TAMPERED")
    elif not audit.passed:
        reasons.add("SOURCE_AUDIT_VIOLATIONS")
    if env is None:
        reasons.add("ENVIRONMENT_RECEIPT_TAMPERED")
    elif not env.passed:
        reasons.add("ENVIRONMENT_POLICY_FAILED")

    names = tuple(item.capability for item in supplied)
    duplicate_names = len(names) != len(set(names))
    if duplicate_names:
        reasons.add("DUPLICATE_CAPABILITY_RECEIPT")
    for receipt in supplied:
        revalidated = _revalidated_canary(receipt)
        if revalidated is None:
            reasons.add(f"TAMPERED_{receipt.capability}")
            continue
        if receipt.capability not in REQUIRED_CAPABILITIES:
            reasons.add(f"UNKNOWN_CAPABILITY_{receipt.capability}")
        if receipt.outcome != CanaryOutcome.DENIED:
            reasons.add(f"CANARY_NOT_DENIED_{receipt.capability}_{receipt.outcome}")
    for capability in REQUIRED_CAPABILITIES - set(names):
        reasons.add(f"MISSING_CAPABILITY_{capability}")

    output_by_capability: dict[str, CapabilityCanaryReceipt] = {}
    for item in supplied:
        capability = item.capability.strip() or "tampered_empty_capability"
        canonical = (
            item
            if _revalidated_canary(item) is not None
            else CapabilityCanaryReceipt.observed(
                capability=capability,
                outcome=CanaryOutcome.UNKNOWN,
                detail="receipt failed integrity revalidation",
                observed_at=created_at,
            )
        )
        output_by_capability.setdefault(capability, canonical)
    output_canaries = tuple(
        output_by_capability[name] for name in sorted(output_by_capability)
    )
    output_audit = audit
    if output_audit is None:
        audit_payload = {
            "root": source_audit.root or "TAMPERED_SOURCE_AUDIT",
            "files": source_audit.files or (("TAMPERED", "0" * 64),),
            "violation_count": max(1, source_audit.violation_count),
            "observed_at": created_at,
        }
        output_audit = SourceAuditReceipt(
            **audit_payload, audit_sha256=content_sha256(audit_payload)
        )
    output_environment = env
    if output_environment is None:
        output_environment = EnvironmentPolicyReceipt.observed(
            allowed_keys=(),
            observed_keys=("UNVERIFIED_ENVIRONMENT",),
            observed_at=created_at,
        )
    failure_reasons = tuple(sorted(reasons))
    decision = ProofDecision.PASS if not failure_reasons else ProofDecision.FAIL
    identity = {
        "run_id": run_id,
        "source_version": source_version,
        "created_at": created_at,
        "source_audit_sha256": output_audit.audit_sha256,
        "environment_sha256": output_environment.receipt_sha256,
        "canary_sha256s": tuple(item.receipt_sha256 for item in output_canaries),
    }
    payload = {
        "schema_version": ALPHA_CONTRACT_VERSION,
        "record_id": stable_record_id("final_capability_proof", identity),
        "run_id": run_id,
        "created_at": created_at,
        "source": "p0_11_final_capability_proof",
        "source_version": source_version,
        "provenance": (),
        "extensions": {},
        "readiness_scope": READINESS_SCOPE,
        "decision": decision,
        "failure_reasons": failure_reasons,
        "source_audit": output_audit,
        "environment": output_environment,
        # Preserve the fact of a tampered input as an explicit UNKNOWN canary
        # rather than allowing nested model revalidation to conceal a failed
        # gate.
        "canaries": output_canaries,
    }
    return FinalCapabilityProof(**payload, proof_sha256=content_sha256(payload))
