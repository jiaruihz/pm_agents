import json
from datetime import datetime, timezone

from scripts.ops.weather_metar_cross_prev_no_shadow import (
    build_city_policy,
    city_base_delay_sec,
    cycle_once,
    circular_minute_distance,
    crossed_prev_no_brackets,
    in_learned_update_window,
    in_city_update_window,
    in_update_window,
    load_source_by_city,
    load_city_configs,
    noaa_tgftp_station_txt_latest,
    observation_summary_from_source_event,
    parsed_label_is_dead_for_running_value,
    parsed_label_matches_no_target,
    parse_report_minutes,
    plan_buy_amount,
    source_for_city,
    update_report_minute_state,
)


def test_crossed_prev_no_brackets_maps_threshold_to_previous_bucket():
    assert crossed_prev_no_brackets(None, 21) == []
    assert crossed_prev_no_brackets(20, 20) == []
    assert crossed_prev_no_brackets(20, 21) == [20]
    assert crossed_prev_no_brackets(19, 21) == [19, 20]


def test_parsed_label_matches_no_target_skips_top_tail():
    assert parsed_label_matches_no_target({"low": 20.0, "high": 20.0, "bottom": False, "top": False}, 20)
    assert parsed_label_matches_no_target({"low": None, "high": 20.0, "bottom": True, "top": False}, 20)
    assert not parsed_label_matches_no_target({"low": 35.0, "high": None, "bottom": False, "top": True}, 35)
    assert not parsed_label_matches_no_target(None, 20)


def test_dead_bracket_guard_rejects_ambiguous_range_until_upper_is_crossed():
    assert not parsed_label_is_dead_for_running_value({"low": 68.0, "high": 69.0, "bottom": False, "top": False}, 68, 69)
    assert parsed_label_is_dead_for_running_value({"low": 68.0, "high": 69.0, "bottom": False, "top": False}, 69, 70)
    assert parsed_label_is_dead_for_running_value({"low": None, "high": 24.0, "bottom": True, "top": False}, 24, 25)
    assert not parsed_label_is_dead_for_running_value({"low": 35.0, "high": None, "bottom": False, "top": True}, 35, 36)


def test_city_policy_allows_same_station_and_blocks_seoul():
    configs = load_city_configs(include_station_diff=False, only_cities={"Shanghai", "Tokyo", "Seoul"})
    assert [cfg.city for cfg in configs] == ["Shanghai", "Tokyo"]

    policy = build_city_policy(include_station_diff=False, only_cities={"Seoul"})
    assert policy["allowed"] == []
    assert policy["rejected"][0]["city"] == "Seoul"
    assert "blocked_unresolved_settlement_basis" in policy["rejected"][0]["reason"]


def test_city_policy_keeps_station_diff_explicit():
    without_station_diff = load_city_configs(include_station_diff=False, only_cities={"Paris"})
    with_station_diff = load_city_configs(include_station_diff=True, only_cities={"Paris"})

    assert without_station_diff == []
    assert len(with_station_diff) == 1
    assert with_station_diff[0].city == "Paris"
    assert with_station_diff[0].official_icao == "LFPB"
    assert with_station_diff[0].registry_class == "official_station_diff_aligned"


def test_crossing_burst_window_targets_report_boundaries():
    assert in_update_window(datetime(2026, 6, 17, 4, 29, 58, tzinfo=timezone.utc), window_min=2)
    assert in_update_window(datetime(2026, 6, 17, 4, 0, 2, tzinfo=timezone.utc), window_min=2)
    assert not in_update_window(datetime(2026, 6, 17, 4, 12, 0, tzinfo=timezone.utc), window_min=2)


def test_learned_burst_window_uses_observed_report_minute():
    state = {}
    update_report_minute_state(state, "Busan", "2026-06-24T23:00:00+00:00")
    assert in_learned_update_window(datetime(2026, 6, 25, 1, 4, 30, tzinfo=timezone.utc), state, window_min=5)
    assert not in_learned_update_window(datetime(2026, 6, 25, 1, 12, 0, tzinfo=timezone.utc), state, window_min=5)
    assert circular_minute_distance(58, 2) == 4


def test_source_by_city_overrides_default_source():
    cfg = load_city_configs(include_station_diff=True, only_cities={"Busan"})[0]
    source_by_city = load_source_by_city(json_text='{"Busan":"synopticdata_timeseries"}')
    assert source_for_city(cfg, "noaa_tgftp_station_txt", source_by_city) == "synopticdata_timeseries"
    assert source_for_city(cfg, "noaa_tgftp_station_txt", {}) == "noaa_tgftp_station_txt"


def test_city_update_window_uses_learned_minute_with_pre_and_chase():
    state = {}
    update_report_minute_state(state, "Busan", "2026-06-24T23:00:00+00:00")
    fallback = parse_report_minutes("30,53")

    assert in_city_update_window(
        datetime(2026, 6, 25, 1, 59, 30, tzinfo=timezone.utc),
        state,
        "Busan",
        fallback_report_minutes=fallback,
        pre_window_min=1,
        chase_window_min=10,
    )
    assert in_city_update_window(
        datetime(2026, 6, 25, 2, 4, 30, tzinfo=timezone.utc),
        state,
        "Busan",
        fallback_report_minutes=fallback,
        pre_window_min=1,
        chase_window_min=10,
    )
    assert not in_city_update_window(
        datetime(2026, 6, 25, 2, 12, 0, tzinfo=timezone.utc),
        state,
        "Busan",
        fallback_report_minutes=fallback,
        pre_window_min=1,
        chase_window_min=10,
    )


def test_city_update_window_falls_back_before_learning():
    assert in_city_update_window(
        datetime(2026, 6, 25, 1, 52, 30, tzinfo=timezone.utc),
        {},
        "Unknown",
        fallback_report_minutes=parse_report_minutes("0,30,53"),
        pre_window_min=1,
        chase_window_min=10,
    )


def test_city_base_delay_is_stable_and_bounded():
    assert city_base_delay_sec("Busan", 45) == city_base_delay_sec("Busan", 45)
    assert 22.5 <= city_base_delay_sec("Busan", 45) <= 67.5


def test_plan_buy_amount_uses_cent_notional_and_five_decimal_size():
    size, notional = plan_buy_amount(0.988, 500.0, 3.0)
    assert notional == 3.0
    assert size == 3.03643

    size, notional = plan_buy_amount(0.847, 2.0, 3.0)
    assert notional == 1.69
    assert size == 1.99527


def test_noaa_tgftp_station_txt_latest_parses_station_text(monkeypatch):
    from scripts.ops import weather_metar_cross_prev_no_shadow as mod

    def fake_fetch_text(*args, **kwargs):
        return "2026/06/24 17:53\nKORD 241751Z 24008KT 10SM FEW050 29/18 A2992 RMK AO2 SLP130 T02940178\n"

    monkeypatch.setattr(mod.source, "fetch_text", fake_fetch_text)
    row = noaa_tgftp_station_txt_latest("KORD", previous_value=83)
    assert row["status"] == "ok"
    assert row["source"] == "noaa_tgftp_station_txt"
    assert row["last_obs_utc"] == "2026-06-24T17:51:00+00:00"
    assert row["current_temp_c"] == 29.0
    assert row["running_max_c"] == 29.0


def test_source_event_observation_summary_maps_latest_row(tmp_path):
    cfg = load_city_configs(include_station_diff=False, only_cities={"Shanghai"})[0]
    source_events = {
        ("Shanghai", "aviationweather_metar"): {
            "city": "Shanghai",
            "target_date": "2026-06-17",
            "source": "aviationweather_metar",
            "station": "ZSPD",
            "status": "ok",
            "temp_c": 27.0,
            "payload_hash": "hash-primary",
            "source_report_ts_utc": "2026-06-17T10:00:00+00:00",
            "raw_metar": "METAR ZSPD 171000Z 00000KT 9999 27/20 Q1008",
        }
    }

    row = observation_summary_from_source_event(
        cfg,
        timezone.utc,
        datetime(2026, 6, 17).date(),
        obs_source="aviationweather_metar",
        source_events=source_events,
        source_events_path=tmp_path / "latest.json",
        now_utc=datetime(2026, 6, 17, 10, 0, 5, tzinfo=timezone.utc),
    )

    assert row["status"] == "ok"
    assert row["source_input"] == "data_feed_source_events"
    assert row["current_temp_c"] == 27.0
    assert row["running_max_c"] == 27.0
    assert row["last_obs_utc"] == "2026-06-17T10:00:00+00:00"


def test_cycle_once_uses_source_events_for_crossing_signal(monkeypatch, tmp_path):
    from scripts.ops import weather_metar_cross_prev_no_shadow as mod

    cfg = load_city_configs(include_station_diff=False, only_cities={"Shanghai"})[0]
    source_events_path = tmp_path / "latest.json"
    source_events_path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "city": "Shanghai",
                        "target_date": "2026-06-30",
                        "source": "aviationweather_metar",
                        "station": "ZSPD",
                        "status": "ok",
                        "temp_c": 25.0,
                        "payload_hash": "hash-primary",
                        "source_report_ts_utc": "2026-06-29T18:30:00+00:00",
                    }
                ]
            }
        )
    )
    out_dir = tmp_path / "metar_cross"
    monkeypatch.setattr(mod, "OUT_DIR", out_dir)

    def fail_live_fetch(*_args, **_kwargs):
        raise AssertionError("cycle_once should not live-fetch weather observations")

    monkeypatch.setattr(mod, "observation_summary", fail_live_fetch)
    monkeypatch.setattr(mod, "datetime", type("FixedDateTime", (), {
        "now": staticmethod(lambda tz=None: datetime(2026, 6, 29, 19, 0, tzinfo=timezone.utc)),
        "fromisoformat": staticmethod(datetime.fromisoformat),
    }))

    counts = cycle_once(
        [cfg],
        max_ask=0.995,
        dry_run=True,
        recent_hours=2,
        obs_source="aviationweather_metar",
        source_by_city=None,
        signal_input="source-events",
        source_events_path=source_events_path,
        live=False,
        confirm_live=False,
        max_notional_per_trade=10,
        max_notional_per_city_day=10,
        max_notional_total_day=50,
        max_workers=1,
    )

    assert counts["cities"] == 1
    cycle_rows = [json.loads(line) for line in (out_dir / "cycles.jsonl").read_text().splitlines()]
    assert cycle_rows[0]["signal_input"] == "source-events"
    assert cycle_rows[0]["obs_source_input"] == "data_feed_source_events"
    assert cycle_rows[0]["obs_payload_hash"] == "hash-primary"
