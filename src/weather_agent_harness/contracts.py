"""Versioned contracts shared by the weather agent harness."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


SCHEMA_VERSION = "weather_agent_harness_v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def stable_hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


class RiskLevel(StrEnum):
    READ_ONLY = "read_only"
    DERIVED_DATA_WRITE = "derived_data_write"
    REPOSITORY_WRITE = "repository_write"
    PRODUCTION_CHANGE = "production_change"
    LIVE_FUNDS = "live_funds"
    DESTRUCTIVE = "destructive"


class RunStatus(StrEnum):
    ACTIVE = "active"
    COMPLETE = "complete"
    WAIT_FOR_EVIDENCE = "wait_for_evidence"
    REQUIRE_AUTHORITY = "require_authority"
    FAILED = "failed"


class CompletionState(StrEnum):
    CONTINUE = "continue"
    COMPLETE_QUALIFIED = "complete_qualified"
    COMPLETE_FALSIFIED = "complete_falsified"
    COMPLETE = "complete"
    WAIT_FOR_EVIDENCE = "wait_for_evidence"
    REQUIRE_AUTHORITY = "require_authority"


class HarnessModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BudgetSpec(HarnessModel):
    max_actions: int = Field(default=50, gt=0)
    max_failures: int = Field(default=5, ge=0)
    max_experiments: int = Field(default=8, ge=0)
    patience: int = Field(default=3, ge=1)
    max_parallelism: int = Field(default=1, ge=1)


class AuthoritySpec(HarnessModel):
    auto_execute: tuple[RiskLevel, ...] = (RiskLevel.READ_ONLY,)
    explicit_grants: tuple[RiskLevel, ...] = ()
    explicit_action_grants: tuple[str, ...] = ()

    @field_validator("auto_execute")
    @classmethod
    def irreversible_actions_are_never_automatic(
        cls, values: tuple[RiskLevel, ...]
    ) -> tuple[RiskLevel, ...]:
        forbidden = {
            RiskLevel.PRODUCTION_CHANGE,
            RiskLevel.LIVE_FUNDS,
            RiskLevel.DESTRUCTIVE,
        }
        if forbidden.intersection(values):
            raise ValueError(
                "production_change, live_funds and destructive cannot be auto_execute"
            )
        return values


class ContextRef(HarnessModel):
    path: str
    phases: tuple[str, ...] = ()
    required: bool = True
    max_chars: int = Field(default=30_000, gt=0)


class DomainSpec(HarnessModel):
    """Portable completion contract for task types without a code plugin."""

    initial_phase: str = "WORK"
    terminal_phase: str = "DONE"
    phase_tools: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    terminal_state: CompletionState = CompletionState.COMPLETE
    auto_complete_on_acceptance: bool = True

    @field_validator("initial_phase", "terminal_phase")
    @classmethod
    def phases_are_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("phase must not be blank")
        return value.strip()

    @field_validator("terminal_state")
    @classmethod
    def terminal_state_is_terminal(cls, value: CompletionState) -> CompletionState:
        if value not in {
            CompletionState.COMPLETE,
            CompletionState.COMPLETE_QUALIFIED,
            CompletionState.COMPLETE_FALSIFIED,
        }:
            raise ValueError("terminal_state must be a COMPLETE state")
        return value


class TaskSpec(HarnessModel):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    task_type: str
    objective: str
    scope: dict[str, Any] = Field(default_factory=dict)
    acceptance: tuple[str, ...]
    authority: AuthoritySpec = Field(default_factory=AuthoritySpec)
    budgets: BudgetSpec = Field(default_factory=BudgetSpec)
    frozen_inputs: dict[str, Any] = Field(default_factory=dict)
    context_refs: tuple[ContextRef, ...] = ()
    domain: DomainSpec | None = None
    created_at_utc: str = Field(default_factory=utc_now)

    @field_validator("run_id", "task_type", "objective")
    @classmethod
    def required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value.strip()

    @field_validator("acceptance")
    @classmethod
    def acceptance_is_unique(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values:
            raise ValueError("at least one acceptance condition is required")
        if len(values) != len(set(values)):
            raise ValueError("acceptance conditions must be unique")
        return values

    @property
    def task_hash(self) -> str:
        # exclude_none preserves hashes for v1 TaskSpec files written before the
        # portable domain contract existed.
        return stable_hash(self.model_dump(mode="json", exclude_none=True))


class ActionRequest(HarnessModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str
    expected_evidence: tuple[str, ...] = ()

    @property
    def action_hash(self) -> str:
        return stable_hash(self.model_dump(mode="json"))


class Finding(HarnessModel):
    finding_id: str
    severity: str
    summary: str
    evidence_refs: tuple[str, ...] = ()
    status: str = "open"


class ActionResult(HarnessModel):
    status: str
    summary: str
    evidence_refs: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = ()
    mutations: tuple[dict[str, Any], ...] = ()
    facts: dict[str, Any] = Field(default_factory=dict)
    suggested_next_actions: tuple[str, ...] = ()
    started_at_utc: str | None = None
    finished_at_utc: str = Field(default_factory=utc_now)

    @field_validator("status")
    @classmethod
    def known_status(cls, value: str) -> str:
        if value not in {"succeeded", "failed", "blocked"}:
            raise ValueError("status must be succeeded, failed or blocked")
        return value


class RunState(HarnessModel):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    task_hash: str
    status: RunStatus = RunStatus.ACTIVE
    phase: str
    action_count: int = 0
    failure_count: int = 0
    completed_acceptance: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    pending_authority: dict[str, Any] | None = None
    inflight_action: ActionRequest | None = None
    inflight_started_at_utc: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    last_action: ActionRequest | None = None
    last_result: ActionResult | None = None
    completion_state: CompletionState = CompletionState.CONTINUE
    updated_at_utc: str = Field(default_factory=utc_now)

    def mark_acceptance(self, *keys: str) -> None:
        values = list(self.completed_acceptance)
        for key in keys:
            if key not in values:
                values.append(key)
        self.completed_acceptance = tuple(values)


class CompletionDecision(HarnessModel):
    state: CompletionState
    reason: str
    unmet_acceptance: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()


class EvidenceEntry(HarnessModel):
    schema_version: str = SCHEMA_VERSION
    sequence: int
    event_type: str
    run_id: str
    phase: str
    created_at_utc: str = Field(default_factory=utc_now)
    action: ActionRequest | None = None
    result: ActionResult | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    previous_entry_hash: str | None = None
    entry_hash: str = ""

    @property
    def calculated_hash(self) -> str:
        return stable_hash(self.model_dump(mode="json", exclude={"entry_hash"}))


__all__ = [
    "ActionRequest",
    "ActionResult",
    "AuthoritySpec",
    "BudgetSpec",
    "CompletionDecision",
    "CompletionState",
    "ContextRef",
    "DomainSpec",
    "EvidenceEntry",
    "Finding",
    "RiskLevel",
    "RunState",
    "RunStatus",
    "SCHEMA_VERSION",
    "TaskSpec",
    "stable_hash",
    "stable_json",
    "utc_now",
]
