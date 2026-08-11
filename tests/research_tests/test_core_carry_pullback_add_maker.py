from scripts.analysis.reheat_risk.core_carry_pullback_add_maker import (
    replay_one,
    replay_post_update_rearm,
    source_clock_blackout,
)
import pytest


ENTRY = {
    "city": "Amsterdam",
    "target_date": "2026-08-10",
    "current_bracket": "23",
    "current_condition_id": "condition",
    "current_yes_token_id": "token",
    "created_at_utc": "2026-08-10T11:31:58Z",
    "source_report_ts_utc": "2026-08-10T10:55:00Z",
    "observation_cadence_min": 30.0,
    "current_yes_ask": 0.87,
    "taker_ladder": {"effective_cost_per_share": 0.87566},
}


def test_touch_without_five_share_depth_is_not_a_fill() -> None:
    rows = [
        {
            "available_at_utc": "2026-08-10T11:40:00Z",
            "bids": [{"price": 0.78, "size": 20}],
            "asks": [{"price": 0.79, "size": 4.9}],
        }
    ]
    replay = replay_one(
        ENTRY,
        rows,
        1.0,
        discount=0.07,
        ttl_min=15,
        taker_quantity=10,
        maker_quantity=5,
    )

    assert replay["touch_observed"] is True
    assert replay["conservative_fill"] is False
    assert replay["incremental_maker_pnl_usd"] == 0.0


def test_full_depth_pullback_then_recovery_adds_winner_profit() -> None:
    rows = [
        {
            "available_at_utc": "2026-08-10T11:46:48Z",
            "bids": [{"price": 0.74, "size": 20}],
            "asks": [{"price": 0.79, "size": 8}],
        },
        {
            "available_at_utc": "2026-08-10T12:34:00Z",
            "bids": [{"price": 0.91, "size": 20}],
            "asks": [{"price": 0.94, "size": 20}],
        },
    ]
    replay = replay_one(
        ENTRY,
        rows,
        1.0,
        discount=0.07,
        ttl_min=15,
        taker_quantity=10,
        maker_quantity=5,
    )

    assert replay["maker_limit"] == 0.80
    assert replay["conservative_fill"] is True
    assert replay["market_recovered_to_entry_ask"] is True
    assert replay["structure"] == "qualifying_pullback_recovered"
    assert replay["incremental_maker_pnl_usd"] == pytest.approx(1.0)


def test_entry_after_source_cancel_deadline_is_clock_blackout() -> None:
    assert source_clock_blackout(ENTRY) is True


def test_post_update_rearm_reuses_same_five_share_sleeve() -> None:
    update = {
        "city": "Amsterdam",
        "target_date": "2026-08-10",
        "current_bracket": "23",
        "current_yes_token_id": "token",
        "decision_snapshot_ts_utc": "2026-08-10T11:26:00Z",
        "source_report_ts_utc": "2026-08-10T11:25:00Z",
        "observation_cadence_min": 30.0,
        "current_yes_bid": 0.78,
        "current_yes_ask": 0.80,
        "current_yes_tick_size": 0.01,
        "model_probability_hold": 0.88,
        "model_input_support_status": "within_training_support",
        "checkpoint_eligible": True,
    }
    replay = replay_post_update_rearm(
        ENTRY,
        update,
        {
            "token": [
                {
                    "available_at_utc": "2026-08-10T11:36:00Z",
                    "bids": [{"price": 0.77, "size": 20}],
                    "asks": [{"price": 0.79, "size": 5}],
                }
            ]
        },
        1.0,
        maker_quantity=5,
        ttl_min=15,
    )

    assert replay["maker_rearm_eligible"] is True
    assert replay["maker_limit"] == pytest.approx(0.79)
    assert replay["conservative_fill"] is True
    assert replay["incremental_maker_cost_usd"] == pytest.approx(3.95)
    assert replay["incremental_maker_pnl_usd"] == pytest.approx(1.05)


def test_post_update_rearm_blocks_rebracketed_token() -> None:
    replay = replay_post_update_rearm(
        ENTRY,
        {
            "current_bracket": "24",
            "current_yes_token_id": "new-token",
            "decision_snapshot_ts_utc": "2026-08-10T11:26:00Z",
            "source_report_ts_utc": "2026-08-10T11:25:00Z",
            "observation_cadence_min": 30.0,
            "current_yes_bid": 0.78,
            "current_yes_ask": 0.80,
            "current_yes_tick_size": 0.01,
            "model_probability_hold": 0.88,
            "model_input_support_status": "within_training_support",
            "checkpoint_eligible": True,
        },
        {},
        0.0,
        maker_quantity=5,
        ttl_min=15,
    )

    assert replay["maker_rearm_eligible"] is False
    assert replay["conservative_fill"] is False
    assert "exact_bracket_token_changed" in replay["rearm_blockers"]
