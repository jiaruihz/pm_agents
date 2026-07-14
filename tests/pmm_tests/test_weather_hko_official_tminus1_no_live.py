import json
from argparse import Namespace

import scripts.ops.weather_hko_official_tminus1_no_live as runner
from scripts.ops.weather_fast_source_stale_book_observer import MarketToken
from scripts.ops.weather_hko_official_tminus1_no_live import first_seen_hko_crosses


def test_hko_crosses_use_official_floor_and_first_detection(tmp_path):
    path = tmp_path / "high_frequency.jsonl"
    rows = [
        {
            "city": "HongKong",
            "source": "hko_obs",
            "station": "HK Observatory",
            "target_date": "2026-07-14",
            "temp_c": 31.9,
            "observation_time_utc": "2026-07-14T02:00:00Z",
            "local_detect_ts_utc": "2026-07-14T02:03:00Z",
        },
        {
            "city": "HongKong",
            "source": "hko_obs",
            "station": "HK Observatory",
            "target_date": "2026-07-14",
            "temp_c": 31.9,
            "observation_time_utc": "2026-07-14T02:00:00Z",
            "local_detect_ts_utc": "2026-07-14T02:02:00Z",
        },
        {
            "city": "HongKong",
            "source": "hko_obs",
            "station": "HK Observatory",
            "target_date": "2026-07-14",
            "temp_c": 32.0,
            "observation_time_utc": "2026-07-14T03:00:00Z",
            "local_detect_ts_utc": "2026-07-14T03:02:00Z",
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    crosses = first_seen_hko_crosses(path, "2026-07-14")

    assert [row["source_floor_bracket_c"] for row in crosses] == [31, 32]
    assert crosses[0]["source_detect_ts_utc"] == "2026-07-14T02:02:00Z"
    assert crosses[1]["t_minus_1_no_bracket_c"] == 31


def test_hko_hard_share_cap_uses_post_only_gtd(tmp_path, monkeypatch):
    source_path = tmp_path / "high_frequency.jsonl"
    source_path.write_text(
        json.dumps(
            {
                "city": "HongKong",
                "source": "hko_obs",
                "station": "HK Observatory",
                "target_date": "2026-07-14",
                "temp_c": 32.0,
                "observation_time_utc": "2026-07-14T03:00:00Z",
                "local_detect_ts_utc": "2026-07-14T03:02:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    token = MarketToken(
        city="HongKong",
        target_date="2026-07-14",
        bracket="31",
        question="Will the highest temperature in Hong Kong be 31C?",
        event_slug="event",
        market_id="market",
        condition_id="condition",
        yes_token_id="yes",
        no_token_id="no",
    )
    monkeypatch.setattr(runner, "latest_paper_snapshot", lambda: None)
    monkeypatch.setattr(runner, "build_market_index", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        runner,
        "augment_market_index_from_gamma",
        lambda *_args, **_kwargs: {("HongKong", "2026-07-14", "31"): token},
    )
    monkeypatch.setattr(
        runner,
        "fetch_fresh_book",
        lambda *_args, **_kwargs: {
            "status": "ok",
            "summary": {"best_ask": 0.80, "ask_size": 10.0, "best_bid": 0.78, "bid_size": 10.0, "tick_size": 0.01},
        },
    )
    args = Namespace(
        target_date="2026-07-14",
        output_dir=str(tmp_path / "out"),
        market_proxy="",
        high_frequency_jsonl=str(source_path),
        max_source_detect_age_min=10**9,
        max_source_observation_lag_min=30.0,
        book_timeout_sec=5.0,
        fok_immediate_retries=0,
        maker_effective_lifetime_sec=45.0,
        acknowledge_historical_share_cap_incidents=False,
        shares=5.0,
        max_shares_per_market=5.0,
        max_no_ask=0.93,
        live=True,
        confirm_live=True,
    )
    place = lambda _row: {
        "order_id": "maker-order",
        "order_type": "GTD",
        "order_mode": "post_only_gtd_buy_shares",
        "post_only": True,
        "place": {"success": True, "status": "live", "orderID": "maker-order"},
    }

    latest = runner.run_once(args, {"place": place})
    order = json.loads((tmp_path / "out/orders.jsonl").read_text(encoding="utf-8"))

    assert latest["live_orders_posted"] == 1
    assert order["order_type"] == "GTD"
    assert order["post_only"] is True
    assert order["limit_price"] == 0.79
    assert order["desired_shares"] == 5.0
    assert order["share_cap_enforcement"] == "resting_post_only_signed_size_v1"
