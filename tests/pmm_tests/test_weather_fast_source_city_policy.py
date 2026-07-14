from scripts.ops.weather_fast_source_city_policy import CITY_POLICIES, configured_city_policies


def test_default_policy_separates_live_trials_from_shadow_cities():
    policies = configured_city_policies()

    assert {city for city, policy in policies.items() if policy.default_mode == "live_trial"} == {
        "Busan",
        "Helsinki",
        "Singapore",
        "Tokyo",
    }
    assert policies["Tokyo"].shares_per_trade == 5.0
    assert policies["Busan"].shares_per_trade == 10.0
    assert policies["Singapore"].source_profile_override_reason


def test_hong_kong_is_not_routed_through_generic_metar_strategy():
    assert not any(policy.city == "HongKong" for policy in CITY_POLICIES.values())


def test_unknown_generic_city_fails_explicitly():
    try:
        configured_city_policies(live_cities=["HongKong"], shadow_cities=[])
    except ValueError as exc:
        assert "unsupported generic fast-source cities" in str(exc)
    else:
        raise AssertionError("HongKong must remain on its dedicated HKO runner")
