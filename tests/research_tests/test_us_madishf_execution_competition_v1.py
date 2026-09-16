from __future__ import annotations

import json

from scripts.analysis.market_structure_edge import research_us_madishf_execution_competition_v1 as research


def test_runner_requotes_reads_dated_opportunity_shards(monkeypatch, tmp_path) -> None:
    runner = tmp_path / "fast_source_prev_no_trial"
    rows = [
        {
            "ts_utc": "2026-08-09T00:00:00Z",
            "source": "noaa_madis_hfmetar",
            "event_key": "event-a",
            "status": "cross_candidate",
            "best_ask": 0.4,
            "ask_size": 10,
        },
        {
            "ts_utc": "2026-08-10T00:00:00Z",
            "source": "noaa_madis_hfmetar",
            "event_key": "event-a",
            "status": "cross_candidate",
            "best_ask": 0.5,
            "ask_size": 8,
        },
    ]
    for row in rows:
        day = row["ts_utc"][:10]
        path = runner / day / "opportunities.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr(research, "RUNNER", runner)

    quotes = research.load_runner_requotes({"event-a"})

    assert [row["ask"] for row in quotes["event-a"]] == [0.4, 0.5]
