#!/usr/bin/env python3
"""Forecast-curve morphology and peak-clock alias research.

The study separates two questions:

1. Whether unusual full-day forecast shapes add out-of-sample probability
   information beyond the frozen current-YES core and same-row market.
2. Whether a decision-time global-peak clock hides a future local heat lobe
   that can still leave the current exact bracket upward.

It is research-only.  It does not change a selector, start a collector, place
an order, or alter production state.
"""

from __future__ import annotations

import glob
import hashlib
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.signal import find_peaks


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.reheat_risk import (  # noqa: E402
    research_current_yes_core_carry_overshoot_missing_mechanisms_v2 as v2,
)
from weather_data_feed.market_brackets import parse_market_bracket  # noqa: E402


RESEARCH_ID = "intraday_forecast_curve_morphology_v1"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated" / RESEARCH_ID
REPORT = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-28-intraday-forecast-curve-morphology-v1.md"
)
RESULT_JSON = REPORT.with_suffix(".json")
LEDGER = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "current_yes_core_carry_overshoot_missing_mechanisms_v2/"
    "feature_ledger.csv"
)
FORWARD_SCORES = (
    ROOT
    / "runtime/weather_edge_v1/current_yes_core_carry_tiny_live_v2/"
    "pre_live_scores.jsonl"
)
DB = ROOT / "runtime/weather.db"
SHARD_GLOB = str(
    ROOT
    / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/"
    "feature_factory_*/reheat_feature_rows.csv"
)

SEED = 20260728
MIN_TRAIN_DATES = 8
BOOTSTRAP_REPS = 5000
EPS = 1e-6
PREREG_CREATED_AT_UTC = "2026-07-28T13:00:00Z"

MORPHOLOGY_FEATURES = [
    "global_peak_hour_sin",
    "global_peak_hour_cos",
    "afternoon_lobe_gap_f",
    "inter_lobe_valley_depth_f",
    "prominent_peak_count",
    "near_max_hours_1f",
    "peak_separation_hours",
]
BOUNDARY_FEATURES = MORPHOLOGY_FEATURES + [
    "assigned_forecast_exit_margin_ticks",
    "assigned_curve_hours_above_exit",
    "assigned_curve_heat_area_ticks",
    "assigned_curve_first_exit_delta_hours",
]
MODEL_SPECS = {
    "morphology": MORPHOLOGY_FEATURES,
    "boundary_morphology": BOUNDARY_FEATURES,
}


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def parse_utc_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def temperature_points_from_cache(
    path_value: Any, target_date: str, cache: dict[str, Any]
) -> list[tuple[int, float]]:
    if path_value is None or str(path_value) in {"", "nan", "None"}:
        return []
    path = Path(str(path_value))
    if not path.exists():
        return []
    key = str(path)
    if key not in cache:
        cache[key] = json.loads(path.read_text(encoding="utf-8"))
    payload = cache[key]
    hourly = payload.get("hourly") or {}
    unit = str((payload.get("hourly_units") or {}).get("temperature_2m") or "°F")
    rows: list[tuple[int, float]] = []
    for raw_time, raw_temperature in zip(
        hourly.get("time") or [], hourly.get("temperature_2m") or []
    ):
        if (
            raw_temperature is None
            or not str(raw_time).startswith(str(target_date))
        ):
            continue
        try:
            hour = int(str(raw_time)[11:13])
            temperature = float(raw_temperature)
        except (TypeError, ValueError, IndexError):
            continue
        temperature_f = (
            temperature if "f" in unit.lower() else temperature * 9.0 / 5.0 + 32.0
        )
        rows.append((hour, temperature_f))
    return rows


def temperature_points_from_live_curve(
    curve: Any,
) -> list[tuple[int, float]]:
    rows: list[tuple[int, float]] = []
    for item in curve or []:
        try:
            rows.append(
                (
                    int(str(item["time_local"])[11:13]),
                    float(item["temperature_f"]),
                )
            )
        except (KeyError, TypeError, ValueError, IndexError):
            continue
    return rows


def curve_morphology(points: list[tuple[int, float]]) -> dict[str, Any]:
    if len(points) < 20:
        return {"curve_morphology_status": "insufficient_hourly_points"}
    points = sorted(points)
    hours = np.array([item[0] for item in points], dtype=int)
    values = np.array([item[1] for item in points], dtype=float)
    global_index = int(np.argmax(values))
    global_hour = int(hours[global_index])
    global_max = float(values[global_index])

    early_indices = np.where(hours <= 6)[0]
    afternoon_indices = np.where((hours >= 12) & (hours <= 18))[0]
    early_index = int(early_indices[np.argmax(values[early_indices])])
    afternoon_index = int(
        afternoon_indices[np.argmax(values[afternoon_indices])]
    )
    early_hour = int(hours[early_index])
    afternoon_hour = int(hours[afternoon_index])
    early_max = float(values[early_index])
    afternoon_max = float(values[afternoon_index])
    lo, hi = sorted([early_index, afternoon_index])
    inter_lobe_valley = float(values[lo : hi + 1].min())
    valley_depth = min(early_max, afternoon_max) - inter_lobe_valley

    raw_peaks = list(find_peaks(values, prominence=0.5)[0])
    if values[0] > values[1]:
        raw_peaks.insert(0, 0)
    if values[-1] > values[-2]:
        raw_peaks.append(len(values) - 1)
    prominent: list[int] = []
    for peak in raw_peaks:
        if peak == 0:
            local_floor = float(values[: min(len(values), 7)].min())
            prominence = float(values[peak] - local_floor)
        elif peak == len(values) - 1:
            local_floor = float(values[max(0, peak - 6) :].min())
            prominence = float(values[peak] - local_floor)
        else:
            left_floor = float(values[: peak + 1].min())
            right_floor = float(values[peak:].min())
            prominence = min(
                float(values[peak] - left_floor),
                float(values[peak] - right_floor),
            )
        if prominence >= 2.0:
            prominent.append(peak)
    near_global = [
        peak
        for peak in prominent
        if values[peak] >= global_max - 3.0
        and abs(int(hours[peak]) - global_hour) >= 4
    ]
    peak_separation = (
        float(
            max(
                abs(int(hours[left]) - int(hours[right]))
                for left in prominent
                for right in prominent
            )
        )
        if len(prominent) >= 2
        else 0.0
    )
    afternoon_gap = global_max - afternoon_max
    near_max_hours = int((values >= global_max - 1.0).sum())

    if (
        global_hour <= 6
        and afternoon_max >= global_max - 2.0
        and valley_depth >= 3.0
    ):
        shape = "overnight_peak_afternoon_lobe"
    elif (
        7 <= global_hour <= 11
        and afternoon_max >= global_max - 2.0
        and valley_depth >= 3.0
    ):
        shape = "morning_peak_afternoon_reheat"
    elif global_hour >= 18:
        shape = "late_peak_or_advection"
    elif near_max_hours >= 4:
        shape = "broad_plateau"
    elif near_global:
        shape = "multi_peak_other"
    elif 12 <= global_hour <= 17:
        shape = "canonical_afternoon_single"
    else:
        shape = "irregular_other"

    angle = 2.0 * math.pi * global_hour / 24.0
    return {
        "curve_morphology_status": "ok",
        "curve_shape": shape,
        "global_peak_hour": global_hour,
        "global_peak_f": global_max,
        "early_peak_hour": early_hour,
        "early_peak_f": early_max,
        "afternoon_peak_hour": afternoon_hour,
        "afternoon_peak_f": afternoon_max,
        "afternoon_lobe_gap_f": afternoon_gap,
        "inter_lobe_valley_f": inter_lobe_valley,
        "inter_lobe_valley_depth_f": valley_depth,
        "prominent_peak_count": len(prominent),
        "near_max_hours_1f": near_max_hours,
        "peak_separation_hours": peak_separation,
        "global_peak_hour_sin": math.sin(angle),
        "global_peak_hour_cos": math.cos(angle),
    }


def add_historical_morphology(frame: pd.DataFrame) -> pd.DataFrame:
    payload_cache: dict[str, Any] = {}
    morphology_cache: dict[tuple[str, str], dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        model = str(row["forecast_assigned_model"])
        path = row.get(f"{model}_forecast_cache_path")
        key = (str(path), str(row["target_date"]))
        if key not in morphology_cache:
            points = temperature_points_from_cache(
                path, str(row["target_date"]), payload_cache
            )
            morphology_cache[key] = curve_morphology(points)
        rows.append(morphology_cache[key])
    return pd.concat(
        [frame.reset_index(drop=True), pd.DataFrame(rows)], axis=1
    )


def target_date_block_rate_delta(
    frame: pd.DataFrame, shape: str
) -> dict[str, Any]:
    sample = frame[
        frame["curve_shape"].isin([shape, "canonical_afternoon_single"])
    ].copy()
    dates = sorted(sample["target_date"].unique())
    if not dates:
        return {"rate_delta_vs_canonical": None, "ci95": [None, None]}

    counts = (
        sample.assign(
            shape_n=sample["curve_shape"].eq(shape).astype(int),
            shape_y=(
                sample["curve_shape"].eq(shape) * sample["overshoot"]
            ).astype(int),
            canonical_n=sample["curve_shape"]
            .eq("canonical_afternoon_single")
            .astype(int),
            canonical_y=(
                sample["curve_shape"].eq("canonical_afternoon_single")
                * sample["overshoot"]
            ).astype(int),
        )
        .groupby("target_date", as_index=False)[
            ["shape_n", "shape_y", "canonical_n", "canonical_y"]
        ]
        .sum()
        .set_index("target_date")
        .reindex(dates, fill_value=0)
    )
    values = counts.to_numpy(float)
    shape_total = values[:, 0].sum()
    canonical_total = values[:, 2].sum()
    point = (
        values[:, 1].sum() / shape_total
        - values[:, 3].sum() / canonical_total
        if shape_total and canonical_total
        else math.nan
    )
    rng = np.random.default_rng(SEED)
    draws: list[float] = []
    for _ in range(BOOTSTRAP_REPS):
        chosen = rng.integers(0, len(values), len(values))
        totals = values[chosen].sum(axis=0)
        if totals[0] and totals[2]:
            draws.append(
                float(totals[1] / totals[0] - totals[3] / totals[2])
            )
    return {
        "rate_delta_vs_canonical": point,
        "ci95": (
            np.quantile(draws, [0.025, 0.975]).tolist()
            if draws
            else [None, None]
        ),
    }


def summarize_shapes(
    historical: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    first = (
        historical.sort_values(
            ["target_date", "city", "decision_snapshot_dt"]
        )
        .drop_duplicates(["city", "target_date"], keep="first")
        .copy()
    )
    rows: list[dict[str, Any]] = []
    for shape, sample in first.groupby("curve_shape", dropna=False):
        delta = target_date_block_rate_delta(first, str(shape))
        rows.append(
            {
                "curve_shape": shape,
                "city_days": len(sample),
                "dates": sample["target_date"].nunique(),
                "overshoots": int(sample["overshoot"].sum()),
                "overshoot_rate": float(sample["overshoot"].mean()),
                "mean_core_risk": float(sample["p_over_core"].mean()),
                "mean_market_risk": float(sample["p_over_market"].mean()),
                **delta,
            }
        )
    summary = pd.DataFrame(rows).sort_values(
        "city_days", ascending=False
    )
    selected = first[first["frozen_baseline_selected"]].copy()
    selected_summary = (
        selected.groupby("curve_shape", as_index=False)
        .agg(
            city_days=("city", "size"),
            dates=("target_date", "nunique"),
            overshoots=("overshoot", "sum"),
            overshoot_rate=("overshoot", "mean"),
            mean_core_risk=("p_over_core", "mean"),
            mean_market_risk=("p_over_market", "mean"),
        )
        .sort_values("city_days", ascending=False)
    )
    return summary, selected_summary


def expanding_oof(
    historical: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = sorted(historical["target_date"].unique())
    predictions: list[pd.DataFrame] = []
    folds: list[dict[str, Any]] = []
    keep = [
        "opportunity_id",
        "city",
        "target_date",
        "overshoot",
        "p_over_core",
        "p_over_market",
    ]
    for index, target_date in enumerate(dates):
        if index < MIN_TRAIN_DATES:
            continue
        train = historical[historical["target_date"].lt(target_date)].copy()
        test = historical[historical["target_date"].eq(target_date)].copy()
        if train["overshoot"].nunique() < 2 or test.empty:
            continue
        result = test[keep].copy()
        for name, features in MODEL_SPECS.items():
            probability, fit = v2.fit_offset_residual(train, test, features)
            result[f"p_over_{name}"] = probability
            folds.append(
                {
                    "target_date": target_date,
                    "model": name,
                    "train_rows": len(train),
                    "train_city_days": train.groupby(
                        ["city", "target_date"]
                    ).ngroups,
                    "train_dates": train["target_date"].nunique(),
                    "test_rows": len(test),
                    "coefficient_norm": fit.get("coefficient_norm"),
                    "columns": json.dumps(fit.get("columns", [])),
                    "coefficients": json.dumps(fit.get("coefficients", [])),
                }
            )
        predictions.append(result)
    return pd.concat(predictions, ignore_index=True), pd.DataFrame(folds)


def score_models(oof: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for model in ["p_over_core", "p_over_market"] + [
        f"p_over_{name}" for name in MODEL_SPECS
    ]:
        metrics = v2.score_metrics(oof, model)
        row = {"model": model, **metrics}
        if model not in {"p_over_core", "p_over_market"}:
            row.update(
                {
                    f"vs_core_{key}": value
                    for key, value in v2.paired_delta(
                        oof, model, "p_over_core"
                    ).items()
                }
            )
            row.update(
                {
                    f"vs_market_{key}": value
                    for key, value in v2.paired_delta(
                        oof, model, "p_over_market"
                    ).items()
                }
            )
        rows.append(row)
    return pd.DataFrame(rows)


def load_d1_quote_context() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    wanted = [
        "city",
        "target_date",
        "decision_hour_local",
        "bracket",
        "current_bracket",
        "outcome",
        "d1_no_bracket",
        "d1_no_bid",
        "d1_no_ask",
        "d1_hit",
    ]
    for path in sorted(glob.glob(SHARD_GLOB)):
        header = set(pd.read_csv(path, nrows=0).columns)
        frame = pd.read_csv(
            path,
            usecols=[column for column in wanted if column in header],
            low_memory=False,
        )
        frame["target_date"] = frame["target_date"].astype(str)
        frame = frame[
            frame["bracket"].astype(str).eq(
                frame["current_bracket"].astype(str)
            )
            & frame["outcome"].astype(str).str.lower().eq("yes")
        ]
        frames.append(frame)
    return pd.concat(frames, ignore_index=True).drop_duplicates(
        ["city", "target_date", "decision_hour_local"], keep="last"
    )


def historical_expression_summary(
    historical: pd.DataFrame,
) -> pd.DataFrame:
    first = (
        historical.sort_values(
            ["target_date", "city", "decision_snapshot_dt"]
        )
        .drop_duplicates(["city", "target_date"], keep="first")
        .copy()
    )
    quotes = load_d1_quote_context()
    work = first.merge(
        quotes[
            [
                "city",
                "target_date",
                "decision_hour_local",
                "d1_no_bracket",
                "d1_no_bid",
                "d1_hit",
            ]
        ],
        on=["city", "target_date", "decision_hour_local"],
        how="left",
        validate="one_to_one",
    )
    work["d1_yes_ask_proxy"] = 1.0 - pd.to_numeric(
        work["d1_no_bid"], errors="coerce"
    )
    valid = (
        work["d1_yes_ask_proxy"].between(0.001, 0.999)
        & work["d1_hit"].notna()
    )
    work.loc[~valid, "d1_yes_ask_proxy"] = np.nan
    work["fee_per_share"] = (
        0.05
        * work["d1_yes_ask_proxy"]
        * (1.0 - work["d1_yes_ask_proxy"])
    )
    work["cost_per_share"] = (
        work["d1_yes_ask_proxy"] + work["fee_per_share"]
    )
    work["pnl_per_share"] = work["d1_hit"] - work["cost_per_share"]
    rows: list[dict[str, Any]] = []
    for shape, sample in work.groupby("curve_shape"):
        quoted = sample.dropna(
            subset=["d1_yes_ask_proxy", "d1_hit", "cost_per_share"]
        )
        cost = float(quoted["cost_per_share"].sum())
        pnl = float(quoted["pnl_per_share"].sum())
        rows.append(
            {
                "curve_shape": shape,
                "city_days": len(sample),
                "quoted_city_days": len(quoted),
                "dates": quoted["target_date"].nunique(),
                "wins": int(quoted["d1_hit"].sum()),
                "mean_yes_ask_proxy": float(
                    quoted["d1_yes_ask_proxy"].mean()
                ),
                "fee_adjusted_pnl_per_share": pnl,
                "fee_adjusted_roi": pnl / cost if cost else None,
                "depth_status": "missing_bid_depth_not_executable_claim",
            }
        )
    return pd.DataFrame(rows).sort_values("quoted_city_days", ascending=False)


def settlement_rows() -> pd.DataFrame:
    connection = sqlite3.connect(
        f"file:{DB}?mode=ro", uri=True, timeout=1.0
    )
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=1000")
    query = """
        SELECT
            city,
            event_date AS target_date,
            bracket,
            MAX(final_yes) AS final_yes
        FROM fact_signal_candidates
        WHERE settlement_status='settled'
          AND final_yes IS NOT NULL
        GROUP BY city, event_date, bracket
    """
    frame = pd.read_sql_query(query, connection)
    connection.close()
    frame["target_date"] = frame["target_date"].astype(str)
    frame["bracket"] = frame["bracket"].astype(str)
    return frame


def winning_settlement_rows() -> pd.DataFrame:
    connection = sqlite3.connect(
        f"file:{DB}?mode=ro", uri=True, timeout=1.0
    )
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=1000")
    query = """
        SELECT
            city,
            event_date AS target_date,
            bracket AS winning_bracket
        FROM fact_signal_candidates
        WHERE settlement_status='settled'
          AND final_yes=1
        GROUP BY city, event_date, bracket
    """
    frame = pd.read_sql_query(query, connection)
    connection.close()
    frame["target_date"] = frame["target_date"].astype(str)
    frame["winning_bracket"] = frame["winning_bracket"].astype(str)
    return frame


def bracket_level(bracket: Any) -> float | None:
    parsed = parse_market_bracket(str(bracket))
    if parsed is None or parsed.low is None:
        return None
    return float(parsed.low)


def live_exit_threshold(bracket: Any, unit: str) -> float | None:
    parsed = parse_market_bracket(str(bracket))
    if parsed is None:
        return None
    upper = parsed.high if parsed.high is not None else parsed.low
    if upper is None:
        return None
    return float(upper) + (0.5 if str(unit).upper() == "C" else 1.0)


def add_live_curve_fields(record: dict[str, Any]) -> dict[str, Any]:
    points_f = temperature_points_from_live_curve(record.get("hourly_curve"))
    morphology = curve_morphology(points_f)
    threshold = live_exit_threshold(
        record.get("current_bracket"), str(record.get("unit") or "C")
    )
    decision_hour = float(
        record.get(
            "decision_hour_local_float",
            record.get("decision_hour_local", math.nan),
        )
    )
    unit = str(record.get("unit") or "C").upper()
    points_native = [
        (hour, (temperature - 32.0) * 5.0 / 9.0 if unit == "C" else temperature)
        for hour, temperature in points_f
    ]
    future_start_hour = int(math.ceil(decision_hour))
    future = [
        value
        for hour, value in points_native
        if hour >= future_start_hour
    ]
    future_max = max(future) if future else math.nan
    global_peak_hour = morphology.get("global_peak_hour")
    peak_past_hours = (
        decision_hour - float(global_peak_hour)
        if global_peak_hour is not None
        else math.nan
    )
    alias_physics = bool(
        threshold is not None
        and math.isfinite(future_max)
        and math.isfinite(peak_past_hours)
        and peak_past_hours > 2.0
        and future_max >= threshold
    )
    decision_dt = parse_utc_datetime(record.get("decision_snapshot_ts_utc"))
    source_report_dt = parse_utc_datetime(record.get("source_report_ts_utc"))
    source_age_minutes = (
        (decision_dt - source_report_dt).total_seconds() / 60.0
        if decision_dt is not None and source_report_dt is not None
        else math.nan
    )
    cadence_minutes = record.get(
        "expected_report_cadence",
        record.get("observation_cadence_min"),
    )
    try:
        cadence_minutes = float(cadence_minutes)
    except (TypeError, ValueError):
        cadence_minutes = math.nan
    observation_lineage_valid = bool(
        str(record.get("obs_status") or "").strip().lower() == "ok"
        and math.isfinite(source_age_minutes)
        and source_age_minutes >= 0
        and math.isfinite(cadence_minutes)
        and cadence_minutes > 0
        and source_age_minutes <= cadence_minutes + 10.0
    )
    alias = alias_physics and observation_lineage_valid
    output = {
        "city": record.get("city"),
        "target_date": str(record.get("target_date")),
        "checkpoint_key": record.get("checkpoint_key"),
        "decision_snapshot_ts_utc": record.get("decision_snapshot_ts_utc"),
        "decision_hour_local": decision_hour,
        "source_report_ts_utc": record.get("source_report_ts_utc"),
        "source_fetched_at_utc": record.get("fetched_at_utc"),
        "stored_obs_age_min": record.get(
            "obs_age_min", record.get("obs_age_minutes")
        ),
        "source_age_recomputed_min": source_age_minutes,
        "expected_report_cadence_min": cadence_minutes,
        "observation_lineage_valid": observation_lineage_valid,
        "current_bracket": str(record.get("current_bracket")),
        "d1_bracket": str(record.get("d1_bracket")),
        "current_yes_bid": record.get("current_yes_bid"),
        "current_yes_ask": record.get("current_yes_ask"),
        "current_yes_bid_size": record.get("current_yes_bid_size"),
        "current_yes_ask_size": record.get("current_yes_ask_size"),
        "current_yes_book_status": record.get("current_yes_book_status"),
        "d1_no_bid": record.get("d1_no_bid"),
        "d1_no_ask": record.get("d1_no_ask"),
        "d1_no_bid_size": record.get("d1_no_bid_size"),
        "d1_no_ask_size": record.get("d1_no_ask_size"),
        "d1_no_book_status": record.get("d1_no_book_status"),
        "model_probability_hold": record.get("model_probability_hold"),
        "model_edge_after_fee_and_depth": record.get(
            "model_edge_after_fee_and_depth"
        ),
        "decision_status": record.get("decision_status"),
        "forecast_source": record.get("forecast_source"),
        "forecast_max_f": record.get("forecast_max_f"),
        "forecast_peak_delta_hours_local": record.get(
            "forecast_peak_delta_hours_local"
        ),
        "future_curve_start_hour_local": future_start_hour,
        "future_curve_max_native": future_max,
        "current_exit_threshold_native": threshold,
        "global_peak_past_hours": peak_past_hours,
        "peak_clock_alias_physics": alias_physics,
        "peak_clock_alias": alias,
        **morphology,
    }
    return output


def load_forward_scores() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    with FORWARD_SCORES.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not record.get("hourly_curve"):
                continue
            rows.append(add_live_curve_fields(record))
    frame = pd.DataFrame(rows)
    frame["decision_snapshot_dt"] = pd.to_datetime(
        frame["decision_snapshot_ts_utc"], utc=True, errors="coerce"
    )
    frame = (
        frame.sort_values("decision_snapshot_dt")
        .drop_duplicates(
            ["city", "target_date", "checkpoint_key"], keep="last"
        )
        .reset_index(drop=True)
    )
    settlements = settlement_rows()
    frame = frame.merge(
        settlements.rename(
            columns={
                "bracket": "current_bracket",
                "final_yes": "current_final_yes",
            }
        ),
        on=["city", "target_date", "current_bracket"],
        how="left",
        validate="many_to_one",
    )
    frame = frame.merge(
        winning_settlement_rows(),
        on=["city", "target_date"],
        how="left",
        validate="many_to_one",
    )
    frame["current_bracket_level"] = frame["current_bracket"].map(
        bracket_level
    )
    frame["winning_bracket_level"] = frame["winning_bracket"].map(
        bracket_level
    )
    frame["settlement_move_brackets"] = (
        frame["winning_bracket_level"] - frame["current_bracket_level"]
    )
    frame["settled_upward_exit"] = (
        frame["settlement_move_brackets"].gt(0).astype("boolean")
    )
    frame.loc[
        frame["winning_bracket"].isna(), "settled_upward_exit"
    ] = pd.NA
    frame = frame.merge(
        settlements.rename(
            columns={
                "bracket": "d1_bracket",
                "final_yes": "d1_final_yes",
            }
        ),
        on=["city", "target_date", "d1_bracket"],
        how="left",
        validate="many_to_one",
    )
    frame["current_no_ask_proxy"] = 1.0 - pd.to_numeric(
        frame["current_yes_bid"], errors="coerce"
    )
    frame["d1_yes_ask_proxy"] = 1.0 - pd.to_numeric(
        frame["d1_no_bid"], errors="coerce"
    )
    for prefix, price_column, label_column in [
        ("current_no", "current_no_ask_proxy", "current_final_yes"),
        ("d1_yes", "d1_yes_ask_proxy", "d1_final_yes"),
    ]:
        price = frame[price_column]
        frame[f"{prefix}_cost_per_share"] = price + 0.05 * price * (1 - price)
        label = (
            1.0 - frame[label_column]
            if prefix == "current_no"
            else frame[label_column]
        )
        frame[f"{prefix}_win"] = label
        frame[f"{prefix}_pnl_per_share"] = (
            label - frame[f"{prefix}_cost_per_share"]
        )
    return frame


def forward_summaries(
    forward: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    alias = forward[forward["peak_clock_alias"]].copy()
    first_alias = (
        alias.sort_values("decision_snapshot_dt")
        .drop_duplicates(["city", "target_date"], keep="first")
        .copy()
    )
    summary_rows = []
    for name, sample in [
        ("all_forward_checkpoints", forward),
        ("alias_checkpoints", alias),
        ("first_alias_city_day", first_alias),
    ]:
        settled = sample[sample["current_final_yes"].notna()]
        summary_rows.append(
            {
                "stage": name,
                "rows": len(sample),
                "city_days": sample.groupby(["city", "target_date"]).ngroups,
                "dates": sample["target_date"].nunique(),
                "settled_rows": len(settled),
                "settled_city_days": settled.groupby(
                    ["city", "target_date"]
                ).ngroups,
                "current_exact_losses": int(
                    settled["current_final_yes"].eq(0).sum()
                ),
                "current_exact_loss_rate": (
                    float(settled["current_final_yes"].eq(0).mean())
                    if len(settled)
                    else None
                ),
                "current_no_quote_rows": int(
                    sample["current_no_ask_proxy"].notna().sum()
                ),
                "d1_yes_quote_rows": int(
                    sample["d1_yes_ask_proxy"].notna().sum()
                ),
            }
        )
    summary = pd.DataFrame(summary_rows)
    expression_rows: list[dict[str, Any]] = []
    for expression in ["current_no", "d1_yes"]:
        price_column = (
            "current_no_ask_proxy"
            if expression == "current_no"
            else "d1_yes_ask_proxy"
        )
        sample = first_alias.dropna(
            subset=[
                price_column,
                f"{expression}_win",
                f"{expression}_cost_per_share",
            ]
        )
        sample = sample[sample[price_column].between(0.001, 0.999)]
        cost = float(sample[f"{expression}_cost_per_share"].sum())
        pnl = float(sample[f"{expression}_pnl_per_share"].sum())
        expression_rows.append(
            {
                "expression": expression,
                "quoted_settled_city_days": len(sample),
                "dates": sample["target_date"].nunique(),
                "wins": int(sample[f"{expression}_win"].sum()),
                "mean_ask_proxy": float(sample[price_column].mean()),
                "fee_adjusted_pnl_per_share": pnl,
                "fee_adjusted_roi": pnl / cost if cost else None,
                "depth_status": "bid_depth_not_stored_in_pre_live_score",
            }
        )
    expression = pd.DataFrame(expression_rows)
    chengdu = forward[
        forward["city"].eq("Chengdu")
        & forward["target_date"].eq("2026-07-27")
    ].copy()
    return summary, expression, chengdu


def build_payload() -> dict[str, Any]:
    historical = pd.read_csv(LEDGER, low_memory=False)
    historical["target_date"] = historical["target_date"].astype(str)
    historical = add_historical_morphology(historical)
    historical["historical_peak_clock_alias"] = (
        historical["forecast_peak_delta_hours_local"].gt(2.0)
        & historical["assigned_curve_hours_above_exit"].gt(0)
        & historical["assigned_curve_first_exit_delta_hours"].ge(0)
    )
    shape_summary, selected_shape_summary = summarize_shapes(historical)
    oof, folds = expanding_oof(historical)
    model_scores = score_models(oof)
    expression_summary = historical_expression_summary(historical)
    forward = load_forward_scores()
    forward_summary, forward_expression, chengdu = forward_summaries(forward)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    historical.to_csv(OUT_DIR / "historical_state_rows.csv", index=False)
    shape_summary.to_csv(OUT_DIR / "shape_summary.csv", index=False)
    selected_shape_summary.to_csv(
        OUT_DIR / "selected_shape_summary.csv", index=False
    )
    oof.to_csv(OUT_DIR / "oof_predictions.csv", index=False)
    folds.to_csv(OUT_DIR / "oof_folds.csv", index=False)
    model_scores.to_csv(OUT_DIR / "model_scores.csv", index=False)
    expression_summary.to_csv(
        OUT_DIR / "historical_expression_summary.csv", index=False
    )
    forward.to_csv(OUT_DIR / "forward_checkpoint_rows.csv", index=False)
    forward_summary.to_csv(OUT_DIR / "forward_funnel.csv", index=False)
    forward_expression.to_csv(
        OUT_DIR / "forward_expression_summary.csv", index=False
    )
    chengdu.to_csv(OUT_DIR / "chengdu_2026_07_27_timeline.csv", index=False)

    historical_first = historical.sort_values(
        ["target_date", "city", "decision_snapshot_dt"]
    ).drop_duplicates(["city", "target_date"], keep="first")
    historical_alias = historical_first[
        historical_first["historical_peak_clock_alias"]
    ]
    forward_alias_first = (
        forward[forward["peak_clock_alias"]]
        .sort_values("decision_snapshot_dt")
        .drop_duplicates(["city", "target_date"], keep="first")
    )
    forward_quarantined_first = (
        forward[
            forward["peak_clock_alias_physics"]
            & ~forward["observation_lineage_valid"]
        ]
        .sort_values("decision_snapshot_dt")
        .drop_duplicates(["city", "target_date"], keep="first")
    )
    forward_quarantined_first.to_csv(
        OUT_DIR / "forward_alias_quarantined_observation_lineage.csv",
        index=False,
    )
    forward_alias_first.to_csv(
        OUT_DIR / "forward_first_alias_city_days.csv", index=False
    )
    settled_forward_alias = forward_alias_first[
        forward_alias_first["current_final_yes"].notna()
    ]
    settled_forward_alias.to_csv(
        OUT_DIR / "forward_settled_first_alias_city_days.csv", index=False
    )
    forward_alias_by_date = (
        forward_alias_first.groupby("target_date", as_index=False)
        .agg(
            signal_city_days=("city", "size"),
            settled_city_days=("current_final_yes", "count"),
            upward_exits=(
                "settled_upward_exit",
                lambda values: int(
                    pd.Series(values, dtype="boolean").fillna(False).sum()
                ),
            ),
            current_no_quote_city_days=("current_no_ask_proxy", "count"),
            d1_yes_quote_city_days=("d1_yes_ask_proxy", "count"),
            current_yes_ask_001_city_days=(
                "current_yes_ask",
                lambda values: int(
                    pd.to_numeric(values, errors="coerce").le(0.001).sum()
                ),
            ),
        )
    )
    forward_alias_by_city = (
        forward_alias_first.groupby("city", as_index=False)
        .agg(
            signal_city_days=("target_date", "size"),
            dates=("target_date", "nunique"),
            settled_city_days=("current_final_yes", "count"),
            upward_exits=(
                "settled_upward_exit",
                lambda values: int(
                    pd.Series(values, dtype="boolean").fillna(False).sum()
                ),
            ),
            current_no_quote_city_days=("current_no_ask_proxy", "count"),
            d1_yes_quote_city_days=("d1_yes_ask_proxy", "count"),
        )
        .sort_values(["signal_city_days", "city"], ascending=[False, True])
    )
    forward_alias_by_date.to_csv(
        OUT_DIR / "forward_alias_by_date.csv", index=False
    )
    forward_alias_by_city.to_csv(
        OUT_DIR / "forward_alias_by_city.csv", index=False
    )
    latest_forward = str(forward["target_date"].max()) if len(forward) else None
    preregistration = {
        "research_id": RESEARCH_ID,
        "created_at_utc": PREREG_CREATED_AT_UTC,
        "status": "zero_notional_feature_collector_preregistration",
        "forward_start_target_date": "2026-07-29",
        "unit": "first city-day peak-clock alias checkpoint",
        "signal": (
            "global peak >2h past AND future hourly lobe reaches "
            "settlement-native current-exact upward exit boundary"
        ),
        "continuous_features": BOUNDARY_FEATURES,
        "primary_target": "current exact upward exit",
        "primary_probability_metric": "Brier and logloss vs same-row market",
        "expression_diagnostics": ["current NO", "d1 YES"],
        "minimum_forward_dates": 15,
        "minimum_fresh_book_coverage": 0.90,
        "promotion_rule": (
            "candidate must improve same-row market/core proper score with "
            "target-date block CI below zero; any expression additionally "
            "needs official fee, direct depth, and fee-adjusted ROI CI above zero"
        ),
        "multiple_testing": (
            "seven named shape labels are diagnostic; only the continuous "
            "boundary-relative model is primary"
        ),
    }
    prereg_path = OUT_DIR / "preregistration.json"
    prereg_path.write_text(
        json.dumps(json_ready(preregistration), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    payload = {
        "research_id": RESEARCH_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "data": {
            "historical_source": str(LEDGER.relative_to(ROOT)),
            "historical_source_sha256": sha256(LEDGER),
            "historical_rows": len(historical),
            "historical_city_days": historical.groupby(
                ["city", "target_date"]
            ).ngroups,
            "historical_dates": historical["target_date"].nunique(),
            "historical_start": str(historical["target_date"].min()),
            "historical_end": str(historical["target_date"].max()),
            "forward_source": str(FORWARD_SCORES.relative_to(ROOT)),
            "forward_rows": len(forward),
            "forward_dates": forward["target_date"].nunique(),
            "forward_start": str(forward["target_date"].min()),
            "forward_end": latest_forward,
            "db_mtime_utc": datetime.fromtimestamp(
                DB.stat().st_mtime, timezone.utc
            ).isoformat(),
            "sync_or_rebuild": False,
        },
        "historical_alias": {
            "city_days": len(historical_alias),
            "dates": historical_alias["target_date"].nunique(),
            "overshoots": int(historical_alias["overshoot"].sum()),
            "overshoot_rate": (
                float(historical_alias["overshoot"].mean())
                if len(historical_alias)
                else None
            ),
        },
        "forward_alias": {
            "city_days": len(forward_alias_first),
            "dates": forward_alias_first["target_date"].nunique(),
            "settled_city_days": len(settled_forward_alias),
            "settled_dates": settled_forward_alias[
                "target_date"
            ].nunique(),
            "current_exact_losses": int(
                settled_forward_alias["current_final_yes"].eq(0).sum()
            ),
            "settled_upward_exits": int(
                settled_forward_alias["settled_upward_exit"].sum()
            ),
            "settled_current_yes_ask_001_city_days": int(
                pd.to_numeric(
                    settled_forward_alias["current_yes_ask"], errors="coerce"
                )
                .le(0.001)
                .sum()
            ),
            "settled_current_no_quote_city_days": int(
                settled_forward_alias["current_no_ask_proxy"].notna().sum()
            ),
            "settlement_move_median_brackets": float(
                settled_forward_alias["settlement_move_brackets"].median()
            ),
            "current_exact_loss_rate": (
                float(
                    settled_forward_alias["current_final_yes"].eq(0).mean()
                )
                if len(settled_forward_alias)
                else None
            ),
            "current_no_direct_quote_city_days": forward_alias_first[
                "current_no_ask_proxy"
            ].notna().sum(),
            "d1_yes_direct_quote_city_days": forward_alias_first[
                "d1_yes_ask_proxy"
            ].notna().sum(),
        },
        "forward_lineage_quarantine": {
            "city_days": len(forward_quarantined_first),
            "dates": forward_quarantined_first["target_date"].nunique(),
            "settled_city_days": int(
                forward_quarantined_first["current_final_yes"].notna().sum()
            ),
            "source_age_median_min": float(
                forward_quarantined_first[
                    "source_age_recomputed_min"
                ].median()
            ),
            "source_age_min_min": float(
                forward_quarantined_first[
                    "source_age_recomputed_min"
                ].min()
            ),
            "source_age_max_min": float(
                forward_quarantined_first[
                    "source_age_recomputed_min"
                ].max()
            ),
        },
        "shape_summary": shape_summary.to_dict("records"),
        "selected_shape_summary": selected_shape_summary.to_dict("records"),
        "model_scores": model_scores.to_dict("records"),
        "historical_expression_summary": expression_summary.to_dict("records"),
        "forward_funnel": forward_summary.to_dict("records"),
        "forward_expression_summary": forward_expression.to_dict("records"),
        "forward_alias_by_date": forward_alias_by_date.to_dict("records"),
        "forward_alias_by_city": forward_alias_by_city.to_dict("records"),
        "chengdu_timeline": chengdu.to_dict("records"),
        "preregistration": {
            **preregistration,
            "sha256": sha256(prereg_path),
        },
    }
    RESULT_JSON.write_text(
        json.dumps(json_ready(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def pct(value: Any, digits: int = 1) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{100 * float(value):.{digits}f}%"


def number(value: Any, digits: int = 4) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{float(value):.{digits}f}"


def table(frame: pd.DataFrame, columns: list[str]) -> str:
    if frame.empty:
        return "_none_"
    work = frame[columns].copy()
    def cell(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, float) and not math.isfinite(value):
            return ""
        return str(value).replace("|", "\\|").replace("\n", " ")

    header = "| " + " | ".join(columns) + " |"
    divider = "|" + "|".join("---" for _ in columns) + "|"
    rows = [
        "| "
        + " | ".join(cell(value) for value in row)
        + " |"
        for row in work.itertuples(index=False, name=None)
    ]
    return "\n".join([header, divider, *rows])


def write_report(payload: dict[str, Any]) -> None:
    shape = pd.DataFrame(payload["shape_summary"])
    selected = pd.DataFrame(payload["selected_shape_summary"])
    scores = pd.DataFrame(payload["model_scores"])
    historical_expression = pd.DataFrame(
        payload["historical_expression_summary"]
    )
    forward_funnel = pd.DataFrame(payload["forward_funnel"])
    forward_expression = pd.DataFrame(
        payload["forward_expression_summary"]
    )
    forward_alias_by_date = pd.DataFrame(
        payload["forward_alias_by_date"]
    )
    forward_alias_by_city = pd.DataFrame(
        payload["forward_alias_by_city"]
    )
    for display in (forward_alias_by_date, forward_alias_by_city):
        if "upward_exits" in display:
            display["upward_exits"] = (
                pd.to_numeric(display["upward_exits"], errors="coerce")
                .fillna(0)
                .astype(int)
            )
    chengdu = pd.DataFrame(payload["chengdu_timeline"]).sort_values(
        "decision_snapshot_dt"
    )
    data = payload["data"]
    historical_alias = payload["historical_alias"]
    forward_alias = payload["forward_alias"]
    forward_lineage_quarantine = payload["forward_lineage_quarantine"]

    score_rows = []
    for _, row in scores.iterrows():
        score_rows.append(
            {
                "model": row["model"],
                "city_days": int(row["city_days"]),
                "dates": int(row["dates"]),
                "Brier": number(row["brier"]),
                "logloss": number(row["logloss"]),
                "AUC": number(row["auc"]),
                "ΔBrier vs core": number(
                    row.get(
                        "vs_core_candidate_minus_baseline_brier"
                    )
                ),
                "Δlogloss vs core": number(
                    row.get(
                        "vs_core_candidate_minus_baseline_logloss"
                    )
                ),
                "Brier CI": (
                    str(row.get("vs_core_brier_delta_ci95"))
                    if row["model"]
                    not in {"p_over_core", "p_over_market"}
                    else "NA"
                ),
            }
        )
    score_table = pd.DataFrame(score_rows)

    shape_display = shape.copy()
    shape_display["overshoot_rate"] = shape_display[
        "overshoot_rate"
    ].map(pct)
    shape_display["delta_vs_canonical"] = shape_display[
        "rate_delta_vs_canonical"
    ].map(pct)
    shape_display["ci95"] = shape_display["ci95"].astype(str)

    selected_display = selected.copy()
    if not selected_display.empty:
        selected_display["overshoot_rate"] = selected_display[
            "overshoot_rate"
        ].map(pct)

    expr_display = historical_expression.copy()
    expr_display["fee_adjusted_roi"] = expr_display[
        "fee_adjusted_roi"
    ].map(pct)
    forward_expr_display = forward_expression.copy()
    forward_expr_display["fee_adjusted_roi"] = forward_expr_display[
        "fee_adjusted_roi"
    ].map(pct)

    chengdu_display = chengdu.copy()
    if not chengdu_display.empty:
        chengdu_display["local_time"] = pd.to_datetime(
            chengdu_display["decision_snapshot_ts_utc"], utc=True
        ).dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d %H:%M")
        chengdu_display["current_no_ask_proxy"] = chengdu_display[
            "current_no_ask_proxy"
        ].round(3)
        chengdu_display["d1_yes_ask_proxy"] = chengdu_display[
            "d1_yes_ask_proxy"
        ].round(3)

    primary = scores[
        scores["model"].eq("p_over_boundary_morphology")
    ].iloc[0]
    brier_delta = primary[
        "vs_core_candidate_minus_baseline_brier"
    ]
    brier_ci = primary["vs_core_brier_delta_ci95"]
    logloss_delta = primary[
        "vs_core_candidate_minus_baseline_logloss"
    ]
    logloss_ci = primary["vs_core_logloss_delta_ci95"]
    chengdu_alias = chengdu[chengdu["peak_clock_alias"].fillna(False)]
    if chengdu_alias.empty:
        chengdu_alias = chengdu.tail(1)
    case = chengdu_alias.iloc[0] if not chengdu_alias.empty else {}

    lines = [
        "# Weather 研究：日内 forecast 曲线形态与 peak-clock alias v1",
        "",
        "## 数据快照",
        "",
        "| 项目 | 值 |",
        "|---|---|",
        f"| historical source | `{data['historical_source']}` |",
        f"| historical coverage | {data['historical_start']}..{data['historical_end']}；{data['historical_rows']} state rows / {data['historical_city_days']} city-days / {data['historical_dates']} target dates |",
        f"| forward raw source | `{data['forward_source']}` |",
        f"| forward coverage | {data['forward_start']}..{data['forward_end']}；{data['forward_rows']} checkpoints / {data['forward_dates']} target dates |",
        f"| DB snapshot mtime UTC | {data['db_mtime_utc']} |",
        "| sync / rebuild | 未执行；历史 parent 已覆盖目标窗，7/27 案例直接读 Mac raw，settlement 只读 canonical DB |",
        "| unsettled / missing bracket | 历史 parent 0 / 0；forward 按 settled 子集单列，不把未结算当策略筛除 |",
        "",
        "## 结论与动作",
        "",
        "**动作：把 `peak-clock alias / future local heat lobe` 作为共享连续风险特征和 zero-notional collector；不改 live，不把“双峰”直接做成交易 gate。**",
        "",
        "成都 7/27 证明这个状态可在事前识别：16:44 当地时间，forecast vintage 把 global argmax 切到 00:00，旧模型得到 `p_hold=96.28%`；但下一小时 17:00 的局部热峰仍到达 current 29 档的 upward-exit boundary。盘口给出 29 NO `27c`、30 YES `31c` 的互补 ask proxy，最终 canonical settlement 为 30。",
        "",
        f"历史 31 日同分母上，新增 boundary-relative morphology 相对 frozen core 的 Brier Δ `{number(brier_delta, 6)}`（95% CI `{brier_ci}`），logloss Δ `{number(logloss_delta, 6)}`（95% CI `{logloss_ci}`）；负值才是改善。当前没有通过 proper-score baseline，因此它还不是独立 alpha。",
        "",
        f"完整 lineage 复核后，旧 `32/33` 必须全部撤回：7 个 city-day 是“当前小时被误算成未来”的时钟边界错误；其余 26 个中又有 {forward_lineage_quarantine['city_days']} 个来自 7/24–7/26 已知 PIT observation selection 事故。污染行真实 source age 中位 {number(forward_lineage_quarantine['source_age_median_min'], 1)} 分钟（范围 {number(forward_lineage_quarantine['source_age_min_min'], 1)}–{number(forward_lineage_quarantine['source_age_max_min'], 1)}），却沿用了 fetch 时的 26–59 分钟 cached age，导致旧 running max 选错 current bracket。最终 clean settled evidence 只有成都 7/27 这 1 个 city-day。",
        "",
        "该 observation 事故已在 7/26 的生产事故修复 `8d61f685` 中定位：旧 index 按输入顺序 last-write-wins，使上一 UTC 日的 capture 覆盖同一 local target_date 的新 capture；修复后改为按 availability/report clock 选择并重算 decision-time age。本研究的问题是错误复用了事故窗口 raw，而不是盘口 archive 丢失。",
        "",
        "结论等级：`inconclusive_feature_value / zero_notional_collector_candidate`；significance=`FAIL`，baseline=`FAIL`，forward=`NA`（规则由 7/27 案例提出，7/29 起才是真 frozen forward）。",
        "",
        "## Target",
        "",
        "```text",
        "估计 P(current exact bracket upward-exit | PIT forecast curve morphology,",
        "      boundary-relative future local heat lobes, observed path, source basis)，",
        "并检验它相对同一时点 market/core probability 的 residual。",
        "```",
        "",
        "- physical target：未来任一局部热峰是否越过 current exact bracket 的 settlement-native 上沿；不是“forecast 有没有双峰”。",
        "- grain：历史为 carry checkpoint state，评分按 city-day 等权；forward signal 为 first `(city,target_date)` alias checkpoint。",
        "- decision timestamp：历史 `decision_snapshot_ts_utc`；forward raw checkpoint 的 immutable hourly curve。",
        "- label：current exact hold/leave；exact bracket 语义，30 被触及后 29 YES 输、29 NO 赢。",
        "- expressions：risk overlay 主对象是 current YES；独立交易诊断为 current NO / d1 YES，必须用 direct complementary quote、depth 和 Weather fee。",
        "- primary metric：Brier/logloss vs same-row frozen core 与 market；交易 ROI 只作 coverage 足够后的第二层。",
        "",
        "## 形态定义",
        "",
        "形态名只用于解释，模型吃连续量：global peak clock 的 sin/cos、afternoon-lobe gap、inter-lobe valley depth、prominent peak count、near-max width、peak separation，再加 future exit margin / hours / heat area。",
        "",
        "| 名称 | 解释 |",
        "|---|---|",
        "| `canonical_afternoon_single` | 12–17 点单峰 |",
        "| `broad_plateau` | 至少 4 个小时位于全日峰值 1°F 内 |",
        "| `overnight_peak_afternoon_lobe` | 0–6 点 global peak，下午 lobe 距峰≤2°F，且中间 valley≥3°F |",
        "| `morning_peak_afternoon_reheat` | 7–11 点主峰后，下午再次接近主峰 |",
        "| `late_peak_or_advection` | 18 点以后主峰，常对应平流/晚清 |",
        "| `multi_peak_other` | 两个相隔≥4h 的近峰 |",
        "| `peak-clock alias` | global peak 已过>2h，但未来 lobe 仍达 current upward-exit boundary |",
        "",
        "`未来`严格从 `ceil(decision_hour_local)` 开始；同时 observation 必须满足 `decision_snapshot - source_report <= expected cadence + 10m`。旧版不仅从 `floor(...)` 开始，还复用了 7/24–7/26 被上一 UTC 日旧观测覆盖的 runtime raw，因此原 33 个 settled 分母中只有成都 7/27 是 clean。",
        "",
        "## 成都 2026-07-27 PIT 时间线",
        "",
        table(
            chengdu_display,
            [
                "local_time",
                "checkpoint_key",
                "curve_shape",
                "global_peak_hour",
                "future_curve_max_native",
                "current_exit_threshold_native",
                "peak_clock_alias",
                "current_bracket",
                "current_no_ask_proxy",
                "d1_bracket",
                "d1_yes_ask_proxy",
                "model_probability_hold",
                "current_final_yes",
                "d1_final_yes",
            ],
        ),
        "",
        f"首个 alias checkpoint：shape=`{case.get('curve_shape')}`，global peak={case.get('global_peak_hour')}h，future max={number(case.get('future_curve_max_native'), 2)}，29 档 exit threshold={number(case.get('current_exit_threshold_native'), 2)}；current NO / 30 YES 的互补 ask proxy 约 `{number(case.get('current_no_ask_proxy'), 2)}` / `{number(case.get('d1_yes_ask_proxy'), 2)}`。这是事前可见 residual；后到的观测和 settlement 只作 label。",
        "",
        "异常形态其实在 13:42 已出现：当时 00:00 与 17:00 是两个相隔 17h 的近峰，分类为 `multi_peak_other`；16:44 forecast vintage 把 global argmax 从 17:00 切到 00:00，但未来 17:00 lobe 仍越过 29 档上沿。真正的危险是旧模型概率从 15:42 的 22.9% 反跳到 16:44 的 96.3%，不是形态突然消失。",
        "",
        "## Wide-denominator sanity",
        "",
        table(
            shape_display,
            [
                "curve_shape",
                "city_days",
                "dates",
                "overshoots",
                "overshoot_rate",
                "mean_core_risk",
                "mean_market_risk",
                "delta_vs_canonical",
                "ci95",
            ],
        ),
        "",
        "命名形态没有一个可凭历史点估直接成为 gate。尤其 D-1 historical alias 只有 "
        f"{historical_alias['city_days']} city-days / {historical_alias['dates']} dates，upward exit {historical_alias['overshoots']}；clean forward settled 只有 {forward_alias['settled_city_days']} 个，不能再报告 forward 命中率或与历史作强比较。",
        "",
        "旧版 negative control Karachi 7/27 实际是时钟边界错误：15:31 决策时被计入的是已经过去的 15:00 forecast 点 `34.5°C`；严格从 16:00 开始后 future max 只有 `33.33°C`，不再是 alias。它被从信号分母移除，不再算策略亏损。",
        "",
        "Frozen selector 的形态分布：",
        "",
        table(
            selected_display,
            [
                "curve_shape",
                "city_days",
                "dates",
                "overshoots",
                "overshoot_rate",
                "mean_core_risk",
                "mean_market_risk",
            ],
        ),
        "",
        "## Model / residual",
        "",
        table(
            score_table,
            [
                "model",
                "city_days",
                "dates",
                "Brier",
                "logloss",
                "AUC",
                "ΔBrier vs core",
                "Δlogloss vs core",
                "Brier CI",
            ],
        ),
        "",
        "`morphology` 只加日形连续量；`boundary_morphology` 再加 relative-to-exit future heat budget。两者都是 expanding OOF，训练只用更早 target dates；但 feature family 是看过 7/27 案例后定义的，因此这轮 OOF 只能作历史 sanity，不冒充真正 frozen forward。",
        "",
        "## Expression / execution",
        "",
        "历史 first-checkpoint d1 YES price-only diagnostic（`YES ask proxy = 1 - d1 NO bid`，官方 taker fee；bid depth 缺失，所以不称 executable）：",
        "",
        table(
            expr_display,
            [
                "curve_shape",
                "quoted_city_days",
                "dates",
                "wins",
                "mean_yes_ask_proxy",
                "fee_adjusted_roi",
                "depth_status",
            ],
        ),
        "",
        "Forward alias first-city-day expression：",
        "",
        table(
            forward_expr_display,
            [
                "expression",
                "quoted_settled_city_days",
                "dates",
                "wins",
                "mean_ask_proxy",
                "fee_adjusted_roi",
                "depth_status",
            ],
        ),
        "",
        "成都的单笔价格很漂亮，但 settled evidence funnel 中 current NO 与 d1 YES 都只有 1 个 clean quoted city-day，且没有保存足以声明 executable fill 的完整 side depth。7/24–7/26 那 25 个 `0.001` 不是正常策略样本：盘口价格本身正确，错的是 stale observation 导致研究选择了已经失败的旧 bracket。因此独立策略仍只有一个 clean case。",
        "",
        "### Forward 日期分布",
        "",
        table(
            forward_alias_by_date,
            [
                "target_date",
                "signal_city_days",
                "settled_city_days",
                "upward_exits",
                "current_yes_ask_001_city_days",
                "current_no_quote_city_days",
                "d1_yes_quote_city_days",
            ],
        ),
        "",
        "### Forward 城市分布",
        "",
        table(
            forward_alias_by_city,
            [
                "city",
                "signal_city_days",
                "dates",
                "settled_city_days",
                "upward_exits",
                "current_no_quote_city_days",
                "d1_yes_quote_city_days",
            ],
        ),
        "",
        "## Signal funnel",
        "",
        "| 层 | grain | rows | dates |",
        "|---|---|---:|---:|",
        f"| historical raw carry parent | checkpoint state | {data['historical_rows']} | {data['historical_dates']} |",
        f"| historical first city-day shape | city-day | {data['historical_city_days']} | {data['historical_dates']} |",
        f"| historical peak-clock alias | city-day | {historical_alias['city_days']} | {historical_alias['dates']} |",
        f"| forward raw checkpoints | checkpoint | {data['forward_rows']} | {data['forward_dates']} |",
        f"| quarantined stale-observation alias | city-day | {forward_lineage_quarantine['city_days']} | {forward_lineage_quarantine['dates']} |",
        f"| forward first alias | city-day | {forward_alias['city_days']} | {forward_alias['dates']} |",
        "",
        "## Evidence funnel",
        "",
        table(
            forward_funnel,
            [
                "stage",
                "rows",
                "city_days",
                "dates",
                "settled_rows",
                "settled_city_days",
                "current_exact_losses",
                "current_no_quote_rows",
                "d1_yes_quote_rows",
            ],
        ),
        "",
        "- PIT curve：historical 用固定 previous-run Single Runs cache；forward 用 raw checkpoint hourly curve。两者不能混成一个 vintage。",
        "- source first-seen / settlement basis：forward 已按 decision-source report age 重算；7/24–7/26 stale observation rows 已 quarantine，不再进入 signal/evidence 分母。",
        "- book：forward alias 大多缺 direct complementary quote/depth，coverage gap 不能当策略筛选。",
        "- settlement：canonical DB 已确认成都 final bracket 30；未结算 7/28 rows 不进入命中率。",
        "- fill：0；本轮不声称真实 fill 或 realized PnL。",
        "",
        "## Frozen forward",
        "",
        f"- prereg SHA256：`{payload['preregistration']['sha256']}`。",
        "- start：target_date 2026-07-29；沿用现有 core-carry full-denominator raw checkpoints，不新开生产进程。",
        "- primary：continuous boundary morphology vs same-row market/core Brier+logloss；15 个独立 target dates 后复核。",
        "- expression：current NO / d1 YES 分开记；fresh direct quote + bid depth coverage≥90%，官方 fee，不能用 future touch 冒充 fill。",
        "- promotion：proper-score delta 的 target-date block CI <0；交易层另需 fee-adjusted ROI 与 market excess CI >0。否则保持 feature/collector。",
        "- multiple testing：7 个 named shapes 只作 diagnostic；primary 只有 continuous boundary-relative head。",
        "",
        "## 8 环与血缘放置",
        "",
        "- 描述性 / 判别 / 概率 / 基准：已覆盖。",
        "- 统计：target-date block bootstrap 已覆盖，但真正 frozen forward 尚未发生。",
        "- 执行：PARTIAL，仅 complementary price proxy；fresh depth/fill 缺失。",
        "- 容量 / 真实 fill：未覆盖。",
        "- 共享数据逻辑：最终应进入 `weather_feature_layer` 的 curve morphology / future-local-peak feature，不进某个 selector 私有脚本。",
        "- canonical candidate：未来按 first checkpoint 写 `state_checkpoint_id + feature_row_id + market_evidence_status`，不另建平行事实表。",
        "- live：本轮不改 selector、sizing、runner 或 deployment。",
        "",
        "## 复现",
        "",
        "```bash",
        ".venv/bin/python scripts/analysis/reheat_risk/research_intraday_forecast_curve_morphology_v1.py",
        "```",
        "",
        f"- report: `{REPORT.relative_to(ROOT)}`",
        f"- result: `{RESULT_JSON.relative_to(ROOT)}`",
        f"- generated: `{OUT_DIR.relative_to(ROOT)}/`",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    payload = build_payload()
    write_report(payload)
    print(
        json.dumps(
            json_ready({
                "research_id": RESEARCH_ID,
                "report": str(REPORT),
                "result_json": str(RESULT_JSON),
                "historical_rows": payload["data"]["historical_rows"],
                "forward_alias": payload["forward_alias"],
                "preregistration_sha256": payload["preregistration"]["sha256"],
            }),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
