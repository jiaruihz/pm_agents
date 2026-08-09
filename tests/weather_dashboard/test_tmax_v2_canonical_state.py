import json
import sqlite3

import pytest

from scripts.etl import materialize_tmax_v2_canonical_state as mat
from weather_dashboard.db.apply_schema_canonical import (
    _ensure_tmax_v2_lineage_columns,
    apply_schema_canonical,
)


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_schema_canonical(conn)
    return conn


def _record(bracket: str, *, city="Testville", target_date="2026-07-11"):
    return {
        "city": city,
        "target_date": target_date,
        "event_date": target_date,
        "event_slug": f"highest-temperature-in-testville-on-{target_date}",
        "bracket": bracket,
        "question": f"Will the highest temperature in Testville be {bracket}°C?",
        "unit": "C",
        "settlement_source_class": "default_wu_station_by_rules",
        "timezone_name": "Europe/Amsterdam",
        "forecast_utc_offset_seconds": 7200,
        "condition_id": f"condition-{bracket}",
        "market_id": f"market-{bracket}",
        "yes_token_id": f"yes-{bracket}",
        "no_token_id": f"no-{bracket}",
        "yes_book_status": "ok",
        "no_book_status": "ok",
        "yes_best_bid": 0.4,
        "yes_best_ask": 0.42,
        "yes_bid_size": 11,
        "yes_ask_size": 12,
        "yes_depth_bid_5c": 31,
        "yes_depth_ask_5c": 32,
        "yes_depth_bid_10c": 41,
        "yes_depth_ask_10c": 42,
        "no_best_bid": 0.58,
        "no_best_ask": 0.6,
        "no_bid_size": 21,
        "no_ask_size": 22,
        "no_depth_bid_5c": 51,
        "no_depth_ask_5c": 52,
        "no_depth_bid_10c": 61,
        "no_depth_ask_10c": 62,
        "yes_book_fetched_at_utc": "2026-07-11T11:59:59Z",
        "no_book_fetched_at_utc": "2026-07-11T11:59:59Z",
    }


def _insert_ladder(conn, *, snapshot_id="ladder", decision="2026-07-11T12:00:00Z"):
    conn.execute(
        """
        INSERT INTO tmax_v2_ladder_snapshots (
            ladder_snapshot_id, source_system, source_path, source_snapshot_ts_utc,
            available_at_utc, source_payload_hash, city, target_date, event_identity,
            absolute_ladder_signature, rung_count, complete_rung_count,
            completeness_status, lineage_status
        ) VALUES (?, 'test', 'snapshot.json', ?, ?, ?, 'Testville', '2026-07-11',
                  'event', 'signature', 2, 2, 'complete', 'pit_verified_capture')
        """,
        (snapshot_id, decision, decision, f"payload-{snapshot_id}"),
    )


def _insert_forecast(conn, capture_id, available):
    conn.execute(
        """
        INSERT INTO tmax_v2_forecast_captures (
            forecast_capture_id, source_system, source_path, source_row_hash,
            snapshot_ts_utc, available_at_utc, city, target_date, hourly_curve_json,
            lineage_status
        ) VALUES (?, 'test', ?, ?, ?, ?, 'Testville', '2026-07-11', '[{"temperature_f":70}]',
                  'pit_verified_capture')
        """,
        (capture_id, f"{capture_id}.jsonl", f"hash-{capture_id}", available, available),
    )


def _insert_observation(
    conn,
    event_id,
    observed,
    available,
    status="pit_verified_first_seen",
    *,
    source_observation_id=None,
    temp_f=70,
    source_system="test",
    source_kind="native",
):
    conn.execute(
        """
        INSERT INTO tmax_v2_observation_event_lineage (
            tmax_v2_observation_id, source_observation_id, source_system, city,
            target_date, obs_ts_utc, temp_f, first_seen_at_utc, available_at_utc,
            source_kind, lineage_status
        ) VALUES (?, ?, ?, 'Testville', '2026-07-11', ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            source_observation_id or f"source-{event_id}",
            source_system,
            observed,
            temp_f,
            available if status == "pit_verified_first_seen" else None,
            available if status == "pit_verified_first_seen" else None,
            source_kind,
            status,
        ),
    )


def test_same_snapshot_persists_all_rungs_and_direct_quotes(tmp_path):
    snapshot_dir = tmp_path / "paper_snapshots"
    snapshot_dir.mkdir()
    (snapshot_dir / "snapshot.json").write_text(
        json.dumps({"ts_utc": "2026-07-11T12:00:00Z", "records": [_record("20"), _record("21")]}),
        encoding="utf-8",
    )
    meta, records = next(mat.iter_ladder_rows(snapshot_dir, "2026-07-11", "2026-07-11"))
    ladder, rungs = mat.make_ladder_payload(meta, records)

    assert ladder["completeness_status"] == "complete"
    assert [rung["absolute_bracket_identity"] for rung in rungs] == ["20", "21"]
    assert rungs[0]["yes_direct_bid"] == 0.4
    assert rungs[0]["no_direct_ask"] == 0.6
    assert ladder["market_unit"] == "C"
    assert ladder["settlement_source_class"] == "default_wu_station_by_rules"
    assert ladder["market_timezone"] == "Europe/Amsterdam"
    assert ladder["market_utc_offset_seconds"] == 7200
    assert rungs[0]["question"] == "Will the highest temperature in Testville be 20°C?"


def test_embedded_snapshot_observation_is_asof_eligible():
    conn = _conn()
    records = [_record("20"), _record("21")]
    for record in records:
        record.update(
            {
                "metar_latest_temp_f": 70,
                "metar_latest_ts_utc": "2026-07-11T11:55:00Z",
                "metar_icao": "KTST",
                "live_observation_source": "aviationweather_metar",
            }
        )
    meta = {
        "source_system": "test_snapshot",
        "source_path": "snapshot.json",
        "source_snapshot_ts_utc": "2026-07-11T12:00:00Z",
        "available_at_utc": "2026-07-11T12:00:00Z",
        "city": "Testville",
        "target_date": "2026-07-11",
        "event_slug": "event",
        "event_identity": "event",
    }
    snapshot, rungs = mat.make_ladder_payload(meta, records)
    embedded = mat.embedded_snapshot_observation_rows(snapshot, records)

    assert len(embedded) == 1
    assert embedded[0]["source_kind"] == "embedded_snapshot_capture"
    assert embedded[0]["first_seen_at_utc"] == "2026-07-11T12:00:00Z"
    assert embedded[0]["obs_ts_utc"] == "2026-07-11T11:55:00Z"
    assert embedded[0]["station_id"] == "KTST"
    assert embedded[0]["icao"] == "KTST"
    assert embedded[0]["feed_identity"] == "aviationweather_metar"
    mat.insert_rows(conn, "tmax_v2_ladder_snapshots", [snapshot], False)
    mat.insert_rows(conn, "tmax_v2_ladder_rung_quotes", rungs, False)
    mat.insert_rows(conn, "tmax_v2_observation_event_lineage", embedded, False)
    _insert_forecast(conn, "forecast-old", "2026-07-11T11:00:00Z")

    inserted, statuses = mat.materialize_states(conn, "2026-07-11", "2026-07-11", False)
    effective = conn.execute("SELECT * FROM tmax_v2_canonical_state_effective").fetchone()

    assert inserted == {"base_states": 1, "state_revisions": 1}
    assert statuses["pit_verified"] == 1
    assert effective["observation_event_id"] == embedded[0]["tmax_v2_observation_id"]
    assert effective["pit_status"] == "pit_verified"


def test_embedded_exact_value_dedupes_to_earliest_capture():
    records = [_record("20")]
    records[0].update(
        {
            "metar_latest_temp_f": 70,
            "metar_latest_ts_utc": "2026-07-11T11:55:00Z",
            "metar_icao": "KTST",
            "live_observation_source": "aviationweather_metar",
        }
    )
    base_meta = {
        "source_system": "test_snapshot",
        "source_path": "snapshot.json",
        "available_at_utc": "2026-07-11T12:00:00Z",
        "city": "Testville",
        "target_date": "2026-07-11",
        "event_slug": "event",
        "event_identity": "event",
    }
    early, _ = mat.make_ladder_payload(
        {**base_meta, "source_snapshot_ts_utc": "2026-07-11T12:00:00Z"}, records
    )
    late, _ = mat.make_ladder_payload(
        {**base_meta, "source_path": "snapshot-late.json", "source_snapshot_ts_utc": "2026-07-11T12:05:00Z"},
        records,
    )
    early_obs = mat.embedded_snapshot_observation_rows(early, records)[0]
    late_obs = mat.embedded_snapshot_observation_rows(late, records)[0]

    deduped = mat.dedupe_embedded_snapshot_observations([late_obs, early_obs])

    assert early_obs["source_observation_id"] == late_obs["source_observation_id"]
    assert len(deduped) == 1
    assert deduped[0]["available_at_utc"] == "2026-07-11T12:00:00Z"


def test_forecast_capture_uses_raw_availability_and_honest_run_time(tmp_path):
    curve_dir = tmp_path / "forecast_hourly_curves"
    curve_dir.mkdir()
    row = {
        "snapshot_ts_utc": "2026-07-11T04:01:25Z",
        "available_at_utc": "2026-07-11T04:06:33.130603Z",
        "forecast_run_ts_utc": None,
        "city": "Testville",
        "target_date": "2026-07-11",
        "forecast_source": "open_meteo_live_gfs",
        "forecast_model": "gfs",
        "forecast_values_hash": "hash-v2",
        "forecast_timezone": "Europe/Amsterdam",
        "forecast_utc_offset_seconds": 7200,
        "available_at_basis": "collector_publish_started_before_final_write_and_atomic_link",
        "forecast_first_seen_utc": "2026-07-11T04:03:00Z",
        "forecast_first_seen_basis": "collector_exact_values_hash",
        "forecast_first_seen_source": "current_capture_detected_at",
        "hourly_curve": [{"time_local": "2026-07-11T12:00", "temperature_f": 70}],
    }
    (curve_dir / "curve.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

    captures = list(mat.iter_forecast_captures(curve_dir, "2026-07-11", "2026-07-11"))

    assert len(captures) == 1
    assert captures[0]["snapshot_ts_utc"] == "2026-07-11T04:01:25Z"
    assert captures[0]["available_at_utc"] == "2026-07-11T04:06:33.130603Z"
    assert captures[0]["forecast_run_at_utc"] is None
    assert captures[0]["lineage_status"] == "pit_verified_capture"
    assert captures[0]["forecast_timezone"] == "Europe/Amsterdam"
    assert captures[0]["forecast_utc_offset_seconds"] == 7200
    assert captures[0]["available_at_basis"] == "collector_publish_started_before_final_write_and_atomic_link"
    assert captures[0]["forecast_first_seen_source"] == "current_capture_detected_at"
    normalized = json.loads(captures[0]["normalized_hourly_curve_json"])
    assert normalized[0]["valid_time_local"] == "2026-07-11T12:00"
    assert normalized[0]["valid_time_utc"] == "2026-07-11T10:00:00Z"
    assert captures[0]["curve_time_lineage_status"] == "local_and_utc_verified"

    conn = _conn()
    mat.insert_rows(conn, "tmax_v2_forecast_captures", captures, False)
    assert mat.selected_forecast(conn, "Testville", "2026-07-11", "2026-07-11T04:05:00Z") is None
    selected = mat.selected_forecast(conn, "Testville", "2026-07-11", "2026-07-11T04:07:00Z")
    assert selected is not None
    assert selected["available_at_utc"] == "2026-07-11T04:06:33.130603Z"


def test_forecast_snapshot_without_availability_is_research_only(tmp_path):
    curve_dir = tmp_path / "forecast_hourly_curves"
    curve_dir.mkdir()
    row = {
        "snapshot_ts_utc": "2026-07-11T04:01:25Z",
        "forecast_run_at_utc": None,
        "forecast_run_ts_utc": "2026-07-11T00:00:00Z",
        "city": "Testville",
        "target_date": "2026-07-11",
        "hourly_curve": [{"time_local": "2026-07-11T12:00", "temperature_f": 70}],
    }
    (curve_dir / "curve.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

    capture = list(mat.iter_forecast_captures(curve_dir, "2026-07-11", "2026-07-11"))[0]

    assert capture["available_at_utc"] is None
    assert capture["lineage_status"] == "research_only_unknown_available_at"
    assert capture["forecast_run_at_utc"] == "2026-07-11T00:00:00Z"
    assert capture["curve_time_lineage_status"] == "local_only_missing_timezone_lineage"
    assert "missing_timezone_and_utc_offset" in capture["curve_time_missing_reason"]


def test_existing_v15_tables_get_additive_lineage_columns_idempotently():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE tmax_v2_ladder_snapshots (ladder_snapshot_id TEXT PRIMARY KEY);
        CREATE TABLE tmax_v2_ladder_rung_quotes (rung_quote_id TEXT PRIMARY KEY);
        CREATE TABLE tmax_v2_forecast_captures (forecast_capture_id TEXT PRIMARY KEY);
        CREATE TABLE tmax_v2_observation_event_lineage (tmax_v2_observation_id TEXT PRIMARY KEY);
        """
    )

    _ensure_tmax_v2_lineage_columns(conn)
    _ensure_tmax_v2_lineage_columns(conn)

    assert "market_unit" in {row[1] for row in conn.execute("PRAGMA table_info(tmax_v2_ladder_snapshots)")}
    assert "question" in {row[1] for row in conn.execute("PRAGMA table_info(tmax_v2_ladder_rung_quotes)")}
    assert "normalized_hourly_curve_json" in {
        row[1] for row in conn.execute("PRAGMA table_info(tmax_v2_forecast_captures)")
    }
    assert {"station_id", "icao", "feed_identity"} <= {
        row[1] for row in conn.execute("PRAGMA table_info(tmax_v2_observation_event_lineage)")
    }


def test_metadata_extensions_backfill_immutable_base_rows():
    conn = _conn()
    records = [_record("20")]
    meta = {
        "source_system": "test_snapshot",
        "source_path": "snapshot.json",
        "source_snapshot_ts_utc": "2026-07-11T12:00:00Z",
        "available_at_utc": "2026-07-11T12:00:00Z",
        "city": "Testville",
        "target_date": "2026-07-11",
        "event_slug": "event",
        "event_identity": "event",
    }
    snapshot, rungs = mat.make_ladder_payload(meta, records)
    legacy_snapshot = {
        key: value
        for key, value in snapshot.items()
        if key not in {
            "market_unit",
            "settlement_source_class",
            "market_timezone",
            "market_utc_offset_seconds",
            "market_metadata_source_json",
            "market_metadata_missing_reason",
        }
    }
    legacy_rung = {key: value for key, value in rungs[0].items() if key != "question"}
    mat.insert_rows(conn, "tmax_v2_ladder_snapshots", [legacy_snapshot], False)
    mat.insert_rows(conn, "tmax_v2_ladder_rung_quotes", [legacy_rung], False)

    assert mat.insert_rows(conn, "tmax_v2_ladder_snapshot_metadata", mat.ladder_metadata_rows([snapshot]), False) == 1
    assert mat.insert_rows(conn, "tmax_v2_ladder_rung_metadata", mat.rung_metadata_rows(rungs), False) == 1
    assert conn.execute("SELECT market_unit FROM tmax_v2_ladder_snapshot_metadata").fetchone()[0] == "C"
    assert "20°C" in conn.execute("SELECT question FROM tmax_v2_ladder_rung_metadata").fetchone()[0]
    assert conn.execute("SELECT effective_market_unit FROM tmax_v2_ladder_snapshots_enriched").fetchone()[0] == "C"
    assert "20°C" in conn.execute("SELECT effective_question FROM tmax_v2_ladder_rung_quotes_enriched").fetchone()[0]


def test_existing_observation_identity_backfills_from_exact_snapshot_value():
    conn = _conn()
    _insert_observation(
        conn,
        "legacy-observation-id",
        "2026-07-11T11:30:00Z",
        "2026-07-11T11:31:00Z",
        source_system="weather_data_feed_embedded_snapshot_observation",
        temp_f=70,
    )
    current = {
        "tmax_v2_observation_id": "current-exact-id",
        "city": "Testville",
        "target_date": "2026-07-11",
        "obs_ts_utc": "2026-07-11T11:30:00Z",
        "temp_f": 70,
        "station_id": "KTST",
        "icao": "KTST",
        "feed_identity": "aviationweather_metar",
        "identity_missing_reason": None,
    }

    inputs = mat.observation_metadata_backfill_inputs(
        conn, [current], "2026-07-11", "2026-07-11"
    )
    legacy = next(row for row in inputs if row["tmax_v2_observation_id"] == "legacy-observation-id")

    assert legacy["station_id"] == "KTST"
    assert legacy["icao"] == "KTST"
    assert legacy["feed_identity"] == "aviationweather_metar"
    assert legacy["identity_missing_reason"] is None


def test_asof_rejects_future_observation_and_forecast():
    conn = _conn()
    _insert_ladder(conn)
    _insert_forecast(conn, "forecast-old", "2026-07-11T11:00:00Z")
    _insert_forecast(conn, "forecast-future", "2026-07-11T13:00:00Z")
    _insert_observation(conn, "obs-old", "2026-07-11T11:30:00Z", "2026-07-11T11:31:00Z")
    _insert_observation(conn, "obs-future", "2026-07-11T12:30:00Z", "2026-07-11T12:31:00Z")

    inserted, statuses = mat.materialize_states(conn, "2026-07-11", "2026-07-11", False)
    state = conn.execute("SELECT * FROM tmax_v2_canonical_states").fetchone()

    assert inserted == {"base_states": 1, "state_revisions": 1}
    assert statuses["pit_verified"] == 1
    assert state["forecast_capture_id"] == "forecast-old"
    assert state["observation_event_id"] == "obs-old"


def test_repeated_embedded_observation_uses_earliest_exact_value_first_seen():
    conn = _conn()
    _insert_ladder(conn, decision="2026-07-11T12:00:00Z")
    _insert_observation(
        conn,
        "obs-late-copy",
        "2026-07-11T11:30:00Z",
        "2026-07-11T11:50:00Z",
        source_observation_id="same-exact-value",
    )
    _insert_observation(
        conn,
        "obs-first-copy",
        "2026-07-11T11:30:00Z",
        "2026-07-11T11:31:00Z",
        source_observation_id="same-exact-value",
    )

    selected = mat.selected_observation(conn, "Testville", "2026-07-11", "2026-07-11T12:00:00Z")

    assert selected is not None
    assert selected["tmax_v2_observation_id"] == "obs-first-copy"
    assert selected["available_at_utc"] == "2026-07-11T11:31:00Z"


def test_same_observation_timestamp_prefers_later_value_revision():
    conn = _conn()
    _insert_ladder(conn, decision="2026-07-11T12:00:00Z")
    _insert_observation(
        conn,
        "obs-original",
        "2026-07-11T11:30:00Z",
        "2026-07-11T11:31:00Z",
        source_observation_id="original-value",
        temp_f=70,
    )
    _insert_observation(
        conn,
        "obs-revised",
        "2026-07-11T11:30:00Z",
        "2026-07-11T11:40:00Z",
        source_observation_id="revised-value",
        temp_f=71,
    )

    selected = mat.selected_observation(conn, "Testville", "2026-07-11", "2026-07-11T12:00:00Z")

    assert selected is not None
    assert selected["tmax_v2_observation_id"] == "obs-revised"
    assert selected["temp_f"] == 71


def test_same_timestamp_prefers_embedded_snapshot_lineage_over_archive_compat():
    conn = _conn()
    _insert_ladder(conn, decision="2026-07-11T12:00:00Z")
    _insert_observation(
        conn,
        "obs-archive",
        "2026-07-11T11:30:00Z",
        "2026-07-11T11:45:00Z",
        source_observation_id="archive-value",
        source_system="archive_compat",
        temp_f=70,
    )
    _insert_observation(
        conn,
        "obs-embedded",
        "2026-07-11T11:30:00Z",
        "2026-07-11T11:31:00Z",
        source_observation_id="embedded-value",
        source_system="aviationweather_metar",
        source_kind="embedded_snapshot_capture",
        temp_f=70,
    )

    selected = mat.selected_observation(conn, "Testville", "2026-07-11", "2026-07-11T12:00:00Z")

    assert selected is not None
    assert selected["tmax_v2_observation_id"] == "obs-embedded"


def test_materializer_is_idempotent_and_does_not_mix_snapshots(tmp_path):
    snapshot_dir = tmp_path / "paper_snapshots"
    snapshot_dir.mkdir()
    for name, brackets in (("one", ["20", "21"]), ("two", ["20", "22"])):
        (snapshot_dir / f"{name}.json").write_text(
            json.dumps({"ts_utc": "2026-07-11T12:00:00Z", "records": [_record(bracket) for bracket in brackets]}),
            encoding="utf-8",
        )
    conn = _conn()
    snapshots, rungs = [], []
    for meta, records in mat.iter_ladder_rows(snapshot_dir, "2026-07-11", "2026-07-11"):
        snapshot, snapshot_rungs = mat.make_ladder_payload(meta, records)
        snapshots.append(snapshot)
        rungs.extend(snapshot_rungs)

    assert mat.insert_rows(conn, "tmax_v2_ladder_snapshots", snapshots, False) == 2
    assert mat.insert_rows(conn, "tmax_v2_ladder_rung_quotes", rungs, False) == 4
    assert mat.insert_rows(conn, "tmax_v2_ladder_snapshots", snapshots, False) == 0
    assert mat.insert_rows(conn, "tmax_v2_ladder_rung_quotes", rungs, False) == 0
    assert mat.materialize_states(conn, "2026-07-11", "2026-07-11", False)[0] == {
        "base_states": 2,
        "state_revisions": 2,
    }
    assert mat.materialize_states(conn, "2026-07-11", "2026-07-11", False)[0] == {
        "base_states": 0,
        "state_revisions": 0,
    }

    rows = conn.execute(
        """
        SELECT state.tmax_state_id, group_concat(rung.absolute_bracket_identity, ',') AS brackets
        FROM tmax_v2_canonical_states AS state
        JOIN tmax_v2_ladder_rung_quotes AS rung ON rung.ladder_snapshot_id = state.ladder_snapshot_id
        GROUP BY state.tmax_state_id
        ORDER BY brackets
        """
    ).fetchall()
    assert [row["brackets"] for row in rows] == ["20,21", "20,22"]


def test_unknown_first_seen_is_research_only_and_not_selected():
    conn = _conn()
    _insert_ladder(conn)
    _insert_forecast(conn, "forecast-old", "2026-07-11T11:00:00Z")
    _insert_observation(
        conn,
        "archive-unknown",
        "2026-07-11T11:30:00Z",
        "2026-07-11T11:30:00Z",
        status="research_only_unknown_first_seen",
    )

    mat.materialize_states(conn, "2026-07-11", "2026-07-11", False)
    state = conn.execute("SELECT * FROM tmax_v2_canonical_states").fetchone()

    assert state["pit_status"] == "research_only_unknown_observation_first_seen"
    assert state["observation_event_id"] is None


def test_missing_observation_and_forecast_are_not_mislabeled_as_forecast_only():
    conn = _conn()
    _insert_ladder(conn)

    mat.materialize_states(conn, "2026-07-11", "2026-07-11", False)
    state = conn.execute("SELECT * FROM tmax_v2_canonical_states").fetchone()

    assert state["pit_status"] == "research_only_missing_inputs"
    assert state["observation_lineage_status"] == "missing"
    assert state["forecast_lineage_status"] == "missing"


def test_late_pit_valid_inputs_append_revision_and_refresh_effective_view():
    conn = _conn()
    _insert_ladder(conn)

    assert mat.materialize_states(conn, "2026-07-11", "2026-07-11", False)[0] == {
        "base_states": 1,
        "state_revisions": 1,
    }
    base = conn.execute("SELECT * FROM tmax_v2_canonical_states").fetchone()
    assert base["pit_status"] == "research_only_missing_inputs"

    _insert_observation(conn, "late-but-pit-obs", "2026-07-11T11:30:00Z", "2026-07-11T11:31:00Z")
    _insert_forecast(conn, "late-but-pit-forecast", "2026-07-11T11:00:00Z")

    assert mat.materialize_states(conn, "2026-07-11", "2026-07-11", False)[0] == {
        "base_states": 0,
        "state_revisions": 1,
    }
    effective = conn.execute("SELECT * FROM tmax_v2_canonical_state_effective").fetchone()
    revision_count = conn.execute("SELECT COUNT(*) FROM tmax_v2_canonical_state_revisions").fetchone()[0]

    assert revision_count == 2
    assert effective["state_revision_seq"] == 2
    assert effective["pit_status"] == "pit_verified"
    assert effective["observation_event_id"] == "late-but-pit-obs"
    assert effective["forecast_capture_id"] == "late-but-pit-forecast"


def test_tmax_v2_facts_reject_mutation_and_deletion():
    conn = _conn()
    _insert_ladder(conn)

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE tmax_v2_ladder_snapshots SET city = 'Elsewhere' WHERE ladder_snapshot_id = 'ladder'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM tmax_v2_ladder_snapshots WHERE ladder_snapshot_id = 'ladder'")
