"""File-backed storage helpers for point-in-time weather feature frames."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from weather_feature_layer.frame import validate_feature_metadata


DEFAULT_KEY_COLUMNS = ("city", "target_date", "decision_snapshot_ts_utc")


@dataclass(frozen=True)
class StoredFeatureFrame:
    store_frame_id: str
    frame_ref: dict[str, Any]
    row_refs: list[dict[str, Any]]
    frame_dir: Path
    rows_path: Path
    index_path: Path
    manifest_path: Path
    row_count: int


def feature_frame_ref_for_row(
    row: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any] | None = None,
    key_columns: Sequence[str] = DEFAULT_KEY_COLUMNS,
    store_frame_id: str | None = None,
) -> dict[str, Any]:
    resolved_metadata = _metadata_from_row(row, metadata)
    validate_feature_metadata(resolved_metadata)
    key_values = {key: _json_ready(row.get(key)) for key in key_columns}
    payload = {
        "metadata": _metadata_for_ref(resolved_metadata),
        "feature_row_key": key_values,
    }
    feature_row_id = _sha256(payload)
    ref = {
        **_metadata_for_ref(resolved_metadata),
        "feature_row_key": key_values,
        "feature_row_id": feature_row_id,
    }
    if store_frame_id is not None:
        ref["store_frame_id"] = store_frame_id
    return ref


def write_feature_frame_store(
    frame: pd.DataFrame,
    store_root: str | Path,
    *,
    key_columns: Sequence[str] = DEFAULT_KEY_COLUMNS,
) -> StoredFeatureFrame:
    if frame.empty:
        raise ValueError("cannot store an empty feature frame")
    metadata = _metadata_from_frame(frame)
    validate_feature_metadata(metadata)
    rows = [_json_ready(row) for row in frame.to_dict("records")]
    row_refs_without_store = [
        feature_frame_ref_for_row(row, metadata=metadata, key_columns=key_columns)
        for row in rows
    ]
    frame_payload = {
        "feature_metadata": _metadata_for_ref(metadata),
        "key_columns": list(key_columns),
        "feature_row_ids": [ref["feature_row_id"] for ref in row_refs_without_store],
    }
    store_frame_id = _sha256(frame_payload)[:24]
    row_refs = [
        {**ref, "store_frame_id": store_frame_id}
        for ref in row_refs_without_store
    ]
    frame_ref = {
        **_metadata_for_ref(metadata),
        "store_frame_id": store_frame_id,
        "key_columns": list(key_columns),
        "row_count": len(rows),
    }

    frame_dir = Path(store_root) / str(metadata["feature_schema_version"]) / str(metadata["feature_grain"]) / store_frame_id
    frame_dir.mkdir(parents=True, exist_ok=True)
    rows_path = frame_dir / "rows.jsonl"
    index_path = frame_dir / "index.json"
    manifest_path = frame_dir / "manifest.json"
    index: dict[str, dict[str, Any]] = {}
    with rows_path.open("w", encoding="utf-8") as handle:
        for row, ref in zip(rows, row_refs, strict=True):
            stored_row = {**row, "feature_frame_ref": ref}
            handle.write(json.dumps(stored_row, sort_keys=True, ensure_ascii=False) + "\n")
            index[str(ref["feature_row_id"])] = stored_row
    index_path.write_text(json.dumps(index, sort_keys=True, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "frame_ref": frame_ref,
        "row_refs": row_refs,
        "rows_path": str(rows_path.relative_to(Path(store_root))),
        "index_path": str(index_path.relative_to(Path(store_root))),
    }
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return StoredFeatureFrame(
        store_frame_id=store_frame_id,
        frame_ref=frame_ref,
        row_refs=row_refs,
        frame_dir=frame_dir,
        rows_path=rows_path,
        index_path=index_path,
        manifest_path=manifest_path,
        row_count=len(rows),
    )


def load_feature_row_by_ref(store_root: str | Path, feature_frame_ref: Mapping[str, Any]) -> dict[str, Any]:
    store_frame_id = str(feature_frame_ref.get("store_frame_id") or "")
    feature_row_id = str(feature_frame_ref.get("feature_row_id") or "")
    feature_schema_version = str(feature_frame_ref.get("feature_schema_version") or "")
    feature_grain = str(feature_frame_ref.get("feature_grain") or "")
    if not store_frame_id or not feature_row_id or not feature_schema_version or not feature_grain:
        raise ValueError("feature_frame_ref is missing store_frame_id, feature_row_id, schema, or grain")
    index_path = Path(store_root) / feature_schema_version / feature_grain / store_frame_id / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    try:
        return dict(index[feature_row_id])
    except KeyError as exc:
        raise KeyError(f"feature_row_id not found: {feature_row_id}") from exc


def _metadata_from_frame(frame: pd.DataFrame) -> dict[str, Any]:
    attr_metadata = frame.attrs.get("feature_metadata")
    if isinstance(attr_metadata, Mapping):
        return _json_ready(dict(attr_metadata))
    first = frame.iloc[0].to_dict()
    return _metadata_from_row(first)


def _metadata_from_row(row: Mapping[str, Any], metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    source = dict(metadata) if metadata is not None else dict(row)
    out: dict[str, Any] = {}
    for key in (
        "feature_schema_version",
        "feature_grain",
        "as_of_ts_utc",
        "source_profile_id",
        "feature_version_manifest",
        "pit_provenance",
        "builder_version",
        "input_snapshot_id",
    ):
        value = source.get(key)
        if key == "feature_version_manifest" and isinstance(value, str):
            value = json.loads(value)
        out[key] = _json_ready(value)
    return out


def _metadata_for_ref(metadata: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "feature_schema_version": metadata["feature_schema_version"],
        "feature_grain": metadata["feature_grain"],
        "feature_version_manifest": _json_ready(metadata["feature_version_manifest"]),
        "as_of_ts_utc": metadata["as_of_ts_utc"],
        "source_profile_id": metadata["source_profile_id"],
        "pit_provenance": metadata["pit_provenance"],
        "builder_version": metadata["builder_version"],
        "input_snapshot_id": metadata["input_snapshot_id"],
    }


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(val) for key, val in value.items()}
    if isinstance(value, list | tuple):
        return [_json_ready(item) for item in value]
    if pd.isna(value) if not isinstance(value, (list, tuple, Mapping)) else False:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _sha256(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(_json_ready(payload), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
