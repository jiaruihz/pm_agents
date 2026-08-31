"""P0-09B deterministic offline decision ledger evidence."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.polymarket_alpha.contracts import (
    BookLeg, BookLevel, CandidateCard, CandidateState, CaptureScope, ClaimEvidence,
    EstimateStage, EvidenceOrigin, EvidenceSupport, HashScope, MarketIdentity,
    MarketResearchPacket, MarketSnapshot, MarketStatus, OrderbookSnapshot, PacketStage, ProbabilityEstimate,
    RecallHit, RecallerType, Replayability, ResearchImportReason,
    ResearchImportReceipt, ResearchImportStatus, ResearchPriority,
    ResearchResultEnvelope, ReviewAction, RuleContract, RuleGate, SourceArtifact,
    SourceTier, TargetDepthMetrics, stable_record_id,
)
from src.polymarket_alpha.decision import (
    DecisionLedgerError, RankConfig, build_ranked_ledger, persist_ranked_ledger, watchlist,
)
from src.polymarket_alpha.rules.models import RuleGateDecision, RuleGateStage
from src.polymarket_alpha.security import audit_source_tree
from src.polymarket_alpha.storage import AlphaRepository, ContractConflictError


UTC = timezone.utc
NOW = datetime(2026, 8, 27, 15, 0, tzinfo=UTC)
SHA = "a" * 64


def _rank_config(**updates: object) -> RankConfig:
    values: dict[str, object] = {
        "version": "fixture-v1",
        "fee_slippage_cost_policy_id": "fixture_fee_free",
        "fee_slippage_cost_policy_version": "v1",
        "fee_rate": Decimal("0"),
        "slippage_buffer": Decimal("0"),
    }
    values.update(updates)
    return RankConfig(**values)


def _env(record_id: str, *, run: str = "decision-run", source: str = "fixture", at: datetime = NOW) -> dict[str, object]:
    return {"record_id": record_id, "run_id": run, "created_at": at, "source": source,
            "source_version": "fixture-v1", "provenance": (), "extensions": {}}


def _identity() -> MarketIdentity:
    return MarketIdentity(event_id="event-1", market_id="market-1", condition_id="condition-1", yes_token_id="yes-1", no_token_id="no-1")


def _candidate() -> CandidateCard:
    candidate_id = stable_record_id("candidate", "market-1", "dedup")
    selected_at = NOW - timedelta(hours=1)
    return CandidateCard(**_env(stable_record_id("candidate_card", candidate_id, "rankable"), at=selected_at), candidate_id=candidate_id,
        market_id="market-1", recall_hit_ids=("recall_hit:fixture",), recall_score=Decimal("90"), dedup_group="dedup",
        selected_at=selected_at, state=CandidateState.RULE_B_PASSED,
        research_priority=ResearchPriority.CORE, selection_rationale=("fixture",))


def _contract() -> RuleContract:
    return RuleContract(**_env(stable_record_id("rule_contract", "market-1", SHA), at=NOW - timedelta(minutes=10)), market_id="market-1", rule_hash=SHA,
        contract_revision_id="rule-revision-1", subject_entity="Agency", entity_match_rule="named agency",
        yes_trigger="final bulletin", timezone="UTC", resolution_sources=("agency",), source_precedence=("agency",),
        initial_or_final="FINAL", clarity_score=Decimal("0.90"), rule_gate=RuleGate.PASS, parser_version="fixture-v1")


def _gates(contract: RuleContract) -> tuple[RuleGateDecision, RuleGateDecision]:
    gate_a_id = stable_record_id("rule_gate_decision", "a", contract.record_id)
    common = dict(market_id=contract.market_id, rule_contract_id=contract.record_id, rule_hash=contract.rule_hash,
        contract_revision_id=contract.contract_revision_id, compiler_version=contract.source_version,
        input_artifact_ids=(contract.record_id,))
    gate_a_at = NOW - timedelta(minutes=6)
    gate_b_at = NOW - timedelta(seconds=30)
    gate_a = RuleGateDecision(**_env(gate_a_id, at=gate_a_at), stage=RuleGateStage.A,
        decision="PASS", reasons=("clear",), evaluated_at=gate_a_at, **common)
    gate_b = RuleGateDecision(**_env(stable_record_id("rule_gate_decision", "b", contract.record_id), at=gate_b_at), stage=RuleGateStage.B,
        decision="PASS", reasons=("bound",), gate_a_decision_id=gate_a_id,
        evaluated_at=gate_b_at, **common)
    return gate_a, gate_b


def _artifact_and_claim(run: str, *, confidence: Decimal = Decimal("0.95")) -> tuple[SourceArtifact, ClaimEvidence]:
    payload = b"A final official bulletin confirms the event."
    import hashlib
    digest = hashlib.sha256(payload).hexdigest()
    artifact_id = stable_record_id("source_artifact", run, "official")
    artifact = SourceArtifact(**_env(artifact_id, run=run, source="source_capture"), artifact_id=artifact_id,
        source_name="Agency", source_url_or_source_id="https://agency.example/bulletin", media_type="text/plain",
        captured_at=NOW - timedelta(minutes=10), effective_as_of=NOW - timedelta(minutes=11),
        capture_scope=CaptureScope.EXCERPT_ONLY, hash_scope=HashScope.CLAIM_EXCERPT, content_sha256=digest,
        content_length_bytes=len(payload), artifact_locator="artifact://official", replayability=Replayability.EXCERPT)
    evidence_id = stable_record_id("evidence", run, "official")
    claim = ClaimEvidence(**_env(evidence_id, run=run, source="claim_importer"), evidence_id=evidence_id,
        claim="A final official bulletin confirms the event.", supports_yes_or_no=EvidenceSupport.YES, source_tier=SourceTier.T0,
        source_name="Agency", source_url_or_source_id=artifact.source_url_or_source_id, accessed_at=NOW - timedelta(minutes=9),
        effective_as_of=artifact.effective_as_of, primary_or_secondary="PRIMARY", quotation_or_paraphrase_location="p1",
        confidence=confidence, origin=EvidenceOrigin.PRIMARY_SOURCE, source_artifact_id=artifact_id,
        capture_scope=artifact.capture_scope, hash_scope=artifact.hash_scope, content_sha256=artifact.content_sha256,
        excerpt_context=payload.decode(), replayability=artifact.replayability)
    return artifact, claim


def _result(stage: PacketStage, packet_id: str, packet_hash: str, *, market: bool, blind_id: str,
            completed_at: datetime, imported_at: datetime,
            evidence_confidence: Decimal = Decimal("0.95")) -> tuple[ResearchResultEnvelope, ResearchImportReceipt]:
    run = f"research-{stage.value.lower()}"
    artifact, claim = _artifact_and_claim(run, confidence=evidence_confidence)
    estimate_id = stable_record_id("probability_estimate", run)
    estimate = ProbabilityEstimate(**_env(estimate_id, run=run, source="research"), market_id="market-1" if market else None,
        blind_candidate_id=blind_id, estimate_stage=EstimateStage.FINAL if market else EstimateStage.BLIND,
        model_type="fixture", p_event_yes_low=Decimal("0.74"), p_event_yes_mid=Decimal("0.80"), p_event_yes_high=Decimal("0.86"),
        p_market_yes_low=Decimal("0.50") if market else None, p_market_yes_mid=Decimal("0.55") if market else None,
        p_market_yes_high=Decimal("0.60") if market else None, uncertainty_drivers=("fixture",), assumptions=("fixture",), model_version="v1")
    result_id = stable_record_id("research_result", packet_id, run)
    result = ResearchResultEnvelope(**_env(result_id, run=run, source="research_result_importer"), result_id=result_id,
        packet_stage=stage, packet_id=packet_id, packet_sha256=packet_hash, probability_estimate=estimate,
        evidence=(claim,), source_artifacts=(artifact,), completed_at=completed_at, producer="fixture", producer_version="v1")
    receipt_id = stable_record_id("research_import_receipt", result_id)
    receipt = ResearchImportReceipt(**_env(receipt_id, run=run, source="research_result_importer"), import_receipt_id=receipt_id,
        packet_stage=stage, packet_id=packet_id, packet_sha256=packet_hash, submitted_artifact_id=stable_record_id("source_artifact", run, "submitted"),
        submitted_result_sha256=SHA, status=ResearchImportStatus.ACCEPTED, reasons=(ResearchImportReason.ACCEPTED,),
        imported_at=imported_at, importer_version="v1", accepted_result_id=result_id, accepted_result_sha256=result.canonical_sha256)
    return result, receipt


def _book() -> OrderbookSnapshot:
    identity = _identity()
    leg_yes = BookLeg(token_id="yes-1", bids=(BookLevel(price=Decimal("0.60"), size=Decimal("100")),), asks=(BookLevel(price=Decimal("0.65"), size=Decimal("100")),))
    leg_no = BookLeg(token_id="no-1", bids=(BookLevel(price=Decimal("0.30"), size=Decimal("100")),), asks=(BookLevel(price=Decimal("0.35"), size=Decimal("100")),))
    yes_depth = TargetDepthMetrics(target_size=Decimal("10"), buy_vwap=Decimal("0.65"),
        sell_vwap=Decimal("0.60"), buy_insufficient_depth=False, sell_insufficient_depth=False)
    no_depth = TargetDepthMetrics(target_size=Decimal("10"), buy_vwap=Decimal("0.35"),
        sell_vwap=Decimal("0.30"), buy_insufficient_depth=False, sell_insufficient_depth=False)
    record_id = stable_record_id("orderbook_snapshot", "market-1", NOW)
    return OrderbookSnapshot(**_env(record_id, source="book_adapter", at=NOW - timedelta(minutes=4)), identity=identity, capture_group_id="capture-1",
        captured_at=NOW - timedelta(minutes=4), source_observed_at=NOW - timedelta(minutes=4), yes_leg=leg_yes, no_leg=leg_no,
        yes_depth=(yes_depth,), no_depth=(no_depth,), stale=False,
        raw_artifact_ids=("owner_book:yes", "owner_book:no"))


def _snapshot() -> MarketSnapshot:
    record_id = stable_record_id("gamma_market_snapshot", "market-1", NOW)
    return MarketSnapshot(**_env(record_id, source="gamma_catalog"), identity=_identity(), title="Fixture market",
        question="Will the agency issue a final bulletin?", status=MarketStatus.ACTIVE, rules_raw="Final bulletin required.",
        source_observed_at=NOW - timedelta(hours=1), ingested_at=NOW - timedelta(hours=1))


def _inputs() -> dict[str, object]:
    candidate, contract = _candidate(), _contract()
    gate_a, gate_b = _gates(contract)
    blind_packet_id = stable_record_id("blind_packet", "fixture")
    blind_result, blind_receipt = _result(PacketStage.BLIND, blind_packet_id, SHA, market=False,
        blind_id="blind_candidate:" + "b" * 64, completed_at=NOW - timedelta(minutes=5),
        imported_at=NOW - timedelta(minutes=4, seconds=30),
        evidence_confidence=Decimal("0.25"))
    book = _book()
    packet_id = stable_record_id("market_packet", "fixture")
    packet = MarketResearchPacket(**_env(packet_id, source="market_packet_builder", at=NOW - timedelta(minutes=3)), candidate_id=candidate.candidate_id,
        market_id="market-1", blind_packet_id=blind_packet_id, blind_result_id=blind_result.result_id,
        rule_contract=contract, blind_evidence=blind_result.evidence, orderbook=book)
    market_result, market_receipt = _result(PacketStage.MARKET_AWARE, packet_id, packet.canonical_sha256, market=True,
        blind_id=blind_result.probability_estimate.blind_candidate_id,
        completed_at=NOW - timedelta(minutes=2), imported_at=NOW - timedelta(minutes=1))
    return dict(candidate=candidate, contract=contract, gate_a=gate_a, gate_b=gate_b, blind_result=blind_result,
        blind_receipt=blind_receipt, market_packet=packet, market_result=market_result, market_receipt=market_receipt,
        book=book, config=_rank_config(version="golden-v1", simulate_threshold=Decimal("0.60")), as_of=NOW, run_id="decision-run")


def test_rank_golden_and_replay_are_deterministic() -> None:
    inputs = _inputs()
    first = build_ranked_ledger(**inputs)
    second = build_ranked_ledger(**inputs)
    assert first == second
    assert first.score == Decimal("0.6265")
    assert first.score_breakdown == {"recall": Decimal("0.180"), "evidence": Decimal("0.1900"), "rule": Decimal("0.2250"), "edge": Decimal("0.0315")}
    assert inputs["blind_result"].evidence[0].confidence == Decimal("0.25")
    assert inputs["market_result"].evidence[0].confidence == Decimal("0.95")
    assert first.decision.action == ReviewAction.SIMULATE
    assert first.decision.execution == "NO_ORDER"
    assert first.decision.target_size == Decimal("10")
    assert first.prediction.position_state.value == "SIMULATED"


def test_rank_config_requires_an_explicit_cost_policy() -> None:
    with pytest.raises(ValueError, match="fee_slippage_cost_policy"):
        RankConfig(version="implicit-cost-is-forbidden")


def test_mismatch_and_stale_inputs_fail_closed() -> None:
    inputs = _inputs()
    inputs["market_receipt"] = inputs["blind_receipt"]
    with pytest.raises(DecisionLedgerError, match="receipt packet binding"):
        build_ranked_ledger(**inputs)
    inputs = _inputs()
    inputs["as_of"] = NOW + timedelta(minutes=6)
    with pytest.raises(DecisionLedgerError, match="orderbook is stale"):
        build_ranked_ledger(**inputs)

    inputs = _inputs()
    inputs["as_of"] = inputs["book"].source_observed_at + timedelta(
        seconds=inputs["config"].max_book_age_seconds
    )
    with pytest.raises(DecisionLedgerError, match="orderbook is stale"):
        build_ranked_ledger(**inputs)

    inputs = _inputs()
    inputs["book"] = inputs["book"].model_copy(update={"extensions": {"tampered": True}})
    with pytest.raises(DecisionLedgerError, match="orderbook content"):
        build_ranked_ledger(**inputs)

    inputs = _inputs()
    inputs["config"] = _rank_config(version="missing-depth", simulation_target_size=Decimal("25"))
    with pytest.raises(DecisionLedgerError, match="configured paired target depth"):
        build_ranked_ledger(**inputs)


def test_protocol_chronology_and_attempt_identity_are_enforced() -> None:
    inputs = _inputs()
    inputs["gate_b"] = inputs["gate_b"].model_copy(
        update={"evaluated_at": NOW - timedelta(minutes=2)}
    )
    with pytest.raises(DecisionLedgerError, match="chronology"):
        build_ranked_ledger(**inputs)

    first_inputs = _inputs()
    first = build_ranked_ledger(**first_inputs)
    later_inputs = _inputs()
    later_inputs["run_id"] = "decision-run-2"
    later = build_ranked_ledger(**later_inputs)
    assert first.decision.record_id != later.decision.record_id
    assert first.prediction.record_id != later.prediction.record_id


def test_watchlist_and_idempotent_repository_replay(tmp_path) -> None:
    inputs = _inputs()
    inputs["config"] = _rank_config(version="watch-v1", simulate_threshold=Decimal("0.90"))
    outcome = build_ranked_ledger(**inputs)
    assert outcome.decision.action == ReviewAction.WATCH
    assert outcome.prediction.position_state.value == "NO_POSITION"
    assert watchlist((outcome.decision,)) == (outcome.decision,)
    repo = AlphaRepository(tmp_path / "alpha.db")
    # The pre-existing storage schema requires catalog/packet/result projections.
    # This proves the narrow ledger service persists only after its bound parents.
    for item in (_snapshot(), inputs["market_packet"].rule_contract, inputs["book"], inputs["market_packet"], inputs["market_result"]):
        repo.save_contract(item)
    assert persist_ranked_ledger(repo, outcome) == persist_ranked_ledger(repo, outcome)
    assert persist_ranked_ledger(repo, outcome).cross_record_atomic is True
    with pytest.raises(ContractConflictError):
        repo.save_contract(outcome.decision.model_copy(update={"extensions": {"changed": "yes"}}))


def test_atomic_ledger_write_rolls_back_new_decision_when_prediction_conflicts(tmp_path) -> None:
    inputs = _inputs()
    outcome = build_ranked_ledger(**inputs)
    repo = AlphaRepository(tmp_path / "alpha.db")
    for item in (_snapshot(), inputs["market_packet"].rule_contract, inputs["book"],
                 inputs["market_packet"], inputs["market_result"]):
        repo.save_contract(item)
    persist_ranked_ledger(repo, outcome)

    new_decision = outcome.decision.model_copy(
        update={"record_id": stable_record_id("review_decision", "rollback-probe")}
    )
    conflicting_prediction = outcome.prediction.model_copy(
        update={"decision_id": new_decision.record_id}
    )
    with pytest.raises(ContractConflictError):
        repo.save_contracts_atomic((new_decision, conflicting_prediction))
    assert repo.get_contract(new_decision.record_id) is None


def test_owned_source_has_no_forbidden_capabilities() -> None:
    assert audit_source_tree(Path("src/polymarket_alpha/decision")).passed
