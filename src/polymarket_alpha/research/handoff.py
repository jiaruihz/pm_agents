"""Filesystem-only handoff for manually supplied Alpha research.

This module deliberately owns no queue, model client, network transport, or
workflow state.  A caller selects an artifact root and explicit relative
locators; this module makes the resulting packet/result exchange immutable and
verifiable before delegating result validation to :mod:`.importer`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Mapping

from ..contracts import (
    BlindResearchPacket,
    MarketResearchPacket,
    PacketStage,
    ResearchImportReceipt,
    bytes_sha256,
    canonical_json,
)
from ..contracts.base import ensure_utc
from .importer import ResearchImportOutcome, import_research_result


RESEARCH_HANDOFF_VERSION = "p0_unified_offline_handoff_v1"
ResearchPacketValue = BlindResearchPacket | MarketResearchPacket


class HandoffState(StrEnum):
    EXPORTED = "EXPORTED"
    ACCEPTED = "ACCEPTED"
    QUARANTINED = "QUARANTINED"


class HandoffPathError(ValueError):
    """A caller-supplied locator cannot be safely used below the artifact root."""


class HandoffConflictError(ValueError):
    """An immutable locator already contains different bytes."""


@dataclass(frozen=True, slots=True)
class PacketHandoffManifest:
    """Immutable binding between one research packet and its exported bytes."""

    handoff_version: str
    state: HandoffState
    packet_stage: PacketStage
    packet_id: str
    packet_sha256: str
    packet_bytes_sha256: str
    packet_byte_length: int
    packet_locator: str
    manifest_locator: str
    created_at: datetime

    def canonical_bytes(self) -> bytes:
        return canonical_json(
            {
                "handoff_version": self.handoff_version,
                "state": self.state,
                "packet_stage": self.packet_stage,
                "packet_id": self.packet_id,
                "packet_sha256": self.packet_sha256,
                "packet_bytes_sha256": self.packet_bytes_sha256,
                "packet_byte_length": self.packet_byte_length,
                "packet_locator": self.packet_locator,
                "manifest_locator": self.manifest_locator,
                "created_at": self.created_at,
            }
        ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class ResultHandoffReceipt:
    """Typed filesystem handoff fact; importer disposition remains canonical."""

    state: HandoffState
    packet_manifest: PacketHandoffManifest
    result_locator: str
    sealed_submission_locator: str
    submitted_bytes_sha256: str
    submitted_byte_length: int
    import_receipt: ResearchImportReceipt

    def canonical_bytes(self) -> bytes:
        return canonical_json(
            {
                "handoff_version": RESEARCH_HANDOFF_VERSION,
                "state": self.state,
                "packet_manifest": {
                    "packet_stage": self.packet_manifest.packet_stage,
                    "packet_id": self.packet_manifest.packet_id,
                    "packet_sha256": self.packet_manifest.packet_sha256,
                    "packet_bytes_sha256": self.packet_manifest.packet_bytes_sha256,
                    "packet_locator": self.packet_manifest.packet_locator,
                    "manifest_locator": self.packet_manifest.manifest_locator,
                    "created_at": self.packet_manifest.created_at,
                },
                "result_locator": self.result_locator,
                "sealed_submission_locator": self.sealed_submission_locator,
                "submitted_bytes_sha256": self.submitted_bytes_sha256,
                "submitted_byte_length": self.submitted_byte_length,
                "import_receipt": self.import_receipt,
            }
        ).encode("utf-8")


def _artifact_root(root: Path) -> Path:
    root = Path(root)
    if not root.is_absolute():
        raise HandoffPathError("artifact_root must be an explicit absolute path")
    if root.is_symlink() or not root.exists() or not root.is_dir():
        raise HandoffPathError("artifact_root must be an existing non-symlink directory")
    return root


def _relative_locator(locator: str) -> PurePosixPath:
    if not isinstance(locator, str) or not locator.strip() or "\\" in locator:
        raise HandoffPathError("locator must be a non-empty POSIX relative path")
    path = PurePosixPath(locator)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise HandoffPathError("locator must not be absolute or traverse directories")
    return path


def _open_parent(root: Path, locator: str, *, create_parents: bool) -> tuple[int, str]:
    """Resolve every parent from an owned dirfd without following symlinks."""

    relative = _relative_locator(locator)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    try:
        current_fd = os.open(root, directory_flags)
    except OSError as error:
        raise HandoffPathError("artifact_root cannot be opened safely") from error
    try:
        for part in relative.parts[:-1]:
            try:
                next_fd = os.open(part, directory_flags, dir_fd=current_fd)
            except FileNotFoundError:
                if not create_parents:
                    raise HandoffPathError("locator parent does not exist")
                try:
                    os.mkdir(part, mode=0o700, dir_fd=current_fd)
                except FileExistsError:
                    pass
                try:
                    next_fd = os.open(part, directory_flags, dir_fd=current_fd)
                except OSError as error:
                    raise HandoffPathError("locator parent must be a real directory") from error
            except OSError as error:
                raise HandoffPathError("locator parent must be a real directory") from error
            os.close(current_fd)
            current_fd = next_fd
        return current_fd, relative.name
    except Exception:
        os.close(current_fd)
        raise


def _read_regular_at(parent_fd: int, name: str) -> bytes:
    try:
        fd = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
    except OSError as error:
        raise HandoffPathError("allowlisted file is absent or unsafe") from error
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise HandoffPathError("allowlisted path must be a regular file")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            return handle.read()
    finally:
        if fd >= 0:
            os.close(fd)


def _write_immutable(root: Path, locator: str, data: bytes) -> None:
    parent_fd, name = _open_parent(root, locator, create_parents=True)
    try:
        try:
            fd = os.open(
                name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent_fd,
            )
        except FileExistsError:
            try:
                existing = _read_regular_at(parent_fd, name)
            except HandoffPathError as error:
                raise HandoffConflictError(f"immutable locator conflict: {locator}") from error
            if existing != data:
                raise HandoffConflictError(f"immutable locator conflict: {locator}")
            return
        except OSError as error:
            raise HandoffPathError("immutable locator cannot be opened safely") from error
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(parent_fd)


def _read_allowed(root: Path, locator: str) -> bytes:
    parent_fd, name = _open_parent(root, locator, create_parents=False)
    try:
        return _read_regular_at(parent_fd, name)
    finally:
        os.close(parent_fd)


def _packet_bytes(packet: ResearchPacketValue) -> bytes:
    return canonical_json(packet).encode("utf-8")


def _validate_manifest(packet: ResearchPacketValue, manifest: PacketHandoffManifest) -> None:
    if (
        manifest.handoff_version != RESEARCH_HANDOFF_VERSION
        or manifest.state != HandoffState.EXPORTED
        or manifest.packet_stage != packet.packet_stage
        or manifest.packet_id != packet.record_id
        or manifest.packet_sha256 != packet.canonical_sha256
    ):
        raise ValueError("packet manifest does not bind the supplied packet")


def export_research_packet(
    *,
    artifact_root: Path,
    packet: ResearchPacketValue,
    packet_locator: str,
    manifest_locator: str,
    created_at: datetime,
) -> PacketHandoffManifest:
    """Export canonical packet bytes and a separately immutable manifest.

    Retrying the exact call is byte-identical.  Reusing either locator with
    non-identical bytes fails closed.
    """

    root = _artifact_root(artifact_root)
    created_at = ensure_utc(created_at)
    packet_bytes = _packet_bytes(packet)
    manifest = PacketHandoffManifest(
        handoff_version=RESEARCH_HANDOFF_VERSION,
        state=HandoffState.EXPORTED,
        packet_stage=packet.packet_stage,
        packet_id=packet.record_id,
        packet_sha256=packet.canonical_sha256,
        packet_bytes_sha256=bytes_sha256(packet_bytes),
        packet_byte_length=len(packet_bytes),
        packet_locator=str(_relative_locator(packet_locator)),
        manifest_locator=str(_relative_locator(manifest_locator)),
        created_at=created_at,
    )
    if manifest.packet_locator == manifest.manifest_locator:
        raise HandoffPathError("packet and manifest locators must differ")
    _write_immutable(root, manifest.packet_locator, packet_bytes)
    _write_immutable(root, manifest.manifest_locator, manifest.canonical_bytes())
    return manifest


def ingest_research_result(
    *,
    artifact_root: Path,
    packet: ResearchPacketValue,
    packet_manifest: PacketHandoffManifest,
    allowed_result_locator: str,
    source_contents: Mapping[str, bytes],
    imported_at: datetime,
    run_id: str,
) -> tuple[ResearchImportOutcome, ResultHandoffReceipt]:
    """Read exactly one allowlisted result file and run the existing importer.

    A packet/manifest mismatch is a caller error, not a quarantinable research
    result: it must never advance a later stage.
    """

    root = _artifact_root(artifact_root)
    _validate_manifest(packet, packet_manifest)
    packet_bytes = _read_allowed(root, packet_manifest.packet_locator)
    if (
        bytes_sha256(packet_bytes) != packet_manifest.packet_bytes_sha256
        or len(packet_bytes) != packet_manifest.packet_byte_length
        or packet_bytes != _packet_bytes(packet)
    ):
        raise HandoffConflictError("exported packet bytes no longer match manifest")
    locator = str(_relative_locator(allowed_result_locator))
    submitted_bytes = _read_allowed(root, locator)
    submitted_sha256 = bytes_sha256(submitted_bytes)
    sealed_submission_locator = f"_sealed/submissions/{submitted_sha256}.json"
    _write_immutable(root, sealed_submission_locator, submitted_bytes)
    outcome = import_research_result(
        packet=packet,
        submitted_bytes=submitted_bytes,
        source_contents=source_contents,
        imported_at=imported_at,
        run_id=run_id,
        submitted_artifact_locator=locator,
        expected_submitted_sha256=submitted_sha256,
    )
    state = HandoffState.ACCEPTED if outcome.result is not None else HandoffState.QUARANTINED
    if state == HandoffState.ACCEPTED:
        _write_immutable(
            root,
            f"_sealed/accepted/{packet.canonical_sha256}.json",
            submitted_bytes,
        )
    return outcome, ResultHandoffReceipt(
        state=state,
        packet_manifest=packet_manifest,
        result_locator=locator,
        sealed_submission_locator=sealed_submission_locator,
        submitted_bytes_sha256=submitted_sha256,
        submitted_byte_length=len(submitted_bytes),
        import_receipt=outcome.receipt,
    )


def seal_result_handoff(
    *, artifact_root: Path, receipt: ResultHandoffReceipt, receipt_locator: str
) -> None:
    """Persist either ACCEPTED or QUARANTINED handoff facts immutably."""

    _write_immutable(_artifact_root(artifact_root), receipt_locator, receipt.canonical_bytes())
