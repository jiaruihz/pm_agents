from __future__ import annotations

from datetime import date
import json
import sqlite3

from scripts.etl.build_weather_signal_candidates import (
    CANDIDATE_DDL,
    FORECAST_CURVE_DDL,
    write_db_incremental,
)
from scripts.etl.materialize_weather_first_seen_pipeline import (
    load_snapshots,
    materialize_pipeline,
)
from scripts.etl.materialize_weather_information_events import main as materialize_events
from scripts.ops.weather_first_seen_zero_notional_forward import _incremental_rows
from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from weather_dashboard.db.first_seen_schema import apply_first_seen_schema
from weather_data_feed.information_events import build_information_event


def test_first_seen_schema_migrates_v1_and_incremental_rebuild_preserves_v2() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE fact_signal_candidates (
          candidate_id TEXT PRIMARY KEY,
          event_date TEXT
        )
        """
    )
    apply_first_seen_schema(conn)
    columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(fact_signal_candidates)")
    }
    assert "candidate_grain_version" in columns
    conn.execute(
        """
        INSERT INTO fact_signal_candidates (
          candidate_id, candidate_grain_version, event_date
        ) VALUES ('v2', 'v2_event_checkpoint', '2026-07-28')
        """
    )
    write_db_incremental(conn, [], event_date_start=date(2026, 7, 1))
    assert conn.execute(
        """
        SELECT count(*)
        FROM fact_signal_candidates
        WHERE candidate_grain_version = 'v2_event_checkpoint'
        """
    ).fetchone()[0] == 1


def test_real_raw_event_builds_feature_checkpoint_and_two_sided_candidates(tmp_path) -> None:
    db = tmp_path / "weather.db"
    raw_path = tmp_path / "source_events.jsonl"
    snapshot_dir = tmp_path / "paper_snapshots"
    feature_store = tmp_path / "feature_store"
    snapshot_dir.mkdir()
    event = build_information_event(
        event_kind="observation",
        event_role="new_content",
        source="aviationweather_metar",
        city="Atlanta",
        station_id="KATL",
        provider_item_id="KATL-20260728-1200",
        content_key="Atlanta|KATL|2026-07-28T12:00:00Z",
        normalized_payload={"raw_metar": "METAR KATL 281200Z 00000KT 25/20"},
        source_event_ts_utc="2026-07-28T12:00:00Z",
        detected_at_utc="2026-07-28T12:00:03Z",
        first_seen_at_utc="2026-07-28T12:00:03Z",
        available_at_utc="2026-07-28T12:00:04Z",
        pit_lineage_class="collector_exact",
    )
    raw_path.write_text(
        json.dumps(
            {
                **event,
                "information_event_status": "material",
                "status": "ok",
                "target_date": "2026-07-28",
                "station": "KATL",
                "source_report_ts_utc": "2026-07-28T12:00:00Z",
                "temp_c": 25.0,
                "unit": "F",
                "raw_metar": "METAR KATL 281200Z 00000KT 25/20",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert materialize_events(
        ["--db", str(db), "--source-events", str(raw_path)]
    ) == 0
    snapshot = {
        "ts_utc": "2026-07-28T12:05:00Z",
        "records": [
            {
                "city": "Atlanta",
                "target_date": "2026-07-28",
                "event_date": "2026-07-28",
                "snapshot_ts_utc": "2026-07-28T12:05:00Z",
                "condition_id": "condition-a",
                "market_id": "market-a",
                "bracket": "77-78",
                "unit": "F",
                "timezone_name": "America/New_York",
                "model": "ecmwf",
                "model_prob": 0.62,
                "forecast_source": "open_meteo_live_ecmwf",
                "forecast_max_f": 78.0,
                "forecast_max_native": 78.0,
                "forecast_peak_hour_local": 15,
                "forecast_peak_time_local": "2026-07-28T15:00:00",
                "forecast_values_hash": "forecast-a",
                "yes_best_bid": 0.55,
                "yes_best_ask": 0.57,
                "yes_spread": 0.02,
                "yes_depth_ask_5c": 100.0,
                "yes_book_fetched_at_utc": "2026-07-28T12:05:02Z",
                "no_best_bid": 0.43,
                "no_best_ask": 0.45,
                "no_spread": 0.02,
                "no_depth_ask_5c": 100.0,
                "no_book_fetched_at_utc": "2026-07-28T12:05:02Z",
            }
        ],
    }
    snapshot_path = snapshot_dir / "snapshot_20260728_1205.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute(CANDIDATE_DDL)
    conn.execute(FORECAST_CURVE_DDL)
    apply_schema_canonical(conn)
    result = materialize_pipeline(
        conn,
        load_snapshots([snapshot_dir]),
        feature_store=feature_store,
        max_snapshot_lag_minutes=20.0,
    )
    checkpoint = conn.execute("SELECT * FROM weather_state_checkpoints").fetchone()
    candidates = conn.execute(
        """
        SELECT *
        FROM fact_signal_candidates
        WHERE candidate_grain_version = 'v2_event_checkpoint'
        ORDER BY side
        """
    ).fetchall()
    conn.close()

    assert result["checkpoint_built"] == 1
    assert checkpoint["feature_store_frame_id"]
    assert checkpoint["feature_row_id"]
    assert len(candidates) == 2
    assert {row["candidate_status"] for row in candidates} == {"scored"}
    assert {row["side"] for row in candidates} == {"BUY_NO", "BUY_YES"}
    assert all(row["paper_ordered"] == 0 and row["live_filled"] == 0 for row in candidates)


def test_forward_bootstrap_skips_history_then_reads_only_new_complete_lines(tmp_path) -> None:
    raw = tmp_path / "sources.jsonl"
    raw.write_text(json.dumps({"sequence": 1}) + "\n", encoding="utf-8")
    state = {}

    assert list(_incremental_rows([raw], state, bootstrap_at_end=True)) == []
    with raw.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"sequence": 2}) + "\n")
    rows = list(_incremental_rows([raw], state, bootstrap_at_end=False))

    assert [row["sequence"] for row, _path in rows] == [2]


def test_forward_pipeline_can_defer_bulk_settlement_updates(tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "weather.db")
    conn.row_factory = sqlite3.Row
    conn.execute(CANDIDATE_DDL)
    conn.execute(FORECAST_CURVE_DDL)
    apply_schema_canonical(conn)

    result = materialize_pipeline(
        conn,
        [],
        feature_store=tmp_path / "feature_store",
        max_snapshot_lag_minutes=20.0,
        candidate_batch_size=1,
        attach_candidate_settlements=False,
    )

    assert result["settlements_attached"] == 0
    assert result["settlements_deferred"] == 1
    conn.close()
