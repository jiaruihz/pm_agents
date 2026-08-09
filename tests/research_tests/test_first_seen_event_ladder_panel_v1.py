from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_model_evaluation.first_seen_event_ladder_panel import (
    _effective_yes_quote,
    load_collector_exact_metar_reports,
    load_direct_event_snapshots,
    materialize_panel,
    normalize_source_event,
)
from weather_model_evaluation.ordinal_model import OrderedThresholdClassifier
from weather_model_evaluation.frozen_probability_artifact import score_frozen_artifact


def _snapshot(anchor: datetime, offset: int, snapshot_id: str, *, exact: bool = True):
    return {
        "snapshot_id": snapshot_id,
        "city": "Helsinki",
        "target_date": "2026-08-08",
        "available_at_utc": (anchor + timedelta(seconds=offset)).isoformat(),
        "event_time_pit_scorable": exact,
        "rungs": [
            {
                "bracket": "20",
                "condition_id": "c20",
                "market_id": "m20",
                "yes_token_id": "y20",
                "no_token_id": "n20",
                "yes_bid": 0.39,
                "yes_ask": 0.41,
                "yes_mid": 0.40,
                "yes_spread": 0.02,
                "yes_bid_size": 10.0,
                "yes_ask_size": 11.0,
            },
            {
                "bracket": "21+",
                "condition_id": "c21",
                "market_id": "m21",
                "yes_token_id": "y21",
                "no_token_id": "n21",
                "yes_bid": 0.59,
                "yes_ask": 0.61,
                "yes_mid": 0.60,
                "yes_spread": 0.02,
                "yes_bid_size": 12.0,
                "yes_ask_size": 13.0,
            },
        ],
    }


def test_materialize_panel_preserves_all_events_and_missing_slots():
    anchor = datetime(2026, 8, 8, 8, 0, tzinfo=UTC)
    events = [
        {
            "event_id": "e1",
            "city": "Helsinki",
            "source": "fmi",
            "target_date": "2026-08-08",
            "first_seen_at_utc": anchor.isoformat(),
            "observed_at_utc": (anchor - timedelta(minutes=10)).isoformat(),
        },
        {
            "event_id": "e2",
            "city": "Helsinki",
            "source": "fmi",
            "target_date": "2026-08-08",
            "first_seen_at_utc": (anchor + timedelta(minutes=10)).isoformat(),
            "observed_at_utc": anchor.isoformat(),
        },
    ]
    snapshots = [
        _snapshot(anchor, -20, "pre"),
        _snapshot(anchor, 5, "t0"),
        _snapshot(anchor, 35, "p30"),
        _snapshot(anchor, 130, "p120"),
        _snapshot(anchor, 305, "p300"),
        _snapshot(anchor, 605, "next"),
    ]
    panel, event_panel, coverage = materialize_panel(events, snapshots)
    assert len(event_panel) == 2
    assert len(panel[panel["event_id"].eq("e1")]) == 2
    assert coverage["signal_funnel"]["material_unique_events"] == 2
    assert coverage["evidence_funnel"]["events_with_all_markouts"] == 1
    second = event_panel[event_panel["event_id"].eq("e2")].iloc[0]
    assert second["p300_status"] == "no_post_snapshot"
    assert coverage["slot_coverage_rate"]["p300"] == 0.5


def test_normalize_source_event_keeps_collector_clocks():
    event = normalize_source_event(
        {
            "city": "Helsinki",
            "source": "fmi",
            "information_event_id": "event-1",
            "target_date": "2026-08-08",
            "observation_time_utc": "2026-08-08T07:50:00Z",
            "first_seen_at_utc": "2026-08-08T08:00:19Z",
            "available_at_utc": "2026-08-08T08:00:21Z",
            "detected_at_utc": "2026-08-08T08:00:19Z",
            "pit_lineage_class": "collector_exact",
            "material_state_change": True,
            "temp_c": 21.2,
        },
        city="Helsinki",
        source="fmi",
    )
    assert event is not None
    assert event["event_id"] == "event-1"
    assert event["first_seen_at_utc"].endswith("Z")
    assert event["available_at_utc"] == "2026-08-08T08:00:21Z"
    assert event["pit_lineage_class"] == "collector_exact"


def test_ordered_threshold_classifier_probability_is_coherent():
    x = np.arange(18, dtype=float).reshape(-1, 1)
    y = np.repeat(np.arange(3), 6)
    model = OrderedThresholdClassifier(
        LogisticRegression(max_iter=200),
        n_classes=3,
    ).fit(x, y)
    probability = model.predict_proba(x)
    assert probability.shape == (18, 3)
    assert np.all(probability >= 0)
    np.testing.assert_allclose(probability.sum(axis=1), 1.0)


def test_frozen_probability_artifact_scores_ensemble_without_reweighting():
    frame = pd.DataFrame({"x": [0.0, 1.0, 2.0, 3.0], "y": [0, 0, 1, 1]})
    model_a = LogisticRegression().fit(frame[["x"]], frame["y"])
    model_b = LogisticRegression(C=0.5).fit(frame[["x"]], frame["y"])
    artifact = {
        "classes": ["0", "1"],
        "ensemble_members": ["a", "b"],
        "ensemble_weights": {"a": 0.25, "b": 0.75},
        "ensemble_models": {"a": model_a, "b": model_b},
        "ensemble_features": {"a": ["x"], "b": ["x"]},
    }
    probability = score_frozen_artifact(artifact, frame)
    expected = 0.25 * model_a.predict_proba(frame[["x"]]) + 0.75 * model_b.predict_proba(frame[["x"]])
    np.testing.assert_allclose(probability, expected)


def test_metar_source_event_loader_deduplicates_poll_revisions(tmp_path):
    path = tmp_path / "sources.jsonl"
    base = {
        "city": "Helsinki",
        "source": "aviationweather_metar",
        "status": "ok",
        "target_date": "2026-08-08",
        "station": "EFHK",
        "source_report_ts_utc": "2026-08-08T08:20:00Z",
        "temp_c": 20.0,
        "pit_lineage_class": "collector_exact",
        "original_first_seen_unknown": False,
    }
    rows = [
        {**base, "information_event_id": "later", "first_seen_at_utc": "2026-08-08T08:31:00Z"},
        {**base, "information_event_id": "first", "first_seen_at_utc": "2026-08-08T08:30:10Z"},
    ]
    path.write_text("".join(__import__("json").dumps(row) + "\n" for row in rows))
    frame, coverage = load_collector_exact_metar_reports(
        [path],
        city="Helsinki",
        start_date="2026-08-08",
        end_date="2026-08-08",
    )
    assert len(frame) == 1
    assert frame.iloc[0]["information_event_id"] == "first"
    assert coverage["eligible_raw_rows"] == 2
    assert coverage["unique_reports"] == 1


def test_effective_yes_quote_keeps_size_from_selected_no_complement():
    quote = _effective_yes_quote(
        {
            "summary": {
                "best_bid": 0.40,
                "best_ask": 0.48,
                "bid_size": 10.0,
                "ask_size": 11.0,
            }
        },
        {
            "summary": {
                "best_bid": 0.55,
                "best_ask": 0.57,
                "bid_size": 20.0,
                "ask_size": 21.0,
            }
        },
    )
    assert quote["yes_bid"] == pytest.approx(0.43)
    assert quote["yes_bid_size"] == 21.0
    assert quote["yes_ask"] == pytest.approx(0.45)
    assert quote["yes_ask_size"] == 20.0


def test_direct_snapshot_rejects_out_of_order_book_clocks(tmp_path):
    day = tmp_path / "snapshots" / "2026-08-08"
    day.mkdir(parents=True)
    payload = {
        "target_date": "2026-08-08",
        "capture_started_at_utc": "2026-08-08T08:00:00Z",
        "ts_utc": "2026-08-08T08:00:03Z",
        "records": [
            {
                "city": "Helsinki",
                "bracket": "20",
                "yes_book_status": "ok",
                "yes_book_request_started_at_utc": "2026-08-08T08:00:01Z",
                "yes_book_response_received_at_utc": "2026-08-08T08:00:00Z",
                "yes_book_parsed_at_utc": "2026-08-08T08:00:02Z",
                "no_book_status": "ok",
                "no_book_request_started_at_utc": "2026-08-08T08:00:00Z",
                "no_book_response_received_at_utc": "2026-08-08T08:00:01Z",
                "no_book_parsed_at_utc": "2026-08-08T08:00:02Z",
            }
        ],
    }
    (day / "snapshot_bad_clock.json").write_text(
        __import__("json").dumps(payload), encoding="utf-8"
    )
    snapshots, coverage = load_direct_event_snapshots(
        tmp_path,
        city="Helsinki",
        start_date="2026-08-08",
        end_date="2026-08-08",
    )
    assert snapshots[0]["available_at_utc"] == "2026-08-08T08:00:03.000000Z"
    assert snapshots[0]["event_time_pit_scorable"] is False
    assert coverage.get("exact_clock_snapshots", 0) == 0


def test_direct_snapshot_uses_completion_clock_and_effective_quote(tmp_path):
    day = tmp_path / "snapshots" / "2026-08-08"
    day.mkdir(parents=True)
    clock_fields = {
        "yes_book_status": "ok",
        "yes_book_request_started_at_utc": "2026-08-08T08:00:00Z",
        "yes_book_response_received_at_utc": "2026-08-08T08:00:01Z",
        "yes_book_parsed_at_utc": "2026-08-08T08:00:02Z",
        "no_book_status": "ok",
        "no_book_request_started_at_utc": "2026-08-08T08:00:00Z",
        "no_book_response_received_at_utc": "2026-08-08T08:00:01Z",
        "no_book_parsed_at_utc": "2026-08-08T08:00:02Z",
    }
    payload = {
        "target_date": "2026-08-08",
        "capture_started_at_utc": "2026-08-08T08:00:00Z",
        "ts_utc": "2026-08-08T08:00:03Z",
        "records": [
            {
                "city": "Helsinki",
                "bracket": "20",
                "yes_best_bid": 0.40,
                "yes_best_ask": 0.48,
                "yes_bid_size": 10.0,
                "yes_ask_size": 11.0,
                "no_best_bid": 0.55,
                "no_best_ask": 0.57,
                "no_bid_size": 20.0,
                "no_ask_size": 21.0,
                **clock_fields,
            }
        ],
    }
    (day / "snapshot_exact.json").write_text(
        __import__("json").dumps(payload), encoding="utf-8"
    )
    snapshots, coverage = load_direct_event_snapshots(
        tmp_path,
        city="Helsinki",
        start_date="2026-08-08",
        end_date="2026-08-08",
    )
    assert snapshots[0]["event_time_pit_scorable"] is True
    assert snapshots[0]["rungs"][0]["yes_bid"] == pytest.approx(0.43)
    assert snapshots[0]["rungs"][0]["yes_bid_size"] == 21.0
    assert coverage["exact_clock_snapshots"] == 1
