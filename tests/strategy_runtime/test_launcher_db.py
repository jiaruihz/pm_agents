import sqlite3
from argparse import Namespace

import pytest

from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from src.strategies.runtime.sync import sync_instance_specs
from src.strategies.runtime.specs import StrategySpec
from scripts.ops import weather_strategy_launcher as launcher


def test_instance_rows_reads_from_db(tmp_path):
    conn = sqlite3.connect(tmp_path / "weather.db")
    conn.row_factory = sqlite3.Row
    apply_schema_canonical(conn)
    sync_instance_specs(conn)
    rows = launcher.instance_rows(conn)
    ids = {r["instance_id"] for r in rows}
    assert "low_price_yes_lottery_tiny_live_v1" in ids
    sample = next(r for r in rows if r["instance_id"] == "low_price_yes_lottery_tiny_live_v1")
    assert sample["desired_status"] == "shelved"
    assert "execution_mode" in sample
    conn.close()


def test_reconcile_observe_populates_runtime_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "weather.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    apply_schema_canonical(conn)
    sync_instance_specs(conn)
    conn.close()
    # Reconcile unit coverage should not scan the real production journals.
    monkeypatch.setattr(launcher, "specs_by_instance", lambda: {})

    rc = launcher.cmd_reconcile(
        Namespace(
            db_path=db_path,
            apply=False,
            confirm_live=False,
            reason=None,
        )
    )
    assert rc == 0
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT process_status, health_status FROM strategy_instance_runtime WHERE instance_id=?",
        ("low_price_yes_lottery_tiny_live_v1",),
    ).fetchone()
    assert row is not None
    assert row["process_status"] in {"stopped", "unknown", "running"}
    assert row["health_status"] in {"unknown", "healthy", "idle", "stale", "blocked", "shelved"}
    conn.close()


def test_legacy_launcher_mutations_fail_closed(tmp_path):
    args = Namespace(
        db_path=tmp_path / "weather.db",
        strategy_instance="fast_source_prev_no_trial_v1",
        apply=True,
        confirm_live=True,
        reason="test",
        no_refresh=True,
    )
    for command in (launcher.cmd_start, launcher.cmd_stop, launcher.cmd_reconcile):
        with pytest.raises(SystemExit, match="weather_production_ctl.py"):
            command(args)


def test_runtime_snapshot_reads_current_summary_and_jrs_tmux(tmp_path):
    runtime_dir = tmp_path / "fast"
    runtime_dir.mkdir()
    (runtime_dir / "latest_summary.json").write_text(
        '{"generated_at_utc":"2026-07-26T06:30:00Z","status":"ok","opportunities":4,"execution_eligible":2,"live_enabled":true}',
        encoding="utf-8",
    )
    (runtime_dir / "orders.jsonl").write_text('{"ts_utc":"2026-07-26T06:30:00Z"}\n', encoding="utf-8")
    spec = StrategySpec(
        strategy_instance="fast", display_name="Fast", family="latency", strategy_key="latency.fast",
        lifecycle_status="live", execution_mode="live", source_layer="runtime_local",
        runtime_dir=str(runtime_dir), summary_file="latest_summary.json", live_order_file="orders.jsonl",
        tmux_session="weather_fast_source_prev_no_trial", expected_live=True,
    )
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_schema_canonical(conn)
    conn.execute("INSERT INTO strategy_instance (instance_id,strategy_key,display_name,family,lifecycle_status,execution_mode,desired_status,source_layer,updated_at_utc) VALUES ('fast','latency.fast','Fast','latency','live','live','enabled','runtime_local','2026-07-26T00:00:00Z')")
    snapshot = launcher._runtime_snapshot(
        spec,
        tmux_sessions={"weather_fast_source_prev_no_trial"},
        screen_sessions=set(),
        existing={},
    )
    assert snapshot["process_status"] == "running"
    assert snapshot["candidate_rows"] == 4
    assert snapshot["plan_rows"] == 2
    assert snapshot["live_order_rows"] == 1


def test_journal_rows_keeps_previous_count_for_large_history(tmp_path):
    path = tmp_path / "history.jsonl"
    path.write_bytes(b"x" * 32)
    assert launcher._journal_rows(path, 7) == 1
    path.write_bytes(b"x" * (launcher.MAX_COUNTED_JOURNAL_BYTES + 1))
    assert launcher._journal_rows(path, 7) == 7
