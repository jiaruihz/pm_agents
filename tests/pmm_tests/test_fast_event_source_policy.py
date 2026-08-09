from __future__ import annotations

import json
from datetime import datetime, timezone

import scripts.ops.weather_fast_source_stale_book_observer as observer
from scripts.ops.weather_fast_source_stale_book_observer import (
    MarketToken,
    active_source_window_health,
    build_active_bracket_book_rows,
    bracket_from_question,
    bracket_lookup,
    build_market_index,
    market_date_has_tokens,
    relative_market_token,
    source_market_episode_key,
    source_latest_by_city,
    source_running_max_by_city,
    target_date_for_city,
    temperature_event_slug,
)
from weather_data_feed.fast_event_source_policy import (
    load_fast_event_source_profiles,
    market_value_from_temp_c,
)


def test_fast_event_profiles_are_calibration_only_and_unit_aware():
    profiles = load_fast_event_source_profiles()

    assert len(profiles) >= 22
    assert len({profile.city for profile in profiles.values()}) >= 21
    assert not any(profile.live_eligible for profile in profiles.values())
    assert profiles[("Atlanta", "noaa_madis_hfmetar")].market_unit == "F"
    assert profiles[("HongKong", "hko_obs")].bracket_rounding == "floor"
    assert profiles[("Shenzhen", "hko_obs")].source_basis_class == "cross_station_proxy"


def test_market_value_conversion_uses_profile_unit_and_rounding():
    profiles = load_fast_event_source_profiles()

    assert market_value_from_temp_c(30.0, profiles[("Atlanta", "noaa_madis_hfmetar")]) == 86
    assert market_value_from_temp_c(30.9, profiles[("HongKong", "hko_obs")]) == 30
    assert market_value_from_temp_c(30.5, profiles[("Tokyo", "jma_amedas")]) == 31


def test_target_date_routes_per_city_local_calendar():
    now = datetime(2026, 7, 13, 16, 49, tzinfo=timezone.utc)

    assert target_date_for_city("Atlanta", now) == "2026-07-13"
    assert target_date_for_city("Tokyo", now) == "2026-07-14"
    assert target_date_for_city("Atlanta", now, "2026-07-20") == "2026-07-20"


def test_active_source_window_health_distinguishes_idle_from_outage(tmp_path):
    latest = tmp_path / "latest.json"
    latest.write_text(
        json.dumps({"active_job_cities": ["Atlanta"]}),
        encoding="utf-8",
    )

    idle = active_source_window_health({}, {"Helsinki"}, latest)
    outage = active_source_window_health({}, {"Atlanta"}, latest)

    assert idle == {
        "status": "idle_outside_source_window",
        "missing_active_source_cities": [],
        "outside_source_window_cities": ["Helsinki"],
    }
    assert outage == {
        "status": "degraded_missing_active_source",
        "missing_active_source_cities": ["Atlanta"],
        "outside_source_window_cities": [],
    }


def test_source_latest_keeps_simultaneous_local_dates_and_market_units(tmp_path):
    profiles = load_fast_event_source_profiles()
    now = datetime(2026, 7, 13, 16, 49, tzinfo=timezone.utc)
    path = tmp_path / "latest.json"
    path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "city": "Atlanta",
                        "target_date": "2026-07-13",
                        "source": "noaa_madis_hfmetar",
                        "temp_c": 30.0,
                        "observation_time_utc": "2026-07-13T16:45:00Z",
                        "local_detect_ts_utc": "2026-07-13T16:46:00Z",
                    },
                    {
                        "city": "Tokyo",
                        "target_date": "2026-07-14",
                        "source": "jma_amedas",
                        "temp_c": 30.5,
                        "observation_time_utc": "2026-07-13T16:40:00Z",
                        "local_detect_ts_utc": "2026-07-13T16:41:00Z",
                    },
                    {
                        "city": "Atlanta",
                        "target_date": "2026-07-14",
                        "source": "noaa_madis_hfmetar",
                        "temp_c": 40.0,
                        "observation_time_utc": "2026-07-13T16:45:00Z",
                        "local_detect_ts_utc": "2026-07-13T16:46:00Z",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    rows = source_latest_by_city(
        path,
        "",
        {"noaa_madis_hfmetar", "jma_amedas"},
        profiles,
        now,
    )

    assert set(rows) == {("Atlanta", "2026-07-13"), ("Tokyo", "2026-07-14")}
    assert rows[("Atlanta", "2026-07-13")]["source_market_value"] == 86
    assert rows[("Tokyo", "2026-07-14")]["source_market_value"] == 31


def test_source_latest_prefers_newer_observation_even_when_temperature_falls(tmp_path):
    profiles = load_fast_event_source_profiles()
    now = datetime(2026, 7, 14, 2, 30, tzinfo=timezone.utc)
    path = tmp_path / "latest.json"
    path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "city": "Tokyo",
                        "target_date": "2026-07-14",
                        "source": "jma_amedas",
                        "temp_c": 33.0,
                        "observation_time_utc": "2026-07-14T02:10:00Z",
                        "local_detect_ts_utc": "2026-07-14T02:11:00Z",
                    },
                    {
                        "city": "Tokyo",
                        "target_date": "2026-07-14",
                        "source": "jma_amedas",
                        "temp_c": 32.0,
                        "observation_time_utc": "2026-07-14T02:20:00Z",
                        "local_detect_ts_utc": "2026-07-14T02:21:00Z",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    rows = source_latest_by_city(path, "", {"jma_amedas"}, profiles, now)

    assert rows[("Tokyo", "2026-07-14")]["temp_c"] == 32.0
    assert rows[("Tokyo", "2026-07-14")]["source_obs_ts_utc"] == "2026-07-14T02:20:00+00:00"


def test_source_latest_prefers_configured_runway_for_same_observation_minute(tmp_path):
    profiles = load_fast_event_source_profiles()
    now = datetime(2026, 7, 14, 5, 0, tzinfo=timezone.utc)
    path = tmp_path / "latest.json"
    path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "city": "Seoul",
                        "target_date": "2026-07-14",
                        "source": "amos_runway",
                        "runway": "16L/34R",
                        "temp_c": 29.1,
                        "is_preferred_temperature_runway": False,
                        "observation_time_utc": "2026-07-14T04:58:00Z",
                        "local_detect_ts_utc": "2026-07-14T04:58:34Z",
                    },
                    {
                        "city": "Seoul",
                        "target_date": "2026-07-14",
                        "source": "amos_runway",
                        "runway": "15R/33L",
                        "temp_c": 29.2,
                        "is_preferred_temperature_runway": True,
                        "observation_time_utc": "2026-07-14T04:58:00Z",
                        "local_detect_ts_utc": "2026-07-14T04:58:34Z",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    rows = source_latest_by_city(path, "", {"amos_runway"}, profiles, now)

    selected = rows[("Seoul", "2026-07-14")]
    assert selected["runway"] == "15R/33L"
    assert selected["temp_c"] == 29.2


def test_source_running_extreme_reads_dated_shards_not_root_aggregate(tmp_path):
    profiles = load_fast_event_source_profiles()
    root = tmp_path / "high_frequency_observations"
    shard = root / "2026-07-14" / "high_frequency_observations.jsonl"
    shard.parent.mkdir(parents=True)
    base = {
        "city": "Tokyo",
        "target_date": "2026-07-14",
        "source": "jma_amedas",
        "observation_time_utc": "2026-07-14T04:00:00Z",
        "local_detect_ts_utc": "2026-07-14T04:01:00Z",
    }
    shard.write_text(json.dumps({**base, "temp_c": 30.5}) + "\n", encoding="utf-8")
    (root / "high_frequency_observations.jsonl").write_text(
        json.dumps({**base, "temp_c": 99.0}) + "\n", encoding="utf-8"
    )

    rows = source_running_max_by_city(
        root,
        "2026-07-14",
        {"jma_amedas"},
        {"Tokyo"},
        profiles,
        datetime(2026, 7, 14, 5, 0, tzinfo=timezone.utc),
    )

    assert rows[("Tokyo", "2026-07-14")]["temp_c"] == 30.5


def test_market_index_does_not_collide_across_target_dates(tmp_path):
    path = tmp_path / "paper.json"
    path.write_text(
        json.dumps(
            {
                "records": [
                    {"city": "Atlanta", "target_date": "2026-07-13", "bracket": "86", "yes_token_id": "old"},
                    {"city": "Atlanta", "target_date": "2026-07-14", "bracket": "86", "yes_token_id": "new"},
                ]
            }
        ),
        encoding="utf-8",
    )

    index = build_market_index(path, {"2026-07-13", "2026-07-14"})

    assert index[("Atlanta", "2026-07-13", "86")].yes_token_id == "old"
    assert index[("Atlanta", "2026-07-14", "86")].yes_token_id == "new"


def test_market_index_isolates_highest_and_lowest_temperature_events(tmp_path):
    path = tmp_path / "paper.json"
    path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "city": "Tokyo",
                        "target_date": "2026-07-14",
                        "bracket": "25",
                        "event_slug": "highest-temperature-in-tokyo-on-july-14-2026",
                        "question": "Will the highest temperature in Tokyo be 25C?",
                        "yes_token_id": "highest",
                    },
                    {
                        "city": "Tokyo",
                        "target_date": "2026-07-14",
                        "bracket": "25",
                        "event_slug": "lowest-temperature-in-tokyo-on-july-14-2026",
                        "question": "Will the lowest temperature in Tokyo be 25C?",
                        "yes_token_id": "lowest",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    highest = build_market_index(path, {"2026-07-14"}, "max")
    lowest = build_market_index(path, {"2026-07-14"}, "min")

    assert highest[("Tokyo", "2026-07-14", "25")].yes_token_id == "highest"
    assert lowest[("Tokyo", "2026-07-14", "25")].yes_token_id == "lowest"


def test_market_ladder_uses_real_range_brackets_not_numeric_plus_minus_one(tmp_path):
    path = tmp_path / "paper.json"
    path.write_text(
        json.dumps(
            {
                "records": [
                    {"city": "SanFrancisco", "target_date": "2026-07-13", "bracket": "74-75"},
                    {"city": "SanFrancisco", "target_date": "2026-07-13", "bracket": "76-77"},
                    {"city": "SanFrancisco", "target_date": "2026-07-13", "bracket": "78-79"},
                ]
            }
        ),
        encoding="utf-8",
    )
    index = build_market_index(path, {"2026-07-13"})

    assert bracket_lookup(index, "SanFrancisco", "2026-07-13", 76).bracket == "76-77"
    assert bracket_lookup(index, "SanFrancisco", "2026-07-13", 77).bracket == "76-77"
    assert relative_market_token(index, "SanFrancisco", "2026-07-13", 77, -1).bracket == "74-75"
    assert relative_market_token(index, "SanFrancisco", "2026-07-13", 77, 1).bracket == "78-79"
    assert market_date_has_tokens(index, "SanFrancisco", "2026-07-13")
    assert not market_date_has_tokens(index, "SanFrancisco", "2026-07-14")


def test_latest_paper_snapshot_skips_in_progress_json(tmp_path, monkeypatch):
    older = tmp_path / "snapshot_20260714_1200.json"
    newest = tmp_path / "snapshot_20260714_1230.json"
    older.write_text('{"records": []}', encoding="utf-8")
    newest.write_text('{"records": [', encoding="utf-8")
    monkeypatch.setattr(observer, "PAPER_SNAPSHOT_DIR", tmp_path)

    assert observer.latest_paper_snapshot() == older


def test_event_key_deduplicates_repeated_source_reports_within_same_market_bracket():
    first = source_market_episode_key("SanFrancisco", "2026-07-13", "noaa_madis_hfmetar", "76-77")
    repeated = source_market_episode_key("SanFrancisco", "2026-07-13", "noaa_madis_hfmetar", "76-77")
    next_bracket = source_market_episode_key("SanFrancisco", "2026-07-13", "noaa_madis_hfmetar", "78-79")

    assert first == repeated
    assert first != next_bracket


def test_active_bracket_archive_keeps_previous_no_covered_across_cross(monkeypatch):
    target_date = "2026-07-20"
    market_index = {
        ("Tokyo", target_date, str(bracket)): MarketToken(
            city="Tokyo",
            target_date=target_date,
            bracket=str(bracket),
            question=f"Will Tokyo be {bracket}C?",
            event_slug="event",
            market_id=f"market-{bracket}",
            condition_id=f"condition-{bracket}",
            yes_token_id=f"yes-{bracket}",
            no_token_id=f"no-{bracket}",
        )
        for bracket in (32, 33, 34, 35, 36)
    }
    monkeypatch.setattr(
        observer,
        "fetch_fresh_book",
        lambda token_id, **_kwargs: {
            "status": "ok",
            "fetched_at_utc": "2026-07-20T03:00:00+00:00",
            "summary": {"best_ask": 0.95, "ask_size": 20.0},
            "raw": {"asks": [{"price": 0.95, "size": 20.0}]},
            "token_id": token_id,
        },
    )
    metar_rows = {
        ("Tokyo", target_date): {
            "metar_running_max_market_value": 34,
            "metar_running_max_round_c": 34,
        }
    }
    before = build_active_bracket_book_rows(
        source_rows={("Tokyo", target_date): {"source": "jma_amedas", "source_market_value": 34}},
        metar_rows=metar_rows,
        market_index=market_index,
        market_proxy="",
        active_cities={"Tokyo"},
        offsets=[-1, 0, 1],
        extreme_kind="max",
    )
    after = build_active_bracket_book_rows(
        source_rows={("Tokyo", target_date): {"source": "jma_amedas", "source_market_value": 35}},
        metar_rows=metar_rows,
        market_index=market_index,
        market_proxy="",
        active_cities={"Tokyo"},
        offsets=[-1, 0, 1],
        extreme_kind="max",
    )

    assert {row["bracket"] for row in before} == {"33", "34", "35"}
    assert {row["bracket"] for row in after} == {"33", "34", "35", "36"}
    assert next(row for row in before if row["bracket"] == "34")["raw"]["asks"][0]["size"] == 20.0
    assert next(row for row in after if row["bracket"] == "34")["monitor_reason"] == (
        "pre_and_post_cross_continuous_active_ladder"
    )
    official_center = next(row for row in after if row["bracket"] == "34")
    assert official_center["schema_version"] == "fast_source_active_bracket_book_v2"
    assert official_center["capture_anchor_values"] == {"source": 35, "official": 34}
    assert {reason["anchor_kind"] for reason in official_center["capture_reasons"]} == {
        "source",
        "official",
    }


def test_gamma_market_parsing_handles_fahrenheit_ranges_and_minimum_slugs():
    assert bracket_from_question("Will the highest temperature in SF be between 76-77°F?") == "76-77"
    assert bracket_from_question("Will the highest temperature in Atlanta be 95°F or higher?") == "95+"
    assert temperature_event_slug("HongKong", "2026-07-14", "min").startswith(
        "lowest-temperature-in-hong-kong-on-july-14-2026"
    )
