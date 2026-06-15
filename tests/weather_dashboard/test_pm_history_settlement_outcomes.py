import json
import sqlite3

import pytest

from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from weather_dashboard.db.connection import apply_pragmas
from weather_dashboard.ingest.pm_history_settlements import ingest


@pytest.fixture
def canonical_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_pragmas(conn)
    apply_schema_canonical(conn)
    yield conn
    conn.close()


def test_pm_history_ingest_writes_city_bracket_outcomes(canonical_conn, tmp_path):
    canonical_conn.execute(
        """
        INSERT INTO signals (
            signal_id, producer_system, producer_run_id, snapshot_ts_utc, snapshot_file,
            target_date, city, city_pool, icao, bracket, unit, signal_side,
            model_version, model_p_yes, forecast_source, market_price, edge, abs_edge,
            condition_id, market_id, token_id, hours_to_settle
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "a" * 64,
            "pm_agent_local",
            "snapshot_20260601_1200",
            "2026-06-01T12:00:00Z",
            "snapshot.json",
            "2026-06-01",
            "Tokyo",
            "t1_trading",
            "RJTT",
            "23",
            "C",
            "YES",
            "gfs",
            0.8,
            "open_meteo_live_gfs",
            0.55,
            0.25,
            0.25,
            "condition-23",
            "market-23",
            "token-23",
            18.0,
        ),
    )
    canonical_conn.commit()

    pmh_dir = tmp_path / "pm_history"
    pmh_dir.mkdir()
    (pmh_dir / "Tokyo_2026-06-01.json").write_text(
        json.dumps(
            {
                "unit": "C",
                "brackets": [
                    {"label": "23", "final_price": 0.9995, "token_id": "token-23", "closed": True},
                    {"label": "24", "final_price": 0.0005, "token_id": "token-24", "closed": True},
                ],
            }
        ),
        encoding="utf-8",
    )

    stats = ingest(canonical_conn, str(pmh_dir))

    assert stats["brackets_seen"] == 2
    assert stats["settlement_outcomes_inserted"] == 2
    assert stats["settlements_inserted"] == 2
    rows = canonical_conn.execute(
        """
        SELECT city, target_date, bracket, condition_id, raw_final_price,
               final_price, settlement_status, source_path
        FROM settlement_outcomes
        ORDER BY bracket
        """
    ).fetchall()
    assert [dict(row) for row in rows] == [
        {
            "city": "Tokyo",
            "target_date": "2026-06-01",
            "bracket": "23",
            "condition_id": "condition-23",
            "raw_final_price": 0.9995,
            "final_price": 1.0,
            "settlement_status": "settled",
            "source_path": str(pmh_dir / "Tokyo_2026-06-01.json"),
        },
        {
            "city": "Tokyo",
            "target_date": "2026-06-01",
            "bracket": "24",
            "condition_id": None,
            "raw_final_price": 0.0005,
            "final_price": 0.0,
            "settlement_status": "settled",
            "source_path": str(pmh_dir / "Tokyo_2026-06-01.json"),
        },
    ]

    stats_second = ingest(canonical_conn, str(pmh_dir))
    assert stats_second["settlement_outcomes_inserted"] == 0
    assert canonical_conn.execute("SELECT COUNT(*) FROM settlement_outcomes").fetchone()[0] == 2
