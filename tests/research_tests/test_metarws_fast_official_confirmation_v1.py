from __future__ import annotations

from datetime import datetime, timezone

from scripts.analysis.forecast_quality.research_metarws_fast_official_confirmation_v1 import (
    Event,
    _episodes_summary,
    confirmation_episodes,
    deduplicate_first_seen,
    exact_time_pairs,
    official_anchor_pairs,
)


def event(source: str, minute: int, temp: float, receive_second: int, *, station: str = "KATL") -> Event:
    observation = datetime(2026, 8, 30, 10, minute, tzinfo=timezone.utc)
    wall = int(observation.timestamp() * 1_000_000_000) + receive_second * 1_000_000_000
    return Event(
        source_id=source,
        observation_version_id=f"{source}-{station}-{minute}-{temp}",
        station_id=station,
        report_kind="METAR",
        observation_time=observation,
        air_temperature_c=temp,
        received_wall_ns=wall,
        received_monotonic_ns=(minute * 60 + receive_second) * 1_000_000_000,
        clock_valid=False,
        source_age_seconds=float(receive_second),
        live_eligible=True,
    )


def test_exact_datis_pair_keeps_same_time_and_receipt_order() -> None:
    rows = [
        event("METAR_WS_DATIS", 53, 30.0, 60),
        event("METAR_WS_METAR", 53, 30.0, 140),
        event("METAR_WS_DATIS", 54, 31.0, 60),
    ]
    pairs = exact_time_pairs(rows, "METAR_WS_DATIS")
    assert len(pairs) == 1
    assert pairs[0][1].received_monotonic_ns - pairs[0][0].received_monotonic_ns == 80_000_000_000


def test_official_anchor_requires_source_to_have_arrived_first() -> None:
    rows = [
        event("METAR_WS_HFMETAR", 50, 29.0, 200),
        event("METAR_WS_HFMETAR", 52, 30.0, 400),
        event("METAR_WS_METAR", 53, 30.0, 140),
    ]
    semantic = official_anchor_pairs(rows, "METAR_WS_HFMETAR", require_early=False)
    early = official_anchor_pairs(rows, "METAR_WS_HFMETAR", require_early=True)
    assert semantic[0][0].observation_time.minute == 52
    assert early[0][0].observation_time.minute == 50


def test_confirmation_episode_collapses_repeats_and_marks_horizons() -> None:
    rows = [
        event("METAR_WS_METAR", 0, 20.0, 120),
        event("METAR_WS_HFMETAR", 5, 21.0, 300),
        event("METAR_WS_HFMETAR", 10, 21.0, 300),
        event("METAR_WS_METAR", 53, 21.0, 120),
    ]
    episodes = confirmation_episodes(rows, "METAR_WS_HFMETAR")
    assert len(episodes) == 1
    assert episodes[0]["direction"] == "up"
    assert episodes[0]["next_official_exact"] is True
    assert episodes[0]["directional_confirmed_20m"] is False
    assert episodes[0]["directional_confirmed_60m"] is True
    summary = _episodes_summary(episodes)
    assert summary["horizon_60m"]["directional_confirmed"] == 1


def test_confirmation_rejects_official_observed_before_fast_event() -> None:
    rows = [
        event("METAR_WS_METAR", 0, 20.0, 120),
        event("METAR_WS_HFMETAR", 55, 21.0, 300),
        # Arrives after the HF event but its observation predates it, so it is
        # not a causal confirmation of the 10:55 observation.
        event("METAR_WS_METAR", 53, 20.0, 500),
    ]
    episodes = confirmation_episodes(rows, "METAR_WS_HFMETAR")
    assert len(episodes) == 1
    assert episodes[0]["next_official_temp_c"] is None


def test_first_seen_dedup_normalizes_equivalent_timezone_instants() -> None:
    first = event("METAR_WS_HFMETAR", 5, 21.0, 300)
    duplicate = Event(
        **{
            **first.__dict__,
            "observation_version_id": "later-revision",
            "observation_time": datetime.fromisoformat("2026-08-30T12:05:00+02:00"),
            "received_monotonic_ns": first.received_monotonic_ns + 1,
        }
    )
    deduplicated = deduplicate_first_seen([duplicate, first])
    assert len(deduplicated) == 1
    assert deduplicated[0].observation_version_id == first.observation_version_id
