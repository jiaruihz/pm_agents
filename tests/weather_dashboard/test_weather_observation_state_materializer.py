import csv
import json
import sqlite3
from pathlib import Path

from scripts.etl import materialize_weather_observation_state as mat


def test_materializes_observation_events_and_intraday_state(tmp_path):
    db_path = tmp_path / "weather.db"
    wu_dir = tmp_path / "wu_obs"
    wu_dir.mkdir()
    station_summary = tmp_path / "summary.json"
    station_summary.write_text(
        json.dumps(
            {
                "stations": [
                    {
                        "city": "NYC",
                        "icao": "KLGA",
                        "unit": "F",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with (wu_dir / "wu_obs_KLGA.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date_local", "valid_utc", "temp", "dewpt", "wspd", "wdir", "wx_phrase"])
        writer.writeheader()
        writer.writerow({"date_local": "2026-07-05", "valid_utc": "2026-07-05T13:00Z", "temp": "70", "dewpt": "60", "wspd": "5", "wdir": "180", "wx_phrase": "CLR"})
        writer.writerow({"date_local": "2026-07-05", "valid_utc": "2026-07-05T15:00Z", "temp": "75", "dewpt": "61", "wspd": "6", "wdir": "190", "wx_phrase": "FEW"})
        writer.writerow({"date_local": "2026-07-05", "valid_utc": "2026-07-05T16:00Z", "temp": "74", "dewpt": "62", "wspd": "7", "wdir": "200", "wx_phrase": "SCT"})

    with mat.connect(db_path) as conn:
        stations = mat.load_stations(station_summary)
        ingest = mat.ingest_observations(
            conn,
            stations=stations,
            wu_dir=wu_dir,
            source_system="test_cache",
            source_path=str(wu_dir),
            start="2026-07-05",
            end="2026-07-05",
        )
        rows = mat.build_state_rows(
            conn,
            source_system="test_cache",
            source_path=str(wu_dir),
            start="2026-07-05",
            end="2026-07-05",
            hours=[11, 12],
            min_obs_per_day=1,
        )
        mat.write_state_rows(conn, rows)

    assert ingest["inserted_observation_events"] == 3
    assert len(rows) == 2
    noon = [row for row in rows if row["decision_hour_local"] == 12][0]
    assert noon["current_temp_f"] == 74
    assert noon["running_max_f"] == 75
    assert noon["final_max_f"] == 75

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM weather_observation_events").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM weather_intraday_state_rows").fetchone()[0] == 2
    finally:
        conn.close()
