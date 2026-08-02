import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from weather_model_evaluation import (
    FixtureCheckpointBuilder,
    FixtureInputCatalog,
    PREDICTION_SCHEMA_VERSION,
    ReplayRunner,
    build_evaluation_report,
    validate_prediction_row,
)


FIXTURES = ROOT / "tests" / "fixtures" / "weather_city_intraday_phase0"


def _run_fixture_replay(paths: list[Path] | None = None):
    return ReplayRunner(
        input_provider=FixtureInputCatalog(paths or list(FIXTURES.glob("*.json"))),
        checkpoint_builder=FixtureCheckpointBuilder(),
    ).run()


def test_three_city_fixture_replay_is_deterministic_and_pit_ordered() -> None:
    paths = list(FIXTURES.glob("*.json"))
    first = _run_fixture_replay(paths)
    second = _run_fixture_replay(list(reversed(paths)))

    assert first.manifest == second.manifest
    assert first.rows == second.rows
    assert first.manifest["cities"] == ["Amsterdam", "Helsinki", "Tokyo"]
    assert first.manifest["event_count"] == 9
    assert first.manifest["prediction_row_count"] == 9
    assert {row["event_payload_kind"] for row in first.rows} >= {
        "point_observation",
        "interval",
        "revision",
    }
    assert [row["decision_ts_utc"] for row in first.rows] == sorted(
        row["decision_ts_utc"] for row in first.rows
    )


def test_revision_lineage_and_cross_day_physical_partition_survive_replay() -> None:
    result = _run_fixture_replay()
    amsterdam = [row for row in result.rows if row["city"] == "Amsterdam"]
    assert [row["event_payload_kind"] for row in amsterdam] == [
        "interval",
        "revision",
    ]
    assert amsterdam[1]["runtime_lineage"]["state_event_ids"] == [
        "7dddd9a333012617ca74c1a42ba50e2c583becf110d02e7d0ac08ba2e35ca6e1"
    ]

    cross_day = [
        row
        for row in result.rows
        if row["event_payload_kind"] == "cross_day_partition"
    ]
    assert len(cross_day) == 2
    assert {row["target_date"] for row in cross_day} == {"2026-08-01"}
    assert {row["input_refs"][0]["physical_shard"] for row in cross_day} == {
        "2026-07-31"
    }


def _prediction_row(**overrides):
    row = {
        "schema_version": PREDICTION_SCHEMA_VERSION,
        "city": "Helsinki",
        "target_date": "2026-08-01",
        "decision_ts_utc": "2026-08-01T05:00:00Z",
        "target_id": "eod_cross_d1",
        "target_kind": "physical_path",
        "p_model": 0.8,
        "label": 1,
        "split": "frozen_forward",
        "model_id": "model-v1",
        "feature_set_id": "features-v1",
        "pit_provenance": "live_capture",
        "checkpoint_id": "checkpoint-1",
        "scorable_status": "scorable",
        "coverage_status": "complete",
        "market_p": 0.6,
        "event_id": "event-1",
        "event_payload_kind": "point_observation",
        "event_available_at_utc": "2026-08-01T04:59:00Z",
        "input_refs": [{"path": "fixture"}],
        "runtime_lineage": {"repo_sha": "abc"},
    }
    row.update(overrides)
    return row


def test_prediction_contract_rejects_future_information() -> None:
    with pytest.raises(ValueError, match="not available"):
        validate_prediction_row(
            _prediction_row(event_available_at_utc="2026-08-01T05:01:00Z")
        )


def test_fixed_report_uses_same_denominator_and_keeps_execution_separate() -> None:
    rows = [
        _prediction_row(
            plan_id="plan-1",
            order_id="order-1",
            fill_id="fill-1",
            liquidity_role="maker",
            settlement_status="settled",
            pnl_usd_at_fill=1.25,
            executable_cost=0.65,
            raw_candidate_count=1,
            canonical_candidate_count=1,
            raw_fill_count=1,
            canonical_fill_count=1,
        ),
        _prediction_row(
            target_date="2026-08-02",
            checkpoint_id="checkpoint-2",
            event_id="event-2",
            p_model=0.2,
            market_p=None,
            label=0,
        ),
    ]
    report = build_evaluation_report(rows)

    assert report["prediction_quality"]["rows"] == 2
    assert report["same_denominator_market_baseline"]["rows"] == 1
    assert report["execution"] == {
        "status": "available",
        "plans": 1,
        "orders": 1,
        "fills": 1,
        "maker_fills": 1,
        "taker_fills": 0,
        "fee_adjusted_realized_pnl_usd": 1.25,
    }
    assert report["raw_canonical_reconciliation"]["candidate_delta"] == 0
    assert report["raw_canonical_reconciliation"]["fill_delta"] == 0


def test_common_cli_writes_only_requested_output_and_repeats_hash(tmp_path: Path) -> None:
    script = (
        ROOT
        / "scripts"
        / "analysis"
        / "market_structure_edge"
        / "replay_city_intraday_evidence_v1.py"
    )
    hashes = []
    for name in ("first", "second"):
        output = tmp_path / name
        completed = subprocess.run(
            [
                sys.executable,
                str(script),
                "--fixtures",
                str(FIXTURES),
                "--output-dir",
                str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        stdout = json.loads(completed.stdout)
        hashes.append(stdout["manifest"]["output_hash"])
        assert {path.name for path in output.iterdir()} == {
            "prediction_table.jsonl",
            "replay_manifest.json",
            "evaluation_report.json",
        }
    assert hashes[0] == hashes[1]


def test_common_cli_refuses_non_research_runtime_output() -> None:
    script = (
        ROOT
        / "scripts"
        / "analysis"
        / "market_structure_edge"
        / "replay_city_intraday_evidence_v1.py"
    )
    forbidden = ROOT / "runtime" / "forbidden_phase1_test_output"
    completed = subprocess.run(
        [sys.executable, str(script), "--output-dir", str(forbidden)],
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "research/temp only" in completed.stderr
    assert not forbidden.exists()
