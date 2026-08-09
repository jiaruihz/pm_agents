"""Shared regime labels for weather state rows."""

from __future__ import annotations

import pandas as pd

from weather_data_feed.city_family import CITY_FAMILY_ATLAS_V1 as CITY_FAMILY


def unit_step(row: pd.Series) -> float:
    return 1.0 if str(row.get("unit", "")).upper() == "F" else 0.5


def label_solar_window(hour: float) -> str:
    if pd.isna(hour):
        return "hour_missing"
    if hour <= 11:
        return "late_morning"
    if hour <= 14:
        return "solar_peak_window"
    if hour <= 17:
        return "afternoon_decay_window"
    return "evening_tail"


def label_day_space(row: pd.Series) -> str:
    gap = row.get("forecast_gap_to_running_native")
    if pd.isna(gap):
        return "day_space_unknown"
    step = unit_step(row)
    if gap >= 3 * step:
        return "day_open_runway"
    if gap >= step:
        return "day_marginal_runway"
    if gap >= -step:
        return "day_forecast_capped"
    return "day_forecast_busted"


def label_moisture_cloud(row: pd.Series) -> str:
    rh = row.get("relative_humidity_pct")
    sky = row.get("sky_cover_code")
    dew_dep = row.get("dewpoint_depression_f")
    if pd.isna(rh) and pd.isna(sky) and pd.isna(dew_dep):
        return "moisture_cloud_unknown"
    if pd.notna(rh) and rh >= 80 and pd.notna(sky) and sky >= 3:
        return "humid_overcast_suppression"
    if pd.notna(rh) and rh >= 75:
        return "humid_convective_risk"
    if pd.notna(sky) and sky >= 3:
        return "cloud_suppression"
    if pd.notna(dew_dep) and dew_dep >= 25:
        return "dry_heat_inertia"
    return "mixed_moisture"


def label_wind_noise(row: pd.Series) -> str:
    wind = row.get("wind_speed_kt")
    if pd.isna(wind):
        return "wind_unknown"
    if wind >= 18:
        return "windy_mixing_noise"
    if wind >= 10:
        return "moderate_wind"
    return "light_wind"


def label_running_state(row: pd.Series) -> str:
    mins = row.get("minutes_since_running_max")
    decline = row.get("decline_native")
    step = unit_step(row)
    if pd.isna(mins) or pd.isna(decline):
        return "running_max_clock_unknown"
    if decline <= 0.25 * step and mins <= 45:
        return "fresh_running_high"
    if decline <= 0.5 * step and mins <= 120:
        return "near_high_plateau"
    if decline <= 0.5 * step:
        return "stalled_high"
    if mins >= 120:
        return "mature_fade"
    return "pullback_from_high"


def label_intraday_state(row: pd.Series) -> str:
    trend1 = row.get("temp_trend_1h_f")
    trend3 = row.get("temp_trend_3h_f")
    decline = row.get("decline_native")
    mins = row.get("minutes_since_running_max")
    hour = row.get("decision_hour_local")
    step = unit_step(row)
    if pd.isna(trend1) and pd.isna(trend3):
        return "state_unknown"
    if pd.notna(trend1) and trend1 >= 1.0 and (pd.isna(decline) or decline <= step):
        return "active_warming"
    if pd.notna(decline) and decline <= 0.25 * step and pd.notna(mins) and mins <= 60:
        return "fresh_high"
    if pd.notna(decline) and decline <= 0.5 * step and pd.notna(mins) and mins > 60:
        return "plateau_near_high"
    if pd.notna(decline) and decline > 0.5 * step:
        if pd.notna(hour) and hour <= 14 and pd.notna(trend3) and trend3 > 0:
            return "false_fade_risk"
        if pd.notna(trend1) and trend1 > 0:
            return "reheating_after_dip"
        if pd.notna(mins) and mins >= 120:
            return "mature_fade"
        return "pullback_uncertain"
    if pd.notna(trend3) and trend3 <= 0:
        return "flat_or_cooling"
    return "slow_warming"


def _strict_high_observed_age(row: pd.Series) -> float | None:
    age = row.get("minutes_since_last_strict_new_high")
    if pd.isna(age):
        age = row.get("minutes_since_first_running_max")
    if pd.isna(age):
        return None
    obs_age = row.get("obs_age_minutes")
    return max(0.0, float(age) - (max(0.0, float(obs_age)) if pd.notna(obs_age) else 0.0))


def label_running_state_v2(row: pd.Series) -> str:
    """Strict-high state; equal highs do not reset the maturity clock."""
    age = _strict_high_observed_age(row)
    decline = row.get("decline_native")
    step = unit_step(row)
    if age is None or pd.isna(decline):
        return "strict_high_clock_unknown"
    if decline <= 0.25 * step:
        return "fresh_strict_high" if age <= 45 else "equal_high_plateau"
    if decline <= 0.5 * step:
        return "near_high_plateau"
    return "mature_fade" if age >= 120 else "pullback_from_high"


def label_intraday_state_v2(row: pd.Series) -> str:
    trend1 = row.get("temp_trend_report_anchored_1h_f")
    trend3 = row.get("temp_trend_report_anchored_3h_f")
    if pd.isna(trend1):
        trend1 = row.get("temp_trend_1h_f")
    if pd.isna(trend3):
        trend3 = row.get("temp_trend_3h_f")
    decline = row.get("decline_native")
    age = _strict_high_observed_age(row)
    step = unit_step(row)
    if pd.isna(trend1) and pd.isna(trend3):
        return "state_unknown"
    if pd.notna(trend1) and trend1 >= 1.0 and (pd.isna(decline) or decline <= step):
        return "active_warming"
    if pd.notna(decline) and decline <= 0.25 * step and age is not None:
        return "fresh_strict_high" if age <= 60 else "equal_high_plateau"
    if pd.notna(decline) and decline <= 0.5 * step and age is not None:
        return "plateau_near_high"
    if pd.notna(decline) and decline > 0.5 * step:
        hour = row.get("decision_hour_local")
        if pd.notna(hour) and hour <= 14 and pd.notna(trend3) and trend3 > 0:
            return "false_fade_risk"
        if pd.notna(trend1) and trend1 > 0:
            return "reheating_after_dip"
        return "mature_fade" if age is not None and age >= 120 else "pullback_uncertain"
    if pd.notna(trend3) and trend3 <= 0:
        return "flat_or_cooling"
    return "slow_warming"


def label_solar_phase_v2(row: pd.Series) -> str:
    elevation = row.get("solar_elevation_deg")
    delta = row.get("solar_elevation_delta_2h_deg")
    if pd.isna(elevation) or pd.isna(delta):
        return "solar_geometry_unknown"
    if elevation <= 0:
        return "below_horizon"
    return "solar_rising" if delta >= 0 else "solar_declining"


def label_moisture_cloud_v2(row: pd.Series) -> str:
    """Do not infer convection from humidity alone."""
    rh = row.get("relative_humidity_pct")
    sky = row.get("sky_cover_code")
    dew_dep = row.get("dewpoint_depression_f")
    if pd.isna(rh) and pd.isna(sky) and pd.isna(dew_dep):
        return "moisture_cloud_unknown"
    if pd.notna(rh) and rh >= 80 and pd.notna(sky) and sky >= 3:
        return "humid_overcast_suppression"
    if pd.notna(rh) and rh >= 75:
        return "humid_clear_or_mixed"
    if pd.notna(sky) and sky >= 3:
        return "cloud_suppression"
    if pd.notna(dew_dep) and dew_dep >= 25:
        return "dry_heat_inertia"
    return "mixed_moisture"


def add_regime_labels(states: pd.DataFrame) -> pd.DataFrame:
    out = states.copy()
    out["city_family"] = out["city"].map(CITY_FAMILY).fillna("other")
    out["solar_window"] = out["decision_hour_local"].apply(label_solar_window)
    out["day_regime"] = out.apply(label_day_space, axis=1)
    out["moisture_cloud_regime"] = out.apply(label_moisture_cloud, axis=1)
    out["wind_regime"] = out.apply(label_wind_noise, axis=1)
    out["running_max_state"] = out.apply(label_running_state, axis=1)
    out["intraday_state"] = out.apply(label_intraday_state, axis=1)
    out["composite_regime"] = (
        out["day_regime"].astype(str)
        + " | "
        + out["intraday_state"].astype(str)
        + " | "
        + out["moisture_cloud_regime"].astype(str)
    )
    out["solar_phase_v2"] = out.apply(label_solar_phase_v2, axis=1)
    out["moisture_cloud_regime_v2"] = out.apply(label_moisture_cloud_v2, axis=1)
    out["running_max_state_v2"] = out.apply(label_running_state_v2, axis=1)
    out["intraday_state_v2"] = out.apply(label_intraday_state_v2, axis=1)
    out["composite_regime_v2"] = (
        out["day_regime"].astype(str)
        + " | "
        + out["intraday_state_v2"].astype(str)
        + " | "
        + out["moisture_cloud_regime_v2"].astype(str)
        + " | "
        + out["solar_phase_v2"].astype(str)
    )
    return out
