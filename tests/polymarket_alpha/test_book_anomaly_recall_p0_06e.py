"""Acceptance tests for the pure P0-06E book-anomaly recall provider."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.polymarket_alpha.contracts import (
    BookLeg,
    BookLevel,
    MarketIdentity,
    OrderbookSnapshot,
    TargetDepthMetrics,
    stable_record_id,
)
from src.polymarket_alpha.recall.book_anomaly import (
    BOOK_ANOMALY_PROVIDER_ID,
    BookAnomalyRecallConfig,
    BookAnomalyRecallProvider,
    BookAnomalyRecallRequest,
    BookAnomalyRouteStatus,
    BookAnomalySkipReason,
    FamilyThresholdRelation,
)
from src.polymarket_alpha.security import audit_source_tree


UTC = timezone.utc
NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def _snapshot(
    market: str = "market-a",
    *,
    yes_bid: str = "0.45",
    yes_ask: str = "0.55",
    no_bid: str = "0.45",
    no_ask: str = "0.55",
    yes_bid_size: str = "20",
    yes_ask_size: str = "20",
    no_bid_size: str = "20",
    no_ask_size: str = "20",
    stale: bool = False,
    observed_at: datetime = NOW,
    sufficient: bool = True,
    one_sided: bool = False,
) -> OrderbookSnapshot:
    record_id = stable_record_id("orderbook_snapshot", market, yes_bid, yes_ask, no_bid, no_ask, observed_at)
    depth = TargetDepthMetrics(
        target_size=Decimal("10"),
        buy_vwap=Decimal("0.5"),
        sell_vwap=Decimal("0.5"),
        buy_insufficient_depth=not sufficient,
        sell_insufficient_depth=not sufficient,
    )
    return OrderbookSnapshot(
        record_id=record_id,
        run_id="fixture-run",
        created_at=observed_at,
        source="existing_book_owner",
        source_version="fixture-v1",
        provenance=(),
        extensions={},
        identity=MarketIdentity(
            event_id=f"event-{market}",
            market_id=market,
            condition_id=f"condition-{market}",
            yes_token_id=f"yes-{market}",
            no_token_id=f"no-{market}",
        ),
        capture_group_id=f"capture-{market}",
        captured_at=observed_at,
        source_observed_at=observed_at,
        yes_leg=BookLeg(
            token_id=f"yes-{market}",
            bids=() if one_sided else (BookLevel(price=Decimal(yes_bid), size=Decimal(yes_bid_size)),),
            asks=(BookLevel(price=Decimal(yes_ask), size=Decimal(yes_ask_size)),),
        ),
        no_leg=BookLeg(
            token_id=f"no-{market}",
            bids=(BookLevel(price=Decimal(no_bid), size=Decimal(no_bid_size)),),
            asks=(BookLevel(price=Decimal(no_ask), size=Decimal(no_ask_size)),),
        ),
        yes_depth=(depth,),
        no_depth=(depth,),
        stale=stale,
        quality_flags=(),
        raw_artifact_ids=(f"owner_book_artifact:{market}",),
    )


def _request(*snapshots: OrderbookSnapshot, as_of: datetime = NOW, relations=()):
    return BookAnomalyRecallRequest(
        run_id="recall-run",
        as_of=as_of,
        snapshots=snapshots,
        family_relations=relations,
    )


def _relation(lower: OrderbookSnapshot, higher: OrderbookSnapshot) -> FamilyThresholdRelation:
    return FamilyThresholdRelation(
        relation_id="threshold-relation-1",
        family_id="threshold-family-1",
        lower_market_id=lower.identity.market_id,
        lower_snapshot_id=lower.record_id,
        lower_snapshot_sha256=lower.canonical_sha256,
        higher_market_id=higher.identity.market_id,
        higher_snapshot_id=higher.record_id,
        higher_snapshot_sha256=higher.canonical_sha256,
    )


def test_no_book_is_typed_skip_and_does_not_create_a_hit() -> None:
    outcome = BookAnomalyRecallProvider().recall(_request())
    assert outcome.hits == ()
    assert outcome.route_results[0].status == BookAnomalyRouteStatus.SKIPPED
    assert outcome.route_results[0].skip_reason == BookAnomalySkipReason.NO_BOOK


@pytest.mark.parametrize(
    ("snapshot", "as_of", "reason"),
    [
        (_snapshot(stale=True), NOW, BookAnomalySkipReason.STALE_BOOK),
        (_snapshot(observed_at=NOW - timedelta(minutes=3)), NOW, BookAnomalySkipReason.STALE_BOOK),
        (_snapshot(one_sided=True), NOW, BookAnomalySkipReason.ONE_SIDED_BOOK),
        (_snapshot(sufficient=False), NOW, BookAnomalySkipReason.INSUFFICIENT_DEPTH),
        (
            _snapshot(observed_at=NOW + timedelta(seconds=1)),
            NOW,
            BookAnomalySkipReason.FUTURE_BOOK,
        ),
        (
            _snapshot(yes_bid="0.55", yes_ask="0.55"),
            NOW,
            BookAnomalySkipReason.CROSSED_BOOK,
        ),
    ],
)
def test_bad_book_routes_are_typed_skips(snapshot, as_of, reason) -> None:
    outcome = BookAnomalyRecallProvider().recall(_request(snapshot, as_of=as_of))
    assert outcome.hits == ()
    assert outcome.route_results[0].status == BookAnomalyRouteStatus.SKIPPED
    assert outcome.route_results[0].skip_reason == reason


def test_spread_depth_and_paired_consistency_are_structural_reasons_only() -> None:
    snapshot = _snapshot(
        yes_bid="0.20",
        yes_ask="0.45",
        no_bid="0.20",
        no_ask="0.45",
        yes_bid_size="5",
        yes_ask_size="5",
        no_bid_size="5",
        no_ask_size="5",
    )
    outcome = BookAnomalyRecallProvider().recall(_request(snapshot))
    hit = outcome.hits[0]
    assert hit.recaller.value == "BOOK_ANOMALY"
    assert hit.source == BOOK_ANOMALY_PROVIDER_ID
    assert hit.provenance[0].source_artifact_id == snapshot.record_id
    assert hit.observed_at == snapshot.source_observed_at
    assert hit.valid_until == snapshot.source_observed_at + timedelta(seconds=120)
    assert hit.reason_codes == (
        "NO_SPREAD_WIDE",
        "PAIRED_MID_SUM_INCONSISTENT",
        "VISIBLE_DEPTH_SHALLOW",
        "YES_SPREAD_WIDE",
    )
    rendered = str(hit.model_dump()).lower()
    assert "mispriced" not in rendered
    assert "fair_value" not in rendered


def test_explicit_family_relation_detects_threshold_monotonicity() -> None:
    lower = _snapshot("threshold-low", yes_bid="0.35", yes_ask="0.45", no_bid="0.55", no_ask="0.65")
    higher = _snapshot("threshold-high", yes_bid="0.65", yes_ask="0.75", no_bid="0.25", no_ask="0.35")
    outcome = BookAnomalyRecallProvider().recall(_request(lower, higher, relations=(_relation(lower, higher),)))
    assert len(outcome.hits) == 2
    assert all(hit.reason_codes == ("FAMILY_THRESHOLD_MONOTONICITY_INCONSISTENT",) for hit in outcome.hits)


def test_family_lineage_conflict_fails_closed() -> None:
    lower = _snapshot("threshold-low")
    higher = _snapshot("threshold-high")
    bad = _relation(lower, higher).model_copy(update={"higher_snapshot_sha256": "f" * 64})
    with pytest.raises(ValueError, match="lineage conflicts"):
        _request(lower, higher, relations=(bad,))


def test_exact_retry_is_deterministic_and_bad_route_does_not_block_good_route() -> None:
    good = _snapshot("good", yes_bid="0.20", yes_ask="0.50", no_bid="0.50", no_ask="0.80")
    stale = _snapshot("stale", stale=True)
    request = _request(good, stale)
    provider = BookAnomalyRecallProvider()
    first = provider.recall(request)
    retry = provider.recall(request)
    assert retry == first
    assert [hit.market_id for hit in first.hits] == ["good"]
    assert any(row.market_id == "stale" and row.status == BookAnomalyRouteStatus.SKIPPED for row in first.route_results)

    later_run = request.model_copy(update={"run_id": "recall-run-2"})
    later = provider.recall(later_run)
    assert first.hits[0].record_id != later.hits[0].record_id


def test_provider_source_passes_capability_audit() -> None:
    audit = audit_source_tree("src/polymarket_alpha/recall/book_anomaly.py")
    assert audit.passed, audit.violations
