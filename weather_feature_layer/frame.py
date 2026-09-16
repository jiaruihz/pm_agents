"""Small helpers for validating feature frame metadata."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from weather_feature_layer.contracts import FEATURE_FRAME_REQUIRED_METADATA, PIT_PROVENANCE_VALUES


@dataclass(frozen=True)
class FeatureFrameMetadata:
    feature_schema_version: str
    feature_grain: str
    as_of_ts_utc: str
    source_profile_id: str
    feature_version_manifest: Mapping[str, str]
    pit_provenance: str
    builder_version: str
    input_snapshot_id: str


def missing_feature_metadata(metadata: Mapping[str, Any]) -> list[str]:
    return [key for key in FEATURE_FRAME_REQUIRED_METADATA if key not in metadata]


def validate_feature_metadata(metadata: Mapping[str, Any]) -> None:
    missing = missing_feature_metadata(metadata)
    if missing:
        raise ValueError(f"missing feature metadata: {', '.join(missing)}")
    if str(metadata.get("pit_provenance")) not in PIT_PROVENANCE_VALUES:
        raise ValueError(f"invalid pit_provenance: {metadata.get('pit_provenance')!r}")
    manifest = metadata.get("feature_version_manifest")
    if not isinstance(manifest, Mapping) or not manifest:
        raise ValueError("feature_version_manifest must be a non-empty mapping")
