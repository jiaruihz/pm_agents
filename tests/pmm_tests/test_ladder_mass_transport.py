from __future__ import annotations

import json

import numpy as np
import pandas as pd

from weather_model_evaluation import ladder_mass_transport as subject
from weather_data_feed.ladder_snapshot_history import (
    history_cache_dates,
    load_history,
    load_history_cache_date,
    materialize_history_cache,
)


def test_feature_blocks_are_nested_and_static_kink_is_control_only() -> None:
    assert set(subject.M0) < set(subject.M1) < set(subject.M2) < set(subject.M3)
    assert "kink_score" not in subject.M3
    assert set(subject.STATIC_KINK) == set(subject.M0) | {"kink_score"}
    assert "snapshot_source" in subject.CATEGORICAL
    assert (subject.RECENT_FORWARD_START, subject.RECENT_FORWARD_END) == (
        "2026-07-29", "2026-08-08"
    )


def test_relative_target_removes_common_ladder_move() -> None:
    frame = pd.DataFrame(
        {
            "ladder_snapshot_id": ["a", "a", "a"],
            "h60_mid_move": [0.03, 0.04, 0.05],
        }
    )
    frame["common"] = frame.groupby("ladder_snapshot_id")["h60_mid_move"].transform("median")
    relative = frame["h60_mid_move"] - frame["common"]
    assert np.allclose(relative, [-0.01, 0.0, 0.01])


def test_date_ci_resamples_target_dates() -> None:
    rows = pd.DataFrame({"target_date": ["d1", "d1", "d2", "d2", "d3", "d3"], "delta": [-1, -1, -2, -2, -3, -3]})
    point, low, high = subject._date_ci(rows, "delta", 500, 7)
    assert point == -2.0
    assert high < 0


def test_candidate_expression_preserves_pure_relative_markout() -> None:
    common = {
        "block": "M2_ladder_transition",
        "horizon": 60,
        "ladder_snapshot_id": "snapshot-1",
        "city": "Amsterdam",
        "target_date": "2026-07-29",
        "event_identity": "event-1",
        "snapshot_ts": "2026-07-29T00:00:00Z",
        "feature_book_snapshot_id": "snapshot-1",
        "future_evaluation_snapshot_id": "snapshot-2",
        "yes_bid_size": 10.0,
        "yes_ask_size": 10.0,
        "no_bid_size": 10.0,
        "no_ask_size": 10.0,
        "h60_yes_bid_size": 10.0,
        "h60_yes_ask_size": 10.0,
        "h60_no_bid_size": 10.0,
        "h60_no_ask_size": 10.0,
        "win": 0.0,
    }
    rows = pd.DataFrame(
        [
            {
                **common,
                "condition_id": "high",
                "bracket": "20",
                "markout_prediction": 0.02,
                "h60_relative_markout": 0.015,
                "yes_bid": 0.20,
                "yes_ask": 0.21,
                "no_bid": 0.78,
                "no_ask": 0.79,
                "h60_yes_bid": 0.22,
                "h60_yes_ask": 0.23,
                "h60_no_bid": 0.76,
                "h60_no_ask": 0.77,
            },
            {
                **common,
                "condition_id": "low",
                "bracket": "19",
                "markout_prediction": -0.01,
                "h60_relative_markout": -0.005,
                "yes_bid": 0.30,
                "yes_ask": 0.31,
                "no_bid": 0.68,
                "no_ask": 0.69,
                "h60_yes_bid": 0.29,
                "h60_yes_ask": 0.30,
                "h60_no_bid": 0.69,
                "h60_no_ask": 0.70,
            },
        ]
    )

    expressions, funnel = subject._candidate_expressions(
        rows, "M2_ladder_transition"
    )

    assert funnel["one_share_depth_all_legs"] == 1
    assert expressions.iloc[0]["target_condition_id"] == "high"
    assert expressions.iloc[0]["hedge_condition_id"] == "low"
    assert np.isclose(expressions.iloc[0]["actual_pair_relative_markout"], 0.02)


def test_history_adapter_preserves_pit_snapshot_and_direct_ladder(tmp_path) -> None:
    records = []
    for index, bracket in enumerate(("18", "19", "20")):
        records.append(
            {
                "city": "Amsterdam",
                "event_date": "2026-05-19",
                "event_slug": "amsterdam-2026-05-19",
                "bracket": bracket,
                "condition_id": f"condition-{index}",
                "unit": "C",
                "forecast_max_f": 67.0,
                "forecast_source": "fixture",
                "model_init_utc_estimated": "00Z",
                "metar_current_max_f": 60.0,
                "metar_latest_ts_utc": "2026-05-19T00:00:00Z",
                "yes_best_bid": 0.1 + index * 0.1,
                "yes_best_ask": 0.11 + index * 0.1,
                "yes_bid_size": 10,
                "yes_ask_size": 11,
                "yes_depth_bid_5c": 20,
                "yes_depth_ask_5c": 21,
                "no_best_bid": 0.89 - index * 0.1,
                "no_best_ask": 0.9 - index * 0.1,
                "no_bid_size": 12,
                "no_ask_size": 13,
                "no_depth_bid_5c": 22,
                "no_depth_ask_5c": 23,
            }
        )
    path = tmp_path / "snapshot_20260519_0100.json"
    path.write_text(json.dumps({"ts_utc": "2026-05-19T01:00:00Z", "records": records}))
    snapshots, rungs, coverage = load_history(
        tmp_path,
        "2026-05-19",
        "2026-05-19",
        sample_seconds=1800,
        workers=1,
    )
    assert len(snapshots) == 1
    assert len(rungs) == 3
    assert snapshots.iloc[0]["snapshot_source"] == "immutable_paper_snapshot"
    assert snapshots.iloc[0]["forecast_event_ts"] == pd.Timestamp("2026-05-19T01:00:00Z")
    assert coverage["sampled_groups"] == 1


def test_development_fold_can_use_predevelopment_history(monkeypatch) -> None:
    dates = ["2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22", "2026-05-23", "2026-07-11"]
    rows = pd.DataFrame(
        {
            "target_date": dates,
            "city": ["Amsterdam"] * len(dates),
            "ladder_snapshot_id": [f"snapshot-{date}" for date in dates],
            "condition_id": [f"condition-{date}" for date in dates],
            "h60_relative_markout": [0.0] * len(dates),
        }
    )
    monkeypatch.setattr(
        subject,
        "_fit_predict",
        lambda train, test, block, alpha, label: (None, np.zeros(len(test))),
    )

    oof = subject.development_oof(rows)

    assert sorted(oof["target_date"].unique()) == ["2026-07-11"]


def test_history_adapter_includes_pre_target_capture_files(tmp_path) -> None:
    record = {
        "city": "Amsterdam",
        "event_date": "2026-05-19",
        "event_slug": "amsterdam-2026-05-19",
        "unit": "C",
        "forecast_max_f": 67.0,
        "forecast_source": "fixture",
        "model_init_utc_estimated": "00Z",
        "metar_current_max_f": 60.0,
        "metar_latest_ts_utc": "2026-05-18T01:00:00Z",
        "yes_best_bid": 0.1,
        "yes_best_ask": 0.11,
        "yes_bid_size": 10,
        "yes_ask_size": 11,
        "yes_depth_bid_5c": 20,
        "yes_depth_ask_5c": 21,
        "no_best_bid": 0.89,
        "no_best_ask": 0.9,
        "no_bid_size": 12,
        "no_ask_size": 13,
        "no_depth_bid_5c": 22,
        "no_depth_ask_5c": 23,
    }
    records = [
        {
            **record,
            "bracket": bracket,
            "condition_id": f"condition-{index}",
        }
        for index, bracket in enumerate(("18", "19", "20"))
    ]
    (tmp_path / "snapshot_20260518_0100.json").write_text(
        json.dumps({"ts_utc": "2026-05-18T01:00:00Z", "records": records})
    )

    snapshots, rungs, coverage = load_history(
        tmp_path,
        "2026-05-19",
        "2026-05-19",
        sample_seconds=1800,
        workers=2,
    )

    assert len(snapshots) == 1
    assert len(rungs) == 3
    assert coverage["input_files"] == 1


def test_history_adapter_streaming_cache_round_trip(tmp_path) -> None:
    records = []
    for index, bracket in enumerate(("18", "19", "20")):
        records.append(
            {
                "city": "Amsterdam",
                "event_date": "2026-05-19",
                "event_slug": "amsterdam-2026-05-19",
                "bracket": bracket,
                "condition_id": f"condition-{index}",
                "unit": "C",
                "forecast_max_f": 67.0,
                "metar_current_max_f": 60.0,
                "metar_latest_ts_utc": "2026-05-19T00:00:00Z",
                "yes_best_bid": 0.1 + index * 0.1,
                "yes_best_ask": 0.11 + index * 0.1,
                "yes_bid_size": 10,
                "yes_ask_size": 11,
                "yes_depth_bid_5c": 20,
                "yes_depth_ask_5c": 21,
                "no_best_bid": 0.89 - index * 0.1,
                "no_best_ask": 0.9 - index * 0.1,
                "no_bid_size": 12,
                "no_ask_size": 13,
                "no_depth_bid_5c": 22,
                "no_depth_ask_5c": 23,
            }
        )
    (tmp_path / "snapshot_20260519_0100.json").write_text(
        json.dumps({"ts_utc": "2026-05-19T01:00:00Z", "records": records})
    )
    cache_path = tmp_path / "history.sqlite"

    coverage = materialize_history_cache(
        tmp_path, "2026-05-19", "2026-05-19", cache_path
    )
    snapshots, rungs = load_history_cache_date(
        cache_path, "2026-05-19", sample_seconds=1800
    )

    assert coverage["cached_groups"] == 1
    assert history_cache_dates(cache_path) == ["2026-05-19"]
    assert len(snapshots) == 1
    assert len(rungs) == 3


def test_candidate_expressions_handles_no_cross_rung_snapshot() -> None:
    rows = pd.DataFrame(
        {
            "block": ["M2_ladder_transition"],
            "horizon": [60],
            "ladder_snapshot_id": ["snapshot-1"],
            "condition_id": ["condition-1"],
            "markout_prediction": [0.01],
        }
    )
    expressions, funnel = subject._candidate_expressions(
        rows, "M2_ladder_transition"
    )
    assert expressions.empty
    assert funnel["pair_constructed"] == 0
    assert funnel["one_share_depth_all_legs"] == 0
