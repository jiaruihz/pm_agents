"""Gate R WP5 deterministic market comparison adversarial coverage."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.polymarket_alpha.contracts import (
    BookLevel, CandidateState, MarketComparison, MarketComparisonV2,
    canonical_json, content_sha256,
    stable_record_id,
)
from src.polymarket_alpha.decision import DecisionLedgerError, RankConfig, build_ranked_ledger
from src.polymarket_alpha.books import EXECUTABLE_COST_VERSION
from src.polymarket_alpha.research import (
    DeterministicMarketAssessmentCompiler,
    MarketComparisonError,
    MarketComparisonPolicy,
    import_research_result,
)
from src.polymarket_alpha.research.market import freeze_market_research_packet
from src.polymarket_alpha.rules.gates import evaluate_gate_b
from tests.polymarket_alpha.test_market_research_p0_08c import NOW, _demand_and_book


def _policy(**updates) -> MarketComparisonPolicy:
    values = dict(policy_id="fee_slippage", version="v1", policy_size=Decimal("10"), max_book_age_seconds=60,
                  fee_rate=Decimal("0.01"), slippage_buffer=Decimal("0.005"))
    values.update(updates)
    return MarketComparisonPolicy(**values)


def _assessment(*, values=None, policy=None, seconds=5):
    values = _demand_and_book() if values is None else values
    packet = freeze_market_research_packet(
        **dict(zip(
            ("candidate", "contract", "gate_a", "blind_packet", "blind_result",
             "blind_import_receipt", "demand", "book_receipt", "snapshot"),
            values,
            strict=True,
        )),
        build_run_identity="market-packet-run",
        created_at=NOW + timedelta(seconds=3),
    )
    return DeterministicMarketAssessmentCompiler().compile(
        packet=packet, accepted_blind_result=values[4], book_receipt=values[7], policy=_policy() if policy is None else policy,
        as_of=NOW + timedelta(seconds=seconds), created_at=NOW + timedelta(seconds=seconds + 1), run_id="wp5-test",
    ), packet, values


def _reseal_v2(raw: dict[str, object]) -> MarketComparisonV2:
    payload = {
        key: value
        for key, value in raw.items()
        if key not in {"record_id", "comparison_id", "comparison_sha256"}
    }
    raw["record_id"] = raw["comparison_id"] = stable_record_id(
        "market_comparison", payload
    )
    raw["comparison_sha256"] = content_sha256(payload)
    return MarketComparisonV2.model_validate(raw)


def test_ready_comparison_recomputes_content_identity_and_imports_existing_market_path() -> None:
    assessment, packet, values = _assessment()
    assert assessment.result is not None
    comparison = assessment.comparison
    assert comparison.status.value == "READY"
    assert comparison.accepted_blind_result_id == values[4].result_id
    assert comparison.accepted_blind_result_sha256 == values[4].canonical_sha256
    assert comparison.rule_hash == packet.rule_contract.rule_hash
    assert comparison.yes_edge_low <= comparison.yes_edge_mid <= comparison.yes_edge_high
    assert comparison.no_edge_low <= comparison.no_edge_mid <= comparison.no_edge_high
    assert comparison.fee_model_version == EXECUTABLE_COST_VERSION
    assert comparison.yes_buy_vwap == Decimal("0.50")
    assert comparison.yes_taker_fee == Decimal("0.0250")
    assert comparison.yes_all_in_buy_price == Decimal("0.5075")
    replay = MarketComparisonV2.model_validate(comparison.model_dump(mode="python"))
    assert replay.record_id == comparison.record_id and replay.comparison_sha256 == comparison.comparison_sha256
    imported = import_research_result(packet=packet, submitted_bytes=canonical_json(assessment.result).encode(),
        source_contents={}, imported_at=NOW + timedelta(seconds=8), run_id="wp5-import", submitted_artifact_locator="results/market.json")
    assert imported.result is not None and imported.receipt.status.value == "ACCEPTED"
    estimate = imported.result.probability_estimate
    blind = values[4].probability_estimate
    assert (estimate.p_event_yes_low, estimate.p_event_yes_mid, estimate.p_event_yes_high) == (blind.p_event_yes_low, blind.p_event_yes_mid, blind.p_event_yes_high)
    assert imported.result.extensions["probability_update"] == "NONE"


@pytest.mark.parametrize("mutation", ["hash", "blind", "rule", "book"])
def test_content_binding_tamper_is_rejected(mutation: str) -> None:
    assessment, _, _ = _assessment()
    raw = assessment.comparison.model_dump(mode="python")
    if mutation == "hash":
        raw["comparison_sha256"] = "0" * 64
    elif mutation == "blind":
        raw["accepted_blind_result_sha256"] = "1" * 64
    elif mutation == "rule":
        raw["rule_hash"] = "2" * 64
    else:
        raw["orderbook_snapshot_sha256"] = "3" * 64
    with pytest.raises(ValidationError, match="recomputed"):
        MarketComparisonV2.model_validate(raw)


def test_semantic_edge_forgery_fails_even_with_recomputed_identity() -> None:
    assessment, _, _ = _assessment()
    raw = assessment.comparison.model_dump(mode="python")
    raw["yes_edge_mid"] = raw["yes_edge_mid"] + Decimal("0.01")
    payload = {key: value for key, value in raw.items()
               if key not in {"record_id", "comparison_id", "comparison_sha256"}}
    raw["record_id"] = raw["comparison_id"] = stable_record_id("market_comparison", payload)
    raw["comparison_sha256"] = content_sha256(payload)
    with pytest.raises(ValidationError, match="deterministically recomputed"):
        MarketComparisonV2.model_validate(raw)


def test_released_v1_market_comparison_payload_remains_parseable() -> None:
    assessment, _, _ = _assessment()
    raw = assessment.comparison.model_dump(mode="python")
    for field in (
        "yes_buy_fills",
        "no_buy_fills",
        "yes_taker_fee",
        "no_taker_fee",
        "yes_all_in_buy_price",
        "no_all_in_buy_price",
        "fee_model_version",
    ):
        raw.pop(field)
    raw["source_version"] = "gate_r_wp5_v1"
    yes_cost = raw["yes_buy_vwap"] * (Decimal("1") + raw["fee_rate"]) + raw[
        "slippage_buffer"
    ]
    no_cost = raw["no_buy_vwap"] * (Decimal("1") + raw["fee_rate"]) + raw[
        "slippage_buffer"
    ]
    raw.update(
        yes_edge_low=raw["blind_p_yes_low"] - yes_cost,
        yes_edge_mid=raw["blind_p_yes_mid"] - yes_cost,
        yes_edge_high=raw["blind_p_yes_high"] - yes_cost,
        no_edge_low=(Decimal("1") - raw["blind_p_yes_high"]) - no_cost,
        no_edge_mid=(Decimal("1") - raw["blind_p_yes_mid"]) - no_cost,
        no_edge_high=(Decimal("1") - raw["blind_p_yes_low"]) - no_cost,
    )
    payload = {
        key: value
        for key, value in raw.items()
        if key not in {"record_id", "comparison_id", "comparison_sha256"}
    }
    raw["record_id"] = raw["comparison_id"] = stable_record_id(
        "market_comparison", payload
    )
    raw["comparison_sha256"] = content_sha256(payload)
    legacy = MarketComparison.model_validate(raw)
    assert legacy.source_version == "gate_r_wp5_v1"
    assert not hasattr(legacy, "fee_model_version")


@pytest.mark.parametrize("case", ["ttl_boundary", "policy_depth"])
def test_unusable_book_is_non_advancing_and_requests_refresh(case: str) -> None:
    assessment, _, _ = (
        _assessment(seconds=61)
        if case == "ttl_boundary"
        else _assessment(policy=_policy(policy_size=Decimal("25")))
    )
    assert assessment.result is None
    assert assessment.comparison.status.value == "BOOK_REFRESH_REQUIRED"
    assert assessment.comparison.yes_edge_mid is None
    expected_reason = "BOOK_REFRESH_REQUIRED" if case == "ttl_boundary" else "INSUFFICIENT_POLICY_DEPTH"
    assert expected_reason in assessment.comparison.reason_codes


def test_one_sided_book_is_rejected_by_existing_packet_owner_before_comparison() -> None:
    values = list(_demand_and_book())
    values[8] = values[8].model_copy(update={"yes_leg": values[8].yes_leg.model_copy(update={"bids": ()})})
    values[7] = values[7].model_copy(update={"orderbook_snapshot_sha256": values[8].canonical_sha256})
    with pytest.raises(ValueError, match="one-sided"):
        _assessment(values=tuple(values))


def test_cross_outcome_inconsistency_blocks_without_false_ttl_reason() -> None:
    values = list(_demand_and_book())
    snapshot = values[8]
    yes_leg = snapshot.yes_leg.model_copy(update={
        "bids": (snapshot.yes_leg.bids[0].model_copy(update={"price": Decimal("0.60")}),),
        "asks": (snapshot.yes_leg.asks[0].model_copy(update={"price": Decimal("0.70")}),),
    })
    no_leg = snapshot.no_leg.model_copy(update={
        "bids": (snapshot.no_leg.bids[0].model_copy(update={"price": Decimal("0.60")}),),
        "asks": (snapshot.no_leg.asks[0].model_copy(update={"price": Decimal("0.70")}),),
    })
    values[8] = snapshot.model_copy(update={"yes_leg": yes_leg, "no_leg": no_leg})
    values[7] = values[7].model_copy(update={"orderbook_snapshot_sha256": values[8].canonical_sha256})
    assessment, _, _ = _assessment(values=tuple(values))
    assert assessment.result is None
    assert assessment.comparison.status.value == "NON_ADVANCING"
    assert assessment.comparison.reason_codes == ("CROSS_OUTCOME_INCONSISTENT",)


def test_blind_packet_or_receipt_mismatch_and_probability_overwrite_fail_closed() -> None:
    assessment, packet, values = _assessment()
    with pytest.raises((MarketComparisonError, ValidationError), match="accepted.*Blind|Blind baseline"):
        DeterministicMarketAssessmentCompiler().compile(packet=packet.model_copy(update={"blind_result_id": "research_result:" + "0" * 64}),
            accepted_blind_result=values[4], book_receipt=values[7], policy=_policy(), as_of=NOW + timedelta(seconds=5),
            created_at=NOW + timedelta(seconds=6), run_id="bad")
    assert assessment.result is not None
    raw = assessment.result.model_dump(mode="python")
    raw["probability_estimate"]["p_event_yes_mid"] = Decimal("0.99")
    # The envelope remains structurally valid, but any result replacement changes its immutable id;
    # existing importer receives only the compiler-produced content-derived result.
    with pytest.raises(ValidationError):
        type(assessment.result).model_validate(raw)

    forged_receipt = values[7].model_copy(update={"extensions": {"tampered": True}})
    with pytest.raises(MarketComparisonError, match="receipt hash"):
        DeterministicMarketAssessmentCompiler().compile(
            packet=packet, accepted_blind_result=values[4], book_receipt=forged_receipt,
            policy=_policy(), as_of=NOW + timedelta(seconds=5),
            created_at=NOW + timedelta(seconds=6), run_id="bad-receipt",
        )

    with pytest.raises(MarketComparisonError, match="cannot precede"):
        DeterministicMarketAssessmentCompiler().compile(
            packet=packet, accepted_blind_result=values[4], book_receipt=values[7],
            policy=_policy(), as_of=NOW + timedelta(seconds=2),
            created_at=NOW + timedelta(seconds=4), run_id="bad-clock",
        )


def test_new_book_rule_or_blind_inputs_create_new_revision_and_duplicate_is_idempotent() -> None:
    first, _, _ = _assessment()
    duplicate, _, _ = _assessment()
    assert duplicate.comparison.record_id == first.comparison.record_id
    changed_policy, _, _ = _assessment(policy=_policy(version="v2"))
    assert changed_policy.comparison.record_id != first.comparison.record_id
    later, _, _ = _assessment(seconds=6)
    assert later.comparison.record_id != first.comparison.record_id
    with pytest.raises(ValueError, match="disabled"):
        _policy(optional_critique_enabled=True)


def test_compiled_result_reaches_rule_b_and_no_order_prediction_ledger() -> None:
    values = list(_demand_and_book())
    values[8] = values[8].model_copy(update={"quality_flags": ()})
    values[7] = values[7].model_copy(update={"orderbook_snapshot_sha256": values[8].canonical_sha256})
    assessment, packet, values = _assessment(values=tuple(values))
    assert assessment.result is not None
    imported = import_research_result(
        packet=packet,
        submitted_bytes=canonical_json(assessment.result).encode(),
        source_contents={},
        imported_at=NOW + timedelta(seconds=8),
        run_id="wp5-ledger-import",
        submitted_artifact_locator="results/market-ledger.json",
    )
    assert imported.result is not None
    gate_b = evaluate_gate_b(
        values[1], values[2], market_packet_id=packet.record_id,
        market_packet_rule_hash=packet.rule_contract.rule_hash,
        market_packet_contract_revision_id=packet.rule_contract.contract_revision_id,
        run_id="wp5-gate-b", evaluated_at=NOW + timedelta(seconds=9),
    )
    ranked = build_ranked_ledger(
        candidate=values[0].model_copy(update={"state": CandidateState.RULE_B_PASSED}),
        contract=values[1], gate_a=values[2], gate_b=gate_b,
        blind_result=values[4], blind_receipt=values[5], market_packet=packet,
        market_result=imported.result, market_receipt=imported.receipt, book=values[8],
        market_comparison=assessment.comparison,
        config=RankConfig(
            version="wp5",
            simulation_target_size=Decimal("10"),
            fee_slippage_cost_policy_id="fee_slippage",
            fee_slippage_cost_policy_version="v1",
            fee_rate=Decimal("0.01"),
            slippage_buffer=Decimal("0.005"),
        ),
        as_of=NOW + timedelta(seconds=10), run_id="wp5-ledger",
    )
    assert ranked.decision.execution == "NO_ORDER"
    assert ranked.prediction.decision_id == ranked.decision.record_id
    expected_evidence = sum(item.confidence for item in values[4].evidence) / Decimal(len(values[4].evidence))
    assert ranked.score_breakdown["evidence"] == expected_evidence * Decimal("0.20")

    overwritten_estimate = imported.result.probability_estimate.model_copy(update={
        "p_event_yes_low": Decimal("0.80"), "p_event_yes_mid": Decimal("0.85"),
        "p_event_yes_high": Decimal("0.90"), "p_market_yes_low": Decimal("0.80"),
        "p_market_yes_mid": Decimal("0.85"), "p_market_yes_high": Decimal("0.90"),
    })
    overwritten_result = imported.result.model_copy(update={
        "probability_estimate": overwritten_estimate,
        "extensions": {**imported.result.extensions,
            "blind_probability_interval": [Decimal("0.80"), Decimal("0.85"), Decimal("0.90")]},
    })
    overwritten_receipt = imported.receipt.model_copy(update={
        "accepted_result_sha256": overwritten_result.canonical_sha256,
    })
    with pytest.raises(DecisionLedgerError, match="cannot overwrite"):
        build_ranked_ledger(
            candidate=values[0].model_copy(update={"state": CandidateState.RULE_B_PASSED}),
            contract=values[1], gate_a=values[2], gate_b=gate_b,
            blind_result=values[4], blind_receipt=values[5], market_packet=packet,
            market_result=overwritten_result, market_receipt=overwritten_receipt,
            book=values[8], market_comparison=assessment.comparison,
            config=RankConfig(
                version="wp5",
                simulation_target_size=Decimal("10"),
                fee_slippage_cost_policy_id="fee_slippage",
                fee_slippage_cost_policy_version="v1",
                fee_rate=Decimal("0.01"),
                slippage_buffer=Decimal("0.005"),
            ),
            as_of=NOW + timedelta(seconds=10), run_id="wp5-overwrite-ledger",
        )


def test_ranker_rejects_cost_policy_drift_from_sealed_comparison() -> None:
    assessment, packet, values = _assessment()
    assert assessment.result is not None
    imported = import_research_result(
        packet=packet,
        submitted_bytes=canonical_json(assessment.result).encode(),
        source_contents={},
        imported_at=NOW + timedelta(seconds=8),
        run_id="wp5-cost-drift-import",
        submitted_artifact_locator="results/market-cost-drift.json",
    )
    assert imported.result is not None
    gate_b = evaluate_gate_b(
        values[1], values[2], market_packet_id=packet.record_id,
        market_packet_rule_hash=packet.rule_contract.rule_hash,
        market_packet_contract_revision_id=packet.rule_contract.contract_revision_id,
        run_id="wp5-cost-drift-gate-b", evaluated_at=NOW + timedelta(seconds=9),
    )
    with pytest.raises(DecisionLedgerError, match="cost policies differ"):
        build_ranked_ledger(
            candidate=values[0].model_copy(update={"state": CandidateState.RULE_B_PASSED}),
            contract=values[1], gate_a=values[2], gate_b=gate_b,
            blind_result=values[4], blind_receipt=values[5], market_packet=packet,
            market_result=imported.result, market_receipt=imported.receipt,
            book=values[8], market_comparison=assessment.comparison,
            config=RankConfig(
                version="wrong-fee", simulation_target_size=Decimal("10"),
                fee_slippage_cost_policy_id="fee_slippage",
                fee_slippage_cost_policy_version="v1", fee_rate=Decimal("0.04"),
                slippage_buffer=Decimal("0.005"),
            ),
            as_of=NOW + timedelta(seconds=10), run_id="wp5-cost-drift-ledger",
        )


def test_ranker_recomputes_fills_against_the_frozen_orderbook() -> None:
    values = list(_demand_and_book())
    values[8] = values[8].model_copy(update={"quality_flags": ()})
    values[7] = values[7].model_copy(
        update={"orderbook_snapshot_sha256": values[8].canonical_sha256}
    )
    assessment, packet, values = _assessment(values=tuple(values))
    assert assessment.result is not None
    imported = import_research_result(
        packet=packet,
        submitted_bytes=canonical_json(assessment.result).encode(),
        source_contents={},
        imported_at=NOW + timedelta(seconds=8),
        run_id="wp5-fill-binding-import",
        submitted_artifact_locator="results/market-fill-binding.json",
    )
    assert imported.result is not None
    gate_b = evaluate_gate_b(
        values[1],
        values[2],
        market_packet_id=packet.record_id,
        market_packet_rule_hash=packet.rule_contract.rule_hash,
        market_packet_contract_revision_id=packet.rule_contract.contract_revision_id,
        run_id="wp5-fill-binding-gate-b",
        evaluated_at=NOW + timedelta(seconds=9),
    )

    raw = assessment.comparison.model_dump(mode="python")
    forged_price = Decimal("0.51")
    target = raw["policy_size"]
    fee = target * raw["fee_rate"] * forged_price * (Decimal("1") - forged_price)
    all_in = forged_price + (fee / target) + raw["slippage_buffer"]
    raw.update(
        yes_ask=forged_price,
        yes_buy_fills=(BookLevel(price=forged_price, size=target),),
        yes_buy_vwap=forged_price,
        yes_taker_fee=fee,
        yes_all_in_buy_price=all_in,
        yes_edge_low=raw["blind_p_yes_low"] - all_in,
        yes_edge_mid=raw["blind_p_yes_mid"] - all_in,
        yes_edge_high=raw["blind_p_yes_high"] - all_in,
    )
    forged_comparison = _reseal_v2(raw)
    forged_result = imported.result.model_copy(
        update={
            "extensions": {
                **imported.result.extensions,
                "market_comparison_id": forged_comparison.record_id,
                "market_comparison_sha256": forged_comparison.comparison_sha256,
            }
        }
    )
    forged_receipt = imported.receipt.model_copy(
        update={"accepted_result_sha256": forged_result.canonical_sha256}
    )
    with pytest.raises(
        DecisionLedgerError,
        match="fills/costs do not match sealed orderbook",
    ):
        build_ranked_ledger(
            candidate=values[0].model_copy(
                update={"state": CandidateState.RULE_B_PASSED}
            ),
            contract=values[1],
            gate_a=values[2],
            gate_b=gate_b,
            blind_result=values[4],
            blind_receipt=values[5],
            market_packet=packet,
            market_result=forged_result,
            market_receipt=forged_receipt,
            book=values[8],
            market_comparison=forged_comparison,
            config=RankConfig(
                version="wp5",
                simulation_target_size=Decimal("10"),
                fee_slippage_cost_policy_id="fee_slippage",
                fee_slippage_cost_policy_version="v1",
                fee_rate=Decimal("0.01"),
                slippage_buffer=Decimal("0.005"),
            ),
            as_of=NOW + timedelta(seconds=10),
            run_id="wp5-fill-binding-ledger",
        )
