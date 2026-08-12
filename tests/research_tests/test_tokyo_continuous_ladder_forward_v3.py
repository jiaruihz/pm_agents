from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pytest

from scripts.analysis.market_structure_edge import (
    research_tokyo_continuous_ladder_forward_v3 as forward,
)


def test_forward_window_is_completely_excluded_from_train() -> None:
    rows = [
        {"state_id": "train", "target_date": "2026-07-15"},
        {"state_id": "forward_1", "target_date": "2026-07-16"},
        {"state_id": "forward_2", "target_date": "2026-07-30"},
        {"state_id": "future", "target_date": "2026-07-31"},
    ]

    train, holdout = forward.split_train_forward(rows)

    assert [row["state_id"] for row in train] == ["train"]
    assert [row["state_id"] for row in holdout] == [
        "forward_1",
        "forward_2",
    ]


def test_official_fee_is_rounded_per_share_to_five_decimals() -> None:
    assert forward.official_fee_per_share(0.5) == 0.0125
    assert forward.official_fee_per_share(0.001) == 0.00005


def test_candidate_policy_only_expresses_current_and_next_brackets() -> None:
    row = {
        "state_id": "state",
        "target_date": "2026-07-20",
        "settlement_lower_bound_violation": 0,
        "current_bracket": 30,
        "winning_bracket": "31",
        "quotes_json": json.dumps(
            {
                "30": {"ask": 0.4, "bid": 0.35},
                "31": {"ask": 0.3, "bid": 0.25},
                "32": {"ask": 0.2, "bid": 0.15},
            }
        ),
        "model_distribution_json": json.dumps([0.3, 0.4, 0.2, 0.1]),
    }

    candidates = forward.current_next_candidates([row], ["model"])

    assert len(candidates) == 4
    assert {row["expression_delta"] for row in candidates} == {0, 1}
    assert {row["expression_bracket"] for row in candidates} == {"30", "31"}
    assert {row["side"] for row in candidates} == {"YES", "NO"}


def test_first_signal_policy_does_not_select_later_better_state(
    monkeypatch,
) -> None:
    candidates = [
        {
            "model": "model",
            "state_id": "early",
            "target_date": "2026-07-20",
            "availability_ts_utc": "2026-07-20T01:00:00+00:00",
            "snapshot_ts_utc": "2026-07-20T01:00:00+00:00",
            "expression_bracket": "30",
            "expression_delta": 0,
            "side": "YES",
            "fee_adjusted_edge": 0.03,
            "selected_side_ask": 0.4,
            "fee_per_share": 0.012,
            "winning_bracket": "30",
        },
        {
            "model": "model",
            "state_id": "later",
            "target_date": "2026-07-20",
            "availability_ts_utc": "2026-07-20T01:10:00+00:00",
            "snapshot_ts_utc": "2026-07-20T01:10:00+00:00",
            "expression_bracket": "31",
            "expression_delta": 1,
            "side": "YES",
            "fee_adjusted_edge": 0.20,
            "selected_side_ask": 0.2,
            "fee_per_share": 0.008,
            "winning_bracket": "31",
        },
    ]
    monkeypatch.setattr(
        forward.v1,
        "raw_ask_size",
        lambda *_args, **_kwargs: 10.0,
    )

    selected = forward.select_first_signal(
        candidates, raw_books=forward.Path(".")
    )

    assert len(selected) == 1
    assert selected[0]["state_id"] == "early"
    assert selected[0]["settled_win"] == 1
    assert selected[0]["fee_adjusted_pnl_usd"] > 0


def test_probability_bins_cover_full_unit_interval() -> None:
    assert forward.probability_bin(0.0) == "[0,.05)"
    assert forward.probability_bin(0.05) == "[.05,.10)"
    assert forward.probability_bin(0.9) == "[.90,1]"
    assert forward.probability_bin(1.0) == "[.90,1]"


def test_wilson_interval_keeps_zero_win_uncertainty_visible() -> None:
    low, high = forward.wilson_interval(0, 12)

    assert low == 0
    assert 0.24 < high < 0.25


def test_episode_state_features_are_prefix_only_and_detect_reheat() -> None:
    base = {
        "target_date": "2026-07-20",
        "current_bracket": 30,
        "jma_temp_slope_30m_cph": 0.6,
        "jma_temp_slope_60m_cph": -0.2,
    }
    prefix = [
        {
            **base,
            "decision_ts_utc": "2026-07-20T01:00:00+00:00",
            "jma_temp_c": 30.4,
            "jma_temp_delta_10m": 0.1,
        },
        {
            **base,
            "decision_ts_utc": "2026-07-20T01:10:00+00:00",
            "jma_temp_c": 30.0,
            "jma_temp_delta_10m": -0.4,
        },
        {
            **base,
            "decision_ts_utc": "2026-07-20T01:20:00+00:00",
            "jma_temp_c": 30.3,
            "jma_temp_delta_10m": 0.3,
        },
    ]
    prefix_scored = forward.add_episode_state_features(prefix)
    with_future = forward.add_episode_state_features(
        prefix
        + [
            {
                **base,
                "decision_ts_utc": "2026-07-20T01:30:00+00:00",
                "jma_temp_c": 31.0,
                "jma_temp_delta_10m": 0.7,
            }
        ]
    )

    assert prefix_scored == with_future[: len(prefix)]
    recovered = prefix_scored[-1]
    assert recovered["jma_episode_giveback_c"] == pytest.approx(0.4)
    assert recovered["jma_recovery_from_trough_c"] == pytest.approx(0.3)
    assert recovered["jma_has_pullback_then_recovery"] == 1
    assert recovered["jma_reheat_active"] == 1


def test_episode_cross_count_tracks_recross_at_current_boundary() -> None:
    rows = [
        {
            "target_date": "2026-07-20",
            "decision_ts_utc": f"2026-07-20T01:{minute:02d}:00+00:00",
            "current_bracket": 30,
            "jma_temp_c": temperature,
            "jma_temp_delta_10m": 0.0,
            "jma_temp_slope_30m_cph": 0.0,
            "jma_temp_slope_60m_cph": 0.0,
        }
        for minute, temperature in ((0, 30.4), (10, 30.6), (20, 30.4), (30, 30.7))
    ]

    scored = forward.add_episode_state_features(rows)

    assert [row["jma_cross_count_current_boundary"] for row in scored] == [
        0,
        1,
        2,
        3,
    ]


def test_settlement_reference_is_label_only_and_requires_one_winner(
    tmp_path: Path,
) -> None:
    path = tmp_path / "reference.csv"
    path.write_text(
        "target_date,winning_bracket,p_model\n"
        "2026-07-20,31,0.9\n"
        "2026-07-20,31,0.1\n",
        encoding="utf-8",
    )

    winners = forward.load_winners_from_reference_rows(path)

    assert winners == {"2026-07-20": "31"}


def test_book_join_uses_latest_state_available_at_book_time() -> None:
    rows = [
        {
            "state_id": "old",
            "target_date": "2026-07-20",
            "decision_ts_utc": "2026-07-20T00:00:00+00:00",
            "current_bracket": 29,
            "local_hour": 9,
            "path_phase": "warming",
            "is_transition": 1,
            "is_state_entry": 1,
        },
        {
            "state_id": "new",
            "target_date": "2026-07-20",
            "decision_ts_utc": "2026-07-20T00:10:00+00:00",
            "current_bracket": 30,
            "local_hour": 9.1667,
            "path_phase": "new_high",
            "is_transition": 1,
            "is_state_entry": 1,
        },
    ]
    predictions = {
        "model": np.asarray(
            [[0.4, 0.3, 0.2, 0.1], [0.2, 0.5, 0.2, 0.1]]
        )
    }
    markets = {
        "2026-07-20": [
            {
                "timestamp": datetime(
                    2026, 7, 20, 0, 27, tzinfo=timezone.utc
                ),
                "quotes": {
                    "ask": {"30": 0.4, "31": 0.3},
                    "bid": {"30": 0.35, "31": 0.25},
                    "mid": {"30": 0.375, "31": 0.275},
                },
            }
        ]
    }

    joined = forward.join_market_asof_books(
        rows,
        predictions,
        exact={},
        markets=markets,
        winners={"2026-07-20": "31"},
    )

    # Archive availability is observation+15m: old=00:15, new=00:25.
    assert len(joined) == 1
    assert joined[0]["state_id"] == "new"
    assert joined[0]["availability_to_book_min"] == 2
    assert json.loads(joined[0]["model_distribution_json"]) == [
        0.2,
        0.5,
        0.2,
        0.1,
    ]


def test_first_per_bracket_keeps_new_brackets_without_add_ons(
    monkeypatch,
) -> None:
    monkeypatch.setattr(forward.v1, "raw_ask_size", lambda *_: 10.0)
    base = {
        "model": "model",
        "target_date": "2026-07-20",
        "winning_bracket": "31",
        "selected_side_ask": 0.2,
        "fee_per_share": 0.0,
        "fee_adjusted_edge": 0.1,
        "snapshot_ts_utc": "2026-07-20T01:00:00+00:00",
        "availability_ts_utc": "2026-07-20T01:00:00+00:00",
        "side": "YES",
    }
    candidates = [
        {
            **base,
            "state_id": "29-first",
            "expression_bracket": "29",
        },
        {
            **base,
            "state_id": "29-add-on",
            "expression_bracket": "29",
            "snapshot_ts_utc": "2026-07-20T01:10:00+00:00",
            "availability_ts_utc": "2026-07-20T01:10:00+00:00",
            "fee_adjusted_edge": 0.3,
        },
        {
            **base,
            "state_id": "30-first",
            "expression_bracket": "30",
            "snapshot_ts_utc": "2026-07-20T01:20:00+00:00",
            "availability_ts_utc": "2026-07-20T01:20:00+00:00",
        },
    ]

    selected = forward.select_first_signal(
        candidates,
        Path("unused"),
        selection_policy=(
            "first_signal_per_model_target_date_bracket"
        ),
    )

    assert [
        (row["expression_bracket"], row["state_id"]) for row in selected
    ] == [("29", "29-first"), ("30", "30-first")]
    assert all(row["add_on_allowed"] == 0 for row in selected)


def test_full_distribution_fusion_preserves_normalization_and_market_tails() -> None:
    market = np.asarray([[0.10, 0.20, 0.30, 0.40]])
    weather = np.asarray([[0.40, 0.30, 0.20, 0.10]])

    fused = forward.full_distribution_geometric_pool(
        market,
        weather,
        market_temperature=1.0,
        weather_weight=0.5,
    )

    assert fused.shape == (1, 4)
    assert fused.sum(axis=1) == pytest.approx([1.0])
    assert np.all(fused > 0)
    assert fused[0, 2] > 0
    assert fused[0, 3] > 0


def test_full_probability_fusion_audit_freezes_after_temporal_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = []
    for day in range(16, 25):
        target_date = f"2026-07-{day:02d}"
        actual_delta = day % 2
        winner = str(30 + actual_delta)
        market = [0.55, 0.35, 0.08, 0.02]
        weather = (
            [0.90, 0.07, 0.02, 0.01]
            if actual_delta == 0
            else [0.08, 0.86, 0.04, 0.02]
        )
        row = {
            "state_id": target_date,
            "target_date": target_date,
            "snapshot_ts_utc": f"{target_date}T01:00:00+00:00",
            "availability_ts_utc": f"{target_date}T01:00:00+00:00",
            "current_bracket": 30,
            "winning_bracket": winner,
            "actual_delta": actual_delta,
            "settlement_lower_bound_violation": 0,
            "market_distribution_json": json.dumps(market),
            "quotes_json": json.dumps(
                {
                    "30": {"ask": 0.58, "bid": 0.52},
                    "31": {"ask": 0.38, "bid": 0.32},
                }
            ),
        }
        for model in forward.FULL_FUSION_WEATHER_MODELS:
            row[f"{model}_distribution_json"] = json.dumps(weather)
        rows.append(row)
    input_path = tmp_path / "market_join_rows.csv"
    forward.write_rows(input_path, rows)
    monkeypatch.setattr(forward.v1, "raw_ask_size", lambda *_args: 10.0)

    summary = forward.run_full_probability_fusion_audit(
        input_path=input_path,
        output_dir=tmp_path / "out",
        raw_books=tmp_path,
        selection_end="2026-07-21",
        clean_forward_start="2026-08-13",
    )

    assert summary["validation"]["target_dates"] == 3
    assert summary["selection"]["selected"]["clean_forward_start"] == (
        "2026-08-13"
    )
    assert summary["selection"]["candidate_count_k"] == (
        len(forward.FULL_FUSION_WEATHER_MODELS)
        * len(forward.FULL_FUSION_MARKET_TEMPERATURES)
        * len(forward.FULL_FUSION_WEATHER_WEIGHTS)
    )
    assert (tmp_path / "out" / "frozen_candidate_spec.json").exists()
