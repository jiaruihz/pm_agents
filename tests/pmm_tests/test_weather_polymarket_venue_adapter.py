from decimal import Decimal

from src.strategies.weather_edge_v1.execution.contracts import (
    EXECUTION_SCHEMA_VERSION,
    ChildOrderPlan,
    ExecutionConstraints,
    ExecutionIntent,
    FeeSchedule,
    VenueCapabilities,
    make_execution_config_id,
    make_live_exposure_key,
    make_plan_dedupe_key,
)
from src.strategies.weather_edge_v1.execution.venue.polymarket import (
    PolymarketOrderRequest,
    PolymarketVenueAdapter,
)


NOW = "2026-07-26T08:00:00Z"


class FakeTransport:
    def __init__(self):
        self.capabilities = {
            "venue": "polymarket_clob",
            "protocol_version": "clob-v2",
            "client_version": "py-clob-client-v2",
            "collateral_asset": "USDC",
            "supported_order_types": ("GTC", "GTD", "FAK", "FOK"),
            "post_only_order_types": ("GTC", "GTD"),
            "price_precision": 3,
            "size_precision": 2,
            "amount_precision_by_order_type": {"GTC": 4, "GTD": 4, "FAK": 4, "FOK": 4},
            "gtd_security_threshold_sec": 30,
            "capabilities_fetched_at_utc": NOW,
            "fee_schedule_ref": "fees-v1",
        }
        self.fees = {
            "venue": "polymarket_clob",
            "fee_schedule_ref": "fees-v1",
            "fee_schedule_fetched_at_utc": NOW,
            "fee_formula_id": "conditional-v1",
            "taker_fee_parameters": {"rate": "0.02"},
            "maker_fee_parameters": {"rate": "0", "rebate_rate": "0.01"},
            "maker_rebate_program": "test-only-estimate",
        }
        self.instrument = {
            "tick_size": "0.001",
            "minimum_order_shares": "1",
            "tick_size_source": "fake-instrument",
            "instrument_version": "instrument-v1",
        }
        self.book = {
            "status": "ok",
            "fetched_at_utc": NOW,
            "venue_timestamp_utc": NOW,
            "sequence": "book-v1",
            "bids": [{"price": "0.900", "size": "20"}],
            "asks": [{"price": "0.905", "size": "20"}],
        }
        self.post_response = {"status": "accepted", "order_id": "venue-order-1"}
        self.cancel_response = {"status": "cancelled"}
        self.reconciliation = {"submit": None, "cancel": None}
        self.created_payloads = []
        self.post_calls = []
        self.cancel_calls = []
        self.order_payload = None

    def fetch_capabilities(self):
        return self.capabilities

    def fetch_fee_schedule(self):
        return self.fees

    def fetch_instrument(self, token_id):
        assert token_id == "token-1"
        return self.instrument

    def fetch_market_book(self, token_id):
        assert token_id == "token-1"
        return self.book

    def create_order(self, payload):
        self.created_payloads.append(dict(payload))
        return {"expected_venue_order_id": "expected-venue-1", **payload}

    def post_order(self, signed_order, *, order_type, post_only):
        self.post_calls.append((dict(signed_order), order_type, post_only))
        if isinstance(self.post_response, Exception):
            raise self.post_response
        return self.post_response

    def cancel_order(self, order_id):
        self.cancel_calls.append(order_id)
        if isinstance(self.cancel_response, Exception):
            raise self.cancel_response
        return self.cancel_response

    def fetch_order(self, order_id, client_order_id):
        return self.order_payload

    def reconcile_unknown(self, *, kind, identity_key):
        return self.reconciliation[kind]


def _request(*, order_type="GTC", post_only=False, price="0.901", shares="5", epoch="", expiration_utc=None):
    return PolymarketOrderRequest(
        token_id="token-1",
        venue_side="BUY",
        price=price,
        shares=shares,
        order_type=order_type,
        post_only=post_only,
        book_epoch_ref=epoch,
        now_utc=NOW,
        expiration_utc=expiration_utc,
    )


def _intent():
    profile = "taker_now_v1"
    return ExecutionIntent(
        execution_schema_version=EXECUTION_SCHEMA_VERSION,
        signal_id="signal-venue-1",
        opportunity_id="opportunity-venue-1",
        comparison_group_id="comparison-venue-1",
        strategy_id="weather-edge",
        strategy_instance="venue-adapter-test",
        config_id="config-venue-v1",
        execution_profile=profile,
        resolved_execution_profile=profile,
        execution_config_id=make_execution_config_id(resolved_execution_profile=profile, fixed_behavior={"venue_adapter": "v1"}),
        plan_dedupe_key=make_plan_dedupe_key(strategy_id="weather-edge", strategy_instance="venue-adapter-test", config_id="config-venue-v1", opportunity_id="opportunity-venue-1", execution_profile=profile, child_role="single"),
        live_exposure_key=make_live_exposure_key(authorized_scope="test", opportunity_id="opportunity-venue-1", token_id="token-1", venue_side="BUY", outcome_side="YES"),
        token_id="token-1",
        venue_side="BUY",
        outcome_side="YES",
        signal_side="YES",
        total_shares="5",
        created_at_utc=NOW,
        constraints=ExecutionConstraints(price_cap="0.91", minimum_shares="1"),
    )


def _child(intent, *, maker_only=False):
    return ChildOrderPlan(intent=intent, child_role="single", requested_shares="5", execution_policy="test", order_lifecycle_policy="taker_now", maker_only=maker_only)


def _snapshots(adapter):
    capabilities = adapter.fetch_capabilities()
    fees = adapter.fetch_fee_schedule()
    book = adapter.fetch_market_book("token-1")
    return capabilities, fees, book


def test_dynamic_tick_and_book_epoch_require_replan_without_fallback():
    transport = FakeTransport()
    adapter = PolymarketVenueAdapter(transport=transport)
    capabilities, fees, first = _snapshots(adapter)
    transport.instrument = {**transport.instrument, "tick_size": "0.01", "instrument_version": "instrument-v2"}
    transport.book = {**transport.book, "sequence": "book-v2"}

    second = adapter.fetch_market_book("token-1")
    prepared = adapter.prepare_order(request=_request(epoch=first.book_epoch_ref), market_book=first, capabilities=capabilities, fee_schedule=fees)

    assert first.tick_size == Decimal("0.001")
    assert second.tick_size == Decimal("0.01")
    assert first.book_epoch_ref != second.book_epoch_ref
    assert prepared.status == "blocked"
    assert prepared.reason == "book_epoch_changed_replan_required"


def test_gtc_gtd_fak_fok_precision_and_post_only_validation():
    transport = FakeTransport()
    adapter = PolymarketVenueAdapter(transport=transport)
    capabilities, fees, book = _snapshots(adapter)

    gtc = adapter.prepare_order(request=_request(epoch=book.book_epoch_ref, post_only=True), market_book=book, capabilities=capabilities, fee_schedule=fees, revalidate_book=False)
    gtd_too_soon = adapter.prepare_order(request=_request(order_type="GTD", post_only=True, epoch=book.book_epoch_ref, expiration_utc="2026-07-26T08:00:30Z"), market_book=book, capabilities=capabilities, fee_schedule=fees, revalidate_book=False)
    gtd = adapter.prepare_order(request=_request(order_type="GTD", post_only=True, epoch=book.book_epoch_ref, expiration_utc="2026-07-26T08:00:31Z"), market_book=book, capabilities=capabilities, fee_schedule=fees, revalidate_book=False)
    fok_amount = adapter.prepare_order(request=_request(order_type="FOK", epoch=book.book_epoch_ref, shares="1.11"), market_book=book, capabilities=capabilities, fee_schedule=fees, revalidate_book=False)
    fak_size = adapter.prepare_order(request=_request(order_type="FAK", epoch=book.book_epoch_ref, shares="1.111"), market_book=book, capabilities=capabilities, fee_schedule=fees, revalidate_book=False)
    crossing = adapter.prepare_order(request=_request(post_only=True, price="0.905", epoch=book.book_epoch_ref), market_book=book, capabilities=capabilities, fee_schedule=fees, revalidate_book=False)

    assert gtc.status == "ready" and gtc.normalized_price == Decimal("0.901")
    assert gtd_too_soon.reason == "gtd_expiration_inside_security_threshold"
    assert gtd.status == "ready"
    assert fok_amount.reason == "amount_precision_exceeded"
    assert fak_size.reason == "size_precision_exceeded"
    assert crossing.reason == "post_only_crosses_book"


def test_place_uses_deterministic_identity_and_preserves_fee_protocol_provenance():
    transport = FakeTransport()
    adapter = PolymarketVenueAdapter(
        transport=transport,
        order_request_builder=lambda intent, child, book, capabilities, fees, replacement: _request(epoch=book.book_epoch_ref),
    )
    capabilities, fees, book = _snapshots(adapter)
    intent = _intent()

    result = adapter.place(intent=intent, child=_child(intent), market_book=book, capabilities=capabilities, fee_schedule=fees)
    same = adapter.place(intent=intent, child=_child(intent), market_book=book, capabilities=capabilities, fee_schedule=fees)

    assert result["status"] == "submitted"
    assert result["client_order_id"] == same["client_order_id"] == transport.created_payloads[0]["client_order_id"]
    assert result["expected_venue_order_id"] == "expected-venue-1"
    assert result["protocol_version"] == "clob-v2"
    assert result["collateral_asset"] == "USDC"
    # 5 * 0.02 * (0.901 * 0.099), rounded to five decimal places.
    assert result["estimated_fee_usd"] == Decimal("0.00892")
    assert result["realized_maker_rebate_usd"] is None


def test_fee_change_changes_fee_identity_without_changing_strategy_identity():
    transport = FakeTransport()
    adapter = PolymarketVenueAdapter(transport=transport)
    capabilities, first_fee, book = _snapshots(adapter)
    intent = _intent()
    strategy_identity = intent.execution_config_id
    request = _request(epoch=book.book_epoch_ref)
    first = adapter.prepare_order(request=request, market_book=book, capabilities=capabilities, fee_schedule=first_fee, revalidate_book=False)
    transport.fees = {**transport.fees, "fee_schedule_ref": "fees-v2"}
    transport.capabilities = {**transport.capabilities, "fee_schedule_ref": "fees-v2"}
    second_fee = adapter.fetch_fee_schedule()
    second_capabilities = adapter.fetch_capabilities()
    second = adapter.prepare_order(request=request, market_book=book, capabilities=second_capabilities, fee_schedule=second_fee, revalidate_book=False)

    assert intent.execution_config_id == strategy_identity
    assert first.fee_identity != second.fee_identity
    assert first.status == second.status == "ready"


def test_maker_rebate_is_an_estimate_never_realized_cash():
    transport = FakeTransport()
    adapter = PolymarketVenueAdapter(
        transport=transport,
        order_request_builder=lambda intent, child, book, capabilities, fees, replacement: _request(post_only=True, epoch=book.book_epoch_ref),
    )
    capabilities, fees, book = _snapshots(adapter)
    intent = _intent()

    result = adapter.place(intent=intent, child=_child(intent, maker_only=True), market_book=book, capabilities=capabilities, fee_schedule=fees)

    assert result["status"] == "submitted"
    assert result["estimated_fee_usd"] == Decimal("0")
    assert result["estimated_maker_rebate_usd"] == Decimal("0.00446")
    assert result["realized_maker_rebate_usd"] is None
    assert transport.post_calls[0][2] is True


def test_post_only_exchange_rejection_and_unknown_submit_cancel_reconcile_are_explicit():
    transport = FakeTransport()
    adapter = PolymarketVenueAdapter(
        transport=transport,
        order_request_builder=lambda intent, child, book, capabilities, fees, replacement: _request(post_only=True, epoch=book.book_epoch_ref),
    )
    capabilities, fees, book = _snapshots(adapter)
    intent = _intent()
    child = _child(intent, maker_only=True)
    transport.post_response = {"status": "error", "message": "invalid post-only order crosses book"}
    rejected = adapter.place(intent=intent, child=child, market_book=book, capabilities=capabilities, fee_schedule=fees)
    transport.post_response = RuntimeError("post may have reached exchange")
    unknown = adapter.place(intent=intent, child=child, market_book=book, capabilities=capabilities, fee_schedule=fees)
    transport.reconciliation["submit"] = {"status": "accepted", "order_id": "after-lookup"}
    reconciled_submit = adapter.reconcile_unknown(kind="submit", identity_key="identity-1")
    transport.cancel_response = RuntimeError("cancel may have reached exchange")
    transport.order_payload = _mapped_order_payload()
    order_state = adapter.fetch_order_state("venue-order-1", "client-order-1")
    assert order_state is not None
    cancelled = adapter.cancel(order_state)
    transport.reconciliation["cancel"] = {"status": "cancelled"}
    reconciled_cancel = adapter.reconcile_unknown(kind="cancel", identity_key="action-1")

    assert rejected["status"] == "rejected" and rejected["reason"] == "post_only_crosses_book"
    assert unknown["status"] == "unknown"
    assert unknown["reason"] == "post_order_dispatch_unknown"
    assert unknown["dispatch_stage"] == "post_order"
    assert reconciled_submit == {"status": "submitted", "raw_response": {"status": "accepted", "order_id": "after-lookup"}, "identity_key": "identity-1"}
    assert cancelled["status"] == "unknown"
    assert cancelled["reason"] == "cancel_order_dispatch_unknown"
    assert cancelled["dispatch_stage"] == "cancel_order"
    assert reconciled_cancel == {"status": "cancelled", "raw_response": {"status": "cancelled"}, "identity_key": "action-1"}


def _mapped_order_payload(**overrides):
    values = {
        "order_id": "venue-order-1",
        "client_order_id": "client-order-1",
        "plan_id": "plan-1",
        "token_id": "token-1",
        "venue_side": "BUY",
        "outcome_side": "YES",
        "requested_shares": "5",
        "matched_shares": "3",
        "remaining_shares": "2",
        "posted_price": "0.901",
        "status": "MATCHED",
        "created_at_utc": NOW,
        "execution_profile": "taker_now_v1",
        "execution_policy": "taker_top_ask_v1",
        "order_lifecycle_policy": "taker_now",
        "authoritative_state_version": "order-v1",
    }
    values.update(overrides)
    return values


def test_authoritative_order_mapping_keeps_raw_trade_like_status_unsafe_for_replacement():
    transport = FakeTransport()
    transport.order_payload = _mapped_order_payload()
    adapter = PolymarketVenueAdapter(transport=transport)

    state = adapter.fetch_order_state("venue-order-1", "client-order-1")

    assert state is not None
    assert state.status == "unknown"
    assert state.raw_venue_status == "MATCHED"
    assert state.order_state_provenance == "polymarket_authenticated_order_lookup_v1"
    assert state.matched_shares == Decimal("3") and state.remaining_shares == Decimal("2")
