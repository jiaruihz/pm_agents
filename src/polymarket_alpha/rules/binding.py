"""Exact, deterministic binding of WP2 model proposals to frozen rule text."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from typing import Mapping, Sequence

from ..contracts import (
    RuleInterpretationPatch, RuleParseFieldProposal, RuleReviewAction,
    RuleReviewApproval, StructuredRuleParseProposal, bytes_sha256, content_sha256,
    stable_record_id,
)
from ..contracts.base import ensure_utc
from .models import RuleSourceEvidence


class RuleBindingError(ValueError):
    """A proposal cannot be bound safely to canonical frozen rule source."""


@dataclass(frozen=True)
class CanonicalRuleSegment:
    source_segment_id: str
    candidate_snapshot_id: str
    candidate_snapshot_sha256: str
    source_artifact_id: str
    source_artifact_sha256: str
    source_bytes: bytes
    text: str

    def __post_init__(self) -> None:
        if not self.source_segment_id.strip() or not self.source_artifact_id.strip() or not self.text:
            raise RuleBindingError("canonical segment fields must not be blank")
        if not self.candidate_snapshot_id.strip():
            raise RuleBindingError("canonical segment must bind a Candidate snapshot")
        if self.source_bytes != self.text.encode("utf-8"):
            raise RuleBindingError("canonical segment bytes must exactly encode its text")
        for digest in (self.candidate_snapshot_sha256, self.source_artifact_sha256):
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise RuleBindingError("canonical segment hashes must be lowercase SHA-256")


@dataclass(frozen=True)
class BoundRuleProposal:
    field_name: str
    proposed_value: str
    source_segment_id: str
    exact_quote: str
    quote_start: int
    quote_end: int
    source_artifact_id: str
    source_artifact_sha256: str
    source_content_sha256: str


@dataclass(frozen=True)
class RuleBindingReceipt:
    receipt_id: str
    proposal_id: str
    proposal_sha256: str
    candidate_snapshot_id: str
    candidate_snapshot_sha256: str
    rule_source_artifact_id: str
    rule_source_sha256: str
    bound: tuple[BoundRuleProposal, ...]


def bind_rule_parse_proposal(*, proposal: StructuredRuleParseProposal,
    segments: Mapping[str, CanonicalRuleSegment], required_fields: Sequence[str]) -> RuleBindingReceipt:
    """Locate every quoted proposal exactly once in its declared source segment.

    A model cannot provide source offsets or hashes.  This function recomputes
    both, rejects partial required coverage, and is intentionally separate from
    the sole RuleContractCompiler.
    """
    required = tuple(sorted(set(required_fields)))
    proposal_fields = {item.field_name: item for item in proposal.proposals}
    missing = set(required) - set(proposal_fields)
    if missing:
        raise RuleBindingError(f"partial required-field coverage: {sorted(missing)}")
    extra = set(proposal_fields) - set(required)
    if extra:
        raise RuleBindingError(f"unexpected proposal fields: {sorted(extra)}")
    bound: list[BoundRuleProposal] = []
    for field_name in required:
        item = proposal_fields[field_name]
        if item.unresolved:
            raise RuleBindingError(f"required field is unresolved: {field_name}")
        segment = segments.get(item.source_segment_id)
        if segment is None:
            raise RuleBindingError("proposal references unknown source segment")
        if (
            segment.candidate_snapshot_id != proposal.candidate_snapshot_id
            or segment.candidate_snapshot_sha256 != proposal.candidate_snapshot_sha256
            or segment.source_artifact_id != proposal.rule_source_artifact_id
            or segment.source_artifact_sha256 != proposal.rule_source_sha256
        ):
            raise RuleBindingError("proposal and source segment do not share one frozen Candidate context")
        positions: list[int] = []
        start = 0
        while True:
            index = segment.text.find(item.exact_quote, start)
            if index < 0:
                break
            positions.append(index)
            start = index + 1
        if len(positions) != 1:
            if not positions:
                raise RuleBindingError("fabricated quote is absent from frozen segment")
            raise RuleBindingError("ambiguous duplicate quote requires a unique exact segment")
        offset = positions[0]
        bound.append(BoundRuleProposal(field_name=field_name, proposed_value=item.proposed_value,
            source_segment_id=segment.source_segment_id, exact_quote=item.exact_quote,
            quote_start=offset, quote_end=offset + len(item.exact_quote),
            source_artifact_id=segment.source_artifact_id,
            source_artifact_sha256=segment.source_artifact_sha256,
            source_content_sha256=bytes_sha256(segment.source_bytes)))
    if len({item.field_name for item in bound}) != len(bound):
        raise RuleBindingError("duplicate field binding")
    receipt_id = stable_record_id("rule_binding_receipt", proposal.record_id, proposal.canonical_sha256, tuple(
        {"field_name": item.field_name, "proposed_value": item.proposed_value,
         "source_segment_id": item.source_segment_id, "exact_quote": item.exact_quote,
         "quote_start": item.quote_start, "quote_end": item.quote_end,
         "source_artifact_id": item.source_artifact_id,
         "source_artifact_sha256": item.source_artifact_sha256,
         "source_content_sha256": item.source_content_sha256} for item in bound
    ))
    return RuleBindingReceipt(receipt_id=receipt_id, proposal_id=proposal.record_id,
        proposal_sha256=proposal.canonical_sha256,
        candidate_snapshot_id=proposal.candidate_snapshot_id,
        candidate_snapshot_sha256=proposal.candidate_snapshot_sha256,
        rule_source_artifact_id=proposal.rule_source_artifact_id,
        rule_source_sha256=proposal.rule_source_sha256, bound=tuple(bound))


def binding_source_evidence(*, receipt: RuleBindingReceipt,
    segments: Mapping[str, CanonicalRuleSegment], observed_at: datetime) -> tuple[RuleSourceEvidence, ...]:
    """Adapt verified bindings to the existing compiler's only source format."""
    observed_at = ensure_utc(observed_at)
    evidence: list[RuleSourceEvidence] = []
    for item in receipt.bound:
        segment = segments.get(item.source_segment_id)
        if segment is None or (
            segment.candidate_snapshot_id != receipt.candidate_snapshot_id
            or segment.candidate_snapshot_sha256 != receipt.candidate_snapshot_sha256
            or segment.source_artifact_id != receipt.rule_source_artifact_id
            or segment.source_artifact_sha256 != receipt.rule_source_sha256
            or bytes_sha256(segment.source_bytes) != item.source_content_sha256
            or segment.text[item.quote_start:item.quote_end] != item.exact_quote
        ):
            raise RuleBindingError("binding receipt no longer matches the frozen source segment")
        evidence.append(RuleSourceEvidence(field_names=(item.field_name,),
            source_artifact_id=segment.source_artifact_id,
            source_content_sha256=item.source_content_sha256,
            artifact_text=segment.text, quote_start=item.quote_start,
            quote_end=item.quote_end, legal_role="canonical_rule_source", adjudication_use="binding",
            observed_at=observed_at))
    return tuple(evidence)


def apply_rule_review(*, draft_id: str, draft: Mapping[str, str], draft_hash: str,
    action: RuleReviewAction, reviewer_id: str, reason_codes: Sequence[str],
    reviewed_at: datetime, patches: Sequence[RuleInterpretationPatch] = (),
    canonical_segments: Mapping[str, CanonicalRuleSegment] | None = None,
    review_note: str = "") -> tuple[dict[str, str], RuleReviewApproval]:
    """Apply source-span-bound human edits; stale edits fail closed before reseal."""
    reviewed_at = ensure_utc(reviewed_at)
    if not draft_id.strip() or not reviewer_id.strip() or not reason_codes:
        raise RuleBindingError("draft, reviewer and reasons are required")
    if content_sha256(dict(draft)) != draft_hash:
        raise RuleBindingError("stale draft hash")
    if action == RuleReviewAction.PATCH:
        if not patches:
            raise RuleBindingError("PATCH requires source-bound edits")
        if not canonical_segments:
            raise RuleBindingError("PATCH requires frozen canonical source segments")
        if len({patch.field_path for patch in patches}) != len(patches):
            raise RuleBindingError("a field may be patched only once")
        result = dict(draft)
        for patch in patches:
            field = patch.field_path
            if field not in result or bytes_sha256(result[field].encode("utf-8")) != patch.old_value_hash:
                raise RuleBindingError("stale patch or unknown field")
            segment = canonical_segments.get(patch.source_segment_id)
            if segment is None:
                raise RuleBindingError("human patch references unknown source segment")
            positions: list[int] = []
            start = 0
            while True:
                index = segment.text.find(patch.exact_quote, start)
                if index < 0:
                    break
                positions.append(index)
                start = index + 1
            if len(positions) != 1:
                raise RuleBindingError("human patch quote must occur exactly once in its source segment")
            position = positions[0]
            if (
                patch.source_artifact_id != segment.source_artifact_id
                or patch.source_content_sha256 != bytes_sha256(segment.source_bytes)
                or patch.quote_start != position
                or patch.quote_end != position + len(patch.exact_quote)
            ):
                raise RuleBindingError("human patch source hash or offsets are not canonical")
            result[field] = patch.new_value
        after_hash = content_sha256(result)
        after_id = stable_record_id("rule_contract_draft", draft_id, after_hash)
    else:
        if patches:
            raise RuleBindingError("only PATCH may carry edits")
        result, after_hash, after_id = dict(draft), None, None
    normalized_reasons = tuple(sorted(set(reason_codes)))
    receipt_id = stable_record_id("rule_review_approval", draft_id, draft_hash, action,
        normalized_reasons, tuple(patches), after_id, after_hash)
    approval = RuleReviewApproval(record_id=receipt_id, review_receipt_id=receipt_id, run_id="gate_r_wp2",
        created_at=reviewed_at, source="human_rule_review", source_version="v1",
        rule_contract_draft_id=draft_id, before_hash=draft_hash, before_draft=dict(draft),
        reviewer_id=reviewer_id, reviewed_at=reviewed_at, action=action,
        reason_codes=normalized_reasons, review_note=review_note, patches=tuple(patches),
        after_draft_id=after_id, after_hash=after_hash,
        after_draft=dict(result) if action == RuleReviewAction.PATCH else None)
    return result, approval
