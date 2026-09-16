from __future__ import annotations

import pytest

from weather_data_feed.information_events import build_information_event


def _event(**overrides: object) -> dict:
    payload = {
        "event_kind": "observation",
        "event_role": "new_content",
        "source": "aviationweather",
        "city": "Atlanta",
        "station_id": "KATL",
        "provider_item_id": "KATL-20260728-1200",
        "content_key": "KATL|2026-07-28T12:00:00Z",
        "normalized_payload": {"raw_metar": "METAR KATL 281200Z 00000KT 25/20"},
        "source_event_ts_utc": "2026-07-28T12:00:00Z",
        "detected_at_utc": "2026-07-28T12:00:03Z",
        "first_seen_at_utc": "2026-07-28T12:00:03Z",
        "available_at_utc": "2026-07-28T12:00:04Z",
        "pit_lineage_class": "collector_exact",
        "raw_source_path": "output/source_events/2026-07-28/sources.jsonl",
        "raw_row_hash": "raw-a",
    }
    payload.update(overrides)
    return build_information_event(**payload)


def test_duplicate_poll_and_restart_keep_one_immutable_identity() -> None:
    first = _event()
    replay = _event(
        detected_at_utc="2026-07-28T12:02:03Z",
        first_seen_at_utc="2026-07-28T12:00:03Z",
        available_at_utc="2026-07-28T12:02:04Z",
        raw_source_path="output/source_events/2026-07-28/restarted-sources.jsonl",
        raw_row_hash="raw-replay",
    )

    assert first["information_event_id"] == replay["information_event_id"]
    assert first["payload_hash"] == replay["payload_hash"]


def test_revision_changes_event_identity_and_links_prior_version() -> None:
    first = _event()
    revision = _event(
        event_role="revision",
        revision_of_event_id=first["information_event_id"],
        normalized_payload={"raw_metar": "METAR KATL 281200Z 00000KT 26/20 COR"},
        detected_at_utc="2026-07-28T12:05:00Z",
        first_seen_at_utc="2026-07-28T12:05:00Z",
        available_at_utc="2026-07-28T12:05:01Z",
    )

    assert revision["information_event_id"] != first["information_event_id"]
    assert revision["revision_of_event_id"] == first["information_event_id"]


def test_cross_source_delivery_does_not_collapse_source_specific_identity() -> None:
    awc = _event()
    iem = _event(source="iem", provider_item_id=None)

    assert awc["content_key"] == iem["content_key"]
    assert awc["information_event_id"] != iem["information_event_id"]


def test_late_backfill_cannot_claim_provider_time_as_exact_first_seen() -> None:
    with pytest.raises(ValueError, match="late backfill"):
        _event(
            pit_lineage_class="late_backfill_first_seen_unknown",
            original_first_seen_unknown=True,
            first_seen_at_utc="2026-07-28T12:00:00Z",
            detected_at_utc="2026-07-28T16:00:00Z",
            available_at_utc="2026-07-28T16:00:01Z",
        )


def test_same_second_distinct_payloads_remain_distinct_events() -> None:
    first = _event()
    second = _event(normalized_payload={"raw_metar": "METAR KATL 281200Z 00000KT 27/20"})

    assert first["information_event_id"] != second["information_event_id"]
