"""Strictly backward-looking KNMI 10-minute path features."""

from __future__ import annotations

import numpy as np
import pandas as pd


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _exact_lag(
    timestamps: pd.Series,
    values: pd.Series,
    minutes: int,
) -> pd.Series:
    indexed = pd.Series(values.to_numpy(), index=pd.DatetimeIndex(timestamps))
    wanted = pd.DatetimeIndex(timestamps) - pd.Timedelta(minutes=minutes)
    return pd.Series(indexed.reindex(wanted).to_numpy(), index=values.index)


def _complete_cadence(
    timestamps: pd.Series,
    minutes: int,
) -> pd.Series:
    available = set(pd.DatetimeIndex(timestamps))
    return pd.Series(
        [
            all(
                timestamp - pd.Timedelta(minutes=offset) in available
                for offset in range(0, minutes + 1, 10)
            )
            for timestamp in pd.DatetimeIndex(timestamps)
        ],
        index=timestamps.index,
        dtype=bool,
    )


def add_knmi_10m_path_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Add row-local, lagged, expanding, and trailing-window features only."""

    output = frame.sort_values(
        ["target_date", "observed_at_utc"]
    ).reset_index(drop=True).copy()
    output["observed_at_utc"] = pd.to_datetime(
        output["observed_at_utc"], utc=True
    )
    target_dates = pd.to_datetime(output["target_date"], errors="raise")
    day_angle = 2.0 * np.pi * target_dates.dt.dayofyear / 365.25
    output["day_of_year_sin"] = np.sin(day_angle)
    output["day_of_year_cos"] = np.cos(day_angle)
    output["rain_present"] = (_numeric(output, "precip_mm_h").fillna(0) > 0).astype(
        float
    )
    wind = np.deg2rad(_numeric(output, "wind_direction_deg"))
    output["wind_direction_sin"] = np.sin(wind)
    output["wind_direction_cos"] = np.cos(wind)

    pieces: list[pd.DataFrame] = []
    for _, group in output.groupby("target_date", sort=False):
        group = group.sort_values("observed_at_utc").copy()
        timestamps = pd.to_datetime(group["observed_at_utc"], utc=True)
        ta = _numeric(group, "ta_c")
        group["source_gap_minutes"] = (
            timestamps.diff().dt.total_seconds() / 60.0
        )
        for minutes in (20, 30, 60, 120, 180, 360):
            group[f"cadence_complete_{minutes}m"] = _complete_cadence(
                timestamps, minutes
            ).astype(float)

        lagged_ta = {
            minutes: _exact_lag(timestamps, ta, minutes)
            for minutes in (10, 20, 30, 60, 120, 180, 360)
        }
        delta_10 = ta - lagged_ta[10]
        group["ta_delta_10m_c"] = delta_10
        group["ta_delta_20m_c"] = ta - lagged_ta[20]
        group["ta_delta_30m_c"] = ta - lagged_ta[30]
        group["ta_delta_60m_c"] = ta - lagged_ta[60]
        group["ta_delta_120m_c"] = ta - lagged_ta[120]
        prior_delta_10 = _exact_lag(timestamps, delta_10, 10)
        group["ta_trend_accel_20m_c"] = delta_10 - prior_delta_10

        indexed_ta = pd.Series(
            ta.to_numpy(), index=pd.DatetimeIndex(timestamps)
        )
        range_60 = (
            indexed_ta.rolling("60min", closed="both").max()
            - indexed_ta.rolling("60min", closed="both").min()
        )
        complete_60 = group["cadence_complete_60m"].eq(1)
        group["ta_range_60m_c"] = np.where(
            complete_60, range_60.to_numpy(), np.nan
        )
        indexed_delta = pd.Series(
            delta_10.to_numpy(), index=pd.DatetimeIndex(timestamps)
        )
        max_positive_step = (
            indexed_delta.clip(lower=0)
            .rolling("60min", closed="both")
            .max()
        )
        group["max_positive_ta_step_60m_c"] = (
            np.where(complete_60, max_positive_step.to_numpy(), np.nan)
        )

        prior_high = ta.cummax().shift(1)
        strict_new_high = prior_high.isna() | ta.gt(prior_high)
        new_high_time = group["observed_at_utc"].where(strict_new_high).ffill()
        group["minutes_since_new_high"] = (
            group["observed_at_utc"] - new_high_time
        ).dt.total_seconds() / 60.0
        drawdown = _numeric(group, "running_max_c") - ta
        group["knmi_drawdown_from_high_c"] = drawdown
        group["plateau_duration_minutes"] = np.where(
            drawdown.le(0.1),
            group["minutes_since_new_high"],
            0.0,
        )

        low_60 = indexed_ta.rolling("60min", closed="both").min()
        group["rebound_from_60m_low_c"] = np.where(
            complete_60, ta.to_numpy() - low_60.to_numpy(), np.nan
        )
        group["is_rebounding_after_pullback"] = (
            group["rebound_from_60m_low_c"].ge(0.2)
            & group["ta_delta_20m_c"].gt(0)
        ).astype(float)

        for source, prefix in (
            ("solar_w_m2", "solar"),
            ("cloud_okta", "cloud"),
            ("precip_mm_h", "precip"),
            ("humidity_pct", "humidity"),
        ):
            values = _numeric(group, source)
            group[f"{prefix}_delta_30m"] = values - _exact_lag(
                timestamps, values, 30
            )
            group[f"{prefix}_delta_60m"] = values - _exact_lag(
                timestamps, values, 60
            )
        for source, prefix in (
            ("pressure_hpa", "pressure"),
            ("wind_speed_mps", "wind_speed"),
            ("dewpoint_c", "dewpoint"),
            ("humidity_pct", "humidity"),
        ):
            values = _numeric(group, source)
            group[f"{prefix}_delta_180m"] = values - _exact_lag(
                timestamps, values, 180
            )
        pressure = _numeric(group, "pressure_hpa")
        group["pressure_delta_360m"] = pressure - _exact_lag(
            timestamps, pressure, 360
        )
        solar = _numeric(group, "solar_w_m2")
        indexed_solar = pd.Series(
            solar.to_numpy(), index=pd.DatetimeIndex(timestamps)
        )
        group["solar_energy_60m_mj_m2"] = (
            np.where(
                complete_60,
                indexed_solar.rolling("50min", closed="both")
                .sum()
                .to_numpy()
                * 600.0
                / 1_000_000.0,
                np.nan,
            )
        )
        cloud = _numeric(group, "cloud_okta")
        indexed_cloud = pd.Series(
            cloud.to_numpy(), index=pd.DatetimeIndex(timestamps)
        )
        group["cloud_mean_60m_okta"] = np.where(
            complete_60,
            indexed_cloud.rolling("50min", closed="both").mean().to_numpy(),
            np.nan,
        )
        complete_180 = group["cadence_complete_180m"].eq(1)
        group["cloud_mean_180m_okta"] = np.where(
            complete_180,
            indexed_cloud.rolling("170min", closed="both")
            .mean()
            .to_numpy(),
            np.nan,
        )
        rain = _numeric(group, "precip_mm_h").fillna(0)
        indexed_rain = pd.Series(
            rain.to_numpy(), index=pd.DatetimeIndex(timestamps)
        )
        group["rain_minutes_60m"] = (
            np.where(
                complete_60,
                indexed_rain.gt(0)
                .rolling("50min", closed="both")
                .sum()
                .to_numpy()
                * 10.0,
                np.nan,
            )
        )
        pieces.append(group)
    return pd.concat(pieces, ignore_index=True)
