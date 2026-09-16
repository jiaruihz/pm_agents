from __future__ import annotations

import pytest

from scripts.analysis.audit_weather_selected_increment_v2 import (
    DEFAULT_CORE_INPUT,
    DEFAULT_TMIN_ROW_AUDIT,
    DEFAULT_TMIN_TRADE_FUNNEL,
    build,
)


def test_core_and_tmin_use_paired_fixed_denominator_increment() -> None:
    result, _core_ledger, _tmin_ledger = build(
        core_input=DEFAULT_CORE_INPUT,
        tmin_row_audit=DEFAULT_TMIN_ROW_AUDIT,
        tmin_trade_funnel=DEFAULT_TMIN_TRADE_FUNNEL,
    )

    core = result["strategies"]["core_carry"]
    assert core["diagnosis"] == "selection_expansion_without_probability_increment"
    assert core["selected_policy"]["challenger_vs_core"]["fixed_universe"] == {
        "opportunities": 182,
        "policy_groups": 108,
        "target_dates": 7,
    }
    assert core["selected_policy"]["challenger_vs_core"]["paired_increment"][
        "pnl_per_share"
    ] == pytest.approx(0.55154)

    tmin = result["strategies"]["tmin"]
    incumbent = tmin["paired_policy"]["v1_incumbent_vs_market"]
    assert incumbent["fixed_universe"] == {
        "opportunities": 111,
        "policy_groups": 33,
        "target_dates": 15,
    }
    assert incumbent["selection_relation"]["challenger_only"] == 17
    assert incumbent["paired_increment"]["pnl_per_share"] == pytest.approx(
        0.51170435
    )
    assert tmin["paired_policy"]["v2_1_routed_vs_market"]["challenger"][
        "entries"
    ] == 0
    assert result["strategies"]["wcir_next_print"]["status"] == (
        "not_identifiable_no_action_mapping"
    )
