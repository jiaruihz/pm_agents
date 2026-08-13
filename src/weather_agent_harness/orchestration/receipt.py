"""Terminal, auditable receipt generation for an orchestration run."""

from __future__ import annotations

import json
from pathlib import Path

from ..contracts import RunStatus, stable_hash
from .contracts import RunReceipt, UsageRecord, WorkStatus
from .store import OrchestrationStore


def aggregate_usage(results: tuple) -> UsageRecord:
    observed = [item.usage for item in results]
    if not observed or any(item is None or item.source == "unavailable" for item in observed):
        return UsageRecord()
    if any(
        value is None
        for item in observed
        if item is not None
        for value in (item.input_tokens, item.output_tokens, item.cached_tokens)
    ):
        return UsageRecord()
    concrete = [item for item in observed if item is not None]
    costs = [item.estimated_cost_usd for item in concrete]
    credit_costs = [item.estimated_cost_credits for item in concrete]
    baseline_costs = [item.baseline_cost_credits for item in concrete]
    actual_credits = (
        sum(value for value in credit_costs if value is not None)
        if all(value is not None for value in credit_costs)
        else None
    )
    baseline_credits = (
        sum(value for value in baseline_costs if value is not None)
        if all(value is not None for value in baseline_costs)
        else None
    )
    return UsageRecord(
        source="aggregated_worker_results",
        input_tokens=sum(item.input_tokens or 0 for item in concrete),
        output_tokens=sum(item.output_tokens or 0 for item in concrete),
        cached_tokens=sum(item.cached_tokens or 0 for item in concrete),
        estimated_cost_usd=(
            sum(value for value in costs if value is not None)
            if all(value is not None for value in costs)
            else None
        ),
        billing_model=(
            concrete[0].billing_model
            if len({item.billing_model for item in concrete}) == 1
            else "mixed"
        ),
        rate_card_id=(
            concrete[0].rate_card_id
            if len({item.rate_card_id for item in concrete}) == 1
            else "mixed"
        ),
        estimated_cost_credits=actual_credits,
        baseline_model=(
            concrete[0].baseline_model
            if len({item.baseline_model for item in concrete}) == 1
            else "mixed"
        ),
        baseline_cost_credits=baseline_credits,
        savings_credits=(
            baseline_credits - actual_credits
            if baseline_credits is not None and actual_credits is not None
            else None
        ),
        savings_ratio=(
            (baseline_credits - actual_credits) / baseline_credits
            if baseline_credits and actual_credits is not None
            else None
        ),
    )


def build_run_receipt(
    store: OrchestrationStore,
    *,
    usage: UsageRecord | None = None,
) -> RunReceipt:
    orchestration, _ = store.reap_expired()
    evidence_store = store.evidence_store
    task = evidence_store.load_task()
    run_state = evidence_store.load_state()
    if not orchestration.work_orders or any(
        item.status != WorkStatus.COMPLETE for item in orchestration.work_orders
    ):
        raise RuntimeError("receipt requires all work orders to be complete")
    if run_state.status != RunStatus.COMPLETE:
        raise RuntimeError("receipt requires a terminal Harness run")
    certification_path = evidence_store.run_dir / "certification.json"
    if not certification_path.is_file():
        raise RuntimeError("receipt requires certification.json")
    certification = json.loads(certification_path.read_text(encoding="utf-8"))
    if certification.get("harness_certified") is not True:
        raise RuntimeError("receipt requires a passing Harness certification")
    entries = evidence_store.entries()
    if not entries or not entries[-1].entry_hash:
        raise RuntimeError("receipt requires a sealed evidence ledger head")
    trace_path = evidence_store.run_dir / "trace.json"
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    expected_identity = {
        "run_id": task.run_id,
        "task_hash": task.task_hash,
    }
    for key, expected in expected_identity.items():
        if certification.get(key) != expected or trace.get(key) != expected:
            raise RuntimeError(f"certification identity mismatch: {key}")
    if certification.get("task_outcome") != run_state.completion_state.value:
        raise RuntimeError("certification outcome does not match current run state")
    if Path(str(certification.get("trace_ref") or "")).resolve() != trace_path.resolve():
        raise RuntimeError("certification trace_ref does not identify the current trace")
    if trace.get("event_count") != len(entries):
        raise RuntimeError("certification is stale relative to the evidence ledger")
    output = evidence_store.run_dir / "run_receipt.json"
    if output.is_file():
        existing = RunReceipt.model_validate_json(output.read_text(encoding="utf-8"))
        if (
            existing.task_hash == task.task_hash
            and existing.ledger_head_hash == entries[-1].entry_hash
            and existing.ledger_sequence == entries[-1].sequence
        ):
            return existing
    counts = {
        status.value: sum(1 for item in orchestration.work_orders if item.status == status)
        for status in WorkStatus
    }
    evidence_refs = tuple(
        dict.fromkeys(
            reference
            for result in orchestration.results
            for reference in result.evidence_refs
        )
    )
    receipt = RunReceipt(
        run_id=task.run_id,
        route_level=orchestration.route.level,
        outcome=run_state.completion_state.value,
        harness_certified=True,
        task_hash=task.task_hash,
        ledger_head_hash=entries[-1].entry_hash,
        ledger_sequence=entries[-1].sequence,
        started_at_utc=orchestration.created_at_utc,
        work_orders=counts,
        agents=orchestration.agent_runs,
        usage=usage or aggregate_usage(orchestration.results),
        evidence_refs=evidence_refs,
        certification_ref=str(certification_path),
    )
    receipt.receipt_hash = stable_hash(
        receipt.model_dump(mode="json", exclude={"receipt_hash"})
    )
    evidence_store._write_json_atomic(output, receipt.model_dump(mode="json"))
    return receipt


__all__ = ["aggregate_usage", "build_run_receipt"]
