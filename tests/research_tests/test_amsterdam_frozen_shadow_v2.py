from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest

from scripts.analysis.forecast_quality.research_wcir_stage02_stage03_rev2 import checkpoint_times
from weather_city_runtime.amsterdam_frozen_shadow_v2 import (
    AmsterdamFrozenShadowRuntime,
    ENTRY_LATENCY_SECONDS,
    OFFICIAL_MATCH_WINDOW_SECONDS,
    capture_demands_for_prediction,
    label_for_prediction,
    resolve_market_identities,
)


def prediction() -> dict:
    pmf = [0.0] * 21
    pmf[11] = 1.0
    return {"prediction_row_id": "p1", "event_id": "event-1", "decision_vintage_id": "vintage-1",
            "forward_epoch_id": "epoch-1", "orders": 0, "fills": 0, "notional": 0,
            "raw_source_lineage": {"observation_time_utc": "2026-08-30T10:00:00Z", "available_at_utc": "2026-08-30T10:02:00Z", "target_date": "2026-08-30", "prior_official": {"last_official_native_value": 20.0}},
            "feature_vector": {"official_running_max": 20.0},
            "B2_PMF": pmf, "M1_PMF": pmf, "M2_PMF": pmf,
            "market_identity": [
                {"market_id": "market-1", "condition_id": "condition-1", "token_id": "token-no", "side": "NO", "native_bracket": "20", "target_date": "2026-08-30"},
                {"market_id": "market-1", "condition_id": "condition-1", "token_id": "token-yes", "side": "YES", "native_bracket": "20", "target_date": "2026-08-30"},
            ]}


def event(clock: str, temp: float, raw: str = "METAR EHAM 301030Z 22005KT 21/12 Q1015") -> dict:
    return {"city": "Amsterdam", "source": "aviationweather_metar", "station": "EHAM",
            "target_date": "2026-08-30", "source_report_ts_utc": clock,
            "first_seen_at_utc": "2026-08-30T10:12:00Z", "temp_c": temp,
            "raw_metar": raw, "payload_hash": f"payload|{clock}|{temp}", "content_key": f"EHAM|{clock}"}


def test_demand_exact_identity_zero_notional() -> None:
    demands = capture_demands_for_prediction(prediction())
    assert {row["token_id"] for row in demands} == {"token-no", "token-yes"}
    assert all(row["consumer_id"] == "weather_amsterdam_wcir_frozen_v2" for row in demands)
    assert all(row["strategy_key"] == "weather_amsterdam_wcir_frozen_v2" for row in demands)
    assert all(row["desired_transport"] == "REST_WS" for row in demands)
    assert all(row["requested_checkpoints_seconds"] == (0, 5, 15, 30, 60, 120) for row in demands)
    assert all(row["metadata"]["orders"] == row["metadata"]["fills"] == row["metadata"]["notional"] == 0 for row in demands)


def test_entry_checkpoint_uses_frozen_p95_latency_not_source_t0() -> None:
    times = checkpoint_times(
        {
            "source_detect_ts_utc": "2026-08-30T10:00:00Z",
            "official_first_seen_at_utc": "2026-08-30T10:10:00Z",
        },
        ENTRY_LATENCY_SECONDS,
    )
    assert times["source_t0"] != times["entry_after_p95_latency"]
    source = datetime.fromisoformat(times["source_t0"])
    entry = datetime.fromisoformat(times["entry_after_p95_latency"])
    assert (entry - source).total_seconds() == pytest.approx(ENTRY_LATENCY_SECONDS)


def test_label_excludes_speci_and_uses_next_distinct_routine() -> None:
    duplicate = event("2026-08-30T10:10:00Z", 21)
    row = label_for_prediction(prediction(), [event("2026-08-30T10:05:00Z", 99, "SPECI EHAM 301005Z 00000KT 99/00 Q1000"), duplicate, dict(duplicate)])
    assert row["status"] == "linked"
    assert row["future_next_print_label"] == 1
    assert row["official_print_time_utc"] == "2026-08-30T10:10:00Z"


def test_label_conflicting_revision_fails_closed() -> None:
    row = label_for_prediction(prediction(), [event("2026-08-30T10:10:00Z", 21), event("2026-08-30T10:10:00Z", 25)])
    assert row["status"] == "missing"
    assert row["missing_reason"] == "AMBIGUOUS_OFFICIAL_PRINT"


def test_label_duplicate_without_payload_hash_ignores_arrival_clock() -> None:
    first = event("2026-08-30T10:10:00Z", 21)
    first.pop("payload_hash")
    second = dict(first)
    second["first_seen_at_utc"] = "2026-08-30T10:13:00Z"
    row = label_for_prediction(prediction(), [first, second])
    assert row["status"] == "linked"
    assert row["official_first_seen_at_utc"] == "2026-08-30T10:12:00Z"


def test_label_outside_frozen_window_is_terminal_not_pending() -> None:
    late = event("2026-08-30T11:10:00Z", 21)
    late["first_seen_at_utc"] = "2026-08-30T11:12:00Z"
    row = label_for_prediction(prediction(), [late])
    assert OFFICIAL_MATCH_WINDOW_SECONDS == 3600
    assert row["status"] == "missing"
    assert row["missing_reason"] == "NEXT_ROUTINE_OUTSIDE_FROZEN_MATCH_WINDOW"


def test_exact_identity_native_bracket_drift_fails_closed() -> None:
    row = prediction()
    conflict = dict(row["market_identity"][0])
    conflict["native_bracket"] = "21"
    row["market_identity"].append(conflict)
    with pytest.raises(ValueError, match="identity"):
        resolve_market_identities(row, None)


def test_post_decision_market_ladder_resolves_identity_only_with_yes_no_pair() -> None:
    row = prediction()
    row["market_identity"] = None
    resolved = resolve_market_identities(row, {"records": [
        {"city": "Amsterdam", "event_date": "2026-08-30", "extreme_kind": "max", "bracket": "20", "market_id": "market-1", "condition_id": "condition-1", "token_id": "token-no", "outcome": "no"},
        {"city": "Amsterdam", "event_date": "2026-08-30", "extreme_kind": "max", "bracket": "20", "market_id": "market-1", "condition_id": "condition-1", "token_id": "token-yes", "outcome": "yes"},
    ]})
    assert resolved["status"] == "resolved"
    assert resolved["identity_source"] == "POST_DECISION_REST_IDENTITY_ONLY_NOT_BOOK_EVIDENCE"
    assert {item["token_id"] for item in resolved["identities"]} == {"token-no", "token-yes"}


def test_runtime_idempotency_and_payload_drift_fail_closed(tmp_path) -> None:
    runtime = AmsterdamFrozenShadowRuntime(tmp_path)
    assert runtime.ingest_predictions([prediction()]) == 1
    assert runtime.ingest_predictions([prediction()]) == 0
    drift = prediction()
    drift["notional"] = 1
    with pytest.raises(ValueError, match="zero-notional"):
        runtime.ingest_predictions([drift])


def test_external_scorer_append_is_refreshed_without_duplicate(tmp_path) -> None:
    runtime = AmsterdamFrozenShadowRuntime(tmp_path)
    journal = tmp_path / "predictions.jsonl"
    journal.write_text(
        json.dumps(prediction(), sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    assert runtime.refresh_predictions() == 1
    assert runtime.ingest_predictions([prediction()]) == 0
    assert len(journal.read_text(encoding="utf-8").splitlines()) == 1
    health = runtime.health()
    assert health["prediction_count"] == 1
    assert health["prediction_physical_row_count"] == 1
    assert health["prediction_duplicate_identity_count"] == 0


def test_legacy_identical_duplicate_is_counted_at_unique_prediction_grain(tmp_path) -> None:
    journal = tmp_path / "predictions.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(prediction(), sort_keys=True, separators=(",", ":")) + "\n"
    journal.write_text(encoded + encoded, encoding="utf-8")

    runtime = AmsterdamFrozenShadowRuntime(tmp_path)
    health = runtime.health()
    assert health["prediction_count"] == 1
    assert health["prediction_physical_row_count"] == 2
    assert health["prediction_duplicate_identity_count"] == 1


def test_prediction_journal_truncation_and_payload_replacement_fail_closed(tmp_path) -> None:
    runtime = AmsterdamFrozenShadowRuntime(tmp_path)
    runtime.ingest_predictions([prediction()])
    journal = tmp_path / "predictions.jsonl"
    original = journal.read_text(encoding="utf-8")

    journal.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="truncated"):
        runtime.refresh_predictions()

    drift = prediction()
    drift["feature_vector"] = {"official_running_max": 99.0}
    journal.write_text(
        json.dumps(drift, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="payload drift"):
        runtime.refresh_predictions()

    journal.write_text(original + '{"prediction_row_id":', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        runtime.refresh_predictions()


def test_health_refreshes_prediction_rows_appended_by_scorer(tmp_path) -> None:
    runtime = AmsterdamFrozenShadowRuntime(tmp_path)
    journal = tmp_path / "predictions.jsonl"
    journal.write_text(
        json.dumps(prediction(), sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    health = runtime.health()
    assert health["prediction_count"] == 1
    assert health["prediction_physical_row_count"] == 1


def test_exact_settlement_join(tmp_path) -> None:
    db = tmp_path / "weather.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE settlement_outcomes (settlement_outcome_id text, condition_id text, token_id text, final_price real)")
    conn.execute("INSERT INTO settlement_outcomes VALUES ('s1','condition-1','token-no',1.0)")
    conn.execute("INSERT INTO settlement_outcomes VALUES ('s2','condition-1','token-yes',0.0)")
    conn.commit(); conn.close()
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps(event("2026-08-30T10:10:00Z", 21)) + "\n")
    runtime = AmsterdamFrozenShadowRuntime(tmp_path / "journal", source_events_path=events, settlement_db=db)
    runtime.ingest_predictions([prediction()])
    result = runtime.materialize()
    assert result["settlements"] == 2
    second = runtime.materialize()
    assert second["demands"] == second["labels"] == second["settlements"] == second["markouts"] == 0
    linked = [row for row in runtime.settlements.rows.values() if row["status"] == "linked"]
    assert {row["settlement"]["settlement_outcome_id"] for row in linked} == {"s1", "s2"}
    assert runtime.health()["orders"] == runtime.health()["fills"] == runtime.health()["notional"] == 0
    assert runtime.health()["layer_b_status"] == "NOT_ESTIMABLE"
    assert runtime.health()["next_print_model_metrics"]["B2"]["raw_row_weighted"]["exact_accuracy"] == 1.0
    restarted = AmsterdamFrozenShadowRuntime(tmp_path / "journal", source_events_path=events, settlement_db=db)
    restarted_result = restarted.materialize()
    assert restarted_result["demands"] == restarted_result["labels"] == restarted_result["settlements"] == restarted_result["markouts"] == 0
