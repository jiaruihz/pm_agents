import pytest

from src.domains.pmm.risk.safety_guard import RiskError, SafetyGuard, SecurityError


def _guard(**kwargs) -> SafetyGuard:
    params = {
        "allowed_tokens": {"YES_1", "NO_1"},
        "max_order_value": 100.0,
        "max_position": 10.0,
        "max_daily_loss": 50.0,
        "price_floor": 0.01,
        "price_ceiling": 0.99,
    }
    params.update(kwargs)
    return SafetyGuard(**params)


def test_validate_order_allows_valid_order():
    guard = _guard()
    guard.validate_order(
        token_id="YES_1",
        price=0.5,
        size=2.0,
        side="BUY",
        current_position=3.0,
    )


def test_validate_order_blocks_non_whitelisted_token():
    guard = _guard()
    with pytest.raises(SecurityError):
        guard.validate_order("UNKNOWN", 0.5, 1.0, "BUY")


def test_validate_order_blocks_invalid_side():
    guard = _guard()
    with pytest.raises(RiskError, match="invalid side"):
        guard.validate_order("YES_1", 0.5, 1.0, "HOLD")


def test_validate_order_blocks_non_positive_size():
    guard = _guard()
    with pytest.raises(RiskError, match="size must be > 0"):
        guard.validate_order("YES_1", 0.5, 0.0, "BUY")


def test_validate_order_blocks_price_below_floor():
    guard = _guard()
    with pytest.raises(RiskError, match="outside"):
        guard.validate_order("YES_1", 0.001, 1.0, "BUY")


def test_validate_order_blocks_price_above_ceiling():
    guard = _guard()
    with pytest.raises(RiskError, match="outside"):
        guard.validate_order("YES_1", 0.9999, 1.0, "BUY")


def test_validate_order_blocks_max_order_notional():
    guard = _guard(max_order_value=10.0)
    with pytest.raises(RiskError, match="order notional"):
        guard.validate_order("YES_1", 0.8, 20.0, "BUY")


def test_validate_order_blocks_directional_buy_notional():
    guard = _guard(max_order_value=1000.0, max_buy_order_value=5.0)
    with pytest.raises(RiskError, match="BUY notional"):
        guard.validate_order("YES_1", 0.5, 20.0, "BUY")


def test_validate_order_blocks_directional_sell_notional():
    guard = _guard(max_order_value=1000.0, max_sell_order_value=4.0)
    with pytest.raises(RiskError, match="SELL notional"):
        guard.validate_order("YES_1", 0.5, 9.0, "SELL")


def test_validate_order_blocks_daily_loss_circuit_breaker():
    guard = _guard(max_daily_loss=50.0)
    guard.record_pnl(-50.0)
    with pytest.raises(RiskError, match="daily loss"):
        guard.validate_order("YES_1", 0.5, 1.0, "BUY")


def test_validate_order_blocks_projected_abs_position():
    guard = _guard(max_position=5.0)
    with pytest.raises(RiskError, match="abs position"):
        guard.validate_order(
            token_id="YES_1",
            price=0.5,
            size=2.0,
            side="BUY",
            current_position=4.0,
        )


def test_validate_order_blocks_projected_short_position():
    guard = _guard(max_short_position=3.0)
    with pytest.raises(RiskError, match="short position"):
        guard.validate_order(
            token_id="YES_1",
            price=0.4,
            size=2.5,
            side="SELL",
            current_position=-1.0,
        )


def test_validate_order_blocks_projected_long_position():
    guard = _guard(max_long_position=4.0)
    with pytest.raises(RiskError, match="long position"):
        guard.validate_order(
            token_id="YES_1",
            price=0.4,
            size=2.5,
            side="BUY",
            current_position=2.0,
        )


def test_predict_position_after_order_and_reset_daily():
    guard = _guard()
    assert guard.predict_position_after_order(1.5, 2.0, "BUY") == 3.5
    assert guard.predict_position_after_order(1.5, 2.0, "SELL") == -0.5

    guard.record_pnl(-12.0)
    assert guard.daily_pnl == -12.0
    guard.reset_daily()
    assert guard.daily_pnl == 0.0
