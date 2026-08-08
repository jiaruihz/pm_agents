from datetime import datetime, timedelta, timezone

from weather_data_feed_service.market_books_ws import HourlyWriter, select_tokens


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


def _select(*, now=NOW, observations=None, bursts=frozenset(), state=None):
    return select_tokens(
        market_payload=_market_payload(),
        observations=observations or {},
        cities=["Busan"],
        now_utc=now,
        active_bracket_count=3,
        post_invalidation_sec=300,
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
    assert len(first.tokens) == 10
    assert first.grace_brackets["Busan"] == ["30", "31"]

    after_grace = _select(
        now=NOW + timedelta(seconds=302),
        observations=observations,
        state=first.invalidation_state,
    )
    assert len(after_grace.tokens) == 6
    assert after_grace.active_brackets["Busan"] == ["32", "33", "34"]
    assert after_grace.grace_brackets["Busan"] == []


def test_already_invalid_on_start_is_not_subscribed() -> None:
    observations = {
        ("Busan", "2026-08-09"): {
            "status": "ok",
            "running_max_c": 32.0,
        }
    }
    selected = _select(observations=observations)

    assert len(selected.tokens) == 6
    assert selected.active_brackets["Busan"] == ["32", "33", "34"]
    assert selected.grace_brackets["Busan"] == []


def test_recent_source_event_temporarily_promotes_full_ladder() -> None:
    observations = {
        ("Busan", "2026-08-09"): {
            "status": "ok",
            "running_max_c": 32.0,
        }
    }
    selected = _select(
        now=NOW + timedelta(seconds=301),
        observations=observations,
        bursts={("Busan", "2026-08-09")},
        state={
            "Busan|2026-08-09|30": NOW.timestamp(),
            "Busan|2026-08-09|31": NOW.timestamp(),
        },
    )
    assert len(selected.tokens) == 10
    assert selected.burst_cities == ["Busan"]
    assert selected.active_brackets["Busan"] == ["32", "33", "34", "35", "36"]
    assert selected.grace_brackets["Busan"] == []


def test_missing_settlement_facing_observation_fails_open_to_full_ladder() -> None:
    selected = _select()
    assert len(selected.tokens) == 14
    assert selected.missing_observation_cities == ["Busan"]


def test_hourly_writer_uses_restart_safe_stream_file(tmp_path) -> None:
    writer = HourlyWriter(tmp_path)
    path = writer.write({"message": "one"}, NOW)
    writer.close()

    assert writer.stream_id in path.name
    assert path.suffix == ".jsonl"
    assert path.read_text(encoding="utf-8").strip() == '{"message":"one"}'
