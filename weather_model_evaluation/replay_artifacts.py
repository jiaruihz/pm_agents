"""Identity binding and verification for replay artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .contracts import stable_sha256
from .dataset_manifest import sha256_file


REPLAY_ARTIFACT_VERIFIER_SCHEMA_VERSION = "weather_replay_artifact_verifier_v1"
REPLAY_ARTIFACT_VERIFIER_BUILD_ID = hashlib.sha256(
    json.dumps(
        {
            "schema_version": REPLAY_ARTIFACT_VERIFIER_SCHEMA_VERSION,
            "file_evidence": "absolute_managed_path_plus_sha256",
            "aggregate_statuses": [
                "ready",
                "legacy_summary_only",
                "blocked_evidence",
                "blocked_missing_artifact",
                "blocked_hash_mismatch",
                "blocked_unverifiable_artifact",
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value.lower()
    )


@dataclass(frozen=True)
class ReplayRunIdentity:
    dataset_hash: str
    split_hash: str
    catalog_build_id: str
    code_sha: str
    source_snapshot_hashes: tuple[str, ...]
    identity_hash: str = ""

    def __post_init__(self) -> None:
        if not all((self.dataset_hash, self.split_hash, self.catalog_build_id, self.code_sha)):
            raise ValueError("replay identity requires dataset, split, catalog and code identities")
        if not self.source_snapshot_hashes:
            raise ValueError("replay identity requires source snapshot hashes")
        object.__setattr__(
            self,
            "source_snapshot_hashes",
            tuple(sorted(str(value) for value in self.source_snapshot_hashes)),
        )
        if any(not _is_sha256(value) for value in self.source_snapshot_hashes):
            raise ValueError("replay source snapshot identities must be SHA-256")
        expected = stable_sha256({key: value for key, value in asdict(self).items() if key != "identity_hash"})
        if self.identity_hash and self.identity_hash != expected:
            raise ValueError("replay identity hash mismatch")
        object.__setattr__(self, "identity_hash", expected)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def artifact_verification_status(
    artifacts: Iterable[Mapping[str, Any]], *, managed_roots: Sequence[Path],
    catalog_build_id: str | None = None,
) -> dict[str, Any]:
    """Verify files without treating aggregate/source hashes as file evidence."""
    roots = tuple(Path(root).resolve() for root in managed_roots)
    if not roots:
        raise ValueError("artifact verification requires at least one managed root")
    verified: list[dict[str, Any]] = []
    for artifact in artifacts:
        path_value = artifact.get("path")
        expected = artifact.get("sha256")
        if artifact.get("legacy") or not path_value:
            verified.append({"artifact": dict(artifact), "status": "legacy_unbound"})
            continue
        if not isinstance(expected, str) or not _is_sha256(expected):
            verified.append({"artifact": dict(artifact), "status": "unverifiable"})
            continue
        raw_path = Path(str(path_value))
        if not raw_path.is_absolute():
            verified.append({"artifact": dict(artifact), "status": "unverifiable"})
            continue
        path = raw_path.resolve()
        if not any(path.is_relative_to(root) for root in roots):
            verified.append({"artifact": dict(artifact), "status": "unverifiable"})
        elif not path.is_file():
            verified.append({"artifact": dict(artifact), "status": "missing"})
        elif sha256_file(path) != expected:
            verified.append({"artifact": dict(artifact), "status": "hash_mismatch"})
        else:
            verified.append({"artifact": dict(artifact), "status": "verified"})
    statuses = [item["status"] for item in verified]
    if statuses and all(status == "verified" for status in statuses):
        aggregate = "ready"
    elif "hash_mismatch" in statuses:
        aggregate = "blocked_hash_mismatch"
    elif "missing" in statuses or not statuses:
        aggregate = "blocked_missing_artifact"
    elif "unverifiable" in statuses:
        aggregate = "blocked_unverifiable_artifact"
    elif statuses and all(status == "legacy_unbound" for status in statuses):
        aggregate = "legacy_summary_only"
    else:
        aggregate = "blocked_evidence"
    return {
        "schema_version": REPLAY_ARTIFACT_VERIFIER_SCHEMA_VERSION,
        "catalog_build_id": catalog_build_id,
        "verifier_build_id": REPLAY_ARTIFACT_VERIFIER_BUILD_ID,
        "status": aggregate,
        "checks": verified,
        # Compatibility alias for callers that predate the catalog verifier.
        "artifacts": verified,
    }


def verify_replay_artifacts(
    artifacts: Iterable[Mapping[str, Any]], *, managed_roots: Sequence[Path],
    catalog_build_id: str | None = None,
) -> dict[str, Any]:
    return artifact_verification_status(
        artifacts,
        managed_roots=managed_roots,
        catalog_build_id=catalog_build_id,
    )
