from scripts.ops.weather_tokyo_metar_stop_reentry_state import (
    TokyoStopReentryPolicy,
    TokyoStopReentryState,
    evaluate_nonconfirming_metar,
    evaluate_reentry,
)


def state(shares: float = 15.0) -> TokyoStopReentryState:
    return TokyoStopReentryState(
        target_date="2026-07-26",
        bracket=32,
        entry_source_obs_ts_utc="2026-07-26T03:10:00+00:00",
        open_shares=shares,
        gross_bought_shares=shares,
    )


def test_nonconfirming_metar_alone_does_not_stop():
    current = state()
    action = evaluate_nonconfirming_metar(
        current,
        TokyoStopReentryPolicy(),
        metar_report_ts_utc="2026-07-26T03:30:00+00:00",
        metar_running_max_bracket=32,
        jma_peak_since_entry_c=33.2,
        jma_latest_c=32.6,
        latest_source_obs_ts_utc="2026-07-26T03:20:00+00:00",
        best_bid=0.41,
        bid_size=20,
    )
    assert action.kind == "hold"
    assert current.open_shares == 15


def test_large_reversal_sells_only_available_depth():
    current = state()
    action = evaluate_nonconfirming_metar(
        current,
        TokyoStopReentryPolicy(),
        metar_report_ts_utc="2026-07-26T03:30:00+00:00",
        metar_running_max_bracket=32,
        jma_peak_since_entry_c=33.2,
        jma_latest_c=32.1,
        latest_source_obs_ts_utc="2026-07-26T03:20:00+00:00",
        best_bid=0.41,
        bid_size=8,
    )
    assert action.kind == "shadow_sell"
    assert action.shares == 8
    assert current.open_shares == 7
    assert current.stopped_shares_available_to_rebuy == 8


def test_official_confirmation_never_stops():
    current = state()
    action = evaluate_nonconfirming_metar(
        current,
        TokyoStopReentryPolicy(),
        metar_report_ts_utc="2026-07-26T03:30:00+00:00",
        metar_running_max_bracket=33,
        jma_peak_since_entry_c=33.2,
        jma_latest_c=32.1,
        latest_source_obs_ts_utc="2026-07-26T03:20:00+00:00",
        best_bid=0.41,
        bid_size=20,
    )
    assert action.kind == "official_confirmed_hold"
    assert current.official_confirmed is True
    assert current.open_shares == 15


def test_reentry_requires_distinct_post_stop_signal():
    current = state()
    evaluate_nonconfirming_metar(
        current,
        TokyoStopReentryPolicy(),
        metar_report_ts_utc="2026-07-26T03:30:00+00:00",
        metar_running_max_bracket=32,
        jma_peak_since_entry_c=33.2,
        jma_latest_c=32.1,
        latest_source_obs_ts_utc="2026-07-26T03:20:00+00:00",
        best_bid=0.41,
        bid_size=15,
    )
    same_observation = evaluate_reentry(
        current,
        TokyoStopReentryPolicy(),
        source_obs_ts_utc="2026-07-26T03:20:00+00:00",
        source_temp_c=33.2,
        best_ask=0.70,
        ask_size=15,
    )
    assert same_observation.kind == "hold"
    assert current.open_shares == 0


def test_reentry_restores_only_sold_shares_and_uses_net_exposure_cap():
    current = state()
    evaluate_nonconfirming_metar(
        current,
        TokyoStopReentryPolicy(),
        metar_report_ts_utc="2026-07-26T03:30:00+00:00",
        metar_running_max_bracket=32,
        jma_peak_since_entry_c=33.2,
        jma_latest_c=32.1,
        latest_source_obs_ts_utc="2026-07-26T03:20:00+00:00",
        best_bid=0.41,
        bid_size=8,
    )
    action = evaluate_reentry(
        current,
        TokyoStopReentryPolicy(),
        source_obs_ts_utc="2026-07-26T03:40:00+00:00",
        source_temp_c=32.8,
        best_ask=0.70,
        ask_size=20,
    )
    assert action.kind == "shadow_rebuy"
    assert action.shares == 8
    assert current.open_shares == 15
    assert current.gross_bought_shares == 23
    assert current.stopped_shares_available_to_rebuy == 0


def test_reentry_above_price_cap_is_not_executable():
    current = state(5)
    evaluate_nonconfirming_metar(
        current,
        TokyoStopReentryPolicy(),
        metar_report_ts_utc="2026-07-26T03:30:00+00:00",
        metar_running_max_bracket=32,
        jma_peak_since_entry_c=33.2,
        jma_latest_c=32.1,
        latest_source_obs_ts_utc="2026-07-26T03:20:00+00:00",
        best_bid=0.41,
        bid_size=5,
    )
    action = evaluate_reentry(
        current,
        TokyoStopReentryPolicy(),
        source_obs_ts_utc="2026-07-26T03:40:00+00:00",
        source_temp_c=32.8,
        best_ask=0.98,
        ask_size=5,
    )
    assert action.kind == "shadow_rebuy_unexecutable"
    assert current.open_shares == 0


def test_stop_cycle_cap_prevents_repeat_churn_after_reentry():
    current = state(5)
    policy = TokyoStopReentryPolicy(max_stop_cycles=1)
    evaluate_nonconfirming_metar(
        current,
        policy,
        metar_report_ts_utc="2026-07-26T03:30:00+00:00",
        metar_running_max_bracket=32,
        jma_peak_since_entry_c=33.2,
        jma_latest_c=32.1,
        latest_source_obs_ts_utc="2026-07-26T03:20:00+00:00",
        best_bid=0.41,
        bid_size=5,
    )
    evaluate_reentry(
        current,
        policy,
        source_obs_ts_utc="2026-07-26T03:40:00+00:00",
        source_temp_c=32.8,
        best_ask=0.70,
        ask_size=5,
    )
    second = evaluate_nonconfirming_metar(
        current,
        policy,
        metar_report_ts_utc="2026-07-26T04:00:00+00:00",
        metar_running_max_bracket=32,
        jma_peak_since_entry_c=33.2,
        jma_latest_c=32.0,
        latest_source_obs_ts_utc="2026-07-26T03:50:00+00:00",
        best_bid=0.40,
        bid_size=5,
    )
    assert second.kind == "hold"
    assert second.reason == "stop_cycle_cap_reached"
