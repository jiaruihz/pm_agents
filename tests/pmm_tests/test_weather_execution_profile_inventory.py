from src.strategies.weather_edge_v1.execution import profiles


def test_execution_profile_inventory_is_explicit():
    assert tuple(profiles._PROFILES) == (
        "taker_now_v1",
        "single_side_maker_v1",
        "d1_taker_only_v1",
        "d1_taker_plus_maker_static_v1",
        "d1_taker_plus_maker_chase_to_mid_v1",
        "split_taker_maker_chase_v1",
        "split_taker_maker_chase_capped_no_fallback_v1",
        "split_taker_maker_edge_capped_no_fallback_v2",
        "split_taker_maker_edge_capped_no_fallback_v3",
        "split_taker_maker_event_validated_staged_no_fallback_v4",
        "split_taker_two_maker_event_validated_no_fallback_v5",
        "split_taker_two_maker_event_rearmed_no_fallback_v6",
        "split_taker_shared_maker_staged_to_pullback_v7",
        "near_core_fixed_rest_maker_ws1_v1",
    )
    assert profiles.execution_profile_names() == (
        "taker_now_v1",
        "single_side_maker_v1",
    )
