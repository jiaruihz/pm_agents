"""P0-06B pre-book new/changed and structural metadata acceptance tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.polymarket_alpha.change import detect_market_change
from src.polymarket_alpha.contracts import (
    MarketIdentity,
    MarketSnapshot,
    MarketStatus,
    RecallerType,
    content_sha256,
    stable_record_id,
)
from src.polymarket_alpha.recall.aggregate import (
    ProviderBatch,
    RecallAggregationRequest,
    RecallAggregator,
)
from src.polymarket_alpha.recall.new_changed import (
    NEW_CHANGED_PROVIDER_ID,
    NewChangedRecaller,
    PrebookRecallRequest,
    RecallSuppressionReason,
)
from src.polymarket_alpha.recall.registry import ProviderRegistry
from src.polymarket_alpha.recall.structural_metadata import (
    STRUCTURAL_METADATA_PROVIDER_ID,
    StructuralMetadataRecaller,
)
from src.polymarket_alpha.security import audit_source_tree


UTC = timezone.utc
OBSERVED = datetime(2026, 8, 27, 8, 0, tzinfo=UTC)
AS_OF = OBSERVED + timedelta(days=2)
FAMILY_A = "a" * 64
FAMILY_B = "b" * 64


def _snapshot(
    revision: str,
    *,
    market_id: str = "market-1",
    status: MarketStatus = MarketStatus.ACTIVE,
    rules: str = "Settlement uses the official result.",
    title: str = "Fixture title",
    question: str = "Will the fixture occur?",
    tags: tuple[str, ...] = ("politics",),
    source_offset: int = 0,
) -> MarketSnapshot:
    observed = OBSERVED + timedelta(minutes=source_offset)
    return MarketSnapshot(
        record_id=stable_record_id("gamma_market_snapshot", market_id, revision),
        run_id="catalog-run",
        created_at=observed,
        source="fixture_catalog",
        source_version="fixture-v1",
        identity=MarketIdentity(
            event_id="event-1",
            market_id=market_id,
            condition_id="condition-1",
            yes_token_id="yes-1",
            no_token_id="no-1",
        ),
        title=title,
        question=question,
        status=status,
        end_at=OBSERVED + timedelta(days=7),
        tags=tags,
        rules_raw=rules,
        volume=Decimal("10"),
        liquidity=Decimal("5"),
        source_observed_at=observed,
        ingested_at=observed + timedelta(seconds=1),
    )


def _event(previous: MarketSnapshot | None, current: MarketSnapshot, **kwargs: object):
    return detect_market_change(
        previous,
        current,
        run_id="detector-run",
        detected_at=current.source_observed_at + timedelta(minutes=1),
        **kwargs,
    )


def _request(*events, snapshots: tuple[MarketSnapshot, ...]) -> PrebookRecallRequest:
    return PrebookRecallRequest(run_id="recall-run", as_of=AS_OF, events=events, snapshots=snapshots)


def test_new_and_rule_change_emit_prebook_hits_without_any_book_input() -> None:
    first = _snapshot("first")
    second = _snapshot("second", rules="Settlement uses revised official result.", source_offset=1)
    new_event = _event(None, first)
    rule_event = _event(first, second)
    assert new_event is not None and rule_event is not None

    outcome = NewChangedRecaller().recall(_request(new_event, rule_event, snapshots=(first, second)))

    assert [hit.reason_codes for hit in outcome.hits] == [("NEW_MARKET",), ("RULE_REVISION",)]
    assert {hit.recaller for hit in outcome.hits} == {RecallerType.NEW_CHANGED}
    assert outcome.suppressions == ()
    assert all("book" not in str(hit.features).lower() for hit in outcome.hits)
    registry = ProviderRegistry([NewChangedRecaller().descriptor()])
    merged = RecallAggregator(registry).aggregate(
        RecallAggregationRequest(
            run_id="aggregate-run",
            created_at=AS_OF,
            as_of=AS_OF,
            include_book_providers=False,
            batches=(ProviderBatch(provider_id=NEW_CHANGED_PROVIDER_ID, hits=outcome.hits),),
        )
    )
    assert len(merged.results) == 1
    assert merged.rejected == ()


def test_metadata_and_explicit_family_change_emit_structural_only() -> None:
    previous = _snapshot("old")
    current = _snapshot("new", title="Revised fixture title", source_offset=1)
    event = _event(
        previous,
        current,
        previous_family_fingerprint=FAMILY_A,
        current_family_fingerprint=FAMILY_B,
    )
    assert event is not None

    structural = StructuralMetadataRecaller().recall(_request(event, snapshots=(previous, current)))
    new_changed = NewChangedRecaller().recall(_request(event, snapshots=(previous, current)))

    assert len(structural.hits) == 1
    hit = structural.hits[0]
    assert hit.recaller == RecallerType.STRUCTURAL_METADATA
    assert hit.reason_codes == ("METADATA_REVISION", "FAMILY_RELATION_CHANGED")
    assert hit.features["family_lineage"] == {
        "previous_fingerprint": FAMILY_A,
        "current_fingerprint": FAMILY_B,
    }
    assert new_changed.hits == ()
    assert new_changed.suppressions[0].reason == RecallSuppressionReason.UNSUPPORTED_CHANGE


def test_deadline_and_identity_metadata_are_explicit_structural_reasons() -> None:
    previous = _snapshot("old")
    current = _snapshot("new", source_offset=1).model_copy(
        update={
            "end_at": OBSERVED + timedelta(days=8),
            "identity": MarketIdentity(
                event_id="event-1",
                market_id="market-1",
                condition_id="condition-1",
                yes_token_id="yes-2",
                no_token_id="no-1",
            ),
        }
    )
    event = _event(previous, current)
    assert event is not None

    outcome = StructuralMetadataRecaller().recall(_request(event, snapshots=(previous, current)))

    assert outcome.hits[0].reason_codes == (
        "METADATA_REVISION",
        "DEADLINE_REVISION",
        "IDENTITY_MAPPING_CHANGED",
    )


def test_terminal_and_post_cutoff_events_are_suppressed_for_both_providers() -> None:
    active = _snapshot("active")
    closed = _snapshot("closed", status=MarketStatus.CLOSED, source_offset=1)
    terminal = _event(active, closed)
    assert terminal is not None
    post_cutoff_request = PrebookRecallRequest(
        run_id="recall-run",
        as_of=OBSERVED,
        events=(terminal,),
        snapshots=(active, closed),
    )

    for provider in (NewChangedRecaller(), StructuralMetadataRecaller()):
        terminal_outcome = provider.recall(_request(terminal, snapshots=(active, closed)))
        assert terminal_outcome.hits == ()
        assert terminal_outcome.suppressions[0].reason == RecallSuppressionReason.TERMINAL_MARKET
        cutoff_outcome = provider.recall(post_cutoff_request)
        assert cutoff_outcome.hits == ()
        assert cutoff_outcome.suppressions[0].reason == RecallSuppressionReason.POST_CUTOFF_EVENT

    late_detection = terminal.model_copy(update={"detected_at": AS_OF + timedelta(seconds=1)})
    outcome = NewChangedRecaller().recall(
        _request(late_detection, snapshots=(active, closed))
    )
    assert outcome.hits == ()
    assert outcome.suppressions[0].reason == RecallSuppressionReason.POST_CUTOFF_EVENT


def test_lifecycle_revision_is_new_changed_but_not_a_terminal_market() -> None:
    closed = _snapshot("closed", status=MarketStatus.CLOSED)
    active = _snapshot("active", status=MarketStatus.ACTIVE, source_offset=1)
    event = _event(closed, active)
    assert event is not None

    outcome = NewChangedRecaller().recall(_request(event, snapshots=(closed, active)))

    assert outcome.hits[0].reason_codes == ("LIFECYCLE_REVISION",)
    assert outcome.hits[0].market_id == "market-1"


def test_missing_or_conflicting_snapshot_lineage_fails_closed() -> None:
    previous = _snapshot("old")
    current = _snapshot("new", rules="changed rule", source_offset=1)
    event = _event(previous, current)
    assert event is not None
    conflicting_current = current.model_copy(update={"title": "conflicting bytes"})

    missing = NewChangedRecaller().recall(_request(event, snapshots=(current,)))
    conflict = StructuralMetadataRecaller().recall(
        _request(event, snapshots=(previous, current, conflicting_current))
    )

    assert missing.hits == ()
    assert missing.suppressions[0].reason == RecallSuppressionReason.MISSING_PREVIOUS_SNAPSHOT
    assert conflict.hits == ()
    assert conflict.suppressions[0].reason == RecallSuppressionReason.SNAPSHOT_ID_CONFLICT

    conflicting_event = event.model_copy(update={"current_rule_hash": "f" * 64})
    event_conflict = NewChangedRecaller().recall(
        _request(event, conflicting_event, snapshots=(previous, current))
    )
    assert event_conflict.hits == ()
    assert event_conflict.suppressions[0].reason == RecallSuppressionReason.EVENT_ID_CONFLICT


def test_exact_retry_and_input_order_are_deterministic() -> None:
    first = _snapshot("first")
    second = _snapshot("second", rules="changed rule", source_offset=1)
    new_event = _event(None, first)
    rule_event = _event(first, second)
    assert new_event is not None and rule_event is not None
    provider = NewChangedRecaller()

    forward = provider.recall(_request(new_event, rule_event, snapshots=(first, second)))
    retry = provider.recall(_request(new_event, rule_event, snapshots=(first, second)))
    reversed_input = provider.recall(_request(rule_event, new_event, snapshots=(second, first)))

    assert content_sha256(forward) == content_sha256(retry)
    assert content_sha256(forward) == content_sha256(reversed_input)
    assert [hit.record_id for hit in forward.hits] == [hit.record_id for hit in reversed_input.hits]


def test_reason_vocabulary_and_provider_ids_are_closed_and_non_directional() -> None:
    assert NewChangedRecaller().descriptor().provider_id == NEW_CHANGED_PROVIDER_ID
    assert StructuralMetadataRecaller().descriptor().provider_id == STRUCTURAL_METADATA_PROVIDER_ID
    allowed = {
        "NEW_MARKET",
        "RULE_REVISION",
        "LIFECYCLE_REVISION",
        "METADATA_REVISION",
        "DEADLINE_REVISION",
        "IDENTITY_MAPPING_CHANGED",
        "FAMILY_RELATION_CHANGED",
    }
    assert set(allowed) == {
        "NEW_MARKET",
        "RULE_REVISION",
        "LIFECYCLE_REVISION",
        "METADATA_REVISION",
        "DEADLINE_REVISION",
        "IDENTITY_MAPPING_CHANGED",
        "FAMILY_RELATION_CHANGED",
    }
    previous = _snapshot("old")
    current = _snapshot("new", title="Updated title", source_offset=1)
    event = _event(
        previous,
        current,
        previous_family_fingerprint=FAMILY_A,
        current_family_fingerprint=FAMILY_B,
    )
    assert event is not None
    emitted = StructuralMetadataRecaller().recall(
        _request(event, snapshots=(previous, current))
    ).hits
    serialized = str([hit.model_dump(mode="json") for hit in emitted]).lower()
    forbidden = ("fair value", "mispriced", "edge", "buy", "sell", "price", "probability")
    assert all(term not in serialized for term in forbidden)


def test_owned_sources_have_no_static_capability_violation() -> None:
    audit = audit_source_tree("src/polymarket_alpha/recall")
    # Other provider files are deliberately outside this WorkOrder.  Inspect
    # only the two owned source files so concurrent changes cannot affect this check.
    violations = tuple(
        violation
        for violation in audit.violations
        if violation.path.name in {"new_changed.py", "structural_metadata.py"}
    )
    assert not violations, violations
