from __future__ import annotations

from weather_data_feed_service.forecast_enrichment import (
    _annotate_taf_information_events,
)
from weather_data_feed_service.source_events import annotate_information_events


def test_observation_content_key_crosses_source_and_local_date_without_changing_identity_group() -> None:
    base = {
        "status": "ok",
        "city": "Atlanta",
        "station": "KATL",
        "source_report_ts_utc": "2026-07-28T23:55:00Z",
        "local_detect_ts_utc": "2026-07-29T00:00:03Z",
        "temp_c": 25.0,
        "raw_metar": "METAR KATL 282355Z 00000KT 25/20",
    }
    awc = annotate_information_events(
        [{**base, "source": "aviationweather_metar", "target_date": "2026-07-28"}],
        {},
        raw_source_path="sources.jsonl",
        available_at_utc="2026-07-29T00:00:04Z",
    )[0]
    iem = annotate_information_events(
        [{**base, "source": "iem", "target_date": "2026-07-29"}],
        {},
        raw_source_path="sources.jsonl",
        available_at_utc="2026-07-29T00:00:05Z",
    )[0]

    assert awc["content_key"] == iem["content_key"]
    assert awc["information_event_id"] != iem["information_event_id"]


def test_taf_changed_payload_with_same_validity_is_linked_revision(tmp_path) -> None:
    def row(raw_taf: str, fetched: str) -> dict:
        return {
            "city": "Atlanta",
            "station": "KATL",
            "taf": {
                "result": {"status": "ok", "fetched_at_utc": fetched},
                "payload": {
                    "issue_time": "2026-07-28T12:00:00Z",
                    "valid_time_from": 1785240000,
                    "valid_time_to": 1785326400,
                    "raw_taf": raw_taf,
                },
            },
        }

    first = _annotate_taf_information_events(
        [row("TAF KATL 281200Z 2812/2912 CAVOK", "2026-07-28T04:00:03Z")],
        tmp_path,
        raw_source_path=tmp_path / "2026-07-28" / "forecast_enrichment.jsonl",
    )[0]["taf"]["information_event"]
    revision = _annotate_taf_information_events(
        [row("TAF AMD KATL 281200Z 2812/2912 TSRA", "2026-07-28T04:05:03Z")],
        tmp_path,
        raw_source_path=tmp_path / "2026-07-28" / "forecast_enrichment.jsonl",
    )[0]["taf"]["information_event"]

    assert revision["event_role"] == "revision"
    assert revision["revision_of_event_id"] == first["information_event_id"]
