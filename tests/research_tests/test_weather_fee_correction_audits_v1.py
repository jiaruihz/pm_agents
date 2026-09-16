from __future__ import annotations

import pandas as pd

from scripts.analysis.market_structure_edge import research_all_yes_underround_fee_correction_v1 as basket_audit
from scripts.analysis.reheat_risk import research_current_bracket_no_prevday_pit_fee_correction_v1 as current_no_audit


def test_official_fee_rounds_per_order_to_five_decimals() -> None:
    assert basket_audit.official_weather_taker_fee(0.50, 1.0) == 0.01250
    assert basket_audit.official_weather_taker_fee(0.18, 1.0) == 0.00738
    assert basket_audit.official_weather_taker_fee(0.18, 5.0) == 0.03690


def test_small_underround_turns_negative_after_taker_fee() -> None:
    asks = [0.18, 0.56, 0.18, 0.049, 0.01]
    gross_pnl = 1.0 - sum(asks)
    fee = sum(basket_audit.official_weather_taker_fee(price) for price in asks)

    assert round(gross_pnl, 6) == 0.021
    assert round(fee, 5) == 0.02990
    assert gross_pnl - fee < 0


def test_fixed_five_dollar_no_fee_uses_shares_not_notional_as_contract_quantity() -> None:
    price = 0.20
    shares = 5.0 / price

    assert shares == 25.0
    assert current_no_audit.official_weather_taker_fee(price, shares) == 0.20


def test_current_no_selection_is_first_signal_per_city_day() -> None:
    frame = pd.DataFrame(
        [
            {"target_date": "2026-01-01", "city": "A", "decision_hour_local": 12, "no_ask": 0.2, "_source_row": 3},
            {"target_date": "2026-01-01", "city": "A", "decision_hour_local": 11, "no_ask": 0.3, "_source_row": 2},
            {"target_date": "2026-01-01", "city": "B", "decision_hour_local": 12, "no_ask": 0.2, "_source_row": 4},
        ]
    )

    selected = current_no_audit.select_first_per_city_day(frame)

    assert len(selected) == 2
    assert selected.loc[selected["city"].eq("A"), "decision_hour_local"].item() == 11
