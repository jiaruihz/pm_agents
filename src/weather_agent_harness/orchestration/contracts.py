"""Versioned contracts for adaptive single- and multi-agent runs."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from ..contracts import HarnessModel, RiskLevel, stable_hash, utc_now


ORCHESTRATION_SCHEMA_VERSION = "agent_orchestration_v4"
RECEIPT_SCHEMA_VERSION = "agent_run_receipt_v3"


class VerifierKind(StrEnum):
    EVIDENCE_HASH = "evidence_hash"
    COMMAND = "command"
    JSON_ASSERTIONS = "json_assertions"


class AssertionOperator(StrEnum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    EXISTS = "exists"
    TRUTHY = "truthy"
    FALSEY = "falsey"


class RouteLevel(StrEnum):
    DIRECT = "L0_direct"
    SINGLE_HARNESS = "L1_single_harness"
    MULTI_AGENT = "L2_multi_agent"
    CONTROLLED = "L3_controlled"


class WorkStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    REVIEW = "review"
    COMPLETE = "complete"
    BLOCKED = "blocked"
    FAILED = "failed"


class RequestProfile(HarnessModel):
    objective: str
    risk: RiskLevel = RiskLevel.READ_ONLY
    estimated_stages: int = Field(default=1, ge=1)
    independent_workstreams: int = Field(default=1, ge=1)
    needs_iteration: bool = False
    needs_resume: bool = False
    needs_independent_review: bool = False
    explicit_harness: bool = False
    explicit_multi_agent: bool = False

    @field_validator("objective")
    @classmethod
    def objective_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("objective must not be blank")
        return value.strip()


class RouteDecision(HarnessModel):
    level: RouteLevel
    reasons: tuple[str, ...]
    profile_hash: str


class RoleSpec(HarnessModel):
    name: str
    requested_model: str
    reasoning_effort: str = "medium"
    sandbox: str = "read-only"
    allowed_tools: tuple[str, ...] = ()
    writable_roots: tuple[str, ...] = ()
    network_access: bool = False
    fallback_role: str | None = None
    require_exact_model: bool = True
    require_usage: bool = True
    baseline_model: str = "gpt-5.6-sol"

    @model_validator(mode="after")
    def role_family_matches_requested_model(self) -> "RoleSpec":
        """Fail closed when a conventionally named role targets another model family."""

        expected_by_prefix = {
            "luna_": "gpt-5.6-luna",
            "terra_": "gpt-5.6-terra",
            "sol_": "gpt-5.6-sol",
        }
        for prefix, expected in expected_by_prefix.items():
            if self.name.startswith(prefix) and self.requested_model != expected:
                raise ValueError(
                    f"role {self.name} requires requested_model={expected}; "
                    f"got {self.requested_model}"
                )
        return self


class JsonAssertion(HarnessModel):
    path: str
    operator: AssertionOperator = AssertionOperator.EQUALS
    expected: Any = None

    @field_validator("path")
    @classmethod
    def path_is_bounded(cls, value: str) -> str:
        value = value.strip()
        if not value or not value.startswith("$."):
            raise ValueError("JSON assertion path must start with $.")
        return value


class VerifierSpec(HarnessModel):
    acceptance_id: str
    kind: VerifierKind
    evidence_ref: str | None = None
    argv: tuple[str, ...] = ()
    expected_exit_code: int = 0
    assertions: tuple[JsonAssertion, ...] = ()
    timeout_seconds: int = Field(default=300, ge=1, le=1800)

    @field_validator("acceptance_id")
    @classmethod
    def acceptance_id_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("acceptance_id must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def kind_has_required_fields(self) -> "VerifierSpec":
        if self.kind == VerifierKind.COMMAND and not self.argv:
            raise ValueError("command verifier requires argv")
        if self.kind == VerifierKind.JSON_ASSERTIONS:
            if not self.evidence_ref or not self.assertions:
                raise ValueError(
                    "json_assertions verifier requires evidence_ref and assertions"
                )
        if self.kind == VerifierKind.EVIDENCE_HASH and not self.evidence_ref:
            raise ValueError("evidence_hash verifier requires evidence_ref")
        return self


class EvidenceRecord(HarnessModel):
    uri: str
    snapshot_uri: str | None = None
    sha256: str
    size_bytes: int = Field(ge=0)
    media_type: str
    producer_work_order_id: str
    producer_attempt: int = Field(ge=1)
    created_at_utc: str = Field(default_factory=utc_now)


class VerifierResult(HarnessModel):
    work_order_id: str
    attempt: int = Field(ge=1)
    acceptance_id: str
    kind: VerifierKind
    passed: bool
    summary: str
    evidence_records: tuple[EvidenceRecord, ...] = ()
    output_refs: tuple[str, ...] = ()
    details: dict[str, Any] = Field(default_factory=dict)
    verified_at_utc: str = Field(default_factory=utc_now)


class EffectiveExecutionProfile(HarnessModel):
    agent_type: str
    sandbox_mode: str
    network_access: bool
    allowed_tools: tuple[str, ...] = ()
    writable_roots: tuple[str, ...] = ()
    enforcement: str = "codex_agent_config"


class WorkOrder(HarnessModel):
    work_order_id: str
    objective: str
    role: str
    parent_run_id: str | None = None
    depends_on: tuple[str, ...] = ()
    scope: dict[str, Any] = Field(default_factory=dict)
    acceptance: tuple[str, ...]
    verifiers: tuple[VerifierSpec, ...] = ()
    risk: RiskLevel = RiskLevel.READ_ONLY
    write_owners: tuple[str, ...] = ()
    status: WorkStatus = WorkStatus.PENDING
    attempt: int = 0
    max_attempts: int = Field(default=2, ge=1)
    lease_timeout_seconds: int = Field(default=900, ge=30)
    heartbeat_interval_seconds: int = Field(default=60, ge=5)
    owner: str | None = None
    lease_id: str | None = None
    lease_expires_at_utc: str | None = None
    last_heartbeat_at_utc: str | None = None
    result_ref: str | None = None
    closes_acceptance: tuple[str, ...] = ()
    created_at_utc: str = Field(default_factory=utc_now)

    @field_validator("work_order_id", "objective", "role")
    @classmethod
    def required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value.strip()

    @field_validator("acceptance")
    @classmethod
    def acceptance_required(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("work order requires acceptance conditions")
        return value

    @model_validator(mode="after")
    def heartbeat_precedes_expiry(self) -> "WorkOrder":
        if self.heartbeat_interval_seconds >= self.lease_timeout_seconds:
            raise ValueError("heartbeat interval must be shorter than lease timeout")
        return self

    @model_validator(mode="after")
    def verifier_ids_are_unique_and_known(self) -> "WorkOrder":
        ids = [item.acceptance_id for item in self.verifiers]
        if len(ids) != len(set(ids)):
            raise ValueError("verifier acceptance ids must be unique")
        unknown = sorted(set(ids) - set(self.acceptance))
        if unknown:
            raise ValueError(f"verifiers reference unknown acceptance ids: {unknown}")
        return self

    @property
    def work_order_hash(self) -> str:
        return stable_hash(
            self.model_dump(
                mode="json",
                exclude={
                    "status",
                    "attempt",
                    "owner",
                    "lease_id",
                    "lease_expires_at_utc",
                    "last_heartbeat_at_utc",
                    "result_ref",
                },
            )
        )


class UsageRecord(HarnessModel):
    source: str = "unavailable"
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    billing_model: str | None = None
    service_tier: str = "standard"
    rate_card_id: str | None = None
    estimated_cost_credits: float | None = Field(default=None, ge=0)
    baseline_model: str | None = None
    baseline_cost_credits: float | None = Field(default=None, ge=0)
    savings_credits: float | None = None
    savings_ratio: float | None = None
    records_measured: int = Field(default=0, ge=0)
    records_total: int = Field(default=0, ge=0)
    usage_coverage_ratio: float = Field(default=0.0, ge=0, le=1)

    @model_validator(mode="after")
    def complete_runtime_usage(self) -> "UsageRecord":
        tokens = (self.input_tokens, self.output_tokens, self.cached_tokens)
        if self.source != "unavailable" and any(value is None for value in tokens):
            raise ValueError("available usage requires input, output and cached tokens")
        if (
            self.input_tokens is not None
            and self.cached_tokens is not None
            and self.cached_tokens > self.input_tokens
        ):
            raise ValueError("cached tokens cannot exceed input tokens")
        return self


class WorkResult(HarnessModel):
    work_order_id: str
    attempt: int = Field(ge=1)
    lease_id: str
    status: str
    summary: str
    evidence_refs: tuple[str, ...] = ()
    evidence_records: tuple[EvidenceRecord, ...] = ()
    mutations: tuple[dict[str, Any], ...] = ()
    acceptance_claims: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    observed_model: str | None = None
    execution_mode: str = "agent"
    duration_seconds: float | None = Field(default=None, ge=0)
    tool_calls: int | None = Field(default=None, ge=0)
    usage: UsageRecord | None = None
    finished_at_utc: str = Field(default_factory=utc_now)

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: str) -> str:
        if value not in {"succeeded", "failed", "blocked"}:
            raise ValueError("status must be succeeded, failed or blocked")
        return value

    @field_validator("execution_mode")
    @classmethod
    def valid_execution_mode(cls, value: str) -> str:
        if value not in {"agent", "coordinator_recovery"}:
            raise ValueError("execution_mode must be agent or coordinator_recovery")
        return value


class AgentRunRecord(HarnessModel):
    work_order_id: str
    role: str
    requested_model: str
    attempt: int = Field(default=0, ge=0)
    lease_id: str | None = None
    observed_model: str | None = None
    execution_mode: str = "agent"
    reasoning_effort: str
    thread_id: str | None = None
    started_at_utc: str = Field(default_factory=utc_now)
    last_heartbeat_at_utc: str | None = None
    finished_at_utc: str | None = None
    terminal_reason: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    tool_calls: int | None = Field(default=None, ge=0)
    usage: UsageRecord | None = None


class OrchestrationState(HarnessModel):
    schema_version: str = ORCHESTRATION_SCHEMA_VERSION
    run_id: str
    route: RouteDecision
    roles: tuple[RoleSpec, ...]
    work_orders: tuple[WorkOrder, ...] = ()
    results: tuple[WorkResult, ...] = ()
    verifier_results: tuple[VerifierResult, ...] = ()
    agent_runs: tuple[AgentRunRecord, ...] = ()
    created_at_utc: str = Field(default_factory=utc_now)
    updated_at_utc: str = Field(default_factory=utc_now)


class RunReceipt(HarnessModel):
    schema_version: str = RECEIPT_SCHEMA_VERSION
    run_id: str
    route_level: RouteLevel
    outcome: str
    harness_certified: bool | None
    # Defaults let the reader load a v2 receipt long enough to reject its schema
    # cleanly and regenerate it from current evidence.
    process_certified: bool = False
    artifact_verified: bool = False
    domain_outcome: str = "unknown"
    task_hash: str
    ledger_head_hash: str
    ledger_sequence: int = Field(ge=1)
    started_at_utc: str
    finished_at_utc: str = Field(default_factory=utc_now)
    work_orders: dict[str, int]
    agents: tuple[AgentRunRecord, ...]
    usage: UsageRecord
    worker_usage: UsageRecord | None = None
    coordinator_usage: UsageRecord | None = None
    retry_waste_credits: float | None = Field(default=None, ge=0)
    usage_coverage_ratio: float = Field(default=0.0, ge=0, le=1)
    evidence_refs: tuple[str, ...]
    evidence_records: tuple[EvidenceRecord, ...] = ()
    certification_ref: str | None = None
    receipt_hash: str = ""


__all__ = [
    "AgentRunRecord",
    "AssertionOperator",
    "EffectiveExecutionProfile",
    "EvidenceRecord",
    "ORCHESTRATION_SCHEMA_VERSION",
    "OrchestrationState",
    "RECEIPT_SCHEMA_VERSION",
    "RequestProfile",
    "RoleSpec",
    "RouteDecision",
    "RouteLevel",
    "RunReceipt",
    "UsageRecord",
    "JsonAssertion",
    "VerifierKind",
    "VerifierResult",
    "VerifierSpec",
    "WorkOrder",
    "WorkResult",
    "WorkStatus",
]
