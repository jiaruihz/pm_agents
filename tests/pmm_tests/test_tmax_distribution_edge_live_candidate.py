from __future__ import annotations

import math
from types import SimpleNamespace

import pandas as pd

from scripts.ops import tmax_distribution_edge_live_candidate_v1 as runner
from weather_data_feed.observation_sources.fetchers import aviationweather_sky_code, relative_humidity_pct


def test_tail_aware_running_value_mapping() -> None:
    bottom = {
        "bracket": "89",
        "question": "Will the highest temperature in Austin be 89°F or below?",
    }
    top = {
        "bracket": "94+",
        "question": "Will the highest temperature in Denver be 94°F or higher?",
    }

    assert runner.record_contains_running_value(bottom, 78.8)
    assert not runner.record_contains_running_value(bottom, 89.5)
    assert runner.record_contains_running_value(top, 94.0)


def test_effective_yes_ask_uses_cheapest_snapshot_estimate() -> None:
    ask, size, source, synthetic = runner.effective_yes_ask(0.48, 7.0, 0.60, 12.0)

    assert math.isclose(ask, 0.40)
    assert size == 12.0
    assert source == "complement_no_bid"
    assert math.isclose(synthetic, 0.40)


def test_nan_yes_ask_does_not_mask_later_valid_expression() -> None:
    live = pd.DataFrame(
        [
            {
                "city": "TestCity",
                "target_date": "2026-07-10",
                "decision_hour_local": 12.0,
                "actual_bucket": "current",
                "temp_trend_3h_f": 1.0,
                "obs_age_min": 10.0,
                "d1_yes_ask": math.nan,
                "d1_yes_ask_size": 0.0,
                "d1_yes_token_id": "yes-d1",
                "d1_no_token_id": "no-d1",
                "d1_market_id": "market-d1",
                "d1_no_bracket": "31",
                "d2_no_ask": 0.50,
                "d2_no_ask_size": 10.0,
                "d2_no_token_id": "no-d2",
                "d2_market_id": "market-d2",
                "d2_no_bracket": "32",
            }
        ]
    )
    pred = pd.DataFrame(
        [
            {
                "city": "TestCity",
                "target_date": "2026-07-10",
                "decision_hour_local": 12.0,
                "actual_bucket": "current",
                f"{runner.MODEL_METHOD}_p_current": 0.20,
                f"{runner.MODEL_METHOD}_p_d1": 0.20,
                f"{runner.MODEL_METHOD}_p_d2": 0.20,
            }
        ]
    )
    args = SimpleNamespace(
        active_expressions=["d1_yes", "d2_no"],
        exclude_trend3h_flat=True,
        trend3h_flat_low=-0.5,
        trend3h_flat_high=0.5,
        fee_rate=0.05,
        ask_floor=0.40,
        ask_ceiling=0.99,
        edge_threshold=0.02,
        policy_id="test",
        max_obs_age_min=90.0,
    )

    selected, blocked = runner.build_candidates(live, pred, args)

    assert [row["chosen_expression"] for row in selected] == ["d2_no"]
    assert any(row.get("block_reason") == "missing_expression_ask" for row in blocked)


def test_missing_observation_age_blocks_candidate() -> None:
    live = pd.DataFrame(
        [
            {
                "city": "TestCity",
                "target_date": "2026-07-10",
                "decision_hour_local": 12.0,
                "actual_bucket": "current",
                "temp_trend_3h_f": 1.0,
                "obs_age_min": math.nan,
            }
        ]
    )
    pred = pd.DataFrame(
        [
            {
                "city": "TestCity",
                "target_date": "2026-07-10",
                "decision_hour_local": 12.0,
                "actual_bucket": "current",
            }
        ]
    )
    args = SimpleNamespace(
        active_expressions=["current_no"],
        exclude_trend3h_flat=True,
        trend3h_flat_low=-0.5,
        trend3h_flat_high=0.5,
        fee_rate=0.05,
        ask_floor=0.40,
        ask_ceiling=0.99,
        edge_threshold=0.02,
        policy_id="test",
        max_obs_age_min=90.0,
    )

    selected, blocked = runner.build_candidates(live, pred, args)

    assert selected == []
    assert [row["block_reason"] for row in blocked] == ["observation_age_missing"]


def test_fresh_yes_quote_requires_direct_yes_ask(monkeypatch) -> None:
    books = {
        "yes-token": {
            "asks": [{"price": "0.32", "size": "8"}],
            "bids": [{"price": "0.20", "size": "8"}],
        },
        "no-token": {
            "asks": [{"price": "0.75", "size": "10"}],
            "bids": [{"price": "0.70", "size": "12"}],
        },
    }
    monkeypatch.setattr(runner, "fetch_book_resilient", lambda token_id, _args: books[token_id])
    args = SimpleNamespace(
        clob_timeout_sec=1.0,
        clob_retries=0,
        fixed_shares=5.0,
        fee_rate=0.05,
        ask_floor=0.20,
        ask_ceiling=0.99,
        max_fresh_ask_drift=0.02,
        edge_threshold=0.02,
    )
    candidate = {
        "token_id": "yes-token",
        "sibling_no_token_id": "no-token",
        "signal_side": "BUY_YES",
        "p_win": 0.40,
        "ask": 0.30,
    }

    quote = runner.fresh_quote(candidate, args)

    assert quote["status"] == "accepted"
    assert math.isclose(quote["fresh_ask"], 0.32)
    assert quote["fresh_quote_source"] == "direct_token_ask"
    assert math.isclose(quote["fresh_synthetic_ask"], 0.30)


def test_fresh_yes_quote_rejects_synthetic_only_book(monkeypatch) -> None:
    books = {
        "yes-token": {"asks": [], "bids": [{"price": "0.20", "size": "8"}]},
        "no-token": {"asks": [], "bids": [{"price": "0.70", "size": "12"}]},
    }
    monkeypatch.setattr(runner, "fetch_book_resilient", lambda token_id, _args: books[token_id])
    args = SimpleNamespace(clob_timeout_sec=1.0, clob_retries=0)
    candidate = {
        "token_id": "yes-token",
        "sibling_no_token_id": "no-token",
        "signal_side": "BUY_YES",
        "p_win": 0.40,
        "ask": 0.30,
    }

    quote = runner.fresh_quote(candidate, args)

    assert quote["status"] == "rejected"
    assert quote["reason"] == "fresh_book_no_effective_ask"
    assert math.isclose(quote["fresh_synthetic_ask"], 0.30)


def test_observation_helpers_derive_rh_and_sky() -> None:
    rh = relative_humidity_pct(30.0, 20.0)
    sky = aviationweather_sky_code({"clouds": [{"cover": "SCT"}, {"cover": "BKN"}]})

    assert rh is not None and 50.0 < rh < 60.0
    assert sky == "BKN"
