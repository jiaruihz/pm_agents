from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from src.weather_agent_harness.catalog import build_core_registry
from src.weather_agent_harness.certification import certify_run
from src.weather_agent_harness.contracts import (
    CompletionState,
    DomainSpec,
    RiskLevel,
    RunStatus,
    TaskSpec,
)
from src.weather_agent_harness.domains.strategy_research import (
    StrategyResearchDomain,
    strategy_task_spec,
)
from src.weather_agent_harness.evidence import EvidenceStore
from src.weather_agent_harness.orchestration import (
    CodexDispatchAdapter,
    DependencyResolver,
    OrchestrationStore,
    RequestProfile,
    RequestRouter,
    RoleSpec,
    RouteLevel,
    UsageRecord,
    WorkOrder,
    WorkResult,
    WorkStatus,
    build_run_receipt,
    price_usage,
    usage_from_codex_session,
)


def _usage() -> UsageRecord:
    return UsageRecord(
        source="codex_runtime",
        input_tokens=120,
        output_tokens=30,
        cached_tokens=40,
    )


def _orders() -> tuple[WorkOrder, WorkOrder]:
    return (
        WorkOrder(
            work_order_id="inspect",
            objective="inspect evidence",
            role="luna_verifier",
            acceptance=("evidence_mapped",),
        ),
        WorkOrder(
            work_order_id="implement",
            objective="implement the fix",
            role="terra_worker",
            depends_on=("inspect",),
            acceptance=("fix_implemented", "tests_passed"),
            risk=RiskLevel.REPOSITORY_WRITE,
            write_owners=("src/weather_agent_harness",),
        ),
    )


def _store(tmp_path: Path) -> OrchestrationStore:
    evidence = EvidenceStore(tmp_path / "multi")
    evidence.initialize(
        strategy_task_spec(
            run_id="multi",
            family="test",
            hypothesis="test orchestration",
            scope={},
        ),
        initial_phase="READINESS",
    )
    route = RequestRouter().classify(
        RequestProfile(
            objective="multi-agent implementation",
            explicit_multi_agent=True,
        )
    )
    store = OrchestrationStore(evidence)
    store.initialize(
        route,
        (
            RoleSpec(
                name="terra_worker",
                requested_model="gpt-5.6-terra",
                reasoning_effort="medium",
                sandbox="workspace-write",
            ),
            RoleSpec(
                name="luna_verifier",
                requested_model="gpt-5.6-luna",
                reasoning_effort="medium",
            ),
        ),
    )
    return store


def test_router_defaults_to_l0_and_escalates_by_need() -> None:
    router = RequestRouter()
    assert router.classify(RequestProfile(objective="explain one concept")).level == RouteLevel.DIRECT
    assert router.classify(
        RequestProfile(objective="iterate until closed", needs_iteration=True)
    ).level == RouteLevel.SINGLE_HARNESS
    assert router.classify(
        RequestProfile(objective="implement and independently review", needs_independent_review=True)
    ).level == RouteLevel.MULTI_AGENT
    assert router.classify(
        RequestProfile(objective="deploy", risk=RiskLevel.PRODUCTION_CHANGE)
    ).level == RouteLevel.CONTROLLED


def test_dependency_rejects_unknown_self_and_cycle() -> None:
    resolver = DependencyResolver()
    with pytest.raises(ValueError, match="missing dependencies"):
        resolver.validate(
            (WorkOrder(work_order_id="a", objective="a", role="r", acceptance=("x",), depends_on=("missing",)),)
        )
    with pytest.raises(ValueError, match="depend on itself"):
        resolver.validate(
            (WorkOrder(work_order_id="a", objective="a", role="r", acceptance=("x",), depends_on=("a",)),)
        )
    with pytest.raises(ValueError, match="cycle"):
        resolver.validate(
            (
                WorkOrder(work_order_id="a", objective="a", role="r", acceptance=("x",), depends_on=("b",)),
                WorkOrder(work_order_id="b", objective="b", role="r", acceptance=("x",), depends_on=("a",)),
            )
        )


def test_dependency_only_releases_after_verified_complete(tmp_path: Path) -> None:
    store = _store(tmp_path)
    state = store.add_work_orders(*_orders())
    assert [item.status for item in state.work_orders] == [WorkStatus.READY, WorkStatus.PENDING]
    _, running, _ = store.start("inspect", thread_id="thread-1", observed_model="gpt-5.6-luna")
    evidence = store.evidence_store.artifact_path("inspect.json")
    evidence.write_text("{}\n", encoding="utf-8")
    result = WorkResult(
        work_order_id="inspect",
        attempt=running.attempt,
        lease_id=running.lease_id or "",
        status="succeeded",
        summary="mapped",
        evidence_refs=(str(evidence),),
        acceptance_claims=("evidence_mapped",),
        observed_model="gpt-5.6-luna",
        usage=_usage(),
    )
    state, accepted = store.record_result(result)
    assert accepted is True
    assert [item.status for item in state.work_orders] == [WorkStatus.REVIEW, WorkStatus.PENDING]
    state = store.accept("inspect", verified_acceptance=("evidence_mapped",))
    assert [item.status for item in state.work_orders] == [WorkStatus.COMPLETE, WorkStatus.READY]


def test_conflicting_write_owners_are_serialized() -> None:
    resolver = DependencyResolver()
    refreshed = resolver.refresh(
        (
            WorkOrder(
                work_order_id="write-a",
                objective="write a",
                role="terra_worker",
                acceptance=("done",),
                write_owners=("src/shared.py",),
            ),
            WorkOrder(
                work_order_id="write-b",
                objective="write b",
                role="terra_worker",
                acceptance=("done",),
                write_owners=("src/shared.py",),
            ),
        )
    )
    assert [item.status for item in refreshed] == [WorkStatus.READY, WorkStatus.PENDING]


def test_stale_and_duplicate_worker_results_cannot_overwrite_state(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_work_orders(_orders()[0])
    _, running, _ = store.start("inspect", thread_id="thread-1", observed_model="gpt-5.6-luna")
    stale = WorkResult(
        work_order_id="inspect",
        attempt=running.attempt,
        lease_id="wrong-lease",
        status="succeeded",
        summary="late",
        evidence_refs=("missing.json",),
    )
    state, accepted = store.record_result(stale)
    assert accepted is False
    assert state.work_orders[0].status == WorkStatus.RUNNING

    evidence = store.evidence_store.artifact_path("inspect.json")
    evidence.write_text("{}\n", encoding="utf-8")
    current = stale.model_copy(
        update={"lease_id": running.lease_id, "evidence_refs": (str(evidence),)}
    )
    _, accepted = store.record_result(current)
    assert accepted is True
    _, accepted = store.record_result(current)
    assert accepted is False


def test_receipt_requires_terminal_certification_and_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_work_orders(_orders()[0])
    _, running, _ = store.start("inspect", thread_id="thread-1", observed_model="gpt-5.6-luna")
    evidence = store.evidence_store.artifact_path("inspect.json")
    evidence.write_text("{}\n", encoding="utf-8")
    store.record_result(
        WorkResult(
            work_order_id="inspect",
            attempt=running.attempt,
            lease_id=running.lease_id or "",
            status="succeeded",
            summary="done",
            evidence_refs=(str(evidence),),
            observed_model="gpt-5.6-luna",
            duration_seconds=12.5,
            tool_calls=3,
            usage=_usage(),
        )
    )
    store.accept("inspect", verified_acceptance=("evidence_mapped",))
    with pytest.raises(RuntimeError, match="terminal"):
        build_run_receipt(store)

    state = store.evidence_store.load_state()
    state.completed_acceptance = store.evidence_store.load_task().acceptance
    state.metadata["qualification_outcome"] = "falsified"
    state.phase = "DONE"
    state.status = RunStatus.COMPLETE
    state.completion_state = CompletionState.COMPLETE_FALSIFIED
    store.evidence_store.save_state(state)
    certification = certify_run(
        store=store.evidence_store,
        registry=build_core_registry(),
        domain=StrategyResearchDomain(),
    )
    assert certification.harness_certified is True
    certification_path = store.evidence_store.run_dir / "certification.json"
    payload = json.loads(certification_path.read_text(encoding="utf-8"))
    payload["task_hash"] = "tampered"
    certification_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="identity mismatch"):
        build_run_receipt(store)
    certify_run(
        store=store.evidence_store,
        registry=build_core_registry(),
        domain=StrategyResearchDomain(),
    )
    first = build_run_receipt(store)
    second = build_run_receipt(store)
    assert first.receipt_hash == second.receipt_hash
    assert first.usage.source == "aggregated_worker_results"
    assert first.usage.input_tokens == 120
    assert first.usage.output_tokens == 30
    assert first.usage.cached_tokens == 40
    assert first.usage.estimated_cost_usd is None
    assert first.usage.estimated_cost_credits == pytest.approx(0.00132)
    assert first.usage.baseline_cost_credits == pytest.approx(0.033)
    assert first.usage.savings_ratio == pytest.approx(0.96)
    assert first.agents[0].requested_model == "gpt-5.6-luna"
    assert first.agents[0].observed_model == "gpt-5.6-luna"


def test_heartbeat_extends_lease_and_expiry_retries_then_runtime_exit_fails(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    order = _orders()[0].model_copy(
        update={
            "lease_timeout_seconds": 30,
            "heartbeat_interval_seconds": 5,
        }
    )
    store.add_work_orders(order)
    _, first, _ = store.start(
        "inspect", thread_id="thread-1", observed_model="gpt-5.6-luna", now_utc="2026-08-13T00:00:00Z"
    )
    state = store.heartbeat(
        "inspect",
        attempt=first.attempt,
        lease_id=first.lease_id or "",
        now_utc="2026-08-13T00:00:20Z",
    )
    assert state.work_orders[0].lease_expires_at_utc == "2026-08-13T00:00:50Z"
    state, expired = store.reap_expired(now_utc="2026-08-13T00:00:49Z")
    assert expired == ()
    assert state.work_orders[0].status == WorkStatus.RUNNING

    state, expired = store.reap_expired(now_utc="2026-08-13T00:00:50Z")
    assert expired == ("inspect",)
    assert state.work_orders[0].status == WorkStatus.READY
    assert state.results[-1].unresolved == ("lease_expired",)
    assert state.agent_runs[0].terminal_reason == "lease_expired"

    _, second, _ = store.start(
        "inspect", thread_id="thread-2", observed_model="gpt-5.6-luna", now_utc="2026-08-13T00:00:51Z"
    )
    state, accepted = store.record_runtime_exit(
        "inspect",
        attempt=second.attempt,
        lease_id=second.lease_id or "",
        runtime_status="interrupted",
        now_utc="2026-08-13T00:00:52Z",
    )
    assert accepted is True
    assert state.work_orders[0].status == WorkStatus.FAILED
    assert state.agent_runs[-1].terminal_reason == "interrupted"


def test_chainlove_portable_task_supports_status_context_and_certify(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "chainlove"
    evidence_store = EvidenceStore(run_dir)
    task = TaskSpec(
        run_id="chainlove-bounty-batch",
        task_type="chainlove.bounty_batch",
        objective="process and verify one bounty batch",
        acceptance=("batch_processed", "batch_verified"),
        domain=DomainSpec(initial_phase="ORCHESTRATE", terminal_phase="DONE"),
    )
    evidence_store.initialize(task, initial_phase=task.domain.initial_phase)
    orchestration = OrchestrationStore(evidence_store)
    route = RequestRouter().classify(
        RequestProfile(objective=task.objective, explicit_multi_agent=True)
    )
    orchestration.initialize(
        route,
        (
            RoleSpec(
                name="terra_worker",
                requested_model="gpt-5.6-terra",
                sandbox="workspace-write",
            ),
        ),
    )
    orchestration.add_work_orders(
        WorkOrder(
            work_order_id="bounty-batch",
            objective="process the frozen bounty batch",
            role="terra_worker",
            acceptance=("output_present", "verification_present"),
            closes_acceptance=("batch_processed", "batch_verified"),
        )
    )
    _, running, _ = orchestration.start(
        "bounty-batch", thread_id="thread-chain", observed_model="gpt-5.6-terra"
    )
    artifact = evidence_store.artifact_path("chainlove/bounty-batch.json")
    artifact.write_text('{"verified": true}\n', encoding="utf-8")
    orchestration.record_result(
        WorkResult(
            work_order_id="bounty-batch",
            attempt=running.attempt,
            lease_id=running.lease_id or "",
            status="succeeded",
            summary="batch processed and independently verified",
            evidence_refs=(str(artifact),),
            observed_model="gpt-5.6-terra",
            usage=_usage(),
        )
    )
    orchestration.accept(
        "bounty-batch",
        verified_acceptance=("output_present", "verification_present"),
    )
    assert evidence_store.load_state().status == RunStatus.COMPLETE

    script = Path("scripts/ops/agent_harness.py").resolve()
    for command in ("status", "context", "certify"):
        result = subprocess.run(
            [sys.executable, str(script), command, "--run-dir", str(run_dir)],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        if command == "status":
            assert payload["task"]["task_type"] == "chainlove.bounty_batch"
            assert payload["decision"]["state"] == "complete"
        elif command == "certify":
            assert payload["harness_certified"] is True


def test_exact_model_and_dedicated_thread_are_enforced(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_work_orders(*_orders())
    _, _, instruction = CodexDispatchAdapter().prepare(store, "inspect")
    assert instruction["fork_turns"] == "none"
    assert instruction["model"] == "gpt-5.6-luna"
    with pytest.raises(ValueError, match="dispatch model mismatch"):
        store.start("inspect", thread_id="bad", observed_model="gpt-5.6-sol")
    _, running, _ = store.start(
        "inspect", thread_id="dedicated", observed_model="gpt-5.6-luna"
    )
    store.record_runtime_exit(
        "inspect",
        attempt=running.attempt,
        lease_id=running.lease_id or "",
        runtime_status="interrupted",
    )
    with pytest.raises(ValueError, match="thread_id already belongs"):
        store.start("inspect", thread_id="dedicated", observed_model="gpt-5.6-luna")


def test_accept_rejects_success_without_measured_usage(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_work_orders(_orders()[0])
    _, running, _ = store.start(
        "inspect", thread_id="thread-1", observed_model="gpt-5.6-luna"
    )
    evidence = store.evidence_store.artifact_path("inspect.json")
    evidence.write_text("{}\n", encoding="utf-8")
    store.record_result(
        WorkResult(
            work_order_id="inspect",
            attempt=running.attempt,
            lease_id=running.lease_id or "",
            status="succeeded",
            summary="done",
            evidence_refs=(str(evidence),),
            observed_model="gpt-5.6-luna",
        )
    )
    with pytest.raises(ValueError, match="measured token usage"):
        store.accept("inspect", verified_acceptance=("evidence_mapped",))


def test_codex_session_usage_is_extracted_and_priced(tmp_path: Path) -> None:
    session = tmp_path / "rollout.jsonl"
    rows = (
        {"timestamp": "2026-08-13T00:00:00Z", "type": "turn_context", "payload": {"model": "gpt-5.6-terra"}},
        {"timestamp": "2026-08-13T00:00:01Z", "type": "response_item", "payload": {"type": "function_call"}},
        {"timestamp": "2026-08-13T00:00:02Z", "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 100}}}},
    )
    session.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    usage, model, duration, calls = usage_from_codex_session(session)
    priced = price_usage(usage, model=model)
    assert model == "gpt-5.6-terra"
    assert duration == 2.0
    assert calls == 1
    assert priced.estimated_cost_credits == pytest.approx(0.044)
    assert priced.baseline_cost_credits == pytest.approx(0.11)
    assert priced.savings_ratio == pytest.approx(0.6)
