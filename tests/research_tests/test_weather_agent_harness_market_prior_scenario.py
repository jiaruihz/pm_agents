from __future__ import annotations

import json
from pathlib import Path

from src.weather_agent_harness.evidence import EvidenceStore
from src.weather_agent_harness.scenarios.market_prior_training import (
    run_market_prior_training_scenario,
)


ROOT = Path(__file__).resolve().parents[2]


def test_market_prior_scenario_runs_to_machine_verified_terminal(tmp_path: Path) -> None:
    run_dir = tmp_path / "golden-run"
    report = tmp_path / "report.html"
    outcome = run_market_prior_training_scenario(
        repo_root=ROOT,
        run_dir=run_dir,
        report_path=report,
        production_preflight={
            "status": "critical",
            "note": "fixture test: production remains fail-closed",
        },
    )

    assert outcome.completion_state == "complete_qualified"
    assert outcome.action_count == 7
    assert outcome.experiment_count == 4
    state = EvidenceStore(run_dir).load_state()
    assert state.schema_version == "weather_agent_harness_v1"
    assert state.metadata["sealed_forward"] is True
    assert state.metadata["qualification_outcome"] == "qualified"

    experiment_paths = sorted((run_dir / "artifacts/experiments").glob("*.json"))
    assert len(experiment_paths) == 4
    assert all(json.loads(path.read_text())["forward_rows_read"] == 0 for path in experiment_paths)
    qualification = json.loads((run_dir / "artifacts/qualification.json").read_text())
    assert qualification["candidate_score"]["target_dates"] == 6
    assert qualification["searcher_saw_forward_labels"] is False

    report_text = report.read_text(encoding="utf-8")
    assert "Harness 到底做了什么" in report_text
    assert "fixture-only / no live change" in report_text
    assert "production remains fail-closed" in report_text


def test_market_prior_scenario_is_deterministic(tmp_path: Path) -> None:
    metrics = []
    champions = []
    for index in (1, 2):
        run_dir = tmp_path / f"run-{index}"
        run_market_prior_training_scenario(
            repo_root=ROOT,
            run_dir=run_dir,
            report_path=tmp_path / f"report-{index}.html",
        )
        qualification = json.loads((run_dir / "artifacts/qualification.json").read_text())
        metrics.append(qualification["paired_brier_delta_vs_market"])
        champions.append(EvidenceStore(run_dir).load_state().metadata["champion"]["run_id"])

    assert metrics[0] == metrics[1]
    assert champions[0] == champions[1]
