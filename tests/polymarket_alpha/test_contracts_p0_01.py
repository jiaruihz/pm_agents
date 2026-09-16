from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

from pydantic import ValidationError
import pytest

from src.polymarket_alpha.contracts import (
    ALPHA_CONTRACT_VERSION,
    RESEARCH_PACKET_ADAPTER,
    BlindCandidateProjection,
    BlindResearchPacket,
    BlindResearchQuestion,
    BlindRuleView,
    BookLeg,
    BookLevel,
    CandidateCard,
    CandidateEventType,
    CaptureScope,
    ClaimEvidence,
    EvidenceOrigin,
    EvidenceSupport,
    HashScope,
    MarketIdentity,
    MarketSnapshot,
    MarketStatus,
    PacketStage,
    ProbabilityEstimate,
    Replayability,
    ResearchPriority,
    RuleContract,
    RuleGate,
    SourceTier,
    ThresholdOperator,
    ThresholdSpec,
    canonical_json,
    compatibility_disposition,
    content_sha256,
    contract_schema_bundle,
    contract_schema_fingerprint,
    rule_sha256,
    stable_record_id,
)
from src.polymarket_alpha.contracts.base import CommonEnvelope, canonical_datetime
from src.polymarket_alpha.contracts.models import EstimateStage


UTC = timezone.utc
FIXTURES = Path(__file__).with_name("fixtures")
NOW = datetime(2026, 8, 26, 4, 5, 6, 120000, tzinfo=UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _env(record_id: str, *, source: str = "fixture") -> dict[str, object]:
    return {
        "schema_version": ALPHA_CONTRACT_VERSION,
        "record_id": record_id,
        "run_id": f"test_run:{SHA_A}",
        "created_at": NOW,
        "source": source,
        "source_version": "fixture-v1",
        "provenance": (),
        "extensions": {},
    }


def _rule_contract(*, resolution_sources: tuple[str, ...] = ("https://example.org/official",)) -> RuleContract:
    return RuleContract(
        **_env(f"rule_contract:{SHA_A}"),
        market_id="market-secret-123",
        rule_hash=SHA_A,
        contract_revision_id=f"rule_revision:{SHA_A}",
        subject_entity="Example Agency",
        entity_match_rule="The named agency in the official bulletin",
        yes_trigger="The official bulletin confirms the stated event by the deadline",
        threshold=ThresholdSpec(operator=ThresholdOperator.GTE, value=Decimal("1"), unit="event"),
        deadline=NOW + timedelta(days=10),
        timezone="UTC",
        resolution_sources=resolution_sources,
        source_precedence=("official bulletin",),
        initial_or_final="FINAL",
        qualifying_examples=("A final bulletin explicitly confirms the event",),
        non_qualifying_examples=("A preliminary rumor",),
        ambiguities=("Late correction after deadline",),
        clarity_score=Decimal("0.91"),
        rule_gate=RuleGate.PASS,
        parser_version="rule-compiler-v1",
    )


def _question(*, text: str = "What primary evidence confirms the rule trigger?") -> BlindResearchQuestion:
    return BlindResearchQuestion(
        question_id=f"blind_question:{SHA_B}",
        template_id="RULE_TRIGGER_EVIDENCE",
        generated_from_rule_contract_hash=SHA_A,
        generated_from_evidence_ids=(),
        text=text,
    )


def _evidence(**overrides: object) -> ClaimEvidence:
    values: dict[str, object] = {
        **_env(f"evidence:{SHA_D}", source="claim_importer"),
        "run_id": f"blind_run:{SHA_C}",
        "evidence_id": f"evidence:{SHA_D}",
        "entity_id": "entity:example-agency",
        "claim": "The agency published a final bulletin.",
        "supports_yes_or_no": EvidenceSupport.YES,
        "source_tier": SourceTier.T0,
        "source_name": "Example Agency",
        "source_url_or_source_id": "https://example.org/official/bulletin",
        "published_at": NOW - timedelta(hours=2),
        "accessed_at": NOW,
        "effective_as_of": NOW - timedelta(hours=1),
        "primary_or_secondary": "PRIMARY",
        "quotation_or_paraphrase_location": "section 2",
        "confidence": Decimal("0.9"),
        "origin": EvidenceOrigin.PRIMARY_SOURCE,
        "source_artifact_id": f"source_artifact:{SHA_D}",
        "capture_scope": CaptureScope.EXCERPT_ONLY,
        "hash_scope": HashScope.CLAIM_EXCERPT,
        "content_sha256": SHA_D,
        "excerpt_context": "Final bulletin excerpt with surrounding context.",
        "replayability": Replayability.EXCERPT,
    }
    values.update(overrides)
    return ClaimEvidence.model_validate(values)


def _projection(*, evidence: tuple[ClaimEvidence, ...] = ()) -> BlindCandidateProjection:
    return BlindCandidateProjection(
        schema_version=ALPHA_CONTRACT_VERSION,
        record_id=f"blind_candidate:{SHA_A}",
        blind_candidate_id=f"blind_candidate:{SHA_A}",
        run_id=f"blind_run:{SHA_C}",
        created_at=NOW,
        source="blind_projection_builder",
        source_version="projection-v1",
        provenance=(),
        extensions={},
        rule_contract_hash=SHA_A,
        neutral_proposition="The official bulletin confirms the stated event by the deadline.",
        subject_entity="Example Agency",
        deadline=NOW + timedelta(days=10),
        research_questions=(_question(),),
        evidence=evidence,
    )


def test_canonical_datetime_decimal_unicode_and_key_order_are_stable() -> None:
    assert canonical_datetime(NOW) == "2026-08-26T04:05:06.12Z"
    assert canonical_datetime(NOW.replace(microsecond=0)) == "2026-08-26T04:05:06Z"
    left = {"z": Decimal("1.2300"), "é": "café", "time": NOW}
    right = {"time": NOW, "e\u0301": "cafe\u0301", "z": Decimal("1.23")}
    assert canonical_json(left) == canonical_json(right)
    assert content_sha256(left) == content_sha256(right)
    assert json.loads(canonical_json(left))["z"] == "1.23"
    with pytest.raises(ValueError, match="collide"):
        canonical_json({"é": 1, "e\u0301": 2})


@pytest.mark.parametrize("bad", [1.0, float("nan"), float("inf")])
def test_canonical_contracts_reject_floats(bad: float) -> None:
    with pytest.raises((TypeError, ValueError)):
        canonical_json({"value": bad})


def test_contract_models_reject_float_inputs_before_decimal_coercion() -> None:
    with pytest.raises(ValidationError, match="float is forbidden"):
        BookLevel(price=0.5, size=Decimal("1"))


def test_stable_record_id_is_deterministic_and_identity_sensitive() -> None:
    first = stable_record_id("market_snapshot", "market-1", NOW, Decimal("0.50"))
    assert first == stable_record_id("market_snapshot", "market-1", NOW, Decimal("0.500"))
    assert first != stable_record_id("market_snapshot", "market-2", NOW, Decimal("0.50"))
    assert first.startswith("market_snapshot:")


def test_common_envelope_rejects_naive_time_unknown_version_and_extra_field() -> None:
    base = _env(f"record:{SHA_A}")
    with pytest.raises(ValidationError):
        CommonEnvelope.model_validate({**base, "created_at": NOW.replace(tzinfo=None)})
    with pytest.raises(ValidationError):
        CommonEnvelope.model_validate({**base, "schema_version": "future-v9"})
    with pytest.raises(ValidationError):
        CommonEnvelope.model_validate({**base, "surprise": True})


def test_market_snapshot_freezes_rule_revision_and_explicit_yes_no_identity() -> None:
    identity = MarketIdentity(
        event_id="event-1",
        market_id="market-1",
        yes_token_id="token-yes",
        no_token_id="token-no",
    )
    snapshot = MarketSnapshot(
        **_env(f"market_snapshot:{SHA_A}"),
        identity=identity,
        title="Example",
        question="Will the event occur?",
        status=MarketStatus.ACTIVE,
        rules_raw="  Final bulletin\r\nconfirms event.  ",
        source_observed_at=NOW,
        ingested_at=NOW + timedelta(seconds=1),
    )
    assert snapshot.rule_hash == rule_sha256(snapshot.rules_raw)
    assert snapshot.rules_normalized == "Final bulletin\nconfirms event."
    with pytest.raises(ValidationError):
        MarketIdentity(event_id="e", market_id="m", yes_token_id="same", no_token_id="same")


def test_book_leg_requires_canonical_sorting_and_unique_prices() -> None:
    valid = BookLeg(
        token_id="token-yes",
        bids=(BookLevel(price=Decimal("0.5"), size=Decimal("2")),),
        asks=(BookLevel(price=Decimal("0.6"), size=Decimal("3")),),
    )
    assert valid.bids[0].price == Decimal("0.5")
    with pytest.raises(ValidationError):
        BookLeg(
            token_id="token-yes",
            bids=(
                BookLevel(price=Decimal("0.4"), size=Decimal("1")),
                BookLevel(price=Decimal("0.5"), size=Decimal("1")),
            ),
        )


def test_blind_rule_view_removes_market_identity() -> None:
    view = BlindRuleView.from_rule_contract(_rule_contract())
    serialized = canonical_json(view)
    assert "market-secret-123" not in serialized
    assert "market_id" not in serialized
    assert view.rule_hash == SHA_A
    with pytest.raises(ValidationError):
        BlindRuleView.from_rule_contract(
            _rule_contract(resolution_sources=("https://polymarket.com/event/secret",))
        )


@pytest.mark.parametrize(
    "text",
    [
        "Why does the market give this event 10%?",
        "Should a trader buy YES?",
        "Use slug: secret-event to research this.",
        "Open https://polymarket.com/event/secret and investigate.",
        "What did the whale wallet buy?",
        "Why are the odds at 10% probability?",
    ],
)
def test_blind_question_rejects_semantic_side_channels(text: str) -> None:
    with pytest.raises(ValidationError):
        _question(text=text)


def test_blind_question_requires_approved_template_and_opaque_provenance() -> None:
    base = _question().model_dump(mode="python")
    with pytest.raises(ValidationError):
        BlindResearchQuestion.model_validate({**base, "template_id": "FREEFORM"})
    with pytest.raises(ValidationError):
        BlindResearchQuestion.model_validate(
            {**base, "generated_from_evidence_ids": ("wallet-trade-buy-yes",)}
        )


def test_blind_projection_rejects_nested_market_wallet_and_operator_payloads() -> None:
    wallet = _evidence(origin=EvidenceOrigin.WALLET)
    with pytest.raises(ValidationError):
        _projection(evidence=(wallet,))

    market_commentary = _evidence(claim="The market thinks this is unlikely.")
    with pytest.raises(ValidationError):
        _projection(evidence=(market_commentary,))

    polymarket_source = _evidence(source_url_or_source_id="https://polymarket.com/event/secret")
    with pytest.raises(ValidationError):
        _projection(evidence=(polymarket_source,))


def test_blind_projection_requires_controlled_opaque_envelope() -> None:
    values = _projection().model_dump(mode="python")
    with pytest.raises(ValidationError):
        BlindCandidateProjection.model_validate({**values, "record_id": "secret-market-slug"})
    with pytest.raises(ValidationError):
        BlindCandidateProjection.model_validate({**values, "run_id": "operator-run-market-1"})
    with pytest.raises(ValidationError):
        BlindCandidateProjection.model_validate({**values, "market_id": "market-secret-123"})


def test_blind_packet_roundtrip_has_no_market_identity_or_price_payload() -> None:
    projection = _projection(evidence=(_evidence(),))
    packet = BlindResearchPacket(
        schema_version=ALPHA_CONTRACT_VERSION,
        record_id=f"blind_packet:{SHA_B}",
        run_id=f"blind_run:{SHA_C}",
        created_at=NOW,
        source="blind_packet_builder",
        source_version="packet-v1",
        provenance=(),
        extensions={},
        packet_stage=PacketStage.BLIND,
        projection=projection,
        blind_rule=BlindRuleView.from_rule_contract(_rule_contract()),
    )
    payload = json.loads(canonical_json(packet))
    parsed = RESEARCH_PACKET_ADAPTER.validate_python(payload)
    assert isinstance(parsed, BlindResearchPacket)
    assert parsed.canonical_sha256 == packet.canonical_sha256
    text = canonical_json(packet).lower()
    for forbidden in ("market_id", "market-secret-123", "yes_token_id", "no_token_id", "polymarket"):
        assert forbidden not in text
    with pytest.raises(ValidationError):
        BlindResearchPacket.model_validate({**packet.model_dump(mode="python"), "rule_contract": _rule_contract()})


def test_claim_evidence_capture_hash_and_replayability_semantics() -> None:
    reference = _evidence(
        capture_scope=CaptureScope.REFERENCE_ONLY,
        hash_scope=None,
        content_sha256=None,
        excerpt_context=None,
        replayability=Replayability.REFERENCE_ONLY,
    )
    assert reference.content_sha256 is None
    with pytest.raises(ValidationError):
        _evidence(
            capture_scope=CaptureScope.REFERENCE_ONLY,
            hash_scope=HashScope.RAW_BYTES,
            content_sha256=SHA_A,
            replayability=Replayability.REFERENCE_ONLY,
        )
    with pytest.raises(ValidationError):
        _evidence(excerpt_context=None)
    with pytest.raises(ValidationError, match="evidence_id must equal record_id"):
        _evidence(record_id=f"evidence:{SHA_A}")


def test_candidate_identity_and_card_revision_use_distinct_stable_namespaces() -> None:
    with pytest.raises(ValidationError, match="candidate_id must be a stable"):
        CandidateCard(
            **_env(f"candidate:{SHA_A}"),
            candidate_id="market-readable-candidate",
            market_id="market-1",
            recall_hit_ids=(f"recall_hit:{SHA_C}",),
            recall_score=Decimal("0.5"),
            dedup_group="market-1",
            selected_at=NOW,
            research_priority=ResearchPriority.CORE,
            selection_rationale=("new market",),
        )
    valid = CandidateCard(
        **_env(f"candidate_card:{SHA_A}"),
        candidate_id=f"candidate:{SHA_B}",
        market_id="market-1",
        recall_hit_ids=(f"recall_hit:{SHA_C}",),
        recall_score=Decimal("0.5"),
        dedup_group="market-1",
        selected_at=NOW,
        research_priority=ResearchPriority.CORE,
        selection_rationale=("new market",),
    )
    assert valid.record_id != valid.candidate_id


def test_blind_probability_estimate_cannot_carry_market_probability_or_identity() -> None:
    values = {
        **_env(f"probability_estimate:{SHA_A}"),
        "blind_candidate_id": f"blind_candidate:{SHA_A}",
        "estimate_stage": EstimateStage.BLIND,
        "model_type": "fixture",
        "p_event_yes_low": Decimal("0.2"),
        "p_event_yes_mid": Decimal("0.3"),
        "p_event_yes_high": Decimal("0.4"),
        "uncertainty_drivers": ("source timing",),
        "assumptions": ("official source is authoritative",),
        "model_version": "v1",
    }
    assert ProbabilityEstimate.model_validate(values).market_id is None
    with pytest.raises(ValidationError):
        ProbabilityEstimate.model_validate(
            {**values, "market_id": "market-secret", "p_market_yes_mid": Decimal("0.1")}
        )


def test_candidate_lifecycle_has_explicit_refresh_and_terminal_events() -> None:
    expected = {
        "RECALL_EXPIRED",
        "EVIDENCE_STALE",
        "RESEARCH_REFRESH_REQUIRED",
        "RULE_REVISION_INVALIDATED",
        "BOOK_REFRESH_REQUIRED",
        "MARKET_CLOSED",
        "RESOLVED",
        "SUPERSEDED",
        "ARCHIVED",
    }
    assert expected <= {item.value for item in CandidateEventType}


def test_schema_bundle_and_fingerprint_are_complete_and_stable() -> None:
    schemas = contract_schema_bundle()
    for required in (
        "MarketSnapshot",
        "OrderbookSnapshot",
        "RecallHit",
        "CandidateCard",
        "RuleContract",
        "BlindRuleView",
        "BlindResearchPacket",
        "ClaimEvidence",
        "ReviewDecision",
        "PredictionRecord",
    ):
        assert required in schemas
    fingerprint = contract_schema_fingerprint()
    assert len(fingerprint) == 64
    assert fingerprint == contract_schema_fingerprint()


def test_contract_golden_fingerprint_and_compatibility_matrix() -> None:
    golden = json.loads((FIXTURES / "p0_01_golden.json").read_text(encoding="utf-8"))
    assert golden["schema_version"] == ALPHA_CONTRACT_VERSION
    assert golden["contract_schema_sha256"] == contract_schema_fingerprint()
    assert golden["canonical_datetime"] == canonical_datetime(NOW)
    assert golden["canonical_decimal"] == json.loads(
        canonical_json({"value": Decimal("1.2300")})
    )["value"]
    actual = {
        version: compatibility_disposition(version).value
        for version in golden["compatibility"]
    }
    assert actual == golden["compatibility"]
