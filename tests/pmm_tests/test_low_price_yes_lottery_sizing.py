from argparse import Namespace

from scripts.ops.low_price_yes_lottery_tiny_live import (
    shares_for_price_tier_6_8_10,
    shares_for_sizing_policy,
    sizing_shadow,
)


def test_price_tier_6_8_10_boundaries():
    assert shares_for_price_tier_6_8_10(price=0.05, min_shares=5.0) == 6.0
    assert shares_for_price_tier_6_8_10(price=0.08, min_shares=5.0) == 6.0
    assert shares_for_price_tier_6_8_10(price=0.0801, min_shares=5.0) == 8.0
    assert shares_for_price_tier_6_8_10(price=0.14, min_shares=5.0) == 8.0
    assert shares_for_price_tier_6_8_10(price=0.1401, min_shares=5.0) == 10.0
    assert shares_for_price_tier_6_8_10(price=0.20, min_shares=5.0) == 10.0


def test_live_sizing_policy_keeps_fixed_cash_as_explicit_alternative():
    row = {"edge": 0.25, "p_cal_no_city_ev": 0.40}
    assert (
        shares_for_sizing_policy(
            policy="price_tier_6_8_10_shares",
            price=0.17,
            row=row,
            order_notional_usd=0.8,
            min_shares=5.0,
        )
        == 10.0
    )
    assert (
        shares_for_sizing_policy(
            policy="fixed_cash_order_notional",
            price=0.17,
            row=row,
            order_notional_usd=0.8,
            min_shares=5.0,
        )
        == 5.0
    )


def test_shadow_sizing_variants_include_live_and_counterfactuals():
    args = Namespace(
        order_notional_usd=0.8,
        min_order_shares=5.0,
        taker_fee_rate=0.05,
        maker_rebate_rate=0.0,
        sizing_policy="price_tier_6_8_10_shares",
    )
    shadow = sizing_shadow(0.17, 0.32, {"edge": 0.15, "p_cal_no_city_ev": 0.55}, args)

    assert shadow["live_selected"]["policy"] == "price_tier_6_8_10_shares"
    assert shadow["live_selected"]["shares"] == 10.0
    assert shadow["fixed_cash_0p80"]["shares"] == 4.705882
    assert shadow["fixed_8_shares"]["shares"] == 8.0
    assert shadow["price_tier_6_8_10_shares"]["shares"] == 10.0
    assert shadow["quality_price_tier_5_8_12_shares"]["shares"] == 12.0
