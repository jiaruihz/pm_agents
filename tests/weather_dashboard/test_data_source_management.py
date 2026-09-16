"""Tests for weather data-source management: schema, materializer, API health."""

import json
import sqlite3
from pathlib import Path

import pytest

from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from weather_dashboard.db.connection import apply_pragmas


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_pragmas(conn)
    apply_schema_canonical(conn)
    yield conn
    conn.close()


# ── Schema tests ────────────────────────────────────────────────────────────

class TestSchema:
    def test_profile_table_exists(self, db):
        row = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='weather_data_source_profile'"
        ).fetchone()
        assert row is not None

    def test_monitor_instance_table_exists(self, db):
        row = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='weather_data_monitor_instance'"
        ).fetchone()
        assert row is not None

    def test_health_table_does_not_exist(self, db):
        row = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='weather_data_source_health'"
        ).fetchone()
        assert row is None

    def test_profile_unique_constraint(self, db):
        db.execute(
            """INSERT INTO weather_data_source_profile
            (profile_id, feed_kind, city, source_key, station_or_feed, runway, source_role)
            VALUES ('p1', 'official_observation', 'Tokyo', 'aviationweather_metar', 'RJTT', '', 'primary')"""
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO weather_data_source_profile
                (profile_id, feed_kind, city, source_key, station_or_feed, runway, source_role)
                VALUES ('p2', 'official_observation', 'Tokyo', 'aviationweather_metar', 'RJTT', '', 'fallback')"""
            )

    def test_profile_unique_constraint_treats_null_runway_as_same_grain(self, db):
        db.execute(
            """INSERT INTO weather_data_source_profile
            (profile_id, feed_kind, city, source_key, station_or_feed, runway, source_role)
            VALUES ('p1', 'high_frequency_observation', 'Tokyo', 'jma_amedas', '44166', NULL, 'reference')"""
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO weather_data_source_profile
                (profile_id, feed_kind, city, source_key, station_or_feed, runway, source_role)
                VALUES ('p2', 'high_frequency_observation', 'Tokyo', 'jma_amedas', '44166', NULL, 'reference')"""
            )


# ── Materializer tests ─────────────────────────────────────────────────────

class TestMaterializer:
    def test_profiles_and_instances_written(self, db, tmp_path):
        from scripts.etl.materialize_weather_data_source_management import (
            build_official_observation_profiles,
            build_monitor_instances,
            upsert_monitor_instances,
            upsert_profiles,
        )

        profiles = build_official_observation_profiles()
        assert len(profiles) > 0
        upsert_profiles(db, profiles)
        db.commit()

        count = db.execute("SELECT COUNT(*) FROM weather_data_source_profile").fetchone()[0]
        assert count == len(profiles)

        runtime_root = tmp_path / "runtime"
        output = runtime_root / "output"
        hf_dir = output / "high_frequency_observations"
        hf_dir.mkdir(parents=True)
        latest = {
            "generated_at_utc": "2026-07-09T12:00:00+00:00",
            "producer": "test",
            "ok_sources": 5,
            "non_ok_sources": 0,
            "cities": ["Tokyo", "Seoul"],
            "records": [{"city": "Tokyo"}],
        }
        (hf_dir / "latest.json").write_text(json.dumps(latest))

        instances = build_monitor_instances(str(runtime_root))
        assert len(instances) > 0
        upsert_monitor_instances(db, instances)
        db.commit()

        mi_count = db.execute("SELECT COUNT(*) FROM weather_data_monitor_instance").fetchone()[0]
        assert mi_count == len(instances)

        hf_row = db.execute(
            "SELECT * FROM weather_data_monitor_instance WHERE monitor_instance_id='high_frequency_observations'"
        ).fetchone()
        assert hf_row is not None
        assert "Tokyo" in json.loads(hf_row["cities_json"])

    def test_monitor_instance_paths_include_state_and_prev_no_journals(self, db, tmp_path):
        from scripts.etl.materialize_weather_data_source_management import build_monitor_instances

        runtime_root = tmp_path / "runtime"
        prev_no_dir = runtime_root / "output" / "fast_source_prev_no_trial"
        prev_no_dir.mkdir(parents=True)
        for name in ("latest.json", "state.json"):
            (prev_no_dir / name).write_text("{}", encoding="utf-8")
        for name in ("events.jsonl", "opportunities.jsonl", "orders.jsonl"):
            (prev_no_dir / name).write_text("", encoding="utf-8")

        rows = {row["monitor_instance_id"]: row for row in build_monitor_instances(str(runtime_root))}
        prev_no = rows["fast_source_prev_no_trial"]
        assert prev_no["state_path"] == str(prev_no_dir / "state.json")
        assert set(json.loads(prev_no["journal_paths_json"])) == {
            str(prev_no_dir / "events.jsonl"),
            str(prev_no_dir / "opportunities.jsonl"),
            str(prev_no_dir / "orders.jsonl"),
        }

    def test_monitor_instance_uses_latest_dated_prev_no_opportunity_shard(self, db, tmp_path):
        from scripts.etl.materialize_weather_data_source_management import build_monitor_instances

        runtime_root = tmp_path / "runtime"
        prev_no_dir = runtime_root / "output" / "fast_source_prev_no_trial"
        oldest = prev_no_dir / "2026-08-09" / "opportunities.jsonl"
        newest = prev_no_dir / "2026-08-10" / "opportunities.jsonl"
        oldest.parent.mkdir(parents=True)
        newest.parent.mkdir(parents=True)
        oldest.write_text("{}\n", encoding="utf-8")
        newest.write_text("{}\n", encoding="utf-8")

        rows = {row["monitor_instance_id"]: row for row in build_monitor_instances(str(runtime_root))}
        journals = json.loads(rows["fast_source_prev_no_trial"]["journal_paths_json"])
        assert journals == [str(newest)]

    def test_idempotent(self, db, tmp_path):
        from scripts.etl.materialize_weather_data_source_management import (
            build_official_observation_profiles,
            build_monitor_instances,
            upsert_monitor_instances,
            upsert_profiles,
        )

        runtime_root = tmp_path / "runtime"
        (runtime_root / "output" / "source_events").mkdir(parents=True)

        profiles = build_official_observation_profiles()
        upsert_profiles(db, profiles)
        db.commit()
        count1 = db.execute("SELECT COUNT(*) FROM weather_data_source_profile").fetchone()[0]

        upsert_profiles(db, profiles)
        db.commit()
        count2 = db.execute("SELECT COUNT(*) FROM weather_data_source_profile").fetchone()[0]
        assert count1 == count2

        instances = build_monitor_instances(str(runtime_root))
        upsert_monitor_instances(db, instances)
        db.commit()
        mi1 = db.execute("SELECT COUNT(*) FROM weather_data_monitor_instance").fetchone()[0]

        upsert_monitor_instances(db, instances)
        db.commit()
        mi2 = db.execute("SELECT COUNT(*) FROM weather_data_monitor_instance").fetchone()[0]
        assert mi1 == mi2


# ── API dynamic health tests ───────────────────────────────────────────────

class TestDynamicHealth:
    def test_fresh_status(self, tmp_path):
        from weather_dashboard.api.routers.data_sources import compute_dynamic_health

        hf_dir = tmp_path / "hf"
        hf_dir.mkdir()
        from datetime import datetime, timezone
        now_str = datetime.now(timezone.utc).isoformat()
        latest = {
            "generated_at_utc": now_str,
            "ok_sources": 3,
            "non_ok_sources": 0,
            "cities": ["Tokyo", "Seoul"],
            "records": [{"city": "Tokyo"}, {"city": "Seoul"}],
        }
        latest_path = hf_dir / "latest.json"
        latest_path.write_text(json.dumps(latest))

        instances = [{
            "monitor_instance_id": "test_hf",
            "display_name": "Test HF",
            "feed_kind": "high_frequency_observation",
            "latest_path": str(latest_path),
            "scan_interval_sec": 60,
        }]
        health = compute_dynamic_health(instances)
        assert len(health) == 1
        assert health[0]["status"] == "fresh"
        assert health[0]["rows"] == 2
        assert "Tokyo" in health[0]["cities"]
        assert health[0]["sample_json"]["city"] == "Tokyo"

    def test_stale_status(self, tmp_path):
        from weather_dashboard.api.routers.data_sources import compute_dynamic_health

        hf_dir = tmp_path / "hf"
        hf_dir.mkdir()
        latest = {
            "generated_at_utc": "2020-01-01T00:00:00+00:00",
            "cities": [],
        }
        latest_path = hf_dir / "latest.json"
        latest_path.write_text(json.dumps(latest))

        instances = [{
            "monitor_instance_id": "test_stale",
            "display_name": "Test Stale",
            "feed_kind": "official_observation",
            "latest_path": str(latest_path),
            "scan_interval_sec": 120,
        }]
        health = compute_dynamic_health(instances)
        assert health[0]["status"] == "stale"

    def test_missing_latest(self):
        from weather_dashboard.api.routers.data_sources import compute_dynamic_health

        instances = [{
            "monitor_instance_id": "test_missing",
            "display_name": "Test Missing",
            "feed_kind": "forecast",
            "latest_path": "/nonexistent/latest.json",
            "scan_interval_sec": 300,
        }]
        health = compute_dynamic_health(instances)
        assert health[0]["status"] == "missing"

    def test_auth_required_status(self, tmp_path):
        from weather_dashboard.api.routers.data_sources import compute_dynamic_health

        latest_path = tmp_path / "latest.json"
        latest_path.write_text(json.dumps({
            "generated_at_utc": "2026-07-09T12:00:00+00:00",
            "source_statuses": {"cwa:Taipei": "auth_required"},
            "source_errors": {"cwa:Taipei": "CWA key missing"},
        }))
        health = compute_dynamic_health([{
            "monitor_instance_id": "test_auth",
            "display_name": "Test Auth",
            "feed_kind": "high_frequency_observation",
            "latest_path": str(latest_path),
            "scan_interval_sec": 60,
        }])
        assert health[0]["status"] == "auth_required"

    def test_fetch_failed_status(self, tmp_path):
        from weather_dashboard.api.routers.data_sources import compute_dynamic_health

        latest_path = tmp_path / "latest.json"
        latest_path.write_text(json.dumps({
            "generated_at_utc": "2026-07-09T12:00:00+00:00",
            "source_statuses": {"mgm:Istanbul": "error"},
            "source_errors": {"mgm:Istanbul": "HTTP 500"},
        }))
        health = compute_dynamic_health([{
            "monitor_instance_id": "test_failed",
            "display_name": "Test Failed",
            "feed_kind": "high_frequency_observation",
            "latest_path": str(latest_path),
            "scan_interval_sec": 60,
        }])
        assert health[0]["status"] == "fetch_failed"

    def test_no_latest_path(self):
        from weather_dashboard.api.routers.data_sources import compute_dynamic_health

        instances = [{
            "monitor_instance_id": "test_none",
            "display_name": "Test None",
            "feed_kind": "orderbook",
            "latest_path": None,
            "scan_interval_sec": 60,
        }]
        health = compute_dynamic_health(instances)
        assert health[0]["status"] == "missing"
