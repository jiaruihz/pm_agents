#!/usr/bin/env python3
"""One-shot frozen-forward weather and taker replay for Amsterdam KNMI models."""

from __future__ import annotations

import argparse
import csv
import glob
import gzip
import hashlib
import io
import json
import math
import pickle
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_modeling.knmi_10m_path import add_knmi_10m_path_features
from weather_modeling.solar_geometry import add_solar_geometry_features
from weather_modeling.forecast_path import (
    FORECAST_PATH_FEATURES,
    add_fixed_lead_forecast_path_features,
)
from weather_modeling.amsterdam_market_offset import (
    add_amsterdam_market_offset_features,
)
from weather_model_evaluation.market_offset_probability import (
    date_block_score_delta,
    date_equal_binary_score,
    fit_fixed_market_offset,
    logit as market_logit,
    predict_fixed_market_offset,
    select_market_offset_model,
)


UTC = timezone.utc
LOCAL = ZoneInfo("Europe/Amsterdam")
FEE_RATE = 0.05
DEFAULT_RUNTIME = Path("/Volumes/jrs/weather_data_feed_service_runtime")
DEFAULT_DB = Path("runtime/weather.db")
DEFAULT_OUTPUT = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/research/model_runs/"
    "amsterdam_knmi_remaining_heat_v8/frozen_2026_08"
)

# This family is written before the frozen rows are read.  The primary policy
# is the first row; the rest are sensitivity, never a post-hoc replacement.
POLICIES = [
    {"id": "primary_preofficial10_edge05_p55", "minutes": {10, 40}, "edge": 0.05, "p_min": 0.55},
    {"id": "preofficial10_edge02_p55", "minutes": {10, 40}, "edge": 0.02, "p_min": 0.55},
    {"id": "preofficial10_edge08_p65", "minutes": {10, 40}, "edge": 0.08, "p_min": 0.65},
    {"id": "nearofficial_edge05_p55", "minutes": {20, 50}, "edge": 0.05, "p_min": 0.55},
    {"id": "all10m_edge05_p55", "minutes": set(range(0, 60, 10)), "edge": 0.05, "p_min": 0.55},
]

# These mechanisms were fixed by the prior KNMI threshold and market-prior
# studies.  They are evaluated as a family; the August window is not used to
# invent another temperature/price cut.
CROSSNO_POLICIES = [
    {"id": "cross05_rule", "margin": 0.5, "selector": "rule"},
    {"id": "cross07_live_rule", "margin": 0.7, "selector": "rule"},
    {"id": "cross05_v9_edge02", "margin": 0.5, "selector": "v9_edge02"},
    {"id": "cross07_v9_edge02", "margin": 0.7, "selector": "v9_edge02"},
    {"id": "cross05_market_prior_edge02", "margin": 0.5, "selector": "market_prior_edge02"},
    {"id": "cross07_market_prior_edge02", "margin": 0.7, "selector": "market_prior_edge02"},
    {"id": "cross05_survival_edge02", "margin": 0.5, "selector": "survival_edge02"},
    {"id": "cross07_survival_edge02", "margin": 0.7, "selector": "survival_edge02"},
    {"id": "cross05_survival_edge01", "margin": 0.5, "selector": "survival_edge01"},
    {"id": "cross07_survival_edge01", "margin": 0.7, "selector": "survival_edge01"},
    {"id": "cross05_survival_veto_p90", "margin": 0.5, "selector": "survival_veto_p90"},
    {"id": "cross07_survival_veto_p90", "margin": 0.7, "selector": "survival_veto_p90"},
]
MARKET_PRIOR_INTERCEPT = -0.0170
MARKET_PRIOR_WEATHER_WEIGHT = 0.0641
DEFAULT_HISTORICAL_DATASET = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/research/"
    "amsterdam_knmi_10m_remaining_heat_v1/weather_checkpoints.csv.gz"
)
DEFAULT_MARKET_REFERENCE_GIT_SPEC = (
    "2009d308:docs/analysis/2026-07/generated/"
    "amsterdam_polymarket_price_reference_v1/"
    "prediction_market_reference_table.csv.gz"
)

MARKET_OFFSET_FEATURE_SETS = {
    "weather_disagreement": ["weather_market_logit_disagreement"],
    "weather_path_mechanism": [
        "weather_market_logit_disagreement",
        "ta_margin_current_c", "tx_margin_current_c",
        "ta_delta_10m_c", "ta_delta_30m_c", "ta_delta_60m_c",
        "ta_trend_accel_20m_c", "plateau_duration_minutes",
        "knmi_drawdown_from_high_c", "rebound_from_60m_low_c",
        "solar_w_m2", "remaining_clear_sky_integral_h",
        "cloud_mean_60m_okta", "rain_minutes_60m",
        "forecast_remaining_max_minus_d1_c", "forecast_minutes_to_peak",
        "forecast_peak_passed", "forecast_future_warming_integral_6h_c_h",
        "time_sin", "time_cos",
    ],
    "weather_path_market_interaction": [
        "weather_market_logit_disagreement",
        "ta_margin_current_c", "tx_margin_current_c",
        "ta_delta_10m_c", "ta_delta_30m_c", "ta_delta_60m_c",
        "ta_trend_accel_20m_c", "plateau_duration_minutes",
        "knmi_drawdown_from_high_c", "rebound_from_60m_low_c",
        "solar_w_m2", "remaining_clear_sky_integral_h",
        "cloud_mean_60m_okta", "rain_minutes_60m",
        "forecast_remaining_max_minus_d1_c", "forecast_minutes_to_peak",
        "forecast_peak_passed", "forecast_future_warming_integral_6h_c_h",
        "time_sin", "time_cos", "market_uncertainty",
        "weather_disagreement_x_uncertainty",
        "forecast_margin_x_remaining_solar", "trend_x_remaining_solar",
        "peak_passed_x_drawdown",
    ],
}
MARKET_OFFSET_L2_GRID = (0.003, 0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0)
MARKET_POSTERIOR_POLICIES = [
    {"id": "market_posterior_preofficial10_edge02_p55", "minutes": {10, 40}, "edge": 0.02, "p_min": 0.55},
    {"id": "market_posterior_preofficial10_edge01_p55", "minutes": {10, 40}, "edge": 0.01, "p_min": 0.55},
    {"id": "market_posterior_preofficial10_edge05_p55", "minutes": {10, 40}, "edge": 0.05, "p_min": 0.55},
]


def parse(value: Any) -> datetime:
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def half_up(value: float) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def read_jsonl(path: Path):
    if not path.is_file():
        return
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield line_number, row


def source_rows(root: Path, start: str, end: str) -> list[dict[str, Any]]:
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted((root / "output/knmi_open_data").glob("20??-??-??/knmi_observations.jsonl")):
        for line, row in read_jsonl(path):
            target_date = str(row.get("target_date") or "")
            if not start <= target_date <= end or row.get("city") != "Amsterdam":
                continue
            if row.get("event_role") != "new_content" or row.get("information_event_status") != "material":
                continue
            observed = str(row.get("observation_time_utc") or "")
            if not observed or parse(observed).astimezone(LOCAL).date().isoformat() != target_date:
                continue
            key = (target_date, observed)
            previous = selected.get(key)
            seen = parse(row.get("source_first_seen_at_utc") or row.get("available_at_utc"))
            if previous is None or seen < parse(previous.get("source_first_seen_at_utc") or previous.get("available_at_utc")):
                selected[key] = {**row, "_source_path": str(path), "_source_line": line}
    return sorted(selected.values(), key=lambda row: parse(row["observation_time_utc"]))


def official_rows(root: Path, target_date: str) -> list[dict[str, Any]]:
    path = root / "output/observations" / target_date / "observations.jsonl"
    rows = []
    for line, row in read_jsonl(path):
        if row.get("city") == "Amsterdam" and row.get("target_date") == target_date:
            rows.append({
                **row,
                "_official_path": str(path),
                "_official_line": line,
                "_fetched": parse(row["fetched_at_utc"]) if row.get("fetched_at_utc") else None,
                "_last_obs": parse(row["last_obs_utc"]) if row.get("last_obs_utc") else None,
            })
    return rows


def official_asof(rows: list[dict[str, Any]], decision: datetime, observed: datetime) -> dict[str, Any] | None:
    cutoff = observed - pd.Timedelta(minutes=10)
    candidates = [
        row for row in rows
        if row.get("_fetched") is not None
        and row["_fetched"] <= decision
        and row.get("_last_obs") is not None
        and row["_last_obs"] <= cutoff
    ]
    return max(candidates, key=lambda row: row["_fetched"]) if candidates else None


def feature_frame(root: Path, sources: list[dict[str, Any]]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    official_cache: dict[str, list[dict[str, Any]]] = {}
    for source in sources:
        fields = source.get("knmi_station_fields") or {}
        records.append({
            "target_date": source["target_date"],
            "observed_at_utc": source["observation_time_utc"],
            "ta_c": fields.get("ta"), "tx_c": fields.get("tx"),
            "solar_w_m2": fields.get("qg"), "cloud_okta": fields.get("n"),
            "precip_mm_h": fields.get("rg"), "humidity_pct": fields.get("rh"),
            "dewpoint_c": fields.get("td"), "wind_speed_mps": fields.get("ff"),
            "wind_gust_mps": fields.get("gff"), "wind_direction_deg": fields.get("dd"),
            "pressure_hpa": source.get("pressure_msl_hpa", fields.get("pp")),
            "source_event_id": source["information_event_id"],
            "source_first_seen_at_utc": source.get("source_first_seen_at_utc") or source.get("available_at_utc"),
            "source_path": source["_source_path"], "source_line": source["_source_line"],
        })
    frame = pd.DataFrame(records).sort_values(["target_date", "observed_at_utc"]).reset_index(drop=True)
    frame["running_max_c"] = frame.groupby("target_date")["ta_c"].cummax()
    frame["knmi_ta_running_max_c"] = frame["running_max_c"]
    frame["knmi_tx_running_max_c"] = frame.groupby("target_date")["tx_c"].cummax()
    frame = add_knmi_10m_path_features(frame)
    frame = add_solar_geometry_features(frame)
    enriched = []
    for row in frame.to_dict("records"):
        target_date = str(row["target_date"])
        official_cache.setdefault(target_date, official_rows(root, target_date))
        decision = parse(row["source_first_seen_at_utc"])
        observed = parse(row["observed_at_utc"])
        official = official_asof(official_cache[target_date], decision, observed)
        if official is None:
            continue
        current = half_up(float(official["running_max_c"]))
        minute = observed.astimezone(LOCAL).hour * 60 + observed.astimezone(LOCAL).minute
        row.update({
            "time_sin": math.sin(2 * math.pi * minute / 1440),
            "time_cos": math.cos(2 * math.pi * minute / 1440),
            "decision_minute_local": minute,
            "source_minute": observed.minute,
            "local_hour": observed.astimezone(LOCAL).hour,
            "official_running_max_c": float(official["running_max_c"]),
            "current_bracket_c": float(current), "d1_bracket_c": float(current + 1),
            "latest_official_temp_c": float(official["current_temp_c"]),
            "official_report_age_minutes": (decision - parse(official["last_obs_utc"])).total_seconds() / 60,
            "distance_to_d1_c": current + 0.5 - float(row["ta_c"]),
            "tx_distance_to_d1_c": current + 0.5 - float(row["tx_c"]),
            "decline_from_running_max_c": float(official["running_max_c"]) - float(row["ta_c"]),
            "minutes_since_running_max": float(official.get("minutes_since_running_max") or 0),
            "tx_minus_ta_c": float(row["tx_c"]) - float(row["ta_c"]),
            "knmi_ta_minus_latest_official_c": float(row["ta_c"]) - float(official["current_temp_c"]),
            "knmi_tx_minus_latest_official_c": float(row["tx_c"]) - float(official["current_temp_c"]),
            "knmi_ta_minus_official_running_max_c": float(row["ta_c"]) - float(official["running_max_c"]),
            "knmi_tx_minus_official_running_max_c": float(row["tx_c"]) - float(official["running_max_c"]),
            "source_above_official_d1": float(float(row["tx_c"]) >= current + 0.5),
            "official_path": official["_official_path"], "official_line": official["_official_line"],
        })
        # Persistence is evaluated on the first-seen source path, not later revisions.
        previous = [item for item in enriched if item["target_date"] == target_date]
        persistence = 1
        for item in reversed(previous):
            if float(item["tx_c"]) >= current + 0.5:
                persistence += 1
            else:
                break
        row["source_above_official_d1_persistence_rows"] = float(persistence if row["source_above_official_d1"] else 0)
        enriched.append(row)
    return pd.DataFrame(enriched)


def settlements(path: Path, start: str, end: str) -> dict[str, int]:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=1)
    rows = connection.execute(
        "SELECT target_date,bracket FROM settlement_outcomes "
        "WHERE city='Amsterdam' AND settlement_status='settled' AND final_price=1 "
        "AND target_date BETWEEN ? AND ?",
        (start, end),
    ).fetchall()
    connection.close()
    return {str(date): int(float(bracket)) for date, bracket in rows}


def book_map(root: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for path in glob.glob(str(root / "output/knmi_first_seen_ladder_v1/snapshots/20??-??-??/*.json")):
        try:
            payload = json.loads(Path(path).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("capture_status") != "complete" or int(payload.get("scheduled_offset_seconds") or 0) != 0:
            continue
        event_id = str(payload.get("source_event_id") or "")
        if event_id:
            result[event_id] = {**payload, "_snapshot_path": path}
    return result


def fee(shares: float, price: float) -> float:
    return round(shares * FEE_RATE * price * (1 - price), 5)


def exact_bracket(record: dict[str, Any], bracket: int) -> bool:
    try:
        return int(float(record.get("bracket"))) == bracket
    except (TypeError, ValueError):
        return False


def probability_metrics(rows: pd.DataFrame, column: str) -> dict[str, Any]:
    if rows.empty:
        return {"rows": 0}
    p = np.clip(rows[column].to_numpy(float), 1e-9, 1 - 1e-9)
    y = rows["label_leave"].to_numpy(float)
    daily = pd.DataFrame({"date": rows["target_date"], "brier": (p-y)**2,
                          "logloss": -(y*np.log(p)+(1-y)*np.log(1-p))}).groupby("date").mean()
    return {"rows": int(len(rows)), "target_dates": int(rows["target_date"].nunique()),
            "brier_date_equal": float(daily.brier.mean()),
            "logloss_date_equal": float(daily.logloss.mean()),
            "accuracy_0_5": float(((p >= .5) == y.astype(bool)).mean())}


def paired_probability_delta(
    rows: pd.DataFrame, candidate_column: str = "p_model"
) -> dict[str, Any]:
    if rows.empty:
        return {"rows": 0}
    p_model = np.clip(rows[candidate_column].to_numpy(float), 1e-9, 1 - 1e-9)
    p_market = np.clip(rows["market_p"].to_numpy(float), 1e-9, 1 - 1e-9)
    label = rows["label_leave"].to_numpy(float)
    scores = pd.DataFrame({
        "target_date": rows["target_date"].to_numpy(),
        "brier_delta": (p_model - label) ** 2 - (p_market - label) ** 2,
        "logloss_delta": (
            -(label * np.log(p_model) + (1 - label) * np.log(1 - p_model))
            + label * np.log(p_market)
            + (1 - label) * np.log(1 - p_market)
        ),
    }).groupby("target_date", as_index=False).mean()
    rng = np.random.default_rng(20260812)
    index = rng.integers(0, len(scores), size=(10000, len(scores)))
    brier_samples = scores["brier_delta"].to_numpy()[index].mean(axis=1)
    logloss_samples = scores["logloss_delta"].to_numpy()[index].mean(axis=1)
    return {
        "rows": int(len(rows)),
        "target_dates": int(len(scores)),
        "brier_delta_model_minus_market": float(scores["brier_delta"].mean()),
        "brier_delta_ci95": [float(value) for value in np.quantile(brier_samples, [.025, .975])],
        "logloss_delta_model_minus_market": float(scores["logloss_delta"].mean()),
        "logloss_delta_ci95": [float(value) for value in np.quantile(logloss_samples, [.025, .975])],
    }


def trade_date_bootstrap(
    records: list[dict[str, Any]], universe_dates: list[str]
) -> dict[str, Any]:
    daily = {
        target_date: {
            "cost": sum(float(row["cost"]) for row in records if row["target_date"] == target_date),
            "pnl": sum(float(row["pnl"]) for row in records if row["target_date"] == target_date),
        }
        for target_date in universe_dates
    }
    if not daily:
        return {"target_dates_in_universe": 0, "bootstrap_repetitions": 0}
    values = np.array([[row["cost"], row["pnl"]] for row in daily.values()], dtype=float)
    rng = np.random.default_rng(20260812)
    indices = rng.integers(0, len(values), size=(10000, len(values)))
    sampled = values[indices].sum(axis=1)
    nonzero = sampled[:, 0] > 0
    roi = sampled[nonzero, 1] / sampled[nonzero, 0]
    return {
        "target_dates_in_universe": len(universe_dates),
        "bootstrap_repetitions": 10000,
        "pnl_ci95": [float(value) for value in np.quantile(sampled[:, 1], [.025, .975])],
        "roi_ci95": (
            [float(value) for value in np.quantile(roi, [.025, .975])]
            if len(roi) else [None, None]
        ),
        "probability_roi_gt_zero": float((roi > 0).mean()) if len(roi) else None,
    }


def trade_stability(
    records: list[dict[str, Any]], universe_dates: list[str]
) -> dict[str, Any]:
    daily_rows = []
    for target_date in universe_dates:
        selected = [row for row in records if row["target_date"] == target_date]
        cost = sum(float(row["cost"]) for row in selected)
        pnl = sum(float(row["pnl"]) for row in selected)
        daily_rows.append({
            "target_date": target_date,
            "signals": len(selected),
            "wins": sum(bool(row["won"]) for row in selected),
            "cost": cost,
            "pnl": pnl,
            "roi": pnl / cost if cost else None,
        })
    ordered = sorted(records, key=lambda row: row["source_first_seen_at_utc"])
    cumulative = np.cumsum([float(row["pnl"]) for row in ordered])
    running_peak = np.maximum.accumulate(np.r_[0.0, cumulative])
    drawdown = np.r_[0.0, cumulative] - running_peak
    by_side = {}
    for side in ("YES", "NO"):
        selected = [row for row in records if row["selected_side"] == side]
        cost = sum(float(row["cost"]) for row in selected)
        pnl = sum(float(row["pnl"]) for row in selected)
        by_side[side] = {
            "signals": len(selected),
            "wins": sum(bool(row["won"]) for row in selected),
            "cost": cost,
            "pnl": pnl,
            "roi": pnl / cost if cost else None,
        }
    active = [row for row in daily_rows if row["signals"]]
    leave_one_date_out = []
    for held_out in sorted({row["target_date"] for row in records}):
        selected = [row for row in records if row["target_date"] != held_out]
        cost = sum(float(row["cost"]) for row in selected)
        pnl = sum(float(row["pnl"]) for row in selected)
        leave_one_date_out.append({
            "held_out": held_out,
            "signals": len(selected),
            "pnl": pnl,
            "roi": pnl / cost if cost else None,
        })
    return {
        "daily": daily_rows,
        "positive_active_dates": sum(row["pnl"] > 0 for row in active),
        "negative_active_dates": sum(row["pnl"] < 0 for row in active),
        "inactive_dates": sum(row["signals"] == 0 for row in daily_rows),
        "by_side": by_side,
        "max_sequential_drawdown_usd": float(drawdown.min()),
        "leave_one_active_date_out": leave_one_date_out,
    }


def same_selected_rows_market_favorite(
    model_records: list[dict[str, Any]], universe_dates: list[str]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    paired = []
    gaps = []
    for model_row in model_records:
        no_favorite = float(model_row["market_p"]) >= 0.5
        side = "NO" if no_favorite else "YES"
        ask = model_row["no_best_ask"] if no_favorite else model_row["yes_best_ask"]
        depth = model_row["no_ask_size"] if no_favorite else model_row["yes_ask_size"]
        if pd.isna(ask) or pd.isna(depth) or float(depth) < 5 or float(ask) > 0.97:
            gaps.append({
                "target_date": model_row["target_date"],
                "source_event_id": model_row["source_event_id"],
                "side": side,
                "reason": "market_favorite_not_5share_executable",
            })
            continue
        shares = 5.0
        price = float(ask)
        cost = shares * price + fee(shares, price)
        won = bool(model_row["label_leave"]) if side == "NO" else not bool(model_row["label_leave"])
        paired.append({
            **model_row,
            "selected_side": side,
            "selected_ask": price,
            "selected_ask_size": float(depth),
            "shares": shares,
            "cost": cost,
            "won": won,
            "pnl": (shares if won else 0.0) - cost,
            "model_selected_side": model_row["selected_side"],
            "model_pnl": float(model_row["pnl"]),
        })
    cost = sum(float(row["cost"]) for row in paired)
    pnl = sum(float(row["pnl"]) for row in paired)
    model_pnl = sum(float(row["model_pnl"]) for row in paired)
    daily_delta = []
    for target_date in universe_dates:
        selected = [row for row in paired if row["target_date"] == target_date]
        daily_delta.append(sum(row["model_pnl"] - row["pnl"] for row in selected))
    rng = np.random.default_rng(20260812)
    values = np.asarray(daily_delta, dtype=float)
    index = rng.integers(0, len(values), size=(10000, len(values)))
    samples = values[index].sum(axis=1)
    return {
        "signals": len(paired),
        "target_dates": len({row["target_date"] for row in paired}),
        "wins": sum(bool(row["won"]) for row in paired),
        "accuracy": sum(bool(row["won"]) for row in paired) / len(paired) if paired else None,
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost else None,
        "model_pnl_same_rows": model_pnl,
        "model_minus_market_pnl": model_pnl - pnl,
        "model_minus_market_pnl_ci95": [
            float(value) for value in np.quantile(samples, [.025, .975])
        ],
        "execution_coverage_gaps": gaps,
        "stability": trade_stability(paired, universe_dates),
    }, paired


def logit(values: pd.Series) -> pd.Series:
    clipped = values.clip(1e-7, 1 - 1e-7)
    return np.log(clipped / (1 - clipped))


def expit(values: pd.Series) -> pd.Series:
    return 1 / (1 + np.exp(-values))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def predict_weather_artifact(
    artifact: dict[str, Any], frame: pd.DataFrame
) -> np.ndarray:
    missing = sorted(set(artifact["features"]) - set(frame.columns))
    if missing:
        raise ValueError(f"weather probability frame missing columns: {missing}")
    values = artifact["estimator"].predict_proba(
        frame[artifact["features"]].apply(pd.to_numeric, errors="coerce")
    )[:, 1]
    if artifact.get("calibrator") is not None:
        values = artifact["calibrator"].predict_proba(
            market_logit(values).reshape(-1, 1)
        )[:, 1]
    return np.asarray(values, dtype=float)


def load_market_reference(
    *, path: Path | None, git_spec: str | None
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if path is not None:
        payload = path.read_bytes()
        source = str(path.resolve())
    elif git_spec:
        payload = subprocess.check_output(["git", "show", git_spec], cwd=ROOT)
        source = f"git:{git_spec}"
    else:
        raise ValueError("market reference requires a path or git spec")
    raw = gzip.decompress(payload) if payload[:2] == b"\x1f\x8b" else payload
    return pd.read_csv(io.BytesIO(raw)), {
        "source": source,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def replay_historical_price_reference(
    rows: pd.DataFrame,
    probability: np.ndarray,
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Replay a policy on sampled PIT prices, explicitly not executable books."""

    working = rows.copy()
    working["p_market_posterior"] = probability
    observed = pd.to_datetime(working["observed_at_utc"], utc=True).dt.tz_convert(
        LOCAL
    )
    working["source_minute"] = observed.dt.minute
    working["local_hour"] = observed.dt.hour
    working["no_reference_price"] = pd.to_numeric(
        working["market_current_no_price_reference"], errors="coerce"
    )
    working["yes_reference_price"] = pd.to_numeric(
        working["market_current_yes_price_reference"], errors="coerce"
    )
    working["no_effective_cost"] = (
        working["no_reference_price"]
        + FEE_RATE
        * working["no_reference_price"]
        * (1.0 - working["no_reference_price"])
    )
    working["yes_effective_cost"] = (
        working["yes_reference_price"]
        + FEE_RATE
        * working["yes_reference_price"]
        * (1.0 - working["yes_reference_price"])
    )
    working["no_edge"] = working["p_market_posterior"] - working["no_effective_cost"]
    working["yes_edge"] = (
        1.0 - working["p_market_posterior"] - working["yes_effective_cost"]
    )
    working["selected_side"] = np.where(
        working["no_edge"] >= working["yes_edge"], "NO", "YES"
    )
    no_selected = working["selected_side"].eq("NO")
    working["selected_probability"] = np.where(
        no_selected,
        working["p_market_posterior"],
        1.0 - working["p_market_posterior"],
    )
    working["selected_edge"] = np.where(
        no_selected, working["no_edge"], working["yes_edge"]
    )
    working["selected_reference_price"] = np.where(
        no_selected,
        working["no_reference_price"],
        working["yes_reference_price"],
    )
    eligible = working[
        working["source_minute"].isin(policy["minutes"])
        & working["local_hour"].between(10, 16)
        & working["selected_reference_price"].notna()
        & working["selected_reference_price"].le(0.97)
        & working["selected_probability"].ge(policy["p_min"])
        & working["selected_edge"].ge(policy["edge"])
    ].copy()
    eligible = eligible.sort_values("observed_at_utc").drop_duplicates(
        ["target_date", "current_bracket_c"], keep="first"
    )
    eligible["won"] = np.where(
        eligible["selected_side"].eq("NO"),
        eligible["label_leave"].astype(bool),
        ~eligible["label_leave"].astype(bool),
    )
    eligible["shares"] = 5.0
    eligible["source_first_seen_at_utc"] = eligible["observed_at_utc"]
    eligible["cost"] = 5.0 * (
        eligible["selected_reference_price"]
        + FEE_RATE
        * eligible["selected_reference_price"]
        * (1.0 - eligible["selected_reference_price"])
    )
    eligible["pnl"] = np.where(eligible["won"], 5.0, 0.0) - eligible["cost"]
    def summarize(selected: pd.DataFrame) -> dict[str, Any]:
        selected_cost = float(selected["cost"].sum())
        selected_pnl = float(selected["pnl"].sum())
        return {
            "signals": int(len(selected)),
            "target_dates": int(selected["target_date"].nunique()),
            "wins": int(selected["won"].sum()),
            "accuracy": float(selected["won"].mean()) if len(selected) else None,
            "cost": selected_cost,
            "pnl": selected_pnl,
            "roi": selected_pnl / selected_cost if selected_cost else None,
        }

    cost = float(eligible["cost"].sum())
    pnl = float(eligible["pnl"].sum())
    by_side = {}
    for side in ("YES", "NO"):
        selected = eligible[eligible["selected_side"].eq(side)]
        side_cost = float(selected["cost"].sum())
        side_pnl = float(selected["pnl"].sum())
        by_side[side] = {
            "signals": int(len(selected)),
            "wins": int(selected["won"].sum()),
            "cost": side_cost,
            "pnl": side_pnl,
            "roi": side_pnl / side_cost if side_cost else None,
        }
    same_rows_market = eligible.copy()
    same_rows_market["selected_side"] = np.where(
        same_rows_market["market_p"].ge(0.5), "NO", "YES"
    )
    same_no = same_rows_market["selected_side"].eq("NO")
    same_rows_market["selected_reference_price"] = np.where(
        same_no,
        same_rows_market["no_reference_price"],
        same_rows_market["yes_reference_price"],
    )
    same_rows_market["won"] = np.where(
        same_no,
        same_rows_market["label_leave"].astype(bool),
        ~same_rows_market["label_leave"].astype(bool),
    )
    same_rows_market["cost"] = 5.0 * (
        same_rows_market["selected_reference_price"]
        + FEE_RATE
        * same_rows_market["selected_reference_price"]
        * (1.0 - same_rows_market["selected_reference_price"])
    )
    same_rows_market["pnl"] = (
        np.where(same_rows_market["won"], 5.0, 0.0)
        - same_rows_market["cost"]
    )
    same_rows_summary = summarize(same_rows_market)
    same_rows_summary["model_minus_market_pnl"] = (
        pnl - float(same_rows_market["pnl"].sum())
    )
    paired_daily = pd.DataFrame(
        {
            "target_date": eligible["target_date"].astype(str).to_numpy(),
            "delta": (
                eligible["pnl"].to_numpy(float)
                - same_rows_market["pnl"].to_numpy(float)
            ),
        }
    ).groupby("target_date", sort=True)["delta"].sum()
    if len(paired_daily):
        rng = np.random.default_rng(20260812)
        indexes = rng.integers(
            0, len(paired_daily), size=(10000, len(paired_daily))
        )
        sampled_delta = paired_daily.to_numpy(float)[indexes].sum(axis=1)
        same_rows_summary["model_minus_market_pnl_ci95"] = [
            float(value)
            for value in np.quantile(sampled_delta, [0.025, 0.975])
        ]
    else:
        same_rows_summary["model_minus_market_pnl_ci95"] = [None, None]

    all_market = working[
        working["source_minute"].isin(policy["minutes"])
        & working["local_hour"].between(10, 16)
    ].copy()
    all_market["selected_side"] = np.where(
        all_market["market_p"].ge(0.5), "NO", "YES"
    )
    all_no = all_market["selected_side"].eq("NO")
    all_market["selected_reference_price"] = np.where(
        all_no, all_market["no_reference_price"], all_market["yes_reference_price"]
    )
    all_market = all_market[
        all_market["selected_reference_price"].notna()
        & all_market["selected_reference_price"].le(0.97)
    ].sort_values("observed_at_utc").drop_duplicates(
        ["target_date", "current_bracket_c"], keep="first"
    )
    all_no = all_market["selected_side"].eq("NO")
    all_market["won"] = np.where(
        all_no,
        all_market["label_leave"].astype(bool),
        ~all_market["label_leave"].astype(bool),
    )
    all_market["cost"] = 5.0 * (
        all_market["selected_reference_price"]
        + FEE_RATE
        * all_market["selected_reference_price"]
        * (1.0 - all_market["selected_reference_price"])
    )
    all_market["pnl"] = np.where(all_market["won"], 5.0, 0.0) - all_market["cost"]

    universe_dates = sorted(rows["target_date"].astype(str).unique())
    records = eligible.to_dict("records")
    output = {
        **summarize(eligible),
        "by_side": by_side,
        "mode": "sampled_price_reference_plus_fee_proxy_not_executable_book",
        "same_selected_rows_market_favorite": same_rows_summary,
        "all_market_favorite_primary_clock": summarize(all_market),
        "target_date_bootstrap": trade_date_bootstrap(records, universe_dates),
        "stability": trade_stability(records, universe_dates),
    }
    return output


def fit_amsterdam_market_offset(
    *,
    weather_artifact: dict[str, Any],
    weather_artifact_path: Path,
    historical_dataset: Path,
    forecast_path: Path,
    market_reference_path: Path | None,
    market_reference_git_spec: str | None,
    output_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    history = pd.read_csv(historical_dataset)
    history = history[
        history["target_date"].astype(str).between("2026-04-03", "2026-07-29")
    ].copy()
    history = add_knmi_10m_path_features(history)
    history = add_solar_geometry_features(history)
    forecast = pd.read_csv(forecast_path)
    history = add_fixed_lead_forecast_path_features(history, forecast)
    history["p_model"] = predict_weather_artifact(weather_artifact, history)
    reference, reference_identity = load_market_reference(
        path=market_reference_path, git_spec=market_reference_git_spec
    )
    joined = reference.merge(
        history,
        on="weather_checkpoint_key",
        how="left",
        suffixes=("", "_weather"),
        validate="one_to_one",
    )
    if joined["p_model"].isna().any():
        raise ValueError(
            f"market reference has {int(joined['p_model'].isna().sum())} unmatched weather rows"
        )
    joined["target_date"] = joined["target_date"].astype(str)
    # The target is the binary current-bracket contract, so the coefficient-one
    # market prior must be that contract's direct NO price.  The normalized
    # full-ladder q_market_0 has a different denominator when ladder sums are
    # not one and is retained only as a separate full-distribution reference.
    joined["market_p"] = pd.to_numeric(
        joined["market_current_no_price_reference"], errors="raise"
    )
    joined["label_leave"] = pd.to_numeric(
        joined["label_d1_cross_eod"], errors="raise"
    ).astype(int)
    joined = add_amsterdam_market_offset_features(joined)
    fitted, selection = select_market_offset_model(
        joined,
        feature_sets=MARKET_OFFSET_FEATURE_SETS,
        l2_grid=MARKET_OFFSET_L2_GRID,
        fit_window=("2026-04-03", "2026-05-31"),
        validation_window=("2026-06-01", "2026-06-30"),
        refit_window=("2026-04-03", "2026-06-30"),
        market_probability_column="market_p",
        label_column="label_leave",
    )
    development_rows = joined[
        joined["target_date"].between("2026-04-03", "2026-05-31")
    ].copy()
    development_model = fit_fixed_market_offset(
        development_rows,
        feature_columns=selection["selected"]["features"],
        market_probability_column="market_p",
        label_column="label_leave",
        date_column="target_date",
        l2_strength=float(selection["selected"]["l2_strength"]),
    )
    fitted.update(
        {
            "model_id": "amsterdam_knmi_market_offset_probability_v2",
            "city": "Amsterdam",
            "target": "P(final EHAM settlement leaves current exact bracket)",
            "market_feature_role": "prior_offset",
            "market_feature_clock": "decision_current",
            "base_weather_model_id": weather_artifact["model_id"],
            "base_weather_artifact": str(weather_artifact_path.resolve()),
            "base_weather_artifact_sha256": sha256(weather_artifact_path),
            "historical_dataset": str(historical_dataset.resolve()),
            "historical_dataset_sha256": sha256(historical_dataset),
            "forecast_path": str(forecast_path.resolve()),
            "forecast_path_sha256": sha256(forecast_path),
            "market_reference": reference_identity,
            "clean_forward_start_utc": datetime.now(UTC).isoformat(),
            "research_only_zero_notional": True,
            "live_eligible": False,
        }
    )
    scores: dict[str, Any] = {}
    for split, start, end in (
        ("fit", "2026-04-03", "2026-05-31"),
        ("validation", "2026-06-01", "2026-06-30"),
        ("reused_holdout_early", "2026-07-01", "2026-07-14"),
        ("reused_holdout_late", "2026-07-15", "2026-07-29"),
        ("reused_holdout_all", "2026-07-01", "2026-07-29"),
    ):
        subset = joined[joined["target_date"].between(start, end)].copy()
        scoring_model = (
            development_model
            if split in {"fit", "validation"}
            else fitted
        )
        posterior = predict_fixed_market_offset(scoring_model, subset)
        scores[split] = {
            "window": [start, end],
            "market": date_equal_binary_score(
                subset, subset["market_p"], label_column="label_leave"
            ),
            "weather": date_equal_binary_score(
                subset, subset["p_model"], label_column="label_leave"
            ),
            "posterior": date_equal_binary_score(
                subset, posterior, label_column="label_leave"
            ),
            "posterior_minus_market": date_block_score_delta(
                subset,
                posterior,
                subset["market_p"],
                label_column="label_leave",
            ),
            "price_reference_policy": replay_historical_price_reference(
                subset,
                posterior,
                MARKET_POSTERIOR_POLICIES[0],
            ),
            "scoring_model_fit_through": (
                "2026-05-31"
                if split in {"fit", "validation"}
                else "2026-06-30"
            ),
        }
    expanding_parts = []
    for start, end, scoring_model in (
        ("2026-06-01", "2026-06-30", development_model),
        ("2026-07-01", "2026-07-29", fitted),
    ):
        part = joined[joined["target_date"].between(start, end)].copy()
        part["p_expanding_oof"] = predict_fixed_market_offset(
            scoring_model, part
        )
        expanding_parts.append(part)
    expanding_oof = pd.concat(expanding_parts, ignore_index=True)
    scores["expanding_oof_20260601_20260729"] = {
        "window": ["2026-06-01", "2026-07-29"],
        "rows": int(len(expanding_oof)),
        "target_dates": int(expanding_oof["target_date"].nunique()),
        "market": date_equal_binary_score(
            expanding_oof,
            expanding_oof["market_p"],
            label_column="label_leave",
        ),
        "posterior": date_equal_binary_score(
            expanding_oof,
            expanding_oof["p_expanding_oof"],
            label_column="label_leave",
        ),
        "posterior_minus_market": date_block_score_delta(
            expanding_oof,
            expanding_oof["p_expanding_oof"],
            expanding_oof["market_p"],
            label_column="label_leave",
        ),
        "price_reference_policy": replay_historical_price_reference(
            expanding_oof,
            expanding_oof["p_expanding_oof"].to_numpy(float),
            MARKET_POSTERIOR_POLICIES[0],
        ),
        "clock": (
            "June scored by model fit through May; July scored by refit through June"
        ),
    }
    summary = {
        "schema_version": "amsterdam_knmi_market_offset_training_v2",
        "model_id": fitted["model_id"],
        "denominator_scope": (
            "Amsterdam 10-minute archive-reconstructed weather checkpoints joined "
            "to timestamped Polymarket price references; sampled prices are not books"
        ),
        "raw_reference_rows": int(len(reference)),
        "joined_rows": int(len(joined)),
        "target_dates": int(joined["target_date"].nunique()),
        "target_date_range": [joined["target_date"].min(), joined["target_date"].max()],
        "selection": selection,
        "multiple_test_candidates": int(selection["selected"] and len(selection["candidates"])),
        "multiple_test_adjustment": "none_development_selection_only",
        "market_probability_contract": (
            "direct current-bracket NO price; q_market_0 full-ladder normalization "
            "is not used as the binary prior"
        ),
        "scores": scores,
        "artifact_path": str(output_path),
        "evidence_boundary": (
            "probability history is PIT price-reference research but non-executable; "
            "July is a reused historical holdout and cannot be called untouched forward"
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as handle:
        pickle.dump(fitted, handle)
    metrics_path = output_path.with_name("market_offset_metrics.json")
    metrics_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return fitted, summary


def apply_probability_expression(
    frame: pd.DataFrame, probability_column: str
) -> pd.DataFrame:
    output = frame.copy()
    output["expression_probability_leave"] = output[probability_column]
    output["no_edge_after_fee"] = (
        output["expression_probability_leave"]
        - output["effective_cost_per_share"]
    )
    output["yes_edge_after_fee"] = (
        1.0
        - output["expression_probability_leave"]
        - output["yes_effective_cost_per_share"]
    )
    output["selected_side"] = np.where(
        output["no_edge_after_fee"] >= output["yes_edge_after_fee"], "NO", "YES"
    )
    no_selected = output["selected_side"].eq("NO")
    output["selected_model_probability"] = np.where(
        no_selected,
        output["expression_probability_leave"],
        1.0 - output["expression_probability_leave"],
    )
    output["selected_market_probability"] = np.where(
        no_selected, output["market_p"], 1.0 - output["market_p"]
    )
    output["selected_edge_after_fee"] = np.where(
        no_selected, output["no_edge_after_fee"], output["yes_edge_after_fee"]
    )
    output["selected_ask"] = np.where(
        no_selected, output["no_best_ask"], output["yes_best_ask"]
    )
    output["selected_ask_size"] = np.where(
        no_selected, output["no_ask_size"], output["yes_ask_size"]
    )
    return output


def replay_crossno_policy(
    rows: pd.DataFrame,
    policy: dict[str, Any],
    universe_dates: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    working = rows.copy()
    working["ta_cross_margin_c"] = (
        working["ta_c"] - working["current_bracket_c"]
    )
    raw = working[working["ta_cross_margin_c"].ge(float(policy["margin"]) - 1e-9)].copy()
    first = raw.sort_values("source_first_seen_at_utc").drop_duplicates(
        ["target_date", "current_bracket_c"], keep="first"
    )
    book_covered = first[first["market_p"].notna()].copy()
    ask_available = book_covered[book_covered["no_best_ask"].notna()].copy()
    executable = ask_available[
        ask_available["no_best_ask"].le(.97)
        & ask_available["no_ask_size"].fillna(0).ge(5)
    ].copy()
    executable["market_prior_p"] = expit(
        logit(executable["market_p"])
        + MARKET_PRIOR_INTERCEPT
        + MARKET_PRIOR_WEATHER_WEIGHT
        * (logit(executable["p_model"]) - logit(executable["market_p"]))
    )
    selector = str(policy["selector"])
    if selector == "v9_edge02":
        selected = executable[
            executable["p_model"].ge(.55)
            & (executable["p_model"] - executable["effective_cost_per_share"]).ge(.02)
        ].copy()
    elif selector == "market_prior_edge02":
        selected = executable[
            executable["market_prior_p"].ge(.55)
            & (executable["market_prior_p"] - executable["effective_cost_per_share"]).ge(.02)
        ].copy()
    elif selector == "survival_edge02":
        selected = executable[
            executable["p_cross_survives"].ge(.55)
            & (
                executable["p_cross_survives"]
                - executable["effective_cost_per_share"]
            ).ge(.02)
        ].copy()
    elif selector == "survival_edge01":
        selected = executable[
            (
                executable["p_cross_survives"]
                - executable["effective_cost_per_share"]
            ).gt(.01)
        ].copy()
    elif selector == "survival_veto_p90":
        # The dedicated weather head is a terminal-false safety screen.  It is
        # deliberately not treated as a market fair-value estimate: the
        # frozen 2025 evaluation fixed this 0.90 cutoff for future forward use.
        selected = executable[executable["p_cross_survives"].ge(.90)].copy()
    else:
        selected = executable
    records = []
    for row in selected.to_dict("records"):
        shares = min(10.0, float(row["no_ask_size"]))
        price = float(row["no_best_ask"])
        cost = shares * price + fee(shares, price)
        won = bool(row["label_leave"])
        records.append({
            **row,
            "selected_side": "NO",
            "selected_ask": price,
            "selected_ask_size": float(row["no_ask_size"]),
            "shares": shares,
            "cost": cost,
            "won": won,
            "pnl": (shares if won else 0.0) - cost,
        })
    cost = sum(float(row["cost"]) for row in records)
    pnl = sum(float(row["pnl"]) for row in records)
    summary = {
        "signals": len(first),
        "signal_target_dates": int(first["target_date"].nunique()),
        "book_covered": len(book_covered),
        "ask_available": len(ask_available),
        "executable": len(executable),
        "selected": len(records),
        "selected_target_dates": len({row["target_date"] for row in records}),
        "wins": sum(bool(row["won"]) for row in records),
        "accuracy": (
            sum(bool(row["won"]) for row in records) / len(records)
            if records else None
        ),
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost else None,
        "actual_fills": 0,
        "mode": "counterfactual_taker_at_captured_t0_ask_hold_to_settlement",
        "target_date_bootstrap": trade_date_bootstrap(records, universe_dates),
        "stability": trade_stability(records, universe_dates),
    }
    return summary, records


def replay_policy(rows: pd.DataFrame, policy: dict[str, Any], *, market_only: bool = False) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    working = rows.copy()
    if market_only:
        no_favorite = working["market_p"].ge(.5)
        working["selected_side"] = np.where(no_favorite, "NO", "YES")
        working["selected_market_probability"] = np.where(
            no_favorite, working["market_p"], 1-working["market_p"]
        )
        working["selected_ask"] = np.where(no_favorite, working["no_best_ask"], working["yes_best_ask"])
        working["selected_ask_size"] = np.where(no_favorite, working["no_ask_size"], working["yes_ask_size"])
    eligible = working[
        rows["source_minute"].isin(policy["minutes"])
        & rows["local_hour"].between(10, 16)
        & rows["selected_ask"].notna()
        & rows["selected_ask_size"].fillna(0).ge(5)
        & rows["selected_ask"].le(.97)
    ].copy()
    if market_only:
        eligible = eligible[eligible["selected_market_probability"].ge(.5)]
    else:
        eligible = eligible[
            eligible["selected_model_probability"].ge(policy["p_min"])
            & eligible["selected_edge_after_fee"].ge(policy["edge"])
        ]
    eligible = eligible.sort_values("source_first_seen_at_utc").drop_duplicates(
        ["target_date", "current_bracket_c"], keep="first"
    )
    records = []
    for row in eligible.to_dict("records"):
        shares = min(5.0, float(row["selected_ask_size"]))
        price = float(row["selected_ask"])
        cost = shares * price + fee(shares, price)
        won = bool(row["label_leave"]) if row["selected_side"] == "NO" else not bool(row["label_leave"])
        records.append({**row, "shares": shares, "cost": cost, "won": won,
                        "pnl": (shares if won else 0.0) - cost})
    cost = sum(row["cost"] for row in records)
    pnl = sum(row["pnl"] for row in records)
    summary = {"signals": len(records), "target_dates": len({row["target_date"] for row in records}),
               "wins": sum(row["won"] for row in records),
               "accuracy": sum(row["won"] for row in records)/len(records) if records else None,
               "cost": cost, "pnl": pnl, "roi": pnl/cost if cost else None,
               "mode": "counterfactual_taker_at_captured_t0_ask", "actual_fills": 0}
    return summary, records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--start", default="2026-08-01")
    parser.add_argument("--end", default="2026-08-31")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--forecast-path", type=Path, default=None)
    parser.add_argument("--cross-survival-artifact", type=Path, default=None)
    parser.add_argument("--historical-dataset", type=Path, default=DEFAULT_HISTORICAL_DATASET)
    parser.add_argument("--market-reference-path", type=Path, default=None)
    parser.add_argument(
        "--market-reference-git-spec",
        default=DEFAULT_MARKET_REFERENCE_GIT_SPEC,
    )
    parser.add_argument("--fit-market-prior-output", type=Path, default=None)
    parser.add_argument("--market-prior-artifact", type=Path, default=None)
    parser.add_argument(
        "--evaluation-role",
        choices=(
            "true_frozen_one_shot",
            "locked_frozen_reproduction",
            "post_freeze_development_audit",
        ),
        required=True,
    )
    args = parser.parse_args()
    with args.artifact.open("rb") as handle:
        artifact = pickle.load(handle)
    market_prior_training = None
    if args.fit_market_prior_output is not None:
        if args.forecast_path is None:
            raise ValueError("--fit-market-prior-output requires --forecast-path")
        _, market_prior_training = fit_amsterdam_market_offset(
            weather_artifact=artifact,
            weather_artifact_path=args.artifact,
            historical_dataset=args.historical_dataset,
            forecast_path=args.forecast_path,
            market_reference_path=args.market_reference_path,
            market_reference_git_spec=args.market_reference_git_spec,
            output_path=args.fit_market_prior_output,
        )
        args.market_prior_artifact = args.fit_market_prior_output
    sources = source_rows(args.runtime_root, args.start, args.end)
    frame = feature_frame(args.runtime_root, sources)
    if any(name in artifact["features"] for name in FORECAST_PATH_FEATURES):
        if args.forecast_path is None:
            raise ValueError("artifact requires --forecast-path")
        frame = add_fixed_lead_forecast_path_features(
            frame, pd.read_csv(args.forecast_path)
        )
    frame["ta_cross_margin_c"] = frame["ta_c"] - frame["current_bracket_c"]
    frame["tx_cross_margin_c"] = frame["tx_c"] - frame["current_bracket_c"]
    missing = sorted(set(artifact["features"]) - set(frame.columns))
    if missing:
        raise ValueError(f"frozen frame missing artifact columns: {missing}")
    frame["p_model"] = predict_weather_artifact(artifact, frame)
    if args.cross_survival_artifact is not None:
        with args.cross_survival_artifact.open("rb") as handle:
            cross_artifact = pickle.load(handle)
        cross_missing = sorted(set(cross_artifact["features"]) - set(frame.columns))
        if cross_missing:
            raise ValueError(
                f"cross-survival frame missing artifact columns: {cross_missing}"
            )
        cross_matrix = frame[cross_artifact["features"]].apply(
            pd.to_numeric, errors="coerce"
        )
        frame["p_cross_survives"] = cross_artifact["estimator"].predict_proba(
            cross_matrix
        )[:, 1]
    else:
        frame["p_cross_survives"] = np.nan
    outcomes = settlements(args.db, args.start, args.end)
    frame = frame[frame["target_date"].isin(outcomes)].copy()
    frame["settlement_bracket"] = frame["target_date"].map(outcomes)
    frame["label_leave"] = frame["settlement_bracket"].ne(frame["current_bracket_c"]).astype(int)
    books = book_map(args.runtime_root)
    quote_rows = []
    for row in frame.to_dict("records"):
        book = books.get(str(row["source_event_id"]))
        record = None if book is None else next(
            (item for item in book.get("records", []) if exact_bracket(item, int(row["current_bracket_c"]))), None
        )
        if record is None:
            row.update({"market_p": np.nan, "no_best_ask": np.nan, "no_ask_size": np.nan,
                        "yes_best_ask": np.nan, "yes_ask_size": np.nan, "snapshot_path": None})
        else:
            bid, ask = record.get("no_best_bid"), record.get("no_best_ask")
            row.update({"market_p": (float(bid)+float(ask))/2 if bid is not None and ask is not None else np.nan,
                        "no_best_ask": float(ask) if ask is not None else np.nan,
                        "no_ask_size": float(record.get("no_ask_size") or 0),
                        "yes_best_ask": float(record["yes_best_ask"]) if record.get("yes_best_ask") is not None else np.nan,
                        "yes_ask_size": float(record.get("yes_ask_size") or 0),
                        "snapshot_path": book["_snapshot_path"]})
        quote_rows.append(row)
    frame = pd.DataFrame(quote_rows)
    frame["effective_cost_per_share"] = frame["no_best_ask"] + FEE_RATE * frame["no_best_ask"] * (1-frame["no_best_ask"])
    frame["edge_after_fee"] = frame["p_model"] - frame["effective_cost_per_share"]
    frame["no_edge_after_fee"] = frame["edge_after_fee"]
    frame["yes_effective_cost_per_share"] = frame["yes_best_ask"] + FEE_RATE * frame["yes_best_ask"] * (1-frame["yes_best_ask"])
    frame["yes_edge_after_fee"] = (1-frame["p_model"]) - frame["yes_effective_cost_per_share"]
    frame["selected_side"] = np.where(frame["no_edge_after_fee"] >= frame["yes_edge_after_fee"], "NO", "YES")
    no_selected = frame["selected_side"].eq("NO")
    frame["selected_model_probability"] = np.where(no_selected, frame["p_model"], 1-frame["p_model"])
    frame["selected_market_probability"] = np.where(no_selected, frame["market_p"], 1-frame["market_p"])
    frame["selected_edge_after_fee"] = np.where(no_selected, frame["no_edge_after_fee"], frame["yes_edge_after_fee"])
    frame["selected_ask"] = np.where(no_selected, frame["no_best_ask"], frame["yes_best_ask"])
    frame["selected_ask_size"] = np.where(no_selected, frame["no_ask_size"], frame["yes_ask_size"])
    common = frame[frame["market_p"].notna()].copy()
    universe_dates = sorted(set(frame["target_date"]))
    strategy = {}
    records_by_policy = {}
    record_rows = []
    for policy in POLICIES:
        summary, records = replay_policy(common, policy)
        summary["target_date_bootstrap"] = trade_date_bootstrap(records, universe_dates)
        summary["stability"] = trade_stability(records, universe_dates)
        strategy[policy["id"]] = summary
        records_by_policy[policy["id"]] = records
        record_rows.extend({"policy_id": policy["id"], **row} for row in records)
    market_summary, market_records = replay_policy(common, POLICIES[0], market_only=True)
    market_summary["target_date_bootstrap"] = trade_date_bootstrap(
        market_records, universe_dates
    )
    locked_policy_id = "preofficial10_edge02_p55"
    same_set_market, same_set_market_records = same_selected_rows_market_favorite(
        records_by_policy[locked_policy_id], universe_dates
    )
    market_posterior_probability = None
    market_posterior_delta = None
    market_posterior_policies: dict[str, Any] = {}
    market_posterior_records: list[dict[str, Any]] = []
    market_posterior_same_rows_market = None
    market_posterior_same_rows_records: list[dict[str, Any]] = []
    market_prior_identity = None
    if args.market_prior_artifact is not None:
        with args.market_prior_artifact.open("rb") as handle:
            market_prior_artifact = pickle.load(handle)
        posterior_frame = add_amsterdam_market_offset_features(frame)
        posterior_frame["p_market_posterior"] = predict_fixed_market_offset(
            market_prior_artifact,
            posterior_frame,
            market_probability=posterior_frame["market_p"],
        )
        posterior_frame = apply_probability_expression(
            posterior_frame, "p_market_posterior"
        )
        posterior_common = posterior_frame[
            posterior_frame["market_p"].notna()
        ].copy()
        market_posterior_probability = probability_metrics(
            posterior_common, "p_market_posterior"
        )
        market_posterior_delta = paired_probability_delta(
            posterior_common, "p_market_posterior"
        )
        posterior_records_by_policy: dict[str, list[dict[str, Any]]] = {}
        for policy in MARKET_POSTERIOR_POLICIES:
            posterior_summary, posterior_records = replay_policy(
                posterior_common, policy
            )
            posterior_summary["target_date_bootstrap"] = trade_date_bootstrap(
                posterior_records, universe_dates
            )
            posterior_summary["stability"] = trade_stability(
                posterior_records, universe_dates
            )
            market_posterior_policies[policy["id"]] = posterior_summary
            posterior_records_by_policy[policy["id"]] = posterior_records
            market_posterior_records.extend(
                {"policy_id": policy["id"], **row} for row in posterior_records
            )
        posterior_primary_id = "market_posterior_preofficial10_edge02_p55"
        (
            market_posterior_same_rows_market,
            market_posterior_same_rows_records,
        ) = same_selected_rows_market_favorite(
            posterior_records_by_policy[posterior_primary_id], universe_dates
        )
        market_prior_identity = {
            "path": str(args.market_prior_artifact),
            "sha256": sha256(args.market_prior_artifact),
            "model_id": market_prior_artifact["model_id"],
            "feature_set_id": market_prior_artifact["feature_set_id"],
            "l2_strength": market_prior_artifact["l2_strength"],
            "clean_forward_start_utc": market_prior_artifact[
                "clean_forward_start_utc"
            ],
        }
    crossno = {}
    crossno_records = []
    for policy in CROSSNO_POLICIES:
        cross_summary, cross_records = replay_crossno_policy(
            frame, policy, universe_dates
        )
        crossno[policy["id"]] = cross_summary
        crossno_records.extend(
            {"policy_id": policy["id"], **row} for row in cross_records
        )
    summary = {
        "schema_version": "amsterdam_knmi_frozen_strategy_replay_v2",
        "artifact": str(args.artifact), "model_id": artifact["model_id"],
        "frozen_window": [args.start, args.end],
        "source_rows": len(sources), "feature_rows": int(len(frame)),
        "settled_dates": sorted(set(frame["target_date"])),
        "book_covered_rows": int(len(common)),
        "weather_all": probability_metrics(frame, "p_model"),
        "weather_market_common": probability_metrics(common, "p_model"),
        "market_common": probability_metrics(common, "market_p"),
        "weather_minus_market_common": paired_probability_delta(common),
        "policies": strategy, "market_favorite_primary_clock": market_summary,
        "locked_policy_id": locked_policy_id,
        "same_selected_rows_market_favorite": same_set_market,
        "market_posterior_probability_common": market_posterior_probability,
        "market_posterior_minus_market_common": market_posterior_delta,
        "market_posterior_policies": market_posterior_policies,
        "market_posterior_same_selected_rows_market_favorite": (
            market_posterior_same_rows_market
        ),
        "market_posterior_policy_contract": [
            {**row, "minutes": sorted(row["minutes"])}
            for row in MARKET_POSTERIOR_POLICIES
        ],
        "market_prior_artifact": market_prior_identity,
        "market_prior_training": market_prior_training,
        "crossno_policies": crossno,
        "crossno_policy_contract": CROSSNO_POLICIES,
        "market_prior_contract": {
            "intercept": MARKET_PRIOR_INTERCEPT,
            "weather_logit_weight": MARKET_PRIOR_WEATHER_WEIGHT,
            "source": "2026-04-03..06-30 train; 2026-07-01..29 historical holdout",
        },
        "cross_survival_artifact": (
            str(args.cross_survival_artifact)
            if args.cross_survival_artifact is not None else None
        ),
        "policy_contract": [{**row, "minutes": sorted(row["minutes"])} for row in POLICIES],
        "evaluation_role": args.evaluation_role,
        "frozen_read_once": args.evaluation_role == "true_frozen_one_shot",
        "boundary": "historical KNMI is non-first-seen; August is collector-exact. Trades are taker counterfactuals, not fills.",
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "metrics.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    frame.to_csv(args.output / "checkpoint_predictions.csv.gz", index=False, compression="gzip")
    pd.DataFrame(record_rows).to_csv(args.output / "policy_trades.csv", index=False)
    pd.DataFrame(market_records).to_csv(args.output / "market_favorite_trades.csv", index=False)
    pd.DataFrame(same_set_market_records).to_csv(
        args.output / "same_selected_rows_market_favorite_trades.csv", index=False
    )
    pd.DataFrame(crossno_records).to_csv(
        args.output / "crossno_policy_trades.csv", index=False
    )
    pd.DataFrame(market_posterior_records).to_csv(
        args.output / "market_posterior_policy_trades.csv", index=False
    )
    pd.DataFrame(market_posterior_same_rows_records).to_csv(
        args.output / "market_posterior_same_rows_market_favorite_trades.csv",
        index=False,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
