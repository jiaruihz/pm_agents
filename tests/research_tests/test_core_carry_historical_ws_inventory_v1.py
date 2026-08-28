from scripts.analysis.execution_quality.core_carry_historical_ws_inventory_v1 import (
    build_inventory,
    classify,
    score_identity,
)


def score(**values):
    return {
        "checkpoint_key": "Paris|2026-08-26|17",
        "created_at_utc": "2026-08-26T15:37:51Z",
        "current_yes_token_id": "current",
        "model_probability_hold": 0.9,
        "artifact_hash": "artifact",
        "target_date": "2026-08-26",
        "city": "Paris",
        "current_bracket": "25",
        "full_ladder_yes_tokens": [
            {"bracket": "25", "token_id": "current"},
            {"bracket": "26", "token_id": "upper"},
        ],
        "reasons": ["non_positive_taker_ev"],
        **values,
    }


def decision(**values):
    return {
        "event_id": "candidate-ladder|Paris",
        "event_kind": "candidate",
        "event_at_utc": "2026-08-26T15:37:51+00:00",
        "checkpoint_key": "Paris|2026-08-26|17",
        "feature_book_snapshot_id": "snapshot",
        "q_up": 0.1,
        "reported_buy_volume": 0.0,
        "reported_sell_volume": 0.0,
        "reconstruction_complete": True,
        "reconstruction_run_id": "run-1",
        "reconstruction_errors": [],
        **values,
    }


def test_score_identity_requires_exact_frozen_fields():
    assert score_identity(score())
    assert score_identity(score(artifact_hash=None)) is None


def test_classification_keeps_upper_rung_as_separate_readiness_axis():
    base = {
        "exact_frozen_score_identity_available": True,
        "settlement_available": True,
        "current_token_raw_incremental_ws_available": True,
        "public_trade_tape_available": True,
        "sequence_reconnect_continuity_available": True,
        "private_order_lifecycle_available": False,
        "upper_rung_raw_incremental_ws_available": False,
    }
    assert classify(base) == "SILVER"
    assert classify({**base, "private_order_lifecycle_available": True}) == "GOLD"


def test_inventory_preserves_near_core_and_existing_maker_denominators():
    near = score()
    existing = score(
        checkpoint_key="Paris|2026-08-26|18",
        created_at_utc="2026-08-26T16:37:51Z",
        signal_id="signal-1",
        reasons=[],
        decision_status="positive_taker_ev",
    )
    rows = build_inventory(
        scores=[near, existing],
        capture_demands=[],
        pretrigger_demands=[],
        decisions=[
            decision(),
            decision(
                event_id="first-positive|Paris",
                event_kind="first_positive",
                event_at_utc="2026-08-26T16:37:51+00:00",
                checkpoint_key="Paris|2026-08-26|18",
                signal_id="signal-1",
            ),
        ],
        live_orders=[
            {
                "signal_id": "signal-1",
                "child_order_role": "maker_staged",
                "token_id": "current",
                "city": "Paris",
                "target_date": "2026-08-26",
                "created_at_utc": "2026-08-26T16:38:01Z",
                "model_token_probability": 0.9,
                "exchange_response": {"place": {"orderID": "venue-1"}},
            }
        ],
        settlements={
            "current": {"final_price": 1.0, "settlement_status": "settled"}
        },
    )
    assert len(rows) == 2
    by_sleeve = {row["source_sleeve"]: row for row in rows}
    assert by_sleeve["CORE_CARRY_NEAR_CORE_MAKER_PROBE_5S"]["classification"] == "SILVER"
    assert by_sleeve["CORE_CARRY_EXISTING_MAKER"]["classification"] == "GOLD"
    assert by_sleeve["CORE_CARRY_EXISTING_MAKER"]["private_venue_order_ids"] == [
        "venue-1"
    ]
