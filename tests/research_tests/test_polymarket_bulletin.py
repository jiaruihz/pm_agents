from __future__ import annotations

from eth_abi import encode
import requests

from src.platform.clients import polymarket_bulletin


def test_fetches_only_question_creator_update(monkeypatch) -> None:
    creator = "0x0000000000000000000000000000000000000001"
    question = (
        100,
        0,
        0,
        7200,
        0,
        False,
        False,
        False,
        False,
        "0x0000000000000000000000000000000000000002",
        creator,
        b"q: title: Test, description: Original market_id: 1",
    )
    replies = iter(
        [
            encode([polymarket_bulletin.QUESTION_TUPLE], [question]),
            encode(["(uint256,bytes)[]"], [[(150, b"Additional context: arms count.")]]),
        ]
    )
    monkeypatch.setattr(polymarket_bulletin, "_eth_call", lambda *args, **kwargs: next(replies))
    result = polymarket_bulletin.fetch_question_and_updates(
        "0x65070be91477460d8a7aeeb94ef92fe056c2f2a7",
        "0x" + "11" * 32,
    )
    assert result["creator"] == creator
    assert result["updates"] == [
        {
            "timestamp": 150,
            "update_hex": "0x" + b"Additional context: arms count.".hex(),
            "text": "Additional context: arms count.",
            "publisher": creator,
        }
    ]


def test_fetch_creator_updates_uses_explicit_creator(monkeypatch) -> None:
    creator = "0x0000000000000000000000000000000000000001"
    calls = []

    def fake_call(*args, **kwargs):
        calls.append((args, kwargs))
        return encode(["(uint256,bytes)[]"], [[(150, b"Clarification")]])

    monkeypatch.setattr(polymarket_bulletin, "_eth_call", fake_call)
    updates = polymarket_bulletin.fetch_creator_updates(
        "0x65070be91477460d8a7aeeb94ef92fe056c2f2a7",
        "0x" + "11" * 32,
        creator,
    )
    assert calls[0][0][1] == "getUpdates(bytes32,address)"
    assert calls[0][0][3][1] == creator
    assert updates[0]["text"] == "Clarification"


def test_is_question_initialized_decodes_bool(monkeypatch) -> None:
    monkeypatch.setattr(
        polymarket_bulletin,
        "_eth_call",
        lambda *args, **kwargs: encode(["bool"], [True]),
    )
    assert polymarket_bulletin.is_question_initialized(
        "0x65070be91477460d8a7aeeb94ef92fe056c2f2a7",
        "0x" + "11" * 32,
    )


def test_eth_call_retries_transient_rpc_failure(monkeypatch) -> None:
    failed = requests.Response()
    failed.status_code = 503
    failed.url = "https://rpc.example"
    good = requests.Response()
    good.status_code = 200
    good._content = b'{"jsonrpc":"2.0","id":1,"result":"0x"}'
    replies = iter([failed, good])
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: next(replies))
    assert polymarket_bulletin._eth_call(
        "0x65070be91477460d8a7aeeb94ef92fe056c2f2a7",
        "isInitialized(bytes32)",
        ["bytes32"],
        [bytes(32)],
        retry_base_seconds=0,
    ) == b""
