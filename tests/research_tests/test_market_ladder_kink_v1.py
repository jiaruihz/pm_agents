from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "scripts"
    / "analysis"
    / "market_structure_edge"
    / "market_ladder_kink_v1.py"
)
SPEC = importlib.util.spec_from_file_location("market_ladder_kink_v1", SCRIPT)
assert SPEC and SPEC.loader
subject = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = subject
SPEC.loader.exec_module(subject)


def test_bracket_center_handles_range_and_open_tail() -> None:
    assert subject.bracket_center("90-91") == 90.5
    assert subject.bracket_center("34+") == 34.0


def test_prepare_ladders_marks_a_cheap_interior_rung_as_positive_kink() -> None:
    raw = pd.DataFrame(
        [
            {
                "ladder_snapshot_id": "snapshot-1",
                "city": "Test City",
                "target_date": "2026-01-01",
                "decision_ts_utc": "2026-01-01T12:00:00Z",
                "yes_book_fetched_at_utc": "2026-01-01T11:59:30Z",
                "local_hours": 12.0,
                "bracket": bracket,
                "yes_bid": midpoint - 0.01,
                "yes_ask": midpoint + 0.01,
                "yes_bid_size": 20.0,
                "yes_ask_size": 20.0,
                "win": win,
            }
            for bracket, midpoint, win in [
                ("10", 0.30, 0.0),
                ("11", 0.05, 1.0),
                ("12", 0.25, 0.0),
            ]
        ]
    )

    rows, coverage = subject.prepare_ladders(raw)
    center = rows.loc[rows["bracket"].eq("11")].iloc[0]

    assert center["kink_available"]
    assert center["kink_score"] > 0
    assert coverage["selected_snapshots"] == 1
    assert np.isclose(rows["market_p"].sum(), 1.0)


def test_softmax_fit_learns_positive_kink_and_normalizes_each_ladder() -> None:
    records = []
    for date_index in range(6):
        for snapshot_index in range(4):
            snapshot_id = f"d{date_index}-s{snapshot_index}"
            for rank in range(4):
                records.append(
                    {
                        "ladder_snapshot_id": snapshot_id,
                        "target_date": f"2026-01-{date_index + 1:02d}",
                        "bracket_rank": rank,
                        "market_p": 0.25,
                        "kink_feature": 2.0 if rank == 1 else 0.0,
                        "win": 1.0 if rank == 1 else 0.0,
                    }
                )
    rows = pd.DataFrame(records)

    fit = subject.fit_softmax(rows, include_kink=True, ridge=0.05)
    ordered = rows.sort_values(["ladder_snapshot_id", "bracket_rank"]).reset_index(drop=True)
    probabilities = subject.predict_softmax(ordered, fit)

    assert fit.converged
    assert fit.beta > 0
    sums = pd.Series(probabilities).groupby(ordered["ladder_snapshot_id"]).sum()
    assert np.allclose(sums.to_numpy(), 1.0)
