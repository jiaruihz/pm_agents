"""Tests for probe pulse helpers and /api/probes endpoints."""

import json

from weather_dashboard.api.probe_pulse import classify_freshness, normalize_probe_row


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

def _seed_registry(api_db, instance="theta_x", lifecycle="live"):
    # Table is created by the canonical schema with many NOT NULL columns;
    # supply sensible defaults for the required ones plus the fields we read.
    api_db.execute(
        """INSERT INTO weather_strategy_runtime_registry (
               strategy_instance, display_name, family, lifecycle_status,
               execution_mode, health_status, source_layer,
               candidate_rows, plan_rows, live_order_rows, paper_order_rows,
               shadow_rows, telemetry_rows, fact_trade_rows, fact_live_real_rows,
               process_status, blocker_count, blockers_json, summary_json,
               refreshed_at_utc, heartbeat_age_min
           ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, 0, 0, ?, 0, '[]', '{}', ?, ?)""",
        (instance, instance, "reheat_risk", lifecycle, "live", "healthy",
         "runtime_remote_mirror", "running", "2026-06-26T00:00:00+00:00", 7.0),
    )
    api_db.commit()


def test_probes_health_lists_registry_with_pulse(client, api_db, tmp_path, monkeypatch):
    _seed_registry(api_db, "theta_x")
    d = tmp_path / "theta_x"; d.mkdir()
    (d / "latest_summary.json").write_text(json.dumps({
        "status": "planned", "candidate_rows": 0,
        "meta": {"snapshot_age_min": 5.0, "snapshot_ts_utc": "2026-06-26T05:30:29Z"}}))
    monkeypatch.setenv("WEATHER_RUNTIME_ROOT", str(tmp_path))
    r = client.get("/api/probes/health")
    assert r.status_code == 200
    probes = r.json()["probes"]
    assert any(p["strategy_instance"] == "theta_x" and p["freshness"] == "fresh" for p in probes)


def test_probes_health_missing_file_does_not_crash(client, api_db, tmp_path, monkeypatch):
    _seed_registry(api_db, "ghost")
    monkeypatch.setenv("WEATHER_RUNTIME_ROOT", str(tmp_path))
    r = client.get("/api/probes/health")
    assert r.status_code == 200
    assert r.json()["probes"][0]["status"] == "no_pulse_file"


def test_probe_detail_404_for_unknown(client, api_db, tmp_path, monkeypatch):
    _seed_registry(api_db, "known")
    monkeypatch.setenv("WEATHER_RUNTIME_ROOT", str(tmp_path))
    r = client.get("/api/probes/does_not_exist")
    assert r.status_code == 404
