from __future__ import annotations

import gzip
import json
from pathlib import Path

from weather_data_feed.market_book_ladder_history import iter_market_book_ladders


def _row(city: str, bracket: str, outcome: str, *, reason: str = "scheduled_full_ladder_snapshot") -> dict:
    return {
        "city": city,
        "event_date": "2026-08-07",
        "event_slug": "highest-temperature-in-amsterdam-on-august-7-2026",
        "bracket": bracket,
        "condition_id": f"condition-{bracket}",
        "market_id": f"market-{bracket}",
        "outcome": outcome,
        "token_id": f"{outcome}-{bracket}",
        "status": "ok",
        "capture_reason": reason,
        "request_batch_capture_id": "batch-1",
        "available_at_utc": "2026-08-07T10:00:01Z" if outcome == "yes" else "2026-08-07T10:00:02Z",
        "summary": {
            "best_bid": 0.1,
            "best_ask": 0.2,
            "bid_size": 10,
            "ask_size": 20,
            "depth_bid_5c": 30,
            "depth_ask_5c": 40,
            "depth_bid_10c": 50,
            "depth_ask_10c": 60,
        },
    }


def test_iter_market_book_ladders_preserves_batch_and_direct_quotes(tmp_path: Path) -> None:
    directory = tmp_path / "2026-08-07"
    directory.mkdir()
    path = directory / "market_books.jsonl.gz"
    rows = [_row("Amsterdam", str(bracket), outcome) for bracket in range(20, 28) for outcome in ("yes", "no")]
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    ladders = list(iter_market_book_ladders(tmp_path, "2026-08-07", "2026-08-07"))
    assert len(ladders) == 1
    meta, records = ladders[0]
    assert meta["source_system"] == "weather_market_books_full_ladder"
    assert meta["source_snapshot_ts_utc"] == "2026-08-07T10:00:02.000Z"
    assert len(records) == 8
    assert records[0]["unit"] == "C"
    assert records[0]["yes_best_bid"] == 0.1
    assert records[0]["no_best_ask"] == 0.2


def test_iter_market_book_ladders_rejects_hot_subset(tmp_path: Path) -> None:
    directory = tmp_path / "2026-08-07"
    directory.mkdir()
    path = directory / "market_books.jsonl.gz"
    rows = [
        _row("Amsterdam", str(bracket), outcome, reason="strategy_hot")
        for bracket in range(20, 23)
        for outcome in ("yes", "no")
    ]
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    assert list(iter_market_book_ladders(tmp_path, "2026-08-07", "2026-08-07")) == []


def test_iter_market_book_ladders_preserves_complete_lattice_with_missing_complementary_book(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "2026-08-07"
    directory.mkdir()
    path = directory / "market_books.jsonl.gz"
    rows = [_row("Amsterdam", str(bracket), outcome) for bracket in range(20, 28) for outcome in ("yes", "no")]
    rows = [row for row in rows if not (row["bracket"] == "24" and row["outcome"] == "no")]
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    ladders = list(iter_market_book_ladders(tmp_path, "2026-08-07", "2026-08-07"))
    assert len(ladders) == 1
    _, records = ladders[0]
    missing_complement = next(row for row in records if row["bracket"] == "24")
    assert missing_complement["yes_token_id"] == "yes-24"
    assert missing_complement["no_token_id"] is None
    assert missing_complement["no_best_ask"] is None
