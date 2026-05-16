"""Integration tests for the FastAPI endpoints."""

import json
import pytest


# ── /health ───────────────────────────────────────────────────────────────────

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ── /api/runs ─────────────────────────────────────────────────────────────────

def test_list_runs_empty(client):
    r = client.get("/api/runs")
    assert r.status_code == 200
    assert r.json() == []


def test_list_runs_returns_seeded(client, seeded_run):
    _, run_id, _ = seeded_run
    r = client.get("/api/runs")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["run_id"] == run_id


def test_list_runs_filter_state(client, seeded_run):
    r = client.get("/api/runs?state=explore")
    assert r.status_code == 200
    assert len(r.json()) == 1

    r2 = client.get("/api/runs?state=live")
    assert r2.status_code == 200
    assert len(r2.json()) == 0


def test_get_run(client, seeded_run):
    _, run_id, _ = seeded_run
    r = client.get(f"/api/runs/{run_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["run_id"] == run_id
    assert "metrics" in data
    assert data["metrics"]["num_trades"] == 0


def test_get_run_not_found(client):
    r = client.get("/api/runs/nonexistent-run-id")
    assert r.status_code == 404


def test_get_run_trades_empty(client, seeded_run):
    _, run_id, _ = seeded_run
    r = client.get(f"/api/runs/{run_id}/trades")
    assert r.status_code == 200
    assert r.json() == []


# ── /api/compare ──────────────────────────────────────────────────────────────

def test_compare_single_run(client, seeded_run):
    _, run_id, _ = seeded_run
    r = client.get(f"/api/compare?run_ids={run_id}")
    assert r.status_code == 200
    data = r.json()
    assert len(data["runs"]) == 1
    assert data["runs"][0]["run_id"] == run_id
    assert "metrics" in data["runs"][0]


def test_compare_missing_run(client):
    r = client.get("/api/compare?run_ids=bad-id-1,bad-id-2")
    assert r.status_code == 200
    data = r.json()
    assert len(data["runs"]) == 2
    assert data["runs"][0]["error"] == "not found"


# ── /api/configs ──────────────────────────────────────────────────────────────

def test_list_configs(client, seeded_run):
    r = client.get("/api/configs")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["name"] == "test_cfg"
    assert isinstance(data[0]["params"], dict)


def test_get_config(client, seeded_run):
    _, _, cid = seeded_run
    r = client.get(f"/api/configs/{cid}")
    assert r.status_code == 200
    assert r.json()["config_id"] == cid


def test_get_config_not_found(client):
    r = client.get("/api/configs/nonexistent")
    assert r.status_code == 404


# ── /api/universes ────────────────────────────────────────────────────────────

def test_list_universes(client, seeded_run):
    r = client.get("/api/universes")
    assert r.status_code == 200
    data = r.json()
    # bootstrap universe + our test universe
    names = {d["universe_id"] for d in data}
    assert "u1" in names


def test_get_universe(client, seeded_run):
    r = client.get("/api/universes/u1")
    assert r.status_code == 200
    data = r.json()
    assert data["cities"] == ["Tokyo"]
    assert data["models"] == ["ecmwf"]


# ── /api/settlements ──────────────────────────────────────────────────────────

def test_list_settlements_empty(client):
    r = client.get("/api/settlements")
    assert r.status_code == 200
    assert r.json() == []


def test_list_settlements_with_filter(client, api_db):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    api_db.execute(
        "INSERT INTO settlements (settlement_id, target_date, bracket, final_yes, status, created_at_utc) "
        "VALUES (?,?,?,?,?,?)",
        ("s1", "2026-05-09", "23", 1, "settled", now),
    )
    api_db.commit()

    r = client.get("/api/settlements?target_date=2026-05-09")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["bracket"] == "23"
    assert data[0]["final_yes"] == 1
