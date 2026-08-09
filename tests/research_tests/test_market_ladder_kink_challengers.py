from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.analysis.market_structure_edge import (
    market_ladder_kink_challengers as subject,
)


def test_validate_v1_freeze_preserves_forward_boundary(tmp_path: Path) -> None:
    payload = {
        "training_target_end": "2026-07-28",
        "clean_forward_start": "2026-08-11",
        "live_enabled": False,
        "orders_enabled": False,
        "notional_usd": 0.0,
    }
    payload["payload_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    path = tmp_path / "model_freeze.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert subject.validate_v1_freeze(path)["clean_forward_start"] == "2026-08-11"


def test_future_snapshot_map_keeps_all_post_ttl_candidates_within_tolerance() -> None:
    entry = pd.DataFrame(
        [
            {
                "ladder_snapshot_id": "entry",
                "city": "A",
                "target_date": "2026-01-01",
                "event_slug": "event",
                "decision_ts": pd.Timestamp("2026-01-01T00:00:00Z"),
            }
        ]
    )
    timeline = pd.DataFrame(
        [
            {
                "ladder_snapshot_id": snapshot_id,
                "city": "A",
                "target_date": "2026-01-01",
                "event_identity": "event-id",
                "source_snapshot_ts_utc": timestamp,
                "snapshot_ts": pd.Timestamp(timestamp),
            }
            for snapshot_id, timestamp in [
                ("entry", "2026-01-01T00:00:00Z"),
                ("exit-15", "2026-01-01T00:15:00Z"),
                ("exit-17", "2026-01-01T00:17:00Z"),
                ("too-late", "2026-01-01T00:26:00Z"),
            ]
        ]
    )

    result = subject.future_snapshot_map(entry, timeline)
    at_15 = result[result["horizon_minutes"].eq(15)]

    assert at_15["execution_exit_book_snapshot_id"].tolist() == ["exit-15", "exit-17"]
    assert at_15["exit_candidate_rank"].tolist() == [0, 1]


def test_dynamic_rows_wait_for_first_three_rung_direct_exit() -> None:
    entry = pd.DataFrame(
        [
            {
                "ladder_snapshot_id": "entry",
                "condition_id": "condition",
                "kink_available": True,
                "cold_distance": 1.0,
                "kink_score": 0.4,
                "yes_ask": 0.20,
                "yes_ask_size": 10.0,
            }
        ]
    )
    mapping = pd.DataFrame(
        [
            {
                "feature_book_snapshot_id": "entry",
                "horizon_minutes": 15,
                "execution_exit_book_snapshot_id": "missing-neighbor",
                "exit_snapshot_ts_utc": "2026-01-01T00:15:00Z",
                "exit_capture_lag_minutes": 0.0,
                "exit_candidate_rank": 0,
            },
            {
                "feature_book_snapshot_id": "entry",
                "horizon_minutes": 15,
                "execution_exit_book_snapshot_id": "scorable",
                "exit_snapshot_ts_utc": "2026-01-01T00:17:00Z",
                "exit_capture_lag_minutes": 2.0,
                "exit_candidate_rank": 1,
            },
        ]
    )
    exit_quotes = pd.DataFrame(
        [
            {
                "execution_exit_book_snapshot_id": "missing-neighbor",
                "condition_id": "condition",
                "exit_kink_score": np.nan,
                "exit_yes_bid": 0.21,
                "exit_yes_bid_size": 10.0,
            },
            {
                "execution_exit_book_snapshot_id": "scorable",
                "condition_id": "condition",
                "exit_kink_score": 0.1,
                "exit_yes_bid": 0.25,
                "exit_yes_bid_size": 10.0,
            },
        ]
    )

    rows, coverage = subject.build_dynamic_rows(entry, mapping, exit_quotes)

    assert len(rows) == 1
    assert rows.iloc[0]["execution_entry_book_snapshot_id"] == "entry"
    assert rows.iloc[0]["execution_exit_book_snapshot_id"] == "scorable"
    assert rows.iloc[0]["relative_kink_convergence"] == pytest.approx(0.3)
    assert rows.iloc[0]["executable_pnl"] < 0.05
    assert coverage["actual_fills"] == 0


def test_candidate_adds_only_kink_cold_distance_feature() -> None:
    rows = pd.DataFrame(
        [
            {
                "market_p": 0.2,
                "steps_from_mode": -1,
                "abs_distance": 1.0,
                "cold": 1.0,
                "hot": 0.0,
                "cold_distance": 1.0,
                "hot_distance": 0.0,
                "lifecycle": "D0_10_14",
                "market_unit": "C",
                "native_distance": -1.0,
                "kink_cold_distance": 0.4,
            }
        ]
    )

    baseline = subject.side_matrix(rows, include_kink=False)
    candidate = subject.side_matrix(rows, include_kink=True)

    assert candidate.shape[1] == baseline.shape[1] + 1
    assert candidate[0, -1] == pytest.approx(0.4)
    assert subject.side_feature_names(True)[-1] == "kink_x_cold_distance"


def test_verdict_localizes_execution_failure_and_exhausts_branch() -> None:
    settlement = {"pass": False}
    probability = pd.DataFrame(
        [
            {"horizon_minutes": 15, "rows": 0, "probability_pass": False},
            {"horizon_minutes": 30, "rows": 10, "probability_pass": True},
            {"horizon_minutes": 60, "rows": 10, "probability_pass": False},
        ]
    )
    trades = pd.DataFrame(
        [
            {
                "horizon_minutes": 30,
                "policy": "kink_candidate",
                "roi_ci_low": -0.2,
                "top_date_abs_pnl_share": 0.2,
                "top_city_abs_pnl_share": 0.1,
            },
            {
                "horizon_minutes": 60,
                "policy": "kink_candidate",
                "roi_ci_low": -0.1,
                "top_date_abs_pnl_share": 0.2,
                "top_city_abs_pnl_share": 0.1,
            },
        ]
    )

    result = subject.verdict(settlement, probability, trades)

    assert result["dynamic_repricing"]["failure_by_horizon"] == {
        "15": "execution_coverage",
        "30": "execution",
        "60": "probability_and_baseline",
    }
    assert result["final"] == "BRANCH_EXHAUSTED"
