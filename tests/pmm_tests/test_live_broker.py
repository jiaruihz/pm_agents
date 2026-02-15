import pytest

from src.domains.pmm.execution.live_broker import LiveBroker


class DummyClient:
    def __init__(self) -> None:
        self.retry_calls = []

    async def retry(self, func, *args, **kwargs):
        self.retry_calls.append((func.__name__, args))
        return await func(*args, **kwargs)

    async def get_balance(self):
        return {"usdc_balance": 123.0}

    async def get_positions(self, token_ids):
        return {token_id: 1.0 for token_id in token_ids}

    async def get_orders(self, token_id=""):
        return [{"id": "o1", "asset_id": token_id or "t1"}]

    async def place_limit_order(self, token_id, price, size, side):
        return {"id": "live_1", "token_id": token_id, "price": price, "size": size, "side": side}

    async def cancel_order(self, order_id):
        return {"canceled": [order_id]}

    async def cancel_orders(self, order_ids):
        return {"canceled": list(order_ids)}

    async def cancel_all_orders(self):
        return {"canceled": "all"}

    async def merge_positions(self, condition_id, partition, amount, collateral_token="", parent_collection_id=""):
        return {
            "condition_id": condition_id,
            "partition": partition,
            "amount": amount,
            "collateral_token": collateral_token,
            "parent_collection_id": parent_collection_id,
        }


class DummyGuard:
    def __init__(self) -> None:
        self.calls = []

    def validate_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
        current_position=None,
    ) -> None:
        self.calls.append((token_id, price, size, side, current_position))


@pytest.mark.asyncio
async def test_live_broker_delegates_read_calls():
    client = DummyClient()
    broker = LiveBroker(http_client=client, dry_run=False)

    balance = await broker.get_balance()
    positions = await broker.get_positions(["t1", "t2"])
    orders = await broker.get_orders("t1")

    assert balance["usdc_balance"] == 123.0
    assert positions["t1"] == 1.0
    assert orders[0]["asset_id"] == "t1"
    assert [name for name, _ in client.retry_calls[:3]] == ["get_balance", "get_positions", "get_orders"]


@pytest.mark.asyncio
async def test_live_broker_place_order_dry_run_with_guard():
    client = DummyClient()
    guard = DummyGuard()
    broker = LiveBroker(http_client=client, safety_guard=guard, dry_run=True)

    res = await broker.place_limit_order("tid", 0.45, 3.0, "BUY")

    assert res["status"] == "simulated"
    assert guard.calls == [("tid", 0.45, 3.0, "BUY", 1.0)]
    assert all(name != "place_limit_order" for name, _ in client.retry_calls)
    assert ("get_positions", (["tid"],)) in client.retry_calls


@pytest.mark.asyncio
async def test_live_broker_place_order_live():
    client = DummyClient()
    broker = LiveBroker(http_client=client, dry_run=False)

    res = await broker.place_limit_order("tid", 0.5, 2.0, "SELL")

    assert res["id"] == "live_1"
    assert ("place_limit_order", ("tid", 0.5, 2.0, "SELL")) in client.retry_calls


@pytest.mark.asyncio
async def test_live_broker_merge_pair_returns_unsupported_without_endpoint():
    client = DummyClient()
    broker = LiveBroker(http_client=client, dry_run=False)

    res = await broker.merge_pair("yes_tid", "no_tid", 10)

    assert res["status"] == "unsupported"
    assert "unavailable" in res["reason"]


@pytest.mark.asyncio
async def test_live_broker_cancel_and_merge():
    client = DummyClient()
    broker = LiveBroker(http_client=client, dry_run=False)

    c1 = await broker.cancel_order("o1")
    c2 = await broker.cancel_orders(["o1", "o2"])
    c3 = await broker.cancel_all_orders()
    m = await broker.merge_positions("cond", [1, 2], 100, "usdc", "")

    assert c1["canceled"] == ["o1"]
    assert c2["canceled"] == ["o1", "o2"]
    assert c3["canceled"] == "all"
    assert m["amount"] == 100
