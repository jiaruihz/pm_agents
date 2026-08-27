"""GLM-OP-03 acceptance tests: existing-owner book artifact bridge.

Pure offline fixtures: caller-supplied raw bytes, capture metadata and an
owner receipt.  No runtime path, no socket, no owner process.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from src.platform.market_data.capture_contract import materialize_orderbook_capture
from src.polymarket_alpha.books.adapter import (
    FrozenOwnerBookArtifact,
    build_owner_capture_demands,
    build_sensing_demand,
)
from src.polymarket_alpha.operational import (
    BookBridgeFailureCode,
    OwnerBookLegSubmission,
    bridge_owner_books,
)
from tests.polymarket_alpha.test_book_adapter_p0_05a import _change, _identity

NOW = __import__("datetime").datetime(2026, 8, 27, 12, 0, tzinfo=__import__("datetime").timezone.utc)


def _demand():
    return build_sensing_demand(
        change_event=_change(),
        identity=_identity(),
        requested_at=NOW,
        valid_until=NOW + timedelta(minutes=10),
        max_staleness_seconds=300,
        target_sizes=(Decimal("10"), Decimal("100")),
        run_id="op-book-bridge-run",
    )


def _raw_book() -> dict:
    return {
        "timestamp": "1787832001000",
        "hash": "exchange-hash",
        "bids": [{"price": "0.40", "size": "25"}],
        "asks": [{"price": "0.50", "size": "20"}, {"price": "0.55", "size": "100"}],
    }


def _capture(token_id: str, *, response_at, batch: str = "bridge-batch-1") -> dict:
    return materialize_orderbook_capture(
        token_id=token_id,
        raw_book=_raw_book(),
        request_started_at_utc=(response_at - timedelta(milliseconds=100))
        .isoformat()
        .replace("+00:00", "Z"),
        response_received_at_utc=response_at.isoformat().replace("+00:00", "Z"),
        parsed_at_utc=(response_at + timedelta(milliseconds=1)).isoformat().replace("+00:00", "Z"),
        request_batch_capture_id=batch,
    )


def _leg(token_id: str, capture: dict, *, locator: str | None = None) -> OwnerBookLegSubmission:
    return OwnerBookLegSubmission(
        locator=locator or f"inbox/books/{token_id}.json",
        raw_book_bytes=json.dumps(_raw_book(), sort_keys=True).encode("utf-8"),
        capture=capture,
        raw_artifact_id=f"owner_book_artifact:{capture['book_capture_id']}",
    )


def _inputs(demand, *, received_at, yes=None, no=None, batch="bridge-batch-1"):
    response_at = NOW + timedelta(seconds=30)
    yes_cap = yes or _capture(demand.identity.yes_token_id, response_at=response_at, batch=batch)
    no_cap = no or _capture(demand.identity.no_token_id, response_at=response_at, batch=batch)
    receipt = {
        "capture_owner": "weather_market_books",
        "alpha_demand_id": demand.demand_id,
        "condition_id": demand.identity.condition_id,
        "yes_token_id": demand.identity.yes_token_id,
        "no_token_id": demand.identity.no_token_id,
        "request_batch_capture_id": batch,
        "yes_book_capture_id": yes_cap["book_capture_id"],
        "no_book_capture_id": no_cap["book_capture_id"],
    }
    return receipt, (_leg(demand.identity.yes_token_id, yes_cap) if yes is None else yes), (
        _leg(demand.identity.no_token_id, no_cap) if no is None else no
    )


def test_accepted_bridge_returns_normalized_pair_for_formal_review() -> None:
    demand = _demand()
    received_at = NOW + timedelta(seconds=45)
    receipt, yes, no = _inputs(demand, received_at=received_at)
    result = bridge_owner_books(
        demand=demand,
        owner_demands=build_owner_capture_demands(demand),
        owner_receipt=receipt,
        yes_submission=yes,
        no_submission=no,
        received_at=received_at,
    )
    assert result.accepted and result.failure is None
    assert isinstance(result.yes_artifact, FrozenOwnerBookArtifact)
    assert isinstance(result.no_artifact, FrozenOwnerBookArtifact)
    assert result.normalization is not None
    assert result.normalization.snapshot is not None
    assert result.normalization.receipt.status.value == "ACCEPTED"
    assert result.normalization.snapshot.identity == demand.identity


def test_owner_identity_and_receipt_bindings_fail_closed() -> None:
    demand = _demand()
    received_at = NOW + timedelta(seconds=45)
    receipt, yes, no = _inputs(demand, received_at=received_at)
    wrong_owner = {**receipt, "capture_owner": "some_other_owner"}
    result = bridge_owner_books(
        demand=demand,
        owner_demands=build_owner_capture_demands(demand),
        owner_receipt=wrong_owner,
        yes_submission=yes,
        no_submission=no,
        received_at=received_at,
    )
    assert result.failure is not None
    assert result.failure.code == BookBridgeFailureCode.OWNER_MISMATCH

    wrong_demand = {**receipt, "alpha_demand_id": "book_demand:" + "0" * 64}
    result = bridge_owner_books(
        demand=demand,
        owner_demands=build_owner_capture_demands(demand),
        owner_receipt=wrong_demand,
        yes_submission=yes,
        no_submission=no,
        received_at=received_at,
    )
    assert result.failure is not None
    assert result.failure.code == BookBridgeFailureCode.RECEIPT_MISMATCH

    missing_field = {key: value for key, value in receipt.items() if key != "condition_id"}
    result = bridge_owner_books(
        demand=demand,
        owner_demands=build_owner_capture_demands(demand),
        owner_receipt=missing_field,
        yes_submission=yes,
        no_submission=no,
        received_at=received_at,
    )
    assert result.failure is not None
    assert result.failure.code == BookBridgeFailureCode.RECEIPT_MISMATCH


def test_missing_leg_and_expired_demand_fail_closed() -> None:
    demand = _demand()
    received_at = NOW + timedelta(seconds=45)
    receipt, yes, _no = _inputs(demand, received_at=received_at)
    missing = bridge_owner_books(
        demand=demand,
        owner_demands=build_owner_capture_demands(demand),
        owner_receipt=receipt,
        yes_submission=yes,
        no_submission=None,
        received_at=received_at,
    )
    assert missing.failure is not None
    assert missing.failure.code == BookBridgeFailureCode.LEG_MISSING

    expired = bridge_owner_books(
        demand=demand,
        owner_demands=build_owner_capture_demands(demand),
        owner_receipt=receipt,
        yes_submission=yes,
        no_submission=_no,
        received_at=demand.valid_until + timedelta(seconds=1),
    )
    assert expired.failure is not None
    assert expired.failure.code == BookBridgeFailureCode.DEMAND_EXPIRED


def test_hash_token_batch_and_clock_checks_fail_closed() -> None:
    demand = _demand()
    received_at = NOW + timedelta(seconds=45)
    receipt, yes, no = _inputs(demand, received_at=received_at)

    tampered_capture = dict(yes.capture)
    tampered_capture["raw_payload_hash"] = "f" * 64
    bad_hash = bridge_owner_books(
        demand=demand,
        owner_demands=build_owner_capture_demands(demand),
        owner_receipt=receipt,
        yes_submission=_leg(demand.identity.yes_token_id, tampered_capture),
        no_submission=no,
        received_at=received_at,
    )
    assert bad_hash.failure is not None
    assert bad_hash.failure.code == BookBridgeFailureCode.HASH_MISMATCH

    swapped = bridge_owner_books(
        demand=demand,
        owner_demands=build_owner_capture_demands(demand),
        owner_receipt=receipt,
        yes_submission=no,  # NO leg supplied as YES
        no_submission=yes,
        received_at=received_at,
    )
    assert swapped.failure is not None
    assert swapped.failure.code in {
        BookBridgeFailureCode.TOKEN_MISMATCH,
        BookBridgeFailureCode.RECEIPT_MISMATCH,
    }

    unscorable_capture = dict(yes.capture)
    unscorable_capture["clock_lineage_status"] = "legacy_missing_response_clock"
    unscorable = bridge_owner_books(
        demand=demand,
        owner_demands=build_owner_capture_demands(demand),
        owner_receipt=receipt,
        yes_submission=_leg(demand.identity.yes_token_id, unscorable_capture),
        no_submission=no,
        received_at=received_at,
    )
    assert unscorable.failure is not None
    assert unscorable.failure.code == BookBridgeFailureCode.UNSCORABLE_CLOCK

    escaped = bridge_owner_books(
        demand=demand,
        owner_demands=build_owner_capture_demands(demand),
        owner_receipt=receipt,
        yes_submission=_leg(demand.identity.yes_token_id, yes.capture, locator="../escape.json"),
        no_submission=no,
        received_at=received_at,
    )
    assert escaped.failure is not None
    assert escaped.failure.code == BookBridgeFailureCode.LOCATOR_INVALID


def test_stale_book_and_capture_group_mismatch_fail_closed() -> None:
    demand = _demand()
    stale_at = NOW + timedelta(seconds=30) + timedelta(seconds=301)
    receipt, yes, no = _inputs(demand, received_at=stale_at)
    stale = bridge_owner_books(
        demand=demand,
        owner_demands=build_owner_capture_demands(demand),
        owner_receipt=receipt,
        yes_submission=yes,
        no_submission=no,
        received_at=stale_at,
    )
    assert stale.failure is not None
    assert stale.failure.code == BookBridgeFailureCode.BOOK_STALE
    assert stale.normalization is None

    split_batch_at = NOW + timedelta(seconds=45)
    receipt2, yes2, _no2 = _inputs(demand, received_at=split_batch_at)
    other_batch = _capture(
        demand.identity.no_token_id,
        response_at=NOW + timedelta(seconds=30),
        batch="bridge-batch-other",
    )
    group = bridge_owner_books(
        demand=demand,
        owner_demands=build_owner_capture_demands(demand),
        owner_receipt=receipt2,
        yes_submission=yes2,
        no_submission=_leg(demand.identity.no_token_id, other_batch),
        received_at=split_batch_at,
    )
    assert group.failure is not None
    assert group.failure.code == BookBridgeFailureCode.RECEIPT_MISMATCH


def test_demand_bundle_binding_is_enforced() -> None:
    demand = _demand()
    other_demand = build_sensing_demand(
        change_event=_change(),
        identity=_identity(),
        requested_at=NOW + timedelta(minutes=5),
        valid_until=NOW + timedelta(minutes=15),
        max_staleness_seconds=300,
        target_sizes=(Decimal("10"),),
        run_id="op-book-bridge-run",
    )
    received_at = NOW + timedelta(seconds=45)
    receipt, yes, no = _inputs(demand, received_at=received_at)
    result = bridge_owner_books(
        demand=demand,
        owner_demands=build_owner_capture_demands(other_demand),
        owner_receipt=receipt,
        yes_submission=yes,
        no_submission=no,
        received_at=received_at,
    )
    assert result.failure is not None
    assert result.failure.code == BookBridgeFailureCode.DEMAND_BUNDLE_MISMATCH


def test_malformed_raw_bytes_fail_closed() -> None:
    demand = _demand()
    received_at = NOW + timedelta(seconds=45)
    receipt, _yes, no = _inputs(demand, received_at=received_at)
    broken = OwnerBookLegSubmission(
        locator="inbox/books/broken.json",
        raw_book_bytes=b"\xff\xfe not utf8",
        capture=_capture(demand.identity.yes_token_id, response_at=NOW + timedelta(seconds=30)),
        raw_artifact_id="owner_book_artifact:broken",
    )
    result = bridge_owner_books(
        demand=demand,
        owner_demands=build_owner_capture_demands(demand),
        owner_receipt=receipt,
        yes_submission=broken,
        no_submission=no,
        received_at=received_at,
    )
    assert result.failure is not None
    assert result.failure.code == BookBridgeFailureCode.RAW_BYTES_INVALID
