"""Autonomous development search with a sealed qualification boundary."""

from __future__ import annotations

from ..contracts import (
    ActionRequest,
    ActionResult,
    CompletionDecision,
    CompletionState,
    RunState,
    TaskSpec,
    stable_hash,
)


STRATEGY_TASK_TYPE = "single_family_model_iteration"

STRATEGY_ACCEPTANCE = (
    "readiness_verified",
    "baseline_established",
    "development_search_closed",
    "qualification_closed",
)


class StrategyResearchDomain:
    task_type = STRATEGY_TASK_TYPE
    initial_phase = "READINESS"

    def allowed_tools(self, task: TaskSpec, state: RunState) -> set[str]:
        return {
            "READINESS": {"strategy.readiness"},
            "BASELINE": {"strategy.baseline"},
            "DEVELOPMENT": {"strategy.experiment"},
            "QUALIFICATION": {"strategy.qualify"},
            "COLLECT": {"strategy.collect_evidence"},
        }.get(state.phase, set())

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
        if action.tool_name == "strategy.readiness":
            if facts.get("ready"):
                state.mark_acceptance("readiness_verified")
                state.phase = "BASELINE"
            else:
                state.phase = "COLLECT"
                state.metadata["resume_condition"] = facts.get("resume_condition")
        elif action.tool_name == "strategy.collect_evidence":
            if facts.get("collector_started") or facts.get("shadow_started"):
                state.metadata["evidence_collection_active"] = True
                state.metadata["resume_condition"] = facts.get("resume_condition")
        elif action.tool_name == "strategy.baseline":
            denominator_hash = str(facts.get("denominator_hash") or "")
            if not denominator_hash or not facts.get("market_baseline_present"):
                return
            state.mark_acceptance("baseline_established")
            state.metadata.update(
                {
                    "denominator_hash": denominator_hash,
                    "baseline_evidence": list(result.evidence_refs),
                    "experiments": [],
                    "no_improvement_streak": 0,
                    "generation": 1,
                }
            )
            if task.budgets.max_experiments == 0:
                self._close_development_falsified(state)
            else:
                state.phase = "DEVELOPMENT"
        elif action.tool_name == "strategy.experiment":
            self._apply_experiment(task, state, result)
        elif action.tool_name == "strategy.qualify":
            self._apply_qualification(state, result)

    def _apply_experiment(
        self, task: TaskSpec, state: RunState, result: ActionResult
    ) -> None:
        facts = result.facts
        changed = facts.get("changed_factors") or []
        if len(changed) != 1:
            state.blockers = (*state.blockers, "experiment_must_change_exactly_one_factor")
            return
        if facts.get("denominator_hash") != state.metadata.get("denominator_hash"):
            state.blockers = (*state.blockers, "development_denominator_changed")
            return
        if facts.get("used_frozen_forward"):
            state.blockers = (*state.blockers, "development_accessed_frozen_forward")
            return
        experiments = list(state.metadata.get("experiments") or [])
        record = {
            "run_id": facts.get("run_id"),
            "parent_run_id": facts.get("parent_run_id"),
            "changed_factor": changed[0],
            "development_loss_delta": facts.get("development_loss_delta"),
            "market_baseline_delta": facts.get("market_baseline_delta"),
            "development_gates_passed": bool(facts.get("development_gates_passed")),
            "code_sha": facts.get("code_sha"),
            "params_hash": facts.get("params_hash"),
            "feature_schema_hash": facts.get("feature_schema_hash"),
            "training_dates_hash": facts.get("training_dates_hash"),
            "denominator_hash": facts.get("denominator_hash"),
            "evidence_refs": list(result.evidence_refs),
        }
        experiments.append(record)
        state.metadata["experiments"] = experiments

        improved = bool(facts.get("development_improved"))
        if improved:
            state.metadata["champion"] = record
            state.metadata["no_improvement_streak"] = 0
        else:
            state.metadata["no_improvement_streak"] = int(
                state.metadata.get("no_improvement_streak") or 0
            ) + 1

        if facts.get("search_complete"):
            champion = state.metadata.get("champion") or {}
            if not champion.get("development_gates_passed"):
                self._close_development_falsified(state)
                return
            frozen_inputs = {
                "generation": state.metadata.get("generation", 1),
                "champion_run_id": champion.get("run_id"),
                "code_sha": champion.get("code_sha"),
                "params_hash": champion.get("params_hash"),
                "feature_schema_hash": champion.get("feature_schema_hash"),
                "training_dates_hash": champion.get("training_dates_hash"),
                "denominator_hash": champion.get("denominator_hash"),
            }
            if not all(frozen_inputs.values()):
                state.blockers = (*state.blockers, "champion_freeze_identity_incomplete")
                return
            state.metadata["sealed_forward"] = True
            state.metadata["frozen_champion"] = frozen_inputs
            state.metadata["frozen_champion_hash"] = stable_hash(frozen_inputs)
            state.mark_acceptance("development_search_closed")
            state.phase = "QUALIFICATION"
            return
        if (
            len(experiments) >= task.budgets.max_experiments
            or int(state.metadata.get("no_improvement_streak") or 0)
            >= task.budgets.patience
        ):
            self._close_development_falsified(state)

    @staticmethod
    def _close_development_falsified(state: RunState) -> None:
        state.mark_acceptance("development_search_closed", "qualification_closed")
        state.metadata["qualification_outcome"] = "falsified"
        state.phase = "DONE"

    def _apply_qualification(self, state: RunState, result: ActionResult) -> None:
        facts = result.facts
        expected = state.metadata.get("frozen_champion_hash")
        if not expected or facts.get("frozen_champion_hash") != expected:
            state.blockers = (*state.blockers, "qualification_identity_mismatch")
            return
        if facts.get("searcher_saw_forward_labels"):
            state.blockers = (*state.blockers, "sealed_forward_leakage")
            return
        if facts.get("insufficient_forward_evidence"):
            state.metadata["resume_condition"] = facts.get("resume_condition")
            state.metadata["qualification_outcome"] = "wait_for_evidence"
            return
        gates = facts.get("gates") or {}
        required = ("probability", "market_baseline", "forward", "execution")
        state.metadata["qualification_outcome"] = (
            "qualified" if all(gates.get(key) is True for key in required) else "falsified"
        )
        state.metadata["qualification_evidence"] = list(result.evidence_refs)
        state.mark_acceptance("qualification_closed")
        state.phase = "DONE"

    def verify(self, task: TaskSpec, state: RunState) -> CompletionDecision:
        unmet = tuple(key for key in task.acceptance if key not in state.completed_acceptance)
        outcome = state.metadata.get("qualification_outcome")
        if outcome == "qualified" and not unmet:
            return CompletionDecision(
                state=CompletionState.COMPLETE_QUALIFIED,
                reason="sealed qualification passed all preregistered gates",
            )
        if outcome == "falsified" and not unmet:
            return CompletionDecision(
                state=CompletionState.COMPLETE_FALSIFIED,
                reason=(
                    "frozen champion failed at least one qualification gate"
                    if state.metadata.get("frozen_champion_hash")
                    else "development budget closed without a qualifying champion"
                ),
            )
        if outcome == "wait_for_evidence" or (
            state.phase == "COLLECT" and state.metadata.get("evidence_collection_active")
        ):
            return CompletionDecision(
                state=CompletionState.WAIT_FOR_EVIDENCE,
                reason=str(state.metadata.get("resume_condition") or "new PIT evidence required"),
                unmet_acceptance=unmet,
            )
        if state.action_count >= task.budgets.max_actions:
            return CompletionDecision(
                state=CompletionState.WAIT_FOR_EVIDENCE,
                reason="action budget exhausted without a valid terminal conclusion",
                unmet_acceptance=unmet,
            )
        return CompletionDecision(
            state=CompletionState.CONTINUE,
            reason=f"research phase {state.phase} is not closed",
            unmet_acceptance=unmet,
        )


def strategy_task_spec(
    *,
    run_id: str,
    family: str,
    hypothesis: str,
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
        task_type=STRATEGY_TASK_TYPE,
        objective=f"Iterate {family} until qualified, falsified, waiting for evidence, or requiring authority: {hypothesis}",
        scope={**scope, "family": family, "hypothesis": hypothesis},
        acceptance=STRATEGY_ACCEPTANCE,
        context_refs=context_refs,
        **kwargs,
    )


__all__ = [
    "STRATEGY_ACCEPTANCE",
    "STRATEGY_TASK_TYPE",
    "StrategyResearchDomain",
    "strategy_task_spec",
]
