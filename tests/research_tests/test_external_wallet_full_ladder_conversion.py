from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = ROOT / "scripts/analysis/wallet_weather"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location(
    "research_external_wallet_full_ladder_history_v1",
    SCRIPT_DIR / "research_external_wallet_full_ladder_history_v1.py",
)
assert SPEC is not None and SPEC.loader is not None
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


def test_neg_risk_conversion_is_cash_inflow_and_burns_full_no_set() -> None:
    event_slug = "highest-temperature-in-test-city-on-july-1-2026"
    rows = [
        {
            "type": "TRADE",
            "side": "BUY",
            "outcome": "No",
            "conditionId": "condition-a",
            "size": 1,
            "usdcSize": 0.4,
            "timestamp": 1_782_860_400,
            "transactionHash": "0xbuy-a",
        },
        {
            "type": "TRADE",
            "side": "BUY",
            "outcome": "No",
            "conditionId": "condition-b",
            "size": 1,
            "usdcSize": 0.4,
            "timestamp": 1_782_860_401,
            "transactionHash": "0xbuy-b",
        },
        {
            "type": "CONVERSION",
            "conditionId": "condition-a",
            "size": 1,
            "usdcSize": 1,
            "timestamp": 1_782_860_500,
            "transactionHash": "0xconvert",
        },
    ]
    metadata = {
        event_slug: {
            "markets": [
                {
                    "conditionId": "condition-a",
                    "groupItemTitle": "20°C",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["1", "0"]',
                },
                {
                    "conditionId": "condition-b",
                    "groupItemTitle": "21°C",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0", "1"]',
                },
            ]
        }
    }

    portfolio = replay.event_portfolio(
        "test-city",
        "2026-07-01",
        [event_slug],
        rows,
        [],
        metadata,
    )

    assert portfolio["conversion_cash"] == 1
    assert portfolio["public_cashflow"] == pytest.approx(0.2)
    assert portfolio["economic_pnl_reconstructed"] == pytest.approx(0.2)
    assert portfolio["has_conversion"] is True
