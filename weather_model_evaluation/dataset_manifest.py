"""Immutable, content-addressed datasets used by weather evaluation/replay.

This module deliberately records *what was read*, rather than merely naming a
database or a catalogue.  A manifest is a sealed research input, not a live
database pointer.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .contracts import parse_utc, stable_json, stable_sha256


DATASET_MANIFEST_SCHEMA_VERSION = "weather_evaluation_dataset_manifest_v1"
_SPLITS = ("train", "validation", "test", "frozen_forward")


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value.lower()
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normal_rows(rows: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    normalized = tuple(dict(row) for row in rows)
    identities: set[str] = set()
    for row in normalized:
        identity = row.get("row_id") or row.get("row_identity")
        if not identity:
            raise ValueError("dataset rows require row_id or row_identity")
        if str(identity) in identities:
            raise ValueError(f"duplicate dataset row identity: {identity}")
        identities.add(str(identity))
    return tuple(sorted(normalized, key=lambda row: str(row.get("row_id") or row["row_identity"])))


def _row_identity(row: Mapping[str, Any]) -> str:
    value = row.get("row_id") or row.get("row_identity")
    if not value:
        raise ValueError("dataset rows require row_id or row_identity")
    return str(value)


def _availability_clocks(
    value: Any, *, prefix: str = ""
) -> list[tuple[str, Any]]:
    clocks: list[tuple[str, Any]] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            field = f"{prefix}.{key}" if prefix else str(key)
            lowered = str(key).lower()
            if item not in (None, "") and not isinstance(item, (Mapping, list, tuple)):
                if "available_at" in lowered or "detected_at" in lowered:
                    clocks.append((field, item))
            if isinstance(item, (Mapping, list, tuple)):
                clocks.extend(_availability_clocks(item, prefix=field))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            if isinstance(item, (Mapping, list, tuple)):
                field = f"{prefix}[{index}]" if prefix else f"[{index}]"
                clocks.extend(_availability_clocks(item, prefix=field))
    return clocks


def _validate_row_source_refs(
    rows: Sequence[Mapping[str, Any]],
    row_source_refs: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    source_ids: set[str],
    label_available_at_field: str,
) -> dict[str, tuple[dict[str, Any], ...]]:
    """Validate the per-decision evidence index persisted with a dataset.

    A source snapshot hash alone cannot prove which records were visible for a
    particular decision.  The caller supplies the already PIT-checked refs;
    this layer makes their membership and identity immutable in the manifest.
    """
    expected = {_row_identity(row) for row in rows}
    actual = {str(key) for key in row_source_refs}
    if actual != expected:
        raise ValueError("row_source_refs must cover every dataset row exactly once")
    normalized: dict[str, tuple[dict[str, Any], ...]] = {}
    decisions: dict[str, Any] = {}
    for row in rows:
        row_id = _row_identity(row)
        decision = row.get("decision_ts_utc")
        if decision in (None, ""):
            raise ValueError(f"dataset row {row_id} requires decision_ts_utc")
        decisions[row_id] = parse_utc(str(decision))
        for field, value in _availability_clocks(row):
            if field.rsplit(".", 1)[-1] == label_available_at_field:
                continue
            if parse_utc(str(value)) > decisions[row_id]:
                raise ValueError(
                    f"PIT violation: row[{row_id}].{field} is after decision_ts_utc"
                )
    for row_id in sorted(expected):
        refs = row_source_refs[row_id]
        if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes)) or not refs:
            raise ValueError(f"dataset row {row_id} requires non-empty source refs")
        seen: set[tuple[str, str]] = set()
        copied: list[dict[str, Any]] = []
        for ref in refs:
            if not isinstance(ref, Mapping):
                raise ValueError("each source ref must be an object")
            source_id = str(ref.get("source_id") or "")
            source_row_id = str(ref.get("row_id") or ref.get("row_identity") or "")
            if source_id not in source_ids or not source_row_id:
                raise ValueError("source refs require a known source_id and row_id")
            clocks = ref.get("clocks")
            if not isinstance(clocks, Mapping) or not clocks:
                raise ValueError("source refs require non-empty resolved clocks")
            for clock_name, clock_value in clocks.items():
                if clock_value in (None, "") or (
                    "available_at" not in str(clock_name).lower()
                    and "detected_at" not in str(clock_name).lower()
                ):
                    raise ValueError("source ref clocks must be availability/detection timestamps")
                if parse_utc(str(clock_value)) > decisions[row_id]:
                    raise ValueError(
                        f"PIT violation: source[{source_id}/{source_row_id}].{clock_name} is after decision_ts_utc"
                    )
            key = (source_id, source_row_id)
            if key in seen:
                raise ValueError(f"duplicate source ref for dataset row {row_id}: {key}")
            seen.add(key)
            copied.append(dict(ref))
        normalized[row_id] = tuple(sorted(copied, key=stable_json))
    return normalized


@dataclass(frozen=True)
class DataSourceSnapshot:
    """Exact accounting for one source consumed by a frozen dataset."""

    source_id: str
    row_count: int
    content_sha256: str
    available_at_field: str
    cutoff_utc: str
    row_identity_sha256: str | None = None
    source_path: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.source_id
            or self.row_count < 0
            or not _is_sha256(self.content_sha256)
        ):
            raise ValueError("source_id, non-negative row_count and SHA-256 are required")
        if not self.available_at_field or not self.cutoff_utc:
            raise ValueError("source availability field and cutoff are required")
        parse_utc(self.cutoff_utc)
        if self.row_identity_sha256 is not None and not _is_sha256(
            self.row_identity_sha256
        ):
            raise ValueError("row_identity_sha256 must be SHA-256")

    @classmethod
    def from_rows(
        cls, source_id: str, rows: Iterable[Mapping[str, Any]], *, available_at_field: str,
        cutoff_utc: str, source_path: Path | None = None,
    ) -> "DataSourceSnapshot":
        materialized = _normal_rows(rows)
        for row in materialized:
            if available_at_field not in row or row[available_at_field] in (None, ""):
                raise ValueError(f"source row lacks availability field: {available_at_field}")
            if parse_utc(str(row[available_at_field])) > parse_utc(cutoff_utc):
                raise ValueError("future source row is not allowed in a snapshot")
        identities = [row.get("row_id") or row.get("row_identity") for row in materialized]
        return cls(
            source_id=source_id, row_count=len(materialized), content_sha256=stable_sha256(materialized),
            row_identity_sha256=stable_sha256(identities), available_at_field=available_at_field,
            cutoff_utc=cutoff_utc, source_path=str(source_path.resolve()) if source_path else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def canonical_db_identity(path: Path, *, schema_version: str) -> dict[str, Any]:
    resolved = Path(path).resolve(strict=True)
    stat = resolved.stat()
    return {
        "realpath": str(resolved), "device": stat.st_dev, "inode": stat.st_ino,
        "schema_version": schema_version, "sha256": sha256_file(resolved),
    }


def validate_target_date_split(split_rows: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
    """Require non-overlapping, chronological target-date blocks."""

    unknown = set(split_rows) - set(_SPLITS)
    if unknown:
        raise ValueError(f"unsupported dataset splits: {sorted(unknown)}")
    owners: dict[str, str] = {}
    split_dates: dict[str, set[date]] = {}
    for split, rows in split_rows.items():
        identities: set[str] = set()
        for row in rows:
            identity = row.get("row_id") or row.get("row_identity")
            if not identity:
                raise ValueError("split rows require row_id or row_identity")
            if str(identity) in identities:
                raise ValueError(
                    f"duplicate row identity within split {split}: {identity}"
                )
            identities.add(str(identity))
            target_date = row.get("target_date")
            if not target_date:
                raise ValueError("split rows require target_date")
            target_date_text = str(target_date)
            try:
                parsed_date = date.fromisoformat(target_date_text)
            except ValueError as exc:
                raise ValueError(f"invalid target_date: {target_date_text}") from exc
            split_dates.setdefault(split, set()).add(parsed_date)
            old = owners.setdefault(target_date_text, split)
            if old != split:
                raise ValueError(
                    f"target_date belongs to both {old} and {split}: {target_date_text}"
                )
    prior_max: date | None = None
    for split in _SPLITS:
        dates = split_dates.get(split, set())
        if not dates:
            continue
        if prior_max is not None and min(dates) <= prior_max:
            raise ValueError(
                f"target_date split order is not chronological at {split}"
            )
        prior_max = max(dates)


@dataclass(frozen=True)
class DatasetManifest:
    dataset_hash: str
    rows_hash: str
    row_identity_hash: str
    split_manifest_hash: str
    db_identity: Mapping[str, Any]
    catalog_build_id: str
    code_sha: str
    source_snapshots: tuple[DataSourceSnapshot, ...]
    measurement_namespace: str
    label_value_field: str
    label_available_at_field: str
    cutoff_utc: str
    split_membership: Mapping[str, tuple[str, ...]]
    row_source_refs: Mapping[str, tuple[Mapping[str, Any], ...]]
    schema_version: str = DATASET_MANIFEST_SCHEMA_VERSION
    sealed: bool = True
    immutable: bool = True

    @classmethod
    def build(
        cls, *, rows: Iterable[Mapping[str, Any]], db_path: Path, db_schema_version: str,
        catalog_build_id: str, code_sha: str, source_snapshots: Sequence[DataSourceSnapshot],
        measurement_namespace: str, label_value_field: str,
        label_available_at_field: str, cutoff_utc: str,
        splits: Mapping[str, Sequence[Mapping[str, Any]]],
        row_source_refs: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> "DatasetManifest":
        materialized = _normal_rows(rows)
        if not catalog_build_id or not code_sha or not label_value_field or not label_available_at_field:
            raise ValueError("catalog build, code SHA, and explicit label fields are required")
        if measurement_namespace not in {"tmax", "tmin"}:
            raise ValueError("sealed weather datasets require explicit tmax/tmin namespace")
        parse_utc(cutoff_utc)
        validate_target_date_split(splits)
        for row in materialized:
            if label_value_field not in row or label_available_at_field not in row:
                raise ValueError("every dataset row requires explicit label value/availability fields")
            if row.get("measurement_namespace") != measurement_namespace:
                raise ValueError("dataset row measurement namespace mismatch")
            value = row[label_available_at_field]
            if value in (None, "") or parse_utc(str(value)) > parse_utc(cutoff_utc):
                raise ValueError("future or unavailable label fails closed")
        memberships = {
            name: tuple(sorted(str(row.get("row_id") or row["row_identity"]) for row in rows))
            for name, rows in sorted(splits.items())
        }
        row_ids = [row.get("row_id") or row["row_identity"] for row in materialized]
        known_ids = {str(value) for value in row_ids}
        assigned_ids = [item for members in memberships.values() for item in members]
        unknown_ids = sorted(set(assigned_ids) - known_ids)
        if unknown_ids:
            raise ValueError(f"split membership contains unknown row identities: {unknown_ids}")
        duplicate_ids = sorted(
            item for item, count in Counter(assigned_ids).items() if count > 1
        )
        if duplicate_ids:
            raise ValueError(f"dataset rows assigned to multiple splits: {duplicate_ids}")
        unassigned_ids = sorted(known_ids - set(assigned_ids))
        if unassigned_ids:
            raise ValueError(f"dataset rows missing split membership: {unassigned_ids}")
        source_ids = [snapshot.source_id for snapshot in source_snapshots]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source snapshots must have distinct source_id values")
        for snapshot in source_snapshots:
            if parse_utc(snapshot.cutoff_utc) > parse_utc(cutoff_utc):
                raise ValueError("source snapshot cutoff exceeds dataset cutoff")
        normalized_refs = _validate_row_source_refs(
            materialized,
            row_source_refs,
            source_ids=set(source_ids),
            label_available_at_field=label_available_at_field,
        )
        split_manifest_hash = stable_sha256(memberships)
        body = {
            "rows_hash": stable_sha256(materialized), "row_identity_hash": stable_sha256(row_ids),
            "split_manifest_hash": split_manifest_hash,
            "db_identity": canonical_db_identity(db_path, schema_version=db_schema_version),
            "catalog_build_id": catalog_build_id, "code_sha": code_sha,
            "source_snapshots": [item.to_dict() for item in source_snapshots],
            "measurement_namespace": measurement_namespace,
            "label_value_field": label_value_field, "label_available_at_field": label_available_at_field,
            "cutoff_utc": cutoff_utc, "split_membership": memberships,
            "row_source_refs": normalized_refs,
        }
        return cls(
            dataset_hash=stable_sha256(body), rows_hash=body["rows_hash"],
            row_identity_hash=body["row_identity_hash"],
            split_manifest_hash=split_manifest_hash, db_identity=body["db_identity"],
            catalog_build_id=catalog_build_id, code_sha=code_sha,
            source_snapshots=tuple(source_snapshots),
            measurement_namespace=measurement_namespace,
            label_value_field=label_value_field,
            label_available_at_field=label_available_at_field, cutoff_utc=cutoff_utc,
            split_membership=memberships, row_source_refs=normalized_refs,
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "dataset_hash": self.dataset_hash, "rows_hash": self.rows_hash,
            "row_identity_hash": self.row_identity_hash,
            "split_manifest_hash": self.split_manifest_hash,
            "db_identity": dict(self.db_identity),
            "catalog_build_id": self.catalog_build_id, "code_sha": self.code_sha,
            "measurement_namespace": self.measurement_namespace,
            "label_value_field": self.label_value_field,
            "label_available_at_field": self.label_available_at_field, "cutoff_utc": self.cutoff_utc,
            "split_membership": dict(self.split_membership), "schema_version": self.schema_version,
            "sealed": self.sealed, "immutable": self.immutable,
        }
        result["row_source_refs"] = {
            row_id: [dict(ref) for ref in refs]
            for row_id, refs in self.row_source_refs.items()
        }
        result["source_snapshots"] = [item.to_dict() for item in self.source_snapshots]
        return result

    def write_atomic(self, path: Path) -> Path:
        path = Path(path)
        if path.exists():
            raise FileExistsError(f"refusing to overwrite sealed manifest: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(self.to_dict(), handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
            # link(2) is create-only: unlike replace it cannot race into
            # overwriting somebody else's already sealed manifest.
            os.link(temporary, path)
            temporary.unlink()
            path.chmod(0o444)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return path
