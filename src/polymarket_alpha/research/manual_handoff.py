"""Gate R WP4 offline human handoff and immutable return capture.

This module has no provider, browser, network, shell, scheduler, repository or
order capability.  All bytes are supplied by the caller and either validated
in memory or written through the existing confined :class:`ArtifactStore`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import json
from pathlib import Path
from typing import Mapping

from ..artifacts import ArtifactConflictError, ArtifactPathError, ArtifactStore
from ..contracts import (
    ALPHA_CONTRACT_VERSION, BlindWorkOrderPromptSeal, ExportApprovalAction, ExportApprovalReceipt,
    ExactFileCopyPolicy, JsonAppendixParseStatus, ManualCaptureScope,
    ResearchAttempt, ResearchReturnCaptureSeal, SourceCapture, SourceCaptureManifest, SourcePlan,
    blind_leak_reasons, bytes_sha256, canonical_json, content_sha256, stable_record_id,
)
from ..contracts.base import ensure_utc
from .blind_plan import BlindPlanError, verify_blind_work_order_prompt
from .draft import ActualSourceBytes, CompiledResearchDraft, ResearchDraft, compile_research_draft
from .importer import ResearchImportOutcome, import_research_result


MANUAL_HANDOFF_VERSION = "gate_r_wp4_offline_v1"


class ManualHandoffError(ValueError):
    """A local handoff invariant failed closed."""


class CaptureDisposition(StrEnum):
    ACCEPTED = "ACCEPTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    QUARANTINED = "QUARANTINED"


@dataclass(frozen=True, slots=True)
class PromptExportReceipt:
    work_order_id: str
    prompt_sha256: str
    byte_length: int
    output_locator: str
    receipt_sha256: str


@dataclass(frozen=True, slots=True)
class CaptureQuarantineReceipt:
    """Append-only typed fact for a rejected return; raw bytes stay untouched."""
    logical_attempt_id: str
    disposition: CaptureDisposition
    reason_codes: tuple[str, ...]
    raw_response_sha256: str | None
    receipt_id: str
    receipt_sha256: str


@dataclass(frozen=True, slots=True)
class ManualReturnBinding:
    seal: ResearchReturnCaptureSeal
    manifest: SourceCaptureManifest
    disposition: CaptureDisposition
    critical_claim_ids: tuple[str, ...]


def quarantine_capture(*, logical_attempt_id: str, reason_codes: tuple[str, ...],
                       raw_response_bytes: bytes | None = None) -> CaptureQuarantineReceipt:
    """Create an append-only quarantine fact without altering submitted bytes."""
    if not logical_attempt_id.strip() or not reason_codes or any(not item.strip() for item in reason_codes):
        raise ManualHandoffError("quarantine requires attempt identity and reason codes")
    reasons = tuple(sorted(set(reason_codes)))
    digest = bytes_sha256(raw_response_bytes) if raw_response_bytes is not None else None
    payload = {"logical_attempt_id": logical_attempt_id, "disposition": CaptureDisposition.QUARANTINED,
               "reason_codes": reasons, "raw_response_sha256": digest}
    return CaptureQuarantineReceipt(**payload, receipt_id=stable_record_id("manual_capture_quarantine", payload),
                                    receipt_sha256=content_sha256(payload))


def _approval_payload(**value: object) -> dict[str, object]:
    return value


def create_export_approval(
    *, seal: BlindWorkOrderPromptSeal, prompt_bytes: bytes, approver_id: str,
    approved_at_utc: datetime, expires_at_utc: datetime, action: ExportApprovalAction,
    review_check_codes: tuple[str, ...], copy_policy: ExactFileCopyPolicy,
    prompt_patch_id: str | None = None,
    parent_approval: ExportApprovalReceipt | None = None,
) -> ExportApprovalReceipt:
    """Create an approval only after independently replaying the WP3 prompt seal."""
    approved_at_utc, expires_at_utc = ensure_utc(approved_at_utc), ensure_utc(expires_at_utc)
    try:
        verify_blind_work_order_prompt(seal=seal, prompt_bytes=prompt_bytes, verified_at_utc=approved_at_utc)
    except BlindPlanError as error:
        raise ManualHandoffError("cannot approve invalid or expired prompt seal") from error
    if expires_at_utc > seal.expires_at_utc:
        raise ManualHandoffError("approval cannot outlive prompt seal")
    # A patch is lineage only: the caller must supply the newly resealed work
    # order, not mutate this one.  Reusing a parent approval is forbidden.
    if bool(prompt_patch_id) != bool(parent_approval):
        raise ManualHandoffError("patched prompt and parent approval must occur together")
    if parent_approval is not None:
        try:
            parent = ExportApprovalReceipt.model_validate(parent_approval.model_dump(mode="python"))
        except ValueError as error:
            raise ManualHandoffError("parent approval is invalid") from error
        if parent.work_order_id == seal.work_order_id or parent.prompt_sha256 == seal.prompt_sha256:
            raise ManualHandoffError("patched prompt must be recompiled and resealed")
    else:
        parent = None
    payload = _approval_payload(
        work_order_id=seal.work_order_id, prompt_sha256=seal.prompt_sha256,
        approver_id=approver_id, approved_at_utc=approved_at_utc, expires_at_utc=expires_at_utc,
        action=action, review_check_codes=review_check_codes, copy_policy=copy_policy,
        prompt_patch_id=prompt_patch_id,
        parent_approval_receipt_id=(parent.approval_receipt_id if parent else None),
        parent_approval_sha256=(parent.approval_sha256 if parent else None),
        parent_work_order_id=(parent.work_order_id if parent else None),
        parent_prompt_sha256=(parent.prompt_sha256 if parent else None),
    )
    return ExportApprovalReceipt(
        approval_receipt_id=stable_record_id("export_approval_receipt", payload), approval_sha256=content_sha256(payload), **payload
    )


def validate_export_approval(*, approval: ExportApprovalReceipt, seal: BlindWorkOrderPromptSeal,
                             prompt_bytes: bytes, checked_at_utc: datetime) -> None:
    checked_at_utc = ensure_utc(checked_at_utc)
    try:
        ExportApprovalReceipt.model_validate(approval.model_dump(mode="python"))
        verify_blind_work_order_prompt(seal=seal, prompt_bytes=prompt_bytes, verified_at_utc=checked_at_utc)
    except (ValueError, BlindPlanError) as error:
        raise ManualHandoffError("approval or prompt seal replay failed") from error
    if approval.action != ExportApprovalAction.APPROVE:
        raise ManualHandoffError("export requires APPROVE receipt")
    if checked_at_utc >= approval.expires_at_utc:
        raise ManualHandoffError("export approval is expired")
    if approval.work_order_id != seal.work_order_id or approval.prompt_sha256 != seal.prompt_sha256:
        raise ManualHandoffError("approval does not bind prompt work order")


def export_exact_prompt(*, artifact_root: Path, output_locator: str, seal: BlindWorkOrderPromptSeal,
                        prompt_bytes: bytes, approval: ExportApprovalReceipt,
                        exported_at_utc: datetime) -> PromptExportReceipt:
    """Write exactly one caller-selected immutable prompt file; no web interaction."""
    validate_export_approval(approval=approval, seal=seal, prompt_bytes=prompt_bytes, checked_at_utc=exported_at_utc)
    store = ArtifactStore(artifact_root)
    locator = store.normalize_locator(output_locator)
    store.write_immutable(locator, prompt_bytes)
    payload = {"work_order_id": seal.work_order_id, "prompt_sha256": seal.prompt_sha256,
               "byte_length": len(prompt_bytes), "output_locator": locator}
    return PromptExportReceipt(**payload, receipt_sha256=content_sha256(payload))


def verify_source_capture_bytes(*, capture: SourceCapture, content_bytes: bytes | None) -> None:
    """Rebind durable metadata to caller-loaded immutable artifact bytes."""
    try:
        SourceCapture.model_validate(capture.model_dump(mode="python"))
    except ValueError as error:
        raise ManualHandoffError("source capture metadata replay failed") from error
    if capture.capture_scope == ManualCaptureScope.REFERENCE:
        if content_bytes is not None:
            raise ManualHandoffError("REFERENCE capture cannot receive content bytes")
        return
    if not isinstance(content_bytes, bytes) or not content_bytes:
        raise ManualHandoffError("captured source bytes are required")
    if len(content_bytes) != capture.content_length_bytes or bytes_sha256(content_bytes) != capture.content_sha256:
        raise ManualHandoffError("captured source bytes do not match metadata")


def build_source_manifest(*, captures: tuple[SourceCapture, ...], critical_claim_ids: tuple[str, ...],
                          created_at_utc: datetime) -> SourceCaptureManifest:
    created_at_utc = ensure_utc(created_at_utc)
    captures = tuple(sorted(captures, key=lambda x: x.source_capture_id))
    if not captures:
        raise ManualHandoffError("source manifest requires captures")
    cutoffs = {capture.pit_cutoff_utc for capture in captures}
    if len(cutoffs) != 1:
        raise ManualHandoffError("source captures must share one PIT cutoff")
    critical = tuple(sorted(set(critical_claim_ids)))
    payload = {"captures": captures, "pit_cutoff_utc": next(iter(cutoffs)),
        "critical_claim_ids": critical, "created_at_utc": created_at_utc}
    return SourceCaptureManifest(manifest_id=stable_record_id("source_capture_manifest", payload),
                                 manifest_sha256=content_sha256(payload), **payload)


def build_source_capture(**values: object) -> SourceCapture:
    """Construct a capture while deriving, rather than trusting, content digest/id.

    ``content_bytes`` is mandatory for FULL/EXCERPT and forbidden for
    REFERENCE.  This narrow builder is optional: direct model construction is
    equally strict and independently recomputes the same values.
    """
    data = dict(values)
    url = str(data.get("canonical_url", ""))
    redirects = tuple(data.get("redirect_chain", ()))
    if not url.startswith("https://") or any(not str(item).startswith("https://") for item in redirects):
        raise ManualHandoffError("source and redirect URLs must use https")
    if any(term in value.lower() for value in (url, *map(str, redirects))
           for term in ("polymarket", "gamma-api", "clob.polymarket", "market mirror")):
        raise ManualHandoffError("prediction-market venue and mirror sources are forbidden")
    scope = ManualCaptureScope(data["capture_scope"])
    raw = data.get("content_bytes")
    if scope == ManualCaptureScope.REFERENCE:
        data.update(content_bytes=None, content_sha256=None, content_length_bytes=None,
                    content_type=None, artifact_locator=None)
    else:
        if not isinstance(raw, bytes) or not raw:
            raise ManualHandoffError("FULL/EXCERPT source capture requires immutable bytes")
        data["content_sha256"] = bytes_sha256(raw)
        data["content_length_bytes"] = len(raw)
    # Materialize model defaults because the contract identity binds them too.
    data.setdefault("published_at_utc", None)
    data.setdefault("updated_at_utc", None)
    data.setdefault("effective_at_utc", None)
    data.setdefault("quote_locator_or_excerpt", None)
    data.setdefault("redirect_chain", ())
    data.setdefault("archive_or_version_identity", None)
    identity = {key: value for key, value in data.items() if key not in {"source_capture_id", "content_bytes"}}
    data["source_capture_id"] = stable_record_id("source_capture", identity)
    return SourceCapture(**data)


def persist_capture_artifacts(*, artifact_root: Path, binding: ManualReturnBinding) -> None:
    """Persist exactly the validated caller bytes through the existing store."""
    store = ArtifactStore(artifact_root)
    seal = binding.seal
    if seal.raw_transcript_bytes is None or seal.raw_response_bytes is None:
        raise ManualHandoffError("persistence requires the in-memory raw return bytes")
    if seal.json_parse_status != JsonAppendixParseStatus.ABSENT and seal.json_appendix_bytes is None:
        raise ManualHandoffError("persistence requires the in-memory JSON appendix bytes")
    items: list[tuple[str, bytes]] = [
        (seal.raw_transcript_locator, seal.raw_transcript_bytes),
        (seal.raw_response_locator, seal.raw_response_bytes),
    ]
    if seal.json_appendix_bytes is not None and seal.json_appendix_locator is not None:
        items.append((seal.json_appendix_locator, seal.json_appendix_bytes))
    for capture in binding.manifest.captures:
        verify_source_capture_bytes(capture=capture, content_bytes=capture.content_bytes)
        if capture.content_bytes is not None and capture.artifact_locator is not None:
            items.append((capture.artifact_locator, capture.content_bytes))
    normalized = tuple((store.normalize_locator(locator), data) for locator, data in items)
    if len({locator for locator, _ in normalized}) != len(normalized):
        raise ManualHandoffError("capture artifact locators must be unique")
    # Preflight every existing locator before the first write. Races remain
    # fail-closed in ArtifactStore; a completion marker distinguishes a complete batch.
    for locator, data in normalized:
        try:
            existing = store.read(locator)
        except ArtifactPathError:
            continue
        if existing != data:
            raise ArtifactConflictError(f"immutable locator conflict: {locator}")
    for locator, data in normalized:
        store.write_immutable(locator, data)
    marker = canonical_json({"return_seal_id": seal.return_seal_id,
        "return_seal_sha256": seal.return_seal_sha256,
        "locators": tuple(locator for locator, _ in normalized)}).encode("utf-8")
    store.write_immutable(f"_sealed/manual-capture/{seal.return_seal_id.split(':', 1)[1]}.json", marker)


def _parse_status(raw: bytes | None) -> JsonAppendixParseStatus:
    if raw is None:
        return JsonAppendixParseStatus.ABSENT
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonAppendixParseStatus.MALFORMED
    return JsonAppendixParseStatus.PARSED if isinstance(parsed, dict) else JsonAppendixParseStatus.MALFORMED


def capture_manual_return(
    *, seal: BlindWorkOrderPromptSeal, approval: ExportApprovalReceipt, attempt: ResearchAttempt,
    source_plan: SourcePlan,
    provider_ui: str, displayed_model: str, session_mode: str, operator_id: str,
    started_at_utc: datetime, completed_at_utc: datetime, captured_at_utc: datetime,
    raw_transcript_bytes: bytes, raw_transcript_locator: str, raw_response_bytes: bytes,
    raw_response_locator: str, json_appendix_bytes: bytes | None, json_appendix_locator: str | None,
    captures: tuple[SourceCapture, ...], observed_tool_usage: tuple[str, ...],
) -> ManualReturnBinding:
    """Freeze and locally validate raw return/source bytes without persisting them."""
    # The original exact bytes were verified during export.  A returned capture
    # does not contain prompt bytes, so it can only revalidate immutable seal /
    # approval identities and their binding, never fabricate a byte check.
    try:
        BlindWorkOrderPromptSeal.model_validate(seal.model_dump(mode="python"))
        ExportApprovalReceipt.model_validate(approval.model_dump(mode="python"))
        attempt = ResearchAttempt.model_validate(attempt.model_dump(mode="python"))
        source_plan = SourcePlan.model_validate(source_plan.model_dump(mode="python"))
    except ValueError as error:
        raise ManualHandoffError("return has invalid seal, approval, attempt, or SourcePlan metadata") from error
    if approval.action != ExportApprovalAction.APPROVE or approval.work_order_id != seal.work_order_id or approval.prompt_sha256 != seal.prompt_sha256:
        raise ManualHandoffError("return does not bind approved work order")
    started_at_utc, completed_at_utc, captured_at_utc = map(
        ensure_utc, (started_at_utc, completed_at_utc, captured_at_utc)
    )
    if attempt.job_id != seal.research_job_id:
        raise ManualHandoffError("ResearchAttempt does not bind the sealed research job")
    if (source_plan.source_plan_id != seal.source_plan_id
        or source_plan.source_plan_sha256 != seal.source_plan_sha256):
        raise ManualHandoffError("SourcePlan does not bind the sealed work order")
    if not (approval.approved_at_utc <= started_at_utc < approval.expires_at_utc):
        raise ManualHandoffError("attempt start is outside approval validity")
    if not (attempt.leased_at <= started_at_utc <= completed_at_utc <= captured_at_utc <= attempt.lease_expires_at):
        raise ManualHandoffError("return clocks are outside the bound ResearchAttempt lease")
    if not isinstance(raw_transcript_bytes, bytes) or not raw_transcript_bytes or not isinstance(raw_response_bytes, bytes) or not raw_response_bytes:
        raise ManualHandoffError("immutable raw transcript/response bytes are required")
    critical = tuple(sorted(source_plan.critical_claim_ids))
    manifest = build_source_manifest(captures=captures, critical_claim_ids=critical,
        created_at_utc=captured_at_utc)
    if manifest.pit_cutoff_utc != source_plan.pit_cutoff_utc:
        raise ManualHandoffError("source capture PIT cutoff does not bind SourcePlan")
    status = _parse_status(json_appendix_bytes)
    decoded_values: tuple[str, ...] = ()
    try:
        decoded_values = (raw_transcript_bytes.decode("utf-8"), raw_response_bytes.decode("utf-8"),
            json_appendix_bytes.decode("utf-8") if json_appendix_bytes is not None else "")
    except UnicodeDecodeError:
        status = JsonAppendixParseStatus.MALFORMED
    leaks = blind_leak_reasons(decoded_values)
    values = dict(
        research_job_id=seal.research_job_id, attempt_id=attempt.record_id,
        attempt_sha256=attempt.canonical_sha256, work_order_id=seal.work_order_id,
        prompt_sha256=seal.prompt_sha256, approval_receipt_id=approval.approval_receipt_id,
        provider_ui=provider_ui, displayed_model=displayed_model, session_mode=session_mode, operator_id=operator_id,
        started_at_utc=started_at_utc, completed_at_utc=completed_at_utc, captured_at_utc=captured_at_utc,
        raw_transcript_locator=raw_transcript_locator, raw_transcript_sha256=bytes_sha256(raw_transcript_bytes), raw_transcript_byte_length=len(raw_transcript_bytes),
        raw_response_locator=raw_response_locator, raw_response_sha256=bytes_sha256(raw_response_bytes), raw_response_byte_length=len(raw_response_bytes),
        json_appendix_locator=json_appendix_locator, json_appendix_sha256=(bytes_sha256(json_appendix_bytes) if json_appendix_bytes is not None else None),
        json_appendix_byte_length=(len(json_appendix_bytes) if json_appendix_bytes is not None else None), json_parse_status=status,
        source_manifest_id=manifest.manifest_id, source_manifest_sha256=manifest.manifest_sha256,
        copy_attestation=approval.copy_policy, observed_tool_usage=tuple(sorted(set(observed_tool_usage))),
        raw_transcript_bytes=raw_transcript_bytes, raw_response_bytes=raw_response_bytes, json_appendix_bytes=json_appendix_bytes,
    )
    logical_id = stable_record_id("research_return_capture", seal.research_job_id,
        attempt.record_id, attempt.canonical_sha256, seal.work_order_id)
    common = dict(schema_version=ALPHA_CONTRACT_VERSION,
        record_id=logical_id, return_seal_id=logical_id,
        run_id=seal.research_job_id, created_at=captured_at_utc,
        source="manual_research_return_capture", source_version=MANUAL_HANDOFF_VERSION,
        provenance=(), extensions={})
    identity = {**common, **values}
    identity.pop("return_seal_id")
    for key in ("raw_transcript_bytes", "raw_response_bytes", "json_appendix_bytes"):
        identity.pop(key)
    result = ResearchReturnCaptureSeal(return_seal_sha256=content_sha256(identity), **common, **values)
    critical_set = set(critical)
    covered = {claim for item in captures if item.capture_scope != ManualCaptureScope.REFERENCE and item.pit_available and item.primary_or_secondary == "PRIMARY" for claim in item.claim_ids}
    disposition = (CaptureDisposition.QUARANTINED
        if status != JsonAppendixParseStatus.PARSED or leaks
        else CaptureDisposition.ACCEPTED if critical_set.issubset(covered)
        else CaptureDisposition.INSUFFICIENT_EVIDENCE)
    return ManualReturnBinding(seal=result, manifest=manifest, disposition=disposition,
        critical_claim_ids=critical)


def bind_return_to_draft(*, binding: ManualReturnBinding, packet: object,
                         run_id: str, created_at: datetime) -> CompiledResearchDraft:
    """Pure adapter to the existing compiler; does not write a repository."""
    if binding.disposition == CaptureDisposition.QUARANTINED or binding.seal.json_parse_status != JsonAppendixParseStatus.PARSED:
        raise ManualHandoffError("quarantined or malformed return cannot enter draft compiler")
    try:
        draft = ResearchDraft.model_validate_json(binding.seal.json_appendix_bytes or b"")
    except ValueError as error:
        raise ManualHandoffError("captured JSON appendix is not a ResearchDraft") from error
    sources = {item.source_key: item for item in binding.manifest.captures}
    actual: list[ActualSourceBytes] = []
    for item in draft.sources:
        capture = sources.get(item.source_key)
        if capture is None:
            raise ManualHandoffError("draft source is absent from the captured manifest")
        expected_scope = {
            "FULL_DOCUMENT": ManualCaptureScope.FULL,
            "EXCERPT_ONLY": ManualCaptureScope.EXCERPT,
            "REFERENCE_ONLY": ManualCaptureScope.REFERENCE,
        }[item.capture_scope.value]
        if (capture.capture_scope != expected_scope or capture.canonical_url != item.source_url_or_source_id
            or capture.content_type != item.media_type):
            raise ManualHandoffError("draft source metadata does not match captured source")
        verify_source_capture_bytes(capture=capture, content_bytes=capture.content_bytes)
        if capture.capture_scope == ManualCaptureScope.REFERENCE:
            continue
        actual.append(ActualSourceBytes(source_key=item.source_key, content=capture.content_bytes or b""))
    return compile_research_draft(packet=packet, draft=draft, actual_sources=tuple(actual), run_id=run_id, created_at=created_at)


def import_bound_return(*, binding: ManualReturnBinding, packet: object, compiled: CompiledResearchDraft,
                        imported_at: datetime, run_id: str, submitted_artifact_locator: str) -> ResearchImportOutcome:
    """Pure bridge to the existing importer; it remains the sole import owner."""
    if binding.disposition != CaptureDisposition.ACCEPTED:
        raise ManualHandoffError("insufficient captured evidence cannot advance import")
    submitted = compiled.result.model_dump_json().encode("utf-8")
    return import_research_result(packet=packet, submitted_bytes=submitted, source_contents=compiled.source_contents,
        imported_at=imported_at, run_id=run_id, submitted_artifact_locator=submitted_artifact_locator)


__all__ = ["MANUAL_HANDOFF_VERSION", "ManualHandoffError", "CaptureDisposition", "CaptureQuarantineReceipt",
           "PromptExportReceipt", "ManualReturnBinding", "quarantine_capture", "create_export_approval", "validate_export_approval",
           "export_exact_prompt", "build_source_capture", "verify_source_capture_bytes", "build_source_manifest", "persist_capture_artifacts",
           "capture_manual_return", "bind_return_to_draft", "import_bound_return"]
