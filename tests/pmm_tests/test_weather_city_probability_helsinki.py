import json
import math
from datetime import datetime, timezone

import pytest

from src.strategies.weather_city_probability_shadow.core import InputNotReady
from src.strategies.weather_city_probability_shadow.helsinki import HelsinkiRemainingHeatAdapter
from src.strategies.weather_city_probability_shadow.helsinki import _offset_probability
from src.strategies.weather_city_probability_shadow.helsinki import _quote
from src.strategies.weather_city_probability_shadow.helsinki import _yes_quote_from_no_quote


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


def test_yes_quote_is_the_executable_complement_of_no_book():
    quote = {
        "outcome": "no",
        "condition_id": "condition",
        "token_id": "no-token",
        "no_token_id": "no-token",
        "yes_token_id": "yes-token",
        "book_snapshot_id": "parent-book",
        "best_bid": 0.72,
        "best_ask": 0.76,
        "raw": {
            "bids": [{"price": 0.72, "size": 8.0}, {"price": 0.70, "size": 4.0}],
            "asks": [{"price": 0.76, "size": 6.0}],
        },
    }

    yes = _yes_quote_from_no_quote(quote)

    assert yes["outcome"] == "yes"
    assert yes["token_id"] == "yes-token"
    assert yes["complement_parent_book_snapshot_id"] == "parent-book"
    assert yes["book_snapshot_id"] != "parent-book"
    assert yes["best_ask"] == pytest.approx(0.28)
    assert yes["best_bid"] == pytest.approx(0.24)
    assert yes["raw"]["asks"][0] == {"price": pytest.approx(0.28), "size": 8.0}


def test_bounded_residual_cannot_move_market_by_more_than_cap_in_logit_space():
    artifact = {"kind": "bounded_weather_market_residual", "logit_cap": 0.25}
    probability = _offset_probability(
        artifact, {"weather_probability": 0.01}, market_p=0.8
    )

    market_logit = math.log(0.8 / 0.2)
    model_logit = math.log(probability / (1 - probability))
    assert 0 < market_logit - model_logit <= 0.25


def test_quote_uses_response_availability_and_exact_fmi_checkpoint(tmp_path):
    book_dir = tmp_path / "books"
    book_dir.mkdir()
    common = {
        "target_date": "2026-08-11",
        "bracket": "18",
        "outcome": "no",
        "book_status": "ok",
        "condition_id": "condition",
        "token_id": "no-token",
        "market_unit": "C",
        "summary": {"best_bid": 0.50, "best_ask": 0.55},
        "raw": {"bids": [{"price": 0.50, "size": 10}], "asks": [{"price": 0.55, "size": 10}]},
    }
    response_after_decision = {
        **common,
        "book_fetched_at_utc": "2026-08-11T08:01:59Z",
        "ts_utc": "2026-08-11T08:03:00Z",
        "source_obs_ts_utc": "2026-08-11T08:00:00Z",
        "capture_reasons": [{"anchor_kind": "official", "anchor_value": 18, "relative_offset": 0}],
    }
    response_before_decision = {
        **common,
        "book_fetched_at_utc": "2026-08-11T08:01:00Z",
        "ts_utc": "2026-08-11T08:02:00Z",
        "source_obs_ts_utc": "2026-08-11T08:00:00Z",
        "capture_reasons": [{"anchor_kind": "official", "anchor_value": 18, "relative_offset": 0}],
    }
    (book_dir / "2026-08-11.jsonl").write_text(
        json.dumps(response_before_decision) + "\n" + json.dumps(response_after_decision) + "\n"
    )

    quote = _quote(
        {"book_dir": str(book_dir)},
        "2026-08-11",
        18,
        as_of=datetime(2026, 8, 11, 8, 2, 30, tzinfo=timezone.utc),
        source_obs_ts_utc="2026-08-11T08:00:00Z",
    )

    assert quote["book_fetched_at_utc"] == "2026-08-11T08:01:00Z"
    assert quote["book_available_at_utc"] == "2026-08-11T08:02:00Z"


def test_insufficient_fmi_history_is_coverage_blocker(monkeypatch, tmp_path):
    decision = datetime(2026, 8, 2, 7, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "src.strategies.weather_city_probability_shadow.helsinki._load_artifact",
        lambda _spec: {},
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
