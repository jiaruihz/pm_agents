"""Strategy-neutral first-seen features for Korean AMOS observations.

The AMOS endpoint may publish multiple runway rows for the same station minute.
This module collapses those rows into one point-in-time city state and derives
rolling path features without making a trading decision.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from statistics import mean
from typing import Any

from weather_data_feed.observation_sources.fetchers import relative_humidity_pct
from weather_data_feed.physical_features import metar_physical_features
from weather_clock_contract import parse_utc_or_none


KOREA_AMOS_STATE_SCHEMA_VERSION = "korea_amos_first_seen_state_v1"
DEFAULT_PATH_WINDOW_MINUTES = (5, 15, 30, 60)


def parse_utc(value: Any) -> datetime | None:
    return parse_utc_or_none(value, field="korea_amos_clock")


def finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _iso(value: datetime | None) -> str:
    return value.astimezone(timezone.utc).isoformat() if value else ""


def _average(values: Iterable[Any]) -> float | None:
    finite = [value for raw in values if (value := finite_float(raw)) is not None]
    return mean(finite) if finite else None


def _preferred(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    return next(
        (row for row in rows if bool(row.get("is_preferred_temperature_runway"))),
        rows[0],
    )


def aggregate_amos_observation(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Collapse all runway rows for one city/source observation timestamp."""

    if not rows:
        raise ValueError("AMOS aggregation requires at least one runway row")
    ordered = sorted(
        (dict(row) for row in rows),
        key=lambda row: (
            not bool(row.get("is_preferred_temperature_runway")),
            str(row.get("runway") or ""),
        ),
    )
    identities = {
        (
            str(row.get("city") or ""),
            str(row.get("target_date") or ""),
            str(row.get("source") or ""),
            str(row.get("observation_time_utc") or ""),
        )
        for row in ordered
    }
    if len(identities) != 1:
        raise ValueError("AMOS rows must share city, target_date, source, and observation timestamp")
    city, target_date, source, observation_ts = next(iter(identities))
    if source != "amos_runway":
        raise ValueError(f"unsupported source for Korea AMOS state: {source!r}")

    preferred = _preferred(ordered)
    temperatures = [
        value for row in ordered if (value := finite_float(row.get("temp_c"))) is not None
    ]
    if not temperatures:
        raise ValueError("AMOS rows must contain a finite temp_c")
    source_temp = max(temperatures)
    preferred_temp = finite_float(preferred.get("temp_c"))
    dewpoint = finite_float(preferred.get("dewpoint_c"))
    if dewpoint is None:
        dewpoint = _average(row.get("dewpoint_c") for row in ordered)
    wind_speed = finite_float(preferred.get("wind_speed"))
    if wind_speed is None:
        wind_speed = _average(row.get("wind_speed") for row in ordered)
    wind_dir = finite_float(preferred.get("wind_dir"))
    if wind_dir is None:
        wind_dir = _average(row.get("wind_dir") for row in ordered)

    first_seen_candidates = [
        parse_utc(
            row.get("source_first_seen_at_utc")
            or row.get("first_seen_at_utc")
            or row.get("local_detect_ts_utc")
            or row.get("fetched_at_utc")
        )
        for row in ordered
    ]
    first_seen_values = [value for value in first_seen_candidates if value is not None]
    first_seen = min(first_seen_values) if first_seen_values else parse_utc(observation_ts)
    available_candidates = [
        parse_utc(row.get("available_at_utc") or row.get("source_published_at_utc"))
        for row in ordered
    ]
    available_values = [value for value in available_candidates if value is not None]
    available_at = min(available_values) if available_values else first_seen

    raw_metar = str(preferred.get("raw_metar") or "")
    if not raw_metar:
        raw_metar = next(
            (str(row.get("raw_metar") or "") for row in ordered if row.get("raw_metar")),
            "",
        )
    metar = metar_physical_features(raw_metar)
    relative_humidity = relative_humidity_pct(source_temp, dewpoint)
    runway_rows = []
    for row in ordered:
        runway_rows.append(
            {
                "runway": str(row.get("runway") or ""),
                "is_preferred_temperature_runway": bool(
                    row.get("is_preferred_temperature_runway")
                ),
                "temp_c": finite_float(row.get("temp_c")),
                "dewpoint_c": finite_float(row.get("dewpoint_c")),
                "wind_speed_kt": finite_float(row.get("wind_speed")),
                "wind_dir_deg": finite_float(row.get("wind_dir")),
                "rvr_m": finite_float(row.get("rvr")),
                "mor_m": finite_float(row.get("mor")),
                "payload_hash": str(row.get("payload_hash") or ""),
                "information_event_id": str(row.get("information_event_id") or ""),
            }
        )

    return {
        "schema_version": KOREA_AMOS_STATE_SCHEMA_VERSION,
        "feature_grain": "city_target_date_distinct_source_observation_first_seen",
        "city": city,
        "target_date": target_date,
        "source": source,
        "station": str(preferred.get("station") or ""),
        "source_observation_ts_utc": observation_ts,
        "source_first_seen_ts_utc": _iso(first_seen),
        "source_available_at_utc": _iso(available_at),
        "source_event_key": "|".join((city, target_date, source, observation_ts)),
        "source_temp_c": round(source_temp, 3),
        "preferred_runway_temp_c": (
            round(preferred_temp, 3) if preferred_temp is not None else None
        ),
        "runway_temp_min_c": round(min(temperatures), 3),
        "runway_temp_max_c": round(max(temperatures), 3),
        "runway_temp_mean_c": round(mean(temperatures), 3),
        "runway_temp_spread_c": round(max(temperatures) - min(temperatures), 3),
        "runway_count": len(runway_rows),
        "runways": runway_rows,
        "dewpoint_c": round(dewpoint, 3) if dewpoint is not None else None,
        "dewpoint_depression_c": (
            round(source_temp - dewpoint, 3) if dewpoint is not None else None
        ),
        "relative_humidity_pct": (
            round(relative_humidity, 3) if relative_humidity is not None else None
        ),
        "source_wind_speed_kt": (
            round(wind_speed, 3) if wind_speed is not None else None
        ),
        "source_wind_dir_deg": round(wind_dir, 3) if wind_dir is not None else None,
        "routine_metar_temp_c": finite_float(preferred.get("metar_temp_c")),
        "raw_metar": raw_metar,
        **metar,
    }


def _prior_at_or_before(
    history: Sequence[Mapping[str, Any]], cutoff: datetime
) -> Mapping[str, Any] | None:
    candidates = [
        row
        for row in history
        if (ts := parse_utc(row.get("source_observation_ts_utc"))) is not None
        and ts <= cutoff
    ]
    return candidates[-1] if candidates else None


def rolling_path_features(
    current: Mapping[str, Any],
    history: Sequence[Mapping[str, Any]],
    *,
    windows_minutes: Sequence[int] = DEFAULT_PATH_WINDOW_MINUTES,
) -> dict[str, Any]:
    """Build timestamp-aware continuous path features for one AMOS state."""

    current_ts = parse_utc(current.get("source_observation_ts_utc"))
    current_temp = finite_float(current.get("source_temp_c"))
    if current_ts is None or current_temp is None:
        raise ValueError("current AMOS state requires timestamp and source_temp_c")
    valid_history = sorted(
        (
            row
            for row in history
            if str(row.get("city") or "") == str(current.get("city") or "")
            and str(row.get("target_date") or "") == str(current.get("target_date") or "")
            and parse_utc(row.get("source_observation_ts_utc")) is not None
            and parse_utc(row.get("source_observation_ts_utc")) <= current_ts
        ),
        key=lambda row: str(row.get("source_observation_ts_utc") or ""),
    )
    if not valid_history or valid_history[-1].get("source_event_key") != current.get(
        "source_event_key"
    ):
        valid_history.append(dict(current))

    temperatures = [
        value
        for row in valid_history
        if (value := finite_float(row.get("source_temp_c"))) is not None
    ]
    running_max = max(temperatures)
    latest_high_row = next(
        row
        for row in reversed(valid_history)
        if finite_float(row.get("source_temp_c")) == running_max
    )
    latest_high_ts = parse_utc(latest_high_row.get("source_observation_ts_utc"))
    windows: dict[str, Any] = {}
    for raw_window in windows_minutes:
        window = int(raw_window)
        cutoff = current_ts.timestamp() - window * 60.0
        cutoff_dt = datetime.fromtimestamp(cutoff, tz=timezone.utc)
        prior = _prior_at_or_before(valid_history, cutoff_dt)
        in_window = [
            row
            for row in valid_history
            if (row_ts := parse_utc(row.get("source_observation_ts_utc"))) is not None
            and cutoff_dt <= row_ts <= current_ts
        ]
        values = [
            value
            for row in in_window
            if (value := finite_float(row.get("source_temp_c"))) is not None
        ]
        prior_temp = finite_float(prior.get("source_temp_c")) if prior else None
        prior_ts = (
            parse_utc(prior.get("source_observation_ts_utc")) if prior else None
        )
        elapsed_min = (
            (current_ts - prior_ts).total_seconds() / 60.0 if prior_ts else None
        )
        delta = current_temp - prior_temp if prior_temp is not None else None
        windows[f"{window}m"] = {
            "window_minutes": window,
            "observation_count": len(in_window),
            "history_complete": prior is not None,
            "anchor_observation_ts_utc": _iso(prior_ts),
            "anchor_age_minutes": round(elapsed_min, 3) if elapsed_min is not None else None,
            "temp_delta_c": round(delta, 3) if delta is not None else None,
            "temp_slope_c_per_hour": (
                round(delta * 60.0 / elapsed_min, 4)
                if delta is not None and elapsed_min and elapsed_min > 0
                else None
            ),
            "temp_min_c": round(min(values), 3) if values else None,
            "temp_max_c": round(max(values), 3) if values else None,
            "temp_mean_c": round(mean(values), 3) if values else None,
            "temp_range_c": round(max(values) - min(values), 3) if values else None,
        }

    return {
        "path_window_minutes": [int(value) for value in windows_minutes],
        "path_windows": windows,
        "source_running_max_c": round(running_max, 3),
        "distance_below_source_running_max_c": round(running_max - current_temp, 3),
        "minutes_since_source_running_max": (
            round((current_ts - latest_high_ts).total_seconds() / 60.0, 3)
            if latest_high_ts
            else None
        ),
        "observation_history_count": len(valid_history),
        "observation_history_span_minutes": round(
            (
                current_ts
                - parse_utc(valid_history[0].get("source_observation_ts_utc"))
            ).total_seconds()
            / 60.0,
            3,
        ),
    }
