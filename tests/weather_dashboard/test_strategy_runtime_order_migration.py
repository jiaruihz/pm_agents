import json
import sqlite3

import pytest

import weather_dashboard.legacy_migration.strategy_runtime_orders as strategy_runtime_orders
from weather_dashboard.legacy_migration import live_cycle
from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from weather_dashboard.db.connection import apply_pragmas
from weather_dashboard.legacy_migration.strategy_runtime_orders import (
    iter_strategy_order_paths,
    migrate_strategy_runtime_orders,
)
from scripts.etl.build_weather_fact_trades import (
    _load_settlements,
    _match_settlement,
    build as build_fact_trades,
    write_db_incremental as write_fact_trades_incremental,
)
from src.strategies.runtime.sync import sync_instance_specs


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_pragmas(conn)
    apply_schema_canonical(conn)
    sync_instance_specs(conn)
    return conn


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_execution_identity_infers_profile_for_pre_restart_live_rows():
    assert live_cycle._execution_identity(
        {
            "execution_mode": "tiny_live_split_taker_maker_probe",
            "execution_policy": "current_yes_heat_death_maker_probe_v1",
            "child_order_role": "maker",
            "maker_only": True,
        }
    ) == (
        "split_taker_maker_chase_v1",
        "current_yes_heat_death_maker_probe_v1",
        "maker_chase_then_taker_fallback_v1",
        "maker",
    )


def test_decision_snapshot_timestamp_has_priority_over_order_creation():
    row = strategy_runtime_orders._enrich_runtime_order(
        {
            "city": "Busan",
            "target_date": "2026-07-15",
            "token_id": "token",
            "decision_snapshot_ts_utc": "2026-07-15T04:01:22Z",
            "created_at_utc": "2026-07-15T04:05:00Z",
        },
        {"snapshot_ts_utc": "2026-07-15T00:59:59Z"},
    )

    assert row["snapshot_ts_utc"] == "2026-07-15T04:01:22Z"
    assert live_cycle._snapshot_ts(row) == "2026-07-15T04:01:22Z"


def test_runtime_order_uses_selected_token_probability_for_core_carry_signal():
    row = strategy_runtime_orders._enrich_runtime_order(
        {
            "city": "Wellington",
            "target_date": "2026-08-05",
            "signal_side": "BUY_YES",
            "order_side": "BUY",
            "model_token_probability": 0.962749510235,
            "model_p_yes_raw": 0.0,
            "model_p_yes_used": 0.0,
            "best_ask": 0.91,
            "edge": 0.0,
            "created_at_utc": "2026-08-05T00:00:00Z",
        },
        None,
    )

    assert row["model_p_yes"] == 0.962749510235
    assert row["market_price"] == 0.91
    assert row["edge"] == pytest.approx(0.052749510235)


def test_heat_death_legacy_rows_infer_historical_plan_config():
    assert strategy_runtime_orders._runtime_plan_config_id(
        {
            "strategy_instance": "current_yes_heat_death_tiny_live_h2_early_dislocation_v1",
            "execution_mode": "tiny_live_taker_probe",
            "size": 10,
        },
        "fallback",
    ) == "current_yes_heat_death_tiny_live_h2_early_dislocation_v1_fixed10"
    assert strategy_runtime_orders._runtime_plan_config_id(
        {
            "strategy_instance": "current_yes_heat_death_tiny_live_h2_early_dislocation_v1",
            "execution_mode": "tiny_live_split_taker_maker_probe",
            "size": 5,
        },
        "fallback",
    ) == "current_yes_heat_death_tiny_live_h2_early_dislocation_v3_split_taker_maker"


def test_blocked_lifecycle_attempt_without_exchange_order_is_not_canonical_order(tmp_path):
    order_path = tmp_path / "runtime" / "weather_edge_v1" / "heat" / "live_orders.jsonl"
    _write_jsonl(
        order_path,
        [
            {
                "record_type": "weather_edge_live_order",
                "strategy_instance": "heat",
                "execution_id": "e" * 64,
                "status": "blocked",
                "source_order_id": "0xsource",
                "execution_action": "h1_maker_cancel_stale_thesis",
                "created_at_utc": "2026-07-18T10:00:00Z",
                "city": "Jeddah",
                "target_date": "2026-07-18",
                "bracket": "41",
                "token_id": "token",
                "signal_side": "BUY_YES",
                "order_side": "BUY",
            }
        ],
    )
    conn = _conn()
    try:
        report = migrate_strategy_runtime_orders(conn, order_path=order_path)

        assert report.orders == 0
        assert report.skipped_reasons == {"non_exchange_order_lifecycle_attempt": 1}
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
    finally:
        conn.close()


def test_runtime_journal_preserves_plan_config_transition(tmp_path):
    order_path = tmp_path / "runtime" / "weather_edge_v1" / "heat" / "live_orders.jsonl"
    base = {
        "record_type": "weather_edge_live_order",
        "strategy_instance": "heat",
        "venue": "polymarket_clob",
        "status": "submitted",
        "target_date": "2026-07-18",
        "city": "Warsaw",
        "bracket": "27",
        "unit": "C",
        "condition_id": "0xcondition",
        "market_id": "0xmarket",
        "token_id": "token",
        "question": "Will Warsaw be 27C?",
        "signal_side": "BUY_YES",
        "order_side": "BUY",
        "limit_price": 0.95,
        "size": 5,
        "forecast_source": "open_meteo_live_ecmwf",
        "model_version": "ecmwf",
    }
    _write_jsonl(
        order_path,
        [
            {
                **base,
                "execution_id": "1" * 64,
                "order_id": "0xorder1",
                "created_at_utc": "2026-07-18T10:00:00Z",
                "decision_snapshot_ts_utc": "2026-07-18T09:59:00Z",
                "config_id": "heat-config-v2",
            },
            {
                **base,
                "execution_id": "2" * 64,
                "order_id": "0xorder2",
                "created_at_utc": "2026-07-18T11:00:00Z",
                "decision_snapshot_ts_utc": "2026-07-18T10:59:00Z",
                "config_id": "heat-config-v3",
            },
        ],
    )
    conn = _conn()
    try:
        report = migrate_strategy_runtime_orders(conn, order_path=order_path)

        assert report.orders == 2
        assert {
            row[0] for row in conn.execute("SELECT DISTINCT config_id FROM plans")
        } == {"heat-config-v2", "heat-config-v3"}
        assert conn.execute("SELECT config_id FROM runs").fetchone()[0].startswith("live_weather_edge_v1_")
    finally:
        conn.close()


def test_migrate_mac_live_strategy_order_file_as_live(tmp_path):
    root = tmp_path / "runtime" / "weather_edge_v1"
    order_path = root / "live" / "low_price_yes_lottery_tiny_live_v1_orders.jsonl"
    _write_jsonl(
        order_path,
        [
            {
                "record_type": "weather_edge_live_order",
                "strategy_id": "low_price_yes_lottery_tiny_live_v1",
                "strategy_instance": "low_price_yes_lottery_tiny_live_v1",
                "execution_id": "a" * 64,
                "order_id": "0xabc",
                "venue": "polymarket_clob",
                "status": "submitted",
                "created_at_utc": "2026-07-03T20:21:59+00:00",
                "target_date": "2026-07-04",
                "city": "Paris",
                "city_pool": "t2_research",
                "icao": "LFPG",
                "bracket": "32",
                "unit": "C",
                "side": "BUY_YES",
                "order_side": "BUY_YES",
                "model_version": "gfs",
                "model_p_yes": 0.3709,
                "forecast_source": "open_meteo_live_gfs",
                "condition_id": "0xcondition",
                "market_id": "2765777",
                "token_id": "token-yes",
                "market_price": 0.061,
                "limit_price": 0.061,
                "posted_price": 0.061,
                "shares": 13.0,
                "notional": 0.8,
                "edge": 0.3054,
                "execution_profile": "single_side_maker_v1",
                "execution_policy": "maker_first_taker_fallback",
                "order_lifecycle_policy": "maker_until_data_update",
                "child_order_role": "single",
                "comparison_group_id": "comparison-1",
                "maker_only": True,
                "exchange_response": {"place": {"orderID": "0xabc", "success": True}},
            }
        ],
    )

    paths = iter_strategy_order_paths([root])
    assert order_path in paths

    conn = _conn()
    try:
        report = migrate_strategy_runtime_orders(conn, order_path=order_path)
        assert report.skipped_rows == 0
        assert report.orders == 1

        run = conn.execute("SELECT execution_mode, state, tags FROM runs").fetchone()
        assert run["execution_mode"] == "live"
        assert run["state"] == "live"
        assert "low_price_yes_lottery_tiny_live_v1" in json.loads(run["tags"])

        order = conn.execute("SELECT venue, status, order_id FROM orders").fetchone()
        assert dict(order) == {
            "venue": "polymarket_clob",
            "status": "submitted",
            "order_id": "0xabc",
        }
        plan = conn.execute(
            """
            SELECT execution_profile, execution_policy, order_lifecycle_policy,
                   child_order_role, comparison_group_id, maker_only
            FROM plans
            """
        ).fetchone()
        assert dict(plan) == {
            "execution_profile": "single_side_maker_v1",
            "execution_policy": "maker_first_taker_fallback",
            "order_lifecycle_policy": "maker_until_data_update",
            "child_order_role": "single",
            "comparison_group_id": "comparison-1",
            "maker_only": 1,
        }
    finally:
        conn.close()


def test_migrate_mac_live_sell_yes_exit_order(tmp_path):
    root = tmp_path / "runtime" / "weather_edge_v1"
    order_path = root / "live" / "low_price_yes_take_profit_exit_v1_orders.jsonl"
    _write_jsonl(
        order_path,
        [
            {
                "record_type": "weather_edge_live_order",
                "strategy_id": "low_price_yes_take_profit_exit_v1",
                "strategy_instance": "low_price_yes_take_profit_exit_v1",
                "execution_id": "b" * 64,
                "order_id": "0xexit",
                "venue": "polymarket_clob",
                "status": "submitted",
                "created_at_utc": "2026-07-03T20:21:59+00:00",
                "target_date": "2026-07-03",
                "city": "London",
                "city_pool": "t1_trading",
                "icao": "EGLL",
                "bracket": "28",
                "unit": "C",
                "signal_side": "SELL_YES",
                "order_side": "SELL",
                "model_version": "gfs",
                "forecast_source": "open_meteo_live_gfs",
                "condition_id": "0xcondition",
                "market_id": "0xmarket",
                "token_id": "token-yes",
                "limit_price": 0.2,
                "posted_price": 0.2,
                "size": 12.5,
                "posted_notional": 2.5,
                "exchange_response": {"place": {"orderID": "0xexit", "success": True}},
            }
        ],
    )

    conn = _conn()
    try:
        report = migrate_strategy_runtime_orders(conn, order_path=order_path)
        assert report.skipped_rows == 0
        assert report.orders == 1

        row = conn.execute(
            """
            SELECT s.signal_side, p.order_side AS plan_order_side, o.order_side, o.cost_usd
            FROM orders o
            JOIN plans p ON p.plan_id = o.plan_id
            JOIN signals s ON s.signal_id = p.signal_id
            """
        ).fetchone()
        assert dict(row) == {
            "signal_side": "YES",
            "plan_order_side": "SELL_YES",
            "order_side": "SELL_YES",
            "cost_usd": 2.5,
        }
    finally:
        conn.close()


def test_snapshot_lookup_skips_rows_with_complete_market_lineage(monkeypatch):
    def fail_snapshot_scan(_cutoff):
        raise AssertionError("snapshot scan should not run")

    monkeypatch.setattr(strategy_runtime_orders, "_snapshot_files_before", fail_snapshot_scan)
    lookup = strategy_runtime_orders._build_snapshot_lookup(
        [
            {
                "target_date": "2026-07-09",
                "condition_id": "0xcondition",
                "token_id": "token-no",
                "question": "Will the highest temperature in Helsinki be 17°C on July 9?",
                "t_minus_1_no_bracket_c": 17,
            }
        ]
    )
    assert lookup == {}


def test_migrate_fast_source_prev_no_matched_fok_order_defers_fill_to_clob_sync(tmp_path):
    order_path = tmp_path / "output" / "fast_source_prev_no_trial" / "orders.jsonl"
    _write_jsonl(
        order_path,
        [
            {
                "schema_version": "fast_source_prev_no_trial_v1",
                "strategy_id": "fast_source_prev_no_trial_v1",
                "strategy_instance": "fast_source_prev_no_trial_v1",
                "city": "Helsinki",
                "target_date": "2026-07-09",
                "condition_id": "0xb1b1e78205c2ea08a92cd1248d68f3ae55ba06d9e827729454987f1c0b70b47d",
                "market_id": "2826049",
                "token_id": "98617391282593622490977003288012573295810667097836660592616146148184755841068",
                "question": "Will the highest temperature in Helsinki be 17°C on July 9?",
                "event_key": "Helsinki|2026-07-09|fmi|2026-07-09T13:40:00+00:00|18|17|17",
                "order_side": "BUY",
                "size": 5.0,
                "best_ask": 0.82,
                "ask_size": 8.76,
                "limit_price": 0.92,
                "submitted_notional_usd": 4.6,
                "live_submit_status": "submitted",
                "order_id": "0x847bf2533bd18fdf08a2ff7771be59068b7d5eb6a33d9ffe21d52e374ba7fe17",
                "live_attempt_ts_utc": "2026-07-09T13:46:08.750690+00:00",
                "ts_utc": "2026-07-09T13:46:07.986599+00:00",
                "t_minus_1_no_bracket_c": 17,
                "source": "fmi",
                "source_obs_ts_utc": "2026-07-09T13:40:00+00:00",
                "source_detect_ts_utc": "2026-07-09T13:45:48.414916+00:00",
                "source_temp_c": 17.5,
                "source_round_c": 18,
                "latest_metar_report_ts_utc": "2026-07-09T13:20:00+00:00",
                "latest_metar_temp_c": 16.0,
                "metar_running_max_round_c": 17,
                "exchange_response": {
                    "place": {
                        "status": "matched",
                        "success": True,
                        "orderID": "0x847bf2533bd18fdf08a2ff7771be59068b7d5eb6a33d9ffe21d52e374ba7fe17",
                        "makingAmount": "4.599999",
                        "takingAmount": "5.609755",
                    }
                },
            }
        ],
    )

    conn = _conn()
    try:
        report = migrate_strategy_runtime_orders(conn, order_path=order_path)
        assert report.skipped_rows == 0
        assert report.orders == 1
        assert report.fills == 0

        row = conn.execute(
            """
            SELECT s.city, s.bracket, s.unit, s.signal_side,
                   o.venue, o.order_side, o.status, o.clob_status
            FROM orders o
            JOIN plans p ON p.plan_id = o.plan_id
            JOIN signals s ON s.signal_id = p.signal_id
            """
        ).fetchone()
        assert dict(row) == {
            "city": "Helsinki",
            "bracket": "17",
            "unit": "C",
            "signal_side": "NO",
            "venue": "polymarket_clob",
            "order_side": "BUY_NO",
            "status": "submitted",
            "clob_status": "matched",
        }
        assert conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0
    finally:
        conn.close()


def test_enrich_runtime_order_recovers_condition_hash_from_market_id(monkeypatch):
    condition_id = "0x" + "a" * 64
    monkeypatch.setattr(strategy_runtime_orders, "_enrich_from_gamma_market", lambda row: None)

    row = strategy_runtime_orders._enrich_runtime_order(
        {
            "market_id": condition_id,
            "target_date": "2026-07-11",
            "city": "Seoul",
            "token_id": "token",
            "created_at_utc": "2026-07-11T00:00:00Z",
        },
        None,
    )

    assert row["condition_id"] == condition_id


def test_snapshot_filename_uses_beijing_wall_clock():
    path = strategy_runtime_orders.Path("snapshot_20260704_2248.json")
    assert strategy_runtime_orders._snapshot_file_ts(path).isoformat() == (
        "2026-07-04T14:48:00+00:00"
    )


def test_snapshot_lookup_uses_latest_record_before_first_signal_order(
    tmp_path, monkeypatch
):
    snapshot_root = tmp_path / "snapshots"
    snapshot_root.mkdir()
    token = "token-causal"
    base_record = {
        "city": "Shanghai",
        "event_date": "2026-07-05",
        "bracket": "35",
        "token_id": token,
        "model_prob": 0.4022,
    }
    (snapshot_root / "snapshot_20260704_2248.json").write_text(
        json.dumps(
            {
                "records": [
                    {**base_record, "snapshot_ts_utc": "2026-07-04T14:48:08Z"}
                ]
            }
        ),
        encoding="utf-8",
    )
    (snapshot_root / "snapshot_20260704_2304.json").write_text(
        json.dumps(
            {
                "records": [
                    {**base_record, "snapshot_ts_utc": "2026-07-04T15:04:23Z"}
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(strategy_runtime_orders, "SNAPSHOT_DIRS", (snapshot_root,))
    rows = [
        {
            "signal_id": "same-signal",
            "execution_id": "execution-1",
            "city": "Shanghai",
            "target_date": "2026-07-05",
            "bracket": "35",
            "token_id": token,
            "model_p_yes_used": 0.4022,
            "created_at_utc": "2026-07-04T15:00:22Z",
        },
        {
            "signal_id": "same-signal",
            "execution_id": "execution-2",
            "city": "Shanghai",
            "target_date": "2026-07-05",
            "bracket": "35",
            "token_id": token,
            "model_p_yes_used": 0.4022,
            "created_at_utc": "2026-07-04T15:03:00Z",
        },
    ]

    lookup = strategy_runtime_orders._build_snapshot_lookup(rows)

    assert set(lookup) == {"execution_id:execution-1", "execution_id:execution-2"}
    assert {
        row["snapshot_ts_utc"] for row in lookup.values()
    } == {"2026-07-04T14:48:08Z"}
    assert {
        strategy_runtime_orders._enrich_runtime_order(row, lookup[f"execution_id:{row['execution_id']}"])[
            "signal_snapshot_lineage_status"
        ]
        for row in rows
    } == {"reconstructed_causal"}


def test_snapshot_lookup_never_falls_forward(tmp_path, monkeypatch):
    snapshot_root = tmp_path / "snapshots"
    snapshot_root.mkdir()
    (snapshot_root / "snapshot_20260704_2304.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "city": "Shanghai",
                        "event_date": "2026-07-05",
                        "bracket": "35",
                        "token_id": "token-future",
                        "snapshot_ts_utc": "2026-07-04T15:04:23Z",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(strategy_runtime_orders, "SNAPSHOT_DIRS", (snapshot_root,))
    row = {
        "signal_id": "future-signal",
        "execution_id": "future-execution",
        "city": "Shanghai",
        "target_date": "2026-07-05",
        "bracket": "35",
        "token_id": "token-future",
        "created_at_utc": "2026-07-04T15:00:22Z",
    }

    assert strategy_runtime_orders._build_snapshot_lookup([row]) == {}
    enriched = strategy_runtime_orders._enrich_runtime_order(row, None)
    assert enriched["snapshot_ts_utc"] == "2026-07-04T15:00:22Z"
    assert enriched["signal_snapshot_lineage_status"] == (
        "blocked_no_signal_snapshot"
    )


def test_fact_trades_uses_sell_side_cashflow_and_pnl(tmp_path):
    root = tmp_path / "runtime" / "weather_edge_v1"
    order_path = root / "live" / "low_price_yes_take_profit_exit_v1_orders.jsonl"
    _write_jsonl(
        order_path,
        [
            {
                "record_type": "weather_edge_live_order",
                "strategy_id": "low_price_yes_take_profit_exit_v1",
                "strategy_instance": "low_price_yes_take_profit_exit_v1",
                "execution_id": "c" * 64,
                "order_id": "0xexit-filled",
                "venue": "polymarket_clob",
                "status": "filled",
                "created_at_utc": "2026-07-03T20:21:59+00:00",
                "target_date": "2026-07-03",
                "city": "London",
                "city_pool": "t1_trading",
                "icao": "EGLL",
                "bracket": "28",
                "unit": "C",
                "signal_side": "SELL_YES",
                "order_side": "SELL",
                "model_version": "gfs",
                "forecast_source": "open_meteo_live_gfs",
                "condition_id": "0xcondition",
                "market_id": "0xmarket",
                "token_id": "token-yes",
                "limit_price": 0.2,
                "posted_price": 0.2,
                "size": 10.0,
                "posted_notional": 2.0,
            }
        ],
    )

    conn = _conn()
    try:
        migrate_strategy_runtime_orders(conn, order_path=order_path)
        order = conn.execute("SELECT execution_id, order_id FROM orders").fetchone()
        conn.execute(
            """
            INSERT INTO fills (
                fill_id, execution_id, order_id, filled_shares, filled_price,
                fees_usd, status, filled_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("fill-sell", order["execution_id"], order["order_id"], 10.0, 0.2, 0.0, "filled", "2026-07-03T20:22:00Z"),
        )
        conn.execute(
            """
            INSERT INTO settlements (
                settlement_id, target_date, condition_id, market_id, bracket,
                token_id, final_price, settlement_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("settle-sell", "2026-07-03", "0xcondition", "0xmarket", "28", "token-yes", 0.0, "settled"),
        )
        conn.execute(
            """
            INSERT INTO fill_fee_adjustments (
                adjustment_id, fill_id, fee_delta_usd, fee_source,
                fee_evidence_class, transaction_hash, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("fee-adjustment", "fill-sell", 0.05, "public_activity_tx_exact", "exact", "0xtx", "{}"),
        )
        signal_id = conn.execute("SELECT signal_id FROM signals").fetchone()[0]
        conn.execute(
            """
            INSERT INTO signal_clock_adjustments (
                adjustment_id, signal_id, corrected_snapshot_ts_utc,
                timestamp_source, timestamp_evidence_class, lineage_status,
                source_snapshot_ref, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "clock-adjustment", signal_id, "2026-07-03T20:20:00Z",
                "strategy_snapshot_record", "reconstructed",
                "reconstructed_causal", "/snapshot.json", "{}",
            ),
        )

        rows, alerts = build_fact_trades(conn)
        assert not [alert for alert in alerts if alert.startswith("SIDE_MISMATCH")]
        assert len(rows) == 1
        assert rows[0]["side"] == "SELL_YES"
        assert rows[0]["signal_side"] == "YES"
        assert rows[0]["order_status"] == "filled"
        assert rows[0]["cost_usd"] == 2.0
        assert rows[0]["base_fees_usd"] == 0.0
        assert rows[0]["fee_adjustment_usd"] == 0.05
        assert rows[0]["fees_usd"] == 0.05
        assert rows[0]["pnl_usd_at_fill"] == 1.95
        assert rows[0]["original_snapshot_ts_utc"] == "2026-07-03T20:21:59Z"
        assert rows[0]["snapshot_ts_utc"] == "2026-07-03T20:20:00Z"
        assert rows[0]["signal_clock_evidence_class"] == "reconstructed"
        assert rows[0]["signal_clock_lineage_status"] == "reconstructed_causal"

        targeted_rows, targeted_alerts = build_fact_trades(
            conn,
            fill_ids=["fill-sell"],
        )
        assert not targeted_alerts
        assert [row["fill_id"] for row in targeted_rows] == ["fill-sell"]
        missing_rows, _ = build_fact_trades(conn, fill_ids=["missing-fill"])
        assert missing_rows == []

    finally:
        conn.close()


def test_fact_trades_incremental_update_preserves_older_schema():
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE fact_trades (fill_id TEXT PRIMARY KEY, settled INTEGER, pnl_usd_at_fill REAL)"
        )
        conn.execute(
            "INSERT INTO fact_trades VALUES ('fill-1', 0, NULL)"
        )
        conn.commit()

        write_fact_trades_incremental(
            conn,
            [
                {
                    "fill_id": "fill-1",
                    "settled": 1,
                    "pnl_usd_at_fill": 1.25,
                    "new_builder_column": "ignored-until-explicit-schema-rebuild",
                }
            ],
        )

        assert conn.execute(
            "SELECT settled, pnl_usd_at_fill FROM fact_trades WHERE fill_id='fill-1'"
        ).fetchone() == (1, 1.25)
    finally:
        conn.close()


def test_fact_settlement_lookup_uses_token_complete_outcomes_fallback():
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO settlement_outcomes (
                settlement_outcome_id, source_system, city, target_date, bracket,
                condition_id, market_id, token_id, final_price, settlement_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "outcome-only",
                "pm_history",
                "Munich",
                "2026-07-15",
                "27",
                "0xcondition",
                "0xmarket",
                "token-only",
                1.0,
                "settled",
            ),
        )
        by_token, by_cid, by_mid = _load_settlements(conn)
        method, row, count = _match_settlement(
            "token-only",
            "2026-07-15",
            "0xcondition",
            "0xmarket",
            "27",
            by_token,
            by_cid,
            by_mid,
        )

        assert method == "token"
        assert count == 1
        assert row is not None
        assert row["settlement_id"] == "outcome-only"
        assert row["source_table"] == "settlement_outcomes"
    finally:
        conn.close()


def test_fact_settlement_lookup_prefers_exact_settled_correction_over_stale_token_gap():
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO settlements (
                settlement_id, target_date, condition_id, market_id, bracket,
                token_id, final_price, settlement_status, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "old-token-gap", "2026-08-11", None, None, "12",
                "token-12", 0.775, "missing_bracket", "2026-08-11T17:11:32Z",
                "corrected-exact", "2026-08-11", "0xcondition", "0xmarket", "12",
                None, 1.0, "settled", "2026-08-12T17:21:06Z",
            ),
        )
        by_token, by_cid, by_mid = _load_settlements(conn)
        method, row, count = _match_settlement(
            "token-12", "2026-08-11", "0xcondition", "0xmarket", "12",
            by_token, by_cid, by_mid,
        )

        assert method == "fallback"
        assert count == 2
        assert row is not None
        assert row["settlement_id"] == "corrected-exact"
        assert row["settlement_status"] == "settled"
    finally:
        conn.close()
