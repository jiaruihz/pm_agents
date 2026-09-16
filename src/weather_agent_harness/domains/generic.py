"""Portable domain controller for orchestration-first, non-weather tasks."""

from __future__ import annotations

from ..contracts import (
    ActionRequest,
    ActionResult,
    CompletionDecision,
    CompletionState,
    DomainSpec,
    RunState,
    TaskSpec,
)


class GenericDomain:
    """Interpret a declarative DomainSpec without task-type conditionals."""

    task_type = "*"

    def __init__(self, spec: DomainSpec | None = None) -> None:
        self.spec = spec or DomainSpec()
        self.initial_phase = self.spec.initial_phase

    def supports(self, task: TaskSpec) -> bool:
        return True

    def allowed_tools(self, task: TaskSpec, state: RunState) -> set[str]:
        return set(self.spec.phase_tools.get(state.phase, ()))

    def apply_result(
        self,
        task: TaskSpec,
        state: RunState,
        action: ActionRequest,
        result: ActionResult,
    ) -> None:
        if result.status != "succeeded":
            return
        completed = result.facts.get("completed_acceptance") or ()
        invalid = sorted(set(completed) - set(task.acceptance))
        if invalid:
            state.blockers = (*state.blockers, f"unknown_acceptance:{','.join(invalid)}")
            return
        state.mark_acceptance(*completed)
        next_phase = result.facts.get("next_phase")
        if next_phase:
            known_phases = {*self.spec.phase_tools, self.spec.terminal_phase}
            if next_phase not in known_phases:
                state.blockers = (*state.blockers, f"unknown_phase:{next_phase}")
                return
            state.phase = str(next_phase)

    def verify(self, task: TaskSpec, state: RunState) -> CompletionDecision:
        verified = state.metadata.get("machine_verified_acceptance") or {}
        unmet = tuple(key for key in task.acceptance if key not in verified)
        invalid = tuple(
            key
            for key, value in verified.items()
            if key in task.acceptance
            and (
                not isinstance(value, dict)
                or not value.get("work_order_id")
                or not value.get("verifier_acceptance_ids")
                or not value.get("evidence_hashes")
            )
        )
        if invalid:
            return CompletionDecision(
                state=CompletionState.CONTINUE,
                reason="portable acceptance has incomplete machine-verifier provenance",
                unmet_acceptance=invalid,
            )
        if not unmet:
            return CompletionDecision(
                state=self.spec.terminal_state,
                reason="portable domain acceptance contract is complete",
            )
        if state.action_count >= task.budgets.max_actions:
            return CompletionDecision(
                state=CompletionState.WAIT_FOR_EVIDENCE,
                reason="action budget exhausted with portable acceptance still open",
                unmet_acceptance=unmet,
            )
        return CompletionDecision(
            state=CompletionState.CONTINUE,
            reason=f"next unmet acceptance: {unmet[0]}",
            unmet_acceptance=unmet,
        )


__all__ = ["GenericDomain"]
