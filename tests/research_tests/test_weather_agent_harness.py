from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.weather_agent_harness.catalog import CORE_CONTRACTS, build_core_registry
from src.weather_agent_harness.certification import certify_run
from src.weather_agent_harness.contracts import (
    ActionRequest,
    ActionResult,
    AuthoritySpec,
    BudgetSpec,
    CompletionState,
    RiskLevel,
    RunStatus,
)
from src.weather_agent_harness.domains.production_audit import (
    ProductionAuditDomain,
    production_task_spec,
)
from src.weather_agent_harness.domains.strategy_research import (
    StrategyResearchDomain,
    strategy_task_spec,
)
from src.weather_agent_harness.engine import HarnessEngine
from src.weather_agent_harness.evidence import EvidenceStore
from src.weather_agent_harness.tools import ToolContext, ToolRegistry


ROOT = Path(__file__).resolve().parents[2]


def action(name: str, **arguments) -> ActionRequest:
    return ActionRequest(
        tool_name=name,
        arguments=arguments,
        rationale=f"test {name}",
    )


def registry_with_handlers(handlers: dict[str, object]) -> ToolRegistry:
    registry = ToolRegistry()
    for contract in CORE_CONTRACTS:
        handler = handlers.get(contract.name)
        if handler is None:
            def missing(arguments, context, *, tool_name=contract.name):
                raise AssertionError(f"unexpected tool execution: {tool_name}")

            handler = missing
        registry.register(contract.model_copy(update={"agent_side": False}), handler)  # type: ignore[arg-type]
    return registry


def fixed(result: ActionResult):
    def handler(arguments: dict, context: ToolContext) -> ActionResult:
        return result

    return handler


def production_engine(tmp_path: Path, handlers: dict[str, object]) -> HarnessEngine:
    task = production_task_spec(
        run_id="prod-core",
        scope={"last_target_dates": 30},
        authority=AuthoritySpec(
            auto_execute=(
                RiskLevel.READ_ONLY,
                RiskLevel.DERIVED_DATA_WRITE,
                RiskLevel.REPOSITORY_WRITE,
            )
        ),
    )
    engine = HarnessEngine(
        repo_root=ROOT,
        store=EvidenceStore(tmp_path / "prod-core"),
        registry=registry_with_handlers(handlers),
        domain=ProductionAuditDomain(),
    )
    engine.initialize(task)
    return engine


def research_engine(
    tmp_path: Path,
    handlers: dict[str, object],
    *,
    budgets: BudgetSpec | None = None,
    authority: AuthoritySpec | None = None,
) -> HarnessEngine:
    task = strategy_task_spec(
        run_id="research-core",
        family="test_family",
        hypothesis="one falsifiable mechanism improves same-row proper score",
        scope={"dates": "development"},
        authority=authority
        or AuthoritySpec(
            auto_execute=(RiskLevel.READ_ONLY, RiskLevel.DERIVED_DATA_WRITE)
        ),
        budgets=budgets or BudgetSpec(),
    )
    engine = HarnessEngine(
        repo_root=ROOT,
        store=EvidenceStore(tmp_path / "research-core"),
        registry=registry_with_handlers(handlers),
        domain=StrategyResearchDomain(),
    )
    engine.initialize(task)
    return engine


def test_production_audit_cannot_finish_at_intermediate_result_and_resumes(
    tmp_path: Path,
) -> None:
    handlers = {
        "production.manifest": fixed(ActionResult(status="succeeded", summary="healthy", facts={"identity_verified": True})),
        "production.sync_canonical": fixed(ActionResult(status="succeeded", summary="current", facts={"canonical_current": True})),
        "production.account_reconcile": fixed(ActionResult(status="succeeded", summary="balanced", facts={"reconciled": True})),
        "production.exposure": fixed(ActionResult(status="succeeded", summary="listed", facts={"complete": True, "valuation_ts_utc": "2026-08-13T00:00:00Z"})),
        "production.lineage": fixed(ActionResult(status="succeeded", summary="covered", facts={"coverage_complete": True})),
        "production.review_findings": fixed(ActionResult(status="succeeded", summary="clean", facts={"repair_required": False})),
        "production.tests": fixed(ActionResult(status="succeeded", summary="passed", facts={"tests_passed": True})),
        "production.summary": fixed(ActionResult(status="succeeded", summary="one page", facts={"one_page_summary": True})),
    }
    engine = production_engine(tmp_path, handlers)

    actions = (
        action("production.manifest"),
        action(
            "production.sync_canonical",
            target_date_start="2026-07-01",
            target_date_end="2026-08-13",
        ),
        action("production.account_reconcile", start="2026-07-01", end="2026-08-13"),
        action("production.exposure"),
        action("production.lineage", start="2026-07-01", end="2026-08-13"),
    )
    for request in actions:
        decision = engine.execute_action(request)
    assert decision.state == CompletionState.CONTINUE
    assert "findings_resolved" in decision.unmet_acceptance

    resumed = HarnessEngine(
        repo_root=ROOT,
        store=engine.store,
        registry=registry_with_handlers(handlers),
        domain=ProductionAuditDomain(),
    )
    assert resumed.store.load_state().phase == "DIAGNOSE"
    resumed.execute_action(action("production.review_findings"))
    resumed.execute_action(action("production.tests"))
    decision = resumed.execute_action(action("production.summary"))

    assert decision.state == CompletionState.COMPLETE
    state = resumed.store.load_state()
    assert state.status == RunStatus.COMPLETE
    assert set(state.completed_acceptance) == set(resumed.store.load_task().acceptance)
    assert len(resumed.store.entries()) >= 17


def test_production_repair_requires_replay_itemized_diff(tmp_path: Path) -> None:
    handlers = {
        "production.repair_code": fixed(ActionResult(status="succeeded", summary="fixed", facts={"root_cause_repaired": True})),
        "production.replay": fixed(ActionResult(status="succeeded", summary="replayed", facts={"affected_window_replayed": True, "itemized_diff_present": False})),
    }
    engine = production_engine(tmp_path, handlers)
    state = engine.store.load_state()
    state.phase = "REPAIR"
    state.completed_acceptance = (
        "production_identity_verified",
        "canonical_synced",
        "account_reconciled",
        "exposures_listed",
        "lineage_covered",
    )
    engine.store.save_state(state)

    engine.execute_action(action("production.repair_code", finding_ids=["f1"]))
    decision = engine.execute_action(action("production.replay", affected_window={"start": "2026-08-01", "end": "2026-08-02"}))

    assert decision.state == CompletionState.CONTINUE
    assert "replay_completed" in decision.unmet_acceptance
    assert engine.store.load_state().phase == "REPLAY"


def test_strategy_iterates_then_seals_champion_before_qualification(
    tmp_path: Path,
) -> None:
    experiment_index = {"value": 0}

    def experiment(arguments: dict, context: ToolContext) -> ActionResult:
        experiment_index["value"] += 1
        improved = experiment_index["value"] == 2
        return ActionResult(
            status="succeeded",
            summary="experiment complete",
            evidence_refs=(f"experiment-{experiment_index['value']}.json",),
            facts={
                "run_id": f"candidate-{experiment_index['value']}",
                "parent_run_id": "baseline" if experiment_index["value"] == 1 else "candidate-1",
                "changed_factors": [arguments["changed_factor"]],
                "denominator_hash": "denom-1",
                "development_loss_delta": -0.02 if improved else 0.01,
                "market_baseline_delta": -0.01 if improved else 0.02,
                "development_improved": improved,
                "development_gates_passed": improved,
                "search_complete": experiment_index["value"] == 2,
                "used_frozen_forward": False,
                "code_sha": "a" * 40,
                "params_hash": "params-1",
                "feature_schema_hash": "features-1",
                "training_dates_hash": "dates-1",
            },
        )

    def qualify(arguments: dict, context: ToolContext) -> ActionResult:
        return ActionResult(
            status="succeeded",
            summary="qualification complete",
            evidence_refs=("qualification.json",),
            facts={
                "frozen_champion_hash": arguments["frozen_champion_hash"],
                "searcher_saw_forward_labels": False,
                "gates": {
                    "probability": True,
                    "market_baseline": True,
                    "forward": True,
                    "execution": True,
                },
            },
        )

    engine = research_engine(
        tmp_path,
        {
            "strategy.readiness": fixed(ActionResult(status="succeeded", summary="ready", facts={"ready": True})),
            "strategy.baseline": fixed(ActionResult(status="succeeded", summary="baseline", facts={"denominator_hash": "denom-1", "market_baseline_present": True})),
            "strategy.experiment": experiment,
            "strategy.qualify": qualify,
        },
    )
    engine.execute_action(action("strategy.readiness"))
    engine.execute_action(action("strategy.baseline"))
    first = engine.execute_action(action("strategy.experiment", hypothesis_id="h1", changed_factor="remaining_heat"))
    assert first.state == CompletionState.CONTINUE
    assert engine.store.load_state().phase == "DEVELOPMENT"

    engine.execute_action(action("strategy.experiment", hypothesis_id="h2", changed_factor="source_basis"))
    state = engine.store.load_state()
    assert state.phase == "QUALIFICATION"
    assert state.metadata["sealed_forward"] is True
    assert "strategy.experiment" not in engine.domain.allowed_tools(engine.store.load_task(), state)

    decision = engine.execute_action(
        action(
            "strategy.qualify",
            frozen_champion_hash=state.metadata["frozen_champion_hash"],
        )
    )
    assert decision.state == CompletionState.COMPLETE_QUALIFIED
    assert engine.store.load_state().status == RunStatus.COMPLETE


def test_strategy_rejects_forward_leakage_and_identity_mismatch(tmp_path: Path) -> None:
    engine = research_engine(tmp_path, {})
    state = engine.store.load_state()
    state.phase = "QUALIFICATION"
    state.completed_acceptance = (
        "readiness_verified",
        "baseline_established",
        "development_search_closed",
    )
    state.metadata["sealed_forward"] = True
    state.metadata["frozen_champion_hash"] = "expected"
    engine.store.save_state(state)

    result = ActionResult(
        status="succeeded",
        summary="bad qualification",
        facts={
            "frozen_champion_hash": "wrong",
            "searcher_saw_forward_labels": True,
            "gates": {
                "probability": True,
                "market_baseline": True,
                "forward": True,
                "execution": True,
            },
        },
    )
    engine.registry = registry_with_handlers({"strategy.qualify": fixed(result)})
    decision = engine.execute_action(action("strategy.qualify", frozen_champion_hash="wrong"))

    assert decision.state == CompletionState.CONTINUE
    state = engine.store.load_state()
    assert state.phase == "QUALIFICATION"
    assert "qualification_identity_mismatch" in state.blockers
    assert "qualification_closed" not in state.completed_acceptance


def test_strategy_patience_ends_as_falsified_not_intermediate(tmp_path: Path) -> None:
    engine = research_engine(
        tmp_path,
        {
            "strategy.readiness": fixed(ActionResult(status="succeeded", summary="ready", facts={"ready": True})),
            "strategy.baseline": fixed(ActionResult(status="succeeded", summary="baseline", facts={"denominator_hash": "denom", "market_baseline_present": True})),
            "strategy.experiment": fixed(
                ActionResult(
                    status="succeeded",
                    summary="no improvement",
                    facts={
                        "run_id": "candidate",
                        "parent_run_id": "baseline",
                        "changed_factors": ["one_factor"],
                        "denominator_hash": "denom",
                        "development_loss_delta": 0.01,
                        "market_baseline_delta": 0.02,
                        "development_improved": False,
                        "development_gates_passed": False,
                        "used_frozen_forward": False,
                    },
                )
            ),
        },
        budgets=BudgetSpec(max_experiments=5, patience=2),
    )
    engine.execute_action(action("strategy.readiness"))
    engine.execute_action(action("strategy.baseline"))
    first = engine.execute_action(action("strategy.experiment", hypothesis_id="h1", changed_factor="one_factor"))
    assert first.state == CompletionState.CONTINUE
    second = engine.execute_action(action("strategy.experiment", hypothesis_id="h2", changed_factor="one_factor"))

    assert second.state == CompletionState.COMPLETE_FALSIFIED
    state = engine.store.load_state()
    assert state.status == RunStatus.COMPLETE
    assert state.metadata["qualification_outcome"] == "falsified"


def test_collector_production_change_requires_explicit_authority(tmp_path: Path) -> None:
    engine = research_engine(
        tmp_path,
        {
            "strategy.readiness": fixed(
                ActionResult(
                    status="succeeded",
                    summary="blocked",
                    facts={"ready": False, "resume_condition": "15 new settled dates"},
                )
            ),
            "strategy.collect_evidence": fixed(
                ActionResult(status="succeeded", summary="started", facts={"collector_started": True})
            ),
        },
    )
    engine.execute_action(action("strategy.readiness"))
    decision = engine.execute_action(
        action("strategy.collect_evidence", resume_condition="15 new settled dates")
    )

    assert decision.state == CompletionState.REQUIRE_AUTHORITY
    state = engine.store.load_state()
    assert state.status == RunStatus.REQUIRE_AUTHORITY
    assert state.pending_authority["risk"] == RiskLevel.PRODUCTION_CHANGE.value

    resumed = engine.grant_and_resume(
        RiskLevel.PRODUCTION_CHANGE.value,
        reason="user approved this exact zero-notional collector change",
    )
    assert resumed.status == RunStatus.ACTIVE
    approved_action = action(
        "strategy.collect_evidence", resume_condition="15 new settled dates"
    )
    assert approved_action.action_hash in engine.store.load_task().authority.explicit_action_grants
    decision = engine.execute_action(approved_action)
    assert decision.state == CompletionState.WAIT_FOR_EVIDENCE


def test_authority_contract_rejects_automatic_irreversible_actions() -> None:
    with pytest.raises(ValueError, match="cannot be auto_execute"):
        AuthoritySpec(auto_execute=(RiskLevel.LIVE_FUNDS,))


def test_agent_side_prepare_approve_record_cycle_is_exact(tmp_path: Path) -> None:
    task = strategy_task_spec(
        run_id="agent-side-cycle",
        family="test",
        hypothesis="fixed hypothesis",
        scope={},
        authority=AuthoritySpec(
            auto_execute=(RiskLevel.READ_ONLY, RiskLevel.DERIVED_DATA_WRITE)
        ),
    )
    engine = HarnessEngine(
        repo_root=ROOT,
        store=EvidenceStore(tmp_path / "agent-side-cycle"),
        registry=build_core_registry(),
        domain=StrategyResearchDomain(),
    )
    engine.initialize(task)
    readiness = action("strategy.readiness")
    engine.prepare_external_action(readiness)
    engine.record_external_result(
        readiness,
        ActionResult(
            status="succeeded",
            summary="needs new evidence",
            evidence_refs=("readiness.json",),
            facts={"ready": False, "resume_condition": "15 new settled dates"},
        ),
    )
    collect = action(
        "strategy.collect_evidence", resume_condition="15 new settled dates"
    )
    pending = engine.prepare_external_action(collect)
    assert pending.state == CompletionState.REQUIRE_AUTHORITY
    engine.grant_and_resume(
        RiskLevel.PRODUCTION_CHANGE.value,
        reason="approve this exact zero-notional collector action",
    )
    prepared = engine.prepare_external_action(collect)
    assert prepared.state == CompletionState.CONTINUE
    decision = engine.record_external_result(
        collect,
        ActionResult(
            status="succeeded",
            summary="collector active",
            evidence_refs=("collector-health.json",),
            facts={
                "collector_started": True,
                "resume_condition": "15 new settled dates",
            },
        ),
    )
    assert decision.state == CompletionState.WAIT_FOR_EVIDENCE


def test_agent_side_result_requires_durable_evidence(tmp_path: Path) -> None:
    task = strategy_task_spec(
        run_id="external-evidence",
        family="test",
        hypothesis="fixed hypothesis",
        scope={},
    )
    engine = HarnessEngine(
        repo_root=ROOT,
        store=EvidenceStore(tmp_path / "external-evidence"),
        registry=registry_with_handlers({}),
        domain=StrategyResearchDomain(),
    )
    engine.initialize(task)
    tool = engine.registry.get("strategy.readiness")
    engine.registry = ToolRegistry()
    engine.registry.register(
        tool.contract.model_copy(update={"agent_side": True}),
        tool.handler,
    )
    readiness = action("strategy.readiness")
    engine.prepare_external_action(readiness)
    with pytest.raises(ValueError, match="durable evidence_refs"):
        engine.record_external_result(
            readiness,
            ActionResult(status="succeeded", summary="unsupported assertion", facts={"ready": True}),
        )


def test_certification_is_independent_of_strategy_outcome(tmp_path: Path) -> None:
    engine = research_engine(
        tmp_path,
        {
            "strategy.readiness": fixed(ActionResult(status="succeeded", summary="ready", facts={"ready": True})),
            "strategy.baseline": fixed(ActionResult(status="succeeded", summary="baseline", facts={"denominator_hash": "denom", "market_baseline_present": True})),
        },
        budgets=BudgetSpec(max_experiments=0),
    )
    engine.execute_action(action("strategy.readiness"))
    decision = engine.execute_action(action("strategy.baseline"))
    assert decision.state == CompletionState.COMPLETE_FALSIFIED

    certification = certify_run(
        store=engine.store,
        registry=engine.registry,
        domain=engine.domain,
    )

    assert certification.task_outcome == CompletionState.COMPLETE_FALSIFIED
    assert certification.harness_certified is True
    assert (engine.store.run_dir / "trace.json").is_file()
    assert (engine.store.run_dir / "run_manifest.json").is_file()


def test_certification_detects_ledger_tampering(tmp_path: Path) -> None:
    engine = research_engine(tmp_path, {})
    lines = engine.store.ledger_path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[0])
    payload["phase"] = "TAMPERED"
    lines[0] = json.dumps(payload)
    engine.store.ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    certification = certify_run(
        store=engine.store,
        registry=engine.registry,
        domain=engine.domain,
    )

    assert certification.harness_certified is False
    grade = next(item for item in certification.grades if item.grader == "ledger_hash_chain")
    assert grade.passed is False
