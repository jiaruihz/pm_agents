import argparse
import json
import threading
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


def _events() -> list[dict]:
    return [
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


def test_market_discovery_is_bounded_parallel_and_retries_transport_failure(
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(
        market_books.legacy,
        "CITIES",
        {
            "CityA": {"slug": "city-a", "unit": "C"},
            "CityB": {"slug": "city-b", "unit": "C"},
        },
    )
    monkeypatch.setattr(
        market_books,
        "city_scan_dates",
        lambda city, now_utc, explicit_target_date=None: ["2026-08-10"],
    )
    monkeypatch.setattr(market_books, "local_settle_utc", lambda *_args: now)
    monkeypatch.setattr(market_books, "city_local_datetime", lambda *_args: now)
    monkeypatch.setattr(market_books, "DISCOVERY_RETRY_BACKOFF_SEC", 0)
    barrier = threading.Barrier(2)
    attempts: dict[str, int] = {}

    def fake_curl(_url, *, params, **_kwargs):
        slug = params["slug"]
        attempts[slug] = attempts.get(slug, 0) + 1
        if attempts[slug] == 1:
            barrier.wait(timeout=1)
            return 0, None, "proxy handshake timeout"
        return 200, {"id": slug, "markets": [{"slug": slug}]}, ""

    monkeypatch.setattr(market_books.legacy, "curl_json_get", fake_curl)
    monkeypatch.setattr(
        market_books.legacy,
        "gamma_market_ladder",
        lambda markets: (
            None,
            [
                {
                    "label": "20",
                    "market_id": markets[0]["slug"],
                    "condition_id": markets[0]["slug"],
                    "yes_token_id": f'{markets[0]["slug"]}-yes',
                    "no_token_id": f'{markets[0]["slug"]}-no',
                }
            ],
        ),
    )
    monkeypatch.setattr(
        market_books.legacy,
        "orderbook_targets_for_strategy_live",
        lambda *_args: set(),
    )

    events, failures = market_books.discover_market_ladders(
        now_utc=now,
        observation_index={},
        max_workers=2,
        retries=1,
    )

    assert failures == []
    assert [row["city"] for row in events] == ["CityA", "CityB"]
    assert [row["discovery_attempt_count"] for row in events] == [2, 2]


def test_market_books_collects_raw_before_weather_views(monkeypatch, tmp_path):
    events = _events()
    unavailable = {
        "city": "Boston",
        "status_code": 200,
        "error": "event_unavailable",
        "discovery_failure_class": "expected_unavailable",
    }
    monkeypatch.setattr(
        market_books,
        "discover_market_ladders",
        lambda **_kwargs: (events, [unavailable]),
    )

    def fake_fetch(_client, request_rows, token_ids, **_kwargs):
        return {token_id: (request_rows[token_id], _book(token_id)) for token_id in token_ids}

    monkeypatch.setattr(market_books, "_fetch_priority_group", fake_fetch)
    monkeypatch.setattr(market_books.httpx, "Client", lambda **_kwargs: type("C", (), {"close": lambda self: None})())

    books = tmp_path / "market_books"
    ladders = tmp_path / "market_ladder_snapshots"
    result = market_books.collect(
        argparse.Namespace(
            output_root=str(books),
            market_ladder_root=str(ladders),
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
    assert latest["summary"]["hot_ok_books"] == 1
    assert latest["summary"]["hot_failed_books"] == 0
    assert latest["summary"]["cold_ok_books"] == 1
    assert latest["summary"]["cold_failed_books"] == 0
    assert latest["summary"]["discovery_operational_failures"] == 0
    assert latest["summary"]["discovery_expected_unavailable"] == 1
    assert latest["summary"]["forecast_dependency"] is False
    assert ladder["records"][0]["market_distribution_complete"] is True
    assert "legacy_orderbook_path" not in result


def test_market_books_degrades_when_discovery_has_operational_failure(
    monkeypatch, tmp_path
) -> None:
    failure = {
        "city": "Boston",
        "status_code": 0,
        "error": "SSL connection timeout",
        "discovery_failure_class": "operational_failure",
    }
    monkeypatch.setattr(
        market_books,
        "discover_market_ladders",
        lambda **_kwargs: (_events(), [failure]),
    )

    def fake_fetch(_client, request_rows, token_ids, **_kwargs):
        return {
            token_id: (request_rows[token_id], _book(token_id))
            for token_id in token_ids
        }

    monkeypatch.setattr(market_books, "_fetch_priority_group", fake_fetch)
    monkeypatch.setattr(
        market_books.httpx,
        "Client",
        lambda **_kwargs: type("C", (), {"close": lambda self: None})(),
    )
    result = market_books.collect(
        argparse.Namespace(
            output_root=str(tmp_path / "market_books"),
            market_ladder_root=str(tmp_path / "market_ladders"),
            observation_cache="",
            target_date=None,
            now_utc="2026-08-07T12:00:00Z",
            orderbook_top_n=20,
            orderbook_budget_sec=240.0,
        )
    )

    assert result["status"] == "degraded"
    assert result["discovery_operational_failures"] == 1


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


def test_strategy_view_builds_market_ladder_from_canonical_book_identity(tmp_path):
    latest = tmp_path / "latest.json"
    records = []
    for outcome, token_id, best_bid, best_ask in (
        ("yes", "yes-1", 0.4, 0.5),
        ("no", "no-1", 0.5, 0.6),
    ):
        records.append(
            {
                "city": "Tokyo",
                "event_date": "2026-08-07",
                "event_id": "event-1",
                "event_slug": "tokyo-event",
                "market_id": "market-1",
                "condition_id": "condition-1",
                "bracket": "31",
                "outcome": outcome,
                "token_id": token_id,
                "status": "ok",
                "summary": {"best_bid": best_bid, "best_ask": best_ask},
            }
        )
    latest.write_text(
        json.dumps({"available_at_utc": "2026-08-07T12:00:00Z", "records": records})
    )

    books, source = paper_snapshot.load_canonical_orderbook_latest(
        latest,
        now_utc=datetime(2026, 8, 7, 12, 1, tzinfo=timezone.utc),
        max_age_sec=420,
    )
    ladders = paper_snapshot.canonical_market_ladders_from_books(books)

    assert source["status"] == "ok"
    ladder = ladders[("Tokyo", "2026-08-07")]
    assert ladder["event_id"] == "event-1"
    assert ladder["bracket_list"] == [("31", 0.45)]
    assert ladder["market_entries"][0]["yes_token_id"] == "yes-1"
    assert ladder["market_entries"][0]["no_token_id"] == "no-1"
    assert paper_snapshot._extract_bracket_label(
        ladder["market_entries"][0]["question"]
    ) == "31"


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
