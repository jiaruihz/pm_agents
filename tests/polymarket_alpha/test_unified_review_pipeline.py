from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path

import pytest

from src.polymarket_alpha.contracts import (
    CandidateEventType,
    CandidateState,
    PositionState,
    ResearchResultEnvelope,
    MarketIdentity,
    MarketSnapshot,
    MarketStatus,
    canonical_json,
    stable_record_id,
)
from src.polymarket_alpha.change import detect_market_change
from src.polymarket_alpha.decision import RankConfig
from src.polymarket_alpha.pipeline import (
    MultiRecallScanRequest,
    MultiRecallScanner,
    ReviewPipelineBlocked,
    ScanRefreshRequest,
    persist_scan_candidates,
    start_blind_review,
    resume_blind_result,
    accept_formal_book,
    resume_market_result,
    plan_scan_refresh,
)
from src.polymarket_alpha.recall.book_anomaly import BookAnomalyRecallRequest
from src.polymarket_alpha.recall.book_anomaly import BookAnomalyRecallProvider
from src.polymarket_alpha.recall.controversy import (
    ControversyMarketMapping,
    ControversyRecaller,
)
from src.polymarket_alpha.recall.new_changed import NewChangedRecaller, PrebookRecallRequest
from src.polymarket_alpha.recall.registry import ProviderRegistry
from src.polymarket_alpha.recall.structural_metadata import StructuralMetadataRecaller
from src.polymarket_alpha.recall.wallet import WalletRecallProvider
from src.polymarket_alpha.recall.controversy import ControversyRecallRequest
from src.polymarket_alpha.recall.wallet import WalletRecallRequest
from src.polymarket_alpha.storage import AlphaRepository
from tests.polymarket_alpha.test_book_adapter_p0_05a import _artifact, _identity, _raw_book
from tests.polymarket_alpha.test_multi_recall_pipeline import (
    DESCRIPTORS,
    NOW,
    Outcome,
    _hit,
    _optional_request,
    _request,
    _scanner,
)
from tests.polymarket_alpha.test_research_handoff_pipeline import (
    _blind_submission,
    _market_submission,
)
from tests.polymarket_alpha.test_rule_gates_p0_07 import (
    _market,
    _request as _rule_request,
)
from tests.polymarket_alpha.test_controversy_recall_p0_06c import (
    _payload as _controversy_payload,
    _request as _controversy_request,
)
from tests.polymarket_alpha.test_wallet_recall_p0_06d import (
    _fresh_payload as _wallet_payload,
    _request as _wallet_request,
)


def _four_route_scan():
    return _scanner(
        Outcome(hits=(_hit(DESCRIPTORS[0], "a"),)),
        Outcome(hits=(_hit(DESCRIPTORS[1], "b"),)),
        Outcome(hits=(_hit(DESCRIPTORS[2], "c"),)),
        Outcome(hits=(_hit(DESCRIPTORS[3], "d"),)),
        Outcome(),
    ).scan(
        _request(
            controversy_request=_optional_request(ControversyRecallRequest),
            wallet_request=_optional_request(WalletRecallRequest),
        )
    )


def test_unified_four_route_handoff_book_rule_b_and_ledger(tmp_path: Path) -> None:
    repository = AlphaRepository(tmp_path / "alpha.db")
    repository.migrate()
    market = _market()
    repository.save_contract(market)
    scan = _four_route_scan()
    candidate = scan.aggregation.results[0].candidate
    assert len(scan.accepted_hits) == 4
    persisted = persist_scan_candidates(repository, outcome=scan)
    assert len(persisted) == 5

    blind = start_blind_review(
        repository=repository,
        candidate=candidate,
        recall_hits=scan.accepted_hits,
        compilation_request=_rule_request(
            run_id="unified-rule",
            compiled_at=NOW + timedelta(minutes=1),
        ).model_copy(update={"market_snapshot_id": market.record_id}),
        artifact_root=tmp_path,
        packet_locator="outbox/blind.packet.json",
        manifest_locator="outbox/blind.manifest.json",
        gate_evaluated_at=NOW + timedelta(minutes=2),
        packet_created_at=NOW + timedelta(minutes=3),
        handoff_created_at=NOW + timedelta(minutes=3, seconds=1),
    )
    assert blind.transitions[-1].to_state == CandidateState.BLIND_PACKET_FROZEN
    blind_bytes, blind_sources = _blind_submission(blind.blind_packet)
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox/blind-result.json").write_bytes(blind_bytes)
    blind_resumed = resume_blind_result(
        repository=repository,
        stage=blind,
        artifact_root=tmp_path,
        result_locator="inbox/blind-result.json",
        receipt_locator="receipts/blind.json",
        source_contents=blind_sources,
        imported_at=NOW + timedelta(hours=5, minutes=1),
        identity=_identity(),
        demand_requested_at=NOW + timedelta(hours=5, minutes=2),
        demand_valid_until=NOW + timedelta(hours=5, minutes=12),
        max_staleness_seconds=300,
        target_sizes=(Decimal("10"),),
    )
    assert blind_resumed.accepted is not None
    blind_retry = resume_blind_result(
        repository=repository,
        stage=blind,
        artifact_root=tmp_path,
        result_locator="inbox/blind-result.json",
        receipt_locator="receipts/blind.json",
        source_contents=blind_sources,
        imported_at=NOW + timedelta(hours=5, minutes=1),
        identity=_identity(),
        demand_requested_at=NOW + timedelta(hours=5, minutes=2),
        demand_valid_until=NOW + timedelta(hours=5, minutes=12),
        max_staleness_seconds=300,
        target_sizes=(Decimal("10"),),
    )
    assert blind_retry.accepted is not None
    assert blind_retry.accepted.demand == blind_resumed.accepted.demand

    response_at = NOW + timedelta(hours=5, minutes=3)
    current_book = _raw_book()
    current_book["timestamp"] = str(int(response_at.timestamp() * 1000))
    market_built = accept_formal_book(
        repository=repository,
        stage=blind_resumed.accepted,
        yes_artifact=_artifact("yes-token", response_at=response_at, raw_book=current_book),
        no_artifact=_artifact("no-token", response_at=response_at, raw_book=current_book),
        received_at=response_at + timedelta(seconds=1),
        artifact_root=tmp_path,
        packet_locator="outbox/market.packet.json",
        manifest_locator="outbox/market.manifest.json",
        packet_created_at=NOW + timedelta(hours=5, minutes=4),
        handoff_created_at=NOW + timedelta(hours=5, minutes=4, seconds=1),
    )
    assert market_built.accepted is not None
    market_retry = accept_formal_book(
        repository=repository,
        stage=blind_resumed.accepted,
        yes_artifact=_artifact("yes-token", response_at=response_at, raw_book=current_book),
        no_artifact=_artifact("no-token", response_at=response_at, raw_book=current_book),
        received_at=response_at + timedelta(seconds=1),
        artifact_root=tmp_path,
        packet_locator="outbox/market.packet.json",
        manifest_locator="outbox/market.manifest.json",
        packet_created_at=NOW + timedelta(hours=5, minutes=4),
        handoff_created_at=NOW + timedelta(hours=5, minutes=4, seconds=1),
    )
    assert market_retry.accepted is not None
    assert market_retry.accepted.market_packet == market_built.accepted.market_packet
    market_bytes, market_sources = _market_submission(market_built.accepted.market_packet)
    market_raw = json.loads(market_bytes)
    market_raw["probability_estimate"]["blind_candidate_id"] = (
        blind_resumed.accepted.result.probability_estimate.blind_candidate_id
    )
    market_raw["completed_at"] = (
        NOW + timedelta(hours=5, minutes=4, seconds=30)
    ).isoformat()
    market_bytes = canonical_json(ResearchResultEnvelope.model_validate(market_raw)).encode()
    (tmp_path / "inbox/market-result.json").write_bytes(market_bytes)
    completed = resume_market_result(
        repository=repository,
        stage=market_built.accepted,
        artifact_root=tmp_path,
        result_locator="inbox/market-result.json",
        receipt_locator="receipts/market.json",
        source_contents=market_sources,
        imported_at=NOW + timedelta(hours=5, minutes=5),
        gate_b_evaluated_at=NOW + timedelta(hours=5, minutes=6),
        decision_as_of=NOW + timedelta(hours=5, minutes=7),
        rank_config=RankConfig(
            version="unified-offline-v1",
            watch_threshold=Decimal("0.05"),
            simulate_threshold=Decimal("0.10"),
        ),
    )
    assert completed.final is not None
    assert completed.final.ranked is not None
    assert completed.final.ranked.decision.execution == "NO_ORDER"
    assert completed.final.ranked.prediction.position_state == PositionState.SIMULATED
    assert completed.final.lifecycle.current_state == CandidateState.SIMULATION_RECORDED
    assert repository.get_contract(completed.final.ranked.decision.record_id) is not None
    assert repository.get_contract(completed.final.ranked.prediction.record_id) is not None
    completed_retry = resume_market_result(
        repository=repository,
        stage=market_built.accepted,
        artifact_root=tmp_path,
        result_locator="inbox/market-result.json",
        receipt_locator="receipts/market.json",
        source_contents=market_sources,
        imported_at=NOW + timedelta(hours=5, minutes=5),
        gate_b_evaluated_at=NOW + timedelta(hours=5, minutes=6),
        decision_as_of=NOW + timedelta(hours=5, minutes=7),
        rank_config=RankConfig(
            version="unified-offline-v1",
            watch_threshold=Decimal("0.05"),
            simulate_threshold=Decimal("0.10"),
        ),
    )
    assert completed_retry.final is not None
    assert completed_retry.final.ranked == completed.final.ranked


def test_quarantined_blind_and_missing_book_cannot_advance(tmp_path: Path) -> None:
    repository = AlphaRepository(tmp_path / "alpha.db")
    repository.migrate()
    market = _market()
    repository.save_contract(market)
    scan = _four_route_scan()
    candidate = scan.aggregation.results[0].candidate
    persist_scan_candidates(repository, outcome=scan)
    blind = start_blind_review(
        repository=repository,
        candidate=candidate,
        recall_hits=scan.accepted_hits,
        compilation_request=_rule_request(
            run_id="unified-rule-failure",
            compiled_at=NOW + timedelta(minutes=1),
        ).model_copy(update={"market_snapshot_id": market.record_id}),
        artifact_root=tmp_path,
        packet_locator="outbox/blind.packet.json",
        manifest_locator="outbox/blind.manifest.json",
        gate_evaluated_at=NOW + timedelta(minutes=2),
        packet_created_at=NOW + timedelta(minutes=3),
        handoff_created_at=NOW + timedelta(minutes=3, seconds=1),
    )
    submitted, sources = _blind_submission(blind.blind_packet)
    (tmp_path / "inbox").mkdir()
    wrong = json.loads(submitted)
    wrong["packet_id"] = "blind_packet:wrong"
    (tmp_path / "inbox/bad.json").write_text(canonical_json(wrong))
    quarantined = resume_blind_result(
        repository=repository,
        stage=blind,
        artifact_root=tmp_path,
        result_locator="inbox/bad.json",
        receipt_locator="receipts/bad.json",
        source_contents=sources,
        imported_at=NOW + timedelta(hours=5, minutes=1),
        identity=_identity(),
        demand_requested_at=NOW + timedelta(hours=5, minutes=2),
        demand_valid_until=NOW + timedelta(hours=5, minutes=12),
        max_staleness_seconds=300,
        target_sizes=(Decimal("10"),),
    )
    assert quarantined.accepted is None

    (tmp_path / "inbox/good.json").write_bytes(submitted)
    accepted = resume_blind_result(
        repository=repository,
        stage=blind,
        artifact_root=tmp_path,
        result_locator="inbox/good.json",
        receipt_locator="receipts/good.json",
        source_contents=sources,
        imported_at=NOW + timedelta(hours=5, minutes=1),
        identity=_identity(),
        demand_requested_at=NOW + timedelta(hours=5, minutes=2),
        demand_valid_until=NOW + timedelta(hours=5, minutes=12),
        max_staleness_seconds=300,
        target_sizes=(Decimal("10"),),
    )
    assert accepted.accepted is not None
    missing_book = accept_formal_book(
        repository=repository,
        stage=accepted.accepted,
        yes_artifact=None,
        no_artifact=None,
        received_at=NOW + timedelta(hours=5, minutes=3),
        artifact_root=tmp_path,
        packet_locator="outbox/market.packet.json",
        manifest_locator="outbox/market.manifest.json",
        packet_created_at=NOW + timedelta(hours=5, minutes=4),
        handoff_created_at=NOW + timedelta(hours=5, minutes=4, seconds=1),
    )
    assert missing_book.accepted is None
    assert not (tmp_path / "outbox/market.packet.json").exists()


def test_second_scan_late_book_hit_becomes_append_only_refresh_event() -> None:
    first = _scanner(
        Outcome(hits=(_hit(DESCRIPTORS[0], "a"),)),
        Outcome(),
        Outcome(),
        Outcome(),
        Outcome(),
    ).scan(_request())
    frozen = first.aggregation.results[0].candidate.model_copy(
        update={"state": CandidateState.BLIND_PACKET_FROZEN}
    )
    second = _scanner(
        Outcome(),
        Outcome(),
        Outcome(),
        Outcome(),
        Outcome(hits=(_hit(DESCRIPTORS[4], "b"),)),
    ).scan(
        _request(
            enable_book_anomaly=True,
            book_anomaly_request=_optional_request(BookAnomalyRecallRequest),
            prior_candidates=(frozen,),
            prior_hits=first.accepted_hits,
            prior_projection_input_hashes={frozen.candidate_id: "a" * 64},
            merged_projection_input_hashes={frozen.candidate_id: "b" * 64},
        )
    )
    impact = second.aggregation.results[0].impact
    plan = plan_scan_refresh(
        ScanRefreshRequest(
            candidate=frozen,
            as_of=NOW,
            late_hit_impacts=(impact,),
        )
    )
    assert [item.event_type for item in plan.transitions] == [
        CandidateEventType.RESEARCH_REFRESH_REQUIRED
    ]
    assert plan.transitions[0].related_artifact_ids == impact.new_recall_hit_ids


def test_second_scan_persists_prior_and_new_hit_payloads_together(tmp_path: Path) -> None:
    repository = AlphaRepository(tmp_path / "alpha.db")
    repository.migrate()
    repository.save_contract(_market())
    first = _scanner(
        Outcome(hits=(_hit(DESCRIPTORS[0], "a"),)),
        Outcome(),
        Outcome(),
        Outcome(),
        Outcome(),
    ).scan(_request())
    persist_scan_candidates(repository, outcome=first)
    prior = first.aggregation.results[0].candidate
    second = _scanner(
        Outcome(),
        Outcome(),
        Outcome(),
        Outcome(),
        Outcome(hits=(_hit(DESCRIPTORS[4], "b"),)),
    ).scan(
        _request(
            enable_book_anomaly=True,
            book_anomaly_request=_optional_request(BookAnomalyRecallRequest),
            prior_candidates=(prior,),
            prior_hits=first.accepted_hits,
            prior_projection_input_hashes={prior.candidate_id: "a" * 64},
            merged_projection_input_hashes={prior.candidate_id: "b" * 64},
        )
    )
    persisted = persist_scan_candidates(repository, outcome=second)
    assert set(item.record_id for item in second.accepted_hits) == {
        _hit(DESCRIPTORS[0], "a").record_id,
        _hit(DESCRIPTORS[4], "b").record_id,
    }
    assert all(repository.get_contract(item) is not None for item in persisted)


def test_released_four_prebook_providers_run_together_without_book() -> None:
    as_of = NOW - timedelta(days=1, hours=1)
    previous_at = as_of - timedelta(hours=2)
    current_at = previous_at + timedelta(minutes=1)
    identity = MarketIdentity(
        event_id="event-1",
        market_id="market-1",
        condition_id="condition-1",
        yes_token_id="yes-token",
        no_token_id="no-token",
    )
    previous = MarketSnapshot(
        record_id=stable_record_id("market_snapshot", "market-1", "released-old"),
        run_id="released-scan",
        created_at=previous_at,
        source="fixture_catalog",
        source_version="v1",
        identity=identity,
        title="Original title",
        question="Will the fixture occur?",
        status=MarketStatus.ACTIVE,
        end_at=as_of + timedelta(days=5),
        rules_raw="Settlement uses the original official result.",
        source_observed_at=previous_at,
        ingested_at=previous_at + timedelta(seconds=1),
    )
    current = MarketSnapshot(
        record_id=stable_record_id("market_snapshot", "market-1", "released-new"),
        run_id="released-scan",
        created_at=current_at,
        source="fixture_catalog",
        source_version="v1",
        identity=identity,
        title="Revised title",
        question="Will the fixture occur?",
        status=MarketStatus.ACTIVE,
        end_at=as_of + timedelta(days=5),
        rules_raw="Settlement uses the revised official result.",
        source_observed_at=current_at,
        ingested_at=current_at + timedelta(seconds=1),
    )
    event = detect_market_change(
        previous,
        current,
        run_id="released-scan",
        detected_at=current_at + timedelta(minutes=1),
    )
    assert event is not None

    controversy_payload = _controversy_payload("corpus_v1.json")
    controversy = _controversy_request(
        controversy_payload,
        as_of=as_of,
        run_id="released-scan",
        mapping=(
            ControversyMarketMapping(market_ref="fed-rate-cut", market_id="market-1"),
            ControversyMarketMapping(
                market_ref="nyc-mayor-special", market_id="market-1"
            ),
        ),
    )
    wallet_payload = _wallet_payload()
    wallet_payload["run_id"] = "released-scan"
    for mapping in wallet_payload["token_markets"]:
        mapping["market_id"] = "market-1"
    wallet = _wallet_request(wallet_payload)

    providers = (
        NewChangedRecaller(),
        StructuralMetadataRecaller(),
        ControversyRecaller(),
        WalletRecallProvider(),
        BookAnomalyRecallProvider(),
    )
    descriptors = tuple(
        item.descriptor() if callable(item.descriptor) else item.descriptor
        for item in providers
    )
    outcome = MultiRecallScanner(ProviderRegistry(descriptors)).scan(
        MultiRecallScanRequest(
            run_id="released-scan",
            created_at=as_of,
            as_of=as_of,
            prebook_request=PrebookRecallRequest(
                run_id="released-scan",
                as_of=as_of,
                events=(event,),
                snapshots=(previous, current),
            ),
            controversy_request=controversy,
            wallet_request=wallet,
        )
    )
    statuses = {item.provider_id: item.status.value for item in outcome.provider_receipts}
    assert statuses == {
        "new_changed": "SUCCESS",
        "structural_metadata": "SUCCESS",
        "controversy": "SUCCESS",
        "specialist_wallet": "SUCCESS",
        "book_anomaly": "SKIPPED",
    }
    candidate = outcome.aggregation.results[0].candidate
    assert candidate.market_id == "market-1"
    assert len(candidate.recall_hit_ids) >= 4
    assert all(hit.recaller.value != "BOOK_ANOMALY" for hit in outcome.accepted_hits)


def test_rule_a_cannot_compile_against_a_different_snapshot_hash(tmp_path: Path) -> None:
    repository = AlphaRepository(tmp_path / "alpha.db")
    repository.migrate()
    market = _market()
    repository.save_contract(market)
    scan = _four_route_scan()
    persist_scan_candidates(repository, outcome=scan)
    with pytest.raises(ReviewPipelineBlocked, match="snapshot market/rule binding"):
        start_blind_review(
            repository=repository,
            candidate=scan.aggregation.results[0].candidate,
            recall_hits=scan.accepted_hits,
            compilation_request=_rule_request(
                expected_rule_hash="f" * 64,
                run_id="mismatched-rule",
                compiled_at=NOW + timedelta(minutes=1),
            ).model_copy(update={"market_snapshot_id": market.record_id}),
            artifact_root=tmp_path,
            packet_locator="outbox/blind.packet.json",
            manifest_locator="outbox/blind.manifest.json",
            gate_evaluated_at=NOW + timedelta(minutes=2),
            packet_created_at=NOW + timedelta(minutes=3),
            handoff_created_at=NOW + timedelta(minutes=3, seconds=1),
        )
