import json

from src.strategies.weather_city_probability_shadow.helsinki import _quote


def test_one_sided_near_binary_quote_is_valid_market_state(tmp_path):
    book_dir = tmp_path / "books"
    book_dir.mkdir()
    row = {
        "target_date": "2026-07-31", "bracket": "26", "outcome": "no",
        "book_status": "ok", "book_fetched_at_utc": "2026-07-31T13:10:00Z",
        "ts_utc": "2026-07-31T13:10:00Z",
        "source_obs_ts_utc": "2026-07-31T13:00:00Z",
        "condition_id": "condition", "token_id": "no-token",
        "summary": {"best_bid": None, "best_ask": .001},
        "raw": {"bids": [], "asks": [{"price": .001, "size": 700}]},
    }
    (book_dir / "2026-07-31.jsonl").write_text(json.dumps(row) + "\n")

    quote = _quote({"book_dir": str(book_dir)}, "2026-07-31", 26)

    assert quote["quote_state"] == "one_sided_near_binary_ask"
    assert quote["market_probability_status"] == "interval_censored"
    assert quote["market_probability_lower"] == 0.0
    assert quote["market_probability_upper"] == .001
    assert quote["best_bid"] is None
    assert quote["best_ask"] == .001
    assert len(quote["book_snapshot_id"]) == 64


def test_one_sided_near_binary_bid_is_valid_but_not_executable(tmp_path):
    book_dir = tmp_path / "books"
    book_dir.mkdir()
    row = {
        "target_date": "2026-08-01", "bracket": "18", "outcome": "no",
        "book_status": "ok", "book_fetched_at_utc": "2026-08-01T05:00:00Z",
        "ts_utc": "2026-08-01T05:00:00Z",
        "source_obs_ts_utc": "2026-08-01T04:50:00Z",
        "condition_id": "condition", "token_id": "no-token",
        "summary": {"best_bid": .999, "best_ask": None},
        "raw": {"bids": [{"price": .999, "size": 700}], "asks": []},
    }
    (book_dir / "2026-08-01.jsonl").write_text(json.dumps(row) + "\n")

    quote = _quote({"book_dir": str(book_dir)}, "2026-08-01", 18)

    assert quote["quote_state"] == "one_sided_near_binary_bid"
    assert quote["market_probability_lower"] == .999
    assert quote["market_probability_upper"] == 1.0
    assert quote["execution_status"] == "not_executable_no_ask"
    assert quote["best_ask"] is None
