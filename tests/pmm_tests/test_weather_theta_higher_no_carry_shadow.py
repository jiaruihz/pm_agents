import argparse

import pandas as pd

from scripts.ops.weather_theta_higher_no_carry_shadow import select_higher_no_candidates


def test_higher_no_selector_uses_independent_no_carry_filters():
    rows = pd.DataFrame(
        [
            {
                "city": "Helsinki",
                "target_date": "2026-06-17",
                "decline_c": 1.0,
                "d1_no_ask": 0.82,
                "d1_no_size": 10.0,
                "d1_no_bracket": "17",
                "yes_current_ask": 0.64,
            },
            {
                "city": "Ankara",
                "target_date": "2026-06-17",
                "decline_c": 1.0,
                "d1_no_ask": 0.70,
                "d1_no_size": 10.0,
                "d1_no_bracket": "31",
                "yes_current_ask": 0.88,
            },
            {
                "city": "Istanbul",
                "target_date": "2026-06-17",
                "decline_c": 0.0,
                "d1_no_ask": 0.90,
                "d1_no_size": 10.0,
                "d1_no_bracket": "24",
                "yes_current_ask": 0.54,
            },
        ]
    )
    args = argparse.Namespace(
        min_decline_c=0.5,
        min_no_ask=0.75,
        max_no_ask=0.97,
        max_current_yes_ask=None,
        min_available_notional=5.0,
    )

    selected = select_higher_no_candidates(rows, args)

    assert selected["city"].tolist() == ["Helsinki"]


def test_higher_no_selector_can_optionally_filter_current_yes_price():
    rows = pd.DataFrame(
        [
            {
                "city": "Helsinki",
                "target_date": "2026-06-17",
                "decline_c": 1.0,
                "d1_no_ask": 0.82,
                "d1_no_size": 10.0,
                "d1_no_bracket": "17",
                "yes_current_ask": 0.64,
            }
        ]
    )
    args = argparse.Namespace(
        min_decline_c=0.5,
        min_no_ask=0.75,
        max_no_ask=0.97,
        max_current_yes_ask=0.5,
        min_available_notional=5.0,
    )

    assert select_higher_no_candidates(rows, args).empty
