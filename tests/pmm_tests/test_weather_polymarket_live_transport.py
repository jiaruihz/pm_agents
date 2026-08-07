from types import SimpleNamespace

from scripts.ops.weather_polymarket_live_transport import LivePolymarketTransport


class FakeClient:
    def __init__(self):
        self.book = SimpleNamespace(
            hash="book-1",
            timestamp="1",
            tick_size="0.01",
            min_order_size="5",
            bids=[
                SimpleNamespace(price="0.80", size="10"),
                SimpleNamespace(price="0.81", size="5"),
            ],
            asks=[
                SimpleNamespace(price="0.85", size="10"),
                SimpleNamespace(price="0.84", size="5"),
            ],
        )
        self.order = {
            "id": "venue-1",
            "status": "LIVE",
            "asset_id": "token-1",
            "side": "BUY",
            "original_size": "5",
            "size_matched": "2",
            "price": "0.81",
        }

    def get_order_book(self, _token_id):
        return self.book

    def get_tick_size(self, _token_id):
        return "0.01"

    def get_fee_rate_bps(self, _token_id):
        return 150

    def create_order(self, args):
        return {"signed": True, "args": args}

    def post_order(self, _signed, *, order_type, post_only):
        assert order_type == "GTC"
        assert post_only is True
        return {"status": "live", "orderID": "venue-1"}

    def get_order(self, _order_id):
        return self.order

    def cancel_order(self, payload):
        return {"canceled": [payload.orderID]}


class Args:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class Payload:
    def __init__(self, orderID):
        self.orderID = orderID


class Types:
    GTC = "GTC"


def _transport():
    return LivePolymarketTransport(
        client=FakeClient(),
        order_args_cls=Args,
        order_payload_cls=Payload,
        order_type_cls=Types,
    )


def test_book_is_sorted_and_token_fee_is_explicit():
    transport = _transport()

    book = transport.fetch_market_book("token-1")
    fees = transport.fetch_fee_schedule_for_token("token-1")

    assert [row["price"] for row in book["bids"]] == ["0.81", "0.80"]
    assert [row["price"] for row in book["asks"]] == ["0.84", "0.85"]
    assert fees["taker_fee_parameters"]["fee_rate_bps"] == 150
    assert fees["taker_fee_parameters"]["rate"] == "0.015"


def test_submit_registry_reconstructs_partial_fill_remaining_shares():
    transport = _transport()
    signed = transport.create_order(
        {
            "client_order_id": "client-1",
            "identity_key": "identity-1",
            "plan_id": "plan-1",
            "token_id": "token-1",
            "price": "0.81",
            "shares": "5",
            "side": "BUY",
            "expiration_utc": None,
            "outcome_side": "YES",
            "post_only": True,
            "created_at_utc": "2026-07-28T08:00:00Z",
            "execution_profile": "split_taker_maker_chase_capped_no_fallback_v1",
            "execution_policy": "current_yes_residual_carry_maker_v1",
            "order_lifecycle_policy": "maker_chase_until_observation_or_ttl_v1",
            "data_epoch_ref": "obs-1",
            "lifecycle_owner": "owner",
        }
    )
    transport.post_order(signed, order_type="GTC", post_only=True)

    state = transport.fetch_order("venue-1", "client-1")

    assert state is not None
    assert state["requested_shares"] == "5"
    assert state["matched_shares"] == "2"
    assert state["remaining_shares"] == "3"
    assert state["authoritative_state_version"] == "LIVE:2:0.81"


def test_cancel_normalizes_confirmed_order_id():
    transport = _transport()

    result = transport.cancel_order("venue-1")

    assert result["status"] == "cancelled"
