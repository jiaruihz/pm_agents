import sqlite3

from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from weather_dashboard.ingest.canonical import insert_strategy_config
from src.strategies.runtime.specs import StrategySpec, load_instance_specs
from src.strategies.runtime.sync import sync_instance_specs


def _db(tmp_path):
    conn = sqlite3.connect(tmp_path / "weather.db")
    conn.row_factory = sqlite3.Row
    apply_schema_canonical(conn)
    return conn


def test_sync_populates_instances(tmp_path):
    conn = _db(tmp_path)
    result = sync_instance_specs(conn)
    assert result["instance_rows"] == len(load_instance_specs())
    row = conn.execute(
        "SELECT * FROM strategy_instance WHERE instance_id=?",
        ("tmax_distribution_edge_first_lock_no_current_yes_shadow_v1",),
    ).fetchone()
    assert row["execution_mode"] == "zero_notional_shadow"
    assert row["desired_status"] == "enabled"  # table default on first insert
    assert row["params_hash"]
    conn.close()


def test_sync_shelves_non_operational_catalog_rows_on_first_insert(tmp_path):
    conn = _db(tmp_path)
    sync_instance_specs(conn)
    row = conn.execute(
        "SELECT desired_status FROM strategy_instance WHERE instance_id=?",
        ("low_price_yes_lottery_tiny_live_v1",),
    ).fetchone()
    assert row["desired_status"] == "shelved"
    conn.close()


def test_sync_shelves_legacy_enabled_row_when_lifecycle_becomes_stale(tmp_path):
    conn = _db(tmp_path)
    conn.execute(
        """INSERT INTO strategy_instance
           (instance_id,strategy_key,display_name,family,lifecycle_status,
            execution_mode,desired_status,source_layer,updated_at_utc)
           VALUES ('old-live','family','Old live','family','live','live',
                   'enabled','runtime_local','2026-08-01T00:00:00Z')"""
    )
    spec = StrategySpec(
        strategy_instance="old-live",
        display_name="Old live",
        family="family",
        lifecycle_status="stale",
        execution_mode="live",
        source_layer="runtime_local",
        expected_live=False,
    )
    sync_instance_specs(conn, specs=[spec])
    row = conn.execute(
        "SELECT lifecycle_status,desired_status,expected_live FROM strategy_instance WHERE instance_id='old-live'"
    ).fetchone()
    assert dict(row) == {
        "lifecycle_status": "stale",
        "desired_status": "shelved",
        "expected_live": 0,
    }
    conn.close()


def test_sync_preserves_explicit_strategy_identity_separate_from_family(tmp_path):
    conn = _db(tmp_path)
    sync_instance_specs(conn)
    rows = conn.execute(
        "SELECT instance_id, strategy_key, family FROM strategy_instance "
        "WHERE instance_id LIKE 'd1_yes_high_mid_%' ORDER BY instance_id"
    ).fetchall()
    assert len(rows) == 2
    assert {row["strategy_key"] for row in rows} == {"d1_yes_high_mid"}
    assert {row["family"] for row in rows} == {
        "market_structure_edge.favorite_low_estimation"
    }
    conn.close()


def test_sync_pulls_file_manifests_into_strategy_def(tmp_path):
    conn = _db(tmp_path)
    result = sync_instance_specs(conn)
    assert result["manifest_def_rows"] >= 5
    # file manifests land as def_source='manifest' with rich metadata
    row = conn.execute(
        "SELECT def_source, runner_module, domain FROM strategy_def WHERE strategy_key=?",
        ("weather_edge_v1",),
    ).fetchone()
    assert row["def_source"] == "manifest"
    assert row["runner_module"] == "src.strategies.pmm.main"
    # Weather definitions are explicit git-authored catalog rows.
    fam = conn.execute(
        "SELECT def_source FROM strategy_def WHERE strategy_key LIKE 'reheat_risk.%' LIMIT 1"
    ).fetchone()
    assert fam["def_source"] == "runtime_definition"
    conn.close()


def test_resync_preserves_operator_desired_status(tmp_path):
    conn = _db(tmp_path)
    sync_instance_specs(conn)
    conn.execute(
        "UPDATE strategy_instance SET desired_status='paused' WHERE instance_id=?",
        ("low_price_yes_lottery_tiny_live_v1",),
    )
    conn.commit()
    sync_instance_specs(conn)  # re-sync must not clobber operator state
    row = conn.execute(
        "SELECT desired_status FROM strategy_instance WHERE instance_id=?",
        ("low_price_yes_lottery_tiny_live_v1",),
    ).fetchone()
    assert row["desired_status"] == "paused"
    conn.close()


def test_sync_assigns_config_only_from_explicit_execution_policy(tmp_path):
    conn = _db(tmp_path)
    insert_strategy_config(
        conn,
        "cfg-low-price",
        "low price",
        {"execution_policy": "low_price_yes_lottery_guarded_taker_v1"},
    )
    insert_strategy_config(conn, "cfg-unknown", "unknown", {"execution_policy": "maker_queue_v1"})
    sync_instance_specs(conn)
    rows = {
        row["config_id"]: row["strategy_key"]
        for row in conn.execute("SELECT config_id, strategy_key FROM strategy_config")
    }
    assert rows["cfg-low-price"] == "forecast_quality.low_price_yes_lottery"
    assert rows["cfg-unknown"] is None
    conn.close()
