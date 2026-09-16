"""PIT features from an immutable fixed-lead hourly temperature curve."""

from __future__ import annotations

import numpy as np
import pandas as pd


FORECAST_PATH_FEATURES = [
    "forecast_curve_hour_count", "forecast_curve_complete", "forecast_now_c",
    "forecast_day_max_c", "forecast_remaining_max_c", "forecast_peak_minute_local",
    "forecast_minutes_to_peak", "forecast_peak_hour_sin", "forecast_peak_hour_cos",
    "forecast_peak_passed", "forecast_peak_outside_13_15_minutes",
    "forecast_temp_delta_60m_c", "forecast_temp_delta_120m_c",
    "forecast_temp_delta_180m_c", "forecast_future_warming_integral_6h_c_h",
    "forecast_now_error_c", "forecast_remaining_max_minus_ta_c",
    "forecast_remaining_max_minus_d1_c", "forecast_day_max_minus_d1_c",
    "forecast_bias_adjusted_remaining_max_minus_d1_c",
]


def _minute(value: object) -> float:
    try:
        text = str(value)
        return float(int(text[11:13]) * 60 + int(text[14:16]))
    except (TypeError, ValueError):
        return np.nan


def add_fixed_lead_forecast_path_features(
    frame: pd.DataFrame, forecast_hourly: pd.DataFrame
) -> pd.DataFrame:
    required = {"target_date", "decision_minute_local", "ta_c", "d1_bracket_c"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"checkpoint frame missing forecast-path inputs: {missing}")
    output = frame.copy()
    output["target_date"] = output["target_date"].astype(str)
    for name in FORECAST_PATH_FEATURES:
        output[name] = np.nan
    output["forecast_curve_hour_count"] = 0.0
    output["forecast_curve_complete"] = 0.0
    curves = forecast_hourly.copy()
    curves["target_date"] = curves["target_date"].astype(str)
    curves["_minute"] = curves["forecast_time_local"].map(_minute)
    curves["_temperature"] = pd.to_numeric(curves["forecast_temperature_c"], errors="coerce")
    curves = curves.dropna(subset=["_minute", "_temperature"])
    for target_date, curve in curves.groupby("target_date", sort=False):
        index = output.index[output["target_date"].eq(target_date)]
        if index.empty:
            continue
        curve = curve.sort_values("_minute").drop_duplicates("_minute", keep="last")
        minutes = curve["_minute"].to_numpy(float)
        temperatures = curve["_temperature"].to_numpy(float)
        if len(minutes) < 2:
            continue
        checkpoints = pd.to_numeric(output.loc[index, "decision_minute_local"], errors="coerce").to_numpy(float)
        now = np.interp(np.clip(checkpoints, minutes[0], minutes[-1]), minutes, temperatures)
        now[~np.isfinite(checkpoints)] = np.nan
        grid = np.arange(1440, dtype=float)
        values = np.interp(grid, minutes, temperatures)
        remaining_grid = np.maximum.accumulate(values[::-1])[::-1]
        grid_index = np.clip(np.nan_to_num(checkpoints).astype(int), 0, 1439)
        remaining = remaining_grid[grid_index]
        peak = float(np.max(temperatures))
        peak_minute = float(minutes[np.flatnonzero(np.isclose(temperatures, peak))[0]])

        future = []
        for horizon in range(60, 361, 60):
            target = checkpoints + horizon
            value = np.interp(np.clip(target, minutes[0], minutes[-1]), minutes, temperatures)
            value[target > minutes[-1]] = np.nan
            future.append(value - now)
        future_matrix = np.vstack(future)
        warming = np.nansum(np.maximum(future_matrix, 0), axis=0)
        warming[np.all(~np.isfinite(future_matrix), axis=0)] = 0.0
        ta = pd.to_numeric(output.loc[index, "ta_c"], errors="coerce").to_numpy(float)
        d1 = pd.to_numeric(output.loc[index, "d1_bracket_c"], errors="coerce").to_numpy(float)
        now_error = ta - now
        angle = 2*np.pi*peak_minute/1440
        feature_values = {
            "forecast_curve_hour_count": np.full(len(index), float(len(minutes))),
            "forecast_curve_complete": np.full(len(index), float(len(minutes) == 24)),
            "forecast_now_c": now, "forecast_day_max_c": np.full(len(index), peak),
            "forecast_remaining_max_c": remaining,
            "forecast_peak_minute_local": np.full(len(index), peak_minute),
            "forecast_minutes_to_peak": peak_minute-checkpoints,
            "forecast_peak_hour_sin": np.full(len(index), np.sin(angle)),
            "forecast_peak_hour_cos": np.full(len(index), np.cos(angle)),
            "forecast_peak_passed": (checkpoints > peak_minute).astype(float),
            "forecast_peak_outside_13_15_minutes": np.full(len(index), max(780-peak_minute, peak_minute-900, 0)),
            "forecast_temp_delta_60m_c": future_matrix[0],
            "forecast_temp_delta_120m_c": future_matrix[1],
            "forecast_temp_delta_180m_c": future_matrix[2],
            "forecast_future_warming_integral_6h_c_h": warming,
            "forecast_now_error_c": now_error,
            "forecast_remaining_max_minus_ta_c": remaining-ta,
            "forecast_remaining_max_minus_d1_c": remaining-d1,
            "forecast_day_max_minus_d1_c": peak-d1,
            "forecast_bias_adjusted_remaining_max_minus_d1_c": remaining+now_error-d1,
        }
        for name, value in feature_values.items():
            output.loc[index, name] = value
    return output
