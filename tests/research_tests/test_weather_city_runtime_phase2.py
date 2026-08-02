from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.strategies.weather_city_probability_shadow.core import OUTPUT_SCHEMA_VERSION
from weather_city_runtime import (
    LegacyDecisionBundle,
    ModelOutput,
    SignalCandidate,
    TemporaryCanonicalBridge,
    TradeIntent,
    legacy_bundle_from_evaluation,
    legacy_trade_intent_from_paper_intent,
)
from scripts.analysis.market_structure_edge.bridge_city_intraday_decisions_v1 import (
    main as bridge_main,
)


def _evaluation(**overrides):
    row = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "record_kind": "evaluation",
        "evaluation_id": "legacy-eval-1",
        "city": "Helsinki",
        "target_date": "2026-08-01",
        "decision_ts_utc": "2026-08-01T05:42:00Z",
        "source_obs_ts_utc": "2026-08-01T05:40:00Z",
        "current_bracket": 18,
        "market_side": "NO",
        "market_probability": 0.4,
        "market_entry_price": 0.42,
        "model_probability": 0.7,
        "model_id": "helsinki-v1",
        "feature_coverage": 1.0,
        "missing_features": [],
        "features": {"remaining_heat": 0.25},
        "market": {
            "condition_id": "condition-18",
            "market_id": "market-18",
            "token_id": "token-no-18",
            "outcome": "no",
            "book_snapshot_id": "book-18",
        },
        "lineage": {
            "source": "fmi",
            "source_first_seen_at_utc": "2026-08-01T05:41:00Z",
            "source_payload_hash": "source-payload",
            "book_snapshot_id": "book-18",
            "model_artifact_sha256": "artifact-18",
            "profile_id": "helsinki_remaining_heat_v1",
            "source_input_ref": {"physical_path": "source.jsonl", "physical_line": 1},
        },
        "evaluation_status": "scored",
        "not_scorable_reason": None,
        "effective_cost_per_share": 0.43,
        "edge_after_fee": 0.27,
        "edge_threshold": 0.05,
        "would_enter": True,
    }
    row.update(overrides)
    return row


def test_legacy_adapter_emits_versioned_model_candidate_and_stable_identity() -> None:
    first = legacy_bundle_from_evaluation(_evaluation())
    second = legacy_bundle_from_evaluation(_evaluation())

    assert first.model_output == second.model_output
    assert first.signal_candidate == second.signal_candidate
    assert first.signal_candidate.selected is False
    assert first.signal_candidate.condition_id == "condition-18"
    assert first.signal_candidate.execution_book_snapshot_id == "book-18"
    assert first.signal_candidate.candidate_id
    assert "pnl" not in json.dumps(first.signal_candidate.to_dict()).lower()


def test_unselected_and_one_sided_rows_remain_candidate_denominator() -> None:
    unselected = legacy_bundle_from_evaluation(_evaluation(would_enter=False))
    one_sided = legacy_bundle_from_evaluation(
        _evaluation(
            market_probability=None,
            model_probability=None,
            market_entry_price=None,
            effective_cost_per_share=None,
            evaluation_status="not_scorable",
            not_scorable_reason="one_sided_market_probability_interval",
            would_enter=False,
        )
    )

    assert unselected.signal_candidate.candidate_status == "scored"
    assert unselected.signal_candidate.selected is False
    assert one_sided.signal_candidate.candidate_status == "blocked"
    assert (
        one_sided.signal_candidate.blocker_reason
        == "one_sided_market_probability_interval"
    )
    assert one_sided.signal_candidate.selected is False


def test_execution_profiles_share_candidate_but_produce_distinct_intents() -> None:
    paper = {**_evaluation(), "record_kind": "paper_intent", "position_key": "position-1"}
    maker = legacy_trade_intent_from_paper_intent(
        paper, execution_profile="maker-a"
    )
    taker = legacy_trade_intent_from_paper_intent(
        paper, execution_profile="taker-b"
    )

    assert maker.candidate_id == taker.candidate_id
    assert maker.intent_id != taker.intent_id
    assert maker.requested_size == taker.requested_size == 0.0
    assert maker.mode == taker.mode == "zero_notional"


def test_trade_intent_contract_refuses_plugin_granted_live_mode() -> None:
    values = legacy_trade_intent_from_paper_intent(
        {**_evaluation(), "record_kind": "paper_intent", "position_key": "position-1"}
    ).to_dict()
    values.pop("intent_id")
    values["mode"] = "live"
    with pytest.raises(ValueError, match="cannot grant live"):
        TradeIntent.create(**values)


def test_temporary_bridge_is_incremental_and_reconciles_exactly(tmp_path: Path) -> None:
    bundle = legacy_bundle_from_evaluation(_evaluation())
    bridge = TemporaryCanonicalBridge(tmp_path / "canonical.db")

    first = bridge.append([bundle])
    second = bridge.append([bundle])

    assert first["inserted_candidates"] == 1
    assert first["candidate_delta"] == 0
    assert second["inserted_candidates"] == 0
    assert second["existing_candidates"] == 1
    assert second["candidate_delta"] == 0
    conn = sqlite3.connect(tmp_path / "canonical.db")
    try:
        row = conn.execute(
            "SELECT candidate_id, candidate_status, policy_selected, target_id, "
            "token_id, feature_book_snapshot_id, execution_book_snapshot_id, policy_id "
            "FROM fact_signal_candidates"
        ).fetchone()
        private_tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND (lower(name) LIKE '%helsinki%' OR lower(name) LIKE '%tokyo%')"
        ).fetchall()
    finally:
        conn.close()
    assert row == (
        bundle.signal_candidate.candidate_id,
        "scored",
        0,
        bundle.signal_candidate.target_id,
        "token-no-18",
        "book-18",
        "book-18",
        "legacy_edge_threshold",
    )
    assert private_tables == []


def test_bridge_accepts_legacy_vnext_and_mixed_without_duplicate_facts(
    tmp_path: Path,
) -> None:
    legacy = legacy_bundle_from_evaluation(_evaluation())
    vnext = LegacyDecisionBundle(
        information_event=dict(legacy.information_event),
        state_checkpoint=dict(legacy.state_checkpoint),
        model_output=ModelOutput.from_dict(legacy.model_output.to_dict()),
        signal_candidate=SignalCandidate.from_dict(
            legacy.signal_candidate.to_dict()
        ),
    )
    bridge = TemporaryCanonicalBridge(tmp_path / "mixed.db")

    result = bridge.append([legacy, vnext])

    assert result["raw_candidate_rows"] == 2
    assert result["raw_unique_candidates"] == 1
    assert result["input_duplicate_candidates"] == 1
    assert result["canonical_candidates"] == 1
    assert result["candidate_delta"] == 0


def test_bridge_rejects_production_canonical_path() -> None:
    with pytest.raises(ValueError, match="research/temp DB"):
        TemporaryCanonicalBridge(
            Path(__file__).resolve().parents[2] / "runtime" / "weather.db"
        )


def test_bridge_rejects_cross_object_lineage_mismatch(tmp_path: Path) -> None:
    bundle = legacy_bundle_from_evaluation(_evaluation())
    broken = LegacyDecisionBundle(
        information_event=bundle.information_event,
        state_checkpoint=bundle.state_checkpoint,
        model_output=bundle.model_output,
        signal_candidate=replace(bundle.signal_candidate, city="Tokyo"),
    )
    with pytest.raises(ValueError, match="city mismatch"):
        TemporaryCanonicalBridge(tmp_path / "broken.db").append([broken])
    assert not (tmp_path / "broken.db").exists()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_bridge_cli_handles_legacy_vnext_and_mixed_journals(tmp_path: Path) -> None:
    evaluation = _evaluation()
    paper = {**evaluation, "record_kind": "paper_intent", "position_key": "position-1"}
    legacy_eval_path = tmp_path / "evaluations.jsonl"
    legacy_intent_path = tmp_path / "paper_intents.jsonl"
    _write_jsonl(legacy_eval_path, [evaluation])
    _write_jsonl(legacy_intent_path, [paper])

    bundle = legacy_bundle_from_evaluation(evaluation)
    intent = legacy_trade_intent_from_paper_intent(paper)
    vnext_bundle_path = tmp_path / "vnext_bundles.jsonl"
    vnext_intent_path = tmp_path / "vnext_intents.jsonl"
    _write_jsonl(
        vnext_bundle_path,
        [{
            "information_event": bundle.information_event,
            "state_checkpoint": bundle.state_checkpoint,
            "model_output": bundle.model_output.to_dict(),
            "signal_candidate": replace(
                bundle.signal_candidate, selected=True
            ).to_dict(),
        }],
    )
    _write_jsonl(vnext_intent_path, [intent.to_dict()])

    cases = {
        "legacy": [
            "--legacy-evaluations", str(legacy_eval_path),
            "--legacy-paper-intents", str(legacy_intent_path),
        ],
        "vnext": [
            "--vnext-bundles", str(vnext_bundle_path),
            "--vnext-intents", str(vnext_intent_path),
        ],
        "mixed": [
            "--legacy-evaluations", str(legacy_eval_path),
            "--vnext-bundles", str(vnext_bundle_path),
        ],
    }
    for name, arguments in cases.items():
        output = tmp_path / name
        assert bridge_main([*arguments, "--output-dir", str(output)]) == 0
        summary = json.loads((output / "bridge_summary.json").read_text())
        assert summary["outputs"]["signal_candidates"] == 1
        if name in {"legacy", "vnext"}:
            assert summary["outputs"]["selected"] == 1
        assert summary["canonical_reconciliation"]["candidate_delta"] == 0
        assert summary["signal_funnel"]["raw_candidates"] == 1
        assert summary["evidence_funnel"]["fill"] == "not_available_phase2"
        assert len((output / "signal_candidates.jsonl").read_text().splitlines()) == 1
    assert json.loads(
        (tmp_path / "mixed" / "bridge_summary.json").read_text()
    )["input_modes"] == ["legacy", "vnext"]

    repeated = tmp_path / "legacy_repeated"
    assert bridge_main(
        [*cases["legacy"], "--output-dir", str(repeated)]
    ) == 0
    for filename in (
        "model_outputs.jsonl",
        "signal_candidates.jsonl",
        "trade_intents.jsonl",
        "trade_intent_blockers.jsonl",
        "bridge_summary.json",
    ):
        assert (tmp_path / "legacy" / filename).read_bytes() == (
            repeated / filename
        ).read_bytes()


def test_three_city_phase0_payloads_keep_coverage_without_fake_amsterdam_candidate() -> None:
    fixture_root = ROOT / "tests" / "fixtures" / "weather_city_intraday_phase0"
    blocked = []
    for city, filename, source in (
        ("Helsinki", "helsinki_one_sided_book.json", "fmi"),
        ("Tokyo", "tokyo_one_sided_book.json", "jma_amedas"),
    ):
        fixture = json.loads((fixture_root / filename).read_text())
        market = fixture["records"]
        row = _evaluation(
            city=city,
            target_date=market["target_date"],
            current_bracket=market["reference_market_value"],
            decision_ts_utc=market["book_fetched_at_utc"],
            source_obs_ts_utc=market["book_fetched_at_utc"],
            market_side=str(market["outcome"]).upper(),
            market_probability=None,
            market_entry_price=None,
            model_probability=None,
            market={**market, "book_snapshot_id": f"fixture-{city.lower()}"},
            lineage={
                "source": source,
                "source_first_seen_at_utc": market["book_fetched_at_utc"],
                "book_snapshot_id": f"fixture-{city.lower()}",
                "model_artifact_sha256": f"artifact-{city.lower()}",
                "profile_id": f"fixture-{city.lower()}",
            },
            evaluation_status="not_scorable",
            not_scorable_reason="one_sided_market_probability_interval",
            effective_cost_per_share=None,
            would_enter=False,
        )
        blocked.append(legacy_bundle_from_evaluation(row).signal_candidate)
    assert [candidate.city for candidate in blocked] == ["Helsinki", "Tokyo"]
    assert all(candidate.candidate_status == "blocked" for candidate in blocked)

    amsterdam = json.loads(
        (fixture_root / "amsterdam_interval_initial.json").read_text()
    )["records"]
    output = ModelOutput.create(
        checkpoint_id="amsterdam-checkpoint",
        trigger_event_id=amsterdam["information_event_id"],
        city="Amsterdam",
        target_date=amsterdam["target_date"],
        decision_ts_utc=amsterdam["available_at_utc"],
        target_id="knmi_interval_remaining_heat",
        target_kind="physical_path",
        p_model=None,
        model_id="not_available",
        model_artifact_id="not_available",
        feature_set_id="interval-contract-only",
        input_refs=({"fixture": "amsterdam_interval_initial.json"},),
        scorable_status="not_scorable",
        blocker_reason="no_deployed_amsterdam_model_output",
        market_feature_role="none",
        market_feature_clock="none",
    )
    invalid_candidate = blocked[0].to_dict()
    invalid_candidate["target_kind"] = output.target_kind
    with pytest.raises(ValueError, match="market_expression"):
        SignalCandidate.from_dict(invalid_candidate)
