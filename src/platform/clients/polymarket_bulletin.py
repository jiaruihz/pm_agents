"""Read Polymarket question data and official bulletin-board updates on Polygon.

The UMA CTF adapters expose both ``getQuestion`` and ``getUpdates``.  Updates
only have adjudicative relevance when they were posted by the question creator
recorded by the adapter; arbitrary callers can post messages for any question.
"""

from __future__ import annotations

import os
import time
from typing import Any

from eth_abi import decode, encode
from eth_utils import keccak
import requests


DEFAULT_POLYGON_RPC_URL = "https://polygon-bor-rpc.publicnode.com"
QUESTION_TUPLE = (
    "(uint256,uint256,uint256,uint256,uint256,bool,bool,bool,bool,address,address,bytes)"
)


def _bytes32(value: str) -> bytes:
    raw = bytes.fromhex(str(value).removeprefix("0x"))
    if len(raw) != 32:
        raise ValueError("question_id must be 32 bytes")
    return raw


def _eth_call(
    contract: str,
    signature: str,
    argument_types: list[str],
    arguments: list[Any],
    *,
    rpc_url: str | None = None,
    timeout: float = 15,
    attempts: int = 4,
    retry_base_seconds: float = 0.25,
) -> bytes:
    endpoint = rpc_url or os.getenv("POLYGON_RPC_URL") or DEFAULT_POLYGON_RPC_URL
    calldata = keccak(text=signature)[:4] + encode(argument_types, arguments)
    response = None
    for attempt in range(attempts):
        try:
            response = requests.post(
                endpoint,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_call",
                    "params": [{"to": contract, "data": "0x" + calldata.hex()}, "latest"],
                },
                timeout=timeout,
            )
        except (requests.Timeout, requests.ConnectionError):
            if attempt + 1 >= attempts:
                raise
            time.sleep(retry_base_seconds * (2**attempt))
            continue
        if response.status_code == 429 or response.status_code >= 500:
            if attempt + 1 >= attempts:
                response.raise_for_status()
            time.sleep(retry_base_seconds * (2**attempt))
            continue
        response.raise_for_status()
        break
    if response is None:
        raise RuntimeError("Polygon eth_call produced no response")
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(f"Polygon eth_call failed: {payload['error']}")
    result = str(payload.get("result") or "")
    if not result.startswith("0x"):
        raise RuntimeError("Polygon eth_call returned no result")
    return bytes.fromhex(result[2:])


def fetch_question_and_updates(
    adapter: str,
    question_id: str,
    *,
    rpc_url: str | None = None,
    timeout: float = 15,
) -> dict[str, Any]:
    """Return the adapter's canonical question and creator-authored updates."""
    qid = _bytes32(question_id)
    question_raw = _eth_call(
        adapter,
        "getQuestion(bytes32)",
        ["bytes32"],
        [qid],
        rpc_url=rpc_url,
        timeout=timeout,
    )
    question = decode([QUESTION_TUPLE], question_raw)[0]
    creator = str(question[10]).lower()
    ancillary = bytes(question[11])
    updates = fetch_creator_updates(
        adapter,
        question_id,
        creator,
        rpc_url=rpc_url,
        timeout=timeout,
    )
    return {
        "schema_version": "polymarket_bulletin_question_v1",
        "adapter": adapter.lower(),
        "question_id": "0x" + qid.hex(),
        "creator": creator,
        "request_timestamp": int(question[0]),
        "resolved": bool(question[5]),
        "paused": bool(question[6]),
        "reset": bool(question[7]),
        "refund": bool(question[8]),
        "ancillary_data_hex": "0x" + ancillary.hex(),
        "ancillary_text": ancillary.decode("utf-8", "replace"),
        "updates": updates,
    }


def fetch_creator_updates(
    adapter: str,
    question_id: str,
    creator: str,
    *,
    rpc_url: str | None = None,
    timeout: float = 15,
) -> list[dict[str, Any]]:
    """Read updates under the authoritative question-creator namespace."""
    qid = _bytes32(question_id)
    updates_raw = _eth_call(
        adapter,
        "getUpdates(bytes32,address)",
        ["bytes32", "address"],
        [qid, creator],
        rpc_url=rpc_url,
        timeout=timeout,
    )
    decoded_updates = decode(["(uint256,bytes)[]"], updates_raw)[0]
    return [
        {
            "timestamp": int(timestamp),
            "update_hex": "0x" + bytes(update).hex(),
            "text": bytes(update).decode("utf-8", "replace"),
            "publisher": creator.lower(),
        }
        for timestamp, update in decoded_updates
    ]


def is_question_initialized(
    adapter: str,
    question_id: str,
    *,
    rpc_url: str | None = None,
    timeout: float = 15,
) -> bool:
    """Check a candidate question key without depending on adapter tuple version."""
    result = _eth_call(
        adapter,
        "isInitialized(bytes32)",
        ["bytes32"],
        [_bytes32(question_id)],
        rpc_url=rpc_url,
        timeout=timeout,
    )
    return bool(decode(["bool"], result)[0])
