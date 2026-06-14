import argparse
import json
import sqlite3

from scripts.ops.all_yes_underround_paper_exec_v0 import evaluate, gate, recording_ttl_audit


def _basket(recorded_at_utc="2026-06-13T18:32:00+00:00", snapshot_ts_utc="2026-06-13T18:30:53Z"):
    return {
        "recorded_at_utc": recorded_at_utc,
        "snapshot_ts_utc": snapshot_ts_utc,
    }


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
        for row in baskets:
            fh.write(json.dumps({"basket_id": row["basket_id"], "condition_id": row["basket_id"] + "_c"}) + "\n")

    result = evaluate(
        argparse.Namespace(
            run_dir=str(run_dir),
            db_path=str(db_path),
            max_snapshot_age_seconds=180.0,
        )
    )

    assert result["raw_baskets"] == 3
    assert result["baskets"] == 2
    assert result["unique_opportunity_baskets"] == 2
    assert result["duplicate_opportunity_baskets"] == 1
    assert result["pending"] == 2
    assert result["ttl_equivalent_pending"] == 2


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
        fh.write(json.dumps({"basket_id": "b1", "condition_id": "b1_c"}) + "\n")
        fh.write(json.dumps({"basket_id": "b2", "condition_id": "b2_c"}) + "\n")

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
        )
    )

    assert result["eval"]["ttl_equivalent_settled_exactly_one_winner"] == 2
    assert result["eval"]["ttl_equivalent_settled_active_event_dates"] == 1
    assert "forward_settled_baskets_ready" in {row["code"] for row in result["passed"]}
    assert "forward_settled_active_dates_low" in {row["code"] for row in result["blockers"]}
