"""Shared PIT feature builders for the Helsinki remaining-heat model.

The formulas in this module mirror the frozen three-year FMI training feature
contract.  Both research replay and the WCIR runtime should call this module so
that a feature name cannot silently acquire a second live definition.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import numpy as np


LOCAL_TZ = ZoneInfo("Europe/Helsinki")
EPS = 1e-6


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _timestamp(row: dict[str, Any]) -> datetime:
    value = str(row["observation_time_utc"]).replace("Z", "+00:00")
    return datetime.fromisoformat(value)


def _raw_value(row: dict[str, Any], *names: str) -> float | None:
    for name in names:
        value = _finite(row.get(name))
        if value is not None:
            return value
    return None


def _lag_value(
    rows: list[dict[str, Any]], timestamp: datetime, field: str, minutes: int
) -> float | None:
    cutoff = timestamp - timedelta(minutes=minutes)
    for row in rows:
        row_time = _timestamp(row)
        if cutoff <= row_time < timestamp:
            value = _raw_value(row, field)
            if value is not None:
                return value
    return None


def _trailing_values(
    rows: list[dict[str, Any]], timestamp: datetime, field: str, minutes: int
) -> list[float]:
    cutoff = timestamp - timedelta(minutes=minutes)
    output: list[float] = []
    for row in rows:
        if not cutoff <= _timestamp(row) <= timestamp:
            continue
        value = _raw_value(row, field)
        if value is not None:
            output.append(value)
    return output


def solar_elevation_approx(timestamp: datetime) -> float:
    """Frozen deterministic clock feature used by the three-year model."""

    local = timestamp.astimezone(LOCAL_TZ)
    day = local.timetuple().tm_yday
    decl = math.radians(23.44 * math.sin(2 * math.pi * (284 + day) / 365.0))
    lat = math.radians(60.3267)
    hour_angle = math.radians(
        15.0 * ((local.hour + local.minute / 60.0) - 12.3)
    )
    sin_alt = (
        math.sin(lat) * math.sin(decl)
        + math.cos(lat) * math.cos(decl) * math.cos(hour_angle)
    )
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))


def build_fmi_remaining_heat_features(
    history: Iterable[dict[str, Any]],
    *,
    official_running_max_c: float,
) -> dict[str, Any]:
    """Build the frozen A0-A3/A5 feature groups as of the latest FMI row."""

    rows = sorted(list(history), key=_timestamp)
    if not rows:
        raise ValueError("FMI history is empty")
    current = rows[-1]
    timestamp = _timestamp(current)
    temps = [_finite(row.get("temp_c")) for row in rows]
    if any(value is None for value in temps):
        raise ValueError("FMI history contains a row without temp_c")
    temp_values = [float(value) for value in temps if value is not None]
    temp = temp_values[-1]
    fmi_running_max = max(temp_values)
    local = timestamp.astimezone(LOCAL_TZ)
    hour = local.hour + local.minute / 60.0
    day = local.timetuple().tm_yday

    previous_rows = rows[:-1]
    lag30_temp = _lag_value(previous_rows, timestamp, "temp_c", 30)
    lag60_temp = _lag_value(previous_rows, timestamp, "temp_c", 60)
    pressure = _raw_value(current, "pressure_hpa")
    lag30_pressure = _lag_value(previous_rows, timestamp, "pressure_hpa", 30)
    glob = _raw_value(current, "global_radiation_wm2")
    diff = _raw_value(current, "diffuse_radiation_wm2")
    lwin = _raw_value(current, "longwave_in_wm2")
    lwout = _raw_value(current, "longwave_out_wm2")
    refl = _raw_value(current, "reflected_radiation_wm2")
    lag10_glob = _lag_value(previous_rows, timestamp, "global_radiation_wm2", 10)
    lag30_glob = _lag_value(previous_rows, timestamp, "global_radiation_wm2", 30)
    wind_speed = _raw_value(current, "wind_speed_ms")
    wind_dir = _raw_value(current, "wind_dir_deg")
    dewpoint = _raw_value(current, "dewpoint_c")

    deltas = [
        temp_values[index] - temp_values[index - 1]
        for index in range(1, len(temp_values))
    ]
    warming_run = 0
    for delta in reversed(deltas):
        if delta <= 0:
            break
        warming_run += 1
    last_high_index = 0
    running = -math.inf
    for index, value in enumerate(temp_values):
        if value > running + EPS:
            running = value
            last_high_index = index
    minutes_since_high = (
        timestamp - _timestamp(rows[last_high_index])
    ).total_seconds() / 60.0

    last_7_temps = temp_values[-7:]
    recent_high_count = sum(
        value >= max(last_7_temps) - 0.05 for value in last_7_temps
    )
    last_6_deltas = deltas[-6:]
    same_value_run = 1
    for value in reversed(temp_values[:-1]):
        if value != temp:
            break
        same_value_run += 1
    cadence = (
        (timestamp - _timestamp(rows[-2])).total_seconds() / 60.0
        if len(rows) > 1
        else 10.0
    )

    glob_30 = _trailing_values(rows, timestamp, "global_radiation_wm2", 30)
    glob_last_6 = [
        value
        for row in rows[-6:]
        if (value := _raw_value(row, "global_radiation_wm2")) is not None
    ]
    glob_last_3 = [
        value
        for row in rows[-3:]
        if (value := _raw_value(row, "global_radiation_wm2")) is not None
    ]
    sunshine_30 = _trailing_values(rows, timestamp, "sunshine_seconds", 30)

    def row_delta(field: str, periods: int = 3) -> float | None:
        if len(rows) <= periods:
            return None
        now_value = _raw_value(current, field)
        old_value = _raw_value(rows[-periods - 1], field)
        if now_value is None or old_value is None:
            return None
        return now_value - old_value

    solar = solar_elevation_approx(timestamp)
    implied_lattice = math.floor(temp + 0.5)
    result = {
        "official_running_max_c": float(official_running_max_c),
        "fmi_running_max_c": fmi_running_max,
        "pullback_depth_c": fmi_running_max - temp,
        "distance_to_next_official_boundary_c": official_running_max_c + 0.5 - temp,
        "fmi_official_lattice_basis_c": implied_lattice - official_running_max_c,
        "local_hour_sin": math.sin(2 * math.pi * hour / 24),
        "local_hour_cos": math.cos(2 * math.pi * hour / 24),
        "doy_sin": math.sin(2 * math.pi * day / 365.25),
        "doy_cos": math.cos(2 * math.pi * day / 365.25),
        "solar_elevation_deg": solar,
        "remaining_daylight_proxy": max(0.0, solar),
        "temp_delta_10m": deltas[-1] if deltas else None,
        "temp_delta_20m": temp - temp_values[-3] if len(temp_values) >= 3 else None,
        "temp_slope_30m_cph": None if lag30_temp is None else (temp - lag30_temp) * 2.0,
        "temp_slope_60m_cph": None if lag60_temp is None else temp - lag60_temp,
        "temp_acceleration_20m": (
            deltas[-1] - deltas[-2] if len(deltas) >= 2 else None
        ),
        "warming_run_count": warming_run,
        "minutes_since_strict_high": minutes_since_high,
        "plateau_duration_min": minutes_since_high,
        "recent_high_count_60m": float(recent_high_count),
        "path_volatility_60m": (
            float(np.std(last_6_deltas, ddof=0)) if last_6_deltas else None
        ),
        "global_radiation_wm2": glob,
        "diffuse_radiation_wm2": diff,
        "diffuse_fraction": (
            None if glob is None or diff is None or glob <= 5 else diff / glob
        ),
        "longwave_in_wm2": lwin,
        "longwave_out_wm2": lwout,
        "reflected_radiation_wm2": refl,
        "radiation_balance_proxy_wm2": (
            None
            if any(value is None for value in (glob, lwin, lwout, refl))
            else glob + lwin - lwout - refl
        ),
        "sunshine_seconds": _raw_value(current, "sunshine_seconds"),
        "global_radiation_delta_10m": (
            None if glob is None or lag10_glob is None else glob - lag10_glob
        ),
        "global_radiation_slope_30m": (
            None if glob is None or lag30_glob is None else (glob - lag30_glob) * 2.0
        ),
        "global_radiation_mean_30m": (
            float(np.mean(glob_30)) if glob_30 else None
        ),
        "global_radiation_mean_60m": (
            float(np.mean(glob_last_6)) if glob_last_6 else None
        ),
        "radiation_integral_30m": (
            float(np.sum(glob_last_3)) / 6.0 if glob_last_3 else None
        ),
        "radiation_integral_60m": (
            float(np.sum(glob_last_6)) / 6.0 if glob_last_6 else None
        ),
        "sunshine_mean_30m": (
            float(np.mean(sunshine_30)) if sunshine_30 else None
        ),
        "relative_humidity_pct": _raw_value(current, "relative_humidity_pct"),
        "relative_humidity_delta_30m": row_delta("relative_humidity_pct"),
        "dewpoint_depression_c": (
            _raw_value(current, "dewpoint_depression_c")
            if _raw_value(current, "dewpoint_depression_c") is not None
            else None if dewpoint is None else temp - dewpoint
        ),
        "dewpoint_depression_delta_30m": row_delta("dewpoint_depression_c"),
        "wind_speed_ms": wind_speed,
        "wind_speed_delta_30m": row_delta("wind_speed_ms"),
        "wind_gust_ms": _raw_value(current, "wind_gust_ms"),
        "wind_u_ms": (
            None
            if wind_speed is None or wind_dir is None
            else -wind_speed * math.sin(math.radians(wind_dir))
        ),
        "wind_v_ms": (
            None
            if wind_speed is None or wind_dir is None
            else -wind_speed * math.cos(math.radians(wind_dir))
        ),
        "pressure_hpa": pressure,
        "pressure_delta_30m": (
            None
            if pressure is None or lag30_pressure is None
            else pressure - lag30_pressure
        ),
        "precipitation_10m_mm": _raw_value(current, "precipitation_10m_mm"),
        "precipitation_1h_mm": _raw_value(current, "precipitation_1h_mm"),
        "visibility_m": _raw_value(current, "visibility_m"),
        "cloud_cover_okta": _raw_value(current, "cloud_cover_okta"),
        "cloud_cover_delta_30m": row_delta("cloud_cover_okta"),
        "present_weather_code": _raw_value(current, "present_weather_code"),
        "source_cadence_gap_min": cadence,
        "source_to_official_level_basis_c": temp - official_running_max_c,
        "same_value_run_count": float(same_value_run),
        "distinct_print_count_60m": float(len(set(last_7_temps))),
        "single_print_state": float(same_value_run == 1),
        "terminal_false_risk_proxy": float(
            implied_lattice > official_running_max_c and same_value_run == 1
        ),
    }
    return result


def _interp(times: np.ndarray, values: np.ndarray, target: float) -> float:
    return float(np.interp(target, times, values))


def _time_integral(hours: np.ndarray, values: np.ndarray) -> float:
    return float(np.trapezoid(values, hours)) if len(values) > 1 else 0.0


def _first_cross_minutes(
    hours: np.ndarray, temperatures: np.ndarray, boundary: float
) -> float | None:
    if temperatures[0] >= boundary:
        return 0.0
    for index in range(1, len(temperatures)):
        left, right = temperatures[index - 1], temperatures[index]
        if left < boundary <= right:
            fraction = (boundary - left) / (right - left) if right != left else 1.0
            return float((hours[index - 1] + fraction * (hours[index] - hours[index - 1])) * 60)
    return None


def build_forecast_remaining_heat_features(
    row: dict[str, Any],
    *,
    decision: datetime,
    official_running_max_c: float,
    current_temp_c: float,
) -> dict[str, Any]:
    """Build the frozen v2-v3 forecast feature contract from one PIT curve."""

    local_now = decision.astimezone(LOCAL_TZ).replace(tzinfo=None)
    curve = list(row["hourly_curve"])
    times = [datetime.fromisoformat(str(item["time_local"])) for item in curve]
    hours = np.asarray(
        [(timestamp - local_now).total_seconds() / 3600 for timestamp in times],
        dtype=float,
    )
    temperatures = np.asarray(
        [(float(item["temperature_f"]) - 32.0) * 5.0 / 9.0 for item in curve],
        dtype=float,
    )
    if not len(hours):
        raise ValueError("forecast curve is empty")
    current_forecast = _interp(hours, temperatures, 0.0)
    future_grid = hours >= 0
    future_hours_grid = hours[future_grid]
    future_temperatures_grid = temperatures[future_grid]
    if not future_grid.any():
        future_hours_grid = np.asarray([0.0])
        future_temperatures_grid = np.asarray([current_forecast])
    path_hours = np.concatenate(([0.0], hours[hours > 0]))
    path_temperatures = np.concatenate(([current_forecast], temperatures[hours > 0]))
    day_peak_index = int(np.argmax(temperatures))
    future_peak_index = int(np.argmax(future_temperatures_grid))
    day_peak = float(temperatures[day_peak_index])
    future_peak = float(future_temperatures_grid[future_peak_index])
    signed_peak_minutes = float(hours[day_peak_index] * 60)
    boundary = official_running_max_c + 0.5
    excess_grid = np.maximum(future_temperatures_grid - boundary, 0.0)
    path_excess = np.maximum(path_temperatures - boundary, 0.0)
    above_path = path_temperatures >= boundary
    radiation = np.asarray(
        [
            np.nan if item.get("shortwave_radiation_wm2") is None else float(item["shortwave_radiation_wm2"])
            for item in curve
        ],
        dtype=float,
    )[future_grid]
    cloud = np.asarray(
        [np.nan if item.get("cloud_cover_pct") is None else float(item["cloud_cover_pct"]) for item in curve],
        dtype=float,
    )[future_grid]
    wind = np.asarray(
        [np.nan if item.get("wind_speed_10m_kt") is None else float(item["wind_speed_10m_kt"]) * 1.852 for item in curve],
        dtype=float,
    )[future_grid]
    differences = np.diff(path_temperatures)
    segment_hours = np.diff(path_hours)
    running_min = np.minimum.accumulate(path_temperatures)
    first_cross = _first_cross_minutes(path_hours, path_temperatures, boundary)
    last_hold = (
        float(path_hours[np.flatnonzero(above_path)[-1]] * 60)
        if above_path.any()
        else None
    )

    def horizon_temperature(value: float) -> float | None:
        if value > float(hours[-1]):
            return None
        return _interp(hours, temperatures, value)

    temp_1h = horizon_temperature(1.0)
    temp_2h = horizon_temperature(2.0)
    available = datetime.fromisoformat(
        str(row["available_at_utc"]).replace("Z", "+00:00")
    )
    return {
        "forecast_available": 1.0,
        "forecast_run_age_h": (decision - available).total_seconds() / 3600.0,
        "forecast_current_innovation_c": current_temp_c - current_forecast,
        "forecast_day_peak_margin_vs_running_c": day_peak - official_running_max_c,
        "forecast_future_peak_margin_vs_running_c": future_peak - official_running_max_c,
        "forecast_future_peak_margin_vs_boundary_c": future_peak - boundary,
        "forecast_signed_minutes_to_day_peak": signed_peak_minutes,
        "forecast_minutes_to_future_peak": float(future_hours_grid[future_peak_index] * 60),
        "forecast_future_heat_area_above_boundary": float(np.nansum(excess_grid)),
        "forecast_future_hours_above_boundary": float(np.sum(future_temperatures_grid >= boundary)),
        "forecast_future_radiation_sum_kwhm2": (
            float(np.nansum(radiation) / 1000.0) if np.isfinite(radiation).any() else None
        ),
        "forecast_future_cloud_mean_pct": float(np.nanmean(cloud)) if np.isfinite(cloud).any() else None,
        "forecast_future_wind_mean_kmh": float(np.nanmean(wind)) if np.isfinite(wind).any() else None,
        "forecast_day_peak_passed": float(signed_peak_minutes < 0),
        "forecast_minutes_since_day_peak": max(-signed_peak_minutes, 0.0),
        "forecast_minutes_until_day_peak": max(signed_peak_minutes, 0.0),
        "forecast_future_peak_discount_from_day_peak_c": future_peak - day_peak,
        "forecast_first_boundary_cross_minutes": first_cross,
        "forecast_last_boundary_hold_minutes": last_hold,
        "forecast_warming_slope_1h_cph": None if temp_1h is None else temp_1h - current_forecast,
        "forecast_warming_slope_2h_cph": None if temp_2h is None else (temp_2h - current_forecast) / 2.0,
        "forecast_future_positive_slope_hours": float(np.sum(segment_hours[differences > 0])),
        "forecast_future_reheat_strength_c": float(np.max(path_temperatures - running_min)),
        "forecast_future_peak_drop_to_eod_c": future_peak - float(path_temperatures[-1]),
        "forecast_future_heat_integral_c_h": _time_integral(path_hours, path_excess),
        "forecast_future_above_boundary_duration_h": _time_integral(path_hours, above_path.astype(float)),
    }
