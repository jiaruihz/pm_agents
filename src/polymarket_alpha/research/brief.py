"""Deterministic, provider-neutral briefs for the manual research seam.

The Alpha pipeline deliberately does not own a model client or browser.  This
module turns an already sealed Blind or Market packet into copyable canonical
bytes while preserving the stage boundary.  A separate, explicitly authorized
human or agent performs the research and returns a structured result to the
existing importer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any

from ..contracts import (
    BlindResearchPacket,
    MarketResearchPacket,
    PacketStage,
    ResearchResultEnvelope,
    bytes_sha256,
    canonical_json,
)
from ..contracts.base import ensure_utc


RESEARCH_BRIEF_VERSION = "alpha_research_brief_v1"
ResearchPacketValue = BlindResearchPacket | MarketResearchPacket


class ResearchBriefError(ValueError):
    """Raised when a packet cannot be safely rendered for external research."""


@dataclass(frozen=True, slots=True)
class ResearchBrief:
    brief_version: str
    packet_stage: PacketStage
    packet_id: str
    packet_sha256: str
    packet_bytes_sha256: str
    created_at: datetime
    instructions: tuple[str, ...]
    packet_payload_json: str
    accepted_blind_result_payload_json: str | None
    result_requirements_json: str

    @property
    def packet_payload(self) -> dict[str, Any]:
        """Return a detached copy; mutating it cannot change this brief."""

        return json.loads(self.packet_payload_json)

    @property
    def accepted_blind_result_payload(self) -> dict[str, Any] | None:
        if self.accepted_blind_result_payload_json is None:
            return None
        return json.loads(self.accepted_blind_result_payload_json)

    @property
    def result_requirements(self) -> dict[str, Any]:
        return json.loads(self.result_requirements_json)

    def canonical_bytes(self) -> bytes:
        return canonical_json(
            {
                "brief_version": self.brief_version,
                "packet_stage": self.packet_stage,
                "packet_id": self.packet_id,
                "packet_sha256": self.packet_sha256,
                "packet_bytes_sha256": self.packet_bytes_sha256,
                "created_at": self.created_at,
                "instructions": self.instructions,
                "packet_payload": self.packet_payload,
                "accepted_blind_result_payload": self.accepted_blind_result_payload,
                "result_requirements": self.result_requirements,
            }
        ).encode("utf-8")

    @property
    def brief_sha256(self) -> str:
        return bytes_sha256(self.canonical_bytes())

    def prompt_text(self) -> str:
        """Return one stable, human-copyable prompt with no hidden context."""

        lines = [
            (
                "EVENT RESEARCH BRIEF"
                if self.packet_stage == PacketStage.BLIND
                else "MARKET-AWARE EVENT RESEARCH BRIEF"
            ),
            f"brief_version={self.brief_version}",
            f"packet_stage={self.packet_stage.value}",
            f"packet_id={self.packet_id}",
            f"packet_sha256={self.packet_sha256}",
            "",
            "INSTRUCTIONS",
            *(f"- {item}" for item in self.instructions),
            "",
            "RETURN CONTRACT",
            self.result_requirements_json,
            "",
            "SEALED INPUT PACKET",
            self.packet_payload_json,
        ]
        if self.accepted_blind_result_payload_json is not None:
            lines.extend(
                (
                    "",
                    "SEALED ACCEPTED BLIND RESULT",
                    self.accepted_blind_result_payload_json,
                )
            )
        return "\n".join(lines) + "\n"


_COMMON_INSTRUCTIONS = (
    "Treat the sealed input packet as immutable and do not infer omitted fields.",
    "Use claim-level evidence; every claim must bind one captured source artifact.",
    "Record published, accessed, and effective-as-of clocks when available.",
    "Distinguish quotation/excerpt evidence from paraphrase and state uncertainty.",
    "Return exactly one JSON object; do not wrap it in Markdown fences.",
    "Do not recommend, create, sign, or submit any order or transaction.",
)

_BLIND_INSTRUCTIONS = (
    "Estimate the event from rule semantics and non-market public evidence only.",
    "Do not use venue pages, prices, order books, trading positions, or wallet activity.",
    "Do not search any trading venue or a mirror of one for this event.",
    "Set market_id and every p_market_yes field to null.",
    "The estimate_stage must be BLIND and must bind the supplied blind_candidate_id.",
)

_MARKET_INSTRUCTIONS = (
    "Reassess the event after considering the supplied frozen paired order book.",
    "Preserve the accepted Blind evidence lineage and explicitly identify red-team adjustments.",
    "The estimate_stage must be FINAL and market_id must equal the packet market_id.",
    "Report both event-probability and market-baseline intervals without execution advice.",
)


def _frozen_packet(packet: ResearchPacketValue) -> ResearchPacketValue:
    if not isinstance(packet, (BlindResearchPacket, MarketResearchPacket)):
        raise ResearchBriefError("expected a released Blind or Market research packet")
    cls = type(packet)
    try:
        rebuilt = cls.model_validate(packet.model_dump(mode="python"))
    except (TypeError, ValueError) as error:
        raise ResearchBriefError(f"research packet is invalid: {error}") from error
    if rebuilt.canonical_sha256 != packet.canonical_sha256:
        raise ResearchBriefError("research packet canonical replay mismatch")
    return rebuilt


def _result_requirements(
    packet: ResearchPacketValue,
    accepted_blind_result: ResearchResultEnvelope | None,
) -> dict[str, Any]:
    blind = isinstance(packet, BlindResearchPacket)
    blind_candidate_id = (
        packet.projection.blind_candidate_id
        if blind
        else accepted_blind_result.probability_estimate.blind_candidate_id
    )
    return {
        "type": "ResearchResultEnvelope",
        "packet_stage": PacketStage.BLIND.value if blind else PacketStage.MARKET_AWARE.value,
        "packet_id": packet.record_id,
        "packet_sha256": packet.canonical_sha256,
        "probability_estimate": {
            "estimate_stage": "BLIND" if blind else "FINAL",
            "blind_candidate_id": blind_candidate_id,
            "market_id": None if blind else packet.market_id,
            "required_probability_fields": (
                "p_event_yes_low",
                "p_event_yes_mid",
                "p_event_yes_high",
            ),
            "market_probability_fields": "MUST_BE_NULL" if blind else "REQUIRED",
            "required_explanation_fields": ("uncertainty_drivers", "assumptions"),
        },
        "evidence_item_required_fields": (
            "claim",
            "supports_yes_or_no",
            "source_tier",
            "source_url_or_source_id",
            "published_at",
            "accessed_at",
            "effective_as_of",
            "primary_or_secondary",
            "quotation_or_paraphrase_location",
            "confidence",
            "source_artifact_id",
            "capture_scope",
            "hash_scope",
            "content_sha256",
            "replayability",
        ),
        "source_artifact_policy": {
            "actual_content_required_for_hash": True,
            "importer_recomputes_hash": True,
            "reference_only_is_not_full_replay": True,
            "excerpt_context_required_for_excerpt_only": True,
        },
        "ordering": {
            "evidence": "sort by evidence_id",
            "source_artifacts": "sort by artifact_id",
        },
    }


def build_research_brief(
    packet: ResearchPacketValue,
    *,
    created_at: datetime,
    accepted_blind_result: ResearchResultEnvelope | None = None,
) -> ResearchBrief:
    """Render one packet without adding private recall or execution context."""

    frozen = _frozen_packet(packet)
    created_at = ensure_utc(created_at)
    if created_at < frozen.created_at:
        raise ResearchBriefError("research brief cannot precede packet creation")
    blind_result_payload = None
    frozen_blind_result = None
    if isinstance(frozen, BlindResearchPacket):
        if accepted_blind_result is not None:
            raise ResearchBriefError("Blind brief cannot receive prior market-aware context")
    else:
        if not isinstance(accepted_blind_result, ResearchResultEnvelope):
            raise ResearchBriefError("Market brief requires the accepted Blind result")
        try:
            frozen_blind_result = ResearchResultEnvelope.model_validate(
                accepted_blind_result.model_dump(mode="python")
            )
        except (TypeError, ValueError) as error:
            raise ResearchBriefError(f"accepted Blind result is invalid: {error}") from error
        if (
            frozen_blind_result.canonical_sha256
            != accepted_blind_result.canonical_sha256
        ):
            raise ResearchBriefError("accepted Blind result canonical replay mismatch")
        references = tuple(
            item
            for item in frozen.provenance
            if item.relation == "accepted_blind_result"
        )
        if (
            frozen_blind_result.packet_stage != PacketStage.BLIND
            or frozen_blind_result.record_id != frozen.blind_result_id
            or frozen_blind_result.packet_id != frozen.blind_packet_id
            or len(references) != 1
            or references[0].source_artifact_id != frozen_blind_result.record_id
            or references[0].content_sha256 != frozen_blind_result.canonical_sha256
            or frozen.blind_evidence != frozen_blind_result.evidence
            or frozen_blind_result.completed_at > frozen.created_at
        ):
            raise ResearchBriefError("accepted Blind result does not bind the Market packet")
        blind_result_payload = frozen_blind_result.model_dump(
            mode="json", exclude_none=False
        )
    packet_payload = frozen.model_dump(mode="json", exclude_none=False)
    packet_bytes = canonical_json(packet_payload).encode("utf-8")
    instructions = _COMMON_INSTRUCTIONS + (
        _BLIND_INSTRUCTIONS
        if isinstance(frozen, BlindResearchPacket)
        else _MARKET_INSTRUCTIONS
    )
    return ResearchBrief(
        brief_version=RESEARCH_BRIEF_VERSION,
        packet_stage=frozen.packet_stage,
        packet_id=frozen.record_id,
        packet_sha256=frozen.canonical_sha256,
        packet_bytes_sha256=bytes_sha256(packet_bytes),
        created_at=created_at,
        instructions=instructions,
        packet_payload_json=canonical_json(packet_payload),
        accepted_blind_result_payload_json=(
            None if blind_result_payload is None else canonical_json(blind_result_payload)
        ),
        result_requirements_json=canonical_json(
            _result_requirements(frozen, frozen_blind_result)
        ),
    )


__all__ = [
    "RESEARCH_BRIEF_VERSION",
    "ResearchBrief",
    "ResearchBriefError",
    "build_research_brief",
]
