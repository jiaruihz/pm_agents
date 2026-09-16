from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.weather_agent_harness.evidence import EvidenceStore
from src.weather_agent_harness.scenarios.busan_market_prior_case import (
    DEFAULT_INPUT,
    run_busan_market_prior_case,
)


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(not DEFAULT_INPUT.is_file(), reason="immutable Busan archive is not mounted")
def test_real_busan_case_reaches_falsified_terminal(tmp_path: Path) -> None:
    run_dir = tmp_path / "busan-real"
    report = tmp_path / "busan-real.html"
    outcome = run_busan_market_prior_case(
        repo_root=ROOT,
        run_dir=run_dir,
        report_path=report,
        production_preflight={"status": "critical", "note": "test fail-closed"},
    )

    assert outcome.completion_state == "complete_falsified"
    assert outcome.action_count == 8
    assert outcome.experiment_count == 5
    state = EvidenceStore(run_dir).load_state()
    assert state.metadata["champion"]["run_id"].endswith("ridge-0.3")
    assert state.metadata["qualification_outcome"] == "falsified"

    for path in (run_dir / "artifacts/experiments").glob("*.json"):
        assert json.loads(path.read_text())["holdout_rows_read"] == 0
    qualification = json.loads((run_dir / "artifacts/qualification.json").read_text())
    assert qualification["candidate_score"]["rows"] == 678
    assert qualification["candidate_score"]["target_dates"] == 22
    assert qualification["candidate_minus_market"]["logloss"]["delta"] == pytest.approx(0.08158535939604222)
    assert qualification["execution"]["pnl_usd"] == pytest.approx(-8.77855)
    assert qualification["gates"] == {
        "execution": False,
        "forward": True,
        "market_baseline": False,
        "probability": False,
    }
    text = report.read_text(encoding="utf-8")
    assert "REAL ARCHIVED PIT DATA" in text
    assert "COMPLETE_FALSIFIED" in text
    assert "不是 fixture" in text
