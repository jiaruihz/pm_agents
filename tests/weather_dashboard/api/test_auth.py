"""Tests for the optional Basic Auth middleware (DASHBOARD_AUTH env-gated)."""

import base64

import pytest
from fastapi.testclient import TestClient

from weather_dashboard.api.app import create_app


def _client(monkeypatch, auth: str | None):
    if auth is None:
        monkeypatch.delenv("DASHBOARD_AUTH", raising=False)
    else:
        monkeypatch.setenv("DASHBOARD_AUTH", auth)
    return TestClient(create_app())


def test_no_auth_when_env_unset(monkeypatch):
    c = _client(monkeypatch, None)
    assert c.get("/api/glossary").status_code == 200
    # The health payload may legitimately be 503 when its canonical DB/runtime
    # dependency is absent in an isolated test checkout. This test owns only
    # the auth boundary: the liveness route must remain reachable without auth.
    assert c.get("/health").status_code != 401


def test_auth_required_when_env_set(monkeypatch):
    c = _client(monkeypatch, "admin:secret123")
    assert c.get("/api/glossary").status_code == 401
    # health stays open for liveness probes
    assert c.get("/health").status_code != 401


def test_good_and_bad_credentials(monkeypatch):
    c = _client(monkeypatch, "admin:secret123")
    ok = base64.b64encode(b"admin:secret123").decode()
    bad = base64.b64encode(b"admin:wrong").decode()
    assert (
        c.get("/api/glossary", headers={"Authorization": f"Basic {ok}"}).status_code
        == 200
    )
    assert (
        c.get("/api/glossary", headers={"Authorization": f"Basic {bad}"}).status_code
        == 401
    )
