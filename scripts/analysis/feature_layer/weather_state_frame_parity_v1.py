"""Offline parity check for weather_feature_layer state frames.

This harness is intentionally read-only. It compares the shared, strategy-neutral
state fields emitted by ``weather_feature_layer.build_weather_state_frame`` with
the current tmax live-candidate private state builder on the same snapshot and
observation-cache inputs.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ops import tmax_distribution_edge_live_candidate_v1 as tmax_live
from scripts.analysis.versioned_artifact_output import (
    prepare_new_run_output,
    resolve_run_output,
)
from weather_data_feed.observation_cache import index_observation_cache
from weather_data_feed.observation_cache import parse_utc
from weather_feature_layer.builders import build_weather_state_frame_with_audits
from weather_feature_layer.contracts import PIT_PROVENANCE_ARCHIVE_RECONSTRUCTION


ARTIFACT_FAMILY = "weather_feature_layer_state_parity_v1"

KEY_FIELDS = ["city", "target_date"]
NUMERIC_FIELDS = {
    "decision_hour_local",
    "forecast_max_f",
    "forecast_max_native",
    "forecast_peak_hour_local",
    "forecast_peak_delta_hours_local",
    "forecast_peak_hour_spread",
    "forecast_gap_to_running_native",
    "running_native",
    "current_native",
    "decline_native",
    "running_value",
    "current_temp_c",
    "running_max_c",
    "tmpf_now",
    "dwpf_now",
    "dewpoint_depression_f",
    "relative_humidity_pct",
    "wind_speed_kt",
    "sky_cover_code",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "minutes_since_running_max",
    "obs_age_min",
}
TEXT_FIELDS = {
    "decision_snapshot_ts_utc",
    "unit",
    "timezone",
    "forecast_source",
    "running_max_obs_utc",
    "obs_source",
    "city_family",
    "solar_window",
    "day_regime",
    "moisture_cloud_regime",
    "wind_regime",
    "running_max_state",
    "intraday_state",
    "composite_regime",
}
SHARED_FIELDS = sorted(NUMERIC_FIELDS | TEXT_FIELDS)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def finite_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def normalize_field_text(field: str, value: Any) -> str:
    if field.endswith("_utc"):
        dt = parse_utc(value)
        if dt is not None:
            return dt.isoformat(timespec="seconds").replace("+00:00", "Z")
    return normalize_text(value)


def numeric_equal(left: Any, right: Any, *, atol: float) -> bool:
    left_value = finite_float(left)
    right_value = finite_float(right)
    if left_value is None and right_value is None:
        return True
    if left_value is None or right_value is None:
        return False
    return abs(left_value - right_value) <= atol


def row_key(row: pd.Series) -> tuple[str, str]:
    return tuple(normalize_text(row.get(field)) for field in KEY_FIELDS)  # type: ignore[return-value]


def compare_frames(feature_df: pd.DataFrame, legacy_df: pd.DataFrame, *, atol: float) -> pd.DataFrame:
    feature_by_key = {row_key(row): row for _, row in feature_df.iterrows()}
    legacy_by_key = {row_key(row): row for _, row in legacy_df.iterrows()}
    common_keys = sorted(set(feature_by_key) & set(legacy_by_key))
    rows: list[dict[str, Any]] = []
    for key in common_keys:
        feature_row = feature_by_key[key]
        legacy_row = legacy_by_key[key]
        for field in SHARED_FIELDS:
            feature_value = feature_row.get(field)
            legacy_value = legacy_row.get(field)
            if field in NUMERIC_FIELDS:
                match = numeric_equal(feature_value, legacy_value, atol=atol)
                diff = None
                if not match and finite_float(feature_value) is not None and finite_float(legacy_value) is not None:
                    diff = finite_float(feature_value) - finite_float(legacy_value)  # type: ignore[operator]
                status = "match" if match else "numeric_mismatch"
            else:
                feature_text = normalize_field_text(field, feature_value)
                legacy_text = normalize_field_text(field, legacy_value)
                match = feature_text == legacy_text
                diff = None
                status = "match" if match else "text_mismatch"
                feature_value = feature_text
                legacy_value = legacy_text
            rows.append(
                {
                    "city": key[0],
                    "target_date": key[1],
                    "field": field,
                    "status": status,
                    "feature_value": feature_value,
                    "legacy_value": legacy_value,
                    "numeric_diff": diff,
                }
            )
    return pd.DataFrame(rows)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    snapshot_path = Path(args.snapshot)
    observations_path = Path(args.observation_cache)
    resolved_output = resolve_run_output(
        ARTIFACT_FAMILY,
        run_id=args.run_id,
        explicit_output=args.out_dir,
    )
    snapshot = load_json(snapshot_path)
    observations = load_json(observations_path)
    records = [row for row in snapshot.get("records", []) if isinstance(row, dict)]

    feature_df, feature_audits = build_weather_state_frame_with_audits(
        snapshot,
        observations,
        as_of_ts_utc=snapshot.get("ts_utc") or snapshot.get("snapshot_ts_utc"),
        source_profile_id="n100_recovery_observation_cache",
        input_snapshot_id=snapshot_path.name,
        pit_provenance=PIT_PROVENANCE_ARCHIVE_RECONSTRUCTION,
    )
    legacy_df, legacy_audits = tmax_live.build_state_rows(snapshot, records, index_observation_cache(observations))

    diff_df = compare_frames(feature_df, legacy_df, atol=float(args.atol))
    mismatch_df = diff_df[diff_df["status"].ne("match")].copy() if not diff_df.empty else pd.DataFrame()
    status_counts = Counter(diff_df["status"]) if not diff_df.empty else Counter()
    mismatch_by_field = Counter(mismatch_df["field"]) if not mismatch_df.empty else Counter()
    feature_keys = {tuple(row[field] for field in KEY_FIELDS) for row in feature_df[KEY_FIELDS].to_dict("records")}
    legacy_keys = {tuple(row[field] for field in KEY_FIELDS) for row in legacy_df[KEY_FIELDS].to_dict("records")} if not legacy_df.empty else set()

    out_dir = prepare_new_run_output(resolved_output)
    feature_df.to_csv(out_dir / "feature_state_rows.csv", index=False)
    legacy_df.to_csv(out_dir / "legacy_tmax_state_rows.csv", index=False)
    diff_df.to_csv(out_dir / "field_diff.csv", index=False)
    mismatch_df.head(int(args.max_examples)).to_csv(out_dir / "mismatch_examples.csv", index=False)
    summary = {
        "snapshot_path": str(snapshot_path),
        "observation_cache_path": str(observations_path),
        "snapshot_ts_utc": snapshot.get("ts_utc") or snapshot.get("snapshot_ts_utc"),
        "observation_cache_generated_at_utc": observations.get("generated_at_utc"),
        "feature_rows": int(len(feature_df)),
        "legacy_tmax_rows": int(len(legacy_df)),
        "common_rows": int(len(feature_keys & legacy_keys)),
        "feature_only_rows": int(len(feature_keys - legacy_keys)),
        "legacy_only_rows": int(len(legacy_keys - feature_keys)),
        "feature_audit_counts": dict(Counter(audit.status for audit in feature_audits)),
        "legacy_audit_counts": dict(Counter(str(row.get("status") or "unknown") for row in legacy_audits)),
        "compared_fields": SHARED_FIELDS,
        "field_comparisons": int(len(diff_df)),
        "status_counts": dict(status_counts),
        "mismatch_by_field": dict(mismatch_by_field),
        "mismatch_examples_path": str(out_dir / "mismatch_examples.csv"),
        "field_diff_path": str(out_dir / "field_diff.csv"),
        "feature_state_rows_path": str(out_dir / "feature_state_rows.csv"),
        "legacy_tmax_state_rows_path": str(out_dir / "legacy_tmax_state_rows.csv"),
        "notes": [
            "Shared-field parity only; tmax ladder, ask/bid, distribution, selector, and executor fields are intentionally excluded.",
            "The sample is archive reconstruction from N100 recovery artifacts and does not change any live runner behavior.",
        ],
    }
    write_json(out_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--observation-cache", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--atol", type=float, default=1e-6)
    parser.add_argument("--max-examples", type=int, default=50)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
