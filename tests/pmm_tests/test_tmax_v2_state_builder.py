from __future__ import annotations

import json
import sqlite3
import sys

import pytest

from scripts.etl import materialize_tmax_v2_canonical_state as materializer
from src.strategies.weather_edge_v1.tools.tmax_feature_contract_v2 import validate_feature_payload
from src.strategies.weather_edge_v1.tools.tmax_v2_state_builder import (
    DEFAULT_DB,
    FEATURE_ARTIFACT_VERSION,
    TmaxV2StateBuilderError,
    build_tmax_v2_feature_frame,
    tmax_v2_coverage_summary,
)
from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical


DECISION = "2026-07-11T12:00:00Z"


def test_tmax_v2_defaults_to_production_canonical_db() -> None:
    assert materializer.DEFAULT_DB == materializer._PRODUCTION.canonical_db_path
    assert DEFAULT_DB == materializer._PRODUCTION.canonical_db_path


def test_materializer_rejects_nonfinite_numbers() -> None:
    assert materializer.number(float("nan")) is None
    assert materializer.number(float("inf")) is None
    assert materializer.number(float("-inf")) is None


def test_materializer_dry_run_does_not_create_missing_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    missing = tmp_path / "missing" / "weather.db"
    monkeypatch.setattr(
        sys,
        "argv",
        ["materialize_tmax_v2_canonical_state.py", "--db", str(missing), "--dry-run"],
    )

    with pytest.raises(sqlite3.OperationalError):
        materializer.main()

    assert not missing.exists()
    assert not missing.parent.exists()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_schema_canonical(conn)
    return conn


def _question(bracket: str, unit: str, *, first: bool, last: bool) -> str:
    suffix = " or below" if first else " or higher" if last else ""
    return f"Will the highest temperature in Testville be {bracket.rstrip('+')}°{unit}{suffix} on July 11?"


def _insert_ladder(
    conn: sqlite3.Connection,
    *,
    unit: str = "F",
    brackets: tuple[str, ...] = ("69", "70", "71", "72", "73+"),
    fetched_at: str | None = DECISION,
    missing_quote_bracket: str | None = None,
    settlement_source: str | None = "official_station_diff_confirmed",
    question_unit_override: dict[str, str] | None = None,
) -> None:
    conn.execute(
        """INSERT INTO tmax_v2_ladder_snapshots (
            ladder_snapshot_id, source_system, source_path, source_snapshot_ts_utc,
            available_at_utc, source_payload_hash, city, target_date, event_identity,
            market_unit, settlement_source_class, market_timezone, market_utc_offset_seconds,
            absolute_ladder_signature, rung_count, complete_rung_count, completeness_status, lineage_status
        ) VALUES ('ladder-1','fixture_market','snapshot.json',?,?,'payload','Testville','2026-07-11','event',
                  ?,?,'UTC',0,?,?,?,'complete','pit_verified_capture')""",
        (DECISION, DECISION, unit, settlement_source, "|".join(brackets), len(brackets), len(brackets)),
    )
    marks = (0.10, 0.20, 0.30, 0.25, 0.15, 0.05)
    overrides = question_unit_override or {}
    for index, bracket in enumerate(brackets):
        mark = marks[index]
        quote_missing = bracket == missing_quote_bracket
        native_unit = overrides.get(bracket, unit)
        conn.execute(
            """INSERT INTO tmax_v2_ladder_rung_quotes (
                rung_quote_id, ladder_snapshot_id, absolute_bracket_identity, condition_id, market_id, question,
                yes_token_id, no_token_id, yes_direct_bid, yes_direct_ask, yes_direct_bid_size, yes_direct_ask_size,
                yes_direct_depth_bid_5c, yes_direct_depth_ask_5c, yes_book_status, yes_book_fetched_at_utc,
                no_direct_bid, no_direct_ask, no_direct_bid_size, no_direct_ask_size,
                no_direct_depth_bid_5c, no_direct_depth_ask_5c, no_book_status, no_book_fetched_at_utc, source_record_hash
            ) VALUES (?, 'ladder-1', ?, ?, ?, ?, ?, ?, ?, ?, 10, 11, 20, 21, 'ok', ?, ?, ?, 12, 13, 22, 23, 'ok', ?, ?)""",
            (
                f"rung-{bracket}",
                bracket,
                f"condition-{bracket}",
                f"market-{bracket}",
                _question(bracket, native_unit, first=index == 0, last=index == len(brackets) - 1),
                f"yes-{bracket}",
                f"no-{bracket}",
                None if quote_missing else mark - 0.01,
                None if quote_missing else mark + 0.01,
                fetched_at,
                None if quote_missing else 1.0 - (mark + 0.01),
                None if quote_missing else 1.0 - (mark - 0.01),
                fetched_at,
                f"hash-{bracket}",
            ),
        )


def _insert_observation(
    conn: sqlite3.Connection,
    event_id: str,
    obs_ts: str,
    available: str,
    temp_f: float,
    *,
    station_id: str | None = "KFIX",
    icao: str | None = "KFIX",
    feed_identity: str | None = "fixture_metar",
) -> None:
    conn.execute(
        """INSERT INTO tmax_v2_observation_event_lineage (
            tmax_v2_observation_id, source_observation_id, source_system, source_path, city, target_date,
            obs_ts_utc, temp_f, first_seen_at_utc, available_at_utc, source_kind,
            station_id, icao, feed_identity, identity_missing_reason, lineage_status
        ) VALUES (?, ?, 'fixture_obs', 'obs.jsonl', 'Testville', '2026-07-11', ?, ?, ?, ?, 'native',
                  ?, ?, ?, ?, 'pit_verified_first_seen')""",
        (
            event_id,
            event_id,
            obs_ts,
            temp_f,
            available,
            available,
            station_id,
            icao,
            feed_identity,
            None if station_id or icao or feed_identity else "identity_missing",
        ),
    )


def _default_curve() -> list[dict[str, object]]:
    return [
        {
            "valid_time_local": "2026-07-11T09:00:00",
            "valid_time_utc": "2026-07-11T09:00:00Z",
            "valid_time_basis": "source_time_local_plus_forecast_timezone",
            "temperature_f": 80,
        },
        {
            "valid_time_local": "2026-07-11T12:00:00",
            "valid_time_utc": DECISION,
            "valid_time_basis": "source_time_local_plus_forecast_timezone",
            "temperature_f": 78,
        },
        {
            "valid_time_local": "2026-07-11T15:00:00",
            "valid_time_utc": "2026-07-11T15:00:00Z",
            "valid_time_basis": "source_time_local_plus_forecast_timezone",
            "temperature_f": 74,
        },
    ]


def _insert_forecast(
    conn: sqlite3.Connection,
    *,
    capture_id: str = "forecast-1",
    available: str = "2026-07-11T11:00:00Z",
    run: str | None = None,
    curve: list[dict[str, object]] | None = None,
) -> None:
    normalized = curve if curve is not None else _default_curve()
    conn.execute(
        """INSERT INTO tmax_v2_forecast_captures (
            forecast_capture_id, source_system, source_path, source_row_hash, snapshot_ts_utc, available_at_utc,
            city, target_date, forecast_source, forecast_model, hourly_curve_json, normalized_hourly_curve_json,
            forecast_run_at_utc, forecast_timezone, forecast_utc_offset_seconds,
            curve_time_lineage_status, curve_time_missing_reason, lineage_status
        ) VALUES (?,'fixture_forecast',?,?,?,?,?,?, 'gfs_source','gfs',?,?,?,?,0,
                  'local_and_utc_verified',NULL,'pit_verified_capture')""",
        (
            capture_id,
            f"{capture_id}.jsonl",
            f"{capture_id}-hash",
            available,
            available,
            "Testville",
            "2026-07-11",
            json.dumps(normalized),
            json.dumps(normalized),
            run,
            "UTC",
        ),
    )


def _materialize(conn: sqlite3.Connection) -> str:
    materializer.materialize_states(conn, "2026-07-11", "2026-07-11", False)
    return str(conn.execute("SELECT tmax_state_id FROM tmax_v2_canonical_state_effective").fetchone()[0])


def _ready_conn(
    *,
    unit: str = "F",
    brackets: tuple[str, ...] = ("69", "70", "71", "72", "73+"),
    observations: tuple[tuple[str, str, float], ...] = (
        ("2026-07-11T09:00:00Z", "2026-07-11T09:01:00Z", 67),
        ("2026-07-11T11:00:00Z", "2026-07-11T11:01:00Z", 69),
        ("2026-07-11T11:30:00Z", "2026-07-11T11:31:00Z", 70),
    ),
    fetched_at: str | None = DECISION,
    missing_quote_bracket: str | None = None,
    settlement_source: str | None = "official_station_diff_confirmed",
    question_unit_override: dict[str, str] | None = None,
    curve: list[dict[str, object]] | None = None,
    forecast_run: str | None = None,
) -> tuple[sqlite3.Connection, str]:
    conn = _conn()
    _insert_ladder(
        conn,
        unit=unit,
        brackets=brackets,
        fetched_at=fetched_at,
        missing_quote_bracket=missing_quote_bracket,
        settlement_source=settlement_source,
        question_unit_override=question_unit_override,
    )
    _insert_forecast(conn, curve=curve, run=forecast_run)
    for index, (obs_ts, available, temp_f) in enumerate(observations):
        _insert_observation(conn, f"obs-{index}", obs_ts, available, temp_f)
    return conn, _materialize(conn)


def test_builder_uses_only_asof_inputs_and_preserves_contract_distinction() -> None:
    conn, state_id = _ready_conn()
    _insert_observation(conn, "obs-future", "2026-07-11T12:30:00Z", "2026-07-11T12:31:00Z", 99)
    _insert_forecast(
        conn,
        capture_id="forecast-future",
        available="2026-07-11T12:30:00Z",
        run="2026-07-11T12:00:00Z",
    )

    frame = build_tmax_v2_feature_frame(conn, tmax_state_id=state_id)[0]
    validation = validate_feature_payload(frame)

    assert frame["features"]["current_temperature"]["value"] == 70
    assert all(item["temp_f"] != 99 for item in frame["derived"]["path"]["history"])
    assert validation["valid"] is True
    assert validation["model_ready"] is False


@pytest.mark.parametrize(
    ("unit", "brackets", "observations", "expected_current", "expected_bucket", "expected_energy"),
    [
        (
            "F",
            ("69", "70", "71", "72", "73+"),
            (("2026-07-11T11:30:00Z", "2026-07-11T11:31:00Z", 70),),
            70.0,
            "70",
            4.0,
        ),
        (
            "C",
            ("18", "19", "20", "21", "22+"),
            (("2026-07-11T11:30:00Z", "2026-07-11T11:31:00Z", 68),),
            20.0,
            "20",
            (74.0 - 32.0) * 5.0 / 9.0 - 20.0,
        ),
    ],
)
def test_c_and_f_geometry_use_market_unit(
    unit: str,
    brackets: tuple[str, ...],
    observations: tuple[tuple[str, str, float], ...],
    expected_current: float,
    expected_bucket: str,
    expected_energy: float,
) -> None:
    conn, state_id = _ready_conn(unit=unit, brackets=brackets, observations=observations)
    frame = build_tmax_v2_feature_frame(conn, tmax_state_id=state_id)[0]

    assert frame["features"]["unit"]["value"] == unit
    assert frame["derived"]["path"]["current_temperature"] == pytest.approx(expected_current)
    assert frame["derived"]["market_geometry"]["buckets"]["current"]["absolute_bracket_identities"] == [expected_bucket]
    assert frame["derived"]["forecast"]["remaining_energy_from_running_max"] == pytest.approx(expected_energy)


def test_pullback_geometry_anchors_current_bucket_to_running_max() -> None:
    observations = (
        ("2026-07-11T10:00:00Z", "2026-07-11T10:01:00Z", 70),
        ("2026-07-11T11:00:00Z", "2026-07-11T11:01:00Z", 71),
        ("2026-07-11T11:30:00Z", "2026-07-11T11:31:00Z", 69),
    )
    conn, state_id = _ready_conn(observations=observations)
    frame = build_tmax_v2_feature_frame(conn, tmax_state_id=state_id)[0]
    path = frame["derived"]["path"]
    geometry = frame["derived"]["market_geometry"]

    assert path["current_temperature"] == 69
    assert path["running_max_temperature"] == 71
    assert path["is_pullback"] is True
    assert geometry["anchor_source"] == "running_max_temperature"
    assert geometry["buckets"]["current"]["absolute_bracket_identities"] == ["71"]
    assert geometry["buckets"]["below"]["absolute_bracket_identities"] == ["69", "70"]


def test_forecast_peak_and_remaining_energy_ignore_past_and_decision_points() -> None:
    conn, state_id = _ready_conn()
    forecast = build_tmax_v2_feature_frame(conn, tmax_state_id=state_id)[0]["derived"]["forecast"]

    assert forecast["future_point_count"] == 1
    assert forecast["forecast_peak_temperature"] == 74
    assert forecast["forecast_peak_valid_time_utc"] == "2026-07-11T15:00:00Z"
    assert forecast["forecast_peak_clock_local"] == "2026-07-11T15:00:00"
    assert forecast["remaining_energy_from_running_max"] == 4
    assert forecast["hourly_temperature_curve"][0]["temperature_market"] == 80
    assert forecast["hourly_temperature_curve"][0]["is_future"] is False


def test_forecast_without_future_curve_points_has_explicit_unknown_energy() -> None:
    curve = _default_curve()[:2]
    conn, state_id = _ready_conn(curve=curve)
    forecast = build_tmax_v2_feature_frame(conn, tmax_state_id=state_id)[0]["derived"]["forecast"]

    assert forecast["forecast_peak_temperature"] is None
    assert forecast["forecast_peak_clock_local"] is None
    assert forecast["remaining_energy_from_running_max"] is None
    assert forecast["remaining_energy_missing_reason"] == "no_future_curve_points_after_decision"


def test_trends_anchor_windows_to_latest_observation_report_timestamp() -> None:
    observations = (
        ("2026-07-11T09:00:00Z", "2026-07-11T09:01:00Z", 60),
        ("2026-07-11T10:00:00Z", "2026-07-11T10:01:00Z", 62),
        ("2026-07-11T10:45:00Z", "2026-07-11T10:46:00Z", 68),
        ("2026-07-11T11:30:00Z", "2026-07-11T11:31:00Z", 70),
    )
    conn, state_id = _ready_conn(observations=observations)
    path = build_tmax_v2_feature_frame(conn, tmax_state_id=state_id)[0]["derived"]["path"]

    assert path["temperature_trend_reference_ts_utc"] == "2026-07-11T11:30:00Z"
    assert path["temperature_trend_1h"] == 8


def test_partial_ladder_never_normalizes_remaining_marks() -> None:
    conn, state_id = _ready_conn(missing_quote_bracket="72")
    frame = build_tmax_v2_feature_frame(conn, tmax_state_id=state_id)[0]
    prior = frame["derived"]["market_full_ladder_prior"]

    assert prior["normalized"] is False
    assert prior["normalization_status"] == "incomplete_marks"
    assert prior["missing_mark_rungs"] == ["72"]
    assert all(rung["market_prior_probability"] is None for rung in prior["rungs"])
    assert frame["derived"]["market_geometry"]["buckets"]["below"]["market_prior_mass"] is None
    assert tmax_v2_coverage_summary(conn)["partial_ladder_states"] == 1


def test_missing_book_timestamp_uses_explicit_parent_snapshot_basis() -> None:
    conn, state_id = _ready_conn(fetched_at=None)
    frame = build_tmax_v2_feature_frame(conn, tmax_state_id=state_id)[0]
    rung = frame["features"]["settlement_ladder"]["value"][0]
    yes = rung["direct_quote"]["yes"]

    assert yes["ask"] is not None
    assert yes["book_fetched_at_utc"] is None
    assert yes["availability_basis"] == {
        "basis": "parent_ladder_snapshot",
        "source_report_ts_utc": DECISION,
        "available_at_utc": DECISION,
        "book_fetched_at_utc": None,
    }
    assert frame["derived"]["market_full_ladder_prior"]["normalized"] is True


def test_book_timestamp_after_decision_drops_side_quotes() -> None:
    conn, state_id = _ready_conn(fetched_at="2026-07-11T12:01:00Z")
    rung = build_tmax_v2_feature_frame(conn, tmax_state_id=state_id)[0]["features"]["settlement_ladder"]["value"][0]

    assert rung["direct_quote"]["yes"]["ask"] is None
    assert rung["direct_quote"]["yes"]["availability_basis"]["basis"] == "book_fetched_after_decision"
    assert rung["direct_quote"]["mark_status"] == "missing_legal_mark"


def test_station_settlement_source_question_and_native_unit_come_from_canonical_views() -> None:
    conn, state_id = _ready_conn()
    frame = build_tmax_v2_feature_frame(conn, tmax_state_id=state_id)[0]
    rung = frame["features"]["settlement_ladder"]["value"][0]

    assert frame["features"]["station_or_feed"]["value"] == "KFIX"
    assert frame["features"]["settlement_source"]["value"] == "official_station_diff_confirmed"
    assert rung["question"].endswith("on July 11?")
    assert rung["native_unit"] == "F"


def test_missing_settlement_source_stays_missing_without_rejecting_pit_state() -> None:
    conn, state_id = _ready_conn(settlement_source=None)
    frame = build_tmax_v2_feature_frame(conn, tmax_state_id=state_id)[0]

    assert frame["pit_status"] == "pit_verified"
    assert frame["features"]["settlement_source"]["value"] is None
    assert frame["features"]["settlement_source"]["missing_reason"]


def test_conflicting_question_unit_fails_the_entire_ladder() -> None:
    conn, state_id = _ready_conn(question_unit_override={"72": "C"})

    with pytest.raises(TmaxV2StateBuilderError, match="conflicting units"):
        build_tmax_v2_feature_frame(conn, tmax_state_id=state_id)


def test_builder_is_deterministic_and_coverage_has_semantic_counts() -> None:
    conn, state_id = _ready_conn()

    first = build_tmax_v2_feature_frame(conn, tmax_state_id=state_id)
    second = build_tmax_v2_feature_frame(conn, tmax_state_id=state_id)

    assert first == second
    assert first[0]["feature_artifact_version"] == FEATURE_ARTIFACT_VERSION
    assert tmax_v2_coverage_summary(conn) == {
        "states": 1,
        "unit_counts": {"F": 1},
        "pullback_states": 0,
        "future_only_energy_nonnull_states": 1,
        "future_only_energy_nonnull_rate": 1.0,
        "partial_ladder_states": 0,
    }
