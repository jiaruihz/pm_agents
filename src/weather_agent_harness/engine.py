"""Stateful run loop that separates planning, policy, execution and verification."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .context import ContextBuilder
from .contracts import (
    ActionRequest,
    ActionResult,
    CompletionDecision,
    CompletionState,
    RunState,
    RunStatus,
    TaskSpec,
    utc_now,
)
from .domains.base import DomainController
from .evidence import EvidenceStore
from .planner import Planner
from .policy import PolicyEngine
from .tools import ToolContext, ToolRegistry


class HarnessEngine:
    def __init__(
        self,
        *,
        repo_root: Path,
        store: EvidenceStore,
        registry: ToolRegistry,
        domain: DomainController,
        policy: PolicyEngine | None = None,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.store = store
        self.registry = registry
        self.domain = domain
        self.policy = policy or PolicyEngine()
        self.context_builder = context_builder or ContextBuilder(self.repo_root)

    def initialize(self, task: TaskSpec) -> RunState:
        supports = getattr(self.domain, "supports", None)
        if task.task_type != self.domain.task_type and not (
            callable(supports) and supports(task)
        ):
            raise ValueError(
                f"domain {self.domain.task_type} cannot run {task.task_type}"
            )
        return self.store.initialize(task, initial_phase=self.domain.initial_phase)

    def context(self) -> dict[str, Any]:
        task, state = self._load()
        names = self.domain.allowed_tools(task, state)
        tools = self.registry.contracts_for(task.task_type, names)
        return self.context_builder.build(task, state, self.store, tools)

    def step(self, planner: Planner) -> CompletionDecision:
        task, state = self._load()
        if state.inflight_action is not None:
            raise RuntimeError(
                "run has an interrupted in-flight action; recover it before planning"
            )
        before = self.domain.verify(task, state)
        self._apply_completion(state, before)
        if before.state != CompletionState.CONTINUE:
            self.store.save_state(state)
            return before

        allowed = self.domain.allowed_tools(task, state)
        tools = self.registry.contracts_for(task.task_type, allowed)
        context = self.context_builder.build(task, state, self.store, tools)
        action = planner.choose_action(context)
        return self.execute_action(action)

    def execute_action(self, action: ActionRequest) -> CompletionDecision:
        task, state = self._load()
        if state.status != RunStatus.ACTIVE:
            raise RuntimeError(f"run is not active: {state.status.value}")
        if state.inflight_action is not None:
            raise RuntimeError(
                "run has an interrupted in-flight action; recover it before execution"
            )
        allowed = self.domain.allowed_tools(task, state)
        if action.tool_name not in allowed:
            result = ActionResult(
                status="failed",
                summary=f"tool is not allowed in phase {state.phase}: {action.tool_name}",
                facts={"allowed_tools": sorted(allowed)},
            )
            return self._record_and_verify(task, state, action, result)
        try:
            tool = self.registry.get(action.tool_name)
        except KeyError as exc:
            result = ActionResult(status="failed", summary=str(exc))
            return self._record_and_verify(task, state, action, result)

        policy = self.policy.evaluate(task, tool.contract.risk, action)
        if not policy.allowed:
            state.pending_authority = {
                "tool_name": action.tool_name,
                "arguments": action.arguments,
                "risk": tool.contract.risk.value,
                "action_hash": action.action_hash,
                "reason": policy.reason,
            }
            state.last_action = action
            state.status = RunStatus.REQUIRE_AUTHORITY
            state.completion_state = CompletionState.REQUIRE_AUTHORITY
            decision = CompletionDecision(
                state=CompletionState.REQUIRE_AUTHORITY,
                reason=policy.reason,
                unmet_acceptance=tuple(
                    key for key in task.acceptance if key not in state.completed_acceptance
                ),
            )
            self.store.append(
                "authority_required",
                phase=state.phase,
                action=action,
                payload=state.pending_authority,
            )
            self.store.save_state(state)
            return decision

        context = ToolContext(
            task=task,
            state=state,
            store=self.store,
            repo_root=self.repo_root,
        )
        self._mark_action_started(state, action, event_type="action_started")
        try:
            result = self.registry.execute(action.tool_name, action.arguments, context)
        except Exception as exc:
            result = ActionResult(
                status="failed",
                summary=f"tool raised {type(exc).__name__}: {exc}",
            )
        return self._record_and_verify(task, state, action, result)

    def prepare_external_action(self, action: ActionRequest) -> CompletionDecision:
        """Validate an agent-side action and stop before execution when authority is missing."""

        task, state = self._load()
        if state.status != RunStatus.ACTIVE:
            raise RuntimeError(f"run is not active: {state.status.value}")
        allowed = self.domain.allowed_tools(task, state)
        if action.tool_name not in allowed:
            raise ValueError(f"tool is not allowed in phase {state.phase}")
        tool = self.registry.get(action.tool_name)
        if not tool.contract.agent_side:
            raise ValueError("prepare_external_action only accepts agent-side tools")
        self.registry.validate_arguments(action.tool_name, action.arguments)
        policy = self.policy.evaluate(task, tool.contract.risk, action)
        unmet = tuple(
            key for key in task.acceptance if key not in state.completed_acceptance
        )
        if not policy.allowed:
            state.pending_authority = {
                "tool_name": action.tool_name,
                "arguments": action.arguments,
                "risk": tool.contract.risk.value,
                "action_hash": action.action_hash,
                "reason": policy.reason,
            }
            state.last_action = action
            state.status = RunStatus.REQUIRE_AUTHORITY
            state.completion_state = CompletionState.REQUIRE_AUTHORITY
            decision = CompletionDecision(
                state=CompletionState.REQUIRE_AUTHORITY,
                reason=policy.reason,
                unmet_acceptance=unmet,
            )
            self.store.append(
                "authority_required",
                phase=state.phase,
                action=action,
                payload=state.pending_authority,
            )
            self.store.save_state(state)
            return decision
        if state.inflight_action is not None:
            if state.inflight_action.action_hash == action.action_hash:
                return CompletionDecision(
                    state=CompletionState.CONTINUE,
                    reason="external action is already prepared and awaiting its result",
                    unmet_acceptance=unmet,
                )
            raise RuntimeError("a different external action is already in flight")
        state.inflight_action = action
        state.inflight_started_at_utc = utc_now()
        self.store.save_state(state)
        self.store.append(
            "external_action_prepared",
            phase=state.phase,
            action=action,
            payload={"risk": tool.contract.risk.value, "action_hash": action.action_hash},
        )
        return CompletionDecision(
            state=CompletionState.CONTINUE,
            reason="external action is valid and authorized",
            unmet_acceptance=unmet,
        )

    def record_external_result(
        self, action: ActionRequest, result: ActionResult
    ) -> CompletionDecision:
        """Record a result produced by an agent-side typed adapter.

        This is deliberately not a bypass for policy: the action must exist in
        the registry, be allowed in the phase, and be covered by TaskSpec.
        """

        task, state = self._load()
        if action.tool_name not in self.domain.allowed_tools(task, state):
            raise ValueError(f"tool is not allowed in phase {state.phase}")
        tool = self.registry.get(action.tool_name)
        self.registry.validate_arguments(action.tool_name, action.arguments)
        policy = self.policy.evaluate(task, tool.contract.risk, action)
        if not policy.allowed:
            raise PermissionError(policy.reason)
        if tool.contract.agent_side:
            inflight = state.inflight_action
            if inflight is None or inflight.action_hash != action.action_hash:
                raise RuntimeError(
                    "agent-side result requires the same action to be prepared first"
                )
        if tool.contract.agent_side and result.status == "succeeded" and not result.evidence_refs:
            raise ValueError("agent-side succeeded result requires durable evidence_refs")
        return self._record_and_verify(task, state, action, result)

    def grant_and_resume(self, risk: str, *, reason: str) -> RunState:
        from .contracts import RiskLevel

        return self.store.grant_pending_authority(RiskLevel(risk), reason=reason)

    def resume_waiting(self) -> RunState:
        task, state = self._load()
        if state.status != RunStatus.WAIT_FOR_EVIDENCE:
            raise RuntimeError("run is not waiting for evidence")
        state.status = RunStatus.ACTIVE
        state.completion_state = CompletionState.CONTINUE
        self.store.append("run_resumed", phase=state.phase)
        self.store.save_state(state)
        return state

    def recover_interrupted_action(self) -> RunState:
        """Make an interrupted action retryable only when its contract says it is safe."""

        _, state = self._load()
        action = state.inflight_action
        if action is None:
            raise RuntimeError("run has no in-flight action")
        tool = self.registry.get(action.tool_name)
        if not tool.contract.replay_safe:
            blocker = f"inflight_action_requires_reconciliation:{action.action_hash}"
            if blocker not in state.blockers:
                state.blockers = (*state.blockers, blocker)
            state.status = RunStatus.WAIT_FOR_EVIDENCE
            state.completion_state = CompletionState.WAIT_FOR_EVIDENCE
            self.store.append(
                "interrupted_action_requires_reconciliation",
                phase=state.phase,
                action=action,
                payload={"action_hash": action.action_hash},
            )
            self.store.save_state(state)
            return state
        state.inflight_action = None
        state.inflight_started_at_utc = None
        self.store.append(
            "interrupted_action_released_for_retry",
            phase=state.phase,
            action=action,
            payload={"action_hash": action.action_hash},
        )
        self.store.save_state(state)
        return state

    def run(self, planner: Planner, *, max_steps: int | None = None) -> CompletionDecision:
        task = self.store.load_task()
        limit = max_steps if max_steps is not None else task.budgets.max_actions
        decision = CompletionDecision(
            state=CompletionState.CONTINUE,
            reason="run initialized",
        )
        for _ in range(limit):
            decision = self.step(planner)
            if decision.state != CompletionState.CONTINUE:
                return decision
        return decision

    def _record_and_verify(
        self,
        task: TaskSpec,
        state: RunState,
        action: ActionRequest,
        result: ActionResult,
    ) -> CompletionDecision:
        state.action_count += 1
        if result.status == "failed":
            state.failure_count += 1
        state.last_action = action
        state.last_result = result
        state.inflight_action = None
        state.inflight_started_at_utc = None
        self.store.append(
            "action_completed",
            phase=state.phase,
            action=action,
            result=result,
        )
        self.domain.apply_result(task, state, action, result)
        if state.failure_count > task.budgets.max_failures:
            decision = CompletionDecision(
                state=CompletionState.WAIT_FOR_EVIDENCE,
                reason="failure budget exhausted; root cause or new evidence is required",
                unmet_acceptance=tuple(
                    key for key in task.acceptance if key not in state.completed_acceptance
                ),
            )
        else:
            decision = self.domain.verify(task, state)
        self._apply_completion(state, decision)
        self.store.append(
            "completion_checked",
            phase=state.phase,
            payload=decision.model_dump(mode="json"),
        )
        self.store.save_state(state)
        return decision

    def _mark_action_started(
        self, state: RunState, action: ActionRequest, *, event_type: str
    ) -> None:
        state.inflight_action = action
        state.inflight_started_at_utc = utc_now()
        self.store.save_state(state)
        self.store.append(
            event_type,
            phase=state.phase,
            action=action,
            payload={"action_hash": action.action_hash},
        )

    @staticmethod
    def _apply_completion(state: RunState, decision: CompletionDecision) -> None:
        state.completion_state = decision.state
        if decision.state == CompletionState.CONTINUE:
            state.status = RunStatus.ACTIVE
        elif decision.state in {
            CompletionState.COMPLETE,
            CompletionState.COMPLETE_QUALIFIED,
            CompletionState.COMPLETE_FALSIFIED,
        }:
            state.status = RunStatus.COMPLETE
        elif decision.state == CompletionState.WAIT_FOR_EVIDENCE:
            state.status = RunStatus.WAIT_FOR_EVIDENCE
        elif decision.state == CompletionState.REQUIRE_AUTHORITY:
            state.status = RunStatus.REQUIRE_AUTHORITY

    def _load(self) -> tuple[TaskSpec, RunState]:
        task = self.store.load_task()
        state = self.store.load_state()
        if state.task_hash != task.task_hash:
            raise RuntimeError("TaskSpec changed without a controlled revision")
        return task, state


__all__ = ["HarnessEngine"]
