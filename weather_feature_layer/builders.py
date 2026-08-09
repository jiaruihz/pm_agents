"""Builders for canonical weather feature frames.

The builders in this module consume normalized ``weather_data_feed`` artifacts
and produce strategy-neutral feature frames. They do not fetch live data or make
trading decisions.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from weather_data_feed.observation_cache import parse_utc
from weather_data_feed.sky_cover import SKY_COVER_CODE
from weather_feature_layer.contracts import (
    DEFAULT_FEATURE_VERSION_MANIFEST,
    FEATURE_FRAME_SCHEMA_VERSION,
    PIT_PROVENANCE_ARCHIVE_RECONSTRUCTION,
)
from weather_feature_layer.frame import validate_feature_metadata
from weather_feature_layer.regimes import add_regime_labels
from weather_feature_layer.state import city_wind_context, physical_context_features, temperature_context_features
from weather_feature_layer.transitions import forecast_transition_timing_features


WEATHER_STATE_FRAME_BUILDER_VERSION = "weather_state_frame_builder_v4"
WEATHER_STATE_FRAME_GRAIN = "city_date_snapshot"


@dataclass(frozen=True)
class WeatherStateBuildAudit:
    city: str
    target_date: str
    status: str
    reason: str


def build_weather_state_frame(
    snapshot_rows: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    observation_cache: Mapping[str, Any] | None,
    *,
    forecast_curve_rows: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None = None,
    forecast_enrichment_rows: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None = None,
    as_of_ts_utc: str | None = None,
    source_profile_id: str | None = None,
    input_snapshot_id: str | None = None,
    pit_provenance: str = PIT_PROVENANCE_ARCHIVE_RECONSTRUCTION,
    builder_version: str = WEATHER_STATE_FRAME_BUILDER_VERSION,
) -> pd.DataFrame:
    """Build a strategy-neutral city/date weather state frame.

    ``snapshot_rows`` can be a snapshot payload with a ``records`` list or an
    iterable of snapshot record mappings. ``observation_cache`` follows the
    shared ``weather_data_feed`` observation-cache contract.
    """

    frame, _audits = build_weather_state_frame_with_audits(
        snapshot_rows,
        observation_cache,
        forecast_curve_rows=forecast_curve_rows,
        forecast_enrichment_rows=forecast_enrichment_rows,
        as_of_ts_utc=as_of_ts_utc,
        source_profile_id=source_profile_id,
        input_snapshot_id=input_snapshot_id,
        pit_provenance=pit_provenance,
        builder_version=builder_version,
    )
    return frame


def build_weather_state_frame_with_audits(
    snapshot_rows: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    observation_cache: Mapping[str, Any] | None,
    *,
    forecast_curve_rows: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None = None,
    forecast_enrichment_rows: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None = None,
    as_of_ts_utc: str | None = None,
    source_profile_id: str | None = None,
    input_snapshot_id: str | None = None,
    pit_provenance: str = PIT_PROVENANCE_ARCHIVE_RECONSTRUCTION,
    builder_version: str = WEATHER_STATE_FRAME_BUILDER_VERSION,
) -> tuple[pd.DataFrame, list[WeatherStateBuildAudit]]:
    records = _snapshot_records(snapshot_rows)
    first_ts = _first_non_empty(records, "snapshot_ts_utc", "ts_utc")
    resolved_as_of = _iso_utc(as_of_ts_utc) or _iso_utc(first_ts) or _now_iso_utc()
    observations = _index_observations(observation_cache, resolved_as_of)
    resolved_source_profile = str(source_profile_id or _first_non_empty(records, "source_profile_id") or "unspecified")
    resolved_snapshot_id = str(
        input_snapshot_id
        or _first_non_empty(records, "snapshot_id", "snapshot_ts_utc", "ts_utc")
        or resolved_as_of
    )
    metadata = _feature_metadata(
        as_of_ts_utc=resolved_as_of,
        source_profile_id=resolved_source_profile,
        input_snapshot_id=resolved_snapshot_id,
        pit_provenance=pit_provenance,
        builder_version=builder_version,
    )
    validate_feature_metadata(metadata)
    forecast_curves = _index_forecast_curves(forecast_curve_rows, resolved_as_of)
    forecast_enrichment = _index_forecast_enrichment(forecast_enrichment_rows, resolved_as_of)

    rows: list[dict[str, Any]] = []
    audits: list[WeatherStateBuildAudit] = []
    for snapshot in _representative_snapshot_rows(records):
        city = str(snapshot.get("city") or "")
        target_date = str(snapshot.get("target_date") or "")
        if not city or not target_date:
            audits.append(WeatherStateBuildAudit(city, target_date, "skipped", "missing_city_or_target_date"))
            continue
        obs = observations.get((city, target_date))
        if not obs:
            audits.append(WeatherStateBuildAudit(city, target_date, "skipped", "missing_observation"))
            continue
        curve = forecast_curves.get((city, target_date))
        enrichment = forecast_enrichment.get((city, target_date))
        row = _build_state_row(
            snapshot,
            obs,
            forecast_curve=curve,
            forecast_enrichment=enrichment,
            as_of_ts_utc=resolved_as_of,
            source_profile_id=resolved_source_profile,
        )
        rows.append(row)
        audits.append(WeatherStateBuildAudit(city, target_date, "included", "ok"))

    frame = pd.DataFrame(rows)
    if not frame.empty:
        context_rows = []
        for row in frame.to_dict("records"):
            physical = physical_context_features(row)
            context_rows.append({**physical, **temperature_context_features({**row, **physical})})
        context_frame = pd.DataFrame(context_rows, index=frame.index)
        overlapping = context_frame.columns.intersection(frame.columns)
        for column in overlapping:
            frame[column] = context_frame[column].where(context_frame[column].notna(), frame[column])
        additions = context_frame.drop(columns=overlapping)
        if not additions.empty:
            frame = pd.concat([frame, additions], axis=1)
        frame = add_regime_labels(frame)
    frame = _attach_feature_metadata(frame, metadata)
    frame.attrs["feature_metadata"] = metadata
    frame.attrs["build_audits"] = [audit.__dict__ for audit in audits]
    return frame, audits


def _build_state_row(
    snapshot: Mapping[str, Any],
    obs: Mapping[str, Any],
    forecast_curve: Mapping[str, Any] | None,
    forecast_enrichment: Mapping[str, Any] | None,
    *,
    as_of_ts_utc: str,
    source_profile_id: str,
) -> dict[str, Any]:
    unit = str(snapshot.get("unit") or obs.get("unit") or "F").upper()
    # An explicit as-of is the caller's availability clock and must take
    # precedence over the producer's collection-start timestamp.
    decision_snapshot_ts = _iso_utc(as_of_ts_utc or snapshot.get("snapshot_ts_utc"))
    last_obs_iso = _iso_utc(_first_value(obs, "source_report_ts_utc", "last_obs_utc", "report_time_utc", "obs_time_utc"))
    running_obs_iso = _iso_utc(_first_value(obs, "running_max_obs_utc", "running_max_time_utc"))
    first_running_obs_iso = _iso_utc(_first_value(obs, "first_running_max_obs_utc"))
    last_running_obs_iso = _iso_utc(_first_value(obs, "last_running_max_obs_utc", "running_max_obs_utc"))
    # Observation age is a decision-clock feature.  Cached ``age_min`` values
    # describe the collector's fetch instant and become stale when an immutable
    # capture is replayed at a later snapshot.
    age_minutes = _age_minutes(last_obs_iso, decision_snapshot_ts)
    if age_minutes is None:
        age_minutes = _first_float(obs, "obs_age_minutes", "obs_age_min", "age_min")
    cadence_min = _first_float(obs, "expected_report_cadence", "observation_cadence_min", "cadence_min", "estimated_cadence_min")

    current_temp_c = _first_float(obs, "current_temp_c", "temp_c_now", "temp_c")
    running_max_c = _first_float(obs, "running_max_c", "running_max_temp_c", "max_temp_c")
    current_native = _native_temp(current_temp_c, unit)
    running_native = _native_temp(running_max_c, unit)
    decline_native = _decline(running_native, current_native)
    forecast_max_native = _first_float(snapshot, "forecast_max_native", "forecast_tmax_native")
    forecast_max_f = _first_float(snapshot, "forecast_max_f")
    if forecast_max_f is None:
        forecast_max_f = _forecast_max_f(forecast_max_native, unit)
    forecast_gap_to_running_native = _gap(forecast_max_native, running_native)
    decision_hour = _decision_hour(snapshot)

    curve = dict(forecast_curve or {})
    row: dict[str, Any] = {
        "city": str(snapshot.get("city") or obs.get("city") or ""),
        "target_date": str(snapshot.get("target_date") or obs.get("target_date") or ""),
        "decision_snapshot_ts_utc": decision_snapshot_ts,
        "decision_hour_local": decision_hour,
        "decision_hour_local_float": decision_hour,
        "timezone": str(snapshot.get("timezone_name") or snapshot.get("timezone") or obs.get("timezone") or ""),
        "unit": unit,
        "source_profile_id": source_profile_id,
        "forecast_source": _clean_str(snapshot.get("forecast_source") or snapshot.get("source_model")),
        "forecast_clock_source": "paper_snapshot",
        "forecast_timezone": _clean_str(snapshot.get("forecast_timezone") or snapshot.get("timezone_name")),
        "forecast_max_native": forecast_max_native,
        "forecast_max_f": forecast_max_f,
        "forecast_peak_hour_local": _first_float(snapshot, "forecast_peak_hour_local", "peak_hour_local"),
        "forecast_peak_delta_hours_local": _first_float(snapshot, "forecast_peak_delta_hours_local", "peak_delta_hours_local"),
        "forecast_peak_hour_spread": _first_float(snapshot, "forecast_peak_hour_spread"),
        "hourly_curve": curve.get("hourly_curve") if isinstance(curve.get("hourly_curve"), list) else [],
        "latitude": _coalesce_float(_first_float(snapshot, "latitude", "lat"), _first_float(curve, "latitude", "lat")),
        "longitude": _coalesce_float(_first_float(snapshot, "longitude", "lon"), _first_float(curve, "longitude", "lon")),
        "forecast_gap_to_running_native": forecast_gap_to_running_native,
        "obs_status": _clean_str(obs.get("status")),
        "obs_source": _clean_str(obs.get("source")),
        "station": _clean_str(obs.get("station")),
        "source_report_ts_utc": last_obs_iso,
        "detect_ts_utc": _iso_utc(_first_value(obs, "detect_ts_utc", "detected_at_utc", "reportTime")),
        "fetched_at_utc": _iso_utc(_first_value(obs, "fetched_at_utc", "fetched_at", "cache_generated_at_utc")),
        "obs_age_minutes": age_minutes,
        "obs_age_min": age_minutes,
        "expected_report_cadence": cadence_min,
        "observation_cadence_min": cadence_min,
        "station_gap_state": _station_gap_state(obs, age_minutes, cadence_min),
        "current_temp_c": current_temp_c,
        "running_max_c": running_max_c,
        "current_native": current_native,
        "current_temp_native": current_native,
        "running_native": running_native,
        "running_max_native": running_native,
        "running_value": running_native,
        "decline_native": decline_native,
        "decline_from_running_max_native": decline_native,
        "tmpf_now": _first_float(obs, "tmpf_now", "tmpf"),
        "dwpf_now": _first_float(obs, "dwpf_now", "dwpf"),
        "dewpoint_depression_f": _dewpoint_depression_f(obs),
        "relative_humidity_pct": _first_float(obs, "relative_humidity_pct", "relh_now", "relh"),
        "wind_speed_kt": _first_float(obs, "wind_speed_kt", "sknt_now", "sknt"),
        "wind_dir_deg": _first_float(obs, "wind_dir_deg", "drct_now", "drct"),
        "raw_metar": _clean_str(_first_value(obs, "raw_metar", "raw_text")),
        "present_weather": _first_value(obs, "present_weather", "wx_string", "wx_phrase"),
        "sky_cover_code": _sky_cover_code(_first_value(obs, "sky_cover_code", "sky_code_now", "sky_now", "sky", "sky_cover")),
        "temp_trend_1h_f": _first_float(obs, "temp_trend_1h_f", "d_tmpf_1h"),
        "temp_trend_3h_f": _first_float(obs, "temp_trend_3h_f", "d_tmpf_3h"),
        "temp_trend_report_anchored_1h_f": _first_float(obs, "temp_trend_report_anchored_1h_f"),
        "temp_trend_report_anchored_3h_f": _first_float(obs, "temp_trend_report_anchored_3h_f"),
        "dewpoint_trend_1h_f": _first_float(obs, "dewpoint_trend_1h_f", "dewpoint_change_1h_f", "d_dwpf_1h"),
        "dewpoint_trend_3h_f": _first_float(obs, "dewpoint_trend_3h_f", "d_dwpf_3h"),
        "dewpoint_trend_report_anchored_1h_f": _first_float(obs, "dewpoint_trend_report_anchored_1h_f"),
        "dewpoint_trend_report_anchored_3h_f": _first_float(obs, "dewpoint_trend_report_anchored_3h_f"),
        "relative_humidity_trend_report_anchored_3h_pct": _first_float(obs, "relative_humidity_trend_report_anchored_3h_pct"),
        "minutes_since_running_max": _first_float(obs, "minutes_since_running_max"),
        "running_max_obs_utc": running_obs_iso,
        "first_running_max_obs_utc": first_running_obs_iso,
        "last_running_max_obs_utc": last_running_obs_iso,
        "minutes_since_first_running_max": _first_float(obs, "minutes_since_first_running_max"),
        "minutes_since_last_running_max": _first_float(obs, "minutes_since_last_running_max", "minutes_since_running_max"),
        "minutes_since_last_strict_new_high": _first_float(obs, "minutes_since_last_strict_new_high"),
        "same_running_max_obs_count": _first_float(obs, "same_running_max_obs_count"),
        "running_max_clock_left_censored": _first_value(obs, "running_max_clock_left_censored"),
        "observation_history_span_minutes": _first_float(obs, "observation_history_span_minutes"),
        "clear_sky_regime_minutes": _first_float(obs, "clear_sky_regime_minutes"),
        "clear_sky_regime_temp_change_f": _first_float(obs, "clear_sky_regime_temp_change_f"),
        "clear_sky_regime_left_censored": _first_value(obs, "clear_sky_regime_left_censored"),
        "precip_free_regime_minutes": _first_float(obs, "precip_free_regime_minutes"),
        "precip_free_regime_temp_change_f": _first_float(obs, "precip_free_regime_temp_change_f"),
        "precip_free_regime_left_censored": _first_value(obs, "precip_free_regime_left_censored"),
        "first_precip_obs_utc": _iso_utc(_first_value(obs, "first_precip_obs_utc")),
        "last_precip_obs_utc": _iso_utc(_first_value(obs, "last_precip_obs_utc")),
        "precip_obs_count": _first_float(obs, "precip_obs_count"),
        "first_thunderstorm_obs_utc": _iso_utc(_first_value(obs, "first_thunderstorm_obs_utc")),
        "last_thunderstorm_obs_utc": _iso_utc(_first_value(obs, "last_thunderstorm_obs_utc")),
        "thunderstorm_obs_count": _first_float(obs, "thunderstorm_obs_count"),
        "precip_onset_clock_source": _clean_str(obs.get("precip_onset_clock_source")),
        "thunderstorm_onset_clock_source": _clean_str(obs.get("thunderstorm_onset_clock_source")),
        "minutes_since_last_precip_obs": _first_float(obs, "minutes_since_last_precip_obs"),
        "cloud_cover_change_1h_code": _first_float(obs, "cloud_cover_change_1h_code", "d_sky_1h"),
        "ceiling_change_1h_ft": _first_float(obs, "ceiling_change_1h_ft"),
        "wind_speed_change_1h_kt": _first_float(obs, "wind_speed_change_1h_kt", "d_wind_speed_1h_kt"),
        "wind_dir_1h_prior_deg": _first_float(obs, "wind_dir_1h_prior_deg"),
        "wind_dir_change_1h_deg": _first_float(obs, "wind_dir_change_1h_deg"),
        "dewpoint_change_1h_f": _first_float(obs, "dewpoint_change_1h_f", "d_dwpf_1h"),
        "relative_humidity_change_1h_pct": _first_float(obs, "relative_humidity_change_1h_pct"),
    }
    row.update(city_wind_context(row["city"], row["wind_dir_deg"]))
    row.update(
        forecast_transition_timing_features(
            forecast_enrichment,
            obs,
            as_of_ts_utc=decision_snapshot_ts,
        )
    )
    return row


def _snapshot_records(snapshot_rows: Mapping[str, Any] | Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(snapshot_rows, Mapping):
        records = snapshot_rows.get("records")
        if isinstance(records, list):
            return [dict(row) for row in records if isinstance(row, Mapping)]
        return [dict(snapshot_rows)]
    return [dict(row) for row in snapshot_rows if isinstance(row, Mapping)]


def _representative_snapshot_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], tuple[tuple[int, datetime], dict[str, Any]]] = {}
    for row in records:
        key = (str(row.get("city") or ""), str(row.get("target_date") or ""))
        row_sort = _representative_sort_key(row)
        if key not in by_key or row_sort > by_key[key][0]:
            by_key[key] = (row_sort, row)
    return [value for _sort, value in by_key.values()]


def _representative_sort_key(row: Mapping[str, Any]) -> tuple[int, datetime]:
    has_forecast = 1 if _first_float(row, "forecast_max_native", "forecast_tmax_native") is not None else 0
    snapshot_dt = parse_utc(row.get("snapshot_ts_utc") or row.get("ts_utc")) or datetime.min.replace(tzinfo=timezone.utc)
    return has_forecast, snapshot_dt


def _index_observations(
    observation_cache: Mapping[str, Any] | None,
    as_of_ts_utc: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    if not observation_cache:
        return {}
    as_of = parse_utc(as_of_ts_utc)
    if all(isinstance(key, tuple) and len(key) == 2 for key in observation_cache):
        raw_rows = []
        for key, value in observation_cache.items():
            if not isinstance(value, Mapping):
                continue
            row = dict(value)
            row.setdefault("city", str(key[0]))
            row.setdefault("target_date", str(key[1]))
            raw_rows.append(row)
    else:
        records = observation_cache.get("records")
        raw_rows = [dict(value) for value in records if isinstance(value, Mapping)] if isinstance(records, list) else []
    out: dict[tuple[str, str], tuple[datetime, dict[str, Any]]] = {}
    first_precip_by_key: dict[tuple[str, str], datetime] = {}
    first_thunder_by_key: dict[tuple[str, str], datetime] = {}
    for row in raw_rows:
        key = (str(row.get("city") or ""), str(row.get("target_date") or ""))
        if not all(key):
            continue
        available = parse_utc(
            row.get("available_at_utc")
            or row.get("first_seen_at_utc")
            or row.get("detect_ts_utc")
            or row.get("fetched_at_utc")
            or row.get("ingest_ts_utc")
        )
        if available is not None and as_of is not None and available > as_of:
            continue
        report_time = parse_utc(row.get("source_report_ts_utc") or row.get("last_obs_utc"))
        if report_time is not None and as_of is not None and report_time > as_of:
            continue
        if report_time is not None and (as_of is None or report_time <= as_of):
            if bool(row.get("precip_observed")):
                previous = first_precip_by_key.get(key)
                if previous is None or report_time < previous:
                    first_precip_by_key[key] = report_time
            if bool(row.get("thunderstorm_observed")):
                previous = first_thunder_by_key.get(key)
                if previous is None or report_time < previous:
                    first_thunder_by_key[key] = report_time
        sort_time = available or parse_utc(row.get("source_report_ts_utc") or row.get("last_obs_utc"))
        sort_time = sort_time or datetime.min.replace(tzinfo=timezone.utc)
        if key not in out or sort_time > out[key][0]:
            out[key] = (sort_time, row)
    indexed = {key: value[1] for key, value in out.items()}
    for key, row in indexed.items():
        if not str(row.get("first_precip_obs_utc") or "") and key in first_precip_by_key:
            row["first_precip_obs_utc"] = first_precip_by_key[key].isoformat()
            row["precip_onset_clock_source"] = "immutable_cache_capture_reconstruction"
        if not str(row.get("first_thunderstorm_obs_utc") or "") and key in first_thunder_by_key:
            row["first_thunderstorm_obs_utc"] = first_thunder_by_key[key].isoformat()
            row["thunderstorm_onset_clock_source"] = "immutable_cache_capture_reconstruction"
    return indexed


def _index_forecast_curves(
    rows: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None,
    as_of_ts_utc: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    if rows is None:
        return {}
    if isinstance(rows, Mapping):
        raw_rows = rows.get("records") or rows.get("rows") or [rows]
    else:
        raw_rows = rows
    as_of = parse_utc(as_of_ts_utc)
    out: dict[tuple[str, str], tuple[datetime, dict[str, Any]]] = {}
    for item in raw_rows:
        if not isinstance(item, Mapping):
            continue
        row = dict(item)
        available = parse_utc(
            row.get("available_at_utc")
            or row.get("forecast_first_seen_utc")
            or row.get("forecast_detected_at_utc")
            or row.get("snapshot_ts_utc")
        )
        if available is None or (as_of is not None and available > as_of):
            continue
        key = (str(row.get("city") or ""), str(row.get("target_date") or ""))
        if not all(key):
            continue
        if key not in out or available > out[key][0]:
            out[key] = (available, row)
    return {key: value[1] for key, value in out.items()}


def _index_forecast_enrichment(
    rows: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None,
    as_of_ts_utc: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Select the latest enrichment capture provably visible at ``as_of``."""

    if rows is None:
        return {}
    if isinstance(rows, Mapping):
        raw_rows = rows.get("records") or rows.get("rows") or [rows]
    else:
        raw_rows = rows
    as_of = parse_utc(as_of_ts_utc)
    out: dict[tuple[str, str], tuple[datetime, dict[str, Any]]] = {}
    for item in raw_rows:
        if not isinstance(item, Mapping):
            continue
        row = dict(item)
        captured = parse_utc(row.get("available_at_utc") or row.get("snapshot_ts_utc"))
        if captured is None or (as_of is not None and captured > as_of):
            continue
        key = (str(row.get("city") or ""), str(row.get("target_date") or ""))
        if not all(key):
            continue
        if key not in out or captured > out[key][0]:
            out[key] = (captured, row)
    return {key: value[1] for key, value in out.items()}


def _feature_metadata(
    *,
    as_of_ts_utc: str,
    source_profile_id: str,
    input_snapshot_id: str,
    pit_provenance: str,
    builder_version: str,
) -> dict[str, Any]:
    return {
        "feature_schema_version": FEATURE_FRAME_SCHEMA_VERSION,
        "feature_grain": WEATHER_STATE_FRAME_GRAIN,
        "as_of_ts_utc": as_of_ts_utc,
        "source_profile_id": source_profile_id,
        "feature_version_manifest": dict(DEFAULT_FEATURE_VERSION_MANIFEST),
        "pit_provenance": pit_provenance,
        "builder_version": builder_version,
        "input_snapshot_id": input_snapshot_id,
    }


def _attach_feature_metadata(frame: pd.DataFrame, metadata: Mapping[str, Any]) -> pd.DataFrame:
    out = frame.copy()
    for key, value in metadata.items():
        out[key] = json.dumps(value, sort_keys=True) if isinstance(value, Mapping) else value
    return out


def _first_non_empty(rows: list[Mapping[str, Any]], *keys: str) -> Any:
    for row in rows:
        value = _first_value(row, *keys)
        if str(value or "").strip():
            return value
    return None


def _first_value(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _first_float(row: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _finite_float(row.get(key))
        if value is not None:
            return value
    return None


def _finite_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _coalesce_float(*values: float | None) -> float | None:
    return next((value for value in values if value is not None), None)


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _native_temp(temp_c: float | None, unit: str) -> float | None:
    if temp_c is None:
        return None
    if unit == "C":
        return temp_c
    return temp_c * 9.0 / 5.0 + 32.0


def _forecast_max_f(forecast_max_native: float | None, unit: str) -> float | None:
    if forecast_max_native is None:
        return None
    if unit == "C":
        return forecast_max_native * 9.0 / 5.0 + 32.0
    return forecast_max_native


def _gap(forecast_max_native: float | None, running_native: float | None) -> float | None:
    if forecast_max_native is None or running_native is None:
        return None
    return forecast_max_native - running_native


def _decline(running_native: float | None, current_native: float | None) -> float | None:
    if running_native is None or current_native is None:
        return None
    return running_native - current_native


def _dewpoint_depression_f(obs: Mapping[str, Any]) -> float | None:
    value = _first_float(obs, "dewpoint_depression_f", "dewpoint_depression_native")
    if value is not None:
        return value
    tmpf = _first_float(obs, "tmpf_now", "tmpf")
    dwpf = _first_float(obs, "dwpf_now", "dwpf")
    if tmpf is None or dwpf is None:
        return None
    return tmpf - dwpf


def _decision_hour(snapshot: Mapping[str, Any]) -> float | None:
    hour = _first_float(snapshot, "decision_hour_local", "decision_hour_local_float")
    if hour is not None:
        return hour
    peak = _first_float(snapshot, "forecast_peak_hour_local", "peak_hour_local")
    delta = _first_float(snapshot, "forecast_peak_delta_hours_local", "peak_delta_hours_local")
    if peak is not None and delta is not None:
        return peak + delta
    local_value = snapshot.get("city_local_ts") or snapshot.get("snapshot_local_ts") or snapshot.get("ts_local")
    if local_value:
        try:
            local_ts = datetime.fromisoformat(str(local_value).replace("Z", "+00:00"))
        except ValueError:
            local_ts = None
        if local_ts is not None:
            return local_ts.hour + local_ts.minute / 60.0 + local_ts.second / 3600.0
    return None


def _sky_cover_code(value: Any) -> float | None:
    numeric = _finite_float(value)
    if numeric is not None:
        return numeric
    text = str(value or "").strip().upper()
    if not text:
        return None
    return float(SKY_COVER_CODE.get(text)) if text in SKY_COVER_CODE else None


def _station_gap_state(obs: Mapping[str, Any], age_minutes: float | None, cadence_min: float | None) -> str:
    status = str(obs.get("status") or "").strip().lower()
    if status and status != "ok":
        return "observation_not_ok"
    if age_minutes is None:
        return "age_unknown"
    if cadence_min is None:
        return "stale_unknown_cadence" if age_minutes > 90 else "fresh_unknown_cadence"
    return "beyond_expected_cadence" if age_minutes > cadence_min + 10 else "within_expected_cadence"


def _age_minutes(source_report_ts_utc: str | None, as_of_ts_utc: str | None) -> float | None:
    source_dt = parse_utc(source_report_ts_utc)
    as_of_dt = parse_utc(as_of_ts_utc)
    if source_dt is None or as_of_dt is None:
        return None
    return (as_of_dt - source_dt).total_seconds() / 60.0


def _iso_utc(value: Any) -> str | None:
    dt = parse_utc(value)
    if dt is None:
        return None
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
