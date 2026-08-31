"""Deterministic, PIT and isolation tests for research-only book features."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.polymarket_alpha.books import (
    BookFeatureStatus,
    build_book_microstructure_policy,
    extract_book_microstructure_features,
)
from src.polymarket_alpha.contracts import (
    BookCapturePurpose,
    BookCaptureReceipt,
    BookCaptureStatus,
    BookLeg,
    BookLevel,
    MarketIdentity,
    OrderbookSnapshot,
    stable_record_id,
)
from src.polymarket_alpha.storage import AlphaRepository


T0 = datetime(2026, 8, 30, tzinfo=timezone.utc)


def _book_and_receipt():
    identity = MarketIdentity(
        event_id="feature-event",
        market_id="feature-market",
        condition_id="feature-condition",
        yes_token_id="feature-yes",
        no_token_id="feature-no",
    )
    fields = {
        "run_id": "feature-book",
        "created_at": T0 + timedelta(seconds=1),
        "source": "fixture",
        "source_version": "v1",
        "provenance": (),
        "extensions": {},
        "identity": identity,
        "capture_group_id": "paired-feature-capture",
        "captured_at": T0 + timedelta(seconds=1),
        "source_observed_at": T0,
        "yes_leg": BookLeg(
            token_id=identity.yes_token_id,
            bids=(
                BookLevel(price=Decimal("0.45"), size=Decimal("4")),
                BookLevel(price=Decimal("0.40"), size=Decimal("6")),
            ),
            asks=(
                BookLevel(price=Decimal("0.50"), size=Decimal("2")),
                BookLevel(price=Decimal("0.55"), size=Decimal("8")),
            ),
        ),
        "no_leg": BookLeg(
            token_id=identity.no_token_id,
            bids=(
                BookLevel(price=Decimal("0.42"), size=Decimal("5")),
                BookLevel(price=Decimal("0.38"), size=Decimal("5")),
            ),
            asks=(
                BookLevel(price=Decimal("0.48"), size=Decimal("4")),
                BookLevel(price=Decimal("0.52"), size=Decimal("6")),
            ),
        ),
        "yes_depth": (),
        "no_depth": (),
        "stale": False,
        "quality_flags": (),
        "raw_artifact_ids": ("raw:yes", "raw:no"),
    }
    snapshot_id = stable_record_id("orderbook_snapshot", fields)
    snapshot = OrderbookSnapshot(record_id=snapshot_id, **fields)
    receipt_fields = {
        "run_id": "feature-receipt",
        "created_at": T0 + timedelta(seconds=2),
        "source": "fixture",
        "source_version": "v1",
        "provenance": (),
        "extensions": {},
        "demand_id": "book_demand:" + "a" * 64,
        "demand_sha256": "b" * 64,
        "market_id": identity.market_id,
        "purpose": BookCapturePurpose.FORMAL_REVIEW,
        "status": BookCaptureStatus.ACCEPTED,
        "capture_owner": "fixture-owner",
        "received_at": T0 + timedelta(seconds=2),
        "orderbook_snapshot_id": snapshot.record_id,
        "orderbook_snapshot_sha256": snapshot.canonical_sha256,
        "capture_group_id": snapshot.capture_group_id,
        "source_observed_at": snapshot.source_observed_at,
        "error_code": None,
    }
    receipt_id = stable_record_id("book_receipt", receipt_fields)
    receipt = BookCaptureReceipt(
        record_id=receipt_id, receipt_id=receipt_id, **receipt_fields
    )
    return snapshot, receipt


def _policy(*, targets=(Decimal("1"), Decimal("5"), Decimal("10"))):
    return build_book_microstructure_policy(
        run_id="feature-policy",
        created_at=T0,
        top_levels=2,
        target_sizes=targets,
        fee_rate=Decimal("0"),
        slippage_buffer=Decimal("0"),
    )


def test_feature_golden_values_and_replay_identity() -> None:
    snapshot, receipt = _book_and_receipt()
    feature = extract_book_microstructure_features(
        snapshot=snapshot,
        receipt=receipt,
        policy=_policy(),
        as_of=T0 + timedelta(seconds=3),
    )
    replay = extract_book_microstructure_features(
        snapshot=snapshot,
        receipt=receipt,
        policy=_policy(),
        as_of=T0 + timedelta(hours=1),
    )
    assert feature.status is BookFeatureStatus.USABLE
    assert replay.record_id == feature.record_id
    assert replay.canonical_sha256 == feature.canonical_sha256
    assert feature.available_at == receipt.received_at
    assert feature.source_to_receipt_seconds == Decimal("2")
    assert feature.capture_to_receipt_seconds == Decimal("1")
    assert feature.yes.mid == Decimal("0.475")
    assert feature.yes.spread == Decimal("0.05")
    assert feature.yes.bid_depth_notional == Decimal("4.2")
    assert feature.yes.ask_depth_notional == Decimal("5.4")
    assert feature.yes.depth_imbalance == Decimal("0")
    assert feature.yes.bid_size_hhi == Decimal("0.52")
    assert feature.yes.ask_size_hhi == Decimal("0.68")
    assert feature.no.bid_size_hhi == Decimal("0.5")
    assert feature.no.ask_size_hhi == Decimal("0.52")
    assert feature.paired_top_ask_premium == Decimal("-0.02")
    assert feature.paired_top_bid_discount == Decimal("0.13")

    point = next(row for row in feature.impact_curve if row.target_size == 5)
    assert point.fully_executable
    assert point.yes_buy_effective_price == Decimal("0.53")
    assert point.yes_sell_effective_price == Decimal("0.44")
    assert point.no_buy_effective_price == Decimal("0.488")
    assert point.no_sell_effective_price == Decimal("0.42")
    assert point.yes_buy_impact == Decimal("0.03")
    assert point.paired_buy_cost == Decimal("1.018")
    assert point.paired_buy_premium == Decimal("0.018")


def test_feature_is_pit_bound_quarantines_quality_and_preserves_partial_curve() -> None:
    snapshot, receipt = _book_and_receipt()
    with pytest.raises(ValueError, match="future receipt"):
        extract_book_microstructure_features(
            snapshot=snapshot,
            receipt=receipt,
            policy=_policy(),
            as_of=T0 + timedelta(seconds=1),
        )

    flagged = snapshot.model_copy(update={"quality_flags": ("CLOCK_INCOMPLETE",)})
    rebound = receipt.model_copy(
        update={"orderbook_snapshot_sha256": flagged.canonical_sha256}
    )
    quarantined = extract_book_microstructure_features(
        snapshot=flagged,
        receipt=rebound,
        policy=_policy(targets=(Decimal("5"), Decimal("11"))),
        as_of=T0 + timedelta(seconds=3),
    )
    assert quarantined.status is BookFeatureStatus.QUARANTINED
    assert quarantined.quarantine_reasons == (
        "QUALITY_FLAG:CLOCK_INCOMPLETE",
    )
    assert quarantined.impact_curve[0].fully_executable
    assert not quarantined.impact_curve[1].fully_executable
    assert quarantined.insufficient_target_sizes == (Decimal("11"),)


def test_feature_rejects_lineage_tamper_and_changes_identity_with_book() -> None:
    snapshot, receipt = _book_and_receipt()
    with pytest.raises(ValueError, match="exact paired snapshot"):
        extract_book_microstructure_features(
            snapshot=snapshot,
            receipt=receipt.model_copy(update={"orderbook_snapshot_sha256": "f" * 64}),
            policy=_policy(),
            as_of=T0 + timedelta(seconds=3),
        )
    changed = snapshot.model_copy(
        update={
            "yes_leg": snapshot.yes_leg.model_copy(
                update={
                    "asks": (
                        BookLevel(price=Decimal("0.51"), size=Decimal("2")),
                        snapshot.yes_leg.asks[1],
                    )
                }
            )
        }
    )
    rebound = receipt.model_copy(
        update={"orderbook_snapshot_sha256": changed.canonical_sha256}
    )
    original = extract_book_microstructure_features(
        snapshot=snapshot,
        receipt=receipt,
        policy=_policy(),
        as_of=T0 + timedelta(seconds=3),
    )
    mutated = extract_book_microstructure_features(
        snapshot=changed,
        receipt=rebound,
        policy=_policy(),
        as_of=T0 + timedelta(seconds=3),
    )
    assert mutated.record_id != original.record_id
    assert mutated.yes.best_ask == Decimal("0.51")


def test_feature_module_has_no_rank_allocator_or_execution_dependency() -> None:
    source = Path(
        "src/polymarket_alpha/books/microstructure.py"
    ).read_text(encoding="utf-8")
    assert "decision.ledger" not in source
    assert "alpha_capital_agent.allocator" not in source
    assert "order_client" not in source
    assert 'decision_use: Literal["PROHIBITED"]' in source


def test_feature_and_policy_are_exactly_replayable_in_alpha_repository(
    tmp_path,
) -> None:
    snapshot, receipt = _book_and_receipt()
    policy = _policy()
    feature = extract_book_microstructure_features(
        snapshot=snapshot,
        receipt=receipt,
        policy=policy,
        as_of=T0 + timedelta(seconds=3),
    )
    repository = AlphaRepository(tmp_path / "alpha.db")
    first = repository.save_contracts_atomic((policy, feature))
    assert repository.save_contracts_atomic((policy, feature)) == first
    stored = repository.get_contract(feature.record_id)
    assert stored is not None
    assert type(feature).model_validate(stored) == feature
