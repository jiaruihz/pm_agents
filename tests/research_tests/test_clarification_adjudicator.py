from __future__ import annotations

from src.strategies.rule_lawyer.clarification_adjudicator import (
    build_clarification_packet,
    clarification_card_blockers,
    clarification_court_prompt,
)


def test_packet_hash_is_stable_and_prompt_forbids_conditional_leak() -> None:
    kwargs = {
        "case_id": "market:1",
        "market_id": "1",
        "title": "Will X happen?",
        "outcomes": ["Yes", "No"],
        "proposal_outcome": "No",
        "binding_rules": "Resolves Yes if X happens.",
        "official_update": "X happened according to the designated source.",
        "update_timestamp": 100,
    }
    first = build_clarification_packet(**kwargs)
    second = build_clarification_packet(**kwargs)
    assert first["input_sha256"] == second["input_sha256"]
    prompt = clarification_court_prompt([first])
    assert "Merely restating a Yes condition is not evidence for Yes" in prompt
    assert "market:1" in prompt


def test_novel_source_metric_exclusion_blocks_trade() -> None:
    packet = build_clarification_packet(
        case_id="market:2",
        market_id="2",
        title="Total kills O/U 45.5?",
        outcomes=["Over", "Under"],
        proposal_outcome="Over",
        binding_rules="Resolves Over if total kills are 46 or more according to Dotabuff.",
        official_update="The resolution source's top-level figure includes Roshan kills, which should not be counted.",
        update_timestamp=100,
    )
    card = {
        "determination": "Outcome1",
        "confidence": 0.99,
        "proof_closed": True,
        "contract_correction_or_refund": False,
        "fundamental_intent_assessment": "consistent",
        "rule_quote": "total kills are 46 or more",
        "update_quote": "Roshan kills, which should not be counted",
    }
    assert clarification_card_blockers(packet, card) == [
        "novel_source_metric_exclusion_may_change_fundamental_intent"
    ]
