"""Fail-closed classification for legacy weather-data migration inputs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


FULL_DECISION = "full_decision"
MODEL_ONLY = "model_only"
LEGACY_SUMMARY_ONLY = "legacy_summary_only"
BLOCKED = "blocked"


@dataclass(frozen=True)
class HistoricalAdaptation:
    classification: str
    source_snapshot_hash: str | None
    physical_ref: dict[str, Any] | None
    blocker: str | None
    identity: str | None
    row: dict[str, Any]


@dataclass(frozen=True)
class HistoricalAdapterAudit:
    scanned: int
    canonical: int
    duplicate: int
    blocked: int


class HistoricalAdapter:
    """Classify legacy records without inventing missing decision lineage."""

    def __init__(self, *, allowed_roots: Iterable[Path] = ()) -> None:
        self.allowed_roots = tuple(
            root.resolve(strict=True) for root in allowed_roots
        )
        self._source_evidence_cache: dict[
            tuple[str, str, int, int, int, int], str | None
        ] = {}

    def register_verified_source(
        self,
        path: Path,
        source_hash: str,
        *,
        device: int,
        inode: int,
        size_bytes: int,
        mtime_ns: int,
    ) -> None:
        """Reuse a stable file hash already computed by the migration scanner."""

        resolved = path.resolve(strict=True)
        if not any(resolved.is_relative_to(root) for root in self.allowed_roots):
            raise ValueError("verified historical source is outside registered roots")
        identity = (device, inode, size_bytes, mtime_ns)
        stat = resolved.stat()
        if (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns) != identity:
            raise ValueError("verified historical source identity changed")
        self._source_evidence_cache[(str(resolved), source_hash, *identity)] = None

    @staticmethod
    def _identity(row: dict[str, Any]) -> str | None:
        for key in ("decision_id", "signal_id", "event_id", "id"):
            value = row.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _physical_evidence_blocker(self, row: dict[str, Any]) -> str | None:
        source_hash = row.get("source_snapshot_hash")
        physical_ref = row.get("physical_ref")
        if (
            not isinstance(source_hash, str)
            or len(source_hash) != 64
            or any(character not in "0123456789abcdef" for character in source_hash.lower())
        ):
            return "missing_source_snapshot_hash"
        if (
            not isinstance(physical_ref, dict)
            or not physical_ref.get("path")
            or not (
                (
                    isinstance(physical_ref.get("line"), int)
                    and int(physical_ref["line"]) > 0
                )
                or (
                    isinstance(physical_ref.get("record_index"), int)
                    and int(physical_ref["record_index"]) >= 0
                )
            )
        ):
            return "missing_physical_ref"
        raw_path = Path(str(physical_ref["path"]))
        if not raw_path.is_absolute():
            return "physical_path_not_absolute"
        if not self.allowed_roots:
            return "unregistered_physical_root"
        try:
            resolved = raw_path.resolve(strict=True)
        except OSError:
            return "physical_path_missing"
        if not resolved.is_file():
            return "physical_path_not_file"
        if not any(resolved.is_relative_to(root) for root in self.allowed_roots):
            return "physical_path_outside_registered_root"
        identity_fields = ("device", "inode", "size_bytes", "mtime_ns")
        if any(
            not isinstance(physical_ref.get(field), int)
            for field in identity_fields
        ):
            return "missing_physical_file_identity"
        declared_identity = tuple(
            int(physical_ref[field]) for field in identity_fields
        )
        cache_key = (str(resolved), source_hash, *declared_identity)
        if cache_key in self._source_evidence_cache:
            return self._source_evidence_cache[cache_key]
        before = resolved.stat()
        actual_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        if actual_identity != declared_identity:
            blocker = "physical_file_identity_mismatch"
            self._source_evidence_cache[cache_key] = blocker
            return blocker
        digest = hashlib.sha256()
        with resolved.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        after = resolved.stat()
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) != actual_identity:
            blocker = "physical_file_changed_during_verification"
        elif digest.hexdigest() != source_hash:
            blocker = "source_snapshot_hash_mismatch"
        else:
            blocker = None
        self._source_evidence_cache[cache_key] = blocker
        return blocker

    def _classification(self, row: dict[str, Any]) -> tuple[str, str | None]:
        physical_blocker = self._physical_evidence_blocker(row)
        if physical_blocker is not None:
            return BLOCKED, physical_blocker
        if row.get("ws_gap"):
            return BLOCKED, "websocket_gap"
        identity = HistoricalAdapter._identity(row)
        if identity is None:
            return BLOCKED, "missing_identity"
        parent = row.get("revision_parent_id")
        revision = row.get("revision")
        if revision not in (None, 0, "0") and not parent:
            return BLOCKED, "missing_revision_parent"
        if all(isinstance(row.get(k), dict) for k in ("event_envelope", "decision_context", "model_output")):
            return FULL_DECISION, None
        if isinstance(row.get("model_output"), dict):
            return MODEL_ONLY, None
        if isinstance(row.get("legacy_summary"), dict) or row.get("summary") is not None:
            return LEGACY_SUMMARY_ONLY, None
        return BLOCKED, "no_supported_legacy_payload"

    def adapt_stream(
        self,
        rows: Iterable[dict[str, Any]],
        *,
        retain_rows: bool = True,
    ) -> tuple[list[HistoricalAdaptation], HistoricalAdapterAudit]:
        """Classify a stream while retaining only compact evidence when requested."""

        seen_hashes: dict[str, str] = {}
        parents: list[str | None] = []
        output: list[HistoricalAdaptation] = []
        canonical = duplicate = blocked = 0
        for row in rows:
            if not isinstance(row, dict):
                item = HistoricalAdaptation(BLOCKED, None, None, "row_is_not_object", None, {})
                blocked += 1
            else:
                identity = self._identity(row)
                classification, blocker = self._classification(row)
                row_hash = hashlib.sha256(
                    json.dumps(
                        row,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                item = HistoricalAdaptation(
                    classification,
                    row.get("source_snapshot_hash"),
                    row.get("physical_ref"),
                    blocker,
                    identity,
                    dict(row) if retain_rows else {},
                )
                if classification != BLOCKED and identity in seen_hashes:
                    if seen_hashes[identity] == row_hash:
                        item = HistoricalAdaptation(BLOCKED, item.source_snapshot_hash, item.physical_ref, "duplicate_identity", identity, item.row)
                        duplicate += 1
                    else:
                        item = HistoricalAdaptation(BLOCKED, item.source_snapshot_hash, item.physical_ref, "conflicting_identity", identity, item.row)
                        blocked += 1
                elif classification == BLOCKED:
                    blocked += 1
                else:
                    seen_hashes[str(identity)] = row_hash
                    canonical += 1
            output.append(item)
            parents.append(
                str(row.get("revision_parent_id"))
                if isinstance(row, dict) and row.get("revision_parent_id")
                else None
            )
        for index, parent in enumerate(parents):
            item = output[index]
            if (
                parent
                and item.classification != BLOCKED
                and parent not in seen_hashes
            ):
                output[index] = HistoricalAdaptation(
                    BLOCKED,
                    item.source_snapshot_hash,
                    item.physical_ref,
                    "unknown_revision_parent",
                    item.identity,
                    item.row,
                )
                canonical -= 1
                blocked += 1
        return output, HistoricalAdapterAudit(len(output), canonical, duplicate, blocked)

    def adapt(self, rows: Iterable[dict[str, Any]]) -> tuple[list[HistoricalAdaptation], HistoricalAdapterAudit]:
        return self.adapt_stream(rows, retain_rows=True)
