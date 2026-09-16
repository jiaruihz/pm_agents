from __future__ import annotations

from src.platform.market_data.capture_contract import materialize_orderbook_capture
from src.platform.market_data.capture_demand import (
    CaptureDemand,
    coalesce_capture_demands,
)
from src.platform.market_data.market_group import binary_market_group_snapshot
from src.platform.market_data.ws_incremental_book import IncrementalBookReconstructor
from weather_data_feed.market_book_contract import (
    materialize_orderbook_capture as weather_materialize_orderbook_capture,
)
from weather_data_feed.ws_incremental_book import (
    IncrementalBookReconstructor as WeatherIncrementalBookReconstructor,
)


def _demand(
    *, consumer: str, token: str, priority: str, transport: str, expires: str
) -> CaptureDemand:
    return CaptureDemand.create(
        consumer_id=consumer,
        strategy_key=f"strategy.{consumer}",
        condition_id=f"condition-{token}",
        token_id=token,
        reason="trigger",
        priority=priority,
        requested_at_utc="2026-08-12T00:00:00Z",
        expires_at_utc=expires,
        desired_transport=transport,
        requested_checkpoints_seconds=(0, 30),
        trigger_event_id=f"event-{consumer}",
    )


def test_weather_book_contract_is_identity_preserving_platform_wrapper() -> None:
    kwargs = {
        "token_id": "token-1",
        "raw_book": {
            "timestamp": "1786492800000",
            "hash": "book-hash",
            "bids": [{"price": "0.4", "size": "5"}],
            "asks": [{"price": "0.5", "size": "7"}],
        },
        "request_started_at_utc": "2026-08-12T00:00:00.000Z",
        "response_received_at_utc": "2026-08-12T00:00:00.125Z",
        "parsed_at_utc": "2026-08-12T00:00:00.126Z",
        "request_batch_capture_id": "batch-1",
    }
    assert weather_materialize_orderbook_capture(**kwargs) == materialize_orderbook_capture(
        **kwargs
    )
    assert WeatherIncrementalBookReconstructor is IncrementalBookReconstructor


def test_capture_demand_coalesces_only_overlapping_tokens() -> None:
    demands = (
        _demand(
            consumer="weather",
            token="shared-token",
            priority="P2",
            transport="REST",
            expires="2026-08-12T00:10:00Z",
        ),
        _demand(
            consumer="dispute",
            token="shared-token",
            priority="P0",
            transport="WS",
            expires="2026-08-12T00:20:00Z",
        ),
        _demand(
            consumer="crypto",
            token="crypto-only-token",
            priority="P1",
            transport="REST_WS",
            expires="2026-08-12T00:15:00Z",
        ),
    )
    assignments = coalesce_capture_demands(
        demands, at_utc="2026-08-12T00:05:00Z"
    )
    assert len(assignments) == 2
    shared = next(row for row in assignments if row.token_id == "shared-token")
    assert shared.priority == "P0"
    assert shared.desired_transport == "REST_WS"
    assert shared.consumer_ids == ("dispute", "weather")


def test_capture_demand_identity_is_stable_across_retry_clock() -> None:
    first = _demand(
        consumer="dispute",
        token="token-1",
        priority="P0",
        transport="REST_WS",
        expires="2026-08-12T00:10:00Z",
    )
    retried = CaptureDemand.create(
        consumer_id="dispute",
        strategy_key="strategy.dispute",
        condition_id="condition-token-1",
        token_id="token-1",
        reason="trigger",
        priority="P0",
        requested_at_utc="2026-08-12T00:01:00Z",
        expires_at_utc="2026-08-12T00:11:00Z",
        desired_transport="REST_WS",
        requested_checkpoints_seconds=(0, 30),
        trigger_event_id="event-dispute",
    )
    assert first.demand_id == retried.demand_id


def test_binary_market_group_references_shared_book_snapshots() -> None:
    snapshot = binary_market_group_snapshot(
        group_id="condition:0xabc",
        market_id="100",
        condition_id="0xabc",
        outcomes=("Over", "Under"),
        token_ids=("over-token", "under-token"),
        captured_at_utc="2026-08-12T00:00:00Z",
        available_at_utc="2026-08-12T00:00:01Z",
        book_snapshot_ids={
            "over-token": "over-book",
            "under-token": "under-book",
        },
        capture_batch_id="batch-1",
    )
    assert snapshot.batch_complete is True
    assert [row.outcome_label for row in snapshot.expressions] == ["Over", "Under"]
    assert snapshot.book_snapshot_ids["under-token"] == "under-book"
    assert snapshot.group_snapshot_id == binary_market_group_snapshot(
        group_id="condition:0xabc",
        market_id="100",
        condition_id="0xabc",
        outcomes=("Over", "Under"),
        token_ids=("over-token", "under-token"),
        captured_at_utc="2026-08-12T00:00:00Z",
        available_at_utc="2026-08-12T00:00:01Z",
        book_snapshot_ids={
            "over-token": "over-book",
            "under-token": "under-book",
        },
        capture_batch_id="batch-1",
    ).group_snapshot_id
