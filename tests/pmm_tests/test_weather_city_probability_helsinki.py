import json
from datetime import datetime, timezone

import pytest

from src.strategies.weather_city_probability_shadow.core import InputNotReady
from src.strategies.weather_city_probability_shadow.helsinki import HelsinkiRemainingHeatAdapter
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


def test_insufficient_fmi_history_is_coverage_blocker(monkeypatch, tmp_path):
    decision = datetime(2026, 8, 2, 7, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "src.strategies.weather_city_probability_shadow.helsinki._verify_artifact",
        lambda _spec: tmp_path / "artifact.joblib",
    )
    monkeypatch.setattr(
        "src.strategies.weather_city_probability_shadow.helsinki.joblib.load",
        lambda _path: {},
    )
    monkeypatch.setattr(
        "src.strategies.weather_city_probability_shadow.helsinki._official_helsinki_as_of",
        lambda *_args, **_kwargs: {
            "running_max_c": 20.0,
            "current_temp_c": 19.5,
            "_input_ref": {"physical_path": "official.jsonl", "physical_line": 1},
        },
    )
    monkeypatch.setattr(
        "src.strategies.weather_city_probability_shadow.helsinki._quote",
        lambda *_args, **_kwargs: {
            "book_fetched_at_utc": decision.isoformat(),
            "source_obs_ts_utc": decision.isoformat(),
            "book_snapshot_id": "book",
            "_input_ref": {"physical_path": "book.jsonl", "physical_line": 1},
        },
    )
    monkeypatch.setattr(
        "src.strategies.weather_city_probability_shadow.helsinki._fmi_history",
        lambda *_args, **_kwargs: [
            {"observation_time_utc": decision.isoformat(), "temp_c": 19.5}
        ],
    )
    profile = {
        "forward_start_utc": "2026-08-01T00:00:00Z",
        "artifacts": {"fixture": {"path": "unused", "sha256": "unused"}},
        "observation_journal_dir": str(tmp_path / "official"),
        "source_journal": str(tmp_path / "fmi.jsonl"),
        "max_book_age_seconds": 300,
    }

    with pytest.raises(InputNotReady) as caught:
        HelsinkiRemainingHeatAdapter().score(profile, decision)

    assert caught.value.reason == "insufficient_pit_source_history"
    assert caught.value.details["available_unique_observations"] == 1
    assert caught.value.details["required_unique_observations"] == 4
