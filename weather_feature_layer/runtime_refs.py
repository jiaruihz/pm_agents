"""Runtime-safe helpers for attaching feature-frame refs to telemetry rows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from weather_feature_layer.contracts import (
    DEFAULT_FEATURE_VERSION_MANIFEST,
    FEATURE_FRAME_SCHEMA_VERSION,
    PIT_PROVENANCE_LIVE_CAPTURE,
)
from weather_feature_layer.store import write_feature_frame_store

import pandas as pd


DEFAULT_DECISION_KEY_COLUMNS = (
    "strategy_instance",
    "strategy_id",
    "city",
    "target_date",
    "decision_snapshot_ts_utc",
    "bracket",
)


def attach_runtime_feature_frame_ref(
    row: dict[str, Any],
    *,
    store_root: str | Path,
    feature_grain: str,
    source_profile_id: str,
    builder_version: str,
    key_columns: tuple[str, ...] = DEFAULT_DECISION_KEY_COLUMNS,
    as_of_ts_utc: str | None = None,
    input_snapshot_id: str | None = None,
    pit_provenance: str = PIT_PROVENANCE_LIVE_CAPTURE,
    strict: bool = False,
) -> dict[str, Any]:
    """Attach a feature-frame ref without changing runner decision behavior."""
    out = dict(row)
    try:
        metadata = {
            "feature_schema_version": FEATURE_FRAME_SCHEMA_VERSION,
            "feature_grain": feature_grain,
            "as_of_ts_utc": as_of_ts_utc or str(row.get("decision_snapshot_ts_utc") or row.get("created_at_utc") or ""),
            "source_profile_id": source_profile_id,
            "feature_version_manifest": dict(DEFAULT_FEATURE_VERSION_MANIFEST),
            "pit_provenance": pit_provenance,
            "builder_version": builder_version,
            "input_snapshot_id": input_snapshot_id or _input_snapshot_id(row),
        }
        frame = pd.DataFrame([{**out, **metadata}])
        frame.attrs["feature_metadata"] = metadata
        stored = write_feature_frame_store(frame, store_root, key_columns=key_columns)
        out["feature_frame_ref"] = stored.row_refs[0]
        out["feature_frame_ref_status"] = "stored"
        out["feature_store_frame_id"] = stored.store_frame_id
    except Exception as exc:  # noqa: BLE001
        if strict:
            raise
        out["feature_frame_ref_status"] = "error"
        out["feature_frame_ref_error"] = f"{type(exc).__name__}: {exc}"
    return out


def _input_snapshot_id(row: dict[str, Any]) -> str:
    for key in (
        "candidate_id",
        "shadow_decision_id",
        "shadow_event_id",
        "signal_id",
        "plan_id",
        "decision_snapshot_ts_utc",
        "created_at_utc",
    ):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return "runtime_telemetry_row"
