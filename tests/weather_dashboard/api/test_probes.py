"""Tests for probe pulse helpers and /api/probes endpoints."""

from datetime import datetime, timezone

from weather_dashboard.api.probe_pulse import classify_freshness, normalize_probe_row
from weather_dashboard.api.routers.probes import _probe_row


# ── pure helpers ────────────────────────────────────────────────────────────

def test_classify_freshness_bands():
    assert classify_freshness(5, 20, 120) == "fresh"
    assert classify_freshness(60, 20, 120) == "aging"
    assert classify_freshness(300, 20, 120) == "stale"
    assert classify_freshness(None, 20, 120) == "unknown"


def test_normalize_probe_row_handles_missing_summary():
    reg = {"strategy_instance": "p1", "lifecycle_status": "live", "health_status": "ok", "heartbeat_age_min": 10}
    row = normalize_probe_row(reg, None)
    assert row["strategy_instance"] == "p1"
    assert row["status"] == "no_pulse_file"
    assert row["snapshot_age_min"] is None


def test_normalize_probe_row_reads_nested_meta_snapshot_age():
    reg = {"strategy_instance": "p2", "lifecycle_status": "shadow", "health_status": "ok", "heartbeat_age_min": 3}
    summary = {"candidate_rows": 0, "meta": {"snapshot_age_min": 96.4, "snapshot_ts_utc": "2026-06-26T05:30:29Z",
              "audit_counts": {"obs_not_ok": 1}}}
    row = normalize_probe_row(reg, summary)
    assert row["snapshot_age_min"] == 96.4
    assert row["freshness"] in ("aging", "stale")
    assert row["top_audit"] == "obs_not_ok"


# ── endpoints ───────────────────────────────────────────────────────────────

def _seed_instance(api_db, instance="theta_x", lifecycle="tiny_live_probe", process_status="running"):
    api_db.execute(
        """INSERT INTO strategy_instance (
               instance_id, strategy_key, display_name, family, lifecycle_status,
               execution_mode, desired_status, source_layer, runtime_dir, expected_live,
               notes, updated_at_utc
           ) VALUES (?, 'reheat_risk.theta', ?, 'reheat_risk', ?, 'tiny_live', 'enabled',
               'runtime_local', ?, 1, '', '2026-07-26T00:00:00Z')""",
        (instance, instance, lifecycle, f"runtime/{instance}"),
    )
    api_db.execute(
        """INSERT INTO strategy_instance_runtime (
               instance_id, process_status, health_status, heartbeat_at_utc, last_tick_ts_utc,
               last_data_ts_utc, candidate_rows, plan_rows, live_order_rows, blocker_count,
               summary_json, refreshed_at_utc
           ) VALUES (?, ?, 'healthy', '2026-07-26T00:00:00Z', '2026-07-26T00:00:00Z',
               '2026-07-26T00:00:00Z', 2, 1, 0, 0, '{\"status\": \"ok\"}', '2026-07-26T00:00:00Z')""",
        (instance, process_status),
    )
    api_db.commit()


def test_probes_health_lists_current_instance_control_plane(client, api_db, tmp_path, monkeypatch):
    _seed_instance(api_db, "theta_x")
    r = client.get("/api/probes/health")
    assert r.status_code == 200
    probes = r.json()["probes"]
    row = next(p for p in probes if p["strategy_instance"] == "theta_x")
    assert row["process_status"] == "running"
    assert row["candidate_rows"] == 2


def test_probe_freshness_is_calculated_from_runtime_timestamp_not_summary_age(tmp_path):
    now = datetime(2026, 7, 26, 1, 0, tzinfo=timezone.utc)
    row = _probe_row({
        "strategy_instance": "old", "process_status": "running", "health_status": "healthy",
        "heartbeat_at_utc": "2026-07-26T00:00:00Z", "last_tick_ts_utc": "2026-07-26T00:00:00Z",
        "last_data_ts_utc": "2026-07-25T20:00:00Z", "summary_json": '{"snapshot_age_min": 1}',
    }, now=now, root=tmp_path)
    assert row["snapshot_age_min"] == 300.0
    assert row["freshness"] == "stale"


def test_probe_detail_404_for_unknown(client, api_db, tmp_path, monkeypatch):
    _seed_instance(api_db, "known")
    monkeypatch.setenv("WEATHER_RUNTIME_ROOT", str(tmp_path))
    r = client.get("/api/probes/does_not_exist")
    assert r.status_code == 404
