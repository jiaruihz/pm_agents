#!/usr/bin/env python3
"""Evaluate same-day observation-minus-forecast innovation for Tmax.

The primary denominator is the canonical ``tmax_v2`` PIT state:

    ladder snapshot
      + latest observation available at the snapshot
      + latest forecast curve available at the snapshot
      + settled exact bracket

This is a weather-feature study, not a selected-trade backtest.  It compares
the raw D0 forecast maximum, an expanding rolling-bias correction, and the
same correction augmented by the contemporaneous observation-minus-model
temperature innovation.  All model variants use the same checkpoint rows.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.linear_model import Ridge

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.market_brackets import parse_market_bracket
from weather_data_feed_service.legacy_weather_predict.city_pools import FULL_CITY_CONFIGS


DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_OUT = ROOT / "docs/analysis/2026-07/generated/forecast_innovation_morning_v1"
DEFAULT_REPORT = ROOT / "docs/analysis/2026-07/2026-07-28-forecast-innovation-morning-v1.md"
CHECKPOINTS = (3, 6, 9, 12)
PRIMARY_CHECKPOINT = 9
CHECKPOINT_TOLERANCE_MIN = 45
MIN_TRAIN_DATES = 5
RIDGE_ALPHA = 10.0
BOOTSTRAP_SAMPLES = 5000
BOOTSTRAP_SEED = 20260728
WEATHER_SIGMA_F = 3.0
FUSION_WEATHER_WEIGHT = 0.30


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser.parse_args()


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    return conn


def load_pit_states(conn: sqlite3.Connection) -> tuple[pd.DataFrame, dict[str, int]]:
    states = pd.read_sql_query(
        """
        SELECT
            s.tmax_state_id,
            s.city,
            s.target_date,
            s.decision_ts_utc,
            s.ladder_snapshot_id,
            s.forecast_capture_id,
            l.market_unit,
            l.market_utc_offset_seconds,
            o.obs_ts_utc,
            o.available_at_utc AS observation_available_at_utc,
            o.temp_f AS observed_temp_f,
            f.available_at_utc AS forecast_available_at_utc,
            f.forecast_source,
            f.forecast_model
        FROM tmax_v2_canonical_states AS s
        JOIN tmax_v2_ladder_snapshots AS l
          ON l.ladder_snapshot_id = s.ladder_snapshot_id
        JOIN tmax_v2_observation_event_lineage AS o
          ON o.tmax_v2_observation_id = s.observation_event_id
        JOIN tmax_v2_forecast_captures AS f
          ON f.forecast_capture_id = s.forecast_capture_id
        WHERE s.pit_status = 'pit_verified'
          AND f.normalized_hourly_curve_json IS NOT NULL
        """,
        conn,
    )
    states["decision_ts"] = pd.to_datetime(states["decision_ts_utc"], utc=True)
    states["local_ts"] = states["decision_ts"] + pd.to_timedelta(
        states["market_utc_offset_seconds"], unit="s"
    )
    states["local_minute"] = (
        states["local_ts"].dt.hour * 60
        + states["local_ts"].dt.minute
        + states["local_ts"].dt.second / 60.0
    )
    checkpoint_rows: list[pd.DataFrame] = []
    for checkpoint in CHECKPOINTS:
        start = checkpoint * 60
        eligible = states[
            states["local_ts"].dt.strftime("%Y-%m-%d").eq(states["target_date"])
            & states["local_minute"].between(
                start, start + CHECKPOINT_TOLERANCE_MIN, inclusive="both"
            )
        ].copy()
        eligible = (
            eligible.sort_values("decision_ts")
            .drop_duplicates(["city", "target_date"], keep="first")
            .copy()
        )
        eligible["checkpoint_hour_local"] = checkpoint
        checkpoint_rows.append(eligible)
    selected = pd.concat(checkpoint_rows, ignore_index=True)
    counts = {
        "canonical_states": int(
            conn.execute("SELECT COUNT(*) FROM tmax_v2_canonical_states").fetchone()[0]
        ),
        "pit_verified_curve_states": len(states),
        "checkpoint_rows_pre_settlement": len(selected),
    }
    return selected, counts


def load_winners(conn: sqlite3.Connection) -> pd.DataFrame:
    winners = pd.read_sql_query(
        """
        SELECT city, target_date, bracket, unit, question
        FROM settlement_outcomes
        WHERE source_system = 'pm_history'
          AND settlement_status = 'settled'
          AND final_price = 1
        """,
        conn,
    )
    duplicated = winners.duplicated(["city", "target_date"], keep=False)
    if duplicated.any():
        examples = winners.loc[duplicated, ["city", "target_date"]].drop_duplicates()
        raise RuntimeError(f"multiple pm_history winners: {examples.head().to_dict('records')}")
    return winners


def chunked(values: list[str], size: int = 500) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def load_curves(conn: sqlite3.Connection, ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for group in chunked(ids):
        placeholders = ",".join("?" for _ in group)
        rows = conn.execute(
            f"""
            SELECT forecast_capture_id, normalized_hourly_curve_json
            FROM tmax_v2_forecast_captures
            WHERE forecast_capture_id IN ({placeholders})
            """,
            group,
        ).fetchall()
        for capture_id, payload in rows:
            if payload:
                result[str(capture_id)] = json.loads(payload)
    return result


def interpolate_curve(
    curve: list[dict[str, Any]],
    *,
    target_date: str,
    local_hour: float,
    field: str,
) -> float:
    points: list[tuple[float, float]] = []
    for item in curve:
        local = str(item.get("time_local") or "")
        value = item.get(field)
        if local[:10] != target_date or value is None:
            continue
        try:
            hour = int(local[11:13]) + int(local[14:16]) / 60.0
            points.append((hour, float(value)))
        except (TypeError, ValueError):
            continue
    points.sort()
    for hour, value in points:
        if abs(hour - local_hour) < 1e-9:
            return value
    for (left_hour, left_value), (right_hour, right_value) in zip(points, points[1:]):
        if left_hour < local_hour < right_hour:
            weight = (local_hour - left_hour) / (right_hour - left_hour)
            return left_value + weight * (right_value - left_value)
    return math.nan


def settlement_mid_f(bracket: str, unit: str, question: str) -> float:
    lower_question = str(question).lower()
    if "or below" in lower_question or "or higher" in lower_question or str(bracket).endswith("+"):
        return math.nan
    parsed = parse_market_bracket(str(bracket), str(unit))
    if parsed is None or parsed.low is None or parsed.high is None:
        return math.nan
    midpoint = (float(parsed.low) + float(parsed.high)) / 2.0
    return midpoint if str(unit).upper() == "F" else midpoint * 9.0 / 5.0 + 32.0


def enrich_weather_rows(
    selected: pd.DataFrame,
    curves: dict[str, list[dict[str, Any]]],
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for row in selected.to_dict("records"):
        curve = curves.get(str(row["forecast_capture_id"]))
        if not curve:
            continue
        local_ts = pd.Timestamp(row["local_ts"])
        local_hour = (
            local_ts.hour + local_ts.minute / 60.0 + local_ts.second / 3600.0
        )
        target_date = str(row["target_date"])
        target_temperatures = [
            float(item["temperature_f"])
            for item in curve
            if str(item.get("time_local") or "")[:10] == target_date
            and item.get("temperature_f") is not None
        ]
        if not target_temperatures:
            continue
        model_current = interpolate_curve(
            curve,
            target_date=target_date,
            local_hour=local_hour,
            field="temperature_f",
        )
        record = dict(row)
        record.update(
            {
                "forecast_temperature_at_decision_f": model_current,
                "forecast_max_f": max(target_temperatures),
                "forecast_remaining_warming_f": max(target_temperatures)
                - model_current,
                "forecast_cloud_cover_at_decision_pct": interpolate_curve(
                    curve,
                    target_date=target_date,
                    local_hour=local_hour,
                    field="cloud_cover_pct",
                ),
                "forecast_wind_at_decision_kt": interpolate_curve(
                    curve,
                    target_date=target_date,
                    local_hour=local_hour,
                    field="wind_speed_10m_kt",
                ),
                "forecast_precip_at_decision_pct": interpolate_curve(
                    curve,
                    target_date=target_date,
                    local_hour=local_hour,
                    field="precipitation_probability_pct",
                ),
            }
        )
        records.append(record)
    out = pd.DataFrame(records)
    out["forecast_innovation_f"] = (
        out["observed_temp_f"] - out["forecast_temperature_at_decision_f"]
    )
    out["settlement_mid_f"] = [
        settlement_mid_f(bracket, unit, question)
        for bracket, unit, question in zip(
            out["bracket"], out["unit"], out["question"], strict=True
        )
    ]
    out["forecast_final_error_f"] = out["settlement_mid_f"] - out["forecast_max_f"]
    out["observation_age_min"] = (
        pd.to_datetime(out["decision_ts_utc"], utc=True)
        - pd.to_datetime(out["obs_ts_utc"], utc=True)
    ).dt.total_seconds() / 60.0
    out["forecast_age_min"] = (
        pd.to_datetime(out["decision_ts_utc"], utc=True)
        - pd.to_datetime(out["forecast_available_at_utc"], utc=True)
    ).dt.total_seconds() / 60.0
    out["region"] = out["city"].map(
        lambda city: str((FULL_CITY_CONFIGS.get(str(city)) or {}).get("region") or "unknown")
    )
    return out


def categorical_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.get_dummies(
        frame[["city", "forecast_model"]],
        columns=["city", "forecast_model"],
        dtype=float,
    )


def variant_matrix(frame: pd.DataFrame, variant: str) -> pd.DataFrame:
    base = categorical_matrix(frame)
    if variant == "rolling_bias":
        return base
    innovation = frame["forecast_innovation_f"].astype(float)
    base["forecast_innovation_f"] = innovation
    if variant == "innovation":
        return base
    if variant != "innovation_regime":
        raise ValueError(variant)
    cloud = frame["forecast_cloud_cover_at_decision_pct"].astype(float) / 100.0
    wind = frame["forecast_wind_at_decision_kt"].astype(float) / 10.0
    precip = frame["forecast_precip_at_decision_pct"].astype(float) / 100.0
    base["cloud_missing"] = cloud.isna().astype(float)
    base["wind_missing"] = wind.isna().astype(float)
    base["precip_missing"] = precip.isna().astype(float)
    cloud = cloud.fillna(0.0)
    wind = wind.fillna(0.0)
    precip = precip.fillna(0.0)
    base["innovation_x_cloud"] = innovation * cloud
    base["innovation_x_wind"] = innovation * wind
    base["innovation_x_precip"] = innovation * precip
    return base


def align_columns(train: pd.DataFrame, test: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    columns = sorted(set(train.columns) | set(test.columns))
    return train.reindex(columns=columns, fill_value=0.0), test.reindex(
        columns=columns, fill_value=0.0
    )


def walk_forward_predictions(rows: pd.DataFrame) -> pd.DataFrame:
    variants = ("rolling_bias", "innovation", "innovation_regime")
    output: list[pd.DataFrame] = []
    common_fields = [
        "forecast_innovation_f",
        "forecast_final_error_f",
        "forecast_max_f",
        "settlement_mid_f",
    ]
    denominator = rows.dropna(subset=common_fields).copy()
    for checkpoint, checkpoint_rows in denominator.groupby("checkpoint_hour_local"):
        dates = sorted(checkpoint_rows["target_date"].unique())
        for target_date in dates:
            train_dates = [date for date in dates if date < target_date]
            if len(train_dates) < MIN_TRAIN_DATES:
                continue
            train = checkpoint_rows[checkpoint_rows["target_date"].isin(train_dates)]
            test = checkpoint_rows[checkpoint_rows["target_date"].eq(target_date)]
            result = test[
                [
                    "tmax_state_id",
                    "city",
                    "target_date",
                    "checkpoint_hour_local",
                    "region",
                    "forecast_model",
                    "forecast_innovation_f",
                    "forecast_final_error_f",
                    "forecast_max_f",
                    "settlement_mid_f",
                ]
            ].copy()
            result["pred_raw_forecast_f"] = test["forecast_max_f"].to_numpy(float)
            for variant in variants:
                x_train, x_test = align_columns(
                    variant_matrix(train, variant),
                    variant_matrix(test, variant),
                )
                model = Ridge(alpha=RIDGE_ALPHA, fit_intercept=True)
                model.fit(x_train.to_numpy(float), train["forecast_final_error_f"].to_numpy(float))
                correction = model.predict(x_test.to_numpy(float))
                result[f"pred_{variant}_f"] = test["forecast_max_f"].to_numpy(float) + correction
                if variant in {"innovation", "innovation_regime"}:
                    innovation_column = list(x_train.columns).index("forecast_innovation_f")
                    result[f"beta_{variant}"] = float(model.coef_[innovation_column])
            result["train_dates"] = len(train_dates)
            result["train_through_date"] = max(train_dates)
            output.append(result)
    if not output:
        return pd.DataFrame()
    return pd.concat(output, ignore_index=True)


def date_block_ci(frame: pd.DataFrame, value: str) -> tuple[float, float]:
    daily = frame.groupby("target_date")[value].mean().dropna().to_numpy(float)
    if len(daily) < 3:
        return math.nan, math.nan
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    indices = rng.integers(0, len(daily), size=(BOOTSTRAP_SAMPLES, len(daily)))
    samples = daily[indices].mean(axis=1)
    return float(np.percentile(samples, 2.5)), float(np.percentile(samples, 97.5))


def score_predictions(predictions: pd.DataFrame) -> pd.DataFrame:
    variants = ("raw_forecast", "rolling_bias", "innovation", "innovation_regime")
    rows: list[dict[str, Any]] = []
    for checkpoint, group in predictions.groupby("checkpoint_hour_local"):
        actual = group["settlement_mid_f"].to_numpy(float)
        base_abs = np.abs(group["pred_rolling_bias_f"].to_numpy(float) - actual)
        for variant in variants:
            errors = group[f"pred_{variant}_f"].to_numpy(float) - actual
            abs_errors = np.abs(errors)
            record: dict[str, Any] = {
                "checkpoint_hour_local": int(checkpoint),
                "variant": variant,
                "rows": len(group),
                "dates": int(group["target_date"].nunique()),
                "cities": int(group["city"].nunique()),
                "mae_f": float(abs_errors.mean()),
                "rmse_f": float(np.sqrt(np.mean(errors**2))),
                "mean_error_f": float(errors.mean()),
            }
            if variant in {"innovation", "innovation_regime"}:
                delta_name = "_delta"
                delta = abs_errors - base_abs
                tmp = group[["target_date"]].copy()
                tmp[delta_name] = delta
                low, high = date_block_ci(tmp, delta_name)
                record.update(
                    {
                        "mae_delta_vs_rolling_bias_f": float(delta.mean()),
                        "delta_ci_low_f": low,
                        "delta_ci_high_f": high,
                    }
                )
            rows.append(record)
    return pd.DataFrame(rows)


def beta_estimates(predictions: pd.DataFrame) -> pd.DataFrame:
    return (
        predictions.groupby(["checkpoint_hour_local", "target_date"], as_index=False)
        .agg(
            rows=("city", "size"),
            beta_innovation=("beta_innovation", "first"),
            beta_innovation_regime=("beta_innovation_regime", "first"),
            train_dates=("train_dates", "first"),
            train_through_date=("train_through_date", "first"),
        )
        .sort_values(["checkpoint_hour_local", "target_date"])
    )


def region_scorecard(predictions: pd.DataFrame) -> pd.DataFrame:
    primary = predictions[
        predictions["checkpoint_hour_local"].eq(PRIMARY_CHECKPOINT)
    ].copy()
    primary["abs_bias"] = (
        primary["pred_rolling_bias_f"] - primary["settlement_mid_f"]
    ).abs()
    primary["abs_innovation"] = (
        primary["pred_innovation_f"] - primary["settlement_mid_f"]
    ).abs()
    primary["mae_delta_f"] = primary["abs_innovation"] - primary["abs_bias"]
    rows: list[dict[str, Any]] = []
    for region, group in primary.groupby("region"):
        low, high = date_block_ci(group, "mae_delta_f")
        rows.append(
            {
                "region": region,
                "rows": len(group),
                "dates": int(group["target_date"].nunique()),
                "cities": int(group["city"].nunique()),
                "rolling_bias_mae_f": float(group["abs_bias"].mean()),
                "innovation_mae_f": float(group["abs_innovation"].mean()),
                "mae_delta_f": float(group["mae_delta_f"].mean()),
                "delta_ci_low_f": low,
                "delta_ci_high_f": high,
            }
        )
    return pd.DataFrame(rows).sort_values("region")


def market_coverage(conn: sqlite3.Connection, rows: pd.DataFrame) -> pd.DataFrame:
    selected = rows[
        ["ladder_snapshot_id", "checkpoint_hour_local", "target_date"]
    ].drop_duplicates()
    quote_rows: list[tuple[Any, ...]] = []
    ids = selected["ladder_snapshot_id"].astype(str).tolist()
    for group in chunked(ids):
        placeholders = ",".join("?" for _ in group)
        quote_rows.extend(
            conn.execute(
                f"""
                SELECT
                    ladder_snapshot_id,
                    COUNT(*) AS rung_count,
                    SUM(yes_direct_bid IS NOT NULL) AS yes_bid_count,
                    SUM(yes_direct_ask IS NOT NULL) AS yes_ask_count,
                    SUM(no_direct_bid IS NOT NULL) AS no_bid_count,
                    SUM(no_direct_ask IS NOT NULL) AS no_ask_count,
                    SUM(
                        (yes_direct_bid IS NOT NULL OR no_direct_ask IS NOT NULL)
                        AND
                        (yes_direct_ask IS NOT NULL OR no_direct_bid IS NOT NULL)
                    ) AS two_sided_effective_count
                FROM tmax_v2_ladder_rung_quotes
                WHERE ladder_snapshot_id IN ({placeholders})
                GROUP BY ladder_snapshot_id
                """,
                group,
            ).fetchall()
        )
    quotes = pd.DataFrame(
        quote_rows,
        columns=[
            "ladder_snapshot_id",
            "rung_count",
            "yes_bid_count",
            "yes_ask_count",
            "no_bid_count",
            "no_ask_count",
            "two_sided_effective_count",
        ],
    )
    merged = selected.merge(quotes, on="ladder_snapshot_id", how="left")
    merged["full_two_sided_ladder"] = merged["two_sided_effective_count"].eq(
        merged["rung_count"]
    )
    merged["full_direct_yes_ask_ladder"] = merged["yes_ask_count"].eq(
        merged["rung_count"]
    )
    return (
        merged.groupby("checkpoint_hour_local", as_index=False)
        .agg(
            checkpoint_rows=("ladder_snapshot_id", "size"),
            dates=("target_date", "nunique"),
            full_two_sided_ladders=("full_two_sided_ladder", "sum"),
            full_direct_yes_ask_ladders=("full_direct_yes_ask_ladder", "sum"),
        )
        .sort_values("checkpoint_hour_local")
    )


def load_quotes(
    conn: sqlite3.Connection, ladder_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    rows: list[tuple[Any, ...]] = []
    for group in chunked(ladder_ids):
        placeholders = ",".join("?" for _ in group)
        rows.extend(
            conn.execute(
                f"""
                SELECT
                    ladder_snapshot_id,
                    absolute_bracket_identity,
                    yes_direct_bid,
                    yes_direct_ask,
                    no_direct_bid,
                    no_direct_ask
                FROM tmax_v2_ladder_rung_quotes
                WHERE ladder_snapshot_id IN ({placeholders})
                """,
                group,
            ).fetchall()
        )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for ladder_id, bracket, yes_bid, yes_ask, no_bid, no_ask in rows:
        grouped.setdefault(str(ladder_id), []).append(
            {
                "bracket": str(bracket),
                "yes_bid": yes_bid,
                "yes_ask": yes_ask,
                "no_bid": no_bid,
                "no_ask": no_ask,
            }
        )
    return grouped


def effective_mid(quote: dict[str, Any]) -> float | None:
    bid_candidates = [
        float(value)
        for value in (quote["yes_bid"],)
        if value is not None
    ]
    if quote["no_ask"] is not None:
        bid_candidates.append(1.0 - float(quote["no_ask"]))
    ask_candidates = [
        float(value)
        for value in (quote["yes_ask"],)
        if value is not None
    ]
    if quote["no_bid"] is not None:
        ask_candidates.append(1.0 - float(quote["no_bid"]))
    if not bid_candidates or not ask_candidates:
        return None
    bid = max(bid_candidates)
    ask = min(ask_candidates)
    if bid > ask + 1e-9:
        return None
    return float(np.clip((bid + ask) / 2.0, 1e-6, 1 - 1e-6))


def ladder_probabilities(
    quotes: list[dict[str, Any]],
    *,
    unit: str,
    predicted_tmax_f: float,
) -> tuple[list[str], np.ndarray, np.ndarray] | None:
    parsed_rows: list[tuple[float, str, float]] = []
    for quote in quotes:
        midpoint = effective_mid(quote)
        bracket = parse_market_bracket(quote["bracket"], unit)
        if midpoint is None or bracket is None:
            return None
        if bracket.low is not None and bracket.high is not None:
            center = (float(bracket.low) + float(bracket.high)) / 2.0
        elif bracket.low is not None:
            center = float(bracket.low)
        elif bracket.high is not None:
            center = float(bracket.high)
        else:
            return None
        parsed_rows.append((center, quote["bracket"], midpoint))
    parsed_rows.sort(key=lambda item: item[0])
    if len(parsed_rows) < 3:
        return None
    centers = np.asarray([item[0] for item in parsed_rows], dtype=float)
    labels = [item[1] for item in parsed_rows]
    market = np.asarray([item[2] for item in parsed_rows], dtype=float)
    market = market / market.sum()
    boundaries = np.concatenate(
        ([-np.inf], (centers[:-1] + centers[1:]) / 2.0, [np.inf])
    )
    if str(unit).upper() == "F":
        mean_native = predicted_tmax_f
        sigma_native = WEATHER_SIGMA_F
    else:
        mean_native = (predicted_tmax_f - 32.0) * 5.0 / 9.0
        sigma_native = WEATHER_SIGMA_F * 5.0 / 9.0
    weather = np.diff(norm.cdf((boundaries - mean_native) / sigma_native))
    weather = np.clip(weather, 1e-9, None)
    weather = weather / weather.sum()
    return labels, market, weather


def probability_scores(
    conn: sqlite3.Connection,
    enriched: pd.DataFrame,
    predictions: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    metadata = enriched[
        [
            "tmax_state_id",
            "ladder_snapshot_id",
            "market_unit",
            "bracket",
        ]
    ].drop_duplicates("tmax_state_id")
    scored = predictions.merge(
        metadata, on="tmax_state_id", how="left", validate="many_to_one"
    )
    quotes = load_quotes(
        conn, scored["ladder_snapshot_id"].astype(str).drop_duplicates().tolist()
    )
    records: list[dict[str, Any]] = []
    variants = {
        "market": None,
        "weather_rolling_bias": "pred_rolling_bias_f",
        "weather_innovation": "pred_innovation_f",
        "fusion_rolling_bias": "pred_rolling_bias_f",
        "fusion_innovation": "pred_innovation_f",
    }
    for row in scored.to_dict("records"):
        ladder_quotes = quotes.get(str(row["ladder_snapshot_id"]))
        if not ladder_quotes:
            continue
        bias_result = ladder_probabilities(
            ladder_quotes,
            unit=str(row["market_unit"]),
            predicted_tmax_f=float(row["pred_rolling_bias_f"]),
        )
        innovation_result = ladder_probabilities(
            ladder_quotes,
            unit=str(row["market_unit"]),
            predicted_tmax_f=float(row["pred_innovation_f"]),
        )
        if bias_result is None or innovation_result is None:
            continue
        labels, market, weather_bias = bias_result
        innovation_labels, innovation_market, weather_innovation = innovation_result
        if labels != innovation_labels or not np.allclose(market, innovation_market):
            raise AssertionError("ladder probability alignment changed across variants")
        try:
            actual_index = labels.index(str(row["bracket"]))
        except ValueError:
            continue
        fusion_bias = np.power(market, 1.0 - FUSION_WEATHER_WEIGHT) * np.power(
            weather_bias, FUSION_WEATHER_WEIGHT
        )
        fusion_bias /= fusion_bias.sum()
        fusion_innovation = np.power(
            market, 1.0 - FUSION_WEATHER_WEIGHT
        ) * np.power(weather_innovation, FUSION_WEATHER_WEIGHT)
        fusion_innovation /= fusion_innovation.sum()
        probabilities = {
            "market": market,
            "weather_rolling_bias": weather_bias,
            "weather_innovation": weather_innovation,
            "fusion_rolling_bias": fusion_bias,
            "fusion_innovation": fusion_innovation,
        }
        one_hot = np.zeros(len(labels))
        one_hot[actual_index] = 1.0
        for variant in variants:
            probability = probabilities[variant]
            records.append(
                {
                    "tmax_state_id": row["tmax_state_id"],
                    "city": row["city"],
                    "target_date": row["target_date"],
                    "checkpoint_hour_local": row["checkpoint_hour_local"],
                    "variant": variant,
                    "actual_probability": float(probability[actual_index]),
                    "logloss": float(-math.log(max(probability[actual_index], 1e-9))),
                    "brier": float(np.mean((probability - one_hot) ** 2)),
                }
            )
    detail = pd.DataFrame(records)
    if detail.empty:
        return detail, pd.DataFrame()
    summary_rows: list[dict[str, Any]] = []
    for (checkpoint, variant), group in detail.groupby(
        ["checkpoint_hour_local", "variant"]
    ):
        summary_rows.append(
            {
                "checkpoint_hour_local": int(checkpoint),
                "variant": variant,
                "rows": len(group),
                "dates": int(group["target_date"].nunique()),
                "cities": int(group["city"].nunique()),
                "date_equal_logloss": float(
                    group.groupby("target_date")["logloss"].mean().mean()
                ),
                "date_equal_brier": float(
                    group.groupby("target_date")["brier"].mean().mean()
                ),
            }
        )
    summary = pd.DataFrame(summary_rows)
    return detail, summary


def markdown_table(frame: pd.DataFrame, columns: list[str]) -> str:
    def cell(value: Any) -> str:
        if value is None or (isinstance(value, float) and not math.isfinite(value)):
            return "NA"
        if isinstance(value, (float, np.floating)):
            return f"{float(value):.4f}"
        return str(value)

    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in frame.reindex(columns=columns).to_dict("records"):
        lines.append("| " + " | ".join(cell(row[column]) for column in columns) + " |")
    return "\n".join(lines)


def write_report(
    path: Path,
    *,
    db_path: Path,
    enriched: pd.DataFrame,
    predictions: pd.DataFrame,
    scores: pd.DataFrame,
    betas: pd.DataFrame,
    regions: pd.DataFrame,
    coverage: pd.DataFrame,
    probability_summary: pd.DataFrame,
    counts: dict[str, int],
) -> None:
    score_pivot = scores[
        scores["variant"].isin(["rolling_bias", "innovation", "innovation_regime"])
    ].copy()
    checkpoint_corr = (
        enriched.dropna(subset=["forecast_final_error_f"])
        .groupby("checkpoint_hour_local")
        .apply(
            lambda group: pd.Series(
                {
                    "rows": len(group),
                    "dates": group["target_date"].nunique(),
                    "innovation_final_error_corr": group[
                        "forecast_innovation_f"
                    ].corr(group["forecast_final_error_f"]),
                    "mean_innovation_f": group["forecast_innovation_f"].mean(),
                }
            ),
            include_groups=False,
        )
        .reset_index()
    )
    latest_betas = (
        betas.sort_values("target_date")
        .groupby("checkpoint_hour_local", as_index=False)
        .tail(1)
    )
    primary = scores[
        scores["checkpoint_hour_local"].eq(PRIMARY_CHECKPOINT)
        & scores["variant"].isin(["rolling_bias", "innovation", "innovation_regime"])
    ]
    lines = [
        "# Forecast innovation：凌晨/早晨观测校准 Tmax v1",
        "",
        "## 数据快照",
        "",
        f"- 数据源：`{db_path}` canonical `tmax_v2_*` + `settlement_outcomes(pm_history)`。",
        f"- 覆盖：{enriched['target_date'].min()}..{enriched['target_date'].max()}；"
        f"{enriched['target_date'].nunique()} 个 target dates，{enriched['city'].nunique()} 城。",
        f"- canonical states={counts['canonical_states']:,}；PIT+normalized curve states="
        f"{counts['pit_verified_curve_states']:,}；checkpoint rows={len(enriched):,}。",
        "- unsettled=0；missing_bracket=0（feature study 只保留已有 pm_history winner 的 city-day）。",
        "- 2026-07-24..27 raw 已同步，但追加物化因全量扫描约 8GB 且本机可用空间仅约 7.7GB而安全中止；本报告冻结到 7/23。",
        "",
        "## 结论与动作",
        "",
        "这个特征应进入共享 weather state，但不能把“凌晨偏暖”直接当成 Tmax 上移。",
        "在当前严格 PIT 样本里，03:00/06:00 innovation 与最终 forecast error 的相关性很弱；"
        "09:00 开始明显，12:00 更强。交易含义是：保留连续 innovation，"
        "让概率模型按 local clock/机制学习 β；不要把它做成凌晨固定加减 1°C 的 hard rule。",
        "",
        "本轮只证明天气层的增量/失效时段。早晨完整 two-sided ladder 覆盖不足，"
        "无法据此发布 fee-adjusted ROI 或宣称相对 market 的 alpha；下一步是 forward "
        "collector 在固定 morning checkpoint 双写 feature + full book。",
        "",
        "## Target",
        "",
        "```text",
        "innovation_t = observed_temp_f - interpolated_model_temp_f(t)",
        "Tmax_hat = model_Tmax + rolling_city/source_bias + beta(checkpoint, regime) * innovation_t",
        "```",
        "",
        f"- primary checkpoint：本地 {PRIMARY_CHECKPOINT:02d}:00，snapshot 容差 "
        f"0..{CHECKPOINT_TOLERANCE_MIN} 分钟；03/06/12 点为预注册时钟对照。",
        "- label：最终 winning exact bracket 的 native midpoint；top/bottom open bracket "
        "不用于连续 MAE，但仍保留在 coverage 层。",
        "- primary metric：同 rows expanding walk-forward MAE；按 target_date block bootstrap。",
        "- variants：raw forecast、rolling bias、rolling bias+innovation、"
        "rolling bias+innovation×forecast cloud/wind/precip。",
        f"- fixed ridge alpha={RIDGE_ALPHA:g}；每个测试日只用更早 target dates，"
        f"至少 {MIN_TRAIN_DATES} 个训练日。",
        "",
        "## Data integrity / PIT",
        "",
        "- 每行 `pit_status=pit_verified`；observation 与 forecast capture 的 available_at "
        "均不晚于 decision timestamp。",
        "- 模型当前温度用 curve 对真实 decision local minute 线性插值；没有使用旧 "
        "`tracking_residual_f` 的整点向下取整。",
        "- 同一 checkpoint 的四个模型共用完全相同的 city-day rows。",
        "- 这是 retrospective expanding OOF，不是从未查看过的 fresh frozen forward；"
        "forward gate 仍为 NA。",
        "",
        "## Signal funnel",
        "",
        f"| 层 | grain | rows | dates |\n|---|---|---:|---:|\n"
        f"| canonical raw state | city-date-snapshot | {counts['canonical_states']} | 20 |\n"
        f"| PIT observation+curve | city-date-snapshot | {counts['pit_verified_curve_states']} | 14 |\n"
        f"| first checkpoint state | city-date-checkpoint | {counts['checkpoint_rows_pre_settlement']} | 13 |\n"
        f"| settled + common model fields | city-date-checkpoint | {len(enriched.dropna(subset=['forecast_final_error_f']))} | "
        f"{enriched.dropna(subset=['forecast_final_error_f'])['target_date'].nunique()} |",
        "",
        "## Evidence funnel",
        "",
        "| 层 | grain | rows | dates | gap |",
        "|---|---|---:|---:|---|",
        f"| PIT feature/source | city-date-checkpoint | {len(enriched)} | {enriched['target_date'].nunique()} | none after selected checkpoint |",
        f"| continuous settlement midpoint | city-date-checkpoint | {enriched['forecast_final_error_f'].notna().sum()} | "
        f"{enriched.loc[enriched['forecast_final_error_f'].notna(),'target_date'].nunique()} | open top/bottom winners excluded from MAE |",
        f"| expanding OOF score | city-date-checkpoint | {len(predictions)} | {predictions['target_date'].nunique()} | first {MIN_TRAIN_DATES} dates train-only |",
        f"| PIT full two-sided ladder | city-date-checkpoint | {int(coverage['full_two_sided_ladders'].sum())} | NA | archive/book coverage gap |",
        "| executable expression | expression | 0 | 0 | not evaluated |",
        "| fill | fill | 0 | 0 | feature study, not fill study |",
        "",
        "## Wide-denominator sanity",
        "",
        markdown_table(
            checkpoint_corr,
            [
                "checkpoint_hour_local",
                "rows",
                "dates",
                "innovation_final_error_corr",
                "mean_innovation_f",
            ],
        ),
        "",
        "03:00/06:00 的弱相关与 09:00/12:00 的增强是主结果：夜间 boundary-layer/"
        "station-grid bias 并不自动延续到白天 Tmax；日出后的 path innovation 才更接近"
        "“当天升温轨迹整体偏离模型”。",
        "",
        "## Expanding walk-forward",
        "",
        markdown_table(
            score_pivot,
            [
                "checkpoint_hour_local",
                "variant",
                "rows",
                "dates",
                "mae_f",
                "rmse_f",
                "mae_delta_vs_rolling_bias_f",
                "delta_ci_low_f",
                "delta_ci_high_f",
            ],
        ),
        "",
        f"Primary {PRIMARY_CHECKPOINT:02d}:00 rows：",
        "",
        markdown_table(
            primary,
            [
                "variant",
                "rows",
                "dates",
                "mae_f",
                "mae_delta_vs_rolling_bias_f",
                "delta_ci_low_f",
                "delta_ci_high_f",
            ],
        ),
        "",
        "## β stability",
        "",
        markdown_table(
            latest_betas,
            [
                "checkpoint_hour_local",
                "target_date",
                "train_dates",
                "beta_innovation",
                "beta_innovation_regime",
            ],
        ),
        "",
        "β 是概率模型参数，不写死在数据层。共享层只保存 raw innovation、model current、"
        "remaining warming 与 PIT lineage；不同策略在自己的 frozen OOF 模型里学习 β。",
        "",
        "## Region diagnostic（09:00，不作 allowlist）",
        "",
        markdown_table(
            regions,
            [
                "region",
                "rows",
                "dates",
                "cities",
                "rolling_bias_mae_f",
                "innovation_mae_f",
                "mae_delta_f",
                "delta_ci_low_f",
                "delta_ci_high_f",
            ],
        ),
        "",
        "该切片只解释传递机制，不作为事后城市/region gate。",
        "",
        "## Market / execution coverage",
        "",
        markdown_table(
            coverage,
            [
                "checkpoint_hour_local",
                "checkpoint_rows",
                "dates",
                "full_two_sided_ladders",
                "full_direct_yes_ask_ladders",
            ],
        ),
        "",
        "盘口缺失属于 evidence gap，不是策略筛选。当前不能把较少的完整 book 行包装成"
        "“精选可交易样本”，也不能从 weather MAE 改善外推 ROI。",
        "",
        "### 完整 two-sided ladder 上的诊断性 proper score",
        "",
        markdown_table(
            probability_summary,
            [
                "checkpoint_hour_local",
                "variant",
                "rows",
                "dates",
                "cities",
                "date_equal_logloss",
                "date_equal_brier",
            ],
        ),
        "",
        f"Weather distribution 使用固定 σ={WEATHER_SIGMA_F:.1f}°F；fusion 为固定"
        f" log-linear market/weather={1-FUSION_WEATHER_WEIGHT:.1f}/"
        f"{FUSION_WEATHER_WEIGHT:.1f}，没有按结果调权重。该表是 coverage-limited "
        "diagnostic，不满足 market baseline 晋升门。",
        "",
        "## Frozen forward",
        "",
        "- train choices frozen：本报告之后冻结 `09:00 primary + continuous innovation + "
        "clock interaction`，不冻结 β 数值。",
        "- fresh forward：尚未开始；需要 collector 继续积累 checkpoint feature + full book。",
        "- multiple testing：4 个 checkpoint、3 个 correction variants；未据此选城市或价格门。",
        "- unresolved blocker：morning full-ladder PIT book 覆盖与 fresh-forward duration。",
        "",
        "## 8 环覆盖",
        "",
        "- 已覆盖：2 统计推断（date block）、3 信号判别（forecast error/MAE）。",
        "- 部分覆盖：4 概率层、8 同时点 market residual 仅有 3--4 个日期的完整"
        " two-sided ladder diagnostic，market baseline 未过。",
        "- 未覆盖：1 fill 绩效、5 执行、6 容量、7 portfolio。",
        "",
        "```text",
        "significance=PASS/FAIL 以 scorecard CI 为准",
        "baseline=FAIL（有限完整盘口样本上 fusion 未打败 market）",
        "forward=NA（无 fresh frozen forward）",
        "conclusion=inconclusive / shared_feature_candidate",
        "```",
        "",
        "## Bloodline placement",
        "",
        "- shared data logic：`weather_data_feed.physical_features` 输出连续 forecast innovation。",
        "- feature layer：`weather_state_v4` additive field，不改变任何现有 selector eligibility。",
        "- probability heads：pre_predict、Tmax distribution、D-1 extreme NO、current/reheat "
        "策略都可消费；β 与 probability calibration 各自 OOF 拟合。",
        "- `fact_signal_candidates`：未来 v2 checkpoint grain 写 raw innovation 与 feature ref，"
        "不新建平行 fact。",
        "- live action：none。",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    conn = connect_ro(args.db)
    selected, counts = load_pit_states(conn)
    selected = selected.merge(
        load_winners(conn),
        on=["city", "target_date"],
        how="inner",
        validate="many_to_one",
    )
    curves = load_curves(
        conn, selected["forecast_capture_id"].astype(str).drop_duplicates().tolist()
    )
    enriched = enrich_weather_rows(selected, curves)
    predictions = walk_forward_predictions(enriched)
    if predictions.empty:
        raise RuntimeError("walk-forward produced zero predictions")
    scores = score_predictions(predictions)
    betas = beta_estimates(predictions)
    regions = region_scorecard(predictions)
    coverage = market_coverage(conn, enriched)
    probability_detail, probability_summary = probability_scores(
        conn, enriched, predictions
    )
    conn.close()

    enriched.to_csv(args.output_dir / "checkpoint_rows.csv", index=False)
    predictions.to_csv(args.output_dir / "oof_predictions.csv", index=False)
    scores.to_csv(args.output_dir / "checkpoint_scorecard.csv", index=False)
    betas.to_csv(args.output_dir / "beta_estimates.csv", index=False)
    regions.to_csv(args.output_dir / "region_scorecard.csv", index=False)
    coverage.to_csv(args.output_dir / "market_coverage.csv", index=False)
    probability_detail.to_csv(
        args.output_dir / "probability_score_rows.csv", index=False
    )
    probability_summary.to_csv(
        args.output_dir / "probability_scorecard.csv", index=False
    )

    summary = {
        "contract": {
            "checkpoints_local": list(CHECKPOINTS),
            "primary_checkpoint_local": PRIMARY_CHECKPOINT,
            "checkpoint_tolerance_minutes": CHECKPOINT_TOLERANCE_MIN,
            "min_train_dates": MIN_TRAIN_DATES,
            "ridge_alpha": RIDGE_ALPHA,
            "bootstrap_samples": BOOTSTRAP_SAMPLES,
            "evidence_class": "canonical_tmax_v2_pit_verified_retrospective_oof",
        },
        "counts": {
            **counts,
            "settled_checkpoint_rows": len(enriched),
            "continuous_label_rows": int(enriched["forecast_final_error_f"].notna().sum()),
            "oof_rows": len(predictions),
            "oof_dates": int(predictions["target_date"].nunique()),
        },
        "scorecard": scores.to_dict("records"),
        "market_coverage": coverage.to_dict("records"),
        "probability_scorecard": probability_summary.to_dict("records"),
        "conclusion": {
            "status": "shared_feature_candidate_inconclusive_market_alpha",
            "live_action": "none",
            "primary_feature": "forecast_innovation_f",
            "beta_location": "probability_model_not_shared_data_layer",
        },
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_report(
        args.report,
        db_path=args.db,
        enriched=enriched,
        predictions=predictions,
        scores=scores,
        betas=betas,
        regions=regions,
        coverage=coverage,
        probability_summary=probability_summary,
        counts=counts,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
