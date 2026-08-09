import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from strategies.weather_edge_v1.tools.city_day_position_state import (  # noqa: E402
    CityDayPositionState,
    PositionIntent,
    apply_position_intent,
    mark_position_status,
    settle_exact_bracket_positions,
)


def _intent(
    signal_id: str,
    *,
    instrument: str = "Helsinki|2026-07-18|25",
    target_shares: float = 5.0,
    eligible: bool = True,
) -> PositionIntent:
    return PositionIntent(
        signal_id=signal_id,
        signal_ts_utc="2026-07-18T12:00:00Z",
        instrument_id=instrument,
        side="BUY_NO",
        target_shares=target_shares,
        unit_cash_cost=0.36,
        p_win=0.40,
        eligible=eligible,
        eligibility_reason="upstream_edge_positive" if eligible else "upstream_edge_non_positive",
    )


def test_open_hold_add_and_duplicate_are_inventory_targets() -> None:
    state = CityDayPositionState(city="Helsinki", target_date="2026-07-18")
    opened = apply_position_intent(state, _intent("s1"))
    held = apply_position_intent(state, _intent("s2"))
    added = apply_position_intent(state, _intent("s3", target_shares=10.0))
    duplicate = apply_position_intent(state, _intent("s3", target_shares=15.0))

    assert opened.action == "OPEN"
    assert opened.incremental_shares == 5.0
    assert held.action == "HOLD"
    assert added.action == "ADD"
    assert added.incremental_shares == 5.0
    assert duplicate.action == "DUPLICATE"
    assert state.cash_deployed == pytest.approx(3.6)


def test_signal_eligibility_remains_upstream() -> None:
    state = CityDayPositionState(city="Helsinki", target_date="2026-07-18")
    action = apply_position_intent(state, _intent("s1", eligible=False))

    assert action.action == "NOOP"
    assert not state.positions
    assert action.reason == "upstream_edge_non_positive"


def test_optional_cash_budget_is_a_risk_budget_not_an_edge_threshold() -> None:
    state = CityDayPositionState(city="Helsinki", target_date="2026-07-18")
    action = apply_position_intent(
        state,
        _intent("s1", target_shares=10.0),
        max_city_day_cash=1.8,
    )

    assert action.action == "OPEN"
    assert action.incremental_shares == 5.0
    assert action.reason.endswith("scaled_by_cash_budget")
    blocked = apply_position_intent(
        state,
        _intent(
            "s2",
            instrument="Helsinki|2026-07-18|26",
            target_shares=5.0,
        ),
        max_city_day_cash=1.8,
    )
    assert blocked.action == "RISK_BLOCK"


def test_lock_and_exact_bracket_settlement_update_payoff() -> None:
    state = CityDayPositionState(city="Helsinki", target_date="2026-07-18")
    apply_position_intent(state, _intent("s1"))
    apply_position_intent(
        state,
        _intent("s2", instrument="Helsinki|2026-07-18|26"),
    )
    mark_position_status(
        state,
        instrument_id="Helsinki|2026-07-18|25",
        side="BUY_NO",
        status="LOCKED_WIN",
        updated_at_utc="2026-07-18T13:00:00Z",
    )
    assert state.cash_at_risk == pytest.approx(1.8)
    assert state.locked_payout == 5.0

    settle_exact_bracket_positions(
        state,
        winning_instrument_id="Helsinki|2026-07-18|26",
        settled_at_utc="2026-07-19T00:00:00Z",
    )
    first = state.positions["Helsinki|2026-07-18|25|BUY_NO"]
    second = state.positions["Helsinki|2026-07-18|26|BUY_NO"]
    assert first.status == "SETTLED_WIN"
    assert second.status == "SETTLED_LOSS"
    assert state.total_pnl == pytest.approx(1.4)


def test_invalid_intent_does_not_poison_signal_dedupe_state() -> None:
    state = CityDayPositionState(city="Helsinki", target_date="2026-07-18")
    invalid = _intent("s1", target_shares=float("nan"))
    with pytest.raises(ValueError, match="target_shares"):
        apply_position_intent(state, invalid)
    assert "s1" not in state.processed_signal_ids

    valid = apply_position_intent(state, _intent("s1"))
    assert valid.action == "OPEN"


def test_invalid_risk_budget_and_settlement_identity_fail_closed() -> None:
    state = CityDayPositionState(city="Helsinki", target_date="2026-07-18")
    with pytest.raises(ValueError, match="max_city_day_cash"):
        apply_position_intent(state, _intent("s1"), max_city_day_cash=float("nan"))
    with pytest.raises(ValueError, match="winning_instrument_id"):
        settle_exact_bracket_positions(
            state, winning_instrument_id="", settled_at_utc="2026-07-19T00:00:00Z"
        )
