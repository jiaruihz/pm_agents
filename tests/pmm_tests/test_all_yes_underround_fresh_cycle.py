import argparse
from datetime import datetime, timezone
from pathlib import Path

import scripts.ops.all_yes_underround_fresh_paper_cycle_v0 as fresh_cycle
from scripts.ops.all_yes_underround_fresh_paper_cycle_v0 import apply_snapshot_service_gate, snapshot_freshness


NOW = datetime(2026, 6, 13, 18, 33, 0, tzinfo=timezone.utc)


def _freshness(value, *, file_age=20, rows=1000, max_age=180):
    return snapshot_freshness(
        freshness_ts_utc=value,
        decision_ts=NOW,
        max_age_seconds=max_age,
        file_mtime=NOW.timestamp() - file_age,
        min_file_stable_seconds=10,
        rows=rows,
        min_snapshot_rows=500,
    )


def test_snapshot_freshness_accepts_snapshot_within_ttl():
    result = _freshness("2026-06-13T18:30:53Z")

    assert result["fresh"] is True
    assert result["reason"] == "fresh"
    assert result["snapshot_age_seconds"] == 127.0


def test_snapshot_freshness_rejects_stale_snapshot():
    result = _freshness("2026-06-13T18:29:59Z")

    assert result["fresh"] is False
    assert result["reason"] == "snapshot_too_old"
    assert result["snapshot_age_seconds"] == 181.0


def test_snapshot_freshness_fails_closed_on_missing_timestamp():
    result = _freshness(None)

    assert result["fresh"] is False
    assert result["reason"] == "missing_snapshot_ts"


def test_snapshot_freshness_rejects_file_still_being_written():
    result = _freshness("2026-06-13T18:30:53Z", file_age=2)

    assert result["fresh"] is False
    assert result["reason"] == "snapshot_file_still_writing"


def test_snapshot_freshness_rejects_partial_snapshot_rows():
    result = _freshness("2026-06-13T18:30:53Z", rows=26)

    assert result["fresh"] is False
    assert result["reason"] == "snapshot_rows_below_min"


def test_snapshot_service_gate_rejects_running_snapshot_capture():
    freshness = _freshness("2026-06-13T18:30:53Z")
    result = apply_snapshot_service_gate(
        freshness,
        {
            "snapshot_service_name": "weather-predict-snapshot.service",
            "snapshot_service_status": "activating",
            "snapshot_service_running": True,
        },
    )

    assert result["fresh"] is False
    assert result["reason"] == "snapshot_service_running"
    assert result["pre_service_gate_reason"] == "fresh"


def test_snapshot_service_gate_keeps_inactive_snapshot_capture_fresh():
    freshness = _freshness("2026-06-13T18:30:53Z")
    result = apply_snapshot_service_gate(
        freshness,
        {
            "snapshot_service_name": "weather-predict-snapshot.service",
            "snapshot_service_status": "inactive",
            "snapshot_service_running": False,
        },
    )

    assert result["fresh"] is True
    assert result["reason"] == "fresh"
    assert result["snapshot_service_status"] == "inactive"


def test_success_marker_is_written_before_monitor_reads_fresh_cycle(monkeypatch, tmp_path):
    run_dir = tmp_path / "run"
    snapshot = tmp_path / "orderbook_snapshot.jsonl.gz"
    snapshot.write_text("snapshot")
    writes = []
    commands = []

    monkeypatch.setattr(
        fresh_cycle,
        "parse_args",
        lambda: argparse.Namespace(
            snapshot_root=str(tmp_path),
            snapshot_path=str(snapshot),
            scan_json=str(tmp_path / "scan.json"),
            scan_md=str(tmp_path / "scan.md"),
            run_dir=str(run_dir),
            db_path=str(tmp_path / "weather.db"),
            gate_path=str(tmp_path / "clob_gate.json"),
            station_basis_gate_path=str(tmp_path / "station_gate.json"),
            max_snapshot_age_seconds=180.0,
            min_file_stable_seconds=10.0,
            min_snapshot_rows=500,
            snapshot_service_name="",
            wait_seconds=0.0,
            poll_seconds=1.0,
            python="python",
        ),
    )
    monkeypatch.setattr(
        fresh_cycle,
        "load_latest_snapshot",
        lambda args: (
            snapshot,
            {"rows": 1000, "orderbook_fetched_at_utc_max": "2026-06-14T07:00:00Z"},
            {"fresh": True, "reason": "fresh", "snapshot_age_seconds": 30.0},
        ),
    )

    def fake_run_cmd(argv):
        commands.append(argv)
        if "monitor" in argv:
            assert any(
                row.get("verdict") == "FRESH_SNAPSHOT_CYCLE_RAN" and row.get("executed_cycle") is True
                for row in writes
            )

    def fake_write_result(_run_dir, result):
        writes.append(dict(result))

    def fake_read_json(path):
        name = Path(path).name
        if name == "last_cycle.json":
            return {"scanner_candidate_count": 1, "appended_baskets": 1}
        if name == "monitor.json":
            return {"verdict": "NOT_READY_ACCUMULATE_PAPER_SHADOW"}
        return {}

    monkeypatch.setattr(fresh_cycle, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(fresh_cycle, "write_result", fake_write_result)
    monkeypatch.setattr(fresh_cycle, "read_json", fake_read_json)

    fresh_cycle.main()

    assert [cmd[1] for cmd in commands] == [
        "scripts/analysis/market_structure_edge/research_all_yes_underround_live_prep_v0.py",
        "scripts/ops/all_yes_underround_paper_exec_v0.py",
        "scripts/ops/all_yes_underround_paper_exec_v0.py",
        "scripts/analysis/market_structure_edge/research_all_yes_underround_live_prep_v0.py",
    ]
    assert writes[-1]["monitor"]["verdict"] == "NOT_READY_ACCUMULATE_PAPER_SHADOW"
