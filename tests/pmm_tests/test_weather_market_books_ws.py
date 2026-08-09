import json
from datetime import datetime, timedelta, timezone

from weather_data_feed_service.market_books_ws import (
    HourlyWriter,
    SourceEventCursor,
    scheduled_report_windows,
    select_tokens,
)


NOW = datetime(2026, 8, 9, 3, 0, tzinfo=timezone.utc)


def _market_payload(city: str = "Busan") -> dict:
    rows = []
    for bracket in ("30", "31", "32", "33", "34", "35", "36"):
        for outcome in ("yes", "no"):
            rows.append(
                {
                    "city": city,
                    "event_date": "2026-08-09",
                    "city_local_date_at_snapshot": "2026-08-09",
                    "bracket": bracket,
                    "outcome": outcome,
                    "token_id": f"{city}-{bracket}-{outcome}",
                }
            )
    return {"records": rows}


def _select(
    *,
    now=NOW,
    observations=None,
    scheduled=frozenset({("Busan", "2026-08-09")}),
    research=frozenset(),
    bursts=frozenset(),
    state=None,
    post_invalidation_sec=300,
):
    return select_tokens(
        market_payload=_market_payload(),
        observations=observations or {},
        cities=["Busan"],
        now_utc=now,
        active_bracket_count=2,
        research_bracket_count=3,
        event_bracket_count=3,
        post_invalidation_sec=post_invalidation_sec,
        scheduled_keys=set(scheduled),
        research_keys=set(research),
        burst_keys=set(bursts),
        invalidation_state=state or {},
    )


def test_physically_invalid_lower_brackets_expire_after_markout_grace() -> None:
    before_cross = {
        ("Busan", "2026-08-09"): {
            "status": "ok",
            "running_max_c": 30.0,
        }
    }
    baseline = _select(observations=before_cross)
    observations = {
        ("Busan", "2026-08-09"): {
            "status": "ok",
            "running_max_c": 32.0,
        }
    }
    first = _select(
        now=NOW + timedelta(seconds=1),
        observations=observations,
        state=baseline.invalidation_state,
    )
    assert len(first.tokens) == 8
    assert first.grace_brackets["Busan"] == ["30", "31"]

    after_grace = _select(
        now=NOW + timedelta(seconds=302),
        observations=observations,
        state=first.invalidation_state,
    )
    assert len(after_grace.tokens) == 4
    assert after_grace.active_brackets["Busan"] == ["32", "33"]
    assert after_grace.grace_brackets["Busan"] == []


def test_already_invalid_on_start_is_not_subscribed() -> None:
    observations = {
        ("Busan", "2026-08-09"): {
            "status": "ok",
            "running_max_c": 32.0,
        }
    }
    selected = _select(observations=observations)

    assert len(selected.tokens) == 4
    assert selected.active_brackets["Busan"] == ["32", "33"]
    assert selected.grace_brackets["Busan"] == []


def test_recent_source_event_opens_only_the_hot_strip() -> None:
    observations = {
        ("Busan", "2026-08-09"): {
            "status": "ok",
            "running_max_c": 32.0,
        }
    }
    selected = _select(
        now=NOW + timedelta(seconds=301),
        observations=observations,
        scheduled=frozenset(),
        bursts={("Busan", "2026-08-09")},
        state={
            "Busan|2026-08-09|30": NOW.timestamp(),
            "Busan|2026-08-09|31": NOW.timestamp(),
        },
    )
    assert len(selected.tokens) == 6
    assert selected.burst_cities == ["Busan"]
    assert selected.active_brackets["Busan"] == ["32", "33", "34"]
    assert selected.grace_brackets["Busan"] == []


def test_sampled_research_window_adds_only_the_third_bracket() -> None:
    observations = {
        ("Busan", "2026-08-09"): {"status": "ok", "running_max_c": 32.0}
    }
    selected = _select(
        observations=observations,
        research={("Busan", "2026-08-09")},
    )

    assert len(selected.tokens) == 6
    assert selected.research_cities == ["Busan"]
    assert selected.active_brackets["Busan"] == ["32", "33", "34"]


def test_missing_settlement_facing_observation_fails_closed() -> None:
    selected = _select()
    assert len(selected.tokens) == 0
    assert selected.missing_observation_cities == ["Busan"]


def test_socket_is_idle_outside_scheduled_or_event_windows() -> None:
    observations = {
        ("Busan", "2026-08-09"): {"status": "ok", "running_max_c": 32.0}
    }
    selected = _select(observations=observations, scheduled=frozenset())

    assert selected.tokens == set()
    assert selected.active_brackets["Busan"] == []


def test_scheduled_report_window_uses_observation_cadence() -> None:
    observations = {
        ("Busan", "2026-08-09"): {
            "status": "ok",
            "last_obs_utc": "2026-08-09T02:30:00Z",
            "estimated_cadence_min": 30,
        }
    }
    active, research, next_reports = scheduled_report_windows(
        observations,
        now_utc=NOW - timedelta(seconds=30),
        before_sec=45,
        after_sec=120,
        extended_before_sec=125,
        research_sample_modulus=0,
    )

    assert active == {("Busan", "2026-08-09")}
    assert research == set()
    assert next_reports == {"Busan": "2026-08-09T03:00:00.000Z"}


def test_sampled_report_window_is_marked_for_three_brackets() -> None:
    observations = {
        ("Busan", "2026-08-09"): {
            "status": "ok",
            "last_obs_utc": "2026-08-09T02:30:00Z",
            "estimated_cadence_min": 30,
        }
    }
    active, research, _ = scheduled_report_windows(
        observations,
        now_utc=NOW - timedelta(seconds=100),
        before_sec=45,
        after_sec=120,
        extended_before_sec=125,
        research_sample_modulus=1,
    )

    assert active == {("Busan", "2026-08-09")}
    assert research == {("Busan", "2026-08-09")}


def test_source_event_cursor_ignores_revisions(tmp_path) -> None:
    path = tmp_path / "sources.jsonl"
    base = {
        "city": "Busan",
        "target_date": "2026-08-09",
        "information_event_status": "material",
        "material_state_change": True,
        "first_seen_at_utc": "2026-08-09T02:59:30Z",
    }
    rows = [
        {**base, "event_role": "revision", "information_event_id": "revision"},
        {**base, "event_role": "new_content", "information_event_id": "new"},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    active = SourceEventCursor(path).read(
        cities={"Busan"}, now_utc=NOW, burst_sec=120
    )

    assert active == {("Busan", "2026-08-09")}


def test_hourly_writer_uses_restart_safe_stream_file(tmp_path) -> None:
    writer = HourlyWriter(tmp_path)
    path = writer.write({"message": "one"}, NOW)
    writer.close()

    assert writer.stream_id in path.name
    assert path.suffix == ".jsonl"
    assert path.read_text(encoding="utf-8").strip() == '{"message":"one"}'
