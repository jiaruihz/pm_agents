from __future__ import annotations

import json
import importlib.util
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "research_tmax_single_snapshot_lineage_replay_v2",
    ROOT / "scripts/analysis/reheat_risk/research_tmax_single_snapshot_lineage_replay_v2.py",
)
assert SPEC is not None and SPEC.loader is not None
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


def test_observation_history_reads_two_files_two_observations(tmp_path, monkeypatch):
    root = tmp_path / "paper_snapshots"
    root.mkdir()
    snapshots = [
        ("2026-06-20T10:00:00Z", "2026-06-20T09:55:00Z", 70.0),
        ("2026-06-20T11:00:00Z", "2026-06-20T10:55:00Z", 72.0),
    ]
    for index, (snapshot_ts, obs_ts, temp_f) in enumerate(snapshots):
        payload = {
            "ts_utc": snapshot_ts,
            "records": [
                {
                    "city": "Testville",
                    "target_date": "2026-06-20",
                    "metar_latest_ts_utc": obs_ts,
                    "metar_latest_temp_f": temp_f,
                    "metar_icao": "KTST",
                }
            ],
        }
        (root / f"snapshot_{index}.json").write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(replay, "SNAPSHOT_ROOTS", [root])
    history, manifest = replay._observation_history()

    assert len(manifest) == 2
    assert history["obs_ts_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ").tolist() == [
        "2026-06-20T09:55:00Z",
        "2026-06-20T10:55:00Z",
    ]
    assert history["first_seen_snapshot_ts_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ").tolist() == [
        "2026-06-20T10:00:00Z",
        "2026-06-20T11:00:00Z",
    ]


def _rung(bracket: str) -> dict:
    return {
        "city": "Testville", "target_date": "2026-06-20", "ts_local": "2026-06-20 12:00:00", "unit": "C",
        "bracket": bracket, "question": f"Will Tmax be {bracket} C?",
        "yes_best_bid": 0.08, "yes_best_ask": 0.10, "no_best_bid": 0.90, "no_best_ask": 0.92,
        "yes_ask_size": 5.0, "no_ask_size": 5.0,
        "forecast_max_native": 21.0, "forecast_peak_delta_hours_local": 2.0, "forecast_peak_hour_local": 14.0,
    }


def test_five_bucket_below_label_and_direct_yes_ask():
    decision = pd.Timestamp("2026-06-20T10:00:00Z")
    history = {
        ("Testville", "2026-06-20"): [
            {"obs_ts_utc": pd.Timestamp("2026-06-20T09:55:00Z"), "first_seen_snapshot_ts_utc": decision, "temp_f": 66.2}
        ]
    }
    state = replay._native_state(
        [_rung(str(value)) for value in range(18, 23)], decision, history,
        {("Testville", "2026-06-20"): "18"}, 0, Path("snapshot.json"),
    )

    assert state is not None
    assert state["actual_bucket"] == "below"
    assert state["winner_rung_index"] < state["anchor_rung_index"]
    assert state["market_p_below"] > 0
    assert state["d1_yes_direct_ask"] == 0.10


def test_execution_requires_finite_five_share_ask_size():
    row = {
        "city": "Testville", "target_date": "2026-06-20", "decision_snapshot_ts_utc": "2026-06-20T10:00:00Z",
        "actual_bucket": "below", "unit": "C", "temp_trend_3h_f": 1.0,
        "current_no_ask": 0.40, "d1_no_ask": 0.99, "d2_no_ask": 0.99,
        "current_no_ask_size": None, "d1_no_ask_size": 5.0, "d2_no_ask_size": 5.0,
        "d1_yes_direct_ask": 0.99, "d2_yes_direct_ask": 0.99,
        "d1_yes_direct_ask_size": 5.0, "d2_yes_direct_ask_size": 5.0,
        "exec_market_p_current": 0.10, "exec_market_p_d1": 0.01, "exec_market_p_d2": 0.01,
    }
    assert replay._policy(pd.DataFrame([row]), "market", "clean", False).empty
    row["current_no_ask_size"] = 5.0
    selected = replay._policy(pd.DataFrame([row]), "market", "clean", False)
    assert len(selected) == 1
    assert selected.iloc[0]["expression"] == "current_no"
    assert selected.iloc[0]["shares"] == 5
