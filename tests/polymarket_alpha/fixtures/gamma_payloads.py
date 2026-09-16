"""Offline Gamma payload fixtures for the P0-03 catalog adapter tests.

Shapes mirror the fields the existing ``src/platform/clients/gamma.py``
normalizer consumes (string-encoded list fields, nested ``events``, camelCase
keys).  Numeric values are strings so no float ever enters the canonical
contracts.  These fixtures are frozen offline evidence; they are not a live
API capture.
"""

from __future__ import annotations

import json
from typing import Any


BASE_RULES = (
    "This market resolves YES if the official NWS final report confirms the "
    "temperature reached the threshold. Resolves NO otherwise."
)


def market_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "12345",
        "conditionId": "0xcondition12345",
        "slug": "will-x-happen",
        "question": "Will X happen?",
        "rules": BASE_RULES,
        "active": True,
        "closed": False,
        "resolved": False,
        "endDate": "2026-12-31T00:00:00Z",
        "volume": "12345.67",
        "liquidity": "890.12",
        "outcomes": json.dumps(["Yes", "No"]),
        "clobTokenIds": json.dumps(["token-yes-12345", "token-no-12345"]),
        "updatedAt": "2026-08-20T00:00:00Z",
        "events": [
            {
                "id": "777",
                "slug": "x-event",
                "title": "X Event",
                "ticker": "XEVT",
            }
        ],
    }
    payload.update(overrides)
    return payload


def multi_market_event_payloads() -> list[dict[str, Any]]:
    """One event with three markets; the third has no condition id."""

    shared_event = [{"id": "event-multi", "slug": "multi", "title": "Multi Market Event"}]
    return [
        market_payload(
            id="20001",
            conditionId="0xcond20001",
            slug="multi-a",
            question="Will A happen?",
            events=shared_event,
            clobTokenIds=json.dumps(["tok-yes-20001", "tok-no-20001"]),
        ),
        market_payload(
            id="20002",
            conditionId="0xcond20002",
            slug="multi-b",
            question="Will B happen?",
            events=shared_event,
            clobTokenIds=json.dumps(["tok-yes-20002", "tok-no-20002"]),
        ),
        market_payload(
            id="20003",
            conditionId=None,
            slug="multi-c",
            question="Will C happen?",
            events=shared_event,
            clobTokenIds=json.dumps(["tok-yes-20003", "tok-no-20003"]),
        ),
    ]
