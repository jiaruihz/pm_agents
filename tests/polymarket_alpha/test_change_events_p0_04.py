"""P0-04 pure revision detector acceptance matrix."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.polymarket_alpha.change import detect_market_change
from src.polymarket_alpha.contracts import (
    MarketChangeType,
    MarketIdentity,
    MarketSnapshot,
    MarketStatus,
    stable_record_id,
)
from src.polymarket_alpha.security import audit_source_tree


UTC = timezone.utc
OBSERVED = datetime(2026, 8, 27, 8, 0, tzinfo=UTC)
DETECTED = OBSERVED + timedelta(minutes=1)
FAMILY_A = "a" * 64
FAMILY_B = "b" * 64


def _snapshot(
    revision: str,
    *,
    rules: str = "Settlement uses the official result.",
    status: MarketStatus = MarketStatus.ACTIVE,
    market_id: str = "market-1",
    title: str = "Fixture title",
    question: str = "Will the fixture occur?",
    end_at: datetime | None = OBSERVED + timedelta(days=1),
    tags: tuple[str, ...] = ("politics", "us"),
    event_id: str = "event-1",
    condition_id: str = "condition-1",
    yes_token_id: str = "yes-1",
    no_token_id: str = "no-1",
    volume: str = "10",
    liquidity: str = "5",
) -> MarketSnapshot:
    record_id = stable_record_id("gamma_market_snapshot", market_id, revision)
    return MarketSnapshot(
        record_id=record_id,
        run_id="catalog-run",
        created_at=OBSERVED,
        source="fixture_catalog",
        source_version="fixture-v1",
        identity=MarketIdentity(
            event_id=event_id,
            market_id=market_id,
            condition_id=condition_id,
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
        ),
        title=title,
        question=question,
        status=status,
        end_at=end_at,
        tags=tags,
        rules_raw=rules,
        volume=Decimal(volume),
        liquidity=Decimal(liquidity),
        source_observed_at=OBSERVED + timedelta(seconds=int(revision[-1], 36) if revision[-1].isalnum() else 0),
        ingested_at=OBSERVED + timedelta(minutes=1),
    )


def _detect(previous: MarketSnapshot | None, current: MarketSnapshot, **kwargs: object):
    return detect_market_change(
        previous,
        current,
        run_id="change-run",
        detected_at=DETECTED + timedelta(hours=3),
        **kwargs,
    )


def test_first_observation_emits_only_new() -> None:
    current = _snapshot("new1")
    event = _detect(None, current)
    assert event is not None
    assert event.change_types == (MarketChangeType.NEW,)
    assert event.changed_fields == ("__new__",)
    assert event.previous_snapshot_id is None


def test_one_character_rule_change_but_whitespace_equivalent_is_noop() -> None:
    previous = _snapshot("old1", rules="The answer is Yes if A.")
    changed = _snapshot("new2", rules="The answer is Yes if B.")
    event = _detect(previous, changed)
    assert event is not None
    assert event.change_types == (MarketChangeType.RULE_CHANGED,)
    assert event.changed_fields == ("rule_hash",)
    whitespace_only = _snapshot("new3", rules="  The  answer is Yes if A. \n")
    assert _detect(previous, whitespace_only) is None


def test_lifecycle_transitions_emit_terminal_types() -> None:
    active = _snapshot("act1")
    closed = _snapshot("clo2", status=MarketStatus.CLOSED)
    resolved = _snapshot("res3", status=MarketStatus.RESOLVED)
    close_event = _detect(active, closed)
    resolve_event = _detect(closed, resolved)
    assert close_event is not None and resolve_event is not None
    assert close_event.change_types == (
        MarketChangeType.CLOSED,
        MarketChangeType.LIFECYCLE_CHANGED,
    )
    assert resolve_event.change_types == (
        MarketChangeType.LIFECYCLE_CHANGED,
        MarketChangeType.RESOLVED,
    )


def test_no_change_and_volume_only_are_not_structural_events() -> None:
    previous = _snapshot("old1")
    exact_retry = _snapshot("new2")
    volume_only = _snapshot("new3", volume="999", liquidity="888")
    assert _detect(previous, exact_retry) is None
    assert _detect(previous, volume_only) is None


def test_metadata_family_and_identity_mapping_drift_are_explicit() -> None:
    previous = _snapshot("old1")
    current = _snapshot(
        "new2",
        question="Will the updated fixture occur?",
        yes_token_id="yes-2",
    )
    event = _detect(
        previous,
        current,
        previous_family_fingerprint=FAMILY_A,
        current_family_fingerprint=FAMILY_B,
    )
    assert event is not None
    assert event.change_types == (
        MarketChangeType.FAMILY_CHANGED,
        MarketChangeType.METADATA_CHANGED,
    )
    assert event.changed_fields == (
        "family_fingerprint",
        "identity.yes_token_id",
        "question",
    )
    assert event.extensions["family_lineage"]["previous_fingerprint"] == FAMILY_A


def test_market_id_mismatch_and_partial_family_lineage_fail_closed() -> None:
    previous = _snapshot("old1")
    with pytest.raises(ValueError, match="market_id mismatch"):
        _detect(previous, _snapshot("new2", market_id="other-market"))
    with pytest.raises(ValueError, match="family fingerprint lineage"):
        _detect(previous, _snapshot("new2"), current_family_fingerprint=FAMILY_A)


def test_retry_is_deterministic_and_later_attempt_has_distinct_identity() -> None:
    previous = _snapshot("old1", tags=("b", "a"))
    current = _snapshot("new2", title="A changed title", tags=("a", "b"))
    first = _detect(previous, current)
    exact_retry = _detect(previous, current)
    later_attempt = detect_market_change(
        previous,
        current,
        run_id="other-run",
        detected_at=DETECTED + timedelta(days=1),
    )
    assert first is not None and exact_retry is not None and later_attempt is not None
    assert first.record_id == exact_retry.record_id
    assert first.canonical_sha256 == exact_retry.canonical_sha256
    assert first.record_id != later_attempt.record_id
    assert first.canonical_sha256 != later_attempt.canonical_sha256
    assert first.changed_fields == ("title",)


def test_naive_clock_and_unreleased_status_fail_closed() -> None:
    current = _snapshot("new2")
    with pytest.raises(ValueError, match="timezone-aware"):
        detect_market_change(
            None,
            current,
            run_id="change-run",
            detected_at=datetime(2026, 8, 27, 9, 0),
        )
    superseded = _snapshot("sup3", status=MarketStatus.SUPERSEDED)
    with pytest.raises(ValueError, match="SUPERSEDED"):
        _detect(current, superseded)


def test_change_module_has_no_static_capability_violation() -> None:
    audit = audit_source_tree("src/polymarket_alpha/change")
    assert audit.passed, audit.violations
