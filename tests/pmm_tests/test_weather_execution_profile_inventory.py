from src.strategies.weather_edge_v1.execution import profiles


def test_execution_profile_inventory_is_explicit():
    assert tuple(profiles._PROFILES) == (
        "taker_now_v1",
        "single_side_maker_v1",
        "d1_taker_only_v1",
        "d1_taker_plus_maker_static_v1",
        "d1_taker_plus_maker_chase_to_mid_v1",
        "split_taker_maker_chase_v1",
    )
    assert profiles.execution_profile_names() == (
        "taker_now_v1",
        "single_side_maker_v1",
    )
