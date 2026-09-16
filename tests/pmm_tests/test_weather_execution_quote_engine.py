import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.strategies.weather_edge_v1.execution.contracts import BookLevel, FeeSchedule, MarketBook, VenueCapabilities
from src.strategies.weather_edge_v1.execution.quote_engine import (
    QuoteInputError,
    compose_price_cap,
    quote_maker,
    quote_marketable_limit_exact_shares,
    quote_taker_top_of_book,
    round_marketable_price_to_tick,
    round_price_to_tick,
)
from src.strategies.weather_edge_v1.tools.execution_policy import price_to_tick


FIXTURES = Path(__file__).parents[1] / "fixtures" / "weather_execution"


def _book(*, bid="0.40", ask="0.42", tick="0.01", bids=None, asks=None, status="ok"):
    return MarketBook(
        token_id="fixture-token",
        status=status,
        fetched_at_utc="2026-07-26T08:00:00Z",
        venue_timestamp_utc="2026-07-26T07:59:59Z",
        book_epoch_ref="book-epoch-fixture-1",
        tick_size=tick,
        tick_size_source="fixture_tick",
        minimum_order_shares="1",
        bids=tuple(BookLevel(price=price, size=size) for price, size in ([(bid, "10")] if bids is None else bids)),
        asks=tuple(BookLevel(price=price, size=size) for price, size in ([(ask, "10")] if asks is None else asks)),
    )


def _fee_schedule():
    return FeeSchedule(
        venue="fixture-clob",
        fee_schedule_ref="fee-fixture-v1",
        fee_schedule_fetched_at_utc="2026-07-26T08:00:00Z",
        fee_formula_id="official-fixture-formula-v1",
        taker_fee_parameters={"basis_points": "12"},
        maker_fee_parameters={"basis_points": "0"},
        maker_rebate_program="fixture-maker-rebate",
    )


def _capabilities(*, fee_schedule_ref="fee-fixture-v1"):
    return VenueCapabilities(
        venue="fixture-clob",
        protocol_version="protocol-fixture-v1",
        client_version="client-fixture-v1",
        collateral_asset="USDC",
        supported_order_types=("GTC",),
        post_only_order_types=("GTC",),
        price_precision=3,
        size_precision=3,
        amount_precision_by_order_type={"GTC": 3},
        gtd_security_threshold_sec=1,
        capabilities_fetched_at_utc="2026-07-26T08:00:00Z",
        fee_schedule_ref=fee_schedule_ref,
    )


def _quote_inputs(**overrides):
    values = {
        "book_age_sec": "1",
        "max_book_age_sec": "5",
        "fee_schedule": _fee_schedule(),
        "venue_capabilities": _capabilities(),
    }
    values.update(overrides)
    return values


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


def test_taker_top_of_book_and_full_depth_exact_share_vwap_and_remainder():
    book = _book(bids=[("0.39", "4")], asks=[("0.41", "2"), ("0.42", "3"), ("0.43", "5")])
    top = quote_taker_top_of_book(market_book=book, venue_side="BUY", requested_shares="2", **_quote_inputs())
    assert top.status == "quoted"
    assert top.normalized_price_exact == Decimal("0.41")
    assert top.expected_vwap == Decimal("0.41")

    exact = quote_marketable_limit_exact_shares(market_book=book, venue_side="BUY", requested_shares="4", **_quote_inputs())
    assert exact.status == "quoted"
    assert exact.expected_vwap == Decimal("0.415")
    assert exact.worst_price == Decimal("0.42")
    assert exact.normalized_price_exact == Decimal("0.42")
    assert exact.blocked_remainder == Decimal("0")

    short = quote_marketable_limit_exact_shares(market_book=book, venue_side="BUY", requested_shares="11", **_quote_inputs())
    assert short.status == "blocked"
    assert short.reason == "insufficient_full_depth"
    assert short.fillable_shares == Decimal("10")
    assert short.blocked_remainder == Decimal("1")


def test_maker_rules_compose_caps_and_never_cross_for_buy_and_sell():
    book = _book(bid="0.40", ask="0.42", tick="0.01")
    common = _quote_inputs()
    assert quote_maker(market_book=book, venue_side="BUY", requested_shares="1", price_rule="join_bid", **common).normalized_price_exact == Decimal("0.40")
    assert quote_maker(market_book=book, venue_side="BUY", requested_shares="1", price_rule="improve_bid", **common).normalized_price_exact == Decimal("0.41")
    assert quote_maker(market_book=book, venue_side="BUY", requested_shares="1", price_rule="one_tick_below_ask", **common).normalized_price_exact == Decimal("0.41")
    assert quote_maker(market_book=book, venue_side="BUY", requested_shares="1", price_rule="improve_bid", price_cap="0.405", **common).normalized_price_exact == Decimal("0.40")
    assert quote_maker(market_book=book, venue_side="SELL", requested_shares="1", price_rule="join_ask", **common).normalized_price_exact == Decimal("0.42")
    assert quote_maker(market_book=book, venue_side="SELL", requested_shares="1", price_rule="improve_ask", **common).normalized_price_exact == Decimal("0.41")
    assert quote_maker(market_book=book, venue_side="SELL", requested_shares="1", price_rule="one_tick_above_bid", **common).normalized_price_exact == Decimal("0.41")

    crossed = quote_maker(market_book=_book(bid="0.40", ask="0.41", tick="0.01"), venue_side="BUY", requested_shares="1", price_rule="improve_bid", **common)
    assert crossed.status == "blocked"
    assert crossed.reason == "maker_would_cross"


def test_book_states_tick_rounding_price_boundaries_and_snapshot_provenance():
    common = _quote_inputs()
    stale = quote_taker_top_of_book(market_book=_book(), venue_side="BUY", requested_shares="1", **_quote_inputs(book_age_sec="6"))
    assert stale.status == "blocked" and stale.reason == "book_stale"
    missing = quote_maker(market_book=_book(bids=[]), venue_side="BUY", requested_shares="1", price_rule="join_bid", **common)
    assert missing.status == "blocked" and missing.reason == "missing_two_sided_book"
    locked = quote_maker(market_book=_book(bid="0.42", ask="0.42"), venue_side="BUY", requested_shares="1", price_rule="join_bid", **common)
    assert locked.status == "blocked" and locked.reason == "book_locked_or_crossed"
    assert locked.rounding_mode == "floor_buy"

    assert round_price_to_tick("0.1239", "0.001", venue_side="BUY") == Decimal("0.123")
    assert round_price_to_tick("0.1231", "0.001", venue_side="SELL") == Decimal("0.124")
    assert round_price_to_tick("0.124", "0.01", venue_side="BUY") == Decimal("0.12")
    assert round_price_to_tick("0.121", "0.01", venue_side="SELL") == Decimal("0.13")
    boundary = quote_maker(market_book=_book(bid="0.001", ask="0.002", tick="0.001"), venue_side="BUY", requested_shares="1", price_rule="join_bid", venue_min_price="0.001", **common)
    assert boundary.status == "blocked" and boundary.reason == "maker_price_outside_bounds"

    provenance = quote_maker(market_book=_book(), venue_side="BUY", requested_shares="1", price_rule="join_bid", **common)
    assert provenance.fee_schedule_ref == "fee-fixture-v1"
    assert provenance.fee_model_version == "official-fixture-formula-v1"
    assert provenance.fee_estimate_input["fee_parameters"] == {"basis_points": "0"}
    mismatch = quote_maker(market_book=_book(), venue_side="BUY", requested_shares="1", price_rule="join_bid", **_quote_inputs(venue_capabilities=_capabilities(fee_schedule_ref="other-fee")))
    assert mismatch.status == "blocked" and mismatch.reason == "fee_capability_provenance_mismatch"


def test_marketable_rounding_preserves_executability_without_widening_caps_or_floors():
    book = _book(bid="0.409", ask="0.411", tick="0.01")
    common = _quote_inputs()
    buy = quote_taker_top_of_book(market_book=book, venue_side="BUY", requested_shares="1", **common)
    sell = quote_taker_top_of_book(market_book=book, venue_side="SELL", requested_shares="1", **common)
    assert buy.status == "quoted" and buy.normalized_price_exact == Decimal("0.42")
    assert sell.status == "quoted" and sell.normalized_price_exact == Decimal("0.40")
    assert buy.rounding_mode == "ceiling_buy"
    assert sell.rounding_mode == "floor_sell"
    exact_buy = quote_marketable_limit_exact_shares(market_book=book, venue_side="BUY", requested_shares="1", **common)
    exact_sell = quote_marketable_limit_exact_shares(market_book=book, venue_side="SELL", requested_shares="1", **common)
    assert exact_buy.normalized_price_exact == Decimal("0.42")
    assert exact_sell.normalized_price_exact == Decimal("0.40")
    assert exact_buy.rounding_mode == "ceiling_buy"
    assert exact_sell.rounding_mode == "floor_sell"

    buy_cap = quote_taker_top_of_book(market_book=book, venue_side="BUY", requested_shares="1", price_cap="0.415", **common)
    sell_floor = quote_marketable_limit_exact_shares(market_book=book, venue_side="SELL", requested_shares="1", price_floor="0.405", **common)
    assert buy_cap.status == "blocked" and buy_cap.reason == "marketable_normalization_outside_price_bound"
    assert sell_floor.status == "blocked" and sell_floor.reason == "marketable_normalization_outside_price_bound"
    assert buy_cap.rounding_mode == "ceiling_buy"
    assert sell_floor.rounding_mode == "floor_sell"

    assert round_price_to_tick("0.411", "0.01", venue_side="BUY") == Decimal("0.41")
    assert round_price_to_tick("0.419", "0.01", venue_side="SELL") == Decimal("0.42")
    assert round_marketable_price_to_tick("0.411", "0.01", venue_side="BUY") == Decimal("0.42")
    assert round_marketable_price_to_tick("0.419", "0.01", venue_side="SELL") == Decimal("0.41")
    assert price_to_tick(0.411, 0.01, side="BUY") == 0.41
    assert price_to_tick(0.419, 0.01, side="SELL") == 0.42
    assert quote_maker(market_book=book, venue_side="BUY", requested_shares="1", price_rule="join_bid", **common).rounding_mode == "floor_buy"
    assert quote_maker(market_book=book, venue_side="SELL", requested_shares="1", price_rule="join_ask", **common).rounding_mode == "ceiling_sell"


def test_quote_engine_uses_phase0_fixture_caps_without_replacing_strategy_rules():
    d1 = _fixture("d1_static_and_capped_chase.json")
    d1_quote = quote_maker(
        market_book=_book(bid=str(d1["input"]["fresh_bid"]), ask=str(d1["input"]["fresh_ask"]), tick="0.001"),
        venue_side="BUY",
        requested_shares=str(d1["input"]["maker_size"]),
        price_rule="improve_bid",
        price_cap=str(d1["input"]["initial_mid_cap"]),
        **_quote_inputs(),
    )
    assert d1_quote.normalized_price_exact == Decimal(str(d1["expected"]["chase"]["next_price"]))

    core = _fixture("core_carry_capped_chase.json")
    core_cap = min(
        (Decimal(str(core["input"]["best_bid"])) + Decimal(str(core["input"]["best_ask"]))) / 2,
        Decimal(str(core["input"]["model_probability_hold"])),
    )
    core_quote = quote_maker(market_book=_book(bid=str(core["input"]["best_bid"]), ask=str(core["input"]["best_ask"]), tick=str(core["input"]["tick_size"])), venue_side="BUY", requested_shares=str(core["input"]["maker_shares"]), price_rule="improve_bid", price_cap=core_cap, **_quote_inputs())
    assert core_quote.normalized_price_exact == Decimal(str(core["expected_children"][1]["limit_price"]))
    assert core_cap == Decimal(str(core["expected_children"][1]["maker_price_cap"]))

    heat = _fixture("heat_death_chase_and_fallback.json")
    heat_quote = quote_maker(market_book=_book(bid=str(heat["input"]["fresh_bid"]), ask=str(heat["input"]["fresh_ask"]), tick="0.001"), venue_side="BUY", requested_shares=str(heat["input"]["maker_shares"]), price_rule="improve_bid", price_cap=str(heat["input"]["initial_ask_cap"]), **_quote_inputs())
    assert heat_quote.normalized_price_exact == Decimal(str(heat["input"]["maker_quote"]))

    low = _fixture("low_price_repost_and_fallback.json")
    low_quote = quote_maker(market_book=_book(bid=str(low["input"]["fresh_best_bid"]), ask=str(low["input"]["fresh_best_ask"]), tick=str(low["input"]["tick_size"])), venue_side="BUY", requested_shares=str(low["input"]["remaining_shares"]), price_rule="improve_bid", price_cap=str(low["input"]["posted_price"]), **_quote_inputs())
    assert low_quote.normalized_price_exact == Decimal(str(low["expected"]["limit_price"]))

    fast = _fixture("fast_source_gtd_and_retry.json")
    fast_quote = quote_maker(market_book=_book(bid="0.62", ask=str(fast["input"]["fresh_retry_best_ask"]), tick=str(fast["input"]["tick_size"])), venue_side="BUY", requested_shares=str(fast["input"]["requested_shares"]), price_rule="one_tick_below_ask", price_cap=str(fast["input"]["max_no_ask"]), **_quote_inputs())
    assert fast_quote.normalized_price_exact == Decimal(str(fast["expected"]["attempts"][1]["limit_price"]))


def test_engine_rejects_float_inputs_and_empty_cap_composition():
    assert compose_price_cap("0.5", price_floor="0.2", price_cap="0.4") == Decimal("0.4")
    assert price_to_tick(0.124, 0.01, side="BUY") == float(round_price_to_tick("0.124", "0.01", venue_side="BUY"))
    assert price_to_tick(0.121, 0.01, side="SELL") == float(round_price_to_tick("0.121", "0.01", venue_side="SELL"))
    with pytest.raises(QuoteInputError, match="must be a Decimal"):
        round_price_to_tick(0.12, "0.01", venue_side="BUY")
    with pytest.raises(QuoteInputError, match="bounds are empty"):
        compose_price_cap("0.5", price_floor="0.6", price_cap="0.5")
