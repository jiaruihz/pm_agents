import sqlite3

import pytest

from src.strategies.weather_edge_v1.ids import (
    make_execution_id,
    make_paper_order_id,
    make_plan_id,
    make_signal_id,
)
from weather_dashboard.db.apply_schema_canonical import (
    SCHEMA_VERSION,
    apply_schema_canonical,
    init_db_canonical,
)
from weather_dashboard.db.connection import apply_pragmas, get_conn
from weather_dashboard.ingest.information_events import ingest_information_events
from weather_data_feed.information_events import build_information_event


@pytest.fixture
def tmp_db_canonical():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_pragmas(conn)
    apply_schema_canonical(conn)
    yield conn
    conn.close()


def test_canonical_tables_exist(tmp_db_canonical):
    tables = [
        "schema_version",
        "code_versions",
        "strategy_config",
        "universes",
        "runs",
        "signals",
        "plans",
        "orders",
        "fills",
        "fill_fee_adjustments",
        "fill_price_adjustments",
        "fill_timestamp_adjustments",
        "settlements",
        "settlement_outcomes",
        "weather_observation_events",
        "weather_information_events",
        "weather_state_checkpoints",
        "weather_intraday_state_rows",
        "run_artifacts",
        "run_alerts",
        "ingestion_log",
    ]
    for table in tables:
        row = tmp_db_canonical.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        assert row is not None, f"Table {table} not found"


def test_canonical_schema_version_written(tmp_db_canonical):
    row = tmp_db_canonical.execute("SELECT version, description FROM schema_version").fetchone()
    assert row["version"] == SCHEMA_VERSION


def test_first_seen_event_and_checkpoint_schema_preserve_late_backfill_boundary(tmp_db_canonical):
    event_id = "e" * 64
    tmp_db_canonical.execute(
        """
        INSERT INTO weather_information_events (
            information_event_id, event_kind, event_role, source, city,
            content_key, payload_hash, ingested_at_utc, pit_lineage_class,
            original_first_seen_unknown
        ) VALUES (?, 'observation', 'new_content', 'aviationweather_metar', 'Atlanta',
                  'KATL|2026-07-28T12:00:00Z', 'payload', '2026-07-28T16:00:00Z',
                  'late_backfill_first_seen_unknown', 1)
        """,
        (event_id,),
    )
    tmp_db_canonical.execute(
        """
        INSERT INTO weather_state_checkpoints (
            state_checkpoint_id, city, target_date, trigger_event_id, as_of_ts_utc,
            input_event_set_hash, feature_schema_version, feature_version_manifest,
            pit_provenance, checkpoint_status, created_at_utc
        ) VALUES (?, 'Atlanta', '2026-07-28', ?, '2026-07-28T16:00:00Z',
                  'inputs', 'feature_frame_v1', '{}', 'late_backfill_first_seen_unknown',
                  'blocked_missing_required_identity', '2026-07-28T16:00:00Z')
        """,
        ("c" * 64, event_id),
    )

    assert tmp_db_canonical.execute("SELECT count(*) FROM weather_state_checkpoints").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        tmp_db_canonical.execute(
            """
            INSERT INTO weather_information_events (
                information_event_id, event_kind, event_role, source, city,
                content_key, payload_hash, first_seen_at_utc, ingested_at_utc,
                pit_lineage_class, original_first_seen_unknown
            ) VALUES ('bad-late', 'observation', 'new_content', 'aviationweather_metar', 'Atlanta',
                      'bad', 'payload-bad', '2026-07-28T12:00:00Z', '2026-07-28T16:00:00Z',
                      'late_backfill_first_seen_unknown', 1)
            """
        )


def test_information_event_ingest_is_append_only_for_duplicate_delivery(tmp_db_canonical):
    first = build_information_event(
        event_kind="observation",
        event_role="new_content",
        source="aviationweather",
        city="Atlanta",
        station_id="KATL",
        provider_item_id="KATL-20260728-1200",
        content_key="KATL|2026-07-28T12:00:00Z",
        normalized_payload={"raw_metar": "METAR KATL 281200Z 00000KT 25/20"},
        detected_at_utc="2026-07-28T12:00:03Z",
        first_seen_at_utc="2026-07-28T12:00:03Z",
        available_at_utc="2026-07-28T12:00:04Z",
        pit_lineage_class="collector_exact",
    )
    duplicate = {**first, "detected_at_utc": "2026-07-28T12:02:03Z", "available_at_utc": "2026-07-28T12:02:04Z"}

    assert ingest_information_events(tmp_db_canonical, [first]) == {"inserted": 1, "duplicates": 0}
    assert ingest_information_events(tmp_db_canonical, [duplicate]) == {"inserted": 0, "duplicates": 1}
    stored = tmp_db_canonical.execute("SELECT * FROM weather_information_events").fetchone()
    assert stored["first_seen_at_utc"] == "2026-07-28T12:00:03Z"
    assert stored["available_at_utc"] == "2026-07-28T12:00:04Z"


def test_fill_fee_lineage_is_v15_compatible(tmp_db_canonical):
    columns = {
        row["name"] for row in tmp_db_canonical.execute("PRAGMA table_info(fills)")
    }
    assert {"fee_source", "fee_rate", "fee_metadata_json", "transaction_hash"} <= columns
    assert max(
        row["version"] for row in tmp_db_canonical.execute("SELECT version FROM schema_version")
    ) >= 15


def test_execution_profile_plan_lineage_is_v16(tmp_db_canonical):
    columns = {
        row["name"] for row in tmp_db_canonical.execute("PRAGMA table_info(plans)")
    }
    assert {
        "execution_profile",
        "execution_policy",
        "order_lifecycle_policy",
        "child_order_role",
        "comparison_group_id",
        "maker_only",
    } <= columns
    assert max(
        row["version"] for row in tmp_db_canonical.execute("SELECT version FROM schema_version")
    ) >= 16


def test_canonical_schema_has_no_legacy_field_names(tmp_db_canonical):
    schema_rows = tmp_db_canonical.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
    ).fetchall()
    schema = "\n".join(row["sql"] for row in schema_rows)

    for legacy_name in ("event_date", "model_prob", "market_yes_price", "final_yes"):
        assert legacy_name not in schema


def test_canonical_rejects_legacy_order_side(tmp_db_canonical):
    with pytest.raises(sqlite3.IntegrityError):
        tmp_db_canonical.execute(
            """
            INSERT INTO orders (
                execution_id, order_id, run_id, plan_id, venue, order_side,
                entry_price, shares, cost_usd, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("c" * 64, "ord", "run", "plan", "paper", "BUY", 0.4, 10, 4, "filled"),
        )


def test_canonical_accepts_sell_exit_order_side(tmp_db_canonical):
    tmp_db_canonical.execute("PRAGMA foreign_keys=OFF")
    tmp_db_canonical.execute(
        """
        INSERT INTO plans (
            plan_id, run_id, signal_id, config_id, order_side,
            execution_policy
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("p" * 64, "run", "s" * 64, "cfg", "SELL_YES", "take_profit_exit_v1"),
    )
    tmp_db_canonical.execute(
        """
        INSERT INTO orders (
            execution_id, order_id, run_id, plan_id, venue, order_side,
            entry_price, shares, cost_usd, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("d" * 64, "ord-exit", "run", "p" * 64, "polymarket_clob", "SELL_YES", 0.2, 10, 2, "submitted"),
    )
    row = tmp_db_canonical.execute("SELECT order_side FROM orders").fetchone()
    assert row["order_side"] == "SELL_YES"


def test_can_insert_canonical_lineage(tmp_db_canonical):
    config_id = "cfg-canonical"
    run_id = "run-canonical"
    signal_id = make_signal_id(
        target_date="2026-05-17",
        city="LA",
        bracket="68-69",
        signal_side="NO",
        model_version="gfs",
        forecast_source="open_meteo_live_gfs",
        snapshot_ts_utc="2026-05-17T04:00:53Z",
        condition_id="0x9abc",
    )
    plan_id = make_plan_id(
        run_id=run_id,
        signal_id=signal_id,
        order_side="BUY_NO",
        execution_policy="mid_price_core_v1",
    )
    execution_id = make_execution_id(
        run_id=run_id,
        plan_id=plan_id,
        venue="paper",
        attempt_index=0,
    )
    order_id = make_paper_order_id(execution_id=execution_id)

    tmp_db_canonical.execute(
        "INSERT INTO strategy_config (config_id, name, params) VALUES (?, ?, ?)",
        (config_id, "weather_edge_canonical", "{}"),
    )
    tmp_db_canonical.execute(
        "INSERT INTO universes (universe_id, name, cities, models) VALUES (?, ?, ?, ?)",
        ("u-canonical", "T24", '["LA"]', '["gfs"]'),
    )
    tmp_db_canonical.execute(
        "INSERT INTO code_versions (code_version) VALUES (?)",
        ("test-sha",),
    )
    tmp_db_canonical.execute(
        """
        INSERT INTO runs (
            run_id, producer_system, producer_run_id, config_id, universe_id,
            code_version, execution_mode, state
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, "pm_agent_local", "snapshot_20260517_1200", config_id, "u-canonical", "test-sha", "paper", "paper"),
    )
    tmp_db_canonical.execute(
        """
        INSERT INTO signals (
            signal_id, producer_system, producer_run_id, snapshot_ts_utc, snapshot_file,
            target_date, city, city_pool, icao, bracket, unit, signal_side,
            model_version, model_p_yes, forecast_source, market_price, edge, abs_edge,
            condition_id, market_id, token_id, hours_to_settle
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal_id,
            "pm_agent_local",
            "snapshot_20260517_1200",
            "2026-05-17T04:00:53Z",
            "snapshot_20260517_1200.json",
            "2026-05-17",
            "LA",
            "t1_trading",
            "KLAX",
            "68-69",
            "F",
            "NO",
            "gfs",
            0.3633,
            "open_meteo_live_gfs",
            0.485,
            0.1517,
            0.1517,
            "0x9abc",
            "2266022",
            "token-no",
            12.5,
        ),
    )
    tmp_db_canonical.execute(
        """
        INSERT INTO plans (
            plan_id, run_id, signal_id, config_id, order_side, notional,
            desired_shares, sizing_mode, entry_price_window, execution_policy,
            limit_price, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (plan_id, run_id, signal_id, config_id, "BUY_NO", 5.0, 10.309278, "notional", "0.25-0.75", "mid_price_core_v1", 0.485, "accepted"),
    )
    tmp_db_canonical.execute(
        """
        INSERT INTO orders (
            execution_id, order_id, run_id, plan_id, venue, order_side,
            limit_price, entry_price, shares, cost_usd, notional, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (execution_id, order_id, run_id, plan_id, "paper", "BUY_NO", 0.485, 0.45, 10.309278, 4.639175, 5.0, "filled"),
    )
    tmp_db_canonical.execute(
        """
        INSERT INTO fills (
            fill_id, execution_id, order_id, filled_shares, filled_price, fees_usd, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("fill-canonical", execution_id, order_id, 10.309278, 0.45, 0.0, "filled"),
    )
    tmp_db_canonical.execute(
        """
        INSERT INTO settlements (
            settlement_id, target_date, condition_id, market_id, bracket,
            token_id, final_price, settlement_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("d" * 64, "2026-05-17", "0x9abc", "2266022", "68-69", "token-no", 0.0, "settled"),
    )
    tmp_db_canonical.commit()

    row = tmp_db_canonical.execute(
        """
        SELECT sig.signal_id, p.plan_id, o.execution_id, f.fill_id, s.final_price
        FROM signals sig
        JOIN plans p ON p.signal_id = sig.signal_id
        JOIN orders o ON o.plan_id = p.plan_id
        JOIN fills f ON f.execution_id = o.execution_id
        JOIN settlements s
          ON s.target_date = sig.target_date
         AND s.condition_id = sig.condition_id
         AND s.bracket = sig.bracket
        WHERE sig.signal_id = ?
        """,
        (signal_id,),
    ).fetchone()

    assert row is not None
    assert row["plan_id"] == plan_id
    assert row["execution_id"] == execution_id
    assert row["final_price"] == 0.0


def test_canonical_signals_append_only(tmp_db_canonical):
    tmp_db_canonical.execute(
        "INSERT INTO strategy_config (config_id, name, params) VALUES ('cfg', 'cfg', '{}')"
    )
    tmp_db_canonical.execute(
        """
        INSERT INTO signals (
            signal_id, producer_system, producer_run_id, snapshot_ts_utc,
            target_date, city, city_pool, icao, bracket, unit, signal_side,
            model_version, model_p_yes, forecast_source, market_price, edge, abs_edge,
            condition_id, market_id, hours_to_settle
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "a" * 64,
            "pm_agent_local",
            "producer-run",
            "2026-05-17T04:00:53Z",
            "2026-05-17",
            "LA",
            "t1_trading",
            "KLAX",
            "68-69",
            "F",
            "NO",
            "gfs",
            0.36,
            "open_meteo_live_gfs",
            0.48,
            0.12,
            0.12,
            "0x9abc",
            "2266022",
            12.5,
        ),
    )
    tmp_db_canonical.commit()

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        tmp_db_canonical.execute("UPDATE signals SET city='SF' WHERE signal_id=?", ("a" * 64,))


def test_init_db_canonical_creates_file(tmp_path):
    db_path = tmp_path / "nested" / "weather.db"
    init_db_canonical(str(db_path))

    assert db_path.exists()
    conn = get_conn(str(db_path))
    try:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row["version"] == SCHEMA_VERSION
    finally:
        conn.close()
