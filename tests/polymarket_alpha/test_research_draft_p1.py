from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.polymarket_alpha.contracts import (
    BlindCandidateProjection, BlindResearchPacket, BlindResearchQuestion,
    BlindRuleView, CaptureScope, EstimateStage, EvidenceOrigin,
    EvidenceSupport, HashScope, PacketStage, Replayability,
    ResearchImportStatus, SourceTier, canonical_json,
)
from src.polymarket_alpha.research.draft import (
    ActualSourceBytes, DraftClaim, DraftEstimate, DraftSource, ResearchDraft,
    ResearchDraftError, compile_research_draft,
)
from src.polymarket_alpha.research.importer import import_research_result
from src.polymarket_alpha.security import audit_source_tree


UTC = timezone.utc
NOW = datetime(2026, 8, 29, 8, 0, tzinfo=UTC)
SHA = "a" * 64


def _env(record_id: str, source: str) -> dict[str, object]:
    return dict(record_id=record_id, run_id="blind_run:" + "b" * 64, created_at=NOW,
                source=source, source_version="fixture", provenance=(), extensions={})


def _packet() -> BlindResearchPacket:
    question = BlindResearchQuestion(
        question_id="blind_question:" + "c" * 64, template_id="RULE_TRIGGER_EVIDENCE",
        generated_from_rule_contract_hash=SHA, text="What primary source confirms the specified rule trigger?",
    )
    projection = BlindCandidateProjection(
        **_env("blind_candidate:" + "d" * 64, "blind_projection_builder"),
        blind_candidate_id="blind_candidate:" + "d" * 64, rule_contract_hash=SHA,
        neutral_proposition="The agency satisfies the stated condition.", subject_entity="Agency",
        deadline=NOW + timedelta(days=1), research_questions=(question,), evidence=(),
    )
    rule = BlindRuleView(rule_hash=SHA, subject_entity="Agency", entity_match_rule="The agency",
        yes_trigger="A final official bulletin confirms the condition", deadline=NOW + timedelta(days=1),
        timezone="UTC", resolution_sources=("Agency bulletin",), source_precedence=("final bulletin",),
        initial_or_final="FINAL", clarity_score=Decimal("0.9"), parser_version="fixture")
    return BlindResearchPacket(**_env("blind_packet:" + "e" * 64, "blind_packet_builder"),
        packet_stage=PacketStage.BLIND, projection=projection, blind_rule=rule)


def _draft(*, url: str = "https://agency.example/final", claim: str = "Agency issued a final bulletin.") -> ResearchDraft:
    source = DraftSource(source_key="final", source_name="Agency", source_url_or_source_id=url,
        media_type="text/plain", captured_at=NOW, effective_as_of=NOW - timedelta(minutes=1),
        capture_scope=CaptureScope.EXCERPT_ONLY, hash_scope=HashScope.CLAIM_EXCERPT,
        artifact_locator="artifact://agency/final", replayability=Replayability.EXCERPT)
    evidence = DraftClaim(source_key="final", claim=claim, supports_yes_or_no=EvidenceSupport.YES,
        source_tier=SourceTier.T0, accessed_at=NOW, effective_as_of=NOW - timedelta(minutes=1),
        primary_or_secondary="PRIMARY", quotation_or_paraphrase_location="paragraph 1",
        confidence=Decimal("0.9"), origin=EvidenceOrigin.PRIMARY_SOURCE, excerpt_context="final bulletin")
    estimate = DraftEstimate(model_type="manual", p_event_yes_low=Decimal("0.2"),
        p_event_yes_mid=Decimal("0.3"), p_event_yes_high=Decimal("0.4"),
        uncertainty_drivers=("timing",), assumptions=("official source",), model_version="v1")
    return ResearchDraft(sources=(source,), claims=(evidence,), estimate=estimate,
        completed_at=NOW, producer="human", producer_version="v1")


def _compile(**changes):
    return compile_research_draft(packet=_packet(), draft=changes.pop("draft", _draft()),
        actual_sources=changes.pop("actual_sources", (ActualSourceBytes(source_key="final", content=b"The final bulletin is published."),)),
        run_id=changes.pop("run_id", "research-run"), created_at=changes.pop("created_at", NOW), **changes)


def test_happy_blind_compiles_deterministically_and_is_importer_ready() -> None:
    first, second = _compile(), _compile()
    assert first.result.canonical_sha256 == second.result.canonical_sha256
    assert first.result.packet_stage == PacketStage.BLIND
    assert first.result.probability_estimate.estimate_stage == EstimateStage.BLIND
    assert first.result.probability_estimate.market_id is None
    assert tuple(item.evidence_id for item in first.result.evidence) == tuple(sorted(item.evidence_id for item in first.result.evidence))
    assert dict(first.source_contents)[first.result.source_artifacts[0].artifact_id] == b"The final bulletin is published."
    imported = import_research_result(
        packet=_packet(),
        submitted_bytes=canonical_json(first.result).encode("utf-8"),
        source_contents=first.source_contents,
        imported_at=NOW + timedelta(seconds=1),
        run_id="draft-import-run",
        submitted_artifact_locator="artifact://draft/submission",
    )
    assert imported.receipt.status == ResearchImportStatus.ACCEPTED
    assert imported.result == first.result


def test_compiler_recomputes_actual_hash_and_rejects_source_byte_cardinality() -> None:
    compiled = _compile()
    assert compiled.result.source_artifacts[0].content_sha256 != "a" * 64
    with pytest.raises(ResearchDraftError, match="exactly once"):
        _compile(actual_sources=())
    with pytest.raises(ResearchDraftError, match="unique"):
        _compile(actual_sources=(ActualSourceBytes(source_key="final", content=b"The final bulletin is published."), ActualSourceBytes(source_key="final", content=b"The final bulletin is published.")))
    with pytest.raises(ResearchDraftError, match="exactly once"):
        _compile(actual_sources=(ActualSourceBytes(source_key="other", content=b"x"),))
    forged = ActualSourceBytes(source_key="final", content=b"original").model_copy(
        update={"source_key": "other"}
    )
    with pytest.raises(ResearchDraftError, match="exactly once"):
        _compile(actual_sources=(forged,))


@pytest.mark.parametrize("draft", [
    _draft(url="https://polymarket.com/event/x"),
    _draft(claim="A wallet bought YES at 40% odds."),
])
def test_blind_semantic_leaks_fail_closed(draft: ResearchDraft) -> None:
    with pytest.raises(ResearchDraftError, match="Blind draft"):
        _compile(draft=draft)


def test_dtos_are_frozen_and_capture_context_must_bind_actual_bytes() -> None:
    draft = _draft()
    with pytest.raises(Exception):
        draft.model_copy(update={"producer": "changed"}).sources[0].source_name = "changed"  # type: ignore[misc]
    with pytest.raises(ResearchDraftError, match="context"):
        _compile(actual_sources=(ActualSourceBytes(source_key="final", content=b"unrelated"),))


def test_market_requires_exact_bound_blind_result_and_compiles_final() -> None:
    # Reuse the sealed market-packet fixture rather than building a second book
    # owner in this compiler test.
    from test_market_research_p0_08c import _demand_and_book, _freeze

    packet = _freeze()
    accepted = _demand_and_book()[4]
    base = _draft().estimate
    market_draft = _draft().model_copy(update={"estimate": base.model_copy(update={
        "p_market_yes_low": Decimal("0.4"), "p_market_yes_mid": Decimal("0.5"), "p_market_yes_high": Decimal("0.6"),
    })})
    compiled = compile_research_draft(packet=packet, draft=market_draft,
        actual_sources=(ActualSourceBytes(source_key="final", content=b"The final bulletin is published."),),
        run_id="market-research-run", created_at=NOW, accepted_blind_result=accepted)
    assert compiled.result.probability_estimate.estimate_stage == EstimateStage.FINAL
    assert compiled.result.probability_estimate.market_id == packet.market_id
    with pytest.raises(ResearchDraftError, match="does not bind"):
        compile_research_draft(packet=packet, draft=market_draft,
            actual_sources=(ActualSourceBytes(source_key="final", content=b"The final bulletin is published."),),
            run_id="market-research-run", created_at=NOW,
            accepted_blind_result=accepted.model_copy(update={"packet_sha256": "0" * 64}))

    other_packet_result = accepted.model_copy(
        update={"packet_id": "blind_packet:" + "9" * 64}
    )
    forged_provenance = tuple(
        item.model_copy(update={"content_sha256": other_packet_result.canonical_sha256})
        if item.relation == "accepted_blind_result"
        else item
        for item in packet.provenance
    )
    cross_packet = packet.model_copy(update={"provenance": forged_provenance})
    with pytest.raises(ResearchDraftError, match="does not bind"):
        compile_research_draft(
            packet=cross_packet,
            draft=market_draft,
            actual_sources=(
                ActualSourceBytes(
                    source_key="final",
                    content=b"The final bulletin is published.",
                ),
            ),
            run_id="market-research-run",
            created_at=NOW,
            accepted_blind_result=other_packet_result,
        )


def test_no_capability_violations() -> None:
    audit = audit_source_tree("src/polymarket_alpha/research/draft.py")
    assert audit.passed, audit.findings
