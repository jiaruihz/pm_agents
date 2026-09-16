"""Completion rules for the production E2E audit and repair task."""

from __future__ import annotations

from ..contracts import (
    ActionRequest,
    ActionResult,
    CompletionDecision,
    CompletionState,
    RunState,
    TaskSpec,
)


PRODUCTION_TASK_TYPE = "production_e2e_audit_30_target_dates"

PRODUCTION_ACCEPTANCE = (
    "production_identity_verified",
    "canonical_synced",
    "account_reconciled",
    "exposures_listed",
    "lineage_covered",
    "findings_resolved",
    "replay_completed",
    "tests_passed",
    "summary_delivered",
)


class ProductionAuditDomain:
    task_type = PRODUCTION_TASK_TYPE
    initial_phase = "PREFLIGHT"

    _PHASE_TO_TOOLS = {
        "PREFLIGHT": {"production.manifest"},
        "SYNC": {"production.sync_canonical"},
        "RECONCILE": {"production.account_reconcile"},
        "EXPOSURE": {"production.exposure"},
        "LINEAGE": {"production.lineage"},
        "DIAGNOSE": {"production.review_findings"},
        "REPAIR": {"production.repair_code", "production.repair_data"},
        "REPLAY": {"production.replay"},
        "TEST": {"production.tests"},
        "DELIVER": {"production.summary"},
    }

    _ACCEPTANCE_PHASE = {
        "production_identity_verified": "PREFLIGHT",
        "canonical_synced": "SYNC",
        "account_reconciled": "RECONCILE",
        "exposures_listed": "EXPOSURE",
        "lineage_covered": "LINEAGE",
        "findings_resolved": "DIAGNOSE",
        "replay_completed": "REPLAY",
        "tests_passed": "TEST",
        "summary_delivered": "DELIVER",
    }

    def allowed_tools(self, task: TaskSpec, state: RunState) -> set[str]:
        return set(self._PHASE_TO_TOOLS.get(state.phase, set()))

    def apply_result(
        self,
        task: TaskSpec,
        state: RunState,
        action: ActionRequest,
        result: ActionResult,
    ) -> None:
        if result.status != "succeeded":
            return
        facts = result.facts
        if action.tool_name == "production.manifest" and facts.get("identity_verified"):
            state.mark_acceptance("production_identity_verified")
        elif action.tool_name == "production.sync_canonical" and facts.get("canonical_current"):
            state.mark_acceptance("canonical_synced")
        elif action.tool_name == "production.account_reconcile" and facts.get("reconciled"):
            state.mark_acceptance("account_reconciled")
        elif action.tool_name == "production.exposure" and facts.get("complete") and facts.get("valuation_ts_utc"):
            state.mark_acceptance("exposures_listed")
        elif action.tool_name == "production.lineage" and facts.get("coverage_complete"):
            state.mark_acceptance("lineage_covered")
        elif action.tool_name == "production.review_findings":
            if facts.get("repair_required") is False:
                state.mark_acceptance("findings_resolved", "replay_completed")
            elif facts.get("repair_required") is True:
                state.metadata["repair_required"] = True
                state.metadata["affected_window"] = facts.get("affected_window")
                state.phase = "REPAIR"
                return
        elif action.tool_name in {"production.repair_code", "production.repair_data"}:
            if facts.get("root_cause_repaired"):
                state.mark_acceptance("findings_resolved")
                state.metadata["repair_evidence"] = list(result.evidence_refs)
                state.phase = "REPLAY"
                return
        elif action.tool_name == "production.replay" and facts.get("affected_window_replayed"):
            if facts.get("itemized_diff_present"):
                state.mark_acceptance("replay_completed")
        elif action.tool_name == "production.tests" and facts.get("tests_passed"):
            state.mark_acceptance("tests_passed")
        elif action.tool_name == "production.summary" and facts.get("one_page_summary"):
            state.mark_acceptance("summary_delivered")
        self._advance_to_first_unmet(task, state)

    def verify(self, task: TaskSpec, state: RunState) -> CompletionDecision:
        unmet = tuple(key for key in task.acceptance if key not in state.completed_acceptance)
        if not unmet:
            return CompletionDecision(
                state=CompletionState.COMPLETE,
                reason="all production audit evidence is closed",
            )
        if state.action_count >= task.budgets.max_actions:
            return CompletionDecision(
                state=CompletionState.WAIT_FOR_EVIDENCE,
                reason="action budget exhausted with audit evidence still open",
                unmet_acceptance=unmet,
            )
        return CompletionDecision(
            state=CompletionState.CONTINUE,
            reason=f"next unmet acceptance: {unmet[0]}",
            unmet_acceptance=unmet,
        )

    def _advance_to_first_unmet(self, task: TaskSpec, state: RunState) -> None:
        if state.phase == "REPAIR":
            return
        for key in task.acceptance:
            if key not in state.completed_acceptance:
                state.phase = self._ACCEPTANCE_PHASE[key]
                return
        state.phase = "DONE"


def production_task_spec(
    *,
    run_id: str,
    scope: dict,
    authority=None,
    budgets=None,
    context_refs=(),
) -> TaskSpec:
    kwargs = {}
    if authority is not None:
        kwargs["authority"] = authority
    if budgets is not None:
        kwargs["budgets"] = budgets
    return TaskSpec(
        run_id=run_id,
        task_type=PRODUCTION_TASK_TYPE,
        objective="Audit and close the last 30 weather target dates end to end",
        scope=scope,
        acceptance=PRODUCTION_ACCEPTANCE,
        context_refs=context_refs,
        **kwargs,
    )


__all__ = [
    "PRODUCTION_ACCEPTANCE",
    "PRODUCTION_TASK_TYPE",
    "ProductionAuditDomain",
    "production_task_spec",
]
