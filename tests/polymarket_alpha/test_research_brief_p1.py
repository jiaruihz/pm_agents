"""Provider-neutral manual research brief tests."""

from __future__ import annotations

from datetime import timedelta

import pytest

from src.polymarket_alpha.contracts import PacketStage, bytes_sha256, canonical_json
from src.polymarket_alpha.research.brief import ResearchBriefError, build_research_brief
from src.polymarket_alpha.security import audit_source_tree
from tests.polymarket_alpha.test_market_research_p0_08c import _accepted_blind_result, _freeze
from tests.polymarket_alpha.test_research_importer_p0_08b import NOW, _packet


def test_blind_brief_is_deterministic_and_contains_no_private_market_values() -> None:
    packet = _packet()
    first = build_research_brief(packet, created_at=NOW + timedelta(minutes=1))
    second = build_research_brief(packet, created_at=NOW + timedelta(minutes=1))
    assert first == second
    assert first.brief_sha256 == second.brief_sha256
    assert first.packet_stage == PacketStage.BLIND
    assert first.packet_id == packet.record_id
    assert first.packet_sha256 == packet.canonical_sha256
    packet_bytes = canonical_json(packet.model_dump(mode="json", exclude_none=False)).encode()
    assert first.packet_bytes_sha256 == bytes_sha256(packet_bytes)
    prompt = first.prompt_text().lower()
    assert "polymarket" not in prompt
    assert '"market_id":null' in prompt
    assert '"market_probability_fields":"must_be_null"' in prompt
    for forbidden in ("condition-1", "yes-token", "no-token", "market-1", "wallet-1"):
        assert forbidden not in prompt
    detached = first.packet_payload
    detached["projection"]["subject_entity"] = "Polymarket market yes"
    assert "polymarket" not in first.prompt_text().lower()
    assert first == second


def test_market_brief_binds_market_and_final_estimate_requirements() -> None:
    packet = _freeze()
    accepted = _accepted_blind_result()[4]
    brief = build_research_brief(
        packet,
        created_at=packet.created_at,
        accepted_blind_result=accepted,
    )
    assert brief.packet_stage == PacketStage.MARKET_AWARE
    assert brief.result_requirements["probability_estimate"]["market_id"] == packet.market_id
    assert brief.result_requirements["probability_estimate"]["estimate_stage"] == "FINAL"
    assert (
        brief.result_requirements["probability_estimate"]["blind_candidate_id"]
        == accepted.probability_estimate.blind_candidate_id
    )
    assert brief.accepted_blind_result_payload is not None
    assert packet.market_id in brief.prompt_text()


def test_market_brief_requires_exact_hash_bound_blind_result() -> None:
    packet = _freeze()
    accepted = _accepted_blind_result()[4]
    with pytest.raises(ResearchBriefError, match="requires"):
        build_research_brief(packet, created_at=packet.created_at)
    forged = accepted.model_copy(update={"producer": "forged"})
    with pytest.raises(ResearchBriefError, match="bind"):
        build_research_brief(
            packet,
            created_at=packet.created_at,
            accepted_blind_result=forged,
        )


def test_model_copy_and_clock_tampering_fail_closed() -> None:
    packet = _packet()
    tampered = packet.model_copy(update={"source": "forged"})
    with pytest.raises(ResearchBriefError, match="invalid"):
        build_research_brief(tampered, created_at=NOW + timedelta(minutes=1))
    with pytest.raises(ResearchBriefError, match="precede"):
        build_research_brief(packet, created_at=packet.created_at - timedelta(seconds=1))


def test_brief_module_has_no_network_storage_or_process_capability() -> None:
    assert audit_source_tree("src/polymarket_alpha/research/brief.py").violations == ()
