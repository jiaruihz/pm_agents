"""P0-08A: deterministic and semantically isolated Blind packet construction."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.polymarket_alpha.contracts import (
    CandidateCard,
    CaptureScope,
    ClaimEvidence,
    EvidenceOrigin,
    EvidenceSupport,
    HashScope,
    RecallHit,
    RecallerType,
    Replayability,
    ResearchPriority,
    RuleContract,
    RuleGate,
    SourceTier,
    canonical_json,
    content_sha256,
    stable_record_id,
)
from src.polymarket_alpha.research import build_blind_research_packet
from src.polymarket_alpha.rules.models import RuleGateDecision, RuleGateStage


UTC = timezone.utc
NOW = datetime(2026, 8, 27, 8, 0, tzinfo=UTC)
RULE_HASH = "a" * 64
CONTRACT_ID = "rule_contract:" + "b" * 64
REVISION_ID = "rule_revision:" + "c" * 64


def _contract(*, trigger: str = "A final public bulletin confirms the defined event") -> RuleContract:
    return RuleContract(
        record_id=CONTRACT_ID,
        run_id="rule-run-fixture",
        created_at=NOW,
        source="fixture_rule_compiler",
        source_version="fixture-v1",
        market_id="market-fixture",
        rule_hash=RULE_HASH,
        contract_revision_id=REVISION_ID,
        subject_entity="Example Agency",
        entity_match_rule="The named agency in the rule",
        yes_trigger=trigger,
        deadline=NOW + timedelta(days=2),
        timezone="UTC",
        resolution_sources=("Example Agency public bulletin",),
        source_precedence=("final bulletin",),
        initial_or_final="FINAL",
        clarity_score=Decimal("0.95"),
        rule_gate=RuleGate.PASS,
        parser_version="fixture-v1",
    )


def _gate(contract: RuleContract, *, decision: str = "PASS") -> RuleGateDecision:
    return RuleGateDecision(
        record_id=stable_record_id("rule_gate_decision", contract.record_id, decision),
        run_id="gate-run-fixture",
        created_at=NOW,
        source="fixture_gate",
        source_version="fixture-v1",
        stage=RuleGateStage.A,
        market_id=contract.market_id,
        rule_contract_id=contract.record_id,
        rule_hash=contract.rule_hash,
        contract_revision_id=contract.contract_revision_id,
        compiler_version=contract.source_version,
        decision=decision,
        reasons=("fixture",),
        input_artifact_ids=(contract.record_id,),
        evaluated_at=NOW,
    )


def _recall(*, recaller: RecallerType = RecallerType.NEW_CHANGED, suffix: str = "1") -> RecallHit:
    return RecallHit(
        record_id=f"recall:{suffix}",
        run_id="recall-run-fixture",
        created_at=NOW,
        source="fixture_recaller",
        source_version="fixture-v1",
        market_id="market-fixture",
        recaller=recaller,
        recaller_version="fixture-v1",
        reason_codes=(f"REASON_{suffix}",),
        features={"private": f"recall-private-{suffix}"},
        raw_score=Decimal("1"),
        observed_at=NOW,
    )


def _candidate(*hits: RecallHit) -> CandidateCard:
    return CandidateCard(
        record_id="candidate_card:" + "d" * 64,
        run_id="candidate-run-fixture",
        created_at=NOW,
        source="fixture_aggregator",
        source_version="fixture-v1",
        candidate_id="candidate:" + "e" * 64,
        market_id="market-fixture",
        recall_hit_ids=tuple(hit.record_id for hit in hits),
        recall_score=Decimal("1"),
        dedup_group="fixture-group",
        selected_at=NOW,
        research_priority=ResearchPriority.CORE,
        selection_rationale=("private candidate rationale",),
    )


def _evidence(*, origin: EvidenceOrigin = EvidenceOrigin.PRIMARY_SOURCE) -> ClaimEvidence:
    content = "Example Agency published a neutral factual statement."
    evidence_id = "evidence:" + content_sha256({"content": content, "origin": origin.value})
    return ClaimEvidence(
        record_id=evidence_id,
        run_id="evidence-run-fixture",
        created_at=NOW,
        source="fixture_evidence",
        source_version="fixture-v1",
        evidence_id=evidence_id,
        claim=content,
        supports_yes_or_no=EvidenceSupport.NEUTRAL,
        source_tier=SourceTier.T0,
        source_name="Example Agency",
        source_url_or_source_id="https://agency.example/evidence",
        accessed_at=NOW,
        effective_as_of=NOW,
        primary_or_secondary="PRIMARY",
        quotation_or_paraphrase_location="paragraph 1",
        confidence=Decimal("0.9"),
        origin=origin,
        source_artifact_id="source_artifact:" + "f" * 64,
        capture_scope=CaptureScope.EXCERPT_ONLY,
        hash_scope=HashScope.CLAIM_EXCERPT,
        content_sha256=content_sha256(content),
        excerpt_context=content,
        replayability=Replayability.EXCERPT,
    )


def _build(**overrides):
    contract = overrides.pop("contract", _contract())
    hits = overrides.pop("hits", (_recall(),))
    return build_blind_research_packet(
        candidate=overrides.pop("candidate", _candidate(*hits)),
        recall_hits=overrides.pop("recall_hits", hits),
        contract=contract,
        gate_a=overrides.pop("gate_a", _gate(contract)),
        build_run_identity=overrides.pop("build_run_identity", "offline-fixture-run"),
        created_at=overrides.pop("created_at", NOW),
        **overrides,
    )


def test_builds_allowlist_only_packet_and_private_recall_fields_do_not_serialize() -> None:
    evidence = _evidence()
    built = _build(
        evidence=(evidence,),
        evidence_ids_by_template={"SOURCE_CONFLICT": (evidence.evidence_id,)},
    )

    assert built.projection.rule_contract_hash == RULE_HASH
    assert built.packet.projection == built.projection
    assert built.packet.blind_rule.rule_hash == RULE_HASH
    rendered = canonical_json(built.packet)
    assert "recall-private" not in rendered
    assert "private candidate rationale" not in rendered
    assert "market-fixture" not in rendered
    assert "wallet" not in rendered.lower()
    question = next(q for q in built.projection.research_questions if q.template_id == "SOURCE_CONFLICT")
    assert question.generated_from_evidence_ids == (evidence.evidence_id,)


def test_gate_a_failure_is_fail_closed() -> None:
    contract = _contract()
    with pytest.raises(ValueError, match="Gate A PASS"):
        _build(contract=contract, gate_a=_gate(contract, decision="WATCH_RULE"))


def test_wallet_recall_can_trigger_candidate_but_never_enters_blind_packet() -> None:
    hit = _recall(recaller=RecallerType.SPECIALIST_WALLET, suffix="wallet")
    built = _build(hits=(hit,))
    rendered = canonical_json(built.packet).lower()
    assert "specialist_wallet" not in rendered
    assert "reason_wallet" not in rendered
    with pytest.raises(ValueError, match="cannot enter Blind"):
        _build(evidence=(_evidence(origin=EvidenceOrigin.WALLET),))


@pytest.mark.parametrize(
    "trigger, expected",
    [
        ("The bid is favorable", "price"),
        ("Buy Yes before the deadline", "direction"),
        ("See slug: private-market", "slug"),
        ("See https://polymarket.com/event/private", "polymarket"),
        ("Gamma API source confirms the event", "polymarket"),
        ("A wallet holder confirms the event", "wallet"),
        ("The market thinks the event happens", "operator_market_commentary"),
    ],
)
def test_adversarial_rule_strings_are_rejected_recursively(trigger: str, expected: str) -> None:
    with pytest.raises(ValueError, match=expected):
        _build(contract=_contract(trigger=trigger))


def test_question_provenance_is_template_only_and_allowlisted() -> None:
    evidence = _evidence()
    with pytest.raises(ValueError, match="unapproved blind question template"):
        _build(template_ids=("OPERATOR_FREE_TEXT",))
    with pytest.raises(ValueError, match="outside Blind allowlist"):
        _build(
            evidence=(evidence,),
            evidence_ids_by_template={"BASE_RATE": ("evidence:" + "0" * 64,)},
        )
    with pytest.raises(ValueError, match="unselected template"):
        _build(evidence_ids_by_template={"BASE_RATE": ()}, template_ids=("ENTITY_STATUS",))
    with pytest.raises(ValueError, match="duplicate ids"):
        _build(
            evidence=(evidence,),
            evidence_ids_by_template={"BASE_RATE": (evidence.evidence_id, evidence.evidence_id)},
        )


def test_duplicate_recall_lineage_is_rejected_instead_of_silently_collapsed() -> None:
    hit = _recall()
    with pytest.raises(ValueError, match="duplicate records"):
        _build(hits=(hit,), recall_hits=(hit, hit))


def test_adversarial_nested_rule_view_strings_are_rejected() -> None:
    unsafe = _contract().model_copy(update={"entity_match_rule": "Use the current bid record"})
    with pytest.raises(ValueError, match="price"):
        _build(contract=unsafe)


def test_input_reordering_has_identical_packet_hash_and_questions() -> None:
    first, second = _recall(suffix="a"), _recall(suffix="b")
    first_evidence, second_evidence = _evidence(), _evidence(origin=EvidenceOrigin.SECONDARY_SOURCE)
    candidate = _candidate(first, second)
    forward = _build(
        candidate=candidate,
        hits=(first, second),
        evidence=(first_evidence, second_evidence),
        template_ids=("SOURCE_CONFLICT", "BASE_RATE"),
        evidence_ids_by_template={"SOURCE_CONFLICT": (second_evidence.evidence_id, first_evidence.evidence_id)},
    )
    reverse = _build(
        candidate=candidate,
        hits=(second, first),
        evidence=(second_evidence, first_evidence),
        template_ids=("BASE_RATE", "SOURCE_CONFLICT"),
        evidence_ids_by_template={"SOURCE_CONFLICT": (first_evidence.evidence_id, second_evidence.evidence_id)},
    )
    assert forward.projection.canonical_sha256 == reverse.projection.canonical_sha256
    assert forward.packet.canonical_sha256 == reverse.packet.canonical_sha256
