"""Gate R WP3: deterministic, offline-only Blind planning and prompt sealing.

No provider, network, filesystem, repository, or execution capability is
imported here.  The caller may export ``PromptSealBuild.prompt_bytes`` exactly;
this module itself only returns immutable values.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Mapping

from src.polymarket_alpha.contracts import (
    BlindCandidateProjection, BlindPlanArtifactBinding, BlindPlanLeakageReceipt,
    BlindPlanQuestion, BlindResearchPacket, BlindResearchPlanSeal,
    BlindResearchQuestionSet, BlindWorkOrderPromptSeal, CandidateSnapshotSeal,
    RuleContract, SourcePlan, bytes_sha256, canonical_json, content_sha256,
    stable_record_id,
)
from src.polymarket_alpha.contracts.base import ensure_utc
from .blind import BLIND_QUESTION_TEMPLATES


BLIND_PLAN_COMPILER_VERSION = "gate_r_wp3_v1"
BLIND_PROMPT_SEAL_VERSION = "gate_r_wp3_prompt_v1"

_FORBIDDEN = re.compile(
    r"polymarket|gamma[-_. ]?api|\bclob\b|https?://|\bslug\b|\b(?:bid|ask|"
    r"order\s*book|orderbook|odds|price)\b|\bwallet\b|\b(?:buy|sell|long|short)\s+"
    r"(?:yes|no)\b|\brecall\s*hit\b|\brecallhit\b|\bglm(?:[-_. ]?\d+(?:\.\d+)?)?\b|"
    r"\boperator(?:[_ -]?(?:commentary|note|text))?\b",
    re.IGNORECASE,
)
_FORBIDDEN_SOURCE = re.compile(r"polymarket|gamma|\bclob\b|mirror", re.IGNORECASE)

# These are the only evidence-gap renderings the compiler may emit.  Values
# inserted in them are taken solely from the already-cleared Blind packet.
_QUESTION_SPECS: Mapping[str, tuple[str, str, str, str, bool]] = {
    "BASE_RATE": ("BASE_RATE", "SOURCES_AND_SUMMARY", "historical factual record", "before cutoff", False),
    "DEADLINE_STATUS": ("STATUS", "PRIMARY_SOURCE_CITATION", "authoritative status", "before cutoff", True),
    "DISCONFIRMING_EVIDENCE": ("DISCONFIRMING", "PRIMARY_SOURCE_CITATION", "contrary factual record", "before cutoff", False),
    "ENTITY_STATUS": ("ENTITY_STATUS", "PRIMARY_SOURCE_CITATION", "entity status", "before cutoff", True),
    "RULE_TRIGGER_EVIDENCE": ("RULE_TRIGGER", "PRIMARY_SOURCE_CITATION", "rule-trigger fact", "before cutoff", True),
    "SOURCE_CONFLICT": ("SOURCE_CONFLICT", "SOURCE_COMPARISON", "source precedence conflict", "before cutoff", False),
}


class BlindPlanError(ValueError):
    """A binding, provenance, or leakage invariant failed closed."""


class BlindPlanLeakageError(BlindPlanError):
    """A recursively scanned value crosses the Blind boundary."""


def _scan(value: Any, path: str = "$") -> tuple[str, ...]:
    """Return typed paths for every forbidden nested string or mapping key."""

    hits: list[str] = []
    if isinstance(value, str):
        if _FORBIDDEN.search(value):
            hits.append(path)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            hits.extend(_scan(str(key), f"{path}.<key>"))
            hits.extend(_scan(item, f"{path}.{key}"))
    elif isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            hits.extend(_scan(item, f"{path}[{index}]"))
    elif hasattr(value, "model_dump"):
        hits.extend(_scan(value.model_dump(mode="python"), path))
    return tuple(hits)


def _require_clean(value: Any, *, label: str) -> None:
    hits = _scan(value)
    if hits:
        raise BlindPlanLeakageError(f"{label} contains forbidden Blind provenance at {hits}")


def _hash_binding(artifact_id: str, artifact_sha256: str) -> BlindPlanArtifactBinding:
    return BlindPlanArtifactBinding(artifact_id=artifact_id, artifact_sha256=artifact_sha256)


@dataclass(frozen=True, slots=True)
class SourcePlanPolicy:
    """Static, typed source/budget policy; it accepts no operator prose."""

    policy_id: str
    policy_version: str
    allowed_source_classes: tuple[str, ...]
    allowed_domains: tuple[str, ...]
    primary_source_requirements: tuple[str, ...]
    fallback_policy: str
    source_independence_policy: str
    capture_preference: str
    minimum_claim_coverage: int
    max_sources: int
    max_searches: int
    max_elapsed_minutes: int
    max_attempts: int
    stop_conditions: tuple[str, ...]
    freshness_policy: str
    availability_policy: str
    forbidden_source_classes: tuple[str, ...] = ("MARKET_VENUE", "MARKET_MIRROR")
    forbidden_domains: tuple[str, ...] = ("polymarket.com", "gamma-api.polymarket.com", "clob.polymarket.com")

    def validate(self) -> None:
        values = self.__dict__ if hasattr(self, "__dict__") else {
            name: getattr(self, name) for name in self.__dataclass_fields__
        }
        _require_clean({key: value for key, value in values.items()
            if key not in {"forbidden_source_classes", "forbidden_domains"}}, label="source policy")
        if any(_FORBIDDEN_SOURCE.search(item) for item in self.allowed_domains + self.allowed_source_classes):
            raise BlindPlanLeakageError("source policy allows forbidden venue or mirror source")
        if set(self.allowed_source_classes).intersection(self.forbidden_source_classes):
            raise BlindPlanError("source class cannot be both allowed and forbidden")
        if set(self.allowed_domains).intersection(self.forbidden_domains):
            raise BlindPlanError("source domain cannot be both allowed and forbidden")
        if not any(_FORBIDDEN_SOURCE.search(item) for item in self.forbidden_source_classes + self.forbidden_domains):
            raise BlindPlanError("source policy must explicitly forbid venue and mirror sources")
        if not self.policy_id.strip() or not self.policy_version.strip():
            raise BlindPlanError("source policy identity is required")
        if not self.allowed_source_classes or not self.primary_source_requirements:
            raise BlindPlanError("source policy requires primary-source coverage")
        if min(self.minimum_claim_coverage, self.max_sources, self.max_elapsed_minutes, self.max_attempts) < 1:
            raise BlindPlanError("source policy budgets must be positive")
        if self.max_searches < 0 or not self.stop_conditions:
            raise BlindPlanError("source policy requires bounded searches and stop conditions")


DEFAULT_SOURCE_PLAN_POLICY = SourcePlanPolicy(
    policy_id="gate_r_blind_primary",
    policy_version="v1",
    allowed_source_classes=("OFFICIAL_PRIMARY", "INDEPENDENT_PUBLIC_RECORD"),
    allowed_domains=(),
    primary_source_requirements=("RULE_TRIGGER", "STATUS", "ENTITY_STATUS"),
    fallback_policy="USE_INDEPENDENT_PUBLIC_RECORD_ONLY_WHEN_PRIMARY_UNAVAILABLE",
    source_independence_policy="DISTINCT_PUBLISHER_REQUIRED_FOR_CONFLICT",
    capture_preference="FULL_DOCUMENT_THEN_EXCERPT",
    minimum_claim_coverage=1,
    max_sources=6,
    max_searches=8,
    max_elapsed_minutes=30,
    max_attempts=2,
    stop_conditions=("CRITICAL_CLAIMS_COVERED", "BUDGET_EXHAUSTED", "PRIMARY_UNAVAILABLE"),
    freshness_policy="SOURCE_EFFECTIVE_AT_OR_BEFORE_PIT_CUTOFF",
    availability_policy="RECORD_PIT_AVAILABILITY_OR_DECLARE_UNAVAILABLE",
)


@dataclass(frozen=True, slots=True)
class BlindPlanBuild:
    question_set: BlindResearchQuestionSet
    source_plan: SourcePlan
    leakage_receipt: BlindPlanLeakageReceipt
    plan_seal: BlindResearchPlanSeal


@dataclass(frozen=True, slots=True)
class PromptSealBuild:
    seal: BlindWorkOrderPromptSeal
    prompt_bytes: bytes

    def export_bytes(self) -> bytes:
        """Return the exact immutable LF/UTF-8 bytes for caller-controlled export."""
        return self.prompt_bytes


class BlindResearchPlanCompiler:
    """Atomic compiler for a QuestionSet and SourcePlan, entirely in memory."""

    def __init__(self, *, compiler_policy_id: str = "gate_r_blind_plan", compiler_policy_version: str = "v1") -> None:
        if not compiler_policy_id.strip() or not compiler_policy_version.strip():
            raise BlindPlanError("compiler policy identity is required")
        _require_clean((compiler_policy_id, compiler_policy_version), label="compiler policy")
        self.compiler_policy_id = compiler_policy_id
        self.compiler_policy_version = compiler_policy_version

    def compile(
        self,
        *,
        candidate_snapshot: CandidateSnapshotSeal,
        projection: BlindCandidateProjection,
        packet: BlindResearchPacket,
        rule_contract: RuleContract,
        input_artifact_sha256s: Mapping[str, str],
        research_as_of_utc: datetime,
        pit_cutoff_utc: datetime,
        source_policy: SourcePlanPolicy = DEFAULT_SOURCE_PLAN_POLICY,
        created_at_utc: datetime,
    ) -> BlindPlanBuild:
        """Compile both artifacts or raise; no partial result is constructed."""

        research_as_of_utc, pit_cutoff_utc, created_at_utc = map(
            ensure_utc, (research_as_of_utc, pit_cutoff_utc, created_at_utc)
        )
        if not (pit_cutoff_utc <= research_as_of_utc <= created_at_utc):
            raise BlindPlanError("Blind planning clocks must order cutoff, research-as-of, creation")
        source_policy.validate()
        self._validate_bindings(candidate_snapshot, projection, packet, rule_contract, input_artifact_sha256s)
        scan_payload = {
            "candidate_snapshot": candidate_snapshot.model_dump(mode="python"),
            "projection": projection.model_dump(mode="python"),
            "packet": packet.model_dump(mode="python"),
            "blind_rule": packet.blind_rule.model_dump(mode="python"),
            "input_artifacts": dict(input_artifact_sha256s),
            "source_policy": {name: getattr(source_policy, name) for name in source_policy.__dataclass_fields__
                if name not in {"forbidden_source_classes", "forbidden_domains"}},
        }
        _require_clean(scan_payload, label="Blind plan inputs")
        input_hash = content_sha256(scan_payload)
        receipt_payload = {
            "scanner_version": BLIND_PLAN_COMPILER_VERSION,
            "scanned_input_sha256": input_hash,
            "status": "PASS",
            "checked_paths": ("$",),
        }
        receipt_id = stable_record_id("blind_plan_leakage_receipt", receipt_payload)
        receipt_hash = content_sha256(receipt_payload)
        receipt = BlindPlanLeakageReceipt(
            receipt_id=receipt_id, receipt_sha256=receipt_hash, scanner_version=BLIND_PLAN_COMPILER_VERSION,
            scanned_input_sha256=input_hash, status="PASS", checked_paths=("$",),
        )
        artifacts = tuple(_hash_binding(key, input_artifact_sha256s[key]) for key in sorted(input_artifact_sha256s))
        questions = self._questions(packet, rule_contract, pit_cutoff_utc)
        question_identity = {
            "candidate_snapshot_id": candidate_snapshot.record_id,
            "candidate_snapshot_sha256": candidate_snapshot.canonical_sha256,
            "blind_projection_id": projection.record_id,
            "blind_projection_sha256": projection.canonical_sha256,
            "blind_packet_id": packet.record_id,
            "blind_packet_sha256": packet.canonical_sha256,
            "rule_contract_id": rule_contract.record_id,
            "rule_contract_sha256": rule_contract.canonical_sha256,
            "compiler_policy_id": self.compiler_policy_id,
            "compiler_policy_version": self.compiler_policy_version,
            "research_as_of_utc": research_as_of_utc,
            "pit_cutoff_utc": pit_cutoff_utc,
            "allowed_input_artifacts": artifacts,
            "leakage_scan_receipt_id": receipt.receipt_id,
            "questions": questions,
            "created_at_utc": created_at_utc,
        }
        question_set_id = stable_record_id("blind_question_set", question_identity)
        question_set_hash = content_sha256(question_identity)
        question_set = BlindResearchQuestionSet(
            schema_version="gate_r_wp3_v1", question_set_id=question_set_id, question_set_sha256=question_set_hash,
            candidate_snapshot_id=candidate_snapshot.record_id, candidate_snapshot_sha256=candidate_snapshot.canonical_sha256,
            blind_projection_id=projection.record_id, blind_projection_sha256=projection.canonical_sha256,
            blind_packet_id=packet.record_id, blind_packet_sha256=packet.canonical_sha256,
            rule_contract_id=rule_contract.record_id, rule_contract_sha256=rule_contract.canonical_sha256,
            compiler_policy_id=self.compiler_policy_id, compiler_policy_version=self.compiler_policy_version,
            research_as_of_utc=research_as_of_utc, pit_cutoff_utc=pit_cutoff_utc,
            allowed_input_artifacts=artifacts, leakage_scan_receipt_id=receipt.receipt_id,
            questions=questions, created_at_utc=created_at_utc,
        )
        question_ids = tuple(question.question_id for question in questions)
        critical = tuple(question.question_id for question in questions if question.required)
        critical_types = tuple(sorted({question.claim_type for question in questions if question.required}))
        plan_identity = {
            "schema_version": "gate_r_wp3_v1",
            "question_set_id": question_set.question_set_id,
            "question_set_sha256": question_set.question_set_sha256,
            "rule_contract_id": rule_contract.record_id,
            "rule_contract_sha256": rule_contract.canonical_sha256,
            "source_policy_id": source_policy.policy_id,
            "source_policy_version": source_policy.policy_version,
            "pit_cutoff_utc": pit_cutoff_utc,
            "allowed_source_classes": source_policy.allowed_source_classes,
            "allowed_domains": source_policy.allowed_domains,
            "forbidden_source_classes": source_policy.forbidden_source_classes,
            "forbidden_domains": source_policy.forbidden_domains,
            "primary_source_requirements": source_policy.primary_source_requirements,
            "fallback_policy": source_policy.fallback_policy,
            "source_independence_policy": source_policy.source_independence_policy,
            "capture_preference": source_policy.capture_preference,
            "minimum_claim_coverage": source_policy.minimum_claim_coverage,
            "question_ids": question_ids,
            "critical_claim_ids": critical,
            "critical_claim_types": critical_types,
            "max_sources": source_policy.max_sources,
            "max_searches": source_policy.max_searches,
            "max_elapsed_minutes": source_policy.max_elapsed_minutes,
            "max_attempts": source_policy.max_attempts,
            "stop_conditions": source_policy.stop_conditions,
            "freshness_policy": source_policy.freshness_policy,
            "availability_policy": source_policy.availability_policy,
            "created_at_utc": created_at_utc,
        }
        source_plan_id = stable_record_id("blind_source_plan", plan_identity)
        source_plan_hash = content_sha256(plan_identity)
        source_plan = SourcePlan(
            schema_version="gate_r_wp3_v1", source_plan_id=source_plan_id, source_plan_sha256=source_plan_hash,
            question_set_id=question_set.question_set_id, question_set_sha256=question_set.question_set_sha256,
            rule_contract_id=rule_contract.record_id, rule_contract_sha256=rule_contract.canonical_sha256,
            source_policy_id=source_policy.policy_id, source_policy_version=source_policy.policy_version,
            pit_cutoff_utc=pit_cutoff_utc, allowed_source_classes=source_policy.allowed_source_classes,
            allowed_domains=source_policy.allowed_domains, forbidden_source_classes=source_policy.forbidden_source_classes,
            forbidden_domains=source_policy.forbidden_domains,
            primary_source_requirements=source_policy.primary_source_requirements,
            fallback_policy=source_policy.fallback_policy, source_independence_policy=source_policy.source_independence_policy,
            capture_preference=source_policy.capture_preference, minimum_claim_coverage=source_policy.minimum_claim_coverage,
            question_ids=question_ids, critical_claim_ids=critical, critical_claim_types=critical_types,
            max_sources=source_policy.max_sources, max_searches=source_policy.max_searches,
            max_elapsed_minutes=source_policy.max_elapsed_minutes, max_attempts=source_policy.max_attempts,
            stop_conditions=source_policy.stop_conditions, freshness_policy=source_policy.freshness_policy,
            availability_policy=source_policy.availability_policy, created_at_utc=created_at_utc,
        )
        seal_payload = {"question_set_id": question_set_id, "question_set_sha256": question_set_hash,
            "source_plan_id": source_plan_id, "source_plan_sha256": source_plan_hash,
            "created_at_utc": created_at_utc}
        seal_id = stable_record_id("blind_research_plan_seal", seal_payload)
        seal_hash = content_sha256(seal_payload)
        seal = BlindResearchPlanSeal(plan_seal_id=seal_id, plan_seal_sha256=seal_hash,
            question_set_id=question_set_id, question_set_sha256=question_set_hash,
            source_plan_id=source_plan_id, source_plan_sha256=source_plan_hash, created_at_utc=created_at_utc)
        return BlindPlanBuild(question_set=question_set, source_plan=source_plan, leakage_receipt=receipt, plan_seal=seal)

    @staticmethod
    def _validate_bindings(snapshot: CandidateSnapshotSeal, projection: BlindCandidateProjection,
                           packet: BlindResearchPacket, rule: RuleContract,
                           artifacts: Mapping[str, str]) -> None:
        if snapshot.eligibility.value != "ELIGIBLE":
            raise BlindPlanError("candidate snapshot must be ELIGIBLE")
        if projection.rule_contract_hash != rule.rule_hash or packet.blind_rule.rule_hash != rule.rule_hash:
            raise BlindPlanError("projection/packet rule hash does not bind RuleContract")
        if packet.projection.record_id != projection.record_id or packet.projection.canonical_sha256 != projection.canonical_sha256:
            raise BlindPlanError("packet does not bind supplied BlindCandidateProjection")
        expected_blind_candidate = stable_record_id("blind_candidate", {
            "candidate_id": snapshot.candidate_id,
            "rule_hash": rule.rule_hash,
            "contract_revision_id": rule.contract_revision_id,
            "questions": tuple(question.question_id for question in projection.research_questions),
            "evidence": tuple(item.evidence_id for item in projection.evidence),
        })
        if projection.record_id != expected_blind_candidate:
            raise BlindPlanError("Blind projection does not derive from the sealed Candidate snapshot")
        expected_rule_view = type(packet.blind_rule).from_rule_contract(rule)
        if packet.blind_rule != expected_rule_view:
            raise BlindPlanError("Blind rule view is not the supplied RuleContract projection")
        expected_packet_id = stable_record_id("blind_packet", {
            "projection_sha256": projection.canonical_sha256,
            "blind_rule_sha256": content_sha256(expected_rule_view),
        })
        if packet.record_id != expected_packet_id:
            raise BlindPlanError("Blind packet id does not bind the supplied projection and rule")
        if set(artifacts) != set(snapshot.allowed_projection_input_ids):
            raise BlindPlanError("input artifacts must exactly bind CandidateSnapshot allowlist")
        if snapshot.canonical_rule_source_artifact_id not in artifacts:
            raise BlindPlanError("input artifacts must retain the snapshot rule-source binding")
        if artifacts[snapshot.canonical_rule_source_artifact_id] != snapshot.rule_source_sha256:
            raise BlindPlanError("rule-source artifact hash does not match CandidateSnapshot")
        for artifact_id, digest in artifacts.items():
            if not artifact_id.strip() or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise BlindPlanError("input artifact bindings require ids and SHA-256 values")

    @staticmethod
    def _questions(packet: BlindResearchPacket, rule: RuleContract, cutoff: datetime) -> tuple[BlindPlanQuestion, ...]:
        result: list[BlindPlanQuestion] = []
        for origin in packet.projection.research_questions:
            if origin.generated_from_rule_contract_hash != rule.rule_hash:
                raise BlindPlanError("question provenance does not bind the supplied RuleContract")
            spec = _QUESTION_SPECS.get(origin.template_id)
            if spec is None:
                raise BlindPlanError("packet question is outside the WP3 allowlisted taxonomy")
            claim_type, answer_type, target, scope, required = spec
            expected_text = BLIND_QUESTION_TEMPLATES[origin.template_id].format(
                subject=rule.subject_entity, trigger=rule.yes_trigger,
                deadline=(rule.deadline.isoformat() if rule.deadline else "the rule deadline"),
            )
            expected_origin_id = stable_record_id("blind_question", {
                "template_id": origin.template_id, "rule_hash": rule.rule_hash,
                "evidence_ids": tuple(sorted(origin.generated_from_evidence_ids)),
            })
            if origin.text != expected_text or origin.question_id != expected_origin_id:
                raise BlindPlanError("question text/id does not derive from its allowlisted template provenance")
            if not set(origin.generated_from_evidence_ids).issubset(
                item.evidence_id for item in packet.projection.evidence
            ):
                raise BlindPlanError("question provenance references evidence outside the Blind projection")
            question_payload = {"claim_type": claim_type, "neutral_question_text": expected_text,
                "required_answer_type": answer_type, "evidence_target": target,
                "time_scope": scope, "required": required, "dependency_ids": (),
                "provenance_template_id": origin.template_id,
                "provenance_evidence_ids": tuple(sorted(origin.generated_from_evidence_ids))}
            question_id = stable_record_id("blind_plan_question", question_payload)
            result.append(BlindPlanQuestion(question_id=question_id, claim_type=claim_type,
                neutral_question_text=expected_text, required_answer_type=answer_type,
                evidence_target=target, time_scope=scope, required=required,
                dependency_ids=(), provenance_template_id=origin.template_id,
                provenance_evidence_ids=tuple(sorted(origin.generated_from_evidence_ids))))
        if not result:
            raise BlindPlanError("Blind projection has no allowlisted questions")
        return tuple(sorted(result, key=lambda item: item.question_id))


def seal_blind_work_order_prompt(
    *, plan: BlindPlanBuild, candidate_snapshot: CandidateSnapshotSeal, packet: BlindResearchPacket,
    rule_contract: RuleContract, research_job_id: str, attempt_policy_id: str,
    output_schema_id: str, output_schema_sha256: str, provider_policy_id: str,
    provider_policy_version: str, created_at_utc: datetime, expires_at_utc: datetime,
) -> PromptSealBuild:
    """Render exact UTF-8/LF bytes and return their identity-bound prompt seal."""

    created_at_utc, expires_at_utc = map(ensure_utc, (created_at_utc, expires_at_utc))
    if expires_at_utc <= created_at_utc:
        raise BlindPlanError("prompt expiry must be after creation")
    question_set, source_plan = plan.question_set, plan.source_plan
    if (question_set.question_set_id != source_plan.question_set_id or
        question_set.question_set_sha256 != source_plan.question_set_sha256):
        raise BlindPlanError("QuestionSet/SourcePlan cross-hash mismatch")
    if (plan.plan_seal.question_set_id != question_set.question_set_id or
        plan.plan_seal.question_set_sha256 != question_set.question_set_sha256 or
        plan.plan_seal.source_plan_id != source_plan.source_plan_id or
        plan.plan_seal.source_plan_sha256 != source_plan.source_plan_sha256):
        raise BlindPlanError("plan seal does not bind QuestionSet and SourcePlan")
    question_ids = tuple(question.question_id for question in question_set.questions)
    critical = tuple(question.question_id for question in question_set.questions if question.required)
    critical_types = tuple(sorted({question.claim_type for question in question_set.questions if question.required}))
    if (source_plan.question_ids != question_ids or source_plan.critical_claim_ids != critical or
        source_plan.critical_claim_types != critical_types):
        raise BlindPlanError("SourcePlan claim policy does not match the QuestionSet")
    if (question_set.candidate_snapshot_id != candidate_snapshot.record_id or
        question_set.candidate_snapshot_sha256 != candidate_snapshot.canonical_sha256 or
        question_set.blind_packet_id != packet.record_id or question_set.blind_packet_sha256 != packet.canonical_sha256 or
        question_set.rule_contract_id != rule_contract.record_id or question_set.rule_contract_sha256 != rule_contract.canonical_sha256):
        raise BlindPlanError("prompt inputs do not bind the compiled plan")
    provider_safe_source_plan = source_plan.model_dump(
        mode="python", exclude={"forbidden_source_classes", "forbidden_domains"}
    )
    _require_clean((question_set, provider_safe_source_plan, research_job_id, attempt_policy_id,
                    output_schema_id, provider_policy_id, provider_policy_version), label="prompt inputs")
    if not re.fullmatch(r"[0-9a-f]{64}", output_schema_sha256):
        raise BlindPlanError("output schema hash must be SHA-256")
    prompt_value = {
        "task": "Research each neutral question using the source plan and return only the required schema.",
        "questions": [item.model_dump(mode="json") for item in question_set.questions],
        "source_plan": {
            "allowed_source_classes": source_plan.allowed_source_classes,
            "allowed_domains": source_plan.allowed_domains,
            "forbidden_source_classes": ("MARKET_VENUE", "MARKET_MIRROR"),
            "primary_source_requirements": source_plan.primary_source_requirements,
            "fallback_policy": source_plan.fallback_policy,
            "source_independence_policy": source_plan.source_independence_policy,
            "capture_preference": source_plan.capture_preference,
            "minimum_claim_coverage": source_plan.minimum_claim_coverage,
            "critical_claim_ids": source_plan.critical_claim_ids,
            "max_sources": source_plan.max_sources, "max_searches": source_plan.max_searches,
            "max_elapsed_minutes": source_plan.max_elapsed_minutes, "max_attempts": source_plan.max_attempts,
            "stop_conditions": source_plan.stop_conditions, "freshness_policy": source_plan.freshness_policy,
            "availability_policy": source_plan.availability_policy,
        },
        "pit_cutoff_utc": question_set.pit_cutoff_utc,
        "output_schema": {"id": output_schema_id, "sha256": output_schema_sha256},
    }
    _require_clean(prompt_value, label="rendered prompt")
    prompt_bytes = (canonical_json(prompt_value) + "\n").encode("utf-8")
    if b"\r" in prompt_bytes:
        raise BlindPlanError("prompt must be LF only")
    prompt_hash, preview_hash = bytes_sha256(prompt_bytes), bytes_sha256(prompt_bytes[:512])
    if created_at_utc < plan.plan_seal.created_at_utc:
        raise BlindPlanError("prompt seal cannot precede the research plan")
    identity = {
        "research_job_id": research_job_id,
        "attempt_policy_id": attempt_policy_id,
        "candidate_snapshot_id": candidate_snapshot.record_id,
        "candidate_snapshot_sha256": candidate_snapshot.canonical_sha256,
        "rule_contract_id": rule_contract.record_id,
        "rule_contract_sha256": rule_contract.canonical_sha256,
        "blind_packet_id": packet.record_id,
        "blind_packet_sha256": packet.canonical_sha256,
        "question_set_id": question_set.question_set_id,
        "question_set_sha256": question_set.question_set_sha256,
        "source_plan_id": source_plan.source_plan_id,
        "source_plan_sha256": source_plan.source_plan_sha256,
        "plan_seal_id": plan.plan_seal.plan_seal_id,
        "plan_seal_sha256": plan.plan_seal.plan_seal_sha256,
        "output_schema_id": output_schema_id,
        "output_schema_sha256": output_schema_sha256,
        "provider_policy_id": provider_policy_id,
        "provider_policy_version": provider_policy_version,
        "content_type": "text/plain", "encoding": "utf-8", "newline_mode": "LF",
        "byte_length": len(prompt_bytes), "prompt_sha256": prompt_hash,
        "preview_sha256": preview_hash, "created_at_utc": created_at_utc,
        "expires_at_utc": expires_at_utc,
    }
    work_order_id = stable_record_id("blind_work_order", identity)
    seal_payload = {"work_order_id": work_order_id, **identity}
    seal = BlindWorkOrderPromptSeal(schema_version="gate_r_wp3_v1", work_order_id=work_order_id,
        research_job_id=research_job_id, attempt_policy_id=attempt_policy_id,
        candidate_snapshot_id=candidate_snapshot.record_id, candidate_snapshot_sha256=candidate_snapshot.canonical_sha256,
        rule_contract_id=rule_contract.record_id, rule_contract_sha256=rule_contract.canonical_sha256,
        blind_packet_id=packet.record_id, blind_packet_sha256=packet.canonical_sha256,
        question_set_id=question_set.question_set_id, question_set_sha256=question_set.question_set_sha256,
        source_plan_id=source_plan.source_plan_id, source_plan_sha256=source_plan.source_plan_sha256,
        plan_seal_id=plan.plan_seal.plan_seal_id, plan_seal_sha256=plan.plan_seal.plan_seal_sha256,
        output_schema_id=output_schema_id, output_schema_sha256=output_schema_sha256,
        provider_policy_id=provider_policy_id, provider_policy_version=provider_policy_version,
        content_type="text/plain", encoding="utf-8", newline_mode="LF", byte_length=len(prompt_bytes),
        prompt_sha256=prompt_hash, preview_sha256=preview_hash, created_at_utc=created_at_utc,
        expires_at_utc=expires_at_utc, seal_sha256=content_sha256(seal_payload))
    return PromptSealBuild(seal=seal, prompt_bytes=prompt_bytes)


def verify_blind_work_order_prompt(*, seal: BlindWorkOrderPromptSeal, prompt_bytes: bytes,
                                   verified_at_utc: datetime) -> None:
    """Fail closed unless caller-presented export bytes exactly match the seal.

    This is intentionally a pure byte check; export, approval, and external
    provider interaction remain owned by later work packages.
    """

    verified_at_utc = ensure_utc(verified_at_utc)
    try:
        BlindWorkOrderPromptSeal.model_validate(seal.model_dump(mode="python"))
    except Exception as error:
        raise BlindPlanError("prompt seal metadata or identity is invalid") from error
    if verified_at_utc >= seal.expires_at_utc:
        raise BlindPlanError("prompt seal is expired")
    if not isinstance(prompt_bytes, bytes) or not prompt_bytes or b"\r" in prompt_bytes:
        raise BlindPlanError("prompt bytes must be non-empty UTF-8 LF bytes")
    try:
        prompt_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BlindPlanError("prompt bytes are not UTF-8") from error
    if (len(prompt_bytes) != seal.byte_length or bytes_sha256(prompt_bytes) != seal.prompt_sha256 or
            bytes_sha256(prompt_bytes[:512]) != seal.preview_sha256):
        raise BlindPlanError("prompt bytes do not match the sealed hash, preview, or length")
