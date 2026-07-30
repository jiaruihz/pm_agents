from __future__ import annotations

from datetime import datetime, timezone
import gzip
import json

from scripts.analysis.market_structure_edge.research_tokyo_jma_multivariate_market_v1 import (
    load_books,
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
