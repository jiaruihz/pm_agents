"""Deterministic Amsterdam solar-geometry features for remaining-heat models."""

from __future__ import annotations

import numpy as np
import pandas as pd


SCHIPHOL_LATITUDE_DEG = 52.3105
SCHIPHOL_LONGITUDE_DEG = 4.7683


def add_solar_geometry_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Add observation-time-only solar geometry and remaining-heating proxies."""

    output = frame.copy()
    timestamps = pd.to_datetime(output["observed_at_utc"], utc=True)
    day = timestamps.dt.dayofyear.to_numpy(float)
    utc_hour = (
        timestamps.dt.hour.to_numpy(float)
        + timestamps.dt.minute.to_numpy(float) / 60.0
        + timestamps.dt.second.to_numpy(float) / 3600.0
    )
    gamma = 2.0 * np.pi / 365.0 * (day - 1.0 + (utc_hour - 12.0) / 24.0)
    equation_of_time = 229.18 * (
        0.000075
        + 0.001868 * np.cos(gamma)
        - 0.032077 * np.sin(gamma)
        - 0.014615 * np.cos(2.0 * gamma)
        - 0.040849 * np.sin(2.0 * gamma)
    )
    declination = (
        0.006918
        - 0.399912 * np.cos(gamma)
        + 0.070257 * np.sin(gamma)
        - 0.006758 * np.cos(2.0 * gamma)
        + 0.000907 * np.sin(2.0 * gamma)
        - 0.002697 * np.cos(3.0 * gamma)
        + 0.00148 * np.sin(3.0 * gamma)
    )
    utc_minute = utc_hour * 60.0
    true_solar_minute = (
        utc_minute + equation_of_time + 4.0 * SCHIPHOL_LONGITUDE_DEG
    )
    hour_angle = np.deg2rad(true_solar_minute / 4.0 - 180.0)
    latitude = np.deg2rad(SCHIPHOL_LATITUDE_DEG)
    base = np.sin(latitude) * np.sin(declination)
    amplitude = np.cos(latitude) * np.cos(declination)
    sin_elevation = base + amplitude * np.cos(hour_angle)
    clipped_sin_elevation = np.clip(sin_elevation, -1.0, 1.0)
    output["solar_elevation_geometry_deg"] = np.rad2deg(
        np.arcsin(clipped_sin_elevation)
    )
    output["solar_elevation_positive"] = np.maximum(sin_elevation, 0.0)

    sunset_cosine = np.clip(-base / amplitude, -1.0, 1.0)
    sunset_hour_angle = np.arccos(sunset_cosine)
    integration_start = np.maximum(hour_angle, -sunset_hour_angle)
    daylight_ahead = hour_angle < sunset_hour_angle
    integration_start = np.minimum(integration_start, sunset_hour_angle)
    remaining_radians = np.maximum(
        sunset_hour_angle - integration_start, 0.0
    )
    remaining_integral = (
        base * remaining_radians
        + amplitude
        * (
            np.sin(sunset_hour_angle)
            - np.sin(integration_start)
        )
    ) * (12.0 / np.pi)
    output["remaining_clear_sky_integral_h"] = np.where(
        daylight_ahead, np.maximum(remaining_integral, 0.0), 0.0
    )
    output["daylight_remaining_geometry_minutes"] = np.where(
        daylight_ahead,
        remaining_radians * 720.0 / np.pi,
        0.0,
    )
    solar_noon_utc_minute = (
        720.0 - 4.0 * SCHIPHOL_LONGITUDE_DEG - equation_of_time
    )
    output["minutes_from_solar_noon"] = (
        utc_minute - solar_noon_utc_minute
    )

    radiation = pd.to_numeric(output["solar_w_m2"], errors="coerce")
    clear_sky_scale = 1000.0 * np.maximum(
        output["solar_elevation_positive"], 0.05
    )
    output["instant_solar_efficiency"] = np.clip(
        radiation / clear_sky_scale,
        0.0,
        2.0,
    )
    plateau_hours = (
        pd.to_numeric(
            output["plateau_duration_minutes"], errors="coerce"
        ).clip(lower=0)
        / 60.0
    )
    drawdown = pd.to_numeric(
        output["knmi_drawdown_from_high_c"], errors="coerce"
    ).clip(lower=0)
    output["remaining_solar_per_plateau"] = (
        output["remaining_clear_sky_integral_h"] / (1.0 + plateau_hours)
    )
    output["remaining_solar_per_drawdown"] = (
        output["remaining_clear_sky_integral_h"] / (1.0 + drawdown)
    )
    return output
