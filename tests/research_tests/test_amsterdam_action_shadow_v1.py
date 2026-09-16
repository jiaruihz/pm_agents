from __future__ import annotations

import json

import pytest

from weather_city_runtime.amsterdam_action_shadow_v1 import (
    action_candidates,
    action_health,
    checkpoint_row,
    next_official_break_probability,
    select_shadow_decisions,
    shadow_results,
    validate_policy_artifact,
)
from weather_city_runtime.amsterdam_frozen_shadow_v2 import AmsterdamFrozenShadowRuntime


def _prediction() -> dict:
    pmf = [0.0] * 21
    pmf[11] = 1.0
    return {
        "prediction_row_id": "p-action",
        "event_id": "event-action",
        "forward_epoch_id": "source-epoch",
        "orders": 0,
        "fills": 0,
        "notional": 0,
        "raw_source_lineage": {
            "available_at_utc": "2026-09-03T04:10:00Z",
            "observation_time_utc": "2026-09-03T04:09:00Z",
            "target_date": "2026-09-03",
            "prior_official": {"last_official_native_value": 20.0},
        },
        "feature_vector": {"official_running_max": 20.0, "last_official_native_value": 20.0},
        "B2_PMF": pmf,
        "M1_PMF": pmf,
        "M2_PMF": pmf,
        "abstain_reasons": ["SCORE_ONLY_NO_ACTION", "LAYER_B_NOT_ESTIMABLE"],
        "market_identity": [
            {"market_id": "m", "condition_id": "c", "token_id": "no", "side": "NO", "native_bracket": "20", "target_date": "2026-09-03"},
            {"market_id": "m", "condition_id": "c", "token_id": "yes", "side": "YES", "native_bracket": "20", "target_date": "2026-09-03"},
        ],
    }


def _policy() -> dict:
    return {
        "schema_version": "wcir_amsterdam_prevmax_no_action_shadow_v1",
        "policy_epoch_id": "policy-1",
        "source_model_epoch_id": "source-epoch",
        "reaction_artifact_id": "artifact-1",
        "arms": ["MR0", "B2", "M1", "M2"],
        "action": "BUY_NO_PREVIOUS_OFFICIAL_RUNNING_MAX",
        "forward_start_utc": "2026-09-03T04:00:00Z",
        "entry_checkpoint": "entry_after_p95_latency",
        "primary_exit_checkpoint": "official_plus_30s",
        "selection_shares": 5,
        "min_expected_markout_per_share": 0.0,
    }


def _artifact() -> dict:
    models = {}
    base = ["entry_effective_cost_per_share", "entry_spread", "minutes_to_expected_routine"]
    for arm in ("MR0", "B2", "M1", "M2"):
        names = base if arm == "MR0" else [*base, f"{arm.lower()}_p_next_break"]
        models[arm] = {
            "feature_names": names,
            "feature_means": [0.0] * len(names),
            "feature_scales": [1.0] * len(names),
            "coefficients": [0.0] * len(names),
            "intercept": 0.01,
        }
    return {"schema_version": "wcir_amsterdam_prevmax_no_reaction_ridge_v1", "artifact_id": "artifact-1", "models": models}


def _markout(checkpoint: str, effective: float, row_id: str) -> dict:
    return {
        "prediction_row_id": "p-action",
        "condition_id": "c",
        "token_id": "no",
        "markout_row_id": row_id,
        "materializer_version": "amsterdam_frozen_ws_checkpoint_v1",
        "checkpoint": checkpoint,
        "checkpoint_at_utc": "2026-09-03T04:10:50Z" if checkpoint.startswith("entry") else "2026-09-03T04:31:00Z",
        "book_snapshot_id": row_id,
        "book_valid": True,
        "primary_status": "VALID_TWO_SIDED_DEPTH",
        "best_ask": 0.42,
        "best_bid": 0.40,
        "sweeps": {
            "buy_5": {"fully_executable": True, "effective_value_usd": 2.1},
            "sell_5": {"fully_executable": True, "effective_value_usd": effective},
        },
        "orders": 0,
        "fills": 0,
        "notional": 0,
    }


def _joined(*, settlement: bool = False) -> dict:
    settlements = []
    if settlement:
        settlements = [{
            "prediction_row_id": "p-action", "condition_id": "c", "token_id": "no",
            "settlement_row_id": "s", "status": "linked", "settlement": {"final_price": 1.0},
        }]
    return {
        "prediction_row_id": "p-action",
        "prediction": _prediction(),
        "market_identities": [{"status": "resolved", "identities": _prediction()["market_identity"]}],
        "markouts": [_markout("entry_after_p95_latency", 2.0, "entry"), _markout("official_plus_30s", 2.5, "exit")],
        "labels": [],
        "settlements": settlements,
    }


def test_break_probability_means_next_eham_exceeds_running_max() -> None:
    assert next_official_break_probability(_prediction(), "B2") == 1.0


def test_candidate_is_pit_and_does_not_require_future_label() -> None:
    validate_policy_artifact(_policy(), _artifact())
    rows = action_candidates(_joined(), _policy(), _artifact())
    assert len(rows) == 4
    assert {row["arm"] for row in rows} == {"MR0", "B2", "M1", "M2"}
    assert all(row["side"] == "NO" and row["eligible"] for row in rows)
    assert all(row["orders"] == row["fills"] == row["notional"] == 0 for row in rows)


def test_candidate_fails_closed_on_pre_prediction_book_or_wrong_previous_max() -> None:
    joined = _joined()
    joined["prediction"]["raw_source_lineage"]["available_at_utc"] = "2026-09-03T04:20:00Z"
    assert action_candidates(joined, _policy(), _artifact()) == []
    joined = _joined()
    joined["market_identities"][0]["identities"][0]["native_bracket"] = "19"
    assert action_candidates(joined, _policy(), _artifact()) == []


def test_checkpoint_duplicate_payload_drift_fails_closed() -> None:
    first = _markout("entry_after_p95_latency", 2.0, "same")
    drift = dict(first)
    drift["best_ask"] = 0.99
    row, reason = checkpoint_row([first, drift], token_id="no", checkpoint="entry_after_p95_latency")
    assert row is None and reason == "AMBIGUOUS_ENTRY_AFTER_P95_LATENCY_BOOK"


def test_first_positive_signal_per_arm_date_bracket_and_fee_adjusted_results() -> None:
    candidates = action_candidates(_joined(), _policy(), _artifact())
    decisions = select_shadow_decisions(candidates + candidates, [], _policy())
    assert len(decisions) == 4
    assert select_shadow_decisions(candidates, decisions, _policy()) == []
    results = shadow_results(
        decisions,
        {row["policy_candidate_id"]: row for row in candidates},
        {"p-action": _joined(settlement=True)},
        _policy(),
    )
    assert len(results) == 8
    primary = [row for row in results if row["result_type"] == "OFFICIAL_PLUS_30S_MARKOUT"]
    assert all(row["counterfactual_cost_usd"] == pytest.approx(2.1) for row in primary)
    assert all(row["counterfactual_proceeds_usd"] == pytest.approx(2.5) for row in primary)
    assert all(row["fee_adjusted_pnl_usd"] == pytest.approx(0.4) for row in primary)
    metrics = action_health(candidates, decisions, results)
    assert metrics["B2"]["signals"] == 1 and metrics["B2"]["win_rate"] == 1.0


def test_exit_before_entry_does_not_create_markout_result() -> None:
    joined = _joined()
    joined["markouts"][1]["checkpoint_at_utc"] = "2026-09-03T04:00:00Z"
    candidates = action_candidates(joined, _policy(), _artifact())
    decisions = select_shadow_decisions(candidates, [], _policy())
    results = shadow_results(
        decisions,
        {row["policy_candidate_id"]: row for row in candidates},
        {"p-action": joined},
        _policy(),
    )
    assert results == []


def test_runtime_action_journals_are_idempotent_and_zero_notional(tmp_path) -> None:
    runtime = AmsterdamFrozenShadowRuntime(tmp_path, action_policy=_policy(), reaction_artifact=_artifact())
    runtime.ingest_predictions([_prediction()])
    runtime.markouts.put(_markout("entry_after_p95_latency", 2.0, "entry"))
    runtime.markouts.put(_markout("official_plus_30s", 2.5, "exit"))
    first = runtime.materialize()
    assert first["policy_candidates"] == 4
    assert first["shadow_decisions"] == 4
    assert first["shadow_results"] == 4
    second = runtime.materialize()
    assert second["policy_candidates"] == second["shadow_decisions"] == second["shadow_results"] == 0
    health = runtime.health(epoch="source-epoch")
    assert health["execution_mode"] == "zero_notional_action_shadow"
    assert health["action_shadow_enabled"] is True
    assert health["orders"] == health["fills"] == health["notional"] == health["actual_notional"] == 0
    ledger = json.loads((tmp_path / "policy_ledger_snapshot.json").read_text())
    assert ledger["actual_notional"] == 0.0
