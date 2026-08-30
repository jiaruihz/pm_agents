from __future__ import annotations

from datetime import datetime

import pytest

from src.platform.market_data.capture_contract import (
    INVALID_COLLECTOR_CLOCK,
    classify_orderbook_clock,
    materialize_orderbook_capture,
)
from weather_clock_contract import (
    DECISION_CLOCK_ORDER,
    local_wall_time_to_utc,
    parse_utc,
    validate_clock_order,
)
from weather_data_feed.information_events import validate_information_event_lineage
from weather_data_feed.source_lineage import build_source_capture_lineage
from weather_model_evaluation.contracts import EventEnvelope


def test_causal_parsers_reject_naive_timestamps_project_wide() -> None:
    with pytest.raises(ValueError, match="include timezone"):
        parse_utc("2026-08-30T12:00:00", field="decision_ts_utc")
    with pytest.raises(ValueError, match="include timezone"):
        build_source_capture_lineage(
            producer="collector",
            producer_build="sha",
            capture_id="capture",
            batch_capture_id="batch",
            raw_payload_hash="raw",
            detected_at_utc="2026-08-30T12:00:00",
            available_at_utc="2026-08-30T12:00:01Z",
            lineage_status="collector_exact",
        )
    with pytest.raises(ValueError, match="include timezone"):
        EventEnvelope(
            event_id="event",
            city="Tokyo",
            target_date="2026-08-30",
            payload_kind="point",
            state_key="state",
            source="jma",
            available_at_utc="2026-08-30T12:00:00Z",
            observed_at_utc="2026-08-30T11:50:00Z",
            first_seen_at_utc="2026-08-30T12:00:00",
            material_state_change=True,
            revision_of_event_id=None,
            physical_ref={},
            payload={},
        )


def test_clock_order_compares_instants_not_iso_strings() -> None:
    # 09:00 +09 is 00:00Z and therefore precedes 00:30Z.  Lexicographic
    # comparison would incorrectly reject this valid lineage.
    validate_information_event_lineage(
        {
            "pit_lineage_class": "collector_exact",
            "first_seen_at_utc": "2026-08-30T09:00:00+09:00",
            "detected_at_utc": "2026-08-30T09:01:00+09:00",
            "available_at_utc": "2026-08-30T00:30:00Z",
            "original_first_seen_unknown": False,
        }
    )


def test_feature_and_post_decision_books_have_distinct_clock_roles() -> None:
    validate_clock_order(
        {
            "feature_book_available_at_utc": "2026-08-30T00:00:00Z",
            "decision_ts_utc": "2026-08-30T00:00:01Z",
            "post_decision_book_received_at_utc": "2026-08-30T00:00:02Z",
        },
        DECISION_CLOCK_ORDER,
    )
    with pytest.raises(ValueError, match="feature_book_available_at_utc"):
        validate_clock_order(
            {
                "feature_book_available_at_utc": "2026-08-30T00:00:02Z",
                "decision_ts_utc": "2026-08-30T00:00:01Z",
            },
            DECISION_CLOCK_ORDER,
        )


def test_local_wall_clock_uses_iana_dst_and_fails_on_ambiguous_time() -> None:
    summer = local_wall_time_to_utc(
        "2026-06-16T13:00", timezone_name="Europe/Helsinki"
    )
    winter = local_wall_time_to_utc(
        "2026-12-16T13:00", timezone_name="Europe/Helsinki"
    )
    assert summer.isoformat() == "2026-06-16T10:00:00+00:00"
    assert winter.isoformat() == "2026-12-16T11:00:00+00:00"
    with pytest.raises(ValueError, match="ambiguous"):
        local_wall_time_to_utc(
            "2026-10-25T03:30", timezone_name="Europe/Helsinki"
        )
    first = local_wall_time_to_utc(
        "2026-10-25T03:30", timezone_name="Europe/Helsinki", fold=0
    )
    second = local_wall_time_to_utc(
        "2026-10-25T03:30", timezone_name="Europe/Helsinki", fold=1
    )
    assert (second - first).total_seconds() == 3600


def test_orderbook_capture_clock_reversal_is_not_scorable() -> None:
    with pytest.raises(ValueError, match="request_started_at_utc"):
        materialize_orderbook_capture(
            token_id="token",
            raw_book={"timestamp": "1000", "bids": [], "asks": []},
            request_started_at_utc="2026-08-30T00:00:02Z",
            response_received_at_utc="2026-08-30T00:00:01Z",
            parsed_at_utc="2026-08-30T00:00:03Z",
            request_batch_capture_id="batch",
        )
    classified = classify_orderbook_clock(
        {
            "request_started_at_utc": "2026-08-30T00:00:02Z",
            "response_received_at_utc": "2026-08-30T00:00:01Z",
            "parsed_at_utc": "2026-08-30T00:00:03Z",
        }
    )
    assert classified["clock_lineage_status"] == INVALID_COLLECTOR_CLOCK
    assert classified["event_time_pit_scorable"] is False
