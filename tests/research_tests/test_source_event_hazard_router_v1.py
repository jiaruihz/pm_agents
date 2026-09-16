from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "research_source_event_hazard_router_v1",
    ROOT / "scripts/analysis/market_structure_edge/research_source_event_hazard_router_v1.py",
)
assert SPEC is not None and SPEC.loader is not None
router = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = router
SPEC.loader.exec_module(router)


def test_weather_fee_uses_official_curve_and_rounding() -> None:
    assert router.fee_per_share(0.50) == 0.0125
    assert router.fee_per_share(0.95) == 0.00238


def test_relative_quote_respects_exact_ladder_order() -> None:
    ladder = [
        {"bracket": "29", "yes_ask": 0.05},
        {"bracket": "30", "yes_ask": 0.60},
        {"bracket": "31", "yes_ask": 0.25},
    ]

    previous_key, previous = router.relative_quote(ladder, "30", -1)
    current_key, current = router.relative_quote(ladder, 30.0, 0)
    next_key, next_quote = router.relative_quote(ladder, "30", 1)

    assert (previous_key, previous["yes_ask"]) == ("29", 0.05)
    assert (current_key, current["yes_ask"]) == ("30", 0.60)
    assert (next_key, next_quote["yes_ask"]) == ("31", 0.25)


def test_missing_relative_quote_fails_closed() -> None:
    key, quote = router.relative_quote([{"bracket": "30"}], "30", 1)
    assert key == ""
    assert quote is None


@pytest.mark.parametrize("value, expected", [(30, "30"), (30.0, "30"), ("30-31", "30-31")])
def test_bracket_key_preserves_market_identity(value, expected) -> None:
    assert router.bracket_key(value) == expected
