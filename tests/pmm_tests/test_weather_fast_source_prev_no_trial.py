from scripts.ops.weather_fast_source_prev_no_trial import source_cross_confirmation


def evaluate(temp: float, obs_ts: str, state: dict, *, city="Busan", source="amos_runway"):
    return source_cross_confirmation(
        city=city,
        source=source,
        target_date="2026-07-11",
        source_temp_c=temp,
        source_obs_ts_utc=obs_ts,
        metar_running_max_c=34,
        state=state,
    )


def test_persistent_sources_accept_exactly_half_degree_as_first_print():
    result = evaluate(34.5, "2026-07-11T04:46:00+00:00", {})

    assert result["confirmed"] is False
    assert result["blocker"] == "source_cross_persistence_not_met"
    assert result["qualifying_distinct_observations"] == 1


def test_persistent_sources_wait_for_a_second_distinct_observation():
    state = {}
    first = evaluate(34.8, "2026-07-11T04:46:00+00:00", state)
    repeated_poll = evaluate(34.8, "2026-07-11T04:46:00+00:00", state)

    assert first["blocker"] == "source_cross_persistence_not_met"
    assert repeated_poll["qualifying_distinct_observations"] == 1
    assert repeated_poll["confirmed"] is False


def test_persistent_policy_does_not_reuse_legacy_persistence_state():
    state = {
        "Busan|2026-07-11|amos_runway|34": {
            "last_source_obs_ts_utc": "2026-07-11T04:45:00+00:00",
            "last_observation_qualified": True,
            "qualifying_distinct_observations": 1,
        }
    }

    result = evaluate(34.8, "2026-07-11T04:46:00+00:00", state)

    assert result["qualifying_distinct_observations"] == 1
    assert result["confirmed"] is False


def test_persistent_sources_confirm_when_latest_print_reaches_seven_tenths():
    state = {}
    evaluate(34.5, "2026-07-11T04:46:00+00:00", state)
    result = evaluate(34.7, "2026-07-11T04:47:00+00:00", state)

    assert result["qualifying_distinct_observations"] == 2
    assert result["confirmed"] is True
    assert result["policy"] == "two_above_half_latest_above_seven_v2"
    assert result["blocker"] == ""


def test_latest_print_below_seven_tenths_does_not_confirm():
    state = {}
    evaluate(34.8, "2026-07-11T04:46:00+00:00", state)
    result = evaluate(34.6, "2026-07-11T04:47:00+00:00", state)

    assert result["qualifying_distinct_observations"] == 2
    assert result["confirmed"] is False
    assert result["blocker"] == "latest_source_cross_strength_not_met"


def test_busans_nonqualifying_print_resets_persistence():
    state = {}
    evaluate(34.8, "2026-07-11T04:46:00+00:00", state)
    evaluate(34.4, "2026-07-11T04:47:00+00:00", state)
    result = evaluate(34.8, "2026-07-11T04:48:00+00:00", state)

    assert result["qualifying_distinct_observations"] == 1
    assert result["confirmed"] is False


def test_persistent_city_states_do_not_clear_each_other():
    state = {}
    evaluate(34.8, "2026-07-11T04:46:00+00:00", state)
    evaluate(34.8, "2026-07-11T04:46:00+00:00", state, city="Helsinki", source="fmi")
    busan = evaluate(34.8, "2026-07-11T04:47:00+00:00", state)
    helsinki = evaluate(34.8, "2026-07-11T04:47:00+00:00", state, city="Helsinki", source="fmi")

    assert busan["confirmed"] is True
    assert helsinki["confirmed"] is True


def test_singapore_uses_persistent_confirmation_policy():
    result = evaluate(
        34.8,
        "2026-07-11T04:46:00+00:00",
        {},
        city="Singapore",
        source="singapore_mss",
    )

    assert result["policy"] == "two_above_half_latest_above_seven_v2"
    assert result["confirmed"] is False


def test_other_sources_keep_existing_arithmetic_round_policy():
    result = source_cross_confirmation(
        city="Tokyo",
        source="jma_amedas",
        target_date="2026-07-11",
        source_temp_c=21.5,
        source_obs_ts_utc="2026-07-11T15:00:00+00:00",
        metar_running_max_c=21,
        state={},
    )

    assert result["policy"] == "arithmetic_round_v1"
    assert result["confirmed"] is True
