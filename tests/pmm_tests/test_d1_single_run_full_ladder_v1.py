from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

from weather_data_feed.historical_forecast_runs import (
    conservative_available_run,
    daily_max_rows,
    fetch_single_run_batch,
)


ROOT = Path(__file__).resolve().parents[2]
RESEARCH_PATH = (
    ROOT
    / "scripts/analysis/market_structure_edge/"
    "research_d1_full_ladder_no_v1.py"
)
SPEC = importlib.util.spec_from_file_location("d1_full_ladder_no_v1", RESEARCH_PATH)
assert SPEC and SPEC.loader
RESEARCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RESEARCH)


def test_conservative_run_floors_after_twelve_hour_lag() -> None:
    assert (
        conservative_available_run("2026-07-22T21:47:00Z")
        == "2026-07-22T06:00"
    )
    assert (
        conservative_available_run("2026-07-22T09:01:00Z")
        == "2026-07-21T18:00"
    )


def test_daily_max_rows_uses_target_local_date_and_preserves_run() -> None:
    payload = {
        "timezone": "America/New_York",
        "latitude": 33.64,
        "longitude": -84.43,
        "hourly": {
            "time": [
                "2026-07-22T23:00",
                "2026-07-23T00:00",
                "2026-07-23T01:00",
            ],
            "temperature_2m_ecmwf_ifs025": [80.0, 82.0, 81.0],
        },
    }
    rows = daily_max_rows(
        payload,
        city="Atlanta",
        target_date="2026-07-23",
        run="2026-07-22T06:00",
        decision_time_utc="2026-07-22T21:47:00Z",
        models=("ecmwf_ifs025",),
    )
    assert len(rows) == 1
    assert rows[0]["forecast_max_f"] == 82.0
    assert rows[0]["requested_run_utc"] == "2026-07-22T06:00:00Z"


def test_single_run_fetch_metadata_uses_response_complete_clock(monkeypatch, tmp_path) -> None:
    class Response:
        content = b'{"hourly":{"time":[],"temperature_2m":[]}}'
        text = content.decode()

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"hourly": {"time": [], "temperature_2m": []}}

    monkeypatch.setattr(
        "weather_data_feed.historical_forecast_runs.httpx.get",
        lambda *args, **kwargs: Response(),
    )
    _, metadata = fetch_single_run_batch(
        [{"city": "Tokyo", "latitude": 35.0, "longitude": 139.0}],
        run="2026-08-04T18:00",
        models=("gfs_global",),
        cache_dir=tmp_path,
        max_attempts=1,
    )
    assert metadata["source_fetch_clock_status"] == "response_complete"
    assert metadata["source_fetch_start_utc"] <= metadata["source_fetch_end_utc"]


def test_outer_two_selection_never_uses_deeper_rung() -> None:
    scored = pd.DataFrame(
        [
            {
                "snapshot_key": "a",
                "distance_from_nearest_endpoint": 0,
                "model_edge": 0.01,
                "cost": 0.99,
            },
            {
                "snapshot_key": "a",
                "distance_from_nearest_endpoint": 1,
                "model_edge": 0.02,
                "cost": 0.95,
            },
            {
                "snapshot_key": "a",
                "distance_from_nearest_endpoint": 2,
                "model_edge": 0.50,
                "cost": 0.50,
            },
        ]
    )
    selected = RESEARCH.select_expression(
        scored, "outer_two_model_max_edge", 1
    )
    assert selected.iloc[0]["distance_from_nearest_endpoint"] == 1
