"""Strategy-neutral, point-in-time atmospheric feature helpers.

All outputs are scalar JSON-friendly fields.  The shared feature layer calls
this module for both live captures and archive reconstruction; strategies must
not maintain private METAR or solar parsers.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any


_CLOUD_RE = re.compile(r"\b(FEW|SCT|BKN|OVC|VV)(\d{3}|///)?\b")
_WIND_RE = re.compile(r"\b(\d{3}|VRB)(\d{2,3})(?:G\d{2,3})?KT\b")
_QNH_HPA_RE = re.compile(r"\bQ(\d{4})\b")
_ALTIMETER_INHG_RE = re.compile(r"\bA(\d{4})\b")
_WX_RE = re.compile(
    r"^(?:\+|-|VC)?(?:MI|PR|BC|DR|BL|SH|TS|FZ)?"
    r"(?:DZ|RA|SN|SG|IC|PL|GR|GS|UP)(?:(?:DZ|RA|SN|SG|IC|PL|GR|GS|UP))?$"
)


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _first_float(record: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _finite(record.get(key))
        if value is not None:
            return value
    return None


def _parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def metar_physical_features(raw_metar: Any, present_weather: Any = None) -> dict[str, Any]:
    """Parse current precipitation, cloud layers, and wind from METAR facts."""

    raw = str(raw_metar or "").upper().strip()
    explicit: list[str] = []
    if isinstance(present_weather, Sequence) and not isinstance(present_weather, (str, bytes)):
        explicit = [str(item).upper().strip() for item in present_weather if str(item).strip()]
    elif str(present_weather or "").strip():
        explicit = re.split(r"[\s,;|]+", str(present_weather).upper().strip())
    tokens = [token.strip("=") for token in raw.split()]
    wx_tokens = [
        token
        for token in [*explicit, *tokens]
        if _WX_RE.fullmatch(token) or token in {"TS", "VCTS"}
    ]
    wx_tokens = list(dict.fromkeys(wx_tokens))

    has_rain = any("RA" in token or "DZ" in token for token in wx_tokens)
    has_snow = any(any(code in token for code in ("SN", "SG", "PL", "IC")) for token in wx_tokens)
    has_hail = any("GR" in token or "GS" in token for token in wx_tokens)
    thunder = any("TS" in token for token in wx_tokens)
    freezing = any("FZ" in token for token in wx_tokens)
    if not raw and not explicit:
        precip_state = "unknown"
    elif thunder:
        precip_state = "thunderstorm"
    elif freezing:
        precip_state = "freezing_precip"
    elif sum((has_rain, has_snow, has_hail)) > 1:
        precip_state = "mixed_precip"
    elif has_hail:
        precip_state = "hail"
    elif has_snow:
        precip_state = "snow_or_ice"
    elif has_rain:
        precip_state = "rain_or_drizzle"
    else:
        precip_state = "none_observed"
    has_precip = has_rain or has_snow or has_hail or freezing
    intensity = 0 if not has_precip else None
    if has_precip:
        intensity = max(3 if token.startswith("+") else 1 if token.startswith("-") else 2 for token in wx_tokens)

    layers: list[tuple[str, int | None]] = []
    for cover, height in _CLOUD_RE.findall(raw):
        layers.append((cover, int(height) * 100 if height.isdigit() else None))
    bases = [base for _cover, base in layers if base is not None]
    ceilings = [base for cover, base in layers if cover in {"BKN", "OVC", "VV"} and base is not None]
    wind_match = _WIND_RE.search(raw)
    wind_dir = None if wind_match is None or wind_match.group(1) == "VRB" else float(wind_match.group(1))
    wind_speed = None if wind_match is None else float(wind_match.group(2))
    qnh_match = _QNH_HPA_RE.search(raw)
    altimeter_match = _ALTIMETER_INHG_RE.search(raw)
    pressure_hpa = (
        float(qnh_match.group(1))
        if qnh_match is not None
        else (
            round(float(altimeter_match.group(1)) / 100.0 * 33.8638866667, 3)
            if altimeter_match is not None
            else None
        )
    )
    return {
        "present_weather_codes": "|".join(wx_tokens),
        "precip_state": precip_state,
        "precip_intensity_code": intensity,
        "precip_observed": has_precip,
        "thunderstorm_observed": thunder,
        "freezing_precip_observed": freezing,
        "cloud_layer_count": len(layers) if raw else None,
        "lowest_cloud_base_ft_agl": min(bases) if bases else None,
        "ceiling_ft_agl": min(ceilings) if ceilings else None,
        "metar_wind_dir_deg": wind_dir,
        "metar_wind_speed_kt": wind_speed,
        "pressure_hpa": pressure_hpa,
    }


def observation_clock_features(record: Mapping[str, Any]) -> dict[str, Any]:
    cadence = _first_float(
        record,
        "expected_report_cadence",
        "observation_cadence_min",
        "cadence_min",
        "estimated_cadence_min",
    )
    report = _parse_utc(record.get("source_report_ts_utc") or record.get("last_obs_utc"))
    decision = _parse_utc(record.get("decision_snapshot_ts_utc") or record.get("as_of_ts_utc"))
    detect = _parse_utc(record.get("detect_ts_utc") or record.get("fetched_at_utc"))
    age = (decision - report).total_seconds() / 60.0 if report and decision else None
    if age is None:
        age = _first_float(record, "obs_age_minutes", "obs_age_min", "age_min")
    latency = (detect - report).total_seconds() / 60.0 if detect and report else None
    return {
        "obs_age_minutes": age,
        "expected_report_cadence": cadence,
        "obs_cadence_ratio": age / cadence if age is not None and cadence and cadence > 0 else None,
        # Keep this signed.  A negative value is useful PIT information: the
        # expected report is overdue, which is not equivalent to "due now".
        "minutes_to_next_expected_obs": cadence - age if age is not None and cadence is not None else None,
        "source_latency_minutes": latency,
    }


def _solar_elevation(ts: datetime, latitude: float, longitude: float) -> tuple[float, float, float]:
    day = ts.timetuple().tm_yday
    hour = ts.hour + ts.minute / 60.0 + ts.second / 3600.0
    gamma = 2.0 * math.pi / 365.0 * (day - 1 + (hour - 12.0) / 24.0)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )
    solar_minutes = (hour * 60.0 + eqtime + 4.0 * longitude) % 1440.0
    hour_angle = math.radians(solar_minutes / 4.0 - 180.0)
    lat = math.radians(latitude)
    cos_zenith = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(hour_angle)
    elevation = 90.0 - math.degrees(math.acos(max(-1.0, min(1.0, cos_zenith))))
    return elevation, eqtime, decl


def solar_geometry_features(record: Mapping[str, Any]) -> dict[str, Any]:
    ts = _parse_utc(record.get("decision_snapshot_ts_utc") or record.get("as_of_ts_utc"))
    lat = _first_float(record, "latitude", "station_lat", "lat")
    lon = _first_float(record, "longitude", "station_lon", "lon")
    if ts is None or lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return {
            "solar_geometry_status": "missing_timestamp_or_coordinates",
            "solar_elevation_deg": None,
            "solar_elevation_2h_deg": None,
            "solar_elevation_delta_2h_deg": None,
            "daylight_remaining_minutes": None,
            "solar_heating_potential": None,
        }
    elevation, eqtime, decl = _solar_elevation(ts, lat, lon)
    elevation_2h, _eq2, _decl2 = _solar_elevation(ts + timedelta(hours=2), lat, lon)
    lat_rad = math.radians(lat)
    denom = math.cos(lat_rad) * math.cos(decl)
    cos_sunset = (math.cos(math.radians(90.833)) / denom - math.tan(lat_rad) * math.tan(decl)) if denom else 2.0
    if cos_sunset < -1:
        remaining, status = 1440.0, "polar_day"
    elif cos_sunset > 1:
        remaining, status = 0.0, "polar_night"
    else:
        sunset_utc_min = 720.0 - 4.0 * lon - eqtime + 4.0 * math.degrees(math.acos(cos_sunset))
        now_utc_min = ts.hour * 60.0 + ts.minute + ts.second / 60.0
        remaining, status = max(0.0, sunset_utc_min - now_utc_min), "ok"
    return {
        "solar_geometry_status": status,
        "solar_elevation_deg": round(elevation, 4),
        "solar_elevation_2h_deg": round(elevation_2h, 4),
        "solar_elevation_delta_2h_deg": round(elevation_2h - elevation, 4),
        "daylight_remaining_minutes": round(remaining, 3),
        "solar_heating_potential": round(max(0.0, math.sin(math.radians(elevation))), 6),
    }


def forecast_window_features(record: Mapping[str, Any]) -> dict[str, Any]:
    curve = record.get("hourly_curve") or record.get("forecast_hourly_curve") or []
    decision_hour = _first_float(record, "decision_hour_local", "decision_hour_local_float")
    peak_hour = _first_float(record, "forecast_peak_hour_local")
    selected: list[Mapping[str, Any]] = []
    remaining_3h: list[Mapping[str, Any]] = []
    future_3h: list[Mapping[str, Any]] = []
    parsed_curve: list[tuple[float, Mapping[str, Any]]] = []
    target_date = str(record.get("target_date") or "").strip()
    peak_passed = decision_hour is not None and peak_hour is not None and peak_hour < decision_hour
    if isinstance(curve, Sequence) and not isinstance(curve, (str, bytes)) and decision_hour is not None:
        remaining_lo = math.floor(decision_hour)
        remaining_hi = decision_hour + 3.0
        for item in curve:
            if not isinstance(item, Mapping):
                continue
            time_local = str(item.get("time_local") or "")
            if target_date and len(time_local) >= 10 and time_local[:10] != target_date:
                continue
            try:
                hour = float(time_local[11:13]) + float(time_local[14:16]) / 60.0
            except (ValueError, IndexError):
                continue
            parsed_curve.append((hour, item))
            if peak_hour is not None and not peak_passed and decision_hour <= hour <= peak_hour:
                selected.append(item)
            # Compatibility: preserve the legacy "remaining_3h" fields,
            # which include the current floor hour.  Strictly future weather
            # uses the explicit future_3h names below.
            if remaining_lo <= hour <= remaining_hi:
                remaining_3h.append(item)
            if decision_hour <= hour <= remaining_hi:
                future_3h.append(item)
    parsed_curve.sort(key=lambda item: item[0])

    def values(rows: Sequence[Mapping[str, Any]], *keys: str) -> list[float]:
        out: list[float] = []
        for item in rows:
            value = _first_float(item, *keys)
            if value is not None:
                out.append(value)
        return out

    precip = values(selected, "precipitation_probability_pct", "precipitation_probability")
    cloud = values(selected, "cloud_cover_pct", "cloud_cover")
    wind = values(selected, "wind_speed_10m_kt", "wind_speed_10m")
    direction = values(selected, "wind_direction_10m_deg", "wind_direction_10m")
    remaining_precip = values(remaining_3h, "precipitation_probability_pct", "precipitation_probability")
    remaining_cloud = values(remaining_3h, "cloud_cover_pct", "cloud_cover")
    remaining_wind = values(remaining_3h, "wind_speed_10m_kt", "wind_speed_10m")
    remaining_direction = values(remaining_3h, "wind_direction_10m_deg", "wind_direction_10m")
    future_precip = values(future_3h, "precipitation_probability_pct", "precipitation_probability")
    future_cloud = values(future_3h, "cloud_cover_pct", "cloud_cover")
    future_wind = values(future_3h, "wind_speed_10m_kt", "wind_speed_10m")
    future_direction = values(future_3h, "wind_direction_10m_deg", "wind_direction_10m")
    temperature_points = [
        (hour, value)
        for hour, item in parsed_curve
        if (value := _first_float(item, "temperature_f")) is not None
    ]
    temperature_at_decision = _linear_value_at(temperature_points, decision_hour)
    future_temperatures = [
        (hour, value)
        for hour, value in temperature_points
        if decision_hour is not None and hour >= decision_hour
    ]
    remaining_temperature_points = list(future_temperatures)
    if decision_hour is not None and temperature_at_decision is not None:
        remaining_temperature_points.append((decision_hour, temperature_at_decision))
    remaining_max = (
        max(value for _hour, value in remaining_temperature_points)
        if remaining_temperature_points
        else None
    )
    remaining_peak_hour = None
    if remaining_max is not None:
        remaining_peak_hour = min(
            hour
            for hour, value in remaining_temperature_points
            if abs(value - remaining_max) < 1e-9
        )
    remaining_delta = (
        None
        if remaining_max is None or temperature_at_decision is None
        else remaining_max - temperature_at_decision
    )
    status = (
        "forecast_peak_passed"
        if peak_passed
        else "ok"
        if selected and any((precip, cloud, wind, direction))
        else "missing_weather_hourly_curve"
    )
    return {
        "forecast_weather_window_status": status,
        "forecast_weather_window_hour_count": len(selected),
        "forecast_precip_probability_to_peak_max_pct": max(precip) if precip else None,
        "forecast_cloud_cover_to_peak_mean_pct": sum(cloud) / len(cloud) if cloud else None,
        "forecast_wind_speed_to_peak_max_kt": max(wind) if wind else None,
        "forecast_wind_direction_to_peak_mean_deg": _circular_mean(direction),
        "forecast_remaining_3h_status": "ok" if remaining_3h else "missing_weather_hourly_curve",
        "forecast_remaining_3h_hour_count": len(remaining_3h),
        "forecast_precip_probability_remaining_3h_max_pct": max(remaining_precip) if remaining_precip else None,
        "forecast_cloud_cover_remaining_3h_mean_pct": (
            sum(remaining_cloud) / len(remaining_cloud) if remaining_cloud else None
        ),
        "forecast_wind_speed_remaining_3h_max_kt": max(remaining_wind) if remaining_wind else None,
        "forecast_wind_direction_remaining_3h_mean_deg": _circular_mean(remaining_direction),
        "forecast_future_3h_status": "ok" if future_3h else "missing_weather_hourly_curve",
        "forecast_future_3h_hour_count": len(future_3h),
        "forecast_precip_probability_future_3h_max_pct": max(future_precip) if future_precip else None,
        "forecast_cloud_cover_future_3h_mean_pct": sum(future_cloud) / len(future_cloud) if future_cloud else None,
        "forecast_wind_speed_future_3h_max_kt": max(future_wind) if future_wind else None,
        "forecast_wind_direction_future_3h_mean_deg": _circular_mean(future_direction),
        "forecast_temperature_at_decision_f": temperature_at_decision,
        "forecast_remaining_max_f": remaining_max,
        "forecast_remaining_temp_delta_f": remaining_delta,
        "forecast_reheat_after_now_f": None if remaining_delta is None else max(0.0, remaining_delta),
        "forecast_remaining_peak_hour_local": remaining_peak_hour,
    }


def _linear_value_at(points: Sequence[tuple[float, float]], target: float | None) -> float | None:
    if target is None or not points:
        return None
    ordered = sorted(points)
    for hour, value in ordered:
        if abs(hour - target) < 1e-9:
            return value
    for (left_hour, left_value), (right_hour, right_value) in zip(ordered, ordered[1:]):
        if left_hour < target < right_hour:
            weight = (target - left_hour) / (right_hour - left_hour)
            return left_value + weight * (right_value - left_value)
    return None


def _circular_mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    sin_mean = sum(math.sin(math.radians(value)) for value in values) / len(values)
    cos_mean = sum(math.cos(math.radians(value)) for value in values) / len(values)
    return math.degrees(math.atan2(sin_mean, cos_mean)) % 360.0


def physical_context_features(record: Mapping[str, Any]) -> dict[str, Any]:
    metar = metar_physical_features(
        record.get("raw_metar") or record.get("raw_text"),
        record.get("present_weather") or record.get("wx_string") or record.get("wx_phrase"),
    )
    wind_dir = _first_float(record, "wind_dir_deg", "wind_direction_deg")
    if wind_dir is None:
        wind_dir = metar["metar_wind_dir_deg"]
    wind_speed = _first_float(record, "wind_speed_kt")
    if wind_speed is None:
        wind_speed = metar["metar_wind_speed_kt"]
    wind_rad = math.radians(wind_dir) if wind_dir is not None else None
    forecast = forecast_window_features(record)
    remaining_max_f = _finite(forecast.get("forecast_remaining_max_f"))
    forecast_temperature_at_decision_f = _finite(
        forecast.get("forecast_temperature_at_decision_f")
    )
    unit = str(record.get("unit") or "F").upper()
    remaining_max_native = (
        None
        if remaining_max_f is None
        else remaining_max_f
        if unit == "F"
        else (remaining_max_f - 32.0) * 5.0 / 9.0
    )
    running_native = _first_float(record, "running_max_native", "running_native")
    current_native = _first_float(record, "current_temp_native", "current_native")
    current_f = _first_float(
        record,
        "current_temp_f",
        "current_f",
        "tmpf_now",
        "temperature_f",
    )
    if current_f is None and current_native is not None:
        current_f = current_native if unit == "F" else current_native * 9.0 / 5.0 + 32.0
    forecast_temperature_innovation_f = (
        None
        if current_f is None or forecast_temperature_at_decision_f is None
        else current_f - forecast_temperature_at_decision_f
    )
    forecast_temperature_innovation_native = (
        None
        if forecast_temperature_innovation_f is None
        else forecast_temperature_innovation_f
        if unit == "F"
        else forecast_temperature_innovation_f * 5.0 / 9.0
    )
    if current_f is None:
        forecast_temperature_innovation_status = "missing_current_temperature"
    elif forecast_temperature_at_decision_f is None:
        forecast_temperature_innovation_status = (
            "missing_forecast_temperature_at_decision"
        )
    else:
        forecast_temperature_innovation_status = "ok"
    return {
        **{key: value for key, value in metar.items() if not key.startswith("metar_")},
        "wind_dir_deg": wind_dir,
        "wind_speed_kt": wind_speed,
        "wind_dir_sin": math.sin(wind_rad) if wind_rad is not None else None,
        "wind_dir_cos": math.cos(wind_rad) if wind_rad is not None else None,
        "cloud_cover_change_1h_code": _first_float(record, "cloud_cover_change_1h_code", "d_sky_1h"),
        "ceiling_change_1h_ft": _first_float(record, "ceiling_change_1h_ft"),
        "wind_speed_change_1h_kt": _first_float(record, "wind_speed_change_1h_kt", "d_wind_speed_1h_kt"),
        "wind_dir_1h_prior_deg": _first_float(record, "wind_dir_1h_prior_deg"),
        "wind_dir_change_1h_deg": _first_float(record, "wind_dir_change_1h_deg"),
        "dewpoint_change_1h_f": _first_float(record, "dewpoint_change_1h_f", "d_dwpf_1h"),
        "dewpoint_trend_1h_f": _first_float(record, "dewpoint_trend_1h_f", "dewpoint_change_1h_f", "d_dwpf_1h"),
        "dewpoint_trend_3h_f": _first_float(record, "dewpoint_trend_3h_f", "d_dwpf_3h"),
        "relative_humidity_change_1h_pct": _first_float(record, "relative_humidity_change_1h_pct"),
        **observation_clock_features(record),
        **solar_geometry_features(record),
        **forecast,
        "forecast_temperature_innovation_status": forecast_temperature_innovation_status,
        "forecast_temperature_innovation_f": forecast_temperature_innovation_f,
        "forecast_temperature_innovation_native": forecast_temperature_innovation_native,
        "forecast_remaining_gap_to_running_native": (
            None if remaining_max_native is None or running_native is None else remaining_max_native - running_native
        ),
        "forecast_remaining_gap_to_current_native": (
            None if remaining_max_native is None or current_native is None else remaining_max_native - current_native
        ),
    }
