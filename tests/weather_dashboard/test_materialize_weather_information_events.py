from __future__ import annotations

import json
import sqlite3

from scripts.etl import materialize_weather_information_events as materialize
from weather_data_feed.information_events import build_information_event
from weather_data_feed.jsonl_partitions import dated_jsonl_paths


def test_partitioned_files_ignore_root_aggregate_and_sibling_datasets(tmp_path):
    root = tmp_path / "forecast_enrichment"
    dated = root / "2026-08-09"
    dated.mkdir(parents=True)
    shard = dated / "forecast_enrichment.jsonl"
    shard.write_text("{}\n", encoding="utf-8")
    (root / "forecast_enrichment.jsonl").write_text("legacy aggregate\n", encoding="utf-8")
    (dated / "forecast_versions.jsonl").write_text("sibling dataset\n", encoding="utf-8")

    assert list(
        dated_jsonl_paths(
            root, filename="forecast_enrichment.jsonl", allow_missing=False
        )
    ) == [shard]


def test_partitioned_files_preserve_explicit_file_compatibility(tmp_path):
    aggregate = tmp_path / "high_frequency_observations.jsonl"
    aggregate.write_text("{}\n", encoding="utf-8")

    assert list(
        dated_jsonl_paths(
            aggregate,
            filename="high_frequency_observations.jsonl",
            allow_missing=False,
        )
    ) == [aggregate]


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
