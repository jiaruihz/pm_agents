"""Immutable point-in-time hourly forecast curve captures."""

from __future__ import annotations

import json
import os
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


FORECAST_HOURLY_CURVE_SCHEMA_VERSION = "forecast_hourly_curve_v4"
_BEIJING = timezone(timedelta(hours=8))
_FIRST_SEEN_KEY_FIELDS = (
    "city",
    "target_date",
    "forecast_source",
    "forecast_model",
    "forecast_values_hash",
)
_REQUIRED_CAPTURE_FIELDS = (
    "city",
    "target_date",
    "forecast_source",
    "forecast_model",
    "forecast_values_hash",
    "snapshot_ts_utc",
    "hourly_curve",
)


def build_hourly_curve(
    times: Iterable[Any],
    temperatures_f: Iterable[Any],
    *,
    precipitation_probability_pct: Iterable[Any] | None = None,
    cloud_cover_pct: Iterable[Any] | None = None,
    wind_speed_10m_kt: Iterable[Any] | None = None,
    wind_direction_10m_deg: Iterable[Any] | None = None,
) -> list[dict[str, Any]]:
    """Normalize the source response into the stable curve payload."""
    time_values = list(times)
    temp_values = list(temperatures_f)
    optional = {
        "precipitation_probability_pct": list(precipitation_probability_pct or []),
        "cloud_cover_pct": list(cloud_cover_pct or []),
        "wind_speed_10m_kt": list(wind_speed_10m_kt or []),
        "wind_direction_10m_deg": list(wind_direction_10m_deg or []),
    }
    curve: list[dict[str, Any]] = []
    for idx, (time_local, temperature_f) in enumerate(zip(time_values, temp_values)):
        if temperature_f is None:
            continue
        try:
            row = {"time_local": str(time_local), "temperature_f": round(float(temperature_f), 3)}
            for field, values in optional.items():
                if idx >= len(values) or values[idx] is None:
                    continue
                try:
                    row[field] = round(float(values[idx]), 3)
                except (TypeError, ValueError):
                    continue
            curve.append(row)
        except (TypeError, ValueError):
            continue
    return curve


def summarize_source_models(
    rows: Iterable[Mapping[str, Any]],
    *,
    expected_city_target_count: int,
) -> dict[str, Any]:
    """Summarize assigned/actual model lineage from the captured curve rows."""
    source_rows = list(rows)
    assigned = Counter(str(row.get("forecast_assigned_model") or "missing") for row in source_rows)
    actual = Counter(str(row.get("forecast_model") or "missing") for row in source_rows)
    fallback_rows = [row for row in source_rows if row.get("forecast_model_fallback") is True]
    fallback_reasons: Counter[str] = Counter()
    for row in fallback_rows:
        reasons = [reason for reason in str(row.get("forecast_model_fallback_reason") or "").split(";") if reason]
        if not reasons:
            reasons = ["missing_reason"]
        fallback_reasons.update(reasons)
    captured_count = len(source_rows)
    return {
        "schema_version": "forecast_source_model_summary_v1",
        "grain": "city_target_forecast",
        "expected_city_target_count": int(expected_city_target_count),
        "captured_city_target_count": captured_count,
        "assigned_model_counts": dict(sorted(assigned.items())),
        "actual_model_counts": dict(sorted(actual.items())),
        "fallback_count": len(fallback_rows),
        "fallback_reason_counts": dict(sorted(fallback_reasons.items())),
        "missing_count": max(0, int(expected_city_target_count) - captured_count),
    }


def build_curve_row(
    *,
    snapshot_ts_utc: str,
    city: str,
    target_date: str,
    forecast_source: str,
    forecast_model: str,
    forecast_assigned_model: str,
    forecast_values_hash: str,
    hourly_curve: list[dict[str, Any]],
    forecast_max_f: float,
    forecast_peak_hour_local: int | None,
    forecast_peak_time_local: str | None,
    forecast_peak_hour_utc: int | None,
    forecast_peak_time_utc: str | None,
    forecast_timezone: str | None,
    forecast_timezone_abbreviation: str | None,
    forecast_utc_offset_seconds: int | None,
    forecast_generationtime_ms: float | None,
    forecast_model_fallback_reason: str | None,
    forecast_detected_at_utc: str | None = None,
    forecast_run_ts_utc: str | None = None,
    forecast_run_lineage_status: str = "source_response_does_not_expose_run_timestamp",
    latitude: float | None = None,
    longitude: float | None = None,
) -> dict[str, Any]:
    """Build one city/target curve row before immutable publication.

    Open-Meteo's live model endpoint does not expose an originating model-run
    timestamp. Keep that absence explicit instead of converting the local
    cycle estimate into false run lineage.
    """
    fallback_applied = forecast_model != forecast_assigned_model
    if fallback_applied and not forecast_model_fallback_reason:
        forecast_model_fallback_reason = "active_model_differs_from_city_model"
    return {
        "schema_version": FORECAST_HOURLY_CURVE_SCHEMA_VERSION,
        "snapshot_ts_utc": snapshot_ts_utc,
        "city": city,
        "target_date": target_date,
        "forecast_source": forecast_source,
        "forecast_model": forecast_model,
        "forecast_assigned_model": forecast_assigned_model,
        "forecast_model_fallback": fallback_applied,
        "forecast_model_fallback_reason": forecast_model_fallback_reason or None,
        "forecast_values_hash": forecast_values_hash,
        "forecast_max_f": round(float(forecast_max_f), 3),
        "forecast_peak_hour_local": forecast_peak_hour_local,
        "forecast_peak_time_local": forecast_peak_time_local,
        "forecast_peak_hour_utc": forecast_peak_hour_utc,
        "forecast_peak_time_utc": forecast_peak_time_utc,
        "forecast_hourly_count": len(hourly_curve),
        "forecast_timezone": forecast_timezone,
        "forecast_timezone_abbreviation": forecast_timezone_abbreviation,
        "forecast_utc_offset_seconds": forecast_utc_offset_seconds,
        "forecast_generationtime_ms": forecast_generationtime_ms,
        "latitude": latitude,
        "longitude": longitude,
        "forecast_detected_at_utc": forecast_detected_at_utc,
        "forecast_detected_at_basis": (
            "collector_after_source_response_parse" if forecast_detected_at_utc else None
        ),
        "forecast_run_ts_utc": forecast_run_ts_utc,
        "forecast_run_lineage_status": forecast_run_lineage_status,
        "hourly_curve": hourly_curve,
    }


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_string(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _first_seen_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(row.get(field) or "") for field in _FIRST_SEEN_KEY_FIELDS)


def _load_first_seen(curve_root: Path) -> dict[tuple[str, ...], tuple[datetime, str]]:
    first_seen: dict[tuple[str, ...], tuple[datetime, str]] = {}
    for path in curve_root.glob("*/forecast_hourly_curves_*.jsonl"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = _first_seen_key(row)
            first_seen_source = str(row.get("forecast_first_seen_source") or "")
            if first_seen_source:
                seen_at = _parse_utc(row.get("forecast_first_seen_utc"))
                historical_source = "historical_capture_first_seen"
            else:
                # v2 captures have a usable publication boundary but their
                # first_seen incorrectly defaulted to snapshot_ts_utc.
                seen_at = _parse_utc(row.get("available_at_utc"))
                historical_source = "historical_capture_available_at"
            if not all(key) or seen_at is None:
                continue
            previous = first_seen.get(key)
            if previous is None or seen_at < previous[0]:
                first_seen[key] = (seen_at, historical_source)
    return first_seen


def _validate_row(row: Mapping[str, Any]) -> None:
    missing = [field for field in _REQUIRED_CAPTURE_FIELDS if not row.get(field)]
    if missing:
        raise ValueError(f"forecast curve row missing required fields: {', '.join(missing)}")
    if not isinstance(row.get("hourly_curve"), list):
        raise ValueError("forecast curve row hourly_curve must be a list")


def write_forecast_hourly_curve_capture(
    output_root: Path,
    rows: Iterable[Mapping[str, Any]],
    *,
    available_at_utc: datetime | None = None,
) -> Path:
    """Publish one immutable capture file and preserve collector first-seen lineage.

    ``available_at_utc`` is the collector publication boundary sampled after
    prior lineage is loaded and immediately before final serialization/fsync
    and atomic link. It is not source fetch completion. The destination mtime
    is the external evidence that the immutable file write completed.
    """
    source_rows = [dict(row) for row in rows]
    if not source_rows:
        raise ValueError("refusing to publish an empty forecast curve capture")
    for row in source_rows:
        _validate_row(row)

    snapshot_at = _parse_utc(source_rows[0]["snapshot_ts_utc"])
    if snapshot_at is None:
        raise ValueError("forecast curve snapshot_ts_utc must be ISO-8601")
    if any(_parse_utc(row["snapshot_ts_utc"]) != snapshot_at for row in source_rows):
        raise ValueError("forecast curve capture rows must share one snapshot_ts_utc")

    curve_root = Path(output_root) / "forecast_hourly_curves"
    curve_root.mkdir(parents=True, exist_ok=True)
    first_seen = _load_first_seen(curve_root)
    capture_id = f"forecast_hourly_curves_{snapshot_at.astimezone(_BEIJING).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:10]}"

    lineage_rows: list[tuple[dict[str, Any], datetime | None, tuple[datetime, str] | None]] = []
    for row in source_rows:
        detected_at = _parse_utc(row.get("forecast_detected_at_utc"))
        lineage_rows.append((row, detected_at, first_seen.get(_first_seen_key(row))))

    # This is intentionally sampled at the capture publication boundary, not
    # when any individual forecast fetch completed.
    available_at = (available_at_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    payload_rows: list[dict[str, Any]] = []
    for row, detected_at, historical_first_seen in lineage_rows:
        key = _first_seen_key(row)
        current_snapshot = _parse_utc(row["snapshot_ts_utc"])
        assert current_snapshot is not None
        if detected_at is not None and not current_snapshot <= detected_at <= available_at:
            raise ValueError("forecast_detected_at_utc must be between snapshot_ts_utc and available_at_utc")
        if historical_first_seen is not None:
            observed_first_seen, first_seen_source = historical_first_seen
        elif detected_at is not None:
            observed_first_seen = detected_at
            first_seen_source = "current_capture_detected_at"
        else:
            observed_first_seen = available_at
            first_seen_source = "current_capture_available_at"
        if observed_first_seen > available_at:
            raise ValueError("forecast_first_seen_utc cannot be after available_at_utc")
        first_seen[key] = (observed_first_seen, first_seen_source)
        payload_rows.append(
            {
                **row,
                "schema_version": FORECAST_HOURLY_CURVE_SCHEMA_VERSION,
                "capture_id": capture_id,
                "available_at_utc": _utc_string(available_at),
                "available_at_basis": "collector_publish_started_before_final_write_and_atomic_link",
                "forecast_first_seen_utc": _utc_string(observed_first_seen),
                "forecast_first_seen_basis": "collector_exact_values_hash",
                "forecast_first_seen_source": first_seen_source,
                "forecast_run_ts_utc": row.get("forecast_run_ts_utc") or None,
                "forecast_run_lineage_status": row.get("forecast_run_lineage_status")
                or "source_response_does_not_expose_run_timestamp",
            }
        )

    output_dir = curve_root / snapshot_at.astimezone(_BEIJING).strftime("%Y-%m-%d")
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{capture_id}.jsonl"
    temporary = output_dir / f".{capture_id}.{os.getpid()}.tmp"
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            for row in payload_rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        # link() publishes only when the destination does not already exist.
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
