"""Core tool contracts; domain implementations stay in existing weather code."""

from __future__ import annotations

from .contracts import ActionResult, RiskLevel
from .domains.production_audit import PRODUCTION_TASK_TYPE
from .domains.strategy_research import STRATEGY_TASK_TYPE
from .tools import ToolContext, ToolContract, ToolRegistry


def _agent_side_only(arguments: dict, context: ToolContext) -> ActionResult:
    raise RuntimeError(
        "tool is agent-side; execute the existing typed adapter and record_external_result"
    )


def _contract(
    name: str,
    description: str,
    risk: RiskLevel,
    task_type: str,
    required: tuple[str, ...],
    *,
    replay_safe: bool | None = None,
) -> ToolContract:
    return ToolContract(
        name=name,
        description=description,
        risk=risk,
        task_types=(task_type,),
        agent_side=True,
        replay_safe=risk == RiskLevel.READ_ONLY if replay_safe is None else replay_safe,
        input_schema={
            "type": "object",
            "required": list(required),
            "additionalProperties": True,
        },
    )


CORE_CONTRACTS = (
    _contract("production.manifest", "Verify production and canonical identity", RiskLevel.READ_ONLY, PRODUCTION_TASK_TYPE, ()),
    _contract("production.sync_canonical", "Run bounded canonical synchronization and verify freshness", RiskLevel.DERIVED_DATA_WRITE, PRODUCTION_TASK_TYPE, ("target_date_start", "target_date_end")),
    _contract("production.account_reconcile", "Reconcile account, exchange fills and canonical live_real", RiskLevel.READ_ONLY, PRODUCTION_TASK_TYPE, ("start", "end")),
    _contract("production.exposure", "List authenticated open positions/orders with valuation clock", RiskLevel.READ_ONLY, PRODUCTION_TASK_TYPE, ()),
    _contract("production.lineage", "Audit signal-order-fill-settlement lineage for the target window", RiskLevel.READ_ONLY, PRODUCTION_TASK_TYPE, ("start", "end")),
    _contract("production.review_findings", "Classify findings and identify root-cause repair scope", RiskLevel.READ_ONLY, PRODUCTION_TASK_TYPE, ()),
    _contract("production.repair_code", "Repair a confirmed repository root cause", RiskLevel.REPOSITORY_WRITE, PRODUCTION_TASK_TYPE, ("finding_ids",)),
    _contract("production.repair_data", "Repair confirmed derived canonical data", RiskLevel.DERIVED_DATA_WRITE, PRODUCTION_TASK_TYPE, ("finding_ids", "affected_window")),
    _contract("production.replay", "Replay the exact affected window and emit itemized before/after diff", RiskLevel.DERIVED_DATA_WRITE, PRODUCTION_TASK_TYPE, ("affected_window",)),
    _contract("production.tests", "Run targeted and regression tests", RiskLevel.READ_ONLY, PRODUCTION_TASK_TYPE, ()),
    _contract("production.summary", "Generate the one-page human summary from evidence", RiskLevel.DERIVED_DATA_WRITE, PRODUCTION_TASK_TYPE, ()),
    _contract("strategy.readiness", "Verify PIT, build, quote, label and forward readiness", RiskLevel.READ_ONLY, STRATEGY_TASK_TYPE, ()),
    _contract("strategy.baseline", "Establish frozen denominator and same-row market baseline", RiskLevel.DERIVED_DATA_WRITE, STRATEGY_TASK_TYPE, ()),
    _contract("strategy.experiment", "Run one attributable development challenger", RiskLevel.DERIVED_DATA_WRITE, STRATEGY_TASK_TYPE, ("hypothesis_id", "changed_factor")),
    _contract("strategy.qualify", "Evaluate the frozen champion once on sealed forward evidence", RiskLevel.DERIVED_DATA_WRITE, STRATEGY_TASK_TYPE, ("frozen_champion_hash",)),
    _contract("strategy.collect_evidence", "Start or extend collector/zero-notional shadow evidence", RiskLevel.PRODUCTION_CHANGE, STRATEGY_TASK_TYPE, ("resume_condition",)),
)


def build_core_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for contract in CORE_CONTRACTS:
        registry.register(contract, _agent_side_only)
    return registry


__all__ = ["CORE_CONTRACTS", "build_core_registry"]
