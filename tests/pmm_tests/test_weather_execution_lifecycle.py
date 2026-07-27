import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from src.strategies.weather_edge_v1.execution.contracts import BookLevel, LifecycleContext, MarketBook
from src.strategies.weather_edge_v1.execution.lifecycle import build_data_update_lifecycle_fields, evaluate_order_lifecycle
from src.strategies.weather_edge_v1.execution.profiles import get_execution_profile
from tests.pmm_tests.test_weather_execution_reconciliation import _order


FIXTURES = Path(__file__).parents[1] / "fixtures" / "weather_execution"


def _book(*, bid, ask, tick="0.001", epoch="book-v1"):
    return MarketBook(
        token_id="token-1",
        status="ok",
        fetched_at_utc="2026-07-26T08:00:00Z",
        venue_timestamp_utc="2026-07-26T08:00:00Z",
        book_epoch_ref=epoch,
        tick_size=tick,
        tick_size_source="fixture",
        minimum_order_shares="1",
        bids=(BookLevel(price=bid, size="20"),),
        asks=(BookLevel(price=ask, size="20"),),
    )


def _context(**overrides):
    values = {
        "now_utc": "2026-07-26T08:00:00Z",
        "data_epoch_ref": "metar:old",
        "lifecycle_owner": "owner-a",
        "thesis_valid": True,
        "token_unchanged": True,
        "book_fresh": True,
        "price_cap_valid": True,
        "depth_valid": True,
        "fee_adjusted_edge_valid": True,
    }
    values.update(overrides)
    return LifecycleContext(**values)


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


def test_d1_static_and_capped_chase_fixture_preserve_lifecycle_behavior():
    fixture = _fixture("d1_static_and_capped_chase.json")
    book = _book(bid=str(fixture["input"]["fresh_bid"]), ask=str(fixture["input"]["fresh_ask"]))
    static = evaluate_order_lifecycle(
        profile=get_execution_profile("d1_taker_plus_maker_static_v1"),
        order_state=_order(status="live", execution_profile="d1_taker_plus_maker_static_v1", execution_policy="d1_yes_high_mid_maker_v1", order_lifecycle_policy="maker_until_data_update", posted_price=str(fixture["input"]["posted_price"])),
        market_book=book,
        lifecycle_context=_context(maker_price_cap=str(fixture["input"]["initial_mid_cap"])),
        now_utc="2026-07-26T08:00:00Z",
    )
    assert static.action == "REST"
    assert static.reason == "static_maker_profile"

    chase = evaluate_order_lifecycle(
        profile=get_execution_profile("d1_taker_plus_maker_chase_to_mid_v1"),
        order_state=_order(status="live", execution_profile="d1_taker_plus_maker_chase_to_mid_v1", execution_policy="d1_yes_high_mid_maker_v1", posted_price=str(fixture["input"]["posted_price"]), reprice_count=0),
        market_book=book,
        lifecycle_context=_context(maker_price_cap=str(fixture["input"]["initial_mid_cap"])),
        now_utc="2026-07-26T08:00:00Z",
    )
    assert chase.action == "REPRICE_MAKER"
    assert chase.replacement_price == Decimal(str(fixture["expected"]["chase"]["next_price"]))
    assert chase.replacement_shares == Decimal("6")
    assert chase.lifecycle_action_id


def test_weather_data_update_cancel_owner_and_terminal_are_pure_noops_or_decisions():
    lifecycle = build_data_update_lifecycle_fields(
        data_source="metar",
        data_epoch_ref="metar:old",
        data_epoch_ts_utc="2026-07-26T07:00:00Z",
        next_data_update_due_utc="2026-07-26T08:02:00Z",
        cancel_buffer_sec=90,
        now=datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc),
    )
    profile = get_execution_profile("d1_taker_plus_maker_static_v1")
    order = _order(execution_profile=profile.name, execution_policy="d1_yes_high_mid_maker_v1", order_lifecycle_policy="maker_until_data_update", status="live", cancel_confirmed=False)
    cancel = evaluate_order_lifecycle(profile=profile, order_state=order, market_book=_book(bid="0.90", ask="0.91"), lifecycle_context=_context(deadline_utc=lifecycle["cancel_before_data_update_utc"]), now_utc=lifecycle["cancel_before_data_update_utc"])
    assert cancel.action == "CANCEL"
    assert cancel.reason == "maker_deadline_reached"
    assert cancel.lifecycle_action_id
    no_version = evaluate_order_lifecycle(profile=profile, order_state=_order(execution_profile=profile.name, execution_policy="d1_yes_high_mid_maker_v1", order_lifecycle_policy="maker_until_data_update", status="live", cancel_confirmed=False, authoritative_state_version=None), market_book=_book(bid="0.90", ask="0.91"), lifecycle_context=_context(deadline_utc=lifecycle["cancel_before_data_update_utc"]), now_utc=lifecycle["cancel_before_data_update_utc"])
    assert no_version.action == "REST" and no_version.reason == "authoritative_state_version_missing"

    changed = evaluate_order_lifecycle(profile=profile, order_state=order, market_book=_book(bid="0.90", ask="0.91"), lifecycle_context=_context(data_epoch_ref="metar:new"), now_utc="2026-07-26T08:00:00Z")
    assert changed.action == "CANCEL" and changed.reason == "data_epoch_changed"
    mismatch = evaluate_order_lifecycle(profile=profile, order_state=order, market_book=_book(bid="0.90", ask="0.91"), lifecycle_context=_context(lifecycle_owner="owner-b", data_epoch_ref="metar:new"), now_utc="2026-07-26T08:00:00Z")
    assert mismatch.action == "REST" and mismatch.reason == "lifecycle_owner_mismatch"
    terminal = evaluate_order_lifecycle(profile=profile, order_state=_order(status="filled", matched_shares="10", remaining_shares="0"), market_book=_book(bid="0.90", ask="0.91"), lifecycle_context=_context(), now_utc="2026-07-26T08:00:00Z")
    assert terminal.action == "TERMINAL"
    assert evaluate_order_lifecycle(profile=profile, order_state=None, market_book=_book(bid="0.90", ask="0.91"), lifecycle_context=_context(), now_utc="2026-07-26T08:00:00Z").reason == "authoritative_order_state_missing"


def test_heat_death_fixture_fallback_waits_for_confirmed_cancel_and_final_remaining():
    fixture = _fixture("heat_death_chase_and_fallback.json")
    profile = get_execution_profile("split_taker_maker_chase_v1")
    book = _book(bid=str(fixture["input"]["fresh_bid"]), ask=str(fixture["input"]["fresh_ask"]))
    context = _context(
        deadline_utc="2026-07-26T07:59:00Z",
        taker_price_cap=str(fixture["input"]["initial_ask_cap"]),
    )
    awaiting_cancel = evaluate_order_lifecycle(
        profile=profile,
        order_state=_order(status="live", cancel_confirmed=False, execution_policy="current_yes_heat_death_maker_probe_v1", posted_price=str(fixture["input"]["maker_quote"])),
        market_book=book,
        lifecycle_context=context,
        now_utc="2026-07-26T08:00:00Z",
    )
    assert awaiting_cancel.action == "CANCEL"

    false_confirmation = evaluate_order_lifecycle(
        profile=profile,
        order_state=_order(status="live", cancel_confirmed=True, execution_policy="current_yes_heat_death_maker_probe_v1", posted_price=str(fixture["input"]["maker_quote"])),
        market_book=book,
        lifecycle_context=context,
        now_utc="2026-07-26T08:00:00Z",
    )
    assert false_confirmation.action == "CANCEL"
    assert false_confirmation.reason == "maker_deadline_reached"

    fallback = evaluate_order_lifecycle(
        profile=profile,
        order_state=_order(status="cancelled", cancel_confirmed=True, execution_policy="current_yes_heat_death_maker_probe_v1", posted_price=str(fixture["input"]["maker_quote"])),
        market_book=book,
        lifecycle_context=context,
        now_utc="2026-07-26T08:00:00Z",
    )
    assert fallback.action == "TAKER_FALLBACK"
    assert fallback.replacement_shares == Decimal("6")
    assert fallback.replacement_price == Decimal(str(fixture["expected"]["fallback"]["limit_price"]))
    assert fallback.lifecycle_action_id


def test_repost_reprice_limits_and_fallback_guards_never_create_unsafe_actions():
    chase = get_execution_profile("d1_taker_plus_maker_chase_to_mid_v1")
    lower = evaluate_order_lifecycle(
        profile=chase,
        order_state=_order(status="live", execution_profile=chase.name, execution_policy="d1_yes_high_mid_maker_v1", posted_price="0.059", reprice_count=0),
        market_book=_book(bid="0.01", ask="0.014"),
        lifecycle_context=_context(repost_price="0.011"),
        now_utc="2026-07-26T08:00:00Z",
    )
    assert lower.action == "REPOST_LOWER" and lower.replacement_price == Decimal("0.011")
    capped = evaluate_order_lifecycle(
        profile=chase,
        order_state=_order(status="live", execution_profile=chase.name, execution_policy="d1_yes_high_mid_maker_v1", reprice_count=3),
        market_book=_book(bid="0.90", ask="0.91"),
        lifecycle_context=_context(maker_price_cap="0.905"),
        now_utc="2026-07-26T08:00:00Z",
    )
    assert capped.action == "REST" and capped.reason == "maker_reprice_limit_reached"
    no_book = evaluate_order_lifecycle(
        profile=chase,
        order_state=_order(status="live", execution_profile=chase.name, execution_policy="d1_yes_high_mid_maker_v1"),
        market_book=None,
        lifecycle_context=_context(),
        now_utc="2026-07-26T08:00:00Z",
    )
    assert no_book.action == "REST" and no_book.reason == "fresh_market_book_missing"

    static = get_execution_profile("d1_taker_plus_maker_static_v1")
    disabled = evaluate_order_lifecycle(
        profile=static,
        order_state=_order(status="cancelled", cancel_confirmed=True, execution_profile=static.name, execution_policy="d1_yes_high_mid_maker_v1", order_lifecycle_policy="maker_until_data_update"),
        market_book=_book(bid="0.90", ask="0.91"),
        lifecycle_context=_context(deadline_utc="2026-07-26T07:59:00Z", taker_price_cap="0.99"),
        now_utc="2026-07-26T08:00:00Z",
    )
    assert disabled.action != "TAKER_FALLBACK"
    assert disabled.action == "REST" and disabled.reason == "cancel_final_no_fallback_profile"
    heat = get_execution_profile("split_taker_maker_chase_v1")
    invalid = evaluate_order_lifecycle(
        profile=heat,
        order_state=_order(status="cancelled", cancel_confirmed=True, execution_policy="current_yes_heat_death_maker_probe_v1"),
        market_book=_book(bid="0.96", ask="0.965"),
        lifecycle_context=_context(deadline_utc="2026-07-26T07:59:00Z", taker_price_cap="0.97", depth_valid=False),
        now_utc="2026-07-26T08:00:00Z",
    )
    assert invalid.action == "REST" and invalid.reason == "taker_fallback_requirements_not_met"


def test_cancel_final_orders_never_emit_repeat_cancel_after_deadline_or_data_change():
    static = get_execution_profile("d1_taker_plus_maker_static_v1")
    book = _book(bid="0.90", ask="0.91")
    deadline_context = _context(deadline_utc="2026-07-26T07:59:00Z")
    cancelled = _order(status="cancelled", cancel_confirmed=True, execution_profile=static.name, execution_policy="d1_yes_high_mid_maker_v1", order_lifecycle_policy="maker_until_data_update")
    at_deadline = evaluate_order_lifecycle(profile=static, order_state=cancelled, market_book=book, lifecycle_context=deadline_context, now_utc="2026-07-26T08:00:00Z")
    assert at_deadline.action == "REST" and at_deadline.reason == "cancel_final_no_fallback_profile"
    after_epoch_change = evaluate_order_lifecycle(profile=static, order_state=cancelled, market_book=book, lifecycle_context=_context(data_epoch_ref="metar:new"), now_utc="2026-07-26T08:00:00Z")
    assert after_epoch_change.action == "REST" and after_epoch_change.reason == "cancel_final_data_epoch_changed"
    expired = evaluate_order_lifecycle(profile=static, order_state=_order(status="expired", cancel_confirmed=False, execution_profile=static.name, execution_policy="d1_yes_high_mid_maker_v1", order_lifecycle_policy="maker_until_data_update"), market_book=book, lifecycle_context=deadline_context, now_utc="2026-07-26T08:00:00Z")
    assert expired.action == "TERMINAL" and expired.reason == "expired_order"

    heat = get_execution_profile("split_taker_maker_chase_v1")
    missing_confirmation = evaluate_order_lifecycle(profile=heat, order_state=_order(status="cancelled", cancel_confirmed=False, execution_policy="current_yes_heat_death_maker_probe_v1"), market_book=_book(bid="0.96", ask="0.965"), lifecycle_context=_context(deadline_utc="2026-07-26T07:59:00Z", taker_price_cap="0.97"), now_utc="2026-07-26T08:00:00Z")
    assert missing_confirmation.action == "REST"
    assert missing_confirmation.reason == "cancel_final_fallback_cancel_unconfirmed"
    heat_epoch_changed = evaluate_order_lifecycle(profile=heat, order_state=_order(status="cancelled", cancel_confirmed=True, execution_policy="current_yes_heat_death_maker_probe_v1"), market_book=_book(bid="0.96", ask="0.965"), lifecycle_context=_context(data_epoch_ref="metar:new", deadline_utc="2026-07-26T07:59:00Z", taker_price_cap="0.97"), now_utc="2026-07-26T08:00:00Z")
    assert heat_epoch_changed.action == "REST"
    assert heat_epoch_changed.reason == "cancel_final_data_epoch_changed"
