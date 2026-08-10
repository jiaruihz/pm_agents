from __future__ import annotations

from datetime import datetime, timezone
import gzip
import json
import pytest

from scripts.analysis.market_structure_edge.research_tokyo_jma_multivariate_market_v1 import (
    first_margin_events,
    load_books,
    load_final_settlement_predictions,
    load_tokyo_winners_from_pm_history,
    paired_policy_delta,
    policy_summary,
    roi_bootstrap,
    scheduled_report_ts,
    source_phase,
)
from scripts.analysis.market_structure_edge.research_tokyo_jma_multivariate_path_v2 import (
    build_first_cross_events,
)


UTC = timezone.utc


def test_first_cross_denominator_deduplicates_date_lattice() -> None:
    base = {
        "target_date": "2026-07-01",
        "local_hour": "10",
        "jma_rounded_c": "31",
        "prior_metar_running_max_c": "30",
    }
    rows = [
        {**base, "decision_ts_utc": "2026-07-01T01:20:00+00:00"},
        {**base, "decision_ts_utc": "2026-07-01T01:10:00+00:00"},
        {
            **base,
            "decision_ts_utc": "2026-07-01T01:00:00+00:00",
            "jma_rounded_c": "30",
        },
    ]

    events = build_first_cross_events(rows)

    assert len(events) == 1
    assert events[0]["decision_ts_utc"] == "2026-07-01T01:10:00+00:00"
    assert events[0]["prior_bracket"] == 30


def test_targeted_book_loader_recovers_complementary_no_quote(tmp_path) -> None:
    day = tmp_path / "2026-07-20"
    day.mkdir()
    path = day / "orderbook_snapshot_20260720_1200.jsonl.gz"
    common = {
        "city": "Tokyo",
        "event_date": "2026-07-20",
        "bracket": "34",
        "snapshot_ts_utc": "2026-07-20T04:00:00Z",
        "status": "ok",
    }
    rows = [
        {
            **common,
            "outcome": "yes",
            "summary": {
                "best_bid": 0.2,
                "best_ask": 0.3,
                "bid_size": 10,
                "ask_size": 11,
            },
        },
        {
            **common,
            "outcome": "no",
            "summary": {
                "best_bid": 0.7,
                "best_ask": None,
                "bid_size": 11,
                "ask_size": None,
            },
        },
    ]
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    books = load_books(
        tmp_path,
        [("2026-07-20", datetime(2026, 7, 20, 3, 59, tzinfo=UTC))],
    )

    quote = books[("2026-07-20", "34")][0]
    assert quote["no_ask"] == 0.8
    assert quote["no_bid"] == 0.7
    assert quote["no_mid"] == 0.75


def test_source_phase_maps_to_same_scheduled_report() -> None:
    early = {"source_obs_ts_utc": "2026-07-20T03:40:00Z"}
    late = {"source_obs_ts_utc": "2026-07-20T03:50:00Z"}

    assert source_phase(early) == ("T13", 13.0)
    assert source_phase(late) == ("T3", 3.0)
    assert scheduled_report_ts(early) == "2026-07-20T04:00:00+00:00"
    assert scheduled_report_ts(late) == "2026-07-20T04:00:00+00:00"


def test_pm_history_loader_reads_only_requested_days(tmp_path) -> None:
    payload = {
        "brackets": [
            {"label": "30", "final_price": 0.0005},
            {"label": "31", "final_price": 0.9995},
        ]
    }
    (tmp_path / "Tokyo_2026-07-20.json").write_text(json.dumps(payload))
    (tmp_path / "Tokyo_2026-07-21.json").write_text("not json")

    winners = load_tokyo_winners_from_pm_history(tmp_path, {"2026-07-20"})

    assert winners == {"2026-07-20": "31"}


def test_policy_summary_uses_partial_top_depth_and_weather_fee() -> None:
    row = {
        "target_date": "2026-07-20",
        "market_bracket": "30",
        "ts_utc": "2026-07-20T03:57:00Z",
        "best_ask": 0.8,
        "ask_size": 8.0,
        "settlement_no_wins": 1,
        "replay_target_shares": 15.0,
        "replay_min_shares": 5.0,
        "replay_max_ask": 0.97,
    }

    summary, trades = policy_summary([row], "direct", lambda _: True)

    assert trades[0]["shares"] == 8.0
    assert trades[0]["fees_usd"] == pytest.approx(8.0 * 0.05 * 0.8 * 0.2)
    assert trades[0]["entry_cost_usd"] == pytest.approx(
        8.0 * (0.8 + 0.05 * 0.8 * 0.2)
    )
    assert summary["wins"] == 1


def test_first_margin_events_keeps_earliest_date_bracket_at_each_threshold() -> None:
    rows = [
        {
            "target_date": "2026-07-20",
            "market_bracket": "30",
            "actual_source_margin_c": margin,
            "ts_utc": timestamp,
        }
        for margin, timestamp in (
            (0.5, "2026-07-20T03:10:01Z"),
            (0.7, "2026-07-20T03:20:01Z"),
            (0.8, "2026-07-20T03:30:01Z"),
        )
    ]

    first_05 = first_margin_events(rows, 0.5)
    first_07 = first_margin_events(rows, 0.7)

    assert first_05[0]["ts_utc"] == "2026-07-20T03:10:01Z"
    assert first_07[0]["ts_utc"] == "2026-07-20T03:20:01Z"


def test_final_settlement_loader_keys_prediction_by_prior_bracket(tmp_path) -> None:
    path = tmp_path / "predictions.csv.gz"
    rows = [
        {
            "target_date": "2026-07-20",
            "decision_ts_utc": "2026-07-20T03:10:00Z",
            "target_id": "final_break",
            "model_id": "event_safe_selector_v2",
            "prior_bracket": "30.0",
            "p_model": "0.8",
        }
    ]
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(",".join(rows[0]) + "\n")
        handle.write(",".join(rows[0][key] for key in rows[0]) + "\n")

    loaded = load_final_settlement_predictions(path)

    assert loaded[("2026-07-20", "2026-07-20T03:10:00+00:00", "30")][
        "p_model"
    ] == "0.8"


def test_bootstrap_aggregates_multiple_brackets_by_target_date() -> None:
    rows = [
        {
            "target_date": "2026-07-20",
            "fee_adjusted_pnl_usd": 2.0,
            "entry_cost_usd": 8.0,
        },
        {
            "target_date": "2026-07-20",
            "fee_adjusted_pnl_usd": -1.0,
            "entry_cost_usd": 2.0,
        },
    ]

    roi, low, high = roi_bootstrap(rows)

    assert roi == pytest.approx(0.1)
    assert low == pytest.approx(0.1)
    assert high == pytest.approx(0.1)


def test_paired_delta_bootstraps_target_dates_including_zero_trade_days() -> None:
    baseline = [
        {
            "target_date": "2026-07-20",
            "fee_adjusted_pnl_usd": 1.0,
            "entry_cost_usd": 5.0,
        }
    ]
    challenger = [
        {
            "target_date": "2026-07-21",
            "fee_adjusted_pnl_usd": 2.0,
            "entry_cost_usd": 5.0,
        }
    ]

    result = paired_policy_delta(
        baseline, challenger, ["2026-07-20", "2026-07-21", "2026-07-22"]
    )

    assert result["denominator_dates"] == 3
    assert result["fee_adjusted_pnl_delta_usd"] == pytest.approx(1.0)
