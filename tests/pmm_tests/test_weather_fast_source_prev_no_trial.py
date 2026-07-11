from scripts.ops.weather_fast_source_prev_no_trial import source_cross_confirmation


def evaluate(temp: float, obs_ts: str, state: dict):
    return source_cross_confirmation(
        city="Busan",
        source="amos_runway",
        target_date="2026-07-11",
        source_temp_c=temp,
        source_obs_ts_utc=obs_ts,
        metar_running_max_c=34,
        state=state,
    )


def test_busans_exact_half_degree_print_is_not_a_confirmed_cross():
    result = evaluate(34.5, "2026-07-11T04:46:00+00:00", {})

    assert result["confirmed"] is False
    assert result["blocker"] == "source_cross_margin_not_met"
    assert result["qualifying_distinct_observations"] == 0


def test_busans_first_above_half_print_waits_for_a_distinct_observation():
    state = {}
    first = evaluate(34.6, "2026-07-11T04:46:00+00:00", state)
    repeated_poll = evaluate(34.6, "2026-07-11T04:46:00+00:00", state)

    assert first["blocker"] == "source_cross_persistence_not_met"
    assert repeated_poll["qualifying_distinct_observations"] == 1
    assert repeated_poll["confirmed"] is False


def test_busans_two_above_half_prints_with_one_above_seven_confirm_cross():
    state = {}
    evaluate(34.6, "2026-07-11T04:46:00+00:00", state)
    result = evaluate(34.8, "2026-07-11T04:47:00+00:00", state)

    assert result["qualifying_distinct_observations"] == 2
    assert result["confirmed"] is True
    assert result["strong_observation_seen"] is True
    assert result["blocker"] == ""


def test_busans_two_above_half_prints_without_one_above_seven_do_not_confirm():
    state = {}
    evaluate(34.6, "2026-07-11T04:46:00+00:00", state)
    result = evaluate(34.7, "2026-07-11T04:47:00+00:00", state)

    assert result["qualifying_distinct_observations"] == 2
    assert result["strong_observation_seen"] is False
    assert result["confirmed"] is False


def test_busans_nonqualifying_print_resets_persistence():
    state = {}
    evaluate(34.8, "2026-07-11T04:46:00+00:00", state)
    evaluate(34.5, "2026-07-11T04:47:00+00:00", state)
    result = evaluate(34.8, "2026-07-11T04:48:00+00:00", state)

    assert result["qualifying_distinct_observations"] == 1
    assert result["confirmed"] is False


def test_other_sources_keep_existing_arithmetic_round_policy():
    result = source_cross_confirmation(
        city="Helsinki",
        source="fmi",
        target_date="2026-07-11",
        source_temp_c=21.5,
        source_obs_ts_utc="2026-07-11T15:00:00+00:00",
        metar_running_max_c=21,
        state={},
    )

    assert result["policy"] == "arithmetic_round_v1"
    assert result["confirmed"] is True
