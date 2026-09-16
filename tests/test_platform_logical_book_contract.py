from __future__ import annotations

from dataclasses import replace

import pytest

from src.platform.market_data.capture_contract import materialize_orderbook_capture
from src.platform.market_data.logical_book import (
    LogicalBookSnapshot,
    bind_feature_execution_books,
    logical_book_from_rest,
    logical_book_from_ws,
)
from src.platform.market_data.identity import canonical_json_hash
from src.platform.market_data.ws_incremental_book import IncrementalBookReconstructor


def _engine() -> IncrementalBookReconstructor:
    engine = IncrementalBookReconstructor()
    engine.activate_epoch("epoch-1", ["token-1"])
    engine.apply_envelope(
        {
            "subscription_epoch_id": "epoch-1",
            "received_at_utc": "2026-08-30T10:00:00Z",
            "message": {
                "event_type": "book",
                "asset_id": "token-1",
                "timestamp": "1788084000000",
                "bids": [{"price": "0.40", "size": "10"}],
                "asks": [{"price": "0.45", "size": "10"}],
            },
        }
    )
    return engine


def _reidentify(
    book: LogicalBookSnapshot, **changes: object
) -> LogicalBookSnapshot:
    payload = book.identity_payload()
    payload.update(changes)
    return LogicalBookSnapshot(
        logical_snapshot_id=canonical_json_hash(payload), **payload
    )


def test_ws_feature_and_execution_refs_stay_explicit_and_gap_free() -> None:
    engine = _engine()
    feature = logical_book_from_ws(
        engine.snapshot(
            "token-1",
            observed_at_utc="2026-08-30T10:00:01Z",
            requested_shares=5,
            book_role="model_feature",
        )
    )
    execution = logical_book_from_ws(
        engine.snapshot(
            "token-1",
            observed_at_utc="2026-08-30T10:00:01Z",
            requested_shares=5,
            book_role="execution_quote",
        )
    )
    pair = bind_feature_execution_books(
        feature_book=feature, execution_book=execution
    )
    assert feature.native_snapshot_id == execution.native_snapshot_id
    assert feature.logical_snapshot_id != execution.logical_snapshot_id
    assert pair.feature_book_snapshot_id == feature.logical_snapshot_id
    assert pair.execution_book_snapshot_id == execution.logical_snapshot_id

    bad_gap = replace(
        engine.snapshot(
            "token-1",
            observed_at_utc="2026-08-30T10:00:01Z",
            book_role="model_feature",
        ),
        gap_detection_status="ws_sequence_gap",
    )
    with pytest.raises(ValueError, match="coverage_gap:ws_sequence_gap"):
        logical_book_from_ws(bad_gap)


def test_rest_snapshot_uses_response_clock_and_never_becomes_ws_fallback() -> None:
    capture = materialize_orderbook_capture(
        token_id="token-1",
        raw_book={
            "timestamp": "1788084000000",
            "hash": "book-hash",
            "bids": [{"price": "0.40", "size": "10"}],
            "asks": [{"price": "0.45", "size": "10"}],
        },
        request_started_at_utc="2026-08-30T10:00:00Z",
        response_received_at_utc="2026-08-30T10:00:00.100Z",
        parsed_at_utc="2026-08-30T10:00:00.101Z",
        request_batch_capture_id="batch-1",
    )
    feature = logical_book_from_rest(
        capture,
        book_role="model_feature",
        depth_status="requested_shares_two_way_executable",
    )
    execution = logical_book_from_rest(
        capture,
        book_role="execution_quote",
        depth_status="requested_shares_two_way_executable",
    )
    assert feature.available_at_utc == "2026-08-30T10:00:00.100Z"
    assert bind_feature_execution_books(
        feature_book=feature, execution_book=execution
    ).pair_id

    with pytest.raises(ValueError, match="fallback is forbidden"):
        bind_feature_execution_books(feature_book=feature, execution_book=None)
    with pytest.raises(ValueError, match="partial book coverage"):
        logical_book_from_rest(
            capture,
            book_role="execution_quote",
            depth_status="requested_shares_two_way_executable",
            coverage_scope="partial_selected_levels",
        )
    with pytest.raises(ValueError, match="insufficient"):
        logical_book_from_rest(
            capture,
            book_role="execution_quote",
            depth_status="insufficient_depth",
        )


def test_feature_execution_pair_requires_same_transport_snapshot_and_clocks() -> None:
    engine = _engine()
    feature = logical_book_from_ws(
        engine.snapshot(
            "token-1",
            observed_at_utc="2026-08-30T10:00:01Z",
            requested_shares=5,
            book_role="model_feature",
        )
    )
    execution = logical_book_from_ws(
        engine.snapshot(
            "token-1",
            observed_at_utc="2026-08-30T10:00:01Z",
            requested_shares=5,
            book_role="execution_quote",
        )
    )

    for changed, message in (
        ({"transport": "REST"}, "transport mismatch"),
        ({"native_snapshot_id": "another-native-snapshot"}, "native snapshot mismatch"),
        ({"exchange_clock": "another-exchange-clock"}, "exchange clock mismatch"),
        ({"available_at_utc": "2026-08-30T10:00:02Z"}, "availability clock mismatch"),
    ):
        with pytest.raises(ValueError, match=message):
            bind_feature_execution_books(
                feature_book=feature,
                execution_book=_reidentify(execution, **changed),
            )
