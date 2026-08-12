from __future__ import annotations

from src.strategies.rule_lawyer.contract_corpus import (
    build_contract_corpus,
    build_dispute_thesis,
    bulletin_adapter_from_ancillary,
    extract_ancillary_description,
)


ADAPTER = "0x65070BE91477460D8A7AeEb94ef92fe056C2f2A7"


def ancillary(description: str) -> str:
    return (
        f"q: title: Will the robot dance?, description: {description} market_id: 123 "
        "res_data: p1: 0, p2: 1. Updates made by the question creator via the "
        f"bulletin board at {ADAPTER} as described by https://polygonscan.com/tx/0xabc "
        "should be considered.,initializer:0xcreator"
    )


def test_extracts_binding_description_and_bulletin_adapter() -> None:
    text = ancillary("A performance using rhythmic body movement will count.")
    assert extract_ancillary_description(text) == "A performance using rhythmic body movement will count."
    assert bulletin_adapter_from_ancillary(text) == ADAPTER.lower()


def test_creator_bulletin_update_is_pit_and_requires_fundamental_intent_review() -> None:
    description = "This market resolves Yes if the robot dances."
    text = ancillary(description)
    corpus = build_contract_corpus(
        ancillary_text=text,
        market={"description": description, "updatedAt": "2026-01-01T00:00:00Z"},
        observed_at_utc="2026-01-02T00:00:00+00:00",
        observed_at_ts=200,
        bulletin={
            "adapter": ADAPTER.lower(),
            "creator": "0x0000000000000000000000000000000000000001",
            "ancillary_text": text,
            "updates": [
                {
                    "timestamp": 150,
                    "text": "Arm-only rhythmic performance may qualify as dancing.",
                    "publisher": "0x0000000000000000000000000000000000000001",
                },
                {
                    "timestamp": 250,
                    "text": "This future update must not enter the 200 snapshot.",
                    "publisher": "0x0000000000000000000000000000000000000001",
                },
            ],
        },
    )
    binding = corpus["binding_map"]
    assert binding["official_update_count_as_of_snapshot"] == 1
    assert binding["future_update_count_excluded"] == 1
    assert not binding["deterministic_verdict_allowed"]
    assert "official_clarification_requires_fundamental_intent_review" in binding["adjudication_blockers"]
    clarification = [
        row for row in corpus["fragments"] if row["legal_role"] == "onchain_clarification"
    ]
    assert [row["text"] for row in clarification] == [
        "Arm-only rhythmic performance may qualify as dancing."
    ]


def test_gamma_additional_context_without_onchain_update_fails_closed() -> None:
    text = ancillary("Original sell definition.")
    corpus = build_contract_corpus(
        ancillary_text=text,
        market={"description": "Additional context: transfers to custodians do not count as sales."},
        observed_at_utc="2026-01-02T00:00:00+00:00",
        observed_at_ts=200,
        bulletin={
            "adapter": ADAPTER.lower(),
            "creator": "0x0000000000000000000000000000000000000001",
            "ancillary_text": text,
            "updates": [],
        },
    )
    assert "gamma_additional_context_not_verified_onchain" in corpus["binding_map"]["adjudication_blockers"]


def test_operational_notice_is_excluded_from_adjudication() -> None:
    description = "This market resolves Yes if the robot dances."
    text = ancillary(description)
    corpus = build_contract_corpus(
        ancillary_text=text,
        market={"description": description},
        observed_at_utc="2026-01-02T00:00:00+00:00",
        observed_at_ts=200,
        bulletin={
            "adapter": ADAPTER.lower(),
            "creator": "0x0000000000000000000000000000000000000001",
            "ancillary_text": text,
            "updates": [
                {
                    "timestamp": 150,
                    "text": "We're aware of the dispute. If a clarification is to be issued, it will be at 3 PM ET. The order book will be cleared.",
                }
            ],
        },
    )
    binding = corpus["binding_map"]
    assert binding["official_operational_notice_count_as_of_snapshot"] == 1
    assert binding["official_adjudication_guidance_count_as_of_snapshot"] == 0
    assert not binding["adjudication_blockers"]
    notice = next(
        row for row in corpus["fragments"] if row["legal_role"] == "platform_operational_notice"
    )
    assert notice["adjudication_use"] == "excluded"


def test_contract_correction_requires_precedence_review() -> None:
    description = "Original rule."
    text = ancillary(description)
    corpus = build_contract_corpus(
        ancillary_text=text,
        market={"description": description},
        observed_at_utc="2026-01-02T00:00:00+00:00",
        observed_at_ts=200,
        bulletin={
            "adapter": ADAPTER.lower(),
            "creator": "0x0000000000000000000000000000000000000001",
            "ancillary_text": text,
            "updates": [{"timestamp": 150, "text": "This market's rules have been updated. All prior trades will be refunded."}],
        },
    )
    assert "official_contract_correction_or_refund_requires_precedence_review" in corpus[
        "binding_map"
    ]["adjudication_blockers"]


def test_dispute_thesis_does_not_assume_challenger_is_correct() -> None:
    snapshot = {
        "issue_features": {"track": "semantic", "mechanism": "general_semantic_boundary"},
        "proposal": {"proposed_binary": 1},
        "outcomes": ["Yes", "No"],
        "contract_corpus": {"contract_corpus_sha256": "abc", "binding_map": {}},
        "rule_verdict": {"status": "verified", "winning_outcome": "Yes"},
    }
    thesis = build_dispute_thesis(snapshot)
    assert thesis["track"] == "interpretive"
    assert thesis["winning_relation_to_proposal"] == "proposal"
    assert thesis["material_adjudication_divergence"] is False


def test_dispute_thesis_maps_uma_zero_to_gamma_no() -> None:
    snapshot = {
        "issue_features": {"track": "semantic", "mechanism": "general_semantic_boundary"},
        "proposal": {"proposed_binary": 0},
        "outcomes": ["Yes", "No"],
        "contract_corpus": {"contract_corpus_sha256": "abc", "binding_map": {}},
        "rule_verdict": {"status": "verified", "winning_outcome": "No"},
    }
    thesis = build_dispute_thesis(snapshot)
    assert thesis["proposed_outcome"] == "No"
    assert thesis["winning_relation_to_proposal"] == "proposal"
    assert thesis["material_adjudication_divergence"] is False
