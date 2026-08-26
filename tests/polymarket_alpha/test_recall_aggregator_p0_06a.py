from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sqlite3

from src.polymarket_alpha.contracts import (
    CandidateCard,
    CandidateState,
    MarketIdentity,
    MarketSnapshot,
    MarketStatus,
    RecallHit,
    RecallerType,
    stable_record_id,
)
from src.polymarket_alpha.recall import (
    LateHitImpactType,
    ProviderBatch,
    ProviderDescriptor,
    ProviderRegistry,
    RecallAggregationRequest,
    RecallAggregator,
)
from src.polymarket_alpha.storage import AlphaRepository


NOW = datetime(2026, 8, 26, 7, 0, tzinfo=timezone.utc)
MARKET_ID = "market-1"


def _registry() -> ProviderRegistry:
    return ProviderRegistry(
        (
            ProviderDescriptor(
                provider_id="new_changed",
                recaller=RecallerType.NEW_CHANGED,
                recaller_version="new-v1",
            ),
            ProviderDescriptor(
                provider_id="wallet",
                recaller=RecallerType.SPECIALIST_WALLET,
                recaller_version="wallet-v1",
            ),
            ProviderDescriptor(
                provider_id="book_anomaly",
                recaller=RecallerType.BOOK_ANOMALY,
                recaller_version="book-v1",
                requires_book=True,
            ),
            ProviderDescriptor(
                provider_id="disabled_dispute",
                recaller=RecallerType.CONTROVERSY,
                recaller_version="dispute-v1",
                enabled=False,
            ),
        )
    )


def _hit(
    marker: str,
    *,
    provider: str = "new_changed",
    recaller: RecallerType = RecallerType.NEW_CHANGED,
    version: str = "new-v1",
    score: str = "0.6",
    observed_at: datetime = NOW,
    reason: str = "NEW_MARKET",
    historical_only: bool = False,
    valid_until: datetime | None = None,
) -> RecallHit:
    return RecallHit(
        record_id=f"recall_hit:{marker * 64}",
        run_id="recall-run",
        created_at=observed_at,
        source=provider,
        source_version=version,
        provenance=(),
        extensions={},
        market_id=MARKET_ID,
        recaller=recaller,
        recaller_version=version,
        reason_codes=(reason,),
        features={"fixture": marker},
        raw_score=Decimal(score),
        observed_at=observed_at,
        valid_until=valid_until,
        historical_only=historical_only,
    )


def _request(
    *batches: ProviderBatch,
    prior_candidate: CandidateCard | None = None,
    prior_hits: tuple[RecallHit, ...] = (),
    before_hash: str | None = None,
    after_hash: str | None = None,
    include_book: bool = False,
) -> RecallAggregationRequest:
    before = {} if before_hash is None or prior_candidate is None else {prior_candidate.candidate_id: before_hash}
    after = {} if after_hash is None or prior_candidate is None else {prior_candidate.candidate_id: after_hash}
    return RecallAggregationRequest(
        run_id="aggregation-run",
        created_at=NOW + timedelta(minutes=10),
        as_of=NOW + timedelta(minutes=10),
        include_book_providers=include_book,
        batches=batches,
        prior_candidates=() if prior_candidate is None else (prior_candidate,),
        prior_hits=prior_hits,
        prior_projection_input_hashes=before,
        merged_projection_input_hashes=after,
    )


def _with_state(candidate: CandidateCard, state: CandidateState) -> CandidateCard:
    values = candidate.model_dump(mode="python")
    values.update(
        state=state,
        record_id=stable_record_id(
            "candidate_card",
            candidate.candidate_id,
            candidate.recall_hit_ids,
            state,
        ),
    )
    return CandidateCard.model_validate(values)


def _market_snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        record_id=stable_record_id("market_snapshot", MARKET_ID, "v1"),
        run_id="catalog-run",
        created_at=NOW,
        source="fixture_catalog",
        source_version="v1",
        identity=MarketIdentity(
            event_id="event-1",
            market_id=MARKET_ID,
            condition_id="condition-1",
            yes_token_id="yes-token",
            no_token_id="no-token",
        ),
        title="Fixture market",
        question="Will the event occur?",
        status=MarketStatus.ACTIVE,
        end_at=NOW + timedelta(days=1),
        rules_raw="Resolves YES if the official source confirms the event.",
        source_observed_at=NOW,
        ingested_at=NOW,
    )


def test_zero_provider_and_missing_provider_do_not_block_the_run() -> None:
    aggregator = RecallAggregator(_registry())
    empty = aggregator.aggregate(_request())
    assert empty.results == ()
    assert empty.rejected == ()
    unknown = aggregator.aggregate(
        _request(ProviderBatch(provider_id="not_registered", hits=(_hit("a"),)))
    )
    assert unknown.results == ()
    assert [(item.provider_id, item.reason) for item in unknown.rejected] == [
        ("not_registered", "UNKNOWN_PROVIDER")
    ]


def test_prebook_hit_builds_candidate_while_book_provider_is_skipped() -> None:
    aggregator = RecallAggregator(_registry())
    prebook = _hit("a")
    book = _hit(
        "b",
        provider="book_anomaly",
        recaller=RecallerType.BOOK_ANOMALY,
        version="book-v1",
        reason="ONE_SIDED_BOOK",
    )
    outcome = aggregator.aggregate(
        _request(
            ProviderBatch(provider_id="new_changed", hits=(prebook,)),
            ProviderBatch(provider_id="book_anomaly", hits=(book,)),
        )
    )
    assert len(outcome.results) == 1
    result = outcome.results[0]
    assert result.candidate.recall_hit_ids == (prebook.record_id,)
    assert result.candidate.state == CandidateState.CANDIDATE_MERGED
    assert result.impact.impact == LateHitImpactType.NOT_APPLICABLE
    assert [(item.recall_hit_id, item.reason) for item in outcome.rejected] == [
        (book.record_id, "BOOK_PROVIDER_SKIPPED")
    ]
    assert "book_anomaly" not in outcome.active_provider_ids


def test_multi_provider_merge_is_deterministic_and_dedupes_restart_retry() -> None:
    aggregator = RecallAggregator(_registry())
    new = _hit("b")
    wallet = _hit(
        "c",
        provider="wallet",
        recaller=RecallerType.SPECIALIST_WALLET,
        version="wallet-v1",
        score="0.2",
        reason="SPECIALIST_WALLET_ENTRY",
    )
    first = aggregator.aggregate(
        _request(
            ProviderBatch(provider_id="new_changed", hits=(new,)),
            ProviderBatch(provider_id="wallet", hits=(wallet,)),
        )
    ).results[0]
    reversed_result = aggregator.aggregate(
        _request(
            ProviderBatch(provider_id="wallet", hits=(wallet,)),
            ProviderBatch(provider_id="new_changed", hits=(new,)),
        ).model_copy(update={"run_id": "different-restart-run", "created_at": NOW + timedelta(hours=1)})
    ).results[0]
    assert first.candidate.canonical_sha256 == reversed_result.candidate.canonical_sha256
    assert first.candidate.recall_score == Decimal("0.8")
    assert first.candidate.research_priority.value == "CORE"

    retry = new.model_copy(
        update={
            "record_id": f"recall_hit:{'f' * 64}",
            "run_id": "retry-run",
            "created_at": NOW + timedelta(minutes=1),
        }
    )
    restarted = aggregator.aggregate(
        _request(
            ProviderBatch(provider_id="new_changed", hits=(retry,)),
            prior_candidate=first.candidate,
            prior_hits=(new, wallet),
        )
    ).results[0]
    assert restarted.candidate.canonical_sha256 == first.candidate.canonical_sha256
    assert restarted.duplicate_recall_hit_ids == (retry.record_id,)


def test_disabled_historical_and_expired_hits_are_explicitly_rejected() -> None:
    aggregator = RecallAggregator(_registry())
    disabled = _hit(
        "a",
        provider="disabled_dispute",
        recaller=RecallerType.CONTROVERSY,
        version="dispute-v1",
    )
    historical = _hit("b", historical_only=True)
    expired = _hit("c", valid_until=NOW + timedelta(minutes=1))
    outcome = aggregator.aggregate(
        _request(
            ProviderBatch(provider_id="disabled_dispute", hits=(disabled,)),
            ProviderBatch(provider_id="new_changed", hits=(historical, expired)),
        )
    )
    assert outcome.results == ()
    assert {item.reason for item in outcome.rejected} == {
        "PROVIDER_DISABLED",
        "HISTORICAL_ONLY",
        "RECALL_EXPIRED",
    }


def test_late_hit_before_blind_refreshes_candidate_without_book() -> None:
    aggregator = RecallAggregator(_registry())
    initial_hit = _hit("a")
    initial = aggregator.aggregate(
        _request(ProviderBatch(provider_id="new_changed", hits=(initial_hit,)))
    ).results[0].candidate
    late = _hit(
        "b",
        observed_at=NOW + timedelta(minutes=2),
        reason="METADATA_CHANGED",
    )
    merged = aggregator.aggregate(
        _request(
            ProviderBatch(provider_id="new_changed", hits=(late,)),
            prior_candidate=initial,
            prior_hits=(initial_hit,),
        )
    ).results[0]
    assert merged.impact.impact == LateHitImpactType.PRE_BLIND_REFRESH
    assert merged.impact.safe_to_advance
    assert merged.candidate.record_id != initial.record_id
    assert merged.candidate.state == CandidateState.CANDIDATE_MERGED


def test_late_material_hit_after_blind_freeze_requires_research_refresh() -> None:
    aggregator = RecallAggregator(_registry())
    initial_hit = _hit("a")
    initial = aggregator.aggregate(
        _request(ProviderBatch(provider_id="new_changed", hits=(initial_hit,)))
    ).results[0].candidate
    frozen = _with_state(initial, CandidateState.BLIND_PROJECTION_FROZEN)
    late = _hit("b", observed_at=NOW + timedelta(minutes=2), reason="RULE_EVIDENCE_CHANGED")
    merged = aggregator.aggregate(
        _request(
            ProviderBatch(provider_id="new_changed", hits=(late,)),
            prior_candidate=frozen,
            prior_hits=(initial_hit,),
            before_hash="a" * 64,
            after_hash="b" * 64,
        )
    ).results[0]
    assert merged.impact.impact == LateHitImpactType.RESEARCH_REFRESH_REQUIRED
    assert merged.impact.required_event is not None
    assert not merged.impact.safe_to_advance
    assert merged.candidate.state == CandidateState.CANDIDATE_MERGED


def test_late_hit_after_freeze_is_blocked_when_projection_impact_is_unknown() -> None:
    aggregator = RecallAggregator(_registry())
    initial_hit = _hit("a")
    initial = aggregator.aggregate(
        _request(ProviderBatch(provider_id="new_changed", hits=(initial_hit,)))
    ).results[0].candidate
    frozen = _with_state(initial, CandidateState.BLIND_PACKET_FROZEN)
    late = _hit("b", observed_at=NOW + timedelta(minutes=2))
    result = aggregator.aggregate(
        _request(
            ProviderBatch(provider_id="new_changed", hits=(late,)),
            prior_candidate=frozen,
            prior_hits=(initial_hit,),
        )
    ).results[0]
    assert result.impact.impact == LateHitImpactType.IMPACT_UNDETERMINED
    assert not result.impact.safe_to_advance
    assert result.candidate.state == CandidateState.BLIND_PACKET_FROZEN


def test_candidate_revisions_roundtrip_through_repository_without_overwrite(tmp_path) -> None:
    aggregator = RecallAggregator(_registry())
    first_hit = _hit("a")
    first = aggregator.aggregate(
        _request(ProviderBatch(provider_id="new_changed", hits=(first_hit,)))
    ).results[0].candidate
    second_hit = _hit("b", observed_at=NOW + timedelta(minutes=2))
    second = aggregator.aggregate(
        _request(
            ProviderBatch(provider_id="new_changed", hits=(second_hit,)),
            prior_candidate=first,
            prior_hits=(first_hit,),
        )
    ).results[0].candidate

    db = tmp_path / "recall.db"
    repository = AlphaRepository(db)
    repository.save_contract(_market_snapshot())
    repository.save_contract(first_hit)
    repository.save_contract(second_hit)
    repository.save_contract(first)
    repository.save_contract(second)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT count(*) FROM alpha_candidate").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM alpha_candidate_revision").fetchone()[0] == 2
    assert conn.execute(
        "SELECT current_card_id FROM alpha_candidate WHERE candidate_id = ?",
        (second.candidate_id,),
    ).fetchone()[0] == second.record_id
    assert conn.execute("SELECT count(*) FROM alpha_candidate_recall_hit").fetchone()[0] == 2
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
