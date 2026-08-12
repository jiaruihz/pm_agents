from __future__ import annotations
from datetime import datetime, timezone

import pandas as pd

from weather_model_evaluation.rest_quote_path import RestQuote, attach_first_touch_labels


def _epoch(minutes: float) -> float:
    base = datetime(2026, 8, 10, tzinfo=timezone.utc).timestamp()
    return base + minutes * 60.0


def _quote(minutes: float, *, bid: float, ask: float, exact: bool = True) -> RestQuote:
    return RestQuote(
        epoch=_epoch(minutes),
        bid=bid,
        ask=ask,
        bid_size=50.0,
        ask_size=50.0,
        available_at_utc=datetime.fromtimestamp(
            _epoch(minutes), timezone.utc
        ).isoformat(),
        clock_lineage_status=(
            "collector_exact_response_clock" if exact else "legacy_fetched_at_clock"
        ),
        event_time_pit_scorable=exact,
        source_path="fixture.jsonl.gz",
        capture_id=f"capture-{minutes}",
    )


def _action() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "forecast_event_id": "event-1",
                "city": "Tokyo",
                "target_date": "2026-08-11",
                "condition_id": "condition-1",
                "bracket": "33",
                "snapshot_epoch": _epoch(0),
                "quote_price": 0.20,
            }
        ]
    )


def test_first_touch_uses_touch_relative_exit_clock() -> None:
    history = {
        "condition-1": [
            _quote(5, bid=0.18, ask=0.22),
            _quote(15, bid=0.17, ask=0.20),
            _quote(25, bid=0.19, ask=0.21),
            _quote(65, bid=0.23, ask=0.25),
            _quote(75, bid=0.24, ask=0.26),
        ]
    }
    row = attach_first_touch_labels(_action(), history).iloc[0]
    assert row["path_touch_proxy"] == True  # noqa: E712
    assert row["first_touch_after_min"] == 15.0
    assert row["fill_relative_exit_gap_min"] == 0.0
    assert row["fill_relative_exit_bid"] == 0.24
    assert row["path_label_status"] == "scoreable_observed_touch_fill_relative_exit"
    assert row["path_clock_grade"] == "collector_exact"


def test_complete_non_touch_path_is_zero_cost_label() -> None:
    history = {
        "condition-1": [
            _quote(5, bid=0.15, ask=0.23),
            _quote(15, bid=0.16, ask=0.22),
            _quote(25, bid=0.16, ask=0.21),
        ]
    }
    row = attach_first_touch_labels(_action(), history).iloc[0]
    assert row["path_touch_proxy"] == False  # noqa: E712
    assert row["path_expected_pnl_label"] == 0.0
    assert row["path_label_status"] == "scoreable_no_observed_touch"


def test_sparse_non_touch_path_is_coverage_blocked() -> None:
    history = {"condition-1": [_quote(15, bid=0.15, ask=0.23)]}
    row = attach_first_touch_labels(_action(), history).iloc[0]
    assert pd.isna(row["path_touch_proxy"])
    assert pd.isna(row["path_expected_pnl_label"])
    assert row["path_label_status"] == "coverage_blocked"
