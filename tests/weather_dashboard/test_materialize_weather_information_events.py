from __future__ import annotations

import json
import sqlite3

from scripts.etl import materialize_weather_information_events as materialize
from weather_data_feed.information_events import build_information_event


def test_raw_material_event_extracts_flat_and_nested_taf_and_is_rebuild_idempotent(tmp_path):
    observation = build_information_event(
        event_kind="observation", event_role="new_content", source="aviationweather", city="Atlanta",
        station_id="KATL", provider_item_id="KATL-1200", content_key="KATL|1200",
        normalized_payload={"raw_metar": "METAR"}, detected_at_utc="2026-07-28T12:00:03Z",
        first_seen_at_utc="2026-07-28T12:00:03Z", available_at_utc="2026-07-28T12:00:04Z",
        pit_lineage_class="collector_exact",
    )
    taf = build_information_event(
        event_kind="taf", event_role="new_content", source="aviationweather_taf", city="Atlanta",
        station_id="KATL", provider_item_id="TAF-1200", content_key="KATL|TAF-1200",
        normalized_payload={"raw_taf": "TAF"}, detected_at_utc="2026-07-28T12:00:03Z",
        first_seen_at_utc="2026-07-28T12:00:03Z", available_at_utc="2026-07-28T12:00:04Z",
        pit_lineage_class="collector_exact",
    )
    raw = tmp_path / "raw.jsonl"
    raw.write_text("\n".join([json.dumps({**observation, "information_event_status": "material"}), json.dumps({"taf": {"information_event_status": "material", "information_event": taf}})]) + "\n")
    db = tmp_path / "weather.db"

    assert materialize.main(["--db", str(db), "--source-events", str(raw)]) == 0
    assert materialize.main(["--db", str(db), "--source-events", str(raw)]) == 0
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT count(*) FROM weather_information_events").fetchone()[0] == 2
