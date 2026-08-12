import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

from scripts.analysis.reheat_risk import late_window_shared
from scripts.analysis.reheat_risk import peak_forming_hazard_shared
from scripts.analysis.reheat_risk import peak_yes_timing_shared
from scripts.analysis.reheat_risk import weather_research_data_shared
from scripts.analysis.reheat_risk import train_current_yes_peak_forming_hazard_v1 as peak_v1
from scripts.analysis.reheat_risk import current_yes_peak_forming_hazard as peak_v2
from scripts.analysis.reheat_risk import (
    research_current_yes_peak_yes_execution_timing_v1 as peak_yes_execution,
)
from scripts.analysis.reheat_risk import (
    research_current_yes_peak_yes_timing_shadow_telemetry_v1 as peak_yes_telemetry,
)
from scripts.analysis.reheat_risk import research_tmax_cross_hour_coherence_audit_v1 as coherence_v1
from scripts.analysis.reheat_risk import research_tmax_cross_hour_coherence_audit_v2 as coherence_v2
from scripts.ops import check_weather_docs


def test_repo_hygiene_rejects_tracked_runtime_artifacts():
    errors = []

    check_weather_docs.check_runtime_artifacts_are_untracked(
        errors,
        {"runtime/logs/process.log", "runtime/process.pid", "src/runner.py"},
    )

    assert errors == [
        "runtime artifacts must not be git tracked: runtime/logs/process.log, "
        "runtime/process.pid"
    ]


def test_repo_hygiene_accepts_runtime_files_outside_git():
    errors = []

    check_weather_docs.check_runtime_artifacts_are_untracked(
        errors,
        {"src/strategies/weather_edge_v1/runtime/execution_journal.py"},
    )

    assert errors == []


def test_internal_analysis_import_check_rejects_pre_reorganization_path(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    script = repo / "scripts/analysis/family/research_runner.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "from scripts.analysis.old_runner import load_rows\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_weather_docs, "ROOT", repo)

    errors: list[str] = []
    check_weather_docs.check_internal_analysis_imports(
        errors, {"scripts/analysis/family/research_runner.py"}
    )

    assert errors == [
        "scripts/analysis/family/research_runner.py:1: internal analysis import "
        "does not exist: scripts.analysis.old_runner"
    ]


def test_research_output_route_check_rejects_new_repo_writer(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    script = repo / "scripts/analysis/family/research_runner.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        'OUT = ROOT / "docs" / "analysis" / "result.json"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(check_weather_docs, "ROOT", repo)
    monkeypatch.setattr(
        check_weather_docs,
        "hygiene_config",
        lambda: {"max_research_scripts_writing_docs_analysis": 0},
    )

    errors: list[str] = []
    check_weather_docs.check_research_output_routes(
        errors, {"scripts/analysis/family/research_runner.py"}
    )

    assert errors == [
        "research scripts writing docs/analysis grew from ceiling 0 to 1; "
        "route machine output through JRS run manifests"
    ]


def test_analysis_history_debt_rejects_new_reports_and_parallel_machine_outputs(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    month = repo / "docs/analysis/2026-08"
    month.mkdir(parents=True)
    report = month / "2026-08-13-one-off-v1.md"
    csv = month / "one-off.csv"
    json_file = month / "one-off.json"
    report.write_text("# one off\n", encoding="utf-8")
    csv.write_text("x\n1\n", encoding="utf-8")
    json_file.write_text('{"x": 1}\n', encoding="utf-8")
    monkeypatch.setattr(check_weather_docs, "ROOT", repo)
    monkeypatch.setattr(
        check_weather_docs,
        "hygiene_config",
        lambda: {
            "max_dated_analysis_reports": 0,
            "max_untracked_dated_analysis_reports": 0,
            "max_top_level_analysis_machine_artifacts": 1,
            "max_untracked_top_level_analysis_machine_artifacts": 0,
            "max_top_level_analysis_machine_bytes": 1,
            "top_level_analysis_machine_artifact_max_bytes": 4,
            "max_parallel_machine_format_stems": 0,
        },
    )

    errors: list[str] = []
    check_weather_docs.check_analysis_history_debt(
        errors,
        {
            "docs/analysis/2026-08/2026-08-13-one-off-v1.md",
            "docs/analysis/2026-08/one-off.csv",
            "docs/analysis/2026-08/one-off.json",
        },
        {
            "docs/analysis/2026-08/2026-08-13-untracked-v1.md",
            "docs/analysis/2026-08/untracked.jsonl",
        },
    )

    assert any("dated analysis reports grew" in error for error in errors)
    assert any("machine artifacts grew" in error for error in errors)
    assert any("machine bytes grew" in error for error in errors)
    assert any("artifact exceeds" in error for error in errors)
    assert any("parallel CSV/JSON" in error for error in errors)
    assert any("untracked dated analysis reports" in error for error in errors)
    assert any("untracked top-level analysis machine artifacts" in error for error in errors)


def test_late_window_shared_helper_contract(tmp_path):
    observation = tmp_path / "latest.json"
    observation.write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-08-05T00:00:00Z",
                "records": [
                    {
                        "city": "Chengdu",
                        "status": "ok",
                        "target_date": "2026-08-05",
                        "running_max_c": 33.0,
                        "decline_c": 1.0,
                        "source_chain": ["METAR", "WU"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    row = late_window_shared.load_today_observation(observation).iloc[0]
    assert row["running_max_f"] == 91.4
    assert row["source_chain"] == "METAR,WU"

    basket = pd.DataFrame(
        [
            {
                "settled": True,
                "execution_mode": "taker",
                "city": "Chengdu",
                "target_date": "2026-08-05",
                "decision_hour_local": 17,
                "snapshot_ts_utc": "2026-08-05T09:00:00Z",
                "leg": "current_yes",
                "cost_per_share": 0.4,
                "pnl_per_share": 0.6,
                "final_winning_bracket": "33",
            }
        ]
    )
    result = late_window_shared.basket_rows(basket).iloc[0]
    assert result["roi"] == pytest.approx(1.5)
    assert late_window_shared.block_ci(np.array([1.0])) == (None, None)


def test_peak_forming_versions_use_shared_version_neutral_helpers():
    assert peak_v1.json_ready is peak_forming_hazard_shared.json_ready
    assert peak_v2.json_ready is peak_forming_hazard_shared.json_ready
    assert peak_v1.metric_row is peak_forming_hazard_shared.metric_row
    assert peak_v2.metric_row is peak_forming_hazard_shared.metric_row
    assert peak_v1.approx_metar_veto is peak_forming_hazard_shared.approx_metar_veto
    assert peak_v2.approx_metar_veto is peak_forming_hazard_shared.approx_metar_veto
    for version in (peak_v1, peak_v2):
        assert version.pipeline.func is peak_forming_hazard_shared.build_pipeline
        assert (
            version.artifact_from_model.func
            is peak_forming_hazard_shared.artifact_from_model
        )
        assert version.score_artifact.func is peak_forming_hazard_shared.score_artifact
        assert version.select_grid.func is peak_forming_hazard_shared.select_grid

    rows = pd.DataFrame(
        [
            {
                "target_date": "2026-06-01",
                "city": "London",
                "label_current_yes_survives": 1,
                "current_yes_ask": 0.8,
                "p_hazard": 0.9,
            },
            {
                "target_date": "2026-06-02",
                "city": "Paris",
                "label_current_yes_survives": 0,
                "current_yes_ask": 0.4,
                "p_hazard": 0.3,
            },
        ]
    )
    v1 = peak_v1.summarize_trade(rows, "p_hazard", "same")
    v2 = peak_v2.summarize_trade(rows, "p_hazard", "same")
    assert v1["orders"] == v2["orders"] == 2
    assert v1["cost"] == pytest.approx(1.2)
    assert v1["pnl"] == pytest.approx(-0.2)


def test_peak_yes_timing_variants_share_serialization_helpers():
    for variant in (peak_yes_execution, peak_yes_telemetry):
        assert variant.json_ready is peak_yes_timing_shared.json_ready
        assert variant.pct is peak_yes_timing_shared.pct
        assert variant.num is peak_yes_timing_shared.num


def test_weather_research_data_helpers_keep_one_read_only_contract(tmp_path):
    database = tmp_path / "weather.db"
    with sqlite3.connect(database) as conn:
        conn.executescript(
            """
            CREATE TABLE fact_trades(
                fact_built_at_utc TEXT, trade_class TEXT, settlement_status TEXT
            );
            CREATE TABLE fact_signal_candidates(
                city TEXT, event_date TEXT, bracket TEXT, side TEXT,
                model_p_yes REAL, eligible INTEGER, paper_ordered INTEGER,
                live_filled INTEGER
            );
            CREATE TABLE orders(execution_id TEXT, status TEXT, venue TEXT);
            CREATE TABLE fills(execution_id TEXT);
            INSERT INTO fact_trades VALUES ('2026-08-01T00:00:00Z', 'live_real', 'settled');
            INSERT INTO fact_signal_candidates VALUES
                ('Tokyo', '2026-08-01', '35', 'BUY_YES', 0.4, 1, 0, 0),
                ('Tokyo', '2026-08-01', '35', 'BUY_YES', 0.6, 1, 0, 0);
            INSERT INTO orders VALUES ('order-1', 'filled', 'polymarket_clob');
            INSERT INTO fills VALUES ('order-1');
            """
        )
    gate = tmp_path / "gate.json"
    gate.write_text(
        json.dumps(
            {
                "gate_pass": True,
                "fail_reasons": [],
                "db_fills": {"missing_order_rows": 0, "over_order_keys": 0},
                "db_fill_cost_minus_fact_cost": 0.0,
            }
        ),
        encoding="utf-8",
    )

    check = weather_research_data_shared.data_self_check(database)
    prior = weather_research_data_shared.load_buy_yes_forecast_prior(database)
    coverage = weather_research_data_shared.load_fill_coverage_gate(gate)

    assert check["fact_trades_max_built_at_utc"] == "2026-08-01T00:00:00Z"
    assert check["clob_order_fill_join"][0]["with_fill"] == 1
    assert prior.iloc[0]["raw_model_p_yes"] == pytest.approx(0.5)
    assert prior.iloc[0]["prior_rows"] == 2
    assert coverage["gate_pass"] is True


def test_tmax_coherence_v2_reuses_v1_serialization_contract():
    assert coherence_v2._json_ready is coherence_v1._json_ready
    assert coherence_v2._safe_float is coherence_v1._safe_float


def test_research_debt_checker_rejects_new_entrypoint_and_duplicate_growth(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    script_root = repo / "scripts/analysis/family"
    script_root.mkdir(parents=True)
    body = """def repeated_function(values):
    total = 0
    for value in values:
        if value:
            total += value
        else:
            total -= 1
    return total
"""
    first = script_root / "research_city_a_v1.py"
    second = script_root / "research_city_b_v1.py"
    first.write_text(body, encoding="utf-8")
    second.write_text(body, encoding="utf-8")
    monkeypatch.setattr(check_weather_docs, "ROOT", repo)
    monkeypatch.setattr(
        check_weather_docs,
        "hygiene_config",
        lambda: {
            "max_research_experiment_entrypoints": 1,
            "max_repeated_research_function_bodies": 0,
        },
    )

    errors: list[str] = []
    check_weather_docs.check_research_script_debt(
        errors,
        {
            "scripts/analysis/family/research_city_a_v1.py",
            "scripts/analysis/family/research_city_b_v1.py",
        },
    )

    assert any("entrypoints grew" in error for error in errors)
    assert any("function bodies grew" in error for error in errors)


def test_research_debt_checker_includes_untracked_worktree_scripts(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    script_root = repo / "scripts/analysis/family"
    script_root.mkdir(parents=True)
    body = """def repeated_function(values):
    total = 0
    for value in values:
        if value:
            total += value
        else:
            total -= 1
    return total
"""
    paths = {
        "scripts/analysis/family/research_city_v1.py",
        "scripts/analysis/family/research_city_v2.py",
    }
    for relative in paths:
        (repo / relative).write_text(body, encoding="utf-8")
    monkeypatch.setattr(check_weather_docs, "ROOT", repo)
    monkeypatch.setattr(
        check_weather_docs,
        "hygiene_config",
        lambda: {
            "max_research_experiment_entrypoints": 0,
            "max_repeated_research_function_bodies": 0,
            "max_untracked_research_experiment_entrypoints": 1,
            "max_total_research_experiment_entrypoints": 1,
            "max_research_version_families": 0,
            "max_research_version_family_copies": 0,
            "max_worktree_repeated_function_extra_copies": 0,
        },
    )

    errors: list[str] = []
    check_weather_docs.check_research_script_debt(errors, set(), paths)

    assert any("untracked research experiment entrypoints grew" in error for error in errors)
    assert any("total tracked+untracked research experiment entrypoints grew" in error for error in errors)
    assert any("versioned research script families grew" in error for error in errors)
    assert any("versioned research script copies grew" in error for error in errors)
    assert any("worktree repeated research function copies grew" in error for error in errors)


def test_superseded_document_cannot_remain_current_in_index(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    docs = repo / "docs"
    analysis = docs / "analysis"
    analysis.mkdir(parents=True)
    (analysis / "old_prompt.md").write_text(
        "# Old Prompt\n\nStatus: superseded-for-now\n", encoding="utf-8"
    )
    (docs / "WEATHER_DOCS_INDEX.md").write_text(
        "| [old](analysis/old_prompt.md) | `current-reference` | stale |\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_weather_docs, "ROOT", repo)

    errors: list[str] = []
    check_weather_docs.check_superseded_not_indexed_current(errors)

    assert errors == [
        "WEATHER_DOCS_INDEX.md: superseded/retired document remains "
        "current-reference at line 1: analysis/old_prompt.md"
    ]
