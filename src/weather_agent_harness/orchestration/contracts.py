"""Versioned contracts for adaptive single- and multi-agent runs."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from ..contracts import HarnessModel, RiskLevel, stable_hash, utc_now


ORCHESTRATION_SCHEMA_VERSION = "weather_agent_orchestration_v3"
RECEIPT_SCHEMA_VERSION = "weather_agent_run_receipt_v2"


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
    fallback_role: str | None = None
    require_exact_model: bool = True
    require_usage: bool = True
    baseline_model: str = "gpt-5.6-sol"


class WorkOrder(HarnessModel):
    work_order_id: str
    objective: str
    role: str
    parent_run_id: str | None = None
    depends_on: tuple[str, ...] = ()
    scope: dict[str, Any] = Field(default_factory=dict)
    acceptance: tuple[str, ...]
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
    agent_runs: tuple[AgentRunRecord, ...] = ()
    created_at_utc: str = Field(default_factory=utc_now)
    updated_at_utc: str = Field(default_factory=utc_now)


class RunReceipt(HarnessModel):
    schema_version: str = RECEIPT_SCHEMA_VERSION
    run_id: str
    route_level: RouteLevel
    outcome: str
    harness_certified: bool | None
    task_hash: str
    ledger_head_hash: str
    ledger_sequence: int = Field(ge=1)
    started_at_utc: str
    finished_at_utc: str = Field(default_factory=utc_now)
    work_orders: dict[str, int]
    agents: tuple[AgentRunRecord, ...]
    usage: UsageRecord
    evidence_refs: tuple[str, ...]
    certification_ref: str | None = None
    receipt_hash: str = ""


__all__ = [
    "AgentRunRecord",
    "ORCHESTRATION_SCHEMA_VERSION",
    "OrchestrationState",
    "RECEIPT_SCHEMA_VERSION",
    "RequestProfile",
    "RoleSpec",
    "RouteDecision",
    "RouteLevel",
    "RunReceipt",
    "UsageRecord",
    "WorkOrder",
    "WorkResult",
    "WorkStatus",
]
