from __future__ import annotations

import pytest

from weather_data_feed.source_lineage import build_source_capture_lineage, capture_batch_id


def test_capture_batch_id_is_order_independent_for_raw_hashes() -> None:
    left = capture_batch_id(
        producer="collector",
        captured_at_utc="2026-08-05T00:00:00Z",
        scope={"cities": ["A", "B"]},
        raw_payload_hashes=["b", "a"],
    )
    right = capture_batch_id(
        producer="collector",
        captured_at_utc="2026-08-05T00:00:00Z",
        scope={"cities": ["A", "B"]},
        raw_payload_hashes=["a", "b"],
    )
    assert left == right
    assert len(left) == 64


def test_source_capture_rejects_clock_reversal() -> None:
    with pytest.raises(ValueError, match="fetch_start"):
        build_source_capture_lineage(
            producer="collector",
            producer_build="sha",
            capture_id="capture",
            batch_capture_id="batch",
            raw_payload_hash="raw",
            source_fetch_start_utc="2026-08-05T00:00:02Z",
            source_fetch_end_utc="2026-08-05T00:00:01Z",
            detected_at_utc="2026-08-05T00:00:03Z",
            first_seen_at_utc="2026-08-05T00:00:03Z",
            available_at_utc="2026-08-05T00:00:04Z",
            lineage_status="collector_exact",
        )
