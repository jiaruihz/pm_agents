#!/usr/bin/env python3
"""Initialize, inspect and advance Agent Harness tasks and weather scenarios."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.production import load_production_spec
from src.weather_agent_harness.catalog import build_core_registry
from src.weather_agent_harness.certification import certify_run
from src.weather_agent_harness.contracts import (
    ActionRequest,
    ActionResult,
    AuthoritySpec,
    BudgetSpec,
    ContextRef,
    RiskLevel,
    TaskSpec,
)
from src.weather_agent_harness.domains import build_domain_registry
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
from src.weather_agent_harness.orchestration import (
    CodexDispatchAdapter,
    OrchestrationStore,
    RequestProfile,
    RequestRouter,
    RoleSpec,
    WorkOrder,
    WorkResult,
    build_run_receipt,
    usage_from_codex_session,
)
from src.weather_agent_harness.scenarios.market_prior_training import (
    run_market_prior_training_scenario,
)
from src.weather_agent_harness.scenarios.busan_market_prior_case import (
    DEFAULT_INPUT as DEFAULT_BUSAN_INPUT,
    run_busan_market_prior_case,
)


def _default_root() -> Path:
    return load_production_spec().research_artifact_root / "agent_harness"


def _run_id(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{stamp}"


def _json_object(value: str) -> dict[str, Any]:
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise argparse.ArgumentTypeError("value must be a JSON object")
    return payload


def _domain(task: TaskSpec):
    return build_domain_registry().resolve(task)


def _engine(run_dir: Path) -> HarnessEngine:
    store = EvidenceStore(run_dir)
    task = store.load_task()
    return HarnessEngine(
        repo_root=ROOT,
        store=store,
        registry=build_core_registry(),
        domain=_domain(task),
    )


def init_task(args: argparse.Namespace) -> int:
    """Initialize any TaskSpec; unknown task types use its portable domain contract."""

    task = TaskSpec.model_validate_json(args.task_json.read_text(encoding="utf-8"))
    run_dir = args.run_dir or (args.artifact_root / task.run_id)
    store = EvidenceStore(run_dir)
    domain = _domain(task)
    HarnessEngine(
        repo_root=ROOT,
        store=store,
        registry=build_core_registry(),
        domain=domain,
    ).initialize(task)
    print(
        json.dumps(
            {
                "run_id": task.run_id,
                "task_type": task.task_type,
                "run_dir": str(run_dir),
                "domain": type(domain).__name__,
                "status": "active",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _production_preflight(run_dir: Path) -> dict[str, Any]:
    """Capture a read-only production check without widening scenario authority."""

    output_path = run_dir / "production_preflight.json"
    command = [
        str(ROOT / ".venv/bin/python"),
        str(ROOT / "scripts/ops/weather_production_ctl.py"),
        "health",
        "--json",
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {
            "status": "critical",
            "parse_error": True,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-4000:],
        }
    payload["command_exit_code"] = result.returncode
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    jrs = payload.get("jrs_context_health") or {}
    semantic = payload.get("data_feed_semantic_health") or {}
    manifest_findings = payload.get("critical_manifest_findings") or []
    notes = [item for item in (jrs.get("output"),) if item]
    notes.extend(str(item) for item in semantic.get("critical_reasons") or [])
    notes.extend(str(item.get("message")) for item in manifest_findings if item.get("message"))
    return {
        "status": payload.get("status", "unknown"),
        "note": "；".join(notes) or "read-only production preflight completed",
        "evidence_ref": str(output_path),
        "command_exit_code": result.returncode,
    }


def run_market_prior(args: argparse.Namespace) -> int:
    run_id = args.run_id or _run_id("market-prior-golden")
    run_dir = args.artifact_root / run_id
    report_path = args.report_out or (run_dir / "report.html")
    preflight = _production_preflight(run_dir)
    outcome = run_market_prior_training_scenario(
        repo_root=ROOT,
        run_dir=run_dir,
        report_path=report_path,
        production_preflight=preflight,
    )
    print(json.dumps({**outcome.__dict__, "production_preflight": preflight}, ensure_ascii=False, indent=2))
    return 0


def run_busan_case(args: argparse.Namespace) -> int:
    run_id = args.run_id or _run_id("busan-market-prior-real")
    run_dir = args.artifact_root / run_id
    report_path = args.report_out or (run_dir / "report.html")
    preflight = _production_preflight(run_dir)
    outcome = run_busan_market_prior_case(
        repo_root=ROOT,
        run_dir=run_dir,
        report_path=report_path,
        input_path=args.input,
        production_preflight=preflight,
    )
    print(json.dumps({**outcome.__dict__, "production_preflight": preflight}, ensure_ascii=False, indent=2))
    return 0


def init_production(args: argparse.Namespace) -> int:
    run_id = args.run_id or _run_id("production-e2e")
    run_dir = args.artifact_root / run_id
    task = production_task_spec(
        run_id=run_id,
        scope={
            **args.scope,
            "last_target_dates": args.last_target_dates,
            "canonical_db": str(load_production_spec().canonical_db_path),
        },
        authority=AuthoritySpec(
            auto_execute=(
                RiskLevel.READ_ONLY,
                RiskLevel.DERIVED_DATA_WRITE,
                RiskLevel.REPOSITORY_WRITE,
            )
        ),
        budgets=BudgetSpec(max_actions=args.max_actions),
        context_refs=(
            ContextRef(path="skills/weather-fact-rebuild/SKILL.md", phases=("SYNC", "REPAIR", "REPLAY")),
            ContextRef(path="skills/weather-live-account-reconcile/SKILL.md", phases=("RECONCILE",)),
            ContextRef(path="skills/weather-strategy-exposure/SKILL.md", phases=("EXPOSURE",)),
            ContextRef(path="skills/weather-strategy-lineage/SKILL.md", phases=("LINEAGE", "DIAGNOSE", "REPLAY")),
        ),
    )
    store = EvidenceStore(run_dir)
    HarnessEngine(
        repo_root=ROOT,
        store=store,
        registry=build_core_registry(),
        domain=ProductionAuditDomain(),
    ).initialize(task)
    print(json.dumps({"run_id": run_id, "run_dir": str(run_dir), "status": "active"}, ensure_ascii=False, indent=2))
    return 0


def init_research(args: argparse.Namespace) -> int:
    run_id = args.run_id or _run_id("strategy-research")
    run_dir = args.artifact_root / run_id
    task = strategy_task_spec(
        run_id=run_id,
        family=args.family,
        hypothesis=args.hypothesis,
        scope=args.scope,
        authority=AuthoritySpec(
            auto_execute=(RiskLevel.READ_ONLY, RiskLevel.DERIVED_DATA_WRITE)
        ),
        budgets=BudgetSpec(
            max_actions=args.max_actions,
            max_experiments=args.max_experiments,
            patience=args.patience,
        ),
        context_refs=(
            ContextRef(path="skills/weather-strategy-research/SKILL.md", max_chars=30_000),
            ContextRef(path="docs/WEATHER_STRATEGY_QUANT_DESIGN.md", phases=("READINESS", "BASELINE"), max_chars=10_000),
            ContextRef(path="docs/WEATHER_STRATEGY_REGISTRY.md", phases=("READINESS",), max_chars=10_000),
        ),
    )
    store = EvidenceStore(run_dir)
    HarnessEngine(
        repo_root=ROOT,
        store=store,
        registry=build_core_registry(),
        domain=StrategyResearchDomain(),
    ).initialize(task)
    print(json.dumps({"run_id": run_id, "run_dir": str(run_dir), "status": "active"}, ensure_ascii=False, indent=2))
    return 0


def show_status(args: argparse.Namespace) -> int:
    engine = _engine(args.run_dir)
    task = engine.store.load_task()
    state = engine.store.load_state()
    decision = engine.domain.verify(task, state)
    allowed = engine.domain.allowed_tools(task, state)
    contracts = engine.registry.contracts_for(task.task_type, allowed)
    payload = {
        "task": {"run_id": task.run_id, "task_type": task.task_type, "objective": task.objective},
        "state": state.model_dump(mode="json"),
        "decision": decision.model_dump(mode="json"),
        "available_tools": [item.model_dump(mode="json") for item in contracts],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def show_context(args: argparse.Namespace) -> int:
    print(json.dumps(_engine(args.run_dir).context(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def record(args: argparse.Namespace) -> int:
    action = ActionRequest.model_validate_json(args.action_json.read_text(encoding="utf-8"))
    result = ActionResult.model_validate_json(args.result_json.read_text(encoding="utf-8"))
    decision = _engine(args.run_dir).record_external_result(action, result)
    print(json.dumps(decision.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def prepare(args: argparse.Namespace) -> int:
    action = ActionRequest.model_validate_json(args.action_json.read_text(encoding="utf-8"))
    decision = _engine(args.run_dir).prepare_external_action(action)
    print(json.dumps(decision.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def approve(args: argparse.Namespace) -> int:
    state = _engine(args.run_dir).grant_and_resume(args.risk, reason=args.reason)
    print(json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def recover(args: argparse.Namespace) -> int:
    state = _engine(args.run_dir).recover_interrupted_action()
    print(json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def certify(args: argparse.Namespace) -> int:
    engine = _engine(args.run_dir)
    result = certify_run(
        store=engine.store,
        registry=engine.registry,
        domain=engine.domain,
    )
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.harness_certified else 2


def route_request(args: argparse.Namespace) -> int:
    profile = RequestProfile.model_validate_json(
        args.profile_json.read_text(encoding="utf-8")
    )
    decision = RequestRouter().classify(profile)
    print(json.dumps(decision.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def init_orchestration(args: argparse.Namespace) -> int:
    profile = RequestProfile.model_validate_json(
        args.profile_json.read_text(encoding="utf-8")
    )
    role_payload = json.loads(args.roles_json.read_text(encoding="utf-8"))
    if not isinstance(role_payload, list):
        raise ValueError("roles JSON must be a list")
    roles = tuple(RoleSpec.model_validate(item) for item in role_payload)
    store = OrchestrationStore(EvidenceStore(args.run_dir))
    state = store.initialize(RequestRouter().classify(profile), roles)
    print(json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def add_work_orders(args: argparse.Namespace) -> int:
    orders = tuple(
        WorkOrder.model_validate_json(path.read_text(encoding="utf-8"))
        for path in args.work_order_json
    )
    state = OrchestrationStore(EvidenceStore(args.run_dir)).add_work_orders(*orders)
    print(json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def prepare_dispatch(args: argparse.Namespace) -> int:
    store = OrchestrationStore(EvidenceStore(args.run_dir))
    _, _, instruction = CodexDispatchAdapter().prepare(store, args.work_order_id)
    print(json.dumps(instruction, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def record_dispatch(args: argparse.Namespace) -> int:
    store = OrchestrationStore(EvidenceStore(args.run_dir))
    order = CodexDispatchAdapter().record_spawn(
        store,
        args.work_order_id,
        thread_id=args.thread_id,
        observed_model=args.observed_model,
    )
    print(json.dumps(order.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def record_work_result(args: argparse.Namespace) -> int:
    result = WorkResult.model_validate_json(args.result_json.read_text(encoding="utf-8"))
    if args.codex_session_jsonl:
        usage, observed_model, duration_seconds, tool_calls = usage_from_codex_session(
            args.codex_session_jsonl
        )
        result = result.model_copy(
            update={
                "observed_model": observed_model,
                "usage": usage,
                "duration_seconds": duration_seconds,
                "tool_calls": tool_calls,
            }
        )
    state, accepted = OrchestrationStore(EvidenceStore(args.run_dir)).record_result(result)
    print(json.dumps({"accepted": accepted, "state": state.model_dump(mode="json")}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if accepted else 2


def accept_work_order(args: argparse.Namespace) -> int:
    state = OrchestrationStore(EvidenceStore(args.run_dir)).accept(
        args.work_order_id,
        verified_acceptance=tuple(args.verified_acceptance),
    )
    print(json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def orchestration_status(args: argparse.Namespace) -> int:
    store = OrchestrationStore(EvidenceStore(args.run_dir))
    state, expired = store.reap_expired()
    payload = {
        "state": state.model_dump(mode="json"),
        "ready": [item.model_dump(mode="json") for item in store.ready()],
        "reaped_expired": list(expired),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def heartbeat_work_order(args: argparse.Namespace) -> int:
    state = OrchestrationStore(EvidenceStore(args.run_dir)).heartbeat(
        args.work_order_id,
        attempt=args.attempt,
        lease_id=args.lease_id,
    )
    print(json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def reap_work_orders(args: argparse.Namespace) -> int:
    state, expired = OrchestrationStore(EvidenceStore(args.run_dir)).reap_expired()
    print(json.dumps({"reaped_expired": list(expired), "state": state.model_dump(mode="json")}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def record_agent_terminal(args: argparse.Namespace) -> int:
    state, accepted = OrchestrationStore(EvidenceStore(args.run_dir)).record_runtime_exit(
        args.work_order_id,
        attempt=args.attempt,
        lease_id=args.lease_id,
        runtime_status=args.runtime_status,
        summary=args.summary,
    )
    print(json.dumps({"accepted": accepted, "state": state.model_dump(mode="json")}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if accepted else 2


def write_receipt(args: argparse.Namespace) -> int:
    receipt = build_run_receipt(OrchestrationStore(EvidenceStore(args.run_dir)))
    print(json.dumps(receipt.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    sub = value.add_subparsers(dest="command", required=True)

    generic = sub.add_parser("init-task", help="initialize a portable TaskSpec")
    generic.add_argument("--task-json", type=Path, required=True)
    generic.add_argument("--run-dir", type=Path)
    generic.add_argument("--artifact-root", type=Path, default=_default_root())
    generic.set_defaults(func=init_task)

    prod = sub.add_parser("init-production-audit")
    prod.add_argument("--run-id")
    prod.add_argument("--artifact-root", type=Path, default=_default_root())
    prod.add_argument("--last-target-dates", type=int, default=30)
    prod.add_argument("--max-actions", type=int, default=50)
    prod.add_argument("--scope", type=_json_object, default={})
    prod.set_defaults(func=init_production)

    research = sub.add_parser("init-strategy-research")
    research.add_argument("--run-id")
    research.add_argument("--artifact-root", type=Path, default=_default_root())
    research.add_argument("--family", required=True)
    research.add_argument("--hypothesis", required=True)
    research.add_argument("--scope", type=_json_object, default={})
    research.add_argument("--max-actions", type=int, default=50)
    research.add_argument("--max-experiments", type=int, default=8)
    research.add_argument("--patience", type=int, default=3)
    research.set_defaults(func=init_research)

    scenario = sub.add_parser(
        "run-market-prior-training",
        help="run the complete deterministic development-to-sealed-forward scenario",
    )
    scenario.add_argument("--run-id")
    scenario.add_argument(
        "--artifact-root",
        type=Path,
        default=ROOT / "runtime/weather_agent_harness_runs",
    )
    scenario.add_argument("--report-out", type=Path)
    scenario.set_defaults(func=run_market_prior)

    busan = sub.add_parser(
        "run-busan-market-prior-case",
        help="run the complete Harness on the real immutable Busan PIT artifact",
    )
    busan.add_argument("--run-id")
    busan.add_argument("--input", type=Path, default=DEFAULT_BUSAN_INPUT)
    busan.add_argument(
        "--artifact-root",
        type=Path,
        default=ROOT / "runtime/weather_agent_harness_runs",
    )
    busan.add_argument("--report-out", type=Path)
    busan.set_defaults(func=run_busan_case)

    status = sub.add_parser("status")
    status.add_argument("--run-dir", type=Path, required=True)
    status.set_defaults(func=show_status)

    context = sub.add_parser("context")
    context.add_argument("--run-dir", type=Path, required=True)
    context.set_defaults(func=show_context)

    prepare_cmd = sub.add_parser("prepare")
    prepare_cmd.add_argument("--run-dir", type=Path, required=True)
    prepare_cmd.add_argument("--action-json", type=Path, required=True)
    prepare_cmd.set_defaults(func=prepare)

    record_cmd = sub.add_parser("record")
    record_cmd.add_argument("--run-dir", type=Path, required=True)
    record_cmd.add_argument("--action-json", type=Path, required=True)
    record_cmd.add_argument("--result-json", type=Path, required=True)
    record_cmd.set_defaults(func=record)

    approve_cmd = sub.add_parser("approve")
    approve_cmd.add_argument("--run-dir", type=Path, required=True)
    approve_cmd.add_argument(
        "--risk",
        required=True,
        choices=[item.value for item in RiskLevel],
    )
    approve_cmd.add_argument("--reason", required=True)
    approve_cmd.set_defaults(func=approve)

    recover_cmd = sub.add_parser("recover-interrupted")
    recover_cmd.add_argument("--run-dir", type=Path, required=True)
    recover_cmd.set_defaults(func=recover)

    certify_cmd = sub.add_parser("certify")
    certify_cmd.add_argument("--run-dir", type=Path, required=True)
    certify_cmd.set_defaults(func=certify)

    route_cmd = sub.add_parser("route")
    route_cmd.add_argument("--profile-json", type=Path, required=True)
    route_cmd.set_defaults(func=route_request)

    orchestration_init = sub.add_parser("init-orchestration")
    orchestration_init.add_argument("--run-dir", type=Path, required=True)
    orchestration_init.add_argument("--profile-json", type=Path, required=True)
    orchestration_init.add_argument("--roles-json", type=Path, required=True)
    orchestration_init.set_defaults(func=init_orchestration)

    work_add = sub.add_parser("add-work-orders")
    work_add.add_argument("--run-dir", type=Path, required=True)
    work_add.add_argument("--work-order-json", type=Path, nargs="+", required=True)
    work_add.set_defaults(func=add_work_orders)

    dispatch_prepare = sub.add_parser("prepare-dispatch")
    dispatch_prepare.add_argument("--run-dir", type=Path, required=True)
    dispatch_prepare.add_argument("--work-order-id", required=True)
    dispatch_prepare.set_defaults(func=prepare_dispatch)

    dispatch_record = sub.add_parser("record-dispatch")
    dispatch_record.add_argument("--run-dir", type=Path, required=True)
    dispatch_record.add_argument("--work-order-id", required=True)
    dispatch_record.add_argument("--thread-id", required=True)
    dispatch_record.add_argument("--observed-model", required=True)
    dispatch_record.set_defaults(func=record_dispatch)

    work_result = sub.add_parser("record-work-result")
    work_result.add_argument("--run-dir", type=Path, required=True)
    work_result.add_argument("--result-json", type=Path, required=True)
    work_result.add_argument("--codex-session-jsonl", type=Path)
    work_result.set_defaults(func=record_work_result)

    work_accept = sub.add_parser("accept-work-order")
    work_accept.add_argument("--run-dir", type=Path, required=True)
    work_accept.add_argument("--work-order-id", required=True)
    work_accept.add_argument("--verified-acceptance", nargs="+", required=True)
    work_accept.set_defaults(func=accept_work_order)

    orchestration_show = sub.add_parser("orchestration-status")
    orchestration_show.add_argument("--run-dir", type=Path, required=True)
    orchestration_show.set_defaults(func=orchestration_status)

    heartbeat_cmd = sub.add_parser("heartbeat-work-order")
    heartbeat_cmd.add_argument("--run-dir", type=Path, required=True)
    heartbeat_cmd.add_argument("--work-order-id", required=True)
    heartbeat_cmd.add_argument("--attempt", type=int, required=True)
    heartbeat_cmd.add_argument("--lease-id", required=True)
    heartbeat_cmd.set_defaults(func=heartbeat_work_order)

    reap_cmd = sub.add_parser("reap-work-orders")
    reap_cmd.add_argument("--run-dir", type=Path, required=True)
    reap_cmd.set_defaults(func=reap_work_orders)

    terminal_cmd = sub.add_parser("record-agent-terminal")
    terminal_cmd.add_argument("--run-dir", type=Path, required=True)
    terminal_cmd.add_argument("--work-order-id", required=True)
    terminal_cmd.add_argument("--attempt", type=int, required=True)
    terminal_cmd.add_argument("--lease-id", required=True)
    terminal_cmd.add_argument(
        "--runtime-status",
        choices=("interrupted", "cancelled", "crashed", "lost"),
        required=True,
    )
    terminal_cmd.add_argument("--summary")
    terminal_cmd.set_defaults(func=record_agent_terminal)

    receipt_cmd = sub.add_parser("write-receipt")
    receipt_cmd.add_argument("--run-dir", type=Path, required=True)
    receipt_cmd.set_defaults(func=write_receipt)
    return value


def main() -> int:
    args = parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
