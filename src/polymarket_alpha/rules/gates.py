"""Fail-closed Gate A/B decisions using one RuleContract revision."""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from src.polymarket_alpha.contracts import RuleContract, RuleGate, stable_record_id

from .models import CompilationStatus, RuleCompilationReceipt, RuleGateDecision, RuleGateStage


def evaluate_gate_a(
    contract: RuleContract,
    compilation_receipt: RuleCompilationReceipt,
    *,
    run_id: str,
    evaluated_at: datetime,
) -> RuleGateDecision:
    if compilation_receipt.status != CompilationStatus.COMPILED:
        raise ValueError("Gate A requires a compiled RuleContract receipt")
    if compilation_receipt.rule_contract_id != contract.record_id:
        raise ValueError("Gate A compilation receipt does not match RuleContract")
    if compilation_receipt.input_rule_hash != contract.rule_hash:
        raise ValueError("Gate A compilation receipt rule hash mismatch")
    if compilation_receipt.compiler_version != contract.source_version:
        raise ValueError("Gate A compilation receipt compiler mismatch")
    reason = {
        RuleGate.PASS: "RULE_CONTRACT_CLEAR",
        RuleGate.WATCH_RULE: "RULE_REVIEW_REQUIRED",
        RuleGate.REJECT_RULE: "RULE_CONTRACT_REJECTED",
    }[contract.rule_gate]
    decision = contract.rule_gate.value
    record_id = stable_record_id(
        "rule_gate_decision",
        "A",
        contract.record_id,
        contract.rule_hash,
        contract.contract_revision_id,
        contract.source_version,
        compilation_receipt.record_id,
        decision,
    )
    return RuleGateDecision(
        record_id=record_id,
        run_id=run_id,
        created_at=evaluated_at,
        source="polymarket_alpha.rule_gate_a",
        source_version=contract.source_version,
        provenance=contract.provenance,
        extensions={},
        stage=RuleGateStage.A,
        market_id=contract.market_id,
        rule_contract_id=contract.record_id,
        rule_hash=contract.rule_hash,
        contract_revision_id=contract.contract_revision_id,
        compiler_version=contract.source_version,
        decision=decision,
        reasons=(reason,),
        input_artifact_ids=(contract.record_id, compilation_receipt.record_id),
        evaluated_at=evaluated_at,
    )


def evaluate_gate_b(
    contract: RuleContract,
    gate_a: RuleGateDecision,
    *,
    market_packet_id: str,
    market_packet_rule_hash: str,
    market_packet_contract_revision_id: str,
    run_id: str,
    evaluated_at: datetime,
    rule_risk_reasons: Sequence[str] = (),
) -> RuleGateDecision:
    blockers: list[str] = []
    if gate_a.stage != RuleGateStage.A:
        blockers.append("GATE_A_DECISION_REQUIRED")
    if gate_a.decision != RuleGate.PASS.value:
        blockers.append("GATE_A_NOT_PASS")
    if gate_a.market_id != contract.market_id:
        blockers.append("GATE_A_MARKET_MISMATCH")
    if gate_a.rule_contract_id != contract.record_id:
        blockers.append("GATE_A_CONTRACT_MISMATCH")
    if gate_a.rule_hash != contract.rule_hash:
        blockers.append("GATE_A_RULE_HASH_MISMATCH")
    if gate_a.contract_revision_id != contract.contract_revision_id:
        blockers.append("GATE_A_REVISION_MISMATCH")
    if gate_a.compiler_version != contract.source_version:
        blockers.append("GATE_A_COMPILER_MISMATCH")
    if market_packet_rule_hash != contract.rule_hash:
        blockers.append("MARKET_PACKET_RULE_HASH_MISMATCH")
    if market_packet_contract_revision_id != contract.contract_revision_id:
        blockers.append("MARKET_PACKET_RULE_REVISION_MISMATCH")
    if contract.rule_gate != RuleGate.PASS:
        blockers.append("CURRENT_RULE_CONTRACT_NOT_PASS")

    normalized_risks = tuple(dict.fromkeys(item.strip() for item in rule_risk_reasons if item.strip()))
    if blockers:
        decision = "BLOCK"
        reasons = tuple(dict.fromkeys(blockers))
    elif normalized_risks:
        decision = "PASS_WITH_RULE_RISK"
        reasons = normalized_risks
    else:
        decision = "PASS"
        reasons = ("RULE_A_B_SAME_HASH_INVARIANT_PASSED",)
    record_id = stable_record_id(
        "rule_gate_decision",
        "B",
        contract.record_id,
        gate_a.record_id,
        market_packet_id,
        market_packet_rule_hash,
        market_packet_contract_revision_id,
        decision,
        reasons,
    )
    return RuleGateDecision(
        record_id=record_id,
        run_id=run_id,
        created_at=evaluated_at,
        source="polymarket_alpha.rule_gate_b",
        source_version=contract.source_version,
        provenance=contract.provenance,
        extensions={},
        stage=RuleGateStage.B,
        market_id=contract.market_id,
        rule_contract_id=contract.record_id,
        rule_hash=contract.rule_hash,
        contract_revision_id=contract.contract_revision_id,
        compiler_version=contract.source_version,
        decision=decision,
        reasons=reasons,
        input_artifact_ids=(contract.record_id, gate_a.record_id, market_packet_id),
        evaluated_at=evaluated_at,
        gate_a_decision_id=gate_a.record_id,
    )
