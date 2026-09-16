"""Tests for research-line aggregation helpers and /api/research/lines."""

import json

from weather_dashboard.api.research_aggregate import aggregate_research_lines


def test_aggregate_extracts_and_flags_ci(tmp_path):
    g = tmp_path / "2026-06" / "generated" / "foo_v1"
    g.mkdir(parents=True)
    (g / "summary.json").write_text(json.dumps({
        "title": "Foo v1", "status": "research-only",
        "holdout_roi": 0.041, "forward_roi": 0.09,
        "roi_ci_low": -0.034, "roi_ci_high": 0.105,
        "excess_roi_vs_baseline": 0.18}))
    lines = aggregate_research_lines(tmp_path)
    row = next(l for l in lines if l["line_id"] == "foo_v1")
    assert row["ci_crosses_zero"] is True
    assert row["gate_ready"] is False  # CI crosses zero ⇒ not gate-ready


def test_aggregate_gate_ready_when_ci_positive_and_forward_positive(tmp_path):
    g = tmp_path / "2026-06" / "generated" / "winner_v1"
    g.mkdir(parents=True)
    (g / "summary.json").write_text(json.dumps({
        "title": "Winner", "status": "shadow_candidate",
        "holdout_roi": 0.2, "forward_roi": 0.15,
        "roi_ci_low": 0.05, "roi_ci_high": 0.30}))
    row = next(l for l in aggregate_research_lines(tmp_path) if l["line_id"] == "winner_v1")
    assert row["ci_crosses_zero"] is False
    assert row["gate_ready"] is True


def test_aggregate_tolerates_missing_fields(tmp_path):
    g = tmp_path / "2026-06" / "generated" / "bar_v1"
    g.mkdir(parents=True)
    (g / "summary.json").write_text(json.dumps({"title": "Bar"}))
    row = next(l for l in aggregate_research_lines(tmp_path) if l["line_id"] == "bar_v1")
    assert row["holdout_roi"] is None
    assert row["gate_ready"] is False


def test_research_lines_endpoint(client, tmp_path, monkeypatch):
    g = tmp_path / "2026-06" / "generated" / "endpoint_v1"
    g.mkdir(parents=True)
    (g / "summary.json").write_text(json.dumps({"title": "Endpoint", "forward_roi": 0.1,
                                                "roi_ci_low": 0.01, "roi_ci_high": 0.2}))
    monkeypatch.setenv("WEATHER_ANALYSIS_ROOT", str(tmp_path))
    r = client.get("/api/research/lines")
    assert r.status_code == 200
    assert any(l["line_id"] == "endpoint_v1" for l in r.json()["lines"])
