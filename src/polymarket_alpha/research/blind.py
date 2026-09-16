"""Deterministic construction of the allowlist-only Blind research packet.

This module deliberately has no adapter or storage dependency.  It consumes
already-frozen contracts and emits a projection whose schema-level validators
reject market, price, wallet, and operator side channels.
"""

from __future__ import annotations

from datetime import datetime
from typing import Mapping, NamedTuple, Sequence

from src.polymarket_alpha.contracts import (
    BlindCandidateProjection,
    BlindResearchPacket,
    BlindResearchQuestion,
    BlindRuleView,
    CandidateCard,
    ClaimEvidence,
    EvidenceOrigin,
    RecallHit,
    RuleContract,
    RuleGate,
    blind_leak_reasons,
    content_sha256,
    stable_record_id,
)
from src.polymarket_alpha.contracts.base import ensure_utc
from src.polymarket_alpha.rules.models import RuleGateDecision, RuleGateStage


BUILDER_VERSION = "p0_08a_v1"

# Text is owned here, not supplied by an operator or recall provider.  Values
# interpolated into a template are strictly from the cleared rule contract.
BLIND_QUESTION_TEMPLATES: Mapping[str, str] = {
    "BASE_RATE": (
        "What historical base-rate evidence is relevant to whether {subject} "
        "satisfies the defined rule condition?"
    ),
    "DEADLINE_STATUS": (
        "What primary evidence can establish the relevant status before "
        "{deadline}?"
    ),
    "DISCONFIRMING_EVIDENCE": (
        "What reliable evidence would disconfirm that {subject} satisfies "
        "the defined rule condition?"
    ),
    "ENTITY_STATUS": "What authoritative sources establish the status of {subject}?",
    "RULE_TRIGGER_EVIDENCE": (
        "What primary evidence directly addresses this rule condition: {trigger}?"
    ),
    "SOURCE_CONFLICT": (
        "Which authoritative sources can resolve conflicts about whether {subject} "
        "satisfies the defined rule condition?"
    ),
}


class BlindPacketBuild(NamedTuple):
    """Both immutable artifacts emitted in one deterministic build."""

    projection: BlindCandidateProjection
    packet: BlindResearchPacket


def _require_gate_a_pass(contract: RuleContract, gate_a: RuleGateDecision) -> None:
    if contract.rule_gate != RuleGate.PASS:
        raise ValueError("Blind construction requires RuleContract Gate A PASS")
    if gate_a.stage != RuleGateStage.A or gate_a.decision != RuleGate.PASS.value:
        raise ValueError("Blind construction requires an explicit Gate A PASS decision")
    if gate_a.market_id != contract.market_id:
        raise ValueError("Gate A decision market does not match RuleContract")
    if gate_a.rule_contract_id != contract.record_id:
        raise ValueError("Gate A decision does not reference this RuleContract")
    if gate_a.rule_hash != contract.rule_hash:
        raise ValueError("Gate A decision rule hash does not match RuleContract")
    if gate_a.contract_revision_id != contract.contract_revision_id:
        raise ValueError("Gate A decision revision does not match RuleContract")


def _require_candidate_lineage(
    candidate: CandidateCard,
    recall_hits: Sequence[RecallHit],
    contract: RuleContract,
) -> None:
    if candidate.market_id != contract.market_id:
        raise ValueError("Candidate market does not match RuleContract")
    hit_ids = tuple(hit.record_id for hit in recall_hits)
    if len(hit_ids) != len(set(hit_ids)):
        raise ValueError("Recall inputs must not contain duplicate records")
    if set(hit_ids) != set(candidate.recall_hit_ids):
        raise ValueError("Recall inputs must exactly match CandidateCard recall_hit_ids")
    if any(hit.market_id != candidate.market_id for hit in recall_hits):
        raise ValueError("RecallHit market does not match CandidateCard")


def _allowed_evidence(evidence: Sequence[ClaimEvidence]) -> tuple[ClaimEvidence, ...]:
    """Reject, rather than silently redact, a non-blind evidence source."""

    forbidden = {
        EvidenceOrigin.WALLET,
        EvidenceOrigin.MARKET,
        EvidenceOrigin.OPERATOR_COMMENTARY,
    }
    normalized = tuple(sorted(evidence, key=lambda item: item.evidence_id))
    ids = tuple(item.evidence_id for item in normalized)
    if len(ids) != len(set(ids)):
        raise ValueError("Blind evidence identifiers must be unique")
    if any(item.origin in forbidden for item in normalized):
        raise ValueError("wallet, market, and operator evidence cannot enter Blind")
    return normalized


def _question(
    *,
    template_id: str,
    contract: RuleContract,
    evidence_ids: Sequence[str],
) -> BlindResearchQuestion:
    try:
        template = BLIND_QUESTION_TEMPLATES[template_id]
    except KeyError as error:
        raise ValueError(f"unapproved blind question template: {template_id}") from error

    raw_ids = tuple(evidence_ids)
    if len(raw_ids) != len(set(raw_ids)):
        raise ValueError("question evidence provenance must not contain duplicate ids")
    normalized_ids = tuple(sorted(raw_ids))
    text = template.format(
        subject=contract.subject_entity,
        trigger=contract.yes_trigger,
        deadline=(contract.deadline.isoformat() if contract.deadline else "the rule deadline"),
    )
    question_id = stable_record_id(
        "blind_question",
        {
            "template_id": template_id,
            "rule_hash": contract.rule_hash,
            "evidence_ids": normalized_ids,
        },
    )
    return BlindResearchQuestion(
        question_id=question_id,
        template_id=template_id,
        generated_from_rule_contract_hash=contract.rule_hash,
        generated_from_evidence_ids=normalized_ids,
        text=text,
    )


def build_blind_research_packet(
    *,
    candidate: CandidateCard,
    recall_hits: Sequence[RecallHit],
    contract: RuleContract,
    gate_a: RuleGateDecision,
    evidence: Sequence[ClaimEvidence] = (),
    template_ids: Sequence[str] = tuple(BLIND_QUESTION_TEMPLATES),
    evidence_ids_by_template: Mapping[str, Sequence[str]] | None = None,
    build_run_identity: str,
    created_at: datetime,
) -> BlindPacketBuild:
    """Build frozen Blind artifacts without serializing candidate/recall facts.

    ``recall_hits`` are validated solely as private lineage.  Their reasons,
    features, wallet facts, score, and source metadata are never copied into a
    projection or packet.  ``template_ids`` and the optional evidence mapping
    are provenance selectors only; arbitrary question text is not accepted.
    """

    if not build_run_identity.strip():
        raise ValueError("build_run_identity must not be blank")
    created_at = ensure_utc(created_at)
    _require_gate_a_pass(contract, gate_a)
    _require_candidate_lineage(candidate, recall_hits, contract)
    frozen_evidence = _allowed_evidence(evidence)
    known_evidence_ids = {item.evidence_id for item in frozen_evidence}

    normalized_templates = tuple(sorted(set(template_ids)))
    if not normalized_templates:
        raise ValueError("at least one controlled blind question template is required")
    if len(normalized_templates) != len(tuple(template_ids)):
        raise ValueError("blind question template ids must be unique")
    provenance = evidence_ids_by_template or {}
    if set(provenance) - set(normalized_templates):
        raise ValueError("question provenance references an unselected template")
    for template_id, ids in provenance.items():
        if any(item not in known_evidence_ids for item in ids):
            raise ValueError("question provenance references evidence outside Blind allowlist")

    questions = tuple(
        _question(
            template_id=template_id,
            contract=contract,
            evidence_ids=provenance.get(template_id, ()),
        )
        for template_id in normalized_templates
    )
    blind_run_id = stable_record_id("blind_run", build_run_identity)
    blind_candidate_id = stable_record_id(
        "blind_candidate",
        {
            "candidate_id": candidate.candidate_id,
            "rule_hash": contract.rule_hash,
            "contract_revision_id": contract.contract_revision_id,
            "questions": tuple(question.question_id for question in questions),
            "evidence": tuple(item.evidence_id for item in frozen_evidence),
        },
    )
    neutral_proposition = (
        f"Determine whether {contract.subject_entity} satisfies the defined rule condition: "
        f"{contract.yes_trigger}"
    )
    projection = BlindCandidateProjection(
        record_id=blind_candidate_id,
        run_id=blind_run_id,
        created_at=created_at,
        source="blind_projection_builder",
        source_version=BUILDER_VERSION,
        provenance=(),
        extensions={},
        blind_candidate_id=blind_candidate_id,
        rule_contract_hash=contract.rule_hash,
        neutral_proposition=neutral_proposition,
        subject_entity=contract.subject_entity,
        deadline=contract.deadline,
        research_questions=questions,
        evidence=frozen_evidence,
    )
    blind_rule = BlindRuleView.from_rule_contract(contract)
    rule_leaks = blind_leak_reasons(blind_rule.model_dump(mode="python"))
    if rule_leaks:
        raise ValueError(f"Blind rule view contains market-derived semantics: {rule_leaks}")
    packet_id = stable_record_id(
        "blind_packet",
        {
            "projection_sha256": projection.canonical_sha256,
            "blind_rule_sha256": content_sha256(blind_rule),
        },
    )
    packet = BlindResearchPacket(
        record_id=packet_id,
        run_id=blind_run_id,
        created_at=created_at,
        source="blind_packet_builder",
        source_version=BUILDER_VERSION,
        provenance=(),
        extensions={},
        projection=projection,
        blind_rule=blind_rule,
    )
    return BlindPacketBuild(projection=projection, packet=packet)
