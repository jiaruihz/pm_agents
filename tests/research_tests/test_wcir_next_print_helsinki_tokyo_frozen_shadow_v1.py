from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from weather_city_runtime.helsinki_tokyo_next_print_frozen_v1 import (
    FrozenModels,
    HelsinkiTokyoNextPrintFrozenRuntime,
    _paired_rps_delta,
    _raw_day_path,
    _summarize_score_rows,
    build_prediction,
    label_prediction,
    resolve_market_book_identity,
    validate_exact_expression,
)


ROOT = Path(__file__).resolve().parents[2]
REVIEW = ROOT / "reviews/wcir_next_official_print_helsinki_tokyo_frozen_v1"


def _event(**updates):
    row = {
        "schema_version": "fast_source_prev_no_trial_v2",
        "city": "Helsinki",
        "source": "fmi",
        "station": "100968",
        "target_date": "2026-08-31",
        "event_key": "Helsinki|2026-08-31|fmi|2026-08-31T03:10:00Z|21|20|20",
        "source_obs_ts_utc": "2026-08-31T03:10:00Z",
        "source_detect_ts_utc": "2026-08-31T03:11:00Z",
        "ts_utc": "2026-08-31T03:11:00Z",
        "source_temp_c": 20.8,
        "source_round_c": 21,
        "source_cross_confirmed": True,
        "latest_metar_report_ts_utc": "2026-08-31T02:50:00Z",
        "latest_metar_round_c": 20,
        "metar_running_max_round_c": 20,
        "t_minus_1_no_bracket_c": 20,
        "market_id": "market-helsinki-20",
        "condition_id": "condition-helsinki-20",
        "token_id": "no-helsinki-20",
        "polymarket_event_id": "event-helsinki-20260831",
        "polymarket_event_slug": "highest-temperature-in-helsinki-on-august-31",
    }
    row.update(updates)
    return row


def _source_path() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "target_date": "2026-08-31",
                "observed_at": stamp,
                "available_at": available,
                "latest_fast_native_value": value,
                "source_observation_id": f"fmi-{stamp}",
            }
            for stamp, available, value in (
                ("2026-08-31T02:50:00Z", "2026-08-31T02:51:00Z", 19.9),
                ("2026-08-31T03:00:00Z", "2026-08-31T03:01:00Z", 20.3),
                ("2026-08-31T03:10:00Z", "2026-08-31T03:10:30Z", 20.8),
            )
        ]
    )


def _official():
    return {
        "city": "Helsinki",
        "target_date": "2026-08-31",
        "source": "aviationweather_metar",
        "station": "EFHK",
        "source_report_ts_utc": "2026-08-31T03:20:00Z",
        "source_event_ts_utc": "2026-08-31T03:20:00Z",
        "first_seen_at_utc": "2026-08-31T03:21:00Z",
        "temp_c": 21.0,
        "information_event_id": "official-efhk-0320",
        "raw_row_hash": "official-row",
        "raw_payload_hash": "official-payload",
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _settlement_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """CREATE TABLE settlement_outcomes (
            settlement_outcome_id TEXT PRIMARY KEY, source_system TEXT NOT NULL,
            source_path TEXT, city TEXT NOT NULL, target_date TEXT NOT NULL,
            bracket TEXT NOT NULL, unit TEXT, condition_id TEXT, market_id TEXT,
            token_id TEXT, raw_final_price REAL, final_price REAL NOT NULL,
            settlement_status TEXT NOT NULL, question TEXT, payload TEXT,
            created_at_utc TEXT NOT NULL
            )"""
        )
        conn.execute(
            """INSERT INTO settlement_outcomes VALUES
            ('settlement-1','fixture',NULL,'Helsinki','2026-08-31','20','C',
            'condition-helsinki-20','market-helsinki-20','no-helsinki-20',1,1,
            'settled',NULL,'{}','2026-09-01T00:00:00Z')"""
        )
        conn.commit()
    finally:
        conn.close()


def _market_payload() -> dict:
    return {
        "id": "market-helsinki-20",
        "conditionId": "condition-helsinki-20",
        "clobTokenIds": json.dumps(["yes-helsinki-20", "no-helsinki-20"]),
        "events": [
            {
                "id": "event-helsinki-20260831",
                "slug": "highest-temperature-in-helsinki-on-august-31",
            }
        ],
    }


def test_canonical_market_book_resolves_event_without_gamma_event_array() -> None:
    seed = {
        "trigger_event_id": "trigger-1",
        "polymarket_event_id": "",
        "market_id": "market-helsinki-20",
        "condition_id": "condition-helsinki-20",
        "token_id": "no-helsinki-20",
        "checkpoint_timestamp": "2026-08-31T03:11:00Z",
        "side": "NO",
        "native_bracket": "20",
    }
    identity, _ = resolve_market_book_identity(
        seed,
        {
            "schema_version": "fixture",
            "producer": "market-books",
            "records": [
                {
                    "event_id": "event-helsinki-20260831",
                    "event_slug": "highest-temperature-in-helsinki-on-august-31",
                    "market_id": "market-helsinki-20",
                    "condition_id": "condition-helsinki-20",
                    "token_id": "no-helsinki-20",
                    "outcome": "no",
                    "bracket": "20",
                    "available_at_utc": "2026-08-31T03:11:02Z",
                }
            ],
        },
    )
    assert identity["polymarket_event_id"] == "event-helsinki-20260831"
    assert identity["identity_source"] == "canonical_market_books_exact_token"


def test_tokyo_feature_path_reads_previous_utc_partition(tmp_path: Path) -> None:
    event = _event(
        city="Tokyo",
        source="jma_amedas",
        station="44166",
        target_date="2026-09-01",
        source_obs_ts_utc="2026-08-31T21:10:00Z",
    )
    rows = [
        {
            "city": "Tokyo",
            "source": "jma_amedas",
            "station": "44166",
            "observation_time_utc": stamp,
            "available_at_utc": stamp,
            "temp_c": value,
            "information_event_id": f"jma-{stamp}",
        }
        for stamp, value in (
            ("2026-08-31T20:50:00Z", 24.0),
            ("2026-08-31T21:00:00Z", 24.1),
            ("2026-08-31T21:10:00Z", 24.2),
        )
    ]
    _write_jsonl(
        tmp_path / "2026-08-31" / "high_frequency_observations.jsonl", rows
    )
    frame = _raw_day_path(tmp_path, event)
    assert len(frame) == 3
    assert frame["observed_at"].min() == pd.Timestamp("2026-08-31T20:50:00Z")


def _producer_health(path: Path, generated_at_utc: str) -> None:
    path.write_text(
        json.dumps(
            {
                "status": "ok",
                "schema_version": "fast_source_prev_no_trial_latest_v2",
                "strategy_id": "fast_source_prev_no_trial_v1",
                "generated_at_utc": generated_at_utc,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _write_fast_path(root: Path) -> None:
    _write_jsonl(
        root / "2026-08-31" / "high_frequency_observations.jsonl",
        [
            {
                "city": "Helsinki",
                "source": "fmi",
                "station": "100968",
                "target_date": "2026-08-31",
                "observation_time_utc": row["observed_at"],
                "available_at_utc": row["available_at"],
                "temp_c": row["latest_fast_native_value"],
                "information_event_id": row["source_observation_id"],
            }
            for row in _source_path().to_dict("records")
        ],
    )


def test_exact_expression_requires_real_source_and_full_identity() -> None:
    assert validate_exact_expression(_event()) == ("ELIGIBLE", [])
    category, reasons = validate_exact_expression(_event(station="EFHK"))
    assert category == "RESEARCH_INELIGIBLE"
    assert "FAST_SOURCE_STATION_MISMATCH" in reasons
    category, reasons = validate_exact_expression(_event(token_id=""))
    assert category == "DATA_FAIL_CLOSED"
    assert any(reason.startswith("EXACT_IDENTITY_MISSING") for reason in reasons)


def test_shared_full_path_builder_scores_all_frozen_arms() -> None:
    models = FrozenModels(REVIEW)
    audit, prediction, lineage = build_prediction(_event(), _source_path(), models)
    assert audit["classification"] == "ELIGIBLE"
    assert prediction is not None
    assert set(prediction["models"]) == {"B2", "M1", "M2"}
    assert prediction["source_path_row_count"] == 3
    assert len(lineage) == 16
    assert all(abs(sum(prediction[f"{model}_PMF"]) - 1.0) < 1e-9 for model in ("B2", "M1", "M2"))


def test_incomplete_path_fails_closed_instead_of_recomputing_from_event() -> None:
    models = FrozenModels(REVIEW)
    audit, prediction, _ = build_prediction(_event(), _source_path().iloc[1:].copy(), models)
    assert prediction is None
    assert audit["classification"] == "DATA_FAIL_CLOSED"
    assert audit["reasons"] == ["INCOMPLETE_CAPTURED_SOURCE_PATH"]


def test_official_link_uses_efhk_not_fmi_station() -> None:
    models = FrozenModels(REVIEW)
    _, prediction, _ = build_prediction(_event(), _source_path(), models)
    assert prediction is not None
    label = label_prediction(prediction, [_official()])
    assert label["status"] == "linked"
    assert label["official_station"] == "EFHK"
    assert label["future_next_print_label"] == 1
    assert label["next_print_new_running_max"] is True


def test_known_physical_target_is_not_skipped_for_a_later_report() -> None:
    models = FrozenModels(REVIEW)
    _, prediction, _ = build_prediction(_event(), _source_path(), models)
    prediction["raw_source_lineage"]["available_at_utc"] = "2026-08-31T03:22:00Z"
    later = {**_official(), "source_report_ts_utc": "2026-08-31T03:50:00Z",
             "first_seen_at_utc": "2026-08-31T03:51:00Z", "information_event_id": "later-print"}
    label = label_prediction(prediction, [_official(), later])
    assert label["status"] == "missing"
    assert label["missing_reason"] == "TARGET_ALREADY_AVAILABLE_AT_DECISION"
    assert label["future_next_print_label"] is None


def test_runtime_is_idempotent_and_stays_zero_notional(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    fast_root = tmp_path / "fast"
    official_root = tmp_path / "official"
    db = tmp_path / "weather.db"
    _write_jsonl(events, [_event()])
    _write_fast_path(fast_root)
    _write_jsonl(official_root / "2026-08-31" / "sources.jsonl", [_official()])
    _settlement_db(db)
    producer_health = tmp_path / "producer_health.json"
    _producer_health(producer_health, "2026-08-31T03:12:00Z")
    runtime = HelsinkiTokyoNextPrintFrozenRuntime(
        tmp_path / "shadow",
        review_root=REVIEW,
        event_path=events,
        source_path_root=fast_root,
        official_path_root=official_root,
        settlement_db=db,
        ws_runtime_root=tmp_path / "missing-ws",
        producer_health_path=producer_health,
        market_metadata_fetcher=lambda _market_id: _market_payload(),
    )
    first = runtime.run_once(
        now_utc=datetime(2026, 8, 31, 3, 12, tzinfo=timezone.utc)
    )
    _producer_health(producer_health, "2026-09-01T00:00:00Z")
    second = runtime.run_once(now_utc=datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert first["health"]["raw_opportunity_events"] == 1
    assert first["health"]["feature_eligible_events"] == 1
    assert len(first["health"]["frozen_hashes"]["action_shadow_overlay_sha256"]) == 64
    assert first["health"]["cities"]["Helsinki"]["label_linked_events"] == 1
    assert first["health"]["exact_identity_action_count"] == 3
    identity = next(iter(runtime.market_identities.rows.values()))
    assert identity["trigger_event_id"] != identity["polymarket_event_id"]
    assert identity["polymarket_event_id"] == "event-helsinki-20260831"
    demand = next(iter(runtime.demands.rows.values()))
    assert demand["condition_id"] == "condition-helsinki-20"
    assert demand["token_id"] == "no-helsinki-20"
    assert demand["metadata"]["market_id"] == "market-helsinki-20"
    assert demand["metadata"]["checkpoint_timestamp"] == "2026-08-31T03:11:00Z"
    assert first["health"]["safety"] == {
        "signal_candidates": 0,
        "trade_intents": 0,
        "plans": 0,
        "orders": 0,
        "fills": 0,
        "exchange_calls": 0,
        "actual_notional_usd": 0.0,
    }
    assert second["ingested"]["raw_events"] == 0
    assert second["health"]["raw_opportunity_events"] == 1


def test_event_first_path_later_retries_without_losing_denominator(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    producer_health = tmp_path / "producer_health.json"
    _write_jsonl(events, [_event()])
    _producer_health(producer_health, "2026-08-31T03:11:30Z")
    runtime = HelsinkiTokyoNextPrintFrozenRuntime(
        tmp_path / "shadow",
        review_root=REVIEW,
        event_path=events,
        source_path_root=tmp_path / "fast",
        official_path_root=tmp_path / "official",
        ws_runtime_root=tmp_path / "ws",
        producer_health_path=producer_health,
        market_metadata_fetcher=lambda _market_id: _market_payload(),
    )
    first = runtime.run_once(
        now_utc=datetime(2026, 8, 31, 3, 11, 30, tzinfo=timezone.utc)
    )
    assert first["ingested"]["retry_pending"] == 1
    assert first["health"]["raw_opportunity_events"] == 1
    assert first["health"]["feature_eligible_events"] == 0
    _write_fast_path(tmp_path / "fast")
    _producer_health(producer_health, "2026-08-31T03:13:00Z")
    second = runtime.run_once(
        now_utc=datetime(2026, 8, 31, 3, 13, tzinfo=timezone.utc)
    )
    assert second["ingested"]["new_input_rows"] == 0
    assert second["ingested"]["predictions"] == 1
    assert second["health"]["raw_opportunity_events"] == 1
    assert second["health"]["feature_eligible_events"] == 1


def test_path_retry_becomes_terminal_after_frozen_timeout(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    producer_health = tmp_path / "producer_health.json"
    _write_jsonl(events, [_event()])
    _producer_health(producer_health, "2026-08-31T03:17:00Z")
    runtime = HelsinkiTokyoNextPrintFrozenRuntime(
        tmp_path / "shadow",
        review_root=REVIEW,
        event_path=events,
        source_path_root=tmp_path / "fast",
        official_path_root=tmp_path / "official",
        ws_runtime_root=tmp_path / "ws",
        producer_health_path=producer_health,
        market_metadata_fetcher=lambda _market_id: _market_payload(),
    )
    first = runtime.run_once(
        now_utc=datetime(2026, 8, 31, 3, 17, tzinfo=timezone.utc)
    )
    assert first["ingested"]["retry_timeouts"] == 1
    latest = next(iter(runtime._latest_attempts().values()))
    assert latest["terminal"] is True
    assert "FEATURE_RETRY_TIMEOUT_300S" in latest["reasons"]
    _write_fast_path(tmp_path / "fast")
    second = runtime.run_once(
        now_utc=datetime(2026, 8, 31, 3, 18, tzinfo=timezone.utc)
    )
    assert second["ingested"]["predictions"] == 0
    assert second["health"]["feature_eligible_events"] == 0


def test_capture_subscription_clock_controls_entry_ready(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    producer_health = tmp_path / "producer_health.json"
    _write_jsonl(events, [_event()])
    _write_fast_path(tmp_path / "fast")
    _producer_health(producer_health, "2026-08-31T03:12:00Z")
    runtime = HelsinkiTokyoNextPrintFrozenRuntime(
        tmp_path / "shadow",
        review_root=REVIEW,
        event_path=events,
        source_path_root=tmp_path / "fast",
        official_path_root=tmp_path / "official",
        ws_runtime_root=tmp_path / "ws",
        producer_health_path=producer_health,
        market_metadata_fetcher=lambda _market_id: _market_payload(),
    )
    runtime.run_once(now_utc=datetime(2026, 8, 31, 3, 12, tzinfo=timezone.utc))
    demand = next(iter(runtime.demands.rows.values()))
    assert demand["requested_at_utc"] == "2026-08-31T03:12:00Z"
    epoch = {
        "subscription_epoch_id": "epoch-ready",
        "producer": "weather_data_feed_service.market_books_ws",
        "producer_build_id": "fixture-sha",
        "started_at_utc": "2026-08-31T03:12:10Z",
        "token_map_id": "token-map-fixture",
        "token_ids": ["no-helsinki-20"],
        "token_rows": {
            "no-helsinki-20": {
                "condition_id": "condition-helsinki-20",
                "capture_demand_id": demand["demand_id"],
                "capture_demand_ids": [demand["demand_id"]],
            }
        },
        "capture_demands": [
            {**demand, "resolution_status": "resolved_direct_token"}
        ],
    }
    _write_jsonl(
        tmp_path
        / "ws"
        / "market_books"
        / "ws_incremental"
        / "subscription_epochs"
        / "subscription_epochs_2026-08-31.jsonl",
        [epoch],
    )
    _producer_health(producer_health, "2026-08-31T03:13:00Z")
    result = runtime.run_once(
        now_utc=datetime(2026, 8, 31, 3, 13, tzinfo=timezone.utc)
    )
    ready = next(iter(runtime.subscription_readiness.rows.values()))
    assert result["subscriptions"]["ready"] == 1
    assert ready["capture_subscription_ready_at_utc"] == "2026-08-31T03:12:10Z"
    assert ready["entry_ready_at_utc"] == "2026-08-31T03:12:10Z"


def test_unresolved_archive_native_event_identity_blocks_action(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    producer_health = tmp_path / "producer_health.json"
    _write_jsonl(events, [_event()])
    _write_fast_path(tmp_path / "fast")
    _producer_health(producer_health, "2026-08-31T03:12:00Z")

    def fail(_market_id: str) -> dict:
        raise OSError("fixture gamma unavailable")

    runtime = HelsinkiTokyoNextPrintFrozenRuntime(
        tmp_path / "shadow",
        review_root=REVIEW,
        event_path=events,
        source_path_root=tmp_path / "fast",
        official_path_root=tmp_path / "official",
        ws_runtime_root=tmp_path / "ws",
        producer_health_path=producer_health,
        market_metadata_fetcher=fail,
    )
    result = runtime.run_once(
        now_utc=datetime(2026, 8, 31, 3, 12, tzinfo=timezone.utc)
    )
    assert result["identities"]["unresolved"] == 1
    assert result["health"]["exact_identity_prediction_count"] == 0
    assert result["health"]["exact_identity_action_count"] == 0
    assert result["health"]["capture_demand_count"] == 0


def test_runtime_prefers_shared_market_book_and_never_calls_gamma(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    producer_health = tmp_path / "producer_health.json"
    market_latest = tmp_path / "market_latest.json"
    _write_jsonl(events, [_event()])
    _write_fast_path(tmp_path / "fast")
    _producer_health(producer_health, "2026-08-31T03:12:00Z")
    market_latest.write_text(
        json.dumps(
            {
                "schema_version": "fixture",
                "producer": "market-books",
                "records": [
                    {
                        "event_id": "event-helsinki-20260831",
                        "event_slug": "highest-temperature-in-helsinki-on-august-31",
                        "market_id": "market-helsinki-20",
                        "condition_id": "condition-helsinki-20",
                        "token_id": "no-helsinki-20",
                        "outcome": "no",
                        "bracket": "20",
                        "available_at_utc": "2026-08-31T03:11:02Z",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def forbidden(_market_id: str) -> dict:
        raise AssertionError("Gamma should not be called")

    runtime = HelsinkiTokyoNextPrintFrozenRuntime(
        tmp_path / "shadow",
        review_root=REVIEW,
        event_path=events,
        source_path_root=tmp_path / "fast",
        official_path_root=tmp_path / "official",
        ws_runtime_root=tmp_path / "ws",
        producer_health_path=producer_health,
        market_latest_path=market_latest,
        market_metadata_fetcher=forbidden,
    )
    result = runtime.run_once(
        now_utc=datetime(2026, 8, 31, 3, 12, tzinfo=timezone.utc)
    )
    assert result["identities"]["resolved_from_market_books"] == 1
    assert result["identities"]["http_calls"] == 0
    assert result["health"]["exact_identity_action_count"] == 3


def test_executable_action_emits_zero_size_standard_facts(tmp_path: Path) -> None:
    runtime = HelsinkiTokyoNextPrintFrozenRuntime(
        tmp_path / "shadow",
        review_root=REVIEW,
        event_path=tmp_path / "events.jsonl",
        source_path_root=tmp_path / "fast",
        official_path_root=tmp_path / "official",
    )
    identity = {
        "market_id": "market-helsinki-20",
        "condition_id": "condition-helsinki-20",
        "token_id": "no-helsinki-20",
        "native_bracket": "20",
    }
    runtime.actions.put(
        {
            "action_id": "action-1",
            "prediction_row_id": "prediction-1",
            "trigger_event_id": "trigger-1",
            "city": "Helsinki",
            "target_date": "2026-08-31",
            "model": "M1",
            "model_id": "ams_next_print_m1_ordinal_logit",
            "p_new_running_max": 0.7,
            "action_ready_at_utc": "2026-08-31T03:11:00Z",
            "market_identity": identity,
        }
    )
    runtime.markouts.put(
        {
            "markout_row_id": "markout-1",
            "prediction_row_id": "prediction-1",
            "materializer_version": "helsinki_tokyo_next_print_frozen_ws_v1",
            "checkpoint": "entry_after_p95_latency",
            "primary_status": "VALID_TWO_SIDED_DEPTH",
            "book_snapshot_id": "book-1",
            "raw_lineage_id": "raw-1",
            "best_ask": 0.60,
            "sweeps": {
                "buy_5": {
                    "fully_executable": True,
                    "average_price": 0.61,
                    "taker_fee_usd": 0.01,
                }
            },
        }
    )
    result = runtime.materialize_standard_lineage()
    assert result == {"signal_candidates": 1, "trade_intents": 1}
    candidate = next(iter(runtime.signal_candidates.rows.values()))
    intent = next(iter(runtime.trade_intents.rows.values()))
    assert candidate["selected"] is True
    assert candidate["p_model"] is None
    assert intent["requested_size"] == 0.0
    assert intent["mode"] == "zero_notional"


def test_producer_freshness_is_fail_closed_in_health(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text("", encoding="utf-8")
    producer_health = tmp_path / "producer_health.json"
    _producer_health(producer_health, "2026-08-31T03:00:00Z")
    runtime = HelsinkiTokyoNextPrintFrozenRuntime(
        tmp_path / "shadow",
        review_root=REVIEW,
        event_path=events,
        source_path_root=tmp_path / "fast",
        official_path_root=tmp_path / "official",
        ws_runtime_root=tmp_path / "ws",
        producer_health_path=producer_health,
        market_metadata_fetcher=lambda _market_id: _market_payload(),
    )
    result = runtime.run_once(
        now_utc=datetime(2026, 8, 31, 3, 10, tzinfo=timezone.utc)
    )
    assert result["health"]["status"] == "warming"
    assert "PRODUCER_HEALTH_STALE" in result["health"]["input_health"]["issues"]


def test_score_weighting_and_paired_ci_use_target_date_blocks() -> None:
    rows = [
        {"prediction_row_id": "a1", "target_date": "2026-08-01", "official_print_group_id": "g1", "exact_delta": 1, "new_max_hit": 1, "rps": 0.1, "logloss": 0.1},
        {"prediction_row_id": "a2", "target_date": "2026-08-01", "official_print_group_id": "g1", "exact_delta": 1, "new_max_hit": 1, "rps": 0.1, "logloss": 0.1},
        {"prediction_row_id": "b1", "target_date": "2026-08-02", "official_print_group_id": "g2", "exact_delta": 0, "new_max_hit": 0, "rps": 0.9, "logloss": 0.9},
    ]
    summary = _summarize_score_rows(rows)
    assert summary["raw_rows"] == 3
    assert summary["official_print_groups"] == 2
    assert summary["target_dates"] == 2
    assert summary["rps"] == 0.5
    challenger = [{**row, "rps": row["rps"] - 0.05} for row in rows]
    paired = _paired_rps_delta(rows, challenger)
    assert paired["target_dates"] == 2
    assert paired["status"] == "OK"
    assert paired["candidate_minus_baseline"] == pytest.approx(-0.05)
    assert paired["ci95"][1] < 0


def test_runner_has_no_order_or_venue_imports() -> None:
    runtime_source = (ROOT / "weather_city_runtime/helsinki_tokyo_next_print_frozen_v1.py").read_text(encoding="utf-8")
    runner_source = (ROOT / "scripts/ops/weather_wcir_next_print_helsinki_tokyo_frozen_shadow_v1.py").read_text(encoding="utf-8")
    for forbidden in ("order_executor", "venue_client", "Credential", "OrderPlan"):
        assert forbidden not in runtime_source
        assert forbidden not in runner_source
