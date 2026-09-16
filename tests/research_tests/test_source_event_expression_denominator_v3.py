from __future__ import annotations

import json
import sqlite3

import pandas as pd

from scripts.analysis.market_structure_edge import research_source_event_expression_denominator_v3 as research
from scripts.ops.backfill_weather_pm_history_from_snapshot_conditions import settled_city_days
from weather_dashboard.ingest.settlement_outcomes import ensure_settlement_outcomes_schema


def _records() -> list[dict]:
    return [
        {
            "city": "TestCity",
            "target_date": "2026-07-01",
            "ts_local": "2026-07-01T10:00:00",
            "unit": "C",
            "bracket": bracket,
            "question": f"Will the highest temperature be {bracket}°C?",
            "yes_best_bid": 0.2,
            "yes_best_ask": 0.3,
            "no_best_bid": 0.7,
            "no_best_ask": 0.8,
            "yes_ask_size": 10,
            "no_ask_size": 10,
        }
        for bracket in ("20", "21")
    ]


def test_current_state_survives_when_d1_is_absent(tmp_path):
    groups = tmp_path / "groups.jsonl"
    payloads = [
        {
            "city": "TestCity",
            "target_date": "2026-07-01",
            "snapshot_ts_utc": "2026-07-01T10:30:00+00:00",
            "root_priority": 0,
            "records": _records(),
        },
        {
            "city": "TestCity",
            "target_date": "2026-07-01",
            "snapshot_ts_utc": "2026-07-01T11:30:00+00:00",
            "root_priority": 0,
            "records": _records(),
        },
    ]
    groups.write_text("".join(json.dumps(row) + "\n" for row in payloads), encoding="utf-8")
    history = {
        ("TestCity", "2026-07-01"): [
            {
                "obs_ts_utc": pd.Timestamp("2026-07-01T10:00:00Z"),
                "first_seen_snapshot_ts_utc": pd.Timestamp("2026-07-01T10:00:00Z"),
                "temp_f": 68.0,
            },
            {
                "obs_ts_utc": pd.Timestamp("2026-07-01T11:00:00Z"),
                "first_seen_snapshot_ts_utc": pd.Timestamp("2026-07-01T11:00:00Z"),
                "temp_f": 69.8,
            },
        ]
    }

    frame, funnel = research.materialize_expression_states(
        groups, history, {("TestCity", "2026-07-01"): "21"}
    )

    assert len(frame) == 2
    assert frame["cross_event"].tolist() == [False, True]
    assert frame.iloc[1]["current_key"] == "21"
    assert frame.iloc[1]["d1_key"] == ""
    assert frame.iloc[1]["y_current_yes"] == 1
    assert funnel["d1_rung_absent_but_current_retained"] == 1


def test_settled_city_days_requires_a_winning_label(tmp_path):
    db = tmp_path / "weather.db"
    conn = sqlite3.connect(db)
    ensure_settlement_outcomes_schema(conn)
    conn.execute(
        """INSERT INTO settlement_outcomes
           (settlement_outcome_id,source_system,city,target_date,bracket,final_price,settlement_status)
           VALUES ('loser','pm_history','A','2026-07-01','20',0,'settled')"""
    )
    conn.execute(
        """INSERT INTO settlement_outcomes
           (settlement_outcome_id,source_system,city,target_date,bracket,final_price,settlement_status)
           VALUES ('winner','pm_history','B','2026-07-01','21',1,'settled')"""
    )
    conn.commit()
    conn.close()

    assert settled_city_days(db) == {("B", "2026-07-01")}
