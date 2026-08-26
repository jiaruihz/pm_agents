"""Deterministic, offline RuleContract compiler for P0-07."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import hashlib
import json
from typing import Any, Mapping, Sequence

from src.polymarket_alpha.contracts import (
    ProvenanceRef,
    RuleContract,
    RuleGate,
    ThresholdSpec,
    rule_sha256,
    stable_record_id,
)

from .models import (
    CompilationStatus,
    CorpusSourceIdentity,
    ParseStatus,
    RuleCompilationOutcome,
    RuleCompilationReceipt,
    RuleCompilationRequest,
    RuleCorpusSnapshot,
    RuleSourceEvidence,
    StructuredRuleParse,
)


CORE_EVIDENCE_FIELDS = frozenset(
    {
        "subject_entity",
        "entity_match_rule",
        "yes_trigger",
        "resolution_sources",
        "source_precedence",
    }
)


def _parse_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value or "").strip().replace("Z", "+00:00")
        if not text:
            raise ValueError("timestamp is required")
        result = datetime.fromisoformat(text)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return result


def structured_parse_from_legacy(
    payload: Mapping[str, Any],
    *,
    resolution_sources: Sequence[str],
    source_precedence: Sequence[str],
    threshold: ThresholdSpec | None = None,
    initial_or_final: str = "UNSPECIFIED",
) -> StructuredRuleParse:
    """Adapt the serialized legacy RuleParse without importing its LLM backend."""

    entity_definitions = list(payload.get("entity_definitions") or [])
    if not entity_definitions:
        raise ValueError("legacy parse has no entity definition")
    first_entity = entity_definitions[0]
    conditions = tuple(str(item).strip() for item in payload.get("trigger_minimum_conditions") or ())
    if not conditions or any(not item for item in conditions):
        raise ValueError("legacy parse has no explicit YES trigger conditions")
    time_window = payload.get("time_window") or {}
    deadline = _parse_utc(time_window["end_at_utc"]) if time_window.get("end_at_utc") else None
    timezone = str(time_window.get("timezone_source") or "").strip()
    if not timezone:
        raise ValueError("legacy parse has no explicit timezone source")
    ambiguities = [str(item).strip() for item in payload.get("ambiguity_flags") or ()]
    ambiguities.extend(str(item).strip() for item in payload.get("ambiguity_explanations") or ())
    if len(entity_definitions) > 1:
        ambiguities.append("multiple_entity_definitions_require_review")
    return StructuredRuleParse(
        subject_entity=str(first_entity.get("entity") or ""),
        entity_match_rule=str(first_entity.get("definition") or ""),
        yes_trigger="; ".join(conditions),
        threshold=threshold,
        deadline=deadline,
        timezone=timezone,
        resolution_sources=tuple(resolution_sources),
        source_precedence=tuple(source_precedence),
        initial_or_final=initial_or_final,
        qualifying_examples=tuple(str(item) for item in payload.get("yes_case_examples") or ()),
        non_qualifying_examples=tuple(str(item) for item in payload.get("no_case_examples") or ()),
        ambiguities=tuple(dict.fromkeys(item for item in ambiguities if item)),
        clarity_score=Decimal(str(payload.get("clarity_score"))),
    )


def _legacy_corpus_basis(payload: Mapping[str, Any]) -> dict[str, Any]:
    fragments = list(payload.get("fragments") or ())
    binding_map = payload.get("binding_map") or {}
    return {
        "fragments": [
            {
                key: fragment.get(key)
                for key in (
                    "origin",
                    "legal_role",
                    "content_sha256",
                    "effective_at_utc",
                    "publisher",
                    "adjudication_use",
                )
            }
            for fragment in fragments
        ],
        "binding_map": {
            key: binding_map.get(key)
            for key in (
                "bulletin_enabled_by_request",
                "bulletin_adapter",
                "bulletin_fetch_error",
                "adapter_question_creator",
                "adapter_ancillary_matches_subgraph",
                "gamma_description_matches_ancillary",
                "official_update_count_as_of_snapshot",
                "future_update_count_excluded",
                "adjudication_blockers",
                "deterministic_verdict_allowed",
                "interpretive_review_required",
            )
        },
    }


def corpus_from_legacy(
    payload: Mapping[str, Any],
    *,
    source_identity: CorpusSourceIdentity,
    source_artifact_id: str,
) -> RuleCorpusSnapshot:
    """Verify and adapt the existing dispute contract corpus snapshot."""

    expected = str(payload.get("contract_corpus_sha256") or "")
    actual = hashlib.sha256(
        json.dumps(_legacy_corpus_basis(payload), sort_keys=True).encode("utf-8")
    ).hexdigest()
    if expected != actual:
        raise ValueError("legacy contract corpus aggregate hash mismatch")
    fragments: list[RuleSourceEvidence] = []
    for fragment in payload.get("fragments") or ():
        text = str(fragment.get("text") or "")
        observed_at = _parse_utc(fragment.get("observed_at_utc"))
        fragments.append(
            RuleSourceEvidence(
                field_names=("contract_corpus",),
                source_artifact_id=stable_record_id(
                    "source_artifact",
                    fragment.get("fragment_id"),
                    fragment.get("origin"),
                    fragment.get("content_sha256"),
                ),
                source_content_sha256=str(fragment["content_sha256"]),
                artifact_text=text,
                quote_start=0,
                quote_end=len(text),
                legal_role=str(fragment.get("legal_role") or "unknown"),
                adjudication_use=str(fragment.get("adjudication_use") or "review_required"),
                observed_at=observed_at,
            )
        )
    binding_map = payload.get("binding_map") or {}
    return RuleCorpusSnapshot(
        contract_corpus_sha256=expected,
        as_of=_parse_utc(payload.get("as_of_utc")),
        source_identity=source_identity,
        source_artifact_id=source_artifact_id,
        fragments=tuple(fragments),
        adjudication_blockers=tuple(binding_map.get("adjudication_blockers") or ()),
        deterministic_verdict_allowed=bool(binding_map.get("deterministic_verdict_allowed")),
    )


class RuleContractCompiler:
    """Compile frozen structured parses into the sole P0 RuleContract shape."""

    def __init__(
        self,
        *,
        version: str = "rule-contract-compiler-v1",
        pass_clarity_min: Decimal = Decimal("0.80"),
        watch_clarity_min: Decimal = Decimal("0.50"),
    ) -> None:
        if not version.strip():
            raise ValueError("compiler version must not be blank")
        if not Decimal("0") <= watch_clarity_min <= pass_clarity_min <= Decimal("1"):
            raise ValueError("compiler clarity thresholds must be ordered in [0,1]")
        self.version = version
        self.pass_clarity_min = pass_clarity_min
        self.watch_clarity_min = watch_clarity_min

    def compile(self, request: RuleCompilationRequest) -> RuleCompilationOutcome:
        reasons: list[str] = []
        actual_rule_hash = rule_sha256(request.raw_rule_text)
        if actual_rule_hash != request.expected_rule_hash:
            reasons.append("RULE_HASH_MISMATCH")
        if request.parse_status != ParseStatus.PARSED or request.parsed is None:
            reasons.append(f"PARSER_{request.parse_status.value}")
        if reasons:
            return self._blocked(request, reasons)

        parsed = request.parsed
        assert parsed is not None
        required_fields = set(CORE_EVIDENCE_FIELDS)
        if parsed.deadline is not None:
            required_fields.add("deadline")
        if parsed.threshold is not None:
            required_fields.add("threshold")
        covered_fields = {
            field_name
            for evidence in request.source_evidence
            for field_name in evidence.field_names
            if evidence.adjudication_use != "excluded"
        }
        missing_fields = sorted(required_fields - covered_fields)
        if missing_fields:
            reasons.extend(f"MISSING_SOURCE_EVIDENCE:{item}" for item in missing_fields)
        excluded_fields = sorted(
            {
                field_name
                for evidence in request.source_evidence
                if evidence.adjudication_use == "excluded"
                for field_name in evidence.field_names
                if field_name in required_fields
            }
        )
        if excluded_fields:
            reasons.extend(f"EXCLUDED_SOURCE_EVIDENCE:{item}" for item in excluded_fields)
        if reasons:
            return self._blocked(request, reasons)

        review_reasons = sorted(
            {
                "SOURCE_PRECEDENCE_REVIEW_REQUIRED"
                for evidence in request.source_evidence
                if evidence.adjudication_use == "review_required"
            }
        )
        if request.corpus is not None:
            review_reasons.extend(
                f"CORPUS_BLOCKER:{item}" for item in request.corpus.adjudication_blockers
            )
        if parsed.clarity_score < self.watch_clarity_min:
            rule_gate = RuleGate.REJECT_RULE
        elif parsed.clarity_score < self.pass_clarity_min or parsed.ambiguities or review_reasons:
            rule_gate = RuleGate.WATCH_RULE
        else:
            rule_gate = RuleGate.PASS

        corpus_hash = request.corpus.contract_corpus_sha256 if request.corpus else None
        revision_parts = (
            request.market_id,
            request.expected_rule_hash,
            corpus_hash or "NO_CORPUS",
            parsed,
            request.parser_version,
            self.version,
        )
        contract_revision_id = stable_record_id("rule_revision", *revision_parts)
        record_id = stable_record_id("rule_contract", *revision_parts)
        provenance_items = [
            ProvenanceRef(
                source_artifact_id=evidence.source_artifact_id,
                relation="rule_fields:" + ",".join(evidence.field_names),
                content_sha256=evidence.source_content_sha256,
                source_observed_at=evidence.observed_at,
            )
            for evidence in sorted(
                request.source_evidence,
                key=lambda item: (item.source_artifact_id, item.quote_start, item.quote_end),
            )
        ]
        if request.corpus is not None:
            provenance_items.append(
                ProvenanceRef(
                    source_artifact_id=request.corpus.source_artifact_id,
                    relation="point_in_time_contract_corpus",
                    content_sha256=request.corpus.contract_corpus_sha256,
                    source_observed_at=request.corpus.as_of,
                )
            )
        provenance = tuple(provenance_items)
        contract = RuleContract(
            record_id=record_id,
            run_id=request.run_id,
            created_at=request.compiled_at,
            source="polymarket_alpha.rule_contract_compiler",
            source_version=self.version,
            provenance=provenance,
            extensions={
                "market_snapshot_id": request.market_snapshot_id,
                "review_reasons": tuple(dict.fromkeys(review_reasons)),
            },
            market_id=request.market_id,
            rule_hash=request.expected_rule_hash,
            contract_revision_id=contract_revision_id,
            contract_corpus_sha256=corpus_hash,
            subject_entity=parsed.subject_entity,
            entity_match_rule=parsed.entity_match_rule,
            yes_trigger=parsed.yes_trigger,
            threshold=parsed.threshold,
            deadline=parsed.deadline,
            timezone=parsed.timezone,
            resolution_sources=parsed.resolution_sources,
            source_precedence=parsed.source_precedence,
            initial_or_final=parsed.initial_or_final,
            qualifying_examples=parsed.qualifying_examples,
            non_qualifying_examples=parsed.non_qualifying_examples,
            ambiguities=parsed.ambiguities,
            clarity_score=parsed.clarity_score,
            rule_gate=rule_gate,
            parser_version=request.parser_version,
        )
        receipt_reasons = [f"RULE_GATE:{rule_gate.value}"]
        receipt_reasons.extend(review_reasons)
        receipt = self._receipt(
            request,
            CompilationStatus.COMPILED,
            tuple(dict.fromkeys(receipt_reasons)),
            contract.record_id,
        )
        return RuleCompilationOutcome(contract=contract, receipt=receipt)

    def _blocked(
        self, request: RuleCompilationRequest, reasons: Sequence[str]
    ) -> RuleCompilationOutcome:
        receipt = self._receipt(
            request,
            CompilationStatus.BLOCKED,
            tuple(dict.fromkeys(reasons)),
            None,
        )
        return RuleCompilationOutcome(contract=None, receipt=receipt)

    def _receipt(
        self,
        request: RuleCompilationRequest,
        status: CompilationStatus,
        reasons: tuple[str, ...],
        rule_contract_id: str | None,
    ) -> RuleCompilationReceipt:
        corpus_hash = request.corpus.contract_corpus_sha256 if request.corpus else None
        record_id = stable_record_id(
            "rule_compilation_receipt",
            request.market_id,
            request.market_snapshot_id,
            request.expected_rule_hash,
            corpus_hash or "NO_CORPUS",
            self.version,
            status,
            reasons,
            rule_contract_id,
        )
        return RuleCompilationReceipt(
            record_id=record_id,
            run_id=request.run_id,
            created_at=request.compiled_at,
            source="polymarket_alpha.rule_contract_compiler",
            source_version=self.version,
            provenance=(),
            extensions={},
            market_id=request.market_id,
            market_snapshot_id=request.market_snapshot_id,
            input_rule_hash=request.expected_rule_hash,
            contract_corpus_sha256=corpus_hash,
            compiler_version=self.version,
            status=status,
            rule_contract_id=rule_contract_id,
            reasons=reasons,
            source_evidence_ids=tuple(
                sorted({item.source_artifact_id for item in request.source_evidence})
            ),
            source_evidence=tuple(
                sorted(
                    request.source_evidence,
                    key=lambda item: (item.source_artifact_id, item.quote_start, item.quote_end),
                )
            ),
        )
