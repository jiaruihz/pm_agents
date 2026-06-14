import argparse
import json
import sqlite3

from scripts.ops.all_yes_underround_paper_exec_v0 import evaluate, gate, monitor, recording_ttl_audit


def _basket(recorded_at_utc="2026-06-13T18:32:00+00:00", snapshot_ts_utc="2026-06-13T18:30:53Z"):
    return {
        "recorded_at_utc": recorded_at_utc,
        "snapshot_ts_utc": snapshot_ts_utc,
    }


def _leg(basket_id: str, condition_id: str, bracket: str):
    return {"basket_id": basket_id, "condition_id": condition_id, "bracket": bracket}


def test_recording_ttl_audit_marks_fresh_basket_live_equivalent():
    audit = recording_ttl_audit(_basket(), 180)

    assert audit["ttl_equivalent"] is True
    assert audit["ttl_status"] == "live_equivalent"
    assert audit["recording_age_seconds"] == 67.0


def test_recording_ttl_audit_marks_stale_basket_observation_only():
    audit = recording_ttl_audit(_basket(recorded_at_utc="2026-06-13T18:53:26.208864+00:00"), 180)

    assert audit["ttl_equivalent"] is False
    assert audit["ttl_status"] == "stale_recording"
    assert audit["recording_age_seconds"] == 1353.209


def test_recording_ttl_audit_fails_closed_on_missing_timestamp():
    audit = recording_ttl_audit(_basket(snapshot_ts_utc=None), 180)

    assert audit["ttl_equivalent"] is False
    assert audit["ttl_status"] == "missing_timestamp"


def test_recording_ttl_audit_prefers_orderbook_fetched_at():
    basket = _basket(
        recorded_at_utc="2026-06-13T18:35:30+00:00",
        snapshot_ts_utc="2026-06-13T18:30:53Z",
    )
    basket["orderbook_fetched_at_utc_max"] = "2026-06-13T18:35:00Z"

    audit = recording_ttl_audit(basket, 180)

    assert audit["ttl_equivalent"] is True
    assert audit["ttl_status"] == "live_equivalent"
    assert audit["recording_age_seconds"] == 30.0


def test_recording_ttl_audit_uses_oldest_orderbook_leg_time():
    basket = _basket(
        recorded_at_utc="2026-06-13T18:35:30+00:00",
        snapshot_ts_utc="2026-06-13T18:30:53Z",
    )
    basket["orderbook_fetched_at_utc_min"] = "2026-06-13T18:30:00Z"
    basket["orderbook_fetched_at_utc_max"] = "2026-06-13T18:35:00Z"

    audit = recording_ttl_audit(basket, 180)

    assert audit["ttl_equivalent"] is False
    assert audit["ttl_status"] == "stale_recording"
    assert audit["recording_age_seconds"] == 330.0


def test_evaluate_dedupes_repeated_city_event_opportunities(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    db_path = tmp_path / "weather.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE settlements (condition_id TEXT PRIMARY KEY, bracket TEXT, final_price REAL, settlement_status TEXT)"
    )
    conn.commit()
    conn.close()

    baskets = [
        {
            "basket_id": "b1",
            "recorded_at_utc": "2026-06-13T18:31:00+00:00",
            "snapshot_ts_utc": "2026-06-13T18:30:00Z",
            "event_date": "2026-06-14",
            "city": "Istanbul",
            "event_slug": "highest-temperature-in-istanbul-on-june-14-2026",
        },
        {
            "basket_id": "b2",
            "recorded_at_utc": "2026-06-13T18:32:00+00:00",
            "snapshot_ts_utc": "2026-06-13T18:31:00Z",
            "event_date": "2026-06-14",
            "city": "Istanbul",
            "event_slug": "highest-temperature-in-istanbul-on-june-14-2026",
        },
        {
            "basket_id": "b3",
            "recorded_at_utc": "2026-06-13T18:33:00+00:00",
            "snapshot_ts_utc": "2026-06-13T18:32:00Z",
            "event_date": "2026-06-14",
            "city": "Jeddah",
            "event_slug": "highest-temperature-in-jeddah-on-june-14-2026",
        },
    ]
    with (run_dir / "paper_baskets.jsonl").open("w") as fh:
        for row in baskets:
            fh.write(json.dumps(row) + "\n")
    with (run_dir / "paper_leg_orders.jsonl").open("w") as fh:
        for idx, row in enumerate(baskets):
            fh.write(json.dumps(_leg(row["basket_id"], row["basket_id"] + "_c", str(20 + idx))) + "\n")

    result = evaluate(
        argparse.Namespace(
            run_dir=str(run_dir),
            db_path=str(db_path),
            max_snapshot_age_seconds=180.0,
            settlement_now_utc="2026-06-14T05:00:00Z",
            settlement_lag_days=1,
            settlement_pipeline_hour_utc=9,
            settlement_pipeline_minute_utc=20,
        )
    )

    assert result["raw_baskets"] == 3
    assert result["baskets"] == 2
    assert result["unique_opportunity_baskets"] == 2
    assert result["duplicate_opportunity_baskets"] == 1
    assert result["pending"] == 2
    assert result["ttl_equivalent_pending"] == 2
    assert result["ttl_equivalent_pending_reason_counts"] == {"missing_settlement_rows": 2}
    assert result["ttl_equivalent_pending_due_status_counts"] == {"not_due_missing_rows": 2}
    assert result["ttl_equivalent_pending_missing_settlement_legs"] == 2
    assert result["ttl_equivalent_pending_unresolved_settlement_legs"] == 0


def test_evaluate_excludes_shape_invalid_baskets_from_live_prep_counts(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    db_path = tmp_path / "weather.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE settlements (condition_id TEXT PRIMARY KEY, bracket TEXT, final_price REAL, settlement_status TEXT)"
    )
    conn.commit()
    conn.close()

    basket = {
        "basket_id": "b1",
        "recorded_at_utc": "2026-06-14T04:48:46+00:00",
        "snapshot_ts_utc": "2026-06-14T04:48:40Z",
        "event_date": "2026-06-14",
        "city": "Seattle",
        "event_slug": "highest-temperature-in-seattle-on-june-14-2026",
    }
    (run_dir / "paper_baskets.jsonl").write_text(json.dumps(basket) + "\n")
    with (run_dir / "paper_leg_orders.jsonl").open("w") as fh:
        fh.write(json.dumps(_leg("b1", "b1_c1", "14")) + "\n")
        fh.write(json.dumps(_leg("b1", "b1_c2", "14")) + "\n")

    result = evaluate(
        argparse.Namespace(
            run_dir=str(run_dir),
            db_path=str(db_path),
            max_snapshot_age_seconds=180.0,
            settlement_now_utc="2026-06-14T05:00:00Z",
            settlement_lag_days=1,
            settlement_pipeline_hour_utc=9,
            settlement_pipeline_minute_utc=20,
        )
    )

    assert result["unique_opportunity_baskets"] == 1
    assert result["shape_valid_baskets"] == 0
    assert result["shape_invalid_baskets"] == 1
    assert result["ttl_equivalent_baskets"] == 0
    assert result["ttl_equivalent_pending"] == 0
    assert result["shape_invalid_opportunities"][0]["basket_shape_blockers"] == ["duplicate_bracket"]


def test_gate_requires_settled_active_event_dates_not_just_basket_count(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    db_path = tmp_path / "weather.db"
    gate_path = tmp_path / "clob_gate.json"
    gate_path.write_text(json.dumps({"gate_pass": True}) + "\n")
    (run_dir / "last_cycle.json").write_text(
        json.dumps(
            {
                "scanner_candidate_count": 1,
                "guard_audit": [
                    {
                        "basket_id": "b_latest",
                        "guard": {"allow": True},
                    }
                ],
            }
        )
        + "\n"
    )
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE settlements (condition_id TEXT PRIMARY KEY, bracket TEXT, final_price REAL, settlement_status TEXT)"
    )
    conn.executemany(
        "INSERT INTO settlements VALUES (?, ?, ?, ?)",
        [
            ("b1_c", "31", 1.0, "settled"),
            ("b2_c", "32", 1.0, "settled"),
        ],
    )
    conn.commit()
    conn.close()

    baskets = [
        {
            "basket_id": "b1",
            "recorded_at_utc": "2026-06-14T18:31:00+00:00",
            "snapshot_ts_utc": "2026-06-14T18:30:00Z",
            "event_date": "2026-06-15",
            "city": "Istanbul",
            "event_slug": "highest-temperature-in-istanbul-on-june-15-2026",
            "shares_per_leg": 5,
            "basket_cost_usd": 4,
        },
        {
            "basket_id": "b2",
            "recorded_at_utc": "2026-06-14T18:32:00+00:00",
            "snapshot_ts_utc": "2026-06-14T18:31:00Z",
            "event_date": "2026-06-15",
            "city": "Jeddah",
            "event_slug": "highest-temperature-in-jeddah-on-june-15-2026",
            "shares_per_leg": 5,
            "basket_cost_usd": 4,
        },
    ]
    with (run_dir / "paper_baskets.jsonl").open("w") as fh:
        for row in baskets:
            fh.write(json.dumps(row) + "\n")
    with (run_dir / "paper_leg_orders.jsonl").open("w") as fh:
        fh.write(json.dumps(_leg("b1", "b1_c", "31")) + "\n")
        fh.write(json.dumps(_leg("b2", "b2_c", "32")) + "\n")

    result = gate(
        argparse.Namespace(
            run_dir=str(run_dir),
            db_path=str(db_path),
            gate_path=str(gate_path),
            max_snapshot_age_seconds=180.0,
            min_settled_baskets=2,
            min_settled_active_dates=2,
            min_positive_basket_rate=0.55,
            min_roi=0.02,
            settlement_now_utc="2026-06-14T05:00:00Z",
            settlement_lag_days=1,
            settlement_pipeline_hour_utc=9,
            settlement_pipeline_minute_utc=20,
        )
    )

    assert result["eval"]["ttl_equivalent_settled_exactly_one_winner"] == 2
    assert result["eval"]["ttl_equivalent_settled_active_event_dates"] == 1
    assert "forward_settled_baskets_ready" in {row["code"] for row in result["passed"]}
    assert "forward_settled_active_dates_low" in {row["code"] for row in result["blockers"]}


def test_monitor_includes_unique_opportunity_detail_and_fresh_cycle(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    db_path = tmp_path / "weather.db"
    gate_path = tmp_path / "clob_gate.json"
    gate_path.write_text(json.dumps({"gate_pass": True}) + "\n")
    (run_dir / "last_cycle.json").write_text(
        json.dumps({"scanner_candidate_count": 0, "appended_baskets": 0, "max_snapshot_age_seconds": 180.0}) + "\n"
    )
    (run_dir / "fresh_cycle.json").write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-06-14T04:48:46+00:00",
                "verdict": "FRESH_SNAPSHOT_CYCLE_RAN",
                "reason": "fresh",
                "executed_cycle": True,
                "snapshot_age_seconds": 1.5,
                "snapshot_path": "/tmp/snapshot.jsonl.gz",
            }
        )
        + "\n"
    )
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE settlements (condition_id TEXT PRIMARY KEY, bracket TEXT, final_price REAL, settlement_status TEXT)"
    )
    conn.commit()
    conn.close()
    basket = {
        "basket_id": "b1",
        "recorded_at_utc": "2026-06-14T04:48:46+00:00",
        "snapshot_ts_utc": "2026-06-14T04:48:40Z",
        "event_date": "2026-06-14",
        "city": "Seattle",
        "event_slug": "highest-temperature-in-seattle-on-june-14-2026",
        "shares_per_leg": 5,
        "total_yes_ask_cost": 0.978,
        "underround": 0.022,
        "basket_cost_usd": 4.89,
        "gross_profit_if_complete_usd": 0.11,
    }
    (run_dir / "paper_baskets.jsonl").write_text(json.dumps(basket) + "\n")
    (run_dir / "paper_leg_orders.jsonl").write_text(json.dumps(_leg("b1", "b1_c", "77")) + "\n")

    result = monitor(
        argparse.Namespace(
            run_dir=str(run_dir),
            db_path=str(db_path),
            gate_path=str(gate_path),
            max_snapshot_age_seconds=180.0,
            min_settled_baskets=20,
            min_settled_active_dates=7,
            min_positive_basket_rate=0.55,
            min_roi=0.02,
            settlement_now_utc="2026-06-14T05:00:00Z",
            settlement_lag_days=1,
            settlement_pipeline_hour_utc=9,
            settlement_pipeline_minute_utc=20,
        )
    )

    assert result["latest_fresh_cycle"]["verdict"] == "FRESH_SNAPSHOT_CYCLE_RAN"
    assert result["unique_opportunities"] == [
        {
            "recorded_at_utc": "2026-06-14T04:48:46+00:00",
            "event_date": "2026-06-14",
            "city": "Seattle",
            "event_slug": "highest-temperature-in-seattle-on-june-14-2026",
            "ttl_equivalent": True,
            "ttl_status": "live_equivalent",
            "basket_shape_valid": True,
            "basket_shape_blockers": [],
            "recording_age_seconds": 6.0,
            "settlement_eval_status": "pending",
            "settled_legs": 0,
            "missing_settlement_legs": 1,
            "unresolved_settlement_legs": 0,
            "pending_reason": "missing_settlement_rows",
            "pending_due_status": "not_due_missing_rows",
            "expected_settlement_after_utc": "2026-06-15T09:20:00+00:00",
            "seconds_until_expected_settlement": 102000.0,
            "winner_count": None,
            "winner_brackets": [],
            "total_yes_ask_cost": 0.978,
            "underround": 0.022,
            "basket_cost_usd": 4.89,
            "gross_profit_if_complete_usd": 0.11,
            "pnl_usd": None,
            "roi": None,
        }
    ]
    assert result["ttl_equivalent_pending_reason_counts"] == {"missing_settlement_rows": 1}
    assert result["ttl_equivalent_pending_due_status_counts"] == {"not_due_missing_rows": 1}
    assert result["ttl_equivalent_pending_settlement_audit"] == [
        {
            "recorded_at_utc": "2026-06-14T04:48:46+00:00",
            "event_date": "2026-06-14",
            "city": "Seattle",
            "event_slug": "highest-temperature-in-seattle-on-june-14-2026",
            "ttl_equivalent": True,
            "basket_shape_valid": True,
            "pending_reason": "missing_settlement_rows",
            "pending_due_status": "not_due_missing_rows",
            "expected_settlement_after_utc": "2026-06-15T09:20:00+00:00",
            "seconds_until_expected_settlement": 102000.0,
            "legs": None,
            "settled_legs": 0,
            "missing_settlement_legs": 1,
            "unresolved_settlement_legs": 0,
        }
    ]


def test_evaluate_separates_unresolved_settlement_rows_from_missing_rows(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    db_path = tmp_path / "weather.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE settlements (condition_id TEXT PRIMARY KEY, bracket TEXT, final_price REAL, settlement_status TEXT)"
    )
    conn.executemany(
        "INSERT INTO settlements VALUES (?, ?, ?, ?)",
        [
            ("b1_c1", "31", None, "pending"),
            ("b1_c2", "32", 0.0, "settled"),
        ],
    )
    conn.commit()
    conn.close()

    basket = {
        "basket_id": "b1",
        "recorded_at_utc": "2026-06-14T04:48:46+00:00",
        "snapshot_ts_utc": "2026-06-14T04:48:40Z",
        "event_date": "2026-06-14",
        "city": "Istanbul",
        "event_slug": "highest-temperature-in-istanbul-on-june-14-2026",
    }
    (run_dir / "paper_baskets.jsonl").write_text(json.dumps(basket) + "\n")
    with (run_dir / "paper_leg_orders.jsonl").open("w") as fh:
        fh.write(json.dumps(_leg("b1", "b1_c1", "31")) + "\n")
        fh.write(json.dumps(_leg("b1", "b1_c2", "32")) + "\n")

    result = evaluate(
        argparse.Namespace(
            run_dir=str(run_dir),
            db_path=str(db_path),
            max_snapshot_age_seconds=180.0,
            settlement_now_utc="2026-06-14T05:00:00Z",
            settlement_lag_days=1,
            settlement_pipeline_hour_utc=9,
            settlement_pipeline_minute_utc=20,
        )
    )

    assert result["ttl_equivalent_pending"] == 1
    assert result["ttl_equivalent_pending_reason_counts"] == {"settlement_rows_unresolved": 1}
    assert result["ttl_equivalent_pending_due_status_counts"] == {"not_due_unresolved_rows": 1}
    assert result["ttl_equivalent_pending_missing_settlement_legs"] == 0
    assert result["ttl_equivalent_pending_unresolved_settlement_legs"] == 1


def test_evaluate_marks_missing_settlement_rows_overdue_after_pipeline_time(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    db_path = tmp_path / "weather.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE settlements (condition_id TEXT PRIMARY KEY, bracket TEXT, final_price REAL, settlement_status TEXT)"
    )
    conn.commit()
    conn.close()

    basket = {
        "basket_id": "b1",
        "recorded_at_utc": "2026-06-13T23:08:54+00:00",
        "snapshot_ts_utc": "2026-06-13T23:08:50Z",
        "event_date": "2026-06-14",
        "city": "Istanbul",
        "event_slug": "highest-temperature-in-istanbul-on-june-14-2026",
    }
    (run_dir / "paper_baskets.jsonl").write_text(json.dumps(basket) + "\n")
    (run_dir / "paper_leg_orders.jsonl").write_text(json.dumps(_leg("b1", "b1_c1", "31")) + "\n")

    result = evaluate(
        argparse.Namespace(
            run_dir=str(run_dir),
            db_path=str(db_path),
            max_snapshot_age_seconds=180.0,
            settlement_now_utc="2026-06-15T10:00:00Z",
            settlement_lag_days=1,
            settlement_pipeline_hour_utc=9,
            settlement_pipeline_minute_utc=20,
        )
    )

    assert result["ttl_equivalent_pending_reason_counts"] == {"missing_settlement_rows": 1}
    assert result["ttl_equivalent_pending_due_status_counts"] == {"overdue_missing_rows": 1}
    assert result["ttl_equivalent_pending_settlement_audit"][0]["expected_settlement_after_utc"] == "2026-06-15T09:20:00+00:00"
    assert result["ttl_equivalent_pending_settlement_audit"][0]["seconds_until_expected_settlement"] == -2400.0
