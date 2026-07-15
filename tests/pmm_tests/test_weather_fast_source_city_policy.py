from scripts.ops.weather_fast_source_city_policy import CITY_POLICIES, configured_city_policies


def test_default_policy_separates_live_trials_from_shadow_cities():
    policies = configured_city_policies()

    assert {city for city, policy in policies.items() if policy.default_mode == "live_trial"} == {
        "Atlanta",
        "Busan",
        "Helsinki",
        "Miami",
        "SanFrancisco",
        "Singapore",
        "Tokyo",
    }
    assert policies["Tokyo"].shares_per_trade == 10.0
    assert policies["Tokyo"].max_shares_per_market == 10.0
    assert policies["Busan"].shares_per_trade == 10.0
    assert policies["Singapore"].source_profile_override_reason
    for city in ("Atlanta", "Miami", "SanFrancisco"):
        policy = policies[city]
        assert policy.signal_handler == "metar_prev_no_range_2f"
        assert policy.shares_per_trade == 5.0
        assert policy.max_shares_per_market == 5.0
        assert policy.max_source_age_min == 30.0
        assert policy.max_source_observation_lag_min == 30.0


def test_hong_kong_is_not_routed_through_generic_metar_strategy():
    assert not any(policy.city == "HongKong" for policy in CITY_POLICIES.values())


def test_unknown_generic_city_fails_explicitly():
    try:
        configured_city_policies(live_cities=["HongKong"], shadow_cities=[])
    except ValueError as exc:
        assert "unsupported generic fast-source cities" in str(exc)
    else:
        raise AssertionError("HongKong must remain on its dedicated HKO runner")
