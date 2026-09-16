from __future__ import annotations

import pytest

from weather_dashboard.legacy_migration.strategy_runtime_orders import (
    _enrich_runtime_order,
)


def test_selected_token_probability_survives_runtime_migration() -> None:
    row = _enrich_runtime_order(
        {
            "city": "Wellington",
            "target_date": "2026-08-05",
            "signal_side": "BUY_YES",
            "order_side": "BUY",
            "model_token_probability": 0.962749510235,
            "model_p_yes_raw": 0.0,
            "model_p_yes_used": 0.0,
            "best_ask": 0.91,
            "edge": 0.0,
            "created_at_utc": "2026-08-05T00:00:00Z",
        },
        None,
    )

    assert row["model_p_yes"] == 0.962749510235
    assert row["market_price"] == 0.91
    assert row["edge"] == pytest.approx(0.052749510235)
