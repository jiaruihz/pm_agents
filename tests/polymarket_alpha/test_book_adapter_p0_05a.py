from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError
import pytest

from src.platform.market_data.capture_contract import materialize_orderbook_capture
from src.polymarket_alpha.books import (
    FrozenOwnerBookArtifact,
    build_formal_review_demand,
    build_owner_capture_demands,
    build_sensing_demand,
    normalize_paired_owner_books,
)
from src.polymarket_alpha.contracts import (
    BookCapturePurpose,
    BookCaptureStatus,
    CaptureScope,
    ClaimEvidence,
    EstimateStage,
    EvidenceOrigin,
    EvidenceSupport,
    HashScope,
    MarketChangeEvent,
    MarketChangeType,
    MarketIdentity,
    MarketSnapshot,
    MarketStatus,
    PacketStage,
    ProbabilityEstimate,
    Replayability,
    ResearchImportReason,
    ResearchImportReceipt,
    ResearchImportStatus,
    ResearchResultEnvelope,
    SourceArtifact,
    SourceTier,
    stable_record_id,
)
from src.polymarket_alpha.security import audit_source_tree
from src.polymarket_alpha.storage import AlphaRepository


UTC = timezone.utc
NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64


def _env(record_id: str, *, run_id: str = "book-run", source: str = "fixture") -> dict[str, object]:
    return {
        "record_id": record_id,
        "run_id": run_id,
        "created_at": NOW,
        "source": source,
        "source_version": "fixture-v1",
        "provenance": (),
        "extensions": {},
    }


def _identity() -> MarketIdentity:
    return MarketIdentity(
        event_id="event-1",
        market_id="market-1",
        condition_id="condition-1",
        yes_token_id="yes-token",
        no_token_id="no-token",
    )


def _change() -> MarketChangeEvent:
    record_id = stable_record_id("market_change", "market-1", SHA_A, SHA_B, NOW)
    return MarketChangeEvent(
        **_env(record_id, source="market_change_detector"),
        change_event_id=record_id,
        market_id="market-1",
        previous_snapshot_id=f"gamma_market_snapshot:{SHA_A}",
        current_snapshot_id=f"gamma_market_snapshot:{SHA_B}",
        previous_snapshot_sha256=SHA_A,
        current_snapshot_sha256=SHA_B,
        previous_status=MarketStatus.ACTIVE,
        current_status=MarketStatus.ACTIVE,
        previous_rule_hash=SHA_A,
        current_rule_hash=SHA_A,
        change_types=(MarketChangeType.METADATA_CHANGED,),
        changed_fields=("question",),
        effective_at=NOW,
        detected_at=NOW,
    )


def _sensing(*, max_staleness_seconds: int = 30):
    return build_sensing_demand(
        change_event=_change(),
        identity=_identity(),
        requested_at=NOW,
        valid_until=NOW + timedelta(minutes=5),
        max_staleness_seconds=max_staleness_seconds,
        target_sizes=(Decimal("10"), Decimal("100")),
        run_id="book-run",
    )


def _raw_book(*, crossed: bool = False, one_sided: bool = False) -> dict[str, object]:
    return {
        "timestamp": "1787832000000",
        "hash": "exchange-hash",
        "bids": [] if one_sided else [{"price": "0.60" if crossed else "0.40", "size": "25"}],
        "asks": [{"price": "0.50", "size": "20"}, {"price": "0.55", "size": "100"}],
    }


def _artifact(
    token_id: str,
    *,
    batch: str = "capture-batch-1",
    response_at: datetime = NOW + timedelta(seconds=1),
    raw_book: dict[str, object] | None = None,
) -> FrozenOwnerBookArtifact:
    raw = raw_book or _raw_book()
    capture = materialize_orderbook_capture(
        token_id=token_id,
        raw_book=raw,
        request_started_at_utc=(response_at - timedelta(milliseconds=100)).isoformat().replace("+00:00", "Z"),
        response_received_at_utc=response_at.isoformat().replace("+00:00", "Z"),
        parsed_at_utc=(response_at + timedelta(milliseconds=1)).isoformat().replace("+00:00", "Z"),
        request_batch_capture_id=batch,
    )
    return FrozenOwnerBookArtifact(
        token_id=token_id,
        raw_book=raw,
        capture=capture,
        raw_artifact_id=f"owner_book_artifact:{capture['book_capture_id']}",
    )


def _blind_result_and_receipt() -> tuple[ResearchResultEnvelope, ResearchImportReceipt]:
    run_id = f"research_run:{SHA_A}"
    artifact = SourceArtifact(
        **_env(f"source_artifact:{SHA_B}", run_id=run_id, source="source_capture"),
        artifact_id=f"source_artifact:{SHA_B}",
        source_name="Official Agency",
        source_url_or_source_id="https://agency.example/final",
        media_type="text/plain",
        captured_at=NOW,
        effective_as_of=NOW - timedelta(minutes=1),
        capture_scope=CaptureScope.EXCERPT_ONLY,
        hash_scope=HashScope.CLAIM_EXCERPT,
        content_sha256=SHA_B,
        content_length_bytes=20,
        artifact_locator="artifact://official-final",
        replayability=Replayability.EXCERPT,
    )
    claim = ClaimEvidence(
        **_env(f"evidence:{SHA_C}", run_id=run_id, source="claim_importer"),
        evidence_id=f"evidence:{SHA_C}",
        claim="The official agency issued a final bulletin.",
        supports_yes_or_no=EvidenceSupport.YES,
        source_tier=SourceTier.T0,
        source_name="Official Agency",
        source_url_or_source_id=artifact.source_url_or_source_id,
        accessed_at=NOW,
        effective_as_of=NOW - timedelta(minutes=1),
        primary_or_secondary="PRIMARY",
        quotation_or_paraphrase_location="paragraph 1",
        confidence=Decimal("0.9"),
        origin=EvidenceOrigin.PRIMARY_SOURCE,
        source_artifact_id=artifact.artifact_id,
        capture_scope=artifact.capture_scope,
        hash_scope=artifact.hash_scope,
        content_sha256=artifact.content_sha256,
        excerpt_context="The official agency issued a final bulletin.",
        replayability=artifact.replayability,
    )
    estimate = ProbabilityEstimate(
        **_env(f"probability_estimate:{SHA_D}", run_id=run_id, source="manual_research"),
        blind_candidate_id=f"blind_candidate:{SHA_E}",
        estimate_stage=EstimateStage.BLIND,
        model_type="manual structured research",
        p_event_yes_low=Decimal("0.2"),
        p_event_yes_mid=Decimal("0.3"),
        p_event_yes_high=Decimal("0.4"),
        uncertainty_drivers=("source timing",),
        assumptions=("official source is authoritative",),
        model_version="fixture-v1",
    )
    result_id = stable_record_id("research_result", f"blind_packet:{SHA_F}", SHA_A)
    result = ResearchResultEnvelope(
        **_env(result_id, run_id=run_id, source="research_result_importer"),
        result_id=result_id,
        packet_stage=PacketStage.BLIND,
        packet_id=f"blind_packet:{SHA_F}",
        packet_sha256=SHA_F,
        probability_estimate=estimate,
        evidence=(claim,),
        source_artifacts=(artifact,),
        completed_at=NOW,
        producer="manual research provider",
        producer_version="fixture-v1",
    )
    receipt_id = stable_record_id("research_import_receipt", result.result_id, result.canonical_sha256)
    receipt = ResearchImportReceipt(
        **_env(receipt_id, run_id=run_id, source="research_result_importer"),
        import_receipt_id=receipt_id,
        packet_stage=PacketStage.BLIND,
        packet_id=result.packet_id,
        packet_sha256=result.packet_sha256,
        submitted_artifact_id=f"source_artifact:{SHA_A}",
        submitted_result_sha256=SHA_A,
        status=ResearchImportStatus.ACCEPTED,
        reasons=(ResearchImportReason.ACCEPTED,),
        imported_at=NOW,
        importer_version="fixture-v1",
        accepted_result_id=result.result_id,
        accepted_result_sha256=result.canonical_sha256,
    )
    return result, receipt


def test_sensing_demand_maps_to_exactly_two_existing_owner_declarations() -> None:
    demand = _sensing()
    retry = _sensing()
    assert retry == demand
    assert demand.purpose == BookCapturePurpose.SENSING
    bundle = build_owner_capture_demands(demand)
    assert bundle.alpha_demand == demand
    assert {row.token_id for row in bundle.owner_demands} == {"yes-token", "no-token"}
    assert {row.priority for row in bundle.owner_demands} == {"P1"}
    assert {row.desired_transport for row in bundle.owner_demands} == {"REST_WS"}
    assert all(row.trigger_event_id == demand.demand_id for row in bundle.owner_demands)


def test_owner_mapping_fails_closed_without_condition_identity() -> None:
    demand = _sensing().model_copy(
        update={"identity": _identity().model_copy(update={"condition_id": None})}
    )
    with pytest.raises(ValueError, match="condition_id"):
        build_owner_capture_demands(demand)


def test_formal_review_demand_requires_the_exact_accepted_blind_result() -> None:
    result, receipt = _blind_result_and_receipt()
    demand = build_formal_review_demand(
        blind_result=result,
        import_receipt=receipt,
        identity=_identity(),
        requested_at=NOW,
        valid_until=NOW + timedelta(minutes=2),
        max_staleness_seconds=10,
        target_sizes=(Decimal("10"),),
        run_id="formal-run",
    )
    assert demand.purpose == BookCapturePurpose.FORMAL_REVIEW
    assert demand.blind_result_id == result.result_id
    later_run = build_formal_review_demand(
        blind_result=result,
        import_receipt=receipt,
        identity=_identity(),
        requested_at=NOW,
        valid_until=NOW + timedelta(minutes=2),
        max_staleness_seconds=10,
        target_sizes=(Decimal("10"),),
        run_id="formal-run-2",
    )
    assert later_run.record_id != demand.record_id
    bad_receipt = ResearchImportReceipt.model_validate(
        {
            **receipt.model_dump(),
            "accepted_result_sha256": SHA_A,
        }
    )
    with pytest.raises(ValueError, match="result bytes"):
        build_formal_review_demand(
            blind_result=result,
            import_receipt=bad_receipt,
            identity=_identity(),
            requested_at=NOW,
            valid_until=NOW + timedelta(minutes=2),
            max_staleness_seconds=10,
            target_sizes=(Decimal("10"),),
            run_id="formal-run",
        )


def test_paired_owner_artifacts_normalize_sort_depth_and_receipt() -> None:
    yes, no = _artifact("yes-token"), _artifact("no-token")
    outcome = normalize_paired_owner_books(
        demand=_sensing(),
        yes_artifact=yes,
        no_artifact=no,
        received_at=NOW + timedelta(seconds=2),
    )
    assert outcome.snapshot is not None
    assert outcome.receipt.status == BookCaptureStatus.ACCEPTED
    assert outcome.snapshot.yes_leg.bids[0].price == Decimal("0.4")
    assert outcome.snapshot.yes_leg.asks[0].price == Decimal("0.5")
    assert outcome.snapshot.yes_depth[0].buy_vwap == Decimal("0.5")
    assert outcome.snapshot.yes_depth[1].buy_insufficient_depth is False
    assert "TARGET_DEPTH_INSUFFICIENT" in outcome.snapshot.quality_flags
    assert outcome.receipt.orderbook_snapshot_sha256 == outcome.snapshot.canonical_sha256

    different_demand = normalize_paired_owner_books(
        demand=_sensing(max_staleness_seconds=31),
        yes_artifact=yes,
        no_artifact=no,
        received_at=NOW + timedelta(seconds=2),
    )
    assert different_demand.snapshot is not None
    assert different_demand.snapshot.record_id != outcome.snapshot.record_id

    alternate_lineage = normalize_paired_owner_books(
        demand=_sensing(),
        yes_artifact=yes.model_copy(update={"raw_artifact_id": "owner_book_artifact:alternate"}),
        no_artifact=no,
        received_at=NOW + timedelta(seconds=2),
    )
    assert alternate_lineage.snapshot is not None
    assert alternate_lineage.snapshot.record_id != outcome.snapshot.record_id


def test_missing_mismatched_expired_and_stale_routes_are_typed() -> None:
    demand = _sensing(max_staleness_seconds=5)
    yes = _artifact("yes-token")
    no = _artifact("no-token")
    missing = normalize_paired_owner_books(
        demand=demand, yes_artifact=yes, no_artifact=None, received_at=NOW + timedelta(seconds=2)
    )
    assert missing.snapshot is None
    assert missing.receipt.status == BookCaptureStatus.SKIPPED

    mismatch = normalize_paired_owner_books(
        demand=demand,
        yes_artifact=yes,
        no_artifact=_artifact("no-token", batch="other-batch"),
        received_at=NOW + timedelta(seconds=2),
    )
    assert mismatch.receipt.status == BookCaptureStatus.FAILED

    stale = normalize_paired_owner_books(
        demand=demand,
        yes_artifact=yes,
        no_artifact=no,
        received_at=NOW + timedelta(seconds=7),
    )
    assert stale.snapshot is not None and stale.snapshot.stale
    assert stale.receipt.status == BookCaptureStatus.STALE

    expired = normalize_paired_owner_books(
        demand=demand,
        yes_artifact=yes,
        no_artifact=no,
        received_at=demand.valid_until,
    )
    assert expired.snapshot is None
    assert expired.receipt.status == BookCaptureStatus.EXPIRED


def test_one_sided_crossed_insufficient_and_float_levels_are_explicit() -> None:
    one_sided = normalize_paired_owner_books(
        demand=_sensing(),
        yes_artifact=_artifact("yes-token", raw_book=_raw_book(one_sided=True)),
        no_artifact=_artifact("no-token", raw_book=_raw_book(crossed=True)),
        received_at=NOW + timedelta(seconds=2),
    )
    assert one_sided.snapshot is not None
    assert "YES_BIDS_EMPTY" in one_sided.snapshot.quality_flags
    assert "NO_BOOK_CROSSED" in one_sided.snapshot.quality_flags
    assert "TARGET_DEPTH_INSUFFICIENT" in one_sided.snapshot.quality_flags

    float_book = _raw_book()
    float_book["bids"] = [{"price": 0.4, "size": "1"}]
    with pytest.raises(ValueError, match="binary float"):
        normalize_paired_owner_books(
            demand=_sensing(),
            yes_artifact=_artifact("yes-token", raw_book=float_book),
            no_artifact=_artifact("no-token"),
            received_at=NOW + timedelta(seconds=2),
        )


def test_owner_artifact_hash_and_clock_lineage_fail_closed() -> None:
    valid = _artifact("yes-token")
    with pytest.raises(ValidationError, match="raw_payload_hash"):
        FrozenOwnerBookArtifact.model_validate(
            {**valid.model_dump(), "raw_book": _raw_book(crossed=True)}
        )
    bad_capture = {**valid.capture, "clock_lineage_status": "legacy_missing_response_clock"}
    with pytest.raises(ValidationError, match="exact response-clock"):
        FrozenOwnerBookArtifact.model_validate(
            {**valid.model_dump(), "capture": bad_capture}
        )


def test_paired_snapshot_and_receipt_persist_against_the_frozen_demand(tmp_path) -> None:
    identity = _identity()
    previous = MarketSnapshot(
        **_env(f"gamma_market_snapshot:{SHA_A}", source="gamma_catalog_adapter"),
        identity=identity,
        title="Fixture",
        question="Original question?",
        status=MarketStatus.ACTIVE,
        rules_raw="The official source decides.",
        source_observed_at=NOW,
        ingested_at=NOW,
    )
    current = MarketSnapshot(
        **_env(f"gamma_market_snapshot:{SHA_B}", source="gamma_catalog_adapter"),
        identity=identity,
        title="Fixture",
        question="Updated question?",
        status=MarketStatus.ACTIVE,
        rules_raw="The official source decides.",
        source_observed_at=NOW + timedelta(seconds=1),
        ingested_at=NOW + timedelta(seconds=1),
    )
    change_id = stable_record_id("market_change", previous.record_id, current.record_id)
    change = MarketChangeEvent(
        **_env(change_id, source="market_change_detector"),
        change_event_id=change_id,
        market_id=identity.market_id,
        previous_snapshot_id=previous.record_id,
        current_snapshot_id=current.record_id,
        previous_snapshot_sha256=previous.canonical_sha256,
        current_snapshot_sha256=current.canonical_sha256,
        previous_status=MarketStatus.ACTIVE,
        current_status=MarketStatus.ACTIVE,
        previous_rule_hash=previous.rule_hash,
        current_rule_hash=current.rule_hash,
        change_types=(MarketChangeType.METADATA_CHANGED,),
        changed_fields=("question",),
        effective_at=current.source_observed_at,
        detected_at=current.ingested_at,
    )
    demand = build_sensing_demand(
        change_event=change,
        identity=identity,
        requested_at=NOW + timedelta(seconds=2),
        valid_until=NOW + timedelta(minutes=5),
        max_staleness_seconds=30,
        target_sizes=(Decimal("10"),),
        run_id="integration-run",
    )
    outcome = normalize_paired_owner_books(
        demand=demand,
        yes_artifact=_artifact("yes-token", response_at=NOW + timedelta(seconds=3)),
        no_artifact=_artifact("no-token", response_at=NOW + timedelta(seconds=3)),
        received_at=NOW + timedelta(seconds=4),
    )
    assert outcome.snapshot is not None
    repo = AlphaRepository(tmp_path / "alpha.db")
    for contract in (previous, current, change, demand, outcome.snapshot, outcome.receipt):
        repo.save_contract(contract)
    assert repo.get_contract(outcome.receipt.record_id) is not None


def test_book_adapter_passes_offline_capability_audit() -> None:
    root = Path(__file__).resolve().parents[2]
    result = audit_source_tree(root / "src/polymarket_alpha/books")
    assert result.passed, result.violations
