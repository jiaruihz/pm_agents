import json

import pytest

from scripts.ops.weather_city_execution_handoff_v1 import materialize
from src.strategies.weather_edge_v1.execution.wcir import (
    WCIRExecutionCompatibilityError,
    build_wcir_execution_handoff,
)
from weather_city_runtime.contracts import SignalCandidate, TradeIntent


def _candidate(*, token_id="token-no"):
    return SignalCandidate.create(
        checkpoint_id="checkpoint-1",
        trigger_event_id="event-1",
        city="Helsinki",
        target_date="2026-08-02",
        decision_ts_utc="2026-08-02T10:00:00Z",
        target_id="condition-1:NO",
        target_kind="market_expression",
        expression_id="condition-1:NO",
        condition_id="condition-1",
        market_id="market-1",
        token_id=token_id,
        bracket="21",
        side="NO",
        p_model=0.72,
        market_p=0.61,
        executable_cost=0.62,
        model_id="model-1",
        model_artifact_id="artifact-1",
        feature_set_id="features-1",
        feature_book_snapshot_id="feature-book-1",
        execution_book_snapshot_id="execution-book-1",
        strategy_key="weather.city_intraday_probability",
        policy_id="policy-1",
        candidate_status="scored",
        blocker_reason=None,
        selected=True,
        market_evidence_status="two_sided",
    )


def _intent(candidate, *, mode="zero_notional", size=0.0, profile="city_probability_zero_notional_v1"):
    return TradeIntent.create(
        candidate_id=candidate.candidate_id,
        condition_id=candidate.condition_id,
        token_id=candidate.token_id,
        side="BUY",
        requested_size=size,
        sizing_profile="fixed_shares_v1",
        execution_profile=profile,
        max_cost=0.63,
        ttl_seconds=120,
        dedupe_key="Helsinki|2026-08-02|21|NO|model-1",
        exposure_bucket="Helsinki|2026-08-02",
        mode=mode,
    )


def test_zero_notional_is_record_only_and_does_not_require_fake_profile():
    candidate = _candidate()
    handoff = build_wcir_execution_handoff(
        trade_intent=_intent(candidate),
        signal_candidate=candidate,
        strategy_instance="wcir-v3",
        config_id="config-v3",
    )

    assert handoff.status == "record_only"
    assert handoff.execution_intent is None


def test_positive_shadow_maps_to_existing_execution_intent():
    candidate = _candidate()
    handoff = build_wcir_execution_handoff(
        trade_intent=_intent(candidate, mode="shadow", size=5.0, profile="single_side_maker_v1"),
        signal_candidate=candidate,
        strategy_instance="wcir-paper-v1",
        config_id="config-v3",
    )

    mapped = handoff.execution_intent
    assert handoff.status == "ready_for_paper_runtime"
    assert mapped is not None
    assert mapped.venue_side == "BUY"
    assert mapped.outcome_side == "NO"
    assert str(mapped.total_shares) == "5.0"
    assert str(mapped.constraints.price_cap) == "0.63"
    assert mapped.constraints.deadline_utc == "2026-08-02T10:02:00Z"
    assert mapped.data_epoch_ref == candidate.checkpoint_id


def test_token_mismatch_fails_instead_of_guessing():
    candidate = _candidate()
    intent = _intent(candidate)
    other = _candidate(token_id="other-token")
    with pytest.raises(WCIRExecutionCompatibilityError, match="token identity mismatch"):
        build_wcir_execution_handoff(
            trade_intent=intent,
            signal_candidate=other,
            strategy_instance="wcir-v3",
            config_id="config-v3",
        )


def test_materializer_is_append_only_and_dedupes(tmp_path):
    candidate = _candidate()
    intent = _intent(candidate)
    bundles = tmp_path / "decision_bundles.jsonl"
    intents = tmp_path / "trade_intents.jsonl"
    output = tmp_path / "execution_handoffs.jsonl"
    bundles.write_text(json.dumps({"signal_candidate": candidate.to_dict()}) + "\n")
    intents.write_text(json.dumps(intent.to_dict()) + "\n")

    first = materialize(
        bundle_path=bundles,
        intent_path=intents,
        output_path=output,
        strategy_instance="wcir-v3",
        config_id="config-v3",
    )
    second = materialize(
        bundle_path=bundles,
        intent_path=intents,
        output_path=output,
        strategy_instance="wcir-v3",
        config_id="config-v3",
    )

    assert first == {
        "input_intents": 1,
        "written": 1,
        "deduped": 0,
        "record_only": 1,
        "ready_for_paper_runtime": 0,
        "blocked": 0,
    }
    assert second["written"] == 0
    assert second["deduped"] == 1
    assert len(output.read_text().splitlines()) == 1
