from __future__ import annotations

import json
import sqlite3

from scripts.etl import materialize_weather_information_events as materialize
from weather_data_feed_service.high_frequency_observations import (
    INFORMATION_EVENT_STATE_KEY,
    annotate_information_events,
)


def _row(temp_c: float, *, first_seen: str | None = "2026-07-29T08:01:02+00:00") -> dict:
    return {
        "schema_version": "weather_high_frequency_observation_v1",
        "source": "fmi",
        "source_status": "ok",
        "city": "Helsinki",
        "station": "100968",
        "target_date": "2026-07-29",
        "observation_time_utc": "2026-07-29T08:00:00+00:00",
        "source_first_seen_at_utc": first_seen,
        "local_detect_ts_utc": first_seen or "2026-07-29T09:00:00+00:00",
        "fetched_at_utc": first_seen or "2026-07-29T09:00:00+00:00",
        "temp_c": temp_c,
        "temp_f": temp_c * 9.0 / 5.0 + 32.0,
        "wind_speed_kt": 4.0,
        "payload_hash": f"temp-{temp_c}",
    }


def test_high_frequency_event_keeps_exact_first_seen_across_duplicate_poll() -> None:
    state: dict = {}
    first = annotate_information_events(
        [_row(20.0)],
        state,
        raw_source_path="high_frequency_observations.jsonl",
        available_at_utc="2026-07-29T08:01:03+00:00",
    )[0]
    duplicate = annotate_information_events(
        [_row(20.0)],
        state,
        raw_source_path="high_frequency_observations.jsonl",
        available_at_utc="2026-07-29T08:06:03+00:00",
    )[0]

    assert first["pit_lineage_class"] == "collector_exact"
    assert first["first_seen_at_utc"] == "2026-07-29T08:01:02+00:00"
    assert duplicate["information_event_id"] == first["information_event_id"]
    assert duplicate["first_seen_at_utc"] == first["first_seen_at_utc"]
    assert duplicate["information_event_status"] == "duplicate"
    assert not duplicate["material_state_change"]
    assert state[INFORMATION_EVENT_STATE_KEY]["first_seen_by_id"]


def test_changed_temperature_is_linked_revision() -> None:
    state: dict = {}
    first = annotate_information_events(
        [_row(20.0)],
        state,
        raw_source_path="high_frequency_observations.jsonl",
        available_at_utc="2026-07-29T08:01:03+00:00",
    )[0]
    revision = annotate_information_events(
        [_row(20.2, first_seen="2026-07-29T08:02:02+00:00")],
        state,
        raw_source_path="high_frequency_observations.jsonl",
        available_at_utc="2026-07-29T08:02:03+00:00",
    )[0]

    assert revision["event_role"] == "revision"
    assert revision["revision_of_event_id"] == first["information_event_id"]
    assert revision["information_event_status"] == "material"


def test_missing_first_seen_remains_late_backfill_unknown() -> None:
    late = annotate_information_events(
        [_row(20.0, first_seen=None)],
        {},
        raw_source_path="archive.jsonl",
        available_at_utc="2026-07-29T09:00:01+00:00",
    )[0]

    assert late["pit_lineage_class"] == "late_backfill_first_seen_unknown"
    assert late["first_seen_at_utc"] is None
    assert late["original_first_seen_unknown"]


def test_historical_exact_row_rebuilds_without_clock_substitution(tmp_path) -> None:
    exact = _row(20.0)
    late = _row(19.0, first_seen=None)
    late["observation_time_utc"] = "2026-07-29T07:50:00+00:00"
    raw = tmp_path / "high_frequency_observations.jsonl"
    raw.write_text(
        json.dumps(exact) + "\n" + json.dumps(late) + "\n",
        encoding="utf-8",
    )
    db = tmp_path / "weather.db"

    assert (
        materialize.main(
            [
                "--db",
                str(db),
                "--high-frequency-observations",
                str(raw),
            ]
        )
        == 0
    )
    conn = sqlite3.connect(db)
    rows = conn.execute(
        """
        SELECT source, first_seen_at_utc, pit_lineage_class,
               original_first_seen_unknown
        FROM weather_information_events
        ORDER BY source_event_ts_utc
        """
    ).fetchall()
    conn.close()

    assert rows[0] == ("fmi", None, "late_backfill_first_seen_unknown", 1)
    assert rows[1] == (
        "fmi",
        "2026-07-29T08:01:02+00:00",
        "collector_exact",
        0,
    )
