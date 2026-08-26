"""Offline importer for manually supplied structured research results.

The importer accepts bytes and caller-supplied source artifacts.  It does not
read files, call a model, browse, or trust hashes declared by the result.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Mapping, NamedTuple
import unicodedata

from pydantic import ValidationError

from ..contracts import (
    BlindResearchPacket,
    CaptureScope,
    HashScope,
    MarketResearchPacket,
    PacketStage,
    ResearchImportReason,
    ResearchImportReceipt,
    ResearchImportStatus,
    ResearchResultEnvelope,
    Replayability,
    SourceArtifact,
    bytes_sha256,
    stable_record_id,
)
from ..contracts.base import ensure_utc, validate_sha256


RESEARCH_IMPORTER_VERSION = "p0_08b_v1"
ResearchPacketValue = BlindResearchPacket | MarketResearchPacket


class ResearchImportOutcome(NamedTuple):
    submitted_artifact: SourceArtifact
    result: ResearchResultEnvelope | None
    receipt: ResearchImportReceipt


def _normalized_text_bytes(raw: bytes) -> bytes:
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("NORMALIZED_TEXT source content must be UTF-8") from error
    normalized = unicodedata.normalize("NFC", decoded).replace("\r\n", "\n").replace("\r", "\n")
    return normalized.encode("utf-8")


def _actual_content_hash(artifact: SourceArtifact, raw: bytes) -> str:
    if artifact.hash_scope == HashScope.NORMALIZED_TEXT:
        return hashlib.sha256(_normalized_text_bytes(raw)).hexdigest()
    if artifact.hash_scope in {HashScope.RAW_BYTES, HashScope.CLAIM_EXCERPT}:
        return bytes_sha256(raw)
    raise ValueError("captured SourceArtifact requires a supported hash_scope")


def _submitted_artifact(
    *,
    submitted_bytes: bytes,
    imported_at: datetime,
    run_id: str,
    artifact_locator: str,
) -> SourceArtifact:
    digest = bytes_sha256(submitted_bytes)
    artifact_id = stable_record_id(
        "source_artifact",
        "research_submission",
        digest,
        run_id,
        imported_at,
        artifact_locator,
    )
    return SourceArtifact(
        record_id=artifact_id,
        run_id=run_id,
        created_at=imported_at,
        source="manual_research_submission",
        source_version=RESEARCH_IMPORTER_VERSION,
        provenance=(),
        extensions={},
        artifact_id=artifact_id,
        source_name="manual structured research result",
        source_url_or_source_id=f"manual-submission:{digest}",
        media_type="application/json",
        captured_at=imported_at,
        effective_as_of=imported_at,
        capture_scope=CaptureScope.FULL_DOCUMENT,
        hash_scope=HashScope.RAW_BYTES,
        content_sha256=digest,
        content_length_bytes=len(submitted_bytes),
        artifact_locator=artifact_locator,
        replayability=Replayability.FULL,
    )


def _receipt(
    *,
    packet: ResearchPacketValue,
    submitted_artifact: SourceArtifact,
    status: ResearchImportStatus,
    reasons: tuple[ResearchImportReason, ...],
    imported_at: datetime,
    run_id: str,
    accepted_result: ResearchResultEnvelope | None = None,
) -> ResearchImportReceipt:
    ordered_reasons = tuple(sorted(reasons, key=lambda item: item.value))
    receipt_id = stable_record_id(
        "research_import_receipt",
        packet.record_id,
        packet.canonical_sha256,
        submitted_artifact.content_sha256,
        status,
        ordered_reasons,
        run_id,
        imported_at,
    )
    return ResearchImportReceipt(
        record_id=receipt_id,
        run_id=run_id,
        created_at=imported_at,
        source="research_result_importer",
        source_version=RESEARCH_IMPORTER_VERSION,
        provenance=(),
        extensions={},
        import_receipt_id=receipt_id,
        packet_stage=packet.packet_stage,
        packet_id=packet.record_id,
        packet_sha256=packet.canonical_sha256,
        submitted_artifact_id=submitted_artifact.artifact_id,
        submitted_result_sha256=submitted_artifact.content_sha256,
        status=status,
        reasons=ordered_reasons,
        imported_at=imported_at,
        importer_version=RESEARCH_IMPORTER_VERSION,
        accepted_result_id=(accepted_result.result_id if accepted_result else None),
        accepted_result_sha256=(accepted_result.canonical_sha256 if accepted_result else None),
        quarantine_artifact_id=(
            submitted_artifact.artifact_id
            if status == ResearchImportStatus.QUARANTINED
            else None
        ),
    )


def _quarantine(
    *,
    packet: ResearchPacketValue,
    submitted_artifact: SourceArtifact,
    reasons: tuple[ResearchImportReason, ...],
    imported_at: datetime,
    run_id: str,
) -> ResearchImportOutcome:
    return ResearchImportOutcome(
        submitted_artifact,
        None,
        _receipt(
            packet=packet,
            submitted_artifact=submitted_artifact,
            status=ResearchImportStatus.QUARANTINED,
            reasons=reasons,
            imported_at=imported_at,
            run_id=run_id,
        ),
    )


def import_research_result(
    *,
    packet: ResearchPacketValue,
    submitted_bytes: bytes,
    source_contents: Mapping[str, bytes],
    imported_at: datetime,
    run_id: str,
    submitted_artifact_locator: str,
    expected_submitted_sha256: str | None = None,
) -> ResearchImportOutcome:
    """Validate one manual result and return immutable accept/quarantine facts."""

    imported_at = ensure_utc(imported_at)
    if not isinstance(submitted_bytes, bytes) or not submitted_bytes:
        raise ValueError("submitted_bytes must be non-empty immutable bytes")
    if not run_id.strip() or not submitted_artifact_locator.strip():
        raise ValueError("run_id and submitted_artifact_locator must not be blank")
    submitted_artifact = _submitted_artifact(
        submitted_bytes=submitted_bytes,
        imported_at=imported_at,
        run_id=run_id,
        artifact_locator=submitted_artifact_locator,
    )
    if expected_submitted_sha256 is not None:
        expected_submitted_sha256 = validate_sha256(expected_submitted_sha256)
        if expected_submitted_sha256 != submitted_artifact.content_sha256:
            return _quarantine(
                packet=packet,
                submitted_artifact=submitted_artifact,
                reasons=(ResearchImportReason.RESULT_HASH_MISMATCH,),
                imported_at=imported_at,
                run_id=run_id,
            )
    try:
        raw = json.loads(submitted_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _quarantine(
            packet=packet,
            submitted_artifact=submitted_artifact,
            reasons=(ResearchImportReason.VALIDATION_FAILED,),
            imported_at=imported_at,
            run_id=run_id,
        )
    if not isinstance(raw, dict):
        return _quarantine(
            packet=packet,
            submitted_artifact=submitted_artifact,
            reasons=(ResearchImportReason.VALIDATION_FAILED,),
            imported_at=imported_at,
            run_id=run_id,
        )

    preflight_reasons: list[ResearchImportReason] = []
    if raw.get("schema_version") != packet.schema_version:
        preflight_reasons.append(ResearchImportReason.SCHEMA_VERSION_MISMATCH)
    if raw.get("packet_stage") != packet.packet_stage.value:
        preflight_reasons.append(ResearchImportReason.PACKET_STAGE_MISMATCH)
    if raw.get("packet_id") != packet.record_id:
        preflight_reasons.append(ResearchImportReason.PACKET_ID_MISMATCH)
    if raw.get("packet_sha256") != packet.canonical_sha256:
        preflight_reasons.append(ResearchImportReason.PACKET_HASH_MISMATCH)
    if preflight_reasons:
        return _quarantine(
            packet=packet,
            submitted_artifact=submitted_artifact,
            reasons=tuple(preflight_reasons),
            imported_at=imported_at,
            run_id=run_id,
        )

    try:
        result = ResearchResultEnvelope.model_validate(raw)
    except ValidationError as error:
        message = str(error).lower()
        reason = (
            ResearchImportReason.BLIND_SEMANTIC_LEAK
            if packet.packet_stage == PacketStage.BLIND
            and ("market-derived semantics" in message or "cannot enter blind" in message)
            else ResearchImportReason.VALIDATION_FAILED
        )
        return _quarantine(
            packet=packet,
            submitted_artifact=submitted_artifact,
            reasons=(reason,),
            imported_at=imported_at,
            run_id=run_id,
        )

    # A point-in-time import cannot accept evidence that claims to have been
    # completed, captured, or accessed after the immutable import clock.
    if (
        result.completed_at > imported_at
        or any(item.captured_at > imported_at for item in result.source_artifacts)
        or any(item.accessed_at > imported_at for item in result.evidence)
    ):
        return _quarantine(
            packet=packet,
            submitted_artifact=submitted_artifact,
            reasons=(ResearchImportReason.VALIDATION_FAILED,),
            imported_at=imported_at,
            run_id=run_id,
        )

    missing = tuple(
        artifact.artifact_id
        for artifact in result.source_artifacts
        if artifact.capture_scope != CaptureScope.REFERENCE_ONLY
        and artifact.artifact_id not in source_contents
    )
    if missing:
        return _quarantine(
            packet=packet,
            submitted_artifact=submitted_artifact,
            reasons=(ResearchImportReason.SOURCE_ARTIFACT_MISSING,),
            imported_at=imported_at,
            run_id=run_id,
        )
    declared_ids = {artifact.artifact_id for artifact in result.source_artifacts}
    if set(source_contents) - declared_ids:
        return _quarantine(
            packet=packet,
            submitted_artifact=submitted_artifact,
            reasons=(ResearchImportReason.SOURCE_ARTIFACT_HASH_MISMATCH,),
            imported_at=imported_at,
            run_id=run_id,
        )
    for artifact in result.source_artifacts:
        raw_content = source_contents.get(artifact.artifact_id)
        if artifact.capture_scope == CaptureScope.REFERENCE_ONLY:
            if raw_content is not None:
                return _quarantine(
                    packet=packet,
                    submitted_artifact=submitted_artifact,
                    reasons=(ResearchImportReason.SOURCE_ARTIFACT_HASH_MISMATCH,),
                    imported_at=imported_at,
                    run_id=run_id,
                )
            continue
        assert raw_content is not None
        if not isinstance(raw_content, bytes):
            return _quarantine(
                packet=packet,
                submitted_artifact=submitted_artifact,
                reasons=(ResearchImportReason.SOURCE_ARTIFACT_HASH_MISMATCH,),
                imported_at=imported_at,
                run_id=run_id,
            )
        try:
            actual_hash = _actual_content_hash(artifact, raw_content)
        except ValueError:
            actual_hash = None
        if artifact.content_length_bytes != len(raw_content) or artifact.content_sha256 != actual_hash:
            return _quarantine(
                packet=packet,
                submitted_artifact=submitted_artifact,
                reasons=(ResearchImportReason.SOURCE_ARTIFACT_HASH_MISMATCH,),
                imported_at=imported_at,
                run_id=run_id,
            )
        if artifact.capture_scope == CaptureScope.EXCERPT_ONLY:
            try:
                frozen_text = _normalized_text_bytes(raw_content).decode("utf-8")
            except ValueError:
                frozen_text = ""
            referenced_claims = (
                claim
                for claim in result.evidence
                if claim.source_artifact_id == artifact.artifact_id
            )
            if any(
                not claim.excerpt_context
                or unicodedata.normalize("NFC", claim.excerpt_context)
                .replace("\r\n", "\n")
                .replace("\r", "\n")
                not in frozen_text
                for claim in referenced_claims
            ):
                return _quarantine(
                    packet=packet,
                    submitted_artifact=submitted_artifact,
                    reasons=(ResearchImportReason.SOURCE_ARTIFACT_HASH_MISMATCH,),
                    imported_at=imported_at,
                    run_id=run_id,
                )

    receipt = _receipt(
        packet=packet,
        submitted_artifact=submitted_artifact,
        status=ResearchImportStatus.ACCEPTED,
        reasons=(ResearchImportReason.ACCEPTED,),
        imported_at=imported_at,
        run_id=run_id,
        accepted_result=result,
    )
    return ResearchImportOutcome(submitted_artifact, result, receipt)
