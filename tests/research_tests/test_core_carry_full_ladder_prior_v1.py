from __future__ import annotations

import gzip
import json

import pandas as pd

from scripts.analysis.reheat_risk import (
    core_carry_full_ladder_prior as subject,
)


def test_boundary_midpoint_preserves_two_sided_and_boundary_books() -> None:
    assert subject.boundary_midpoint(
        {"summary": {"best_bid": 0.7, "best_ask": 0.8}}
    ) == (0.75, True)
    assert subject.boundary_midpoint(
        {"summary": {"best_bid": None, "best_ask": 0.02}}
    ) == (0.01, False)
    assert subject.boundary_midpoint(
        {"summary": {"best_bid": 0.98, "best_ask": None}}
    ) == (0.99, False)


def test_read_yes_ladder_uses_exact_city_date_and_yes_rows(tmp_path) -> None:
    path = tmp_path / "snapshot.jsonl.gz"
    rows = [
        {
            "city": "Wellington",
            "event_date": "2026-08-08",
            "outcome": "yes",
            "status": "ok",
            "bracket": bracket,
            "summary": {"best_bid": bid, "best_ask": ask},
        }
        for bracket, bid, ask in [("11", 0.01, 0.02), ("12", 0.7, 0.8), ("13", 0.1, 0.2)]
    ]
    rows.append({**rows[0], "outcome": "no"})
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    ladder = subject.read_yes_ladder(path, "Wellington", "2026-08-08")
    assert [row["bracket"] for row in ladder] == ["11", "12", "13"]
    assert [row["midpoint"] for row in ladder] == [0.015, 0.75, 0.15000000000000002]


def test_first_state_entries_keeps_first_checkpoint_per_exact_bracket() -> None:
    frame = pd.DataFrame(
        [
            {"city": "A", "target_date": "2026-01-01", "current_bracket": "12", "decision_snapshot_dt": "2026-01-01T02:00Z", "label": 1},
            {"city": "A", "target_date": "2026-01-01", "current_bracket": "12", "decision_snapshot_dt": "2026-01-01T01:00Z", "label": 1},
            {"city": "A", "target_date": "2026-01-01", "current_bracket": "13", "decision_snapshot_dt": "2026-01-01T03:00Z", "label": 0},
        ]
    )
    result = subject.first_state_entries(frame)
    assert len(result) == 2
    assert result.loc[result["current_bracket"].eq("12"), "decision_snapshot_dt"].iloc[0] == "2026-01-01T01:00Z"
