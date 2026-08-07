import argparse
import json
from datetime import datetime, timezone

from weather_data_feed_service import market_books
from weather_data_feed_service.legacy_weather_predict import paper_snapshot


def _book(token_id: str) -> dict:
    return {
        "status": "ok",
        "token_id": token_id,
        "request_started_at_utc": "2026-08-07T12:00:00.000Z",
        "response_received_at_utc": "2026-08-07T12:00:00.100Z",
        "parsed_at_utc": "2026-08-07T12:00:00.101Z",
        "fetched_at_utc": "2026-08-07T12:00:00.100Z",
        "summary": {"best_bid": 0.4, "best_ask": 0.5, "bids": [], "asks": []},
        "raw": {"bids": [], "asks": [], "timestamp": "1", "hash": token_id},
    }


def test_market_books_collects_raw_before_weather_views(monkeypatch, tmp_path):
    events = [
        {
            "city": "Amsterdam",
            "target_date": "2026-08-08",
            "event_slug": "weather-amsterdam",
            "event_id": "event-1",
            "condition_count": 1,
            "city_local_date_at_capture": "2026-08-07",
            "strategy_targets": {("20", "yes")},
            "entries": [
                {
                    "label": "20",
                    "market_id": "market-1",
                    "condition_id": "condition-1",
                    "yes_token_id": "yes-1",
                    "no_token_id": "no-1",
                }
            ],
        }
    ]
    monkeypatch.setattr(market_books, "discover_market_ladders", lambda **_kwargs: (events, []))

    def fake_fetch(_client, request_rows, token_ids, **_kwargs):
        return {token_id: (request_rows[token_id], _book(token_id)) for token_id in token_ids}

    monkeypatch.setattr(market_books, "_fetch_priority_group", fake_fetch)
    monkeypatch.setattr(market_books.httpx, "Client", lambda **_kwargs: type("C", (), {"close": lambda self: None})())

    books = tmp_path / "market_books"
    ladders = tmp_path / "market_ladder_snapshots"
    legacy = tmp_path / "full_ladder_output" / "orderbook_snapshots"
    result = market_books.collect(
        argparse.Namespace(
            output_root=str(books),
            market_ladder_root=str(ladders),
            legacy_full_orderbook_root=str(legacy),
            observation_cache="",
            target_date=None,
            now_utc="2026-08-07T12:00:00Z",
            orderbook_top_n=20,
            orderbook_budget_sec=240.0,
        )
    )

    latest = json.loads((books / "latest.json").read_text())
    ladder = json.loads((ladders / "latest.json").read_text())
    assert result["status"] == "ok"
    assert latest["summary"]["hot_tokens"] == 1
    assert latest["summary"]["cold_tokens"] == 1
    assert latest["summary"]["forecast_dependency"] is False
    assert ladder["records"][0]["market_distribution_complete"] is True
    assert result["legacy_orderbook_path"]


def test_strategy_view_reads_canonical_books_but_keeps_target_scope(tmp_path):
    latest = tmp_path / "latest.json"
    latest.write_text(
        json.dumps(
            {
                "available_at_utc": "2026-08-07T12:00:00Z",
                "batch_capture_id": "batch-1",
                "archive_path": "archive.gz",
                "records": [{"token_id": "yes-1", **_book("yes-1")}],
            }
        )
    )
    books, source = paper_snapshot.load_canonical_orderbook_latest(
        latest,
        now_utc=datetime(2026, 8, 7, 12, 1, tzinfo=timezone.utc),
        max_age_sec=420,
    )

    assert source["status"] == "ok"
    assert books["yes-1"]["summary"]["best_ask"] == 0.5
    skipped = paper_snapshot.orderbook_for_entry(
        books,
        "yes-1",
        label="20",
        outcome="yes",
        targets={("21", "no")},
    )
    assert skipped["status"] == "orderbook_scope_skipped"


def test_strategy_view_rejects_stale_canonical_batch(tmp_path):
    latest = tmp_path / "latest.json"
    latest.write_text(json.dumps({"available_at_utc": "2026-08-07T12:00:00Z", "records": []}))
    books, source = paper_snapshot.load_canonical_orderbook_latest(
        latest,
        now_utc=datetime(2026, 8, 7, 12, 8, tzinfo=timezone.utc),
        max_age_sec=420,
    )
    assert books == {}
    assert source["reason"] == "canonical_market_books_stale"
