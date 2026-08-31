from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.analysis.market_structure_edge import research_tmax_fixed_60m_exit_v1 as study


def _settlements() -> study.SettlementIndex:
    return study.SettlementIndex(
        by_condition={"condition-no": 0.0, "condition-yes": 1.0},
        by_city_date_bracket={},
        snapshot={},
    )


def _fast_quote(ts: datetime, *, ask=None, bid=None, size=10.0):
    return {
        "event_key": "Tokyo|2026-08-20|jma|30",
        "ts_utc": ts.isoformat(),
        "quotes": {
            "t_minus_1": {
                "no": {
                    "fresh_status": "ok",
                    "fresh_fetched_at_utc": (ts - timedelta(seconds=1)).isoformat(),
                    "fresh_best_ask": ask,
                    "fresh_ask_size": size if ask is not None else None,
                    "fresh_best_bid": bid,
                    "fresh_bid_size": size if bid is not None else None,
                    "token_id": "token-no",
                    "condition_id": "condition-no",
                    "bracket": "29",
                }
            }
        },
    }


def test_fast_source_uses_first_executable_and_fixed_exit_window():
    created = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
    events = [
        {
            "schema_version": "fast_source_stale_book_event_v3",
            "extreme_kind": "max",
            "event_key": "Tokyo|2026-08-20|jma|30",
            "created_at_utc": created.isoformat(),
            "city": "Tokyo",
            "target_date": "2026-08-20",
            "source": "jma",
            "previous_market_bracket": "29",
        }
    ]
    quotes = [
        _fast_quote(created, ask=0.40, size=4.0),
        _fast_quote(created + timedelta(minutes=1), ask=0.40, size=10.0),
        _fast_quote(created + timedelta(minutes=60), bid=0.50, size=4.0),
        _fast_quote(created + timedelta(minutes=61), bid=0.52, size=10.0),
    ]
    frame, funnel = study.evaluate_fast_source(events, quotes, _settlements())
    assert funnel["selected_city_dates"] == 1
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["entry_ts"] == created + timedelta(minutes=1)
    assert row["exit_ts"] == created + timedelta(minutes=61)
    assert row["exit_covered"]
    expected = 5 * (0.52 - study.weather_fee_per_share(0.52)) - 5 * (
        0.40 + study.weather_fee_per_share(0.40)
    )
    assert abs(row["round_trip_pnl_usd"] - expected) < 1e-12


def test_fast_source_excludes_v1_mapping_rows():
    created = datetime(2026, 7, 13, 1, 0, tzinfo=timezone.utc)
    events = [
        {
            "schema_version": "fast_source_stale_book_event_v1",
            "extreme_kind": "max",
            "event_key": "Tokyo|2026-08-20|jma|30",
            "created_at_utc": created.isoformat(),
            "city": "Tokyo",
            "target_date": "2026-08-20",
            "previous_no_bracket_c": "29",
        }
    ]
    frame, funnel = study.evaluate_fast_source(
        events, [_fast_quote(created, ask=0.4)], _settlements()
    )
    assert frame.empty
    assert funnel["valid_schema_events"] == 0


def test_settlement_payoff_rejects_nonbinary_and_preserves_side():
    settlements = study.SettlementIndex(
        by_condition={"winner": 1.0, "loser": 0.0, "unresolved": 0.5},
        by_city_date_bracket={},
        snapshot={},
    )
    common = {"city": "Tokyo", "target_date": "2026-08-20", "bracket": "30"}
    assert settlements.payoff(condition_id="winner", side="yes", **common) == 1.0
    assert settlements.payoff(condition_id="winner", side="no", **common) == 0.0
    assert settlements.payoff(condition_id="loser", side="no", **common) == 1.0
    assert settlements.payoff(condition_id="unresolved", side="yes", **common) is None


def test_quote_age_boundary_is_fail_closed():
    ts = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
    quote = _fast_quote(ts, ask=0.4)
    no = quote["quotes"]["t_minus_1"]["no"]
    no["fresh_fetched_at_utc"] = (ts - timedelta(seconds=300)).isoformat()
    assert study.fresh_top(
        quote, expression="t_minus_1", side="no", action="buy", min_shares=5
    ) is not None
    no["fresh_fetched_at_utc"] = (ts - timedelta(seconds=301)).isoformat()
    assert study.fresh_top(
        quote, expression="t_minus_1", side="no", action="buy", min_shares=5
    ) is None


def test_core_exit_requires_full_original_quantity_and_uses_first_executable_book():
    entry_ts = datetime(2026, 8, 20, 2, 0, tzinfo=timezone.utc)
    entry = {
        "would_submit_after_family_dedupe": True,
        "city": "Tokyo",
        "target_date": "2026-08-20",
        "current_bracket": "30",
        "current_condition_id": "condition-yes",
        "current_yes_token_id": "token-yes",
        "current_yes_book_fetched_at_utc": entry_ts.isoformat(),
        "decision_snapshot_ts_utc": (entry_ts - timedelta(seconds=2)).isoformat(),
        "taker_ladder": {
            "executable": True,
            "quantity": 10.0,
            "principal": 7.0,
            "fee": 0.105,
            "principal_vwap": 0.70,
            "effective_cost_per_share": 0.7105,
            "available_shares": 20.0,
        },
    }
    def book(minutes: int, size: float, bid: float):
        ts = entry_ts + timedelta(minutes=minutes)
        return {
            "available_at_utc": ts.isoformat(),
            "snapshot_ts_utc": (ts - timedelta(seconds=1)).isoformat(),
            "bids": [{"price": bid, "size": size}],
            "asks": [],
            "source_path": "/tmp/book.jsonl",
        }
    books = {"token-yes": [book(60, 5.0, 0.80), book(61, 10.0, 0.79)]}
    frame = study.evaluate_core_entries([entry], books, _settlements())
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["exit_ts"] == entry_ts + timedelta(minutes=61)
    assert row["exit_covered"]
    expected_net = 10 * 0.79 - 10 * study.weather_fee_per_share(0.79)
    assert abs(row["round_trip_pnl_usd"] - (expected_net - 7.105)) < 1e-12


def test_exit_after_minute_72_is_not_used():
    entry_ts = datetime(2026, 8, 20, 2, 0, tzinfo=timezone.utc)
    entry = {
        "would_submit_after_family_dedupe": True,
        "city": "Tokyo",
        "target_date": "2026-08-20",
        "current_bracket": "30",
        "current_condition_id": "condition-yes",
        "current_yes_token_id": "token-yes",
        "current_yes_book_fetched_at_utc": entry_ts.isoformat(),
        "taker_ladder": {
            "executable": True,
            "quantity": 5.0,
            "principal": 3.5,
            "fee": 0.0525,
            "principal_vwap": 0.70,
            "effective_cost_per_share": 0.7105,
        },
    }
    late = entry_ts + timedelta(minutes=73)
    books = {
        "token-yes": [
            {
                "available_at_utc": late.isoformat(),
                "snapshot_ts_utc": (late - timedelta(seconds=1)).isoformat(),
                "bids": [{"price": 0.9, "size": 20}],
                "asks": [],
            }
        ]
    }
    frame = study.evaluate_core_entries([entry], books, _settlements())
    assert not bool(frame.iloc[0]["exit_covered"])


def test_core_rejects_quantity_outside_frozen_contract():
    entry_ts = datetime(2026, 8, 20, 2, 0, tzinfo=timezone.utc)
    entry = {
        "would_submit_after_family_dedupe": True,
        "city": "Tokyo",
        "target_date": "2026-08-20",
        "current_bracket": "30",
        "current_condition_id": "condition-yes",
        "current_yes_token_id": "token-yes",
        "current_yes_book_fetched_at_utc": entry_ts.isoformat(),
        "taker_ladder": {
            "executable": True,
            "quantity": 7.0,
            "principal": 4.9,
            "fee": 0.0735,
            "principal_vwap": 0.70,
            "effective_cost_per_share": 0.7105,
        },
    }
    assert study.evaluate_core_entries([entry], {}, _settlements()).empty


def test_report_keeps_all_exit_and_same_settled_denominators_explicit():
    summary = {
        "signals": 4,
        "exit_covered": 3,
        "settled_exit_rows": 2,
        "entry_cash_cost_usd": 10.0,
        "round_trip_pnl_usd": 3.0,
        "round_trip_pnl_usd_same_settled_rows": 2.0,
        "round_trip_roi_same_settled_rows": {
            "ratio": 0.2,
            "ci_low": 0.1,
            "ci_high": 0.3,
        },
        "hold_pnl_usd_same_rows": 1.0,
        "fixed_exit_minus_hold_pnl_usd": 1.0,
    }
    payload = {
        "research_status": "inconclusive",
        "results": {
            "fast_source_previous_no": {"windows": {"all": summary}},
            "normal_core_carry_current_yes": {"windows": {"all": summary}},
        },
        "inputs": {
            "fast_events": {"rows": 1},
            "fast_quotes": {"rows": 1},
            "core_entries": {"rows": 1},
        },
        "gates": {
            "significance": "FAIL",
            "baseline": "FAIL",
            "forward": "FAIL_not_pristine_forward",
            "conclusion": "inconclusive",
        },
    }
    report = study.markdown(payload)
    assert "exits / settled" in report
    assert "3 / 2" in report
    assert "$+3.00 | $+2.00" in report
    assert "$+1.00 | $+1.00" in report
