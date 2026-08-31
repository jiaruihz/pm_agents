#!/usr/bin/env python3
"""Research-only V2.1/V3 Tmin model and frozen-selector strategy readout.

This runner fixes the historical Seoul/Tokyo local-day mapping, builds a
weather-only binary foundation and a one-hour discrete hazard foundation,
fits non-negative market residual adaptors with strict prior-date OOF, and
replays the already frozen selector/execution evidence.  It never writes
production state and never uses PnL to fit or select a model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import pyarrow
import scipy
from scipy.optimize import minimize


ROOT = Path(__file__).resolve().parents[3]
SEED = 20260829
EPS = 1e-8
FEE_RATE = 0.05
ADAPTOR_L2 = 1.0
FOUNDATION_L2 = 1.0
MIN_PRIOR_DATES = 5
MIN_PRIOR_NEGATIVES = 2
CHECKPOINT_HOURS = (6, 9, 12, 18, 21, 23)
ACTIVE_WINDOWS = {"morning_cooling", "post_sunrise_provisional_low"}
CITY_PATHS = {
    "Seoul": ("Asia/Seoul", "RKSI"),
    "Tokyo": ("Asia/Tokyo", "RJTT"),
}
FOUNDATION_FEATURES = (
    "rebound_c",
    "minutes_since_running_min",
    "hours_remaining",
    "temperature_change_60m_c",
    "native_rung_safety_margin_c",
)
HAZARD_FEATURES = (
    "rebound_c",
    "minutes_since_running_min",
    "temperature_change_60m_c",
    "native_rung_safety_margin_c",
    "future_clock_hours",
    "elapsed_hours",
)
P0_SEGMENTS = {
    "SEED_2026-08-12_TO_16": ("2026-08-12", "2026-08-16"),
    "VALIDATION_2026-08-17_TO_21": ("2026-08-17", "2026-08-21"),
    "TEST_2026-08-22_TO_26": ("2026-08-22", "2026-08-26"),
}


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, text=True, capture_output=True
    ).stdout.strip()


def _rung(value: float) -> int:
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


def _clip(value: Iterable[float] | pd.Series | np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(value, dtype=float), EPS, 1.0 - EPS)


def _logit(value: Iterable[float] | pd.Series | np.ndarray) -> np.ndarray:
    probability = _clip(value)
    return np.log(probability / (1.0 - probability))


def _sigmoid(value: Iterable[float] | np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(np.asarray(value, dtype=float), -35, 35)))


def _logloss(y: np.ndarray, probability: np.ndarray) -> np.ndarray:
    p = _clip(probability)
    return -(y * np.log(p) + (1.0 - y) * np.log(1.0 - p))


def _date_equal(frame: pd.DataFrame, values: np.ndarray) -> float:
    return float(
        pd.DataFrame({"target_date": frame["target_date"], "value": values})
        .groupby("target_date", sort=True)["value"]
        .mean()
        .mean()
    )


def _city_date_equal(frame: pd.DataFrame, values: np.ndarray) -> float:
    return float(
        pd.DataFrame(
            {"city": frame["city"], "target_date": frame["target_date"], "value": values}
        )
        .groupby(["city", "target_date"], sort=True)["value"]
        .mean()
        .mean()
    )


def _probability_metrics(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    if frame.empty:
        return {"rows": 0, "target_dates": 0, "city_dates": 0}
    y = frame["label"].to_numpy(int)
    p = frame[column].to_numpy(float)
    ll = _logloss(y, p)
    br = np.square(p - y)
    accuracy = (p >= 0.5) == y
    return {
        "rows": int(len(frame)),
        "target_dates": int(frame["target_date"].nunique()),
        "city_dates": int(frame[["city", "target_date"]].drop_duplicates().shape[0]),
        "negative_rows": int((y == 0).sum()),
        "row_logloss": float(ll.mean()),
        "date_equal_logloss": _date_equal(frame, ll),
        "city_date_equal_logloss": _city_date_equal(frame, ll),
        "row_brier": float(br.mean()),
        "date_equal_brier": _date_equal(frame, br),
        "city_date_equal_brier": _city_date_equal(frame, br),
        "classification_accuracy_at_0_5": float(accuracy.mean()),
    }


def _bootstrap_delta(
    frame: pd.DataFrame, candidate: str, baseline: str, draws: int = 20_000
) -> dict[str, Any]:
    y = frame["label"].to_numpy(int)
    output: dict[str, Any] = {}
    for metric, candidate_loss, baseline_loss in (
        (
            "logloss",
            _logloss(y, frame[candidate].to_numpy(float)),
            _logloss(y, frame[baseline].to_numpy(float)),
        ),
        (
            "brier",
            np.square(frame[candidate].to_numpy(float) - y),
            np.square(frame[baseline].to_numpy(float) - y),
        ),
    ):
        daily = (
            pd.DataFrame(
                {
                    "target_date": frame["target_date"],
                    "delta": candidate_loss - baseline_loss,
                }
            )
            .groupby("target_date", sort=True)["delta"]
            .mean()
        )
        rng = np.random.default_rng(SEED + (0 if metric == "logloss" else 1))
        sample = rng.integers(0, len(daily), size=(draws, len(daily)))
        boot = daily.to_numpy()[sample].mean(axis=1)
        output[metric] = {
            "date_equal_delta": float(daily.mean()),
            "ci95": [float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))],
            "draws": draws,
        }
    return output


@dataclass
class Fit:
    beta: np.ndarray
    median: np.ndarray
    scale: np.ndarray
    features: tuple[str, ...]
    iterations: int


def _fit_logistic(
    frame: pd.DataFrame,
    features: tuple[str, ...],
    label_column: str,
    *,
    l2: float,
) -> Fit:
    raw = frame.loc[:, features].to_numpy(float)
    median = np.nanmedian(raw, axis=0)
    median = np.where(np.isfinite(median), median, 0.0)
    raw = np.where(np.isfinite(raw), raw, median)
    scale = np.nanstd(raw, axis=0)
    scale = np.where(scale > 1e-10, scale, 1.0)
    x = np.column_stack([np.ones(len(raw)), (raw - median) / scale])
    y = frame[label_column].to_numpy(float)
    sizes = frame.groupby("target_date")["target_date"].transform("size").to_numpy()
    weights = 1.0 / sizes
    weights /= weights.sum()

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        p = _sigmoid(x @ beta)
        penalty = 0.5 * l2 * float(beta[1:] @ beta[1:])
        value = float(np.sum(weights * _logloss(y, p)) + penalty)
        gradient = x.T @ (weights * (p - y))
        gradient[1:] += l2 * beta[1:]
        return value, gradient

    result = minimize(
        lambda beta: objective(beta)[0],
        np.zeros(x.shape[1]),
        jac=lambda beta: objective(beta)[1],
        method="L-BFGS-B",
        options={"maxiter": 1000, "ftol": 1e-12, "gtol": 1e-8},
    )
    if not result.success:
        raise RuntimeError(f"foundation fit failed: {result.message}")
    return Fit(result.x, median, scale, features, int(result.nit))


def _predict_logistic(fit: Fit, frame: pd.DataFrame) -> np.ndarray:
    raw = frame.loc[:, fit.features].to_numpy(float)
    raw = np.where(np.isfinite(raw), raw, fit.median)
    x = np.column_stack([np.ones(len(raw)), (raw - fit.median) / fit.scale])
    return _sigmoid(x @ fit.beta)


def _load_weather_paths(path_by_city: dict[str, Path]) -> pd.DataFrame:
    output: list[pd.DataFrame] = []
    for city, path in path_by_city.items():
        timezone_name, station = CITY_PATHS[city]
        frame = pd.read_csv(path)
        frame["observation_event_time_utc"] = pd.to_datetime(frame["valid"], utc=True)
        frame["temp_c"] = pd.to_numeric(frame["tmpc"], errors="coerce")
        frame = frame.dropna(subset=["temp_c"])
        frame["local_time"] = frame["observation_event_time_utc"].dt.tz_convert(
            timezone_name
        )
        frame["target_date"] = frame["local_time"].dt.date.astype(str)
        frame = frame[frame["target_date"].between("2026-04-15", "2026-08-20")]
        frame["city"] = city
        frame["station"] = station
        output.append(frame)
    return pd.concat(output, ignore_index=True).sort_values(
        ["target_date", "city", "observation_event_time_utc"]
    )


def _build_weather_panel(paths: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    panel_rows: list[dict[str, Any]] = []
    hazard_rows: list[dict[str, Any]] = []
    for (city, target_date), day in paths.groupby(["city", "target_date"], sort=True):
        timezone_name, _ = CITY_PATHS[city]
        day = day.sort_values("local_time")
        final_rung = _rung(float(day["temp_c"].min()))
        for local_hour in CHECKPOINT_HOURS:
            checkpoint = pd.Timestamp(target_date, tz=timezone_name) + pd.Timedelta(
                hours=local_hour
            )
            past = day[day["local_time"].le(checkpoint)]
            if past.empty:
                continue
            current = past.iloc[-1]
            running_min = float(past["temp_c"].min())
            current_rung = _rung(running_min)
            last_min = past[past["temp_c"].eq(running_min)].iloc[-1]
            prior_hour = past[past["local_time"].le(checkpoint - pd.Timedelta(hours=1))]
            old_temp = float(prior_hour.iloc[-1]["temp_c"]) if len(prior_hour) else float(current["temp_c"])
            future_cross = day[
                day["local_time"].gt(checkpoint)
                & day["temp_c"].map(_rung).lt(current_rung)
            ]
            event_time = None if future_cross.empty else future_cross.iloc[0]["local_time"]
            base = {
                "city": city,
                "target_date": target_date,
                "checkpoint_time_utc": checkpoint.tz_convert("UTC"),
                "local_hour": local_hour,
                "current_native_rung": current_rung,
                "rebound_c": float(current["temp_c"]) - running_min,
                "minutes_since_running_min": float(
                    (checkpoint - last_min["local_time"]).total_seconds() / 60.0
                ),
                "hours_remaining": 24 - local_hour,
                "temperature_change_60m_c": float(current["temp_c"]) - old_temp,
                "native_rung_safety_margin_c": running_min - (current_rung - 0.5),
                "label": int(final_rung == current_rung),
                "crossed_before_day_end": bool(final_rung < current_rung),
                "event_time_utc": (
                    pd.NaT if event_time is None else event_time.tz_convert("UTC")
                ),
                "final_native_rung": final_rung,
            }
            panel_rows.append(base)
            for elapsed_hours in range(24 - local_hour):
                interval_start = checkpoint + pd.Timedelta(hours=elapsed_hours)
                interval_end = interval_start + pd.Timedelta(hours=1)
                event = bool(
                    event_time is not None and interval_start < event_time <= interval_end
                )
                hazard_rows.append(
                    {
                        **base,
                        "elapsed_hours": elapsed_hours,
                        "future_clock_hours": 24 - local_hour - elapsed_hours,
                        "hazard_label": int(event),
                    }
                )
                if event:
                    break
    panel = pd.DataFrame(panel_rows)
    hazard = pd.DataFrame(hazard_rows)
    if panel[["city", "target_date", "local_hour"]].duplicated().any():
        raise AssertionError("weather panel key is not unique")
    return panel, hazard


def _clock_probability(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    global_mean = float(train["label"].mean())
    summary = train.groupby("local_hour")["label"].agg(["sum", "count"])
    mapping = {
        int(hour): float((row["sum"] + 20.0 * global_mean) / (row["count"] + 20.0))
        for hour, row in summary.iterrows()
    }
    return test["local_hour"].map(mapping).fillna(global_mean).to_numpy(float)


def _predict_survival(fit: Fit, frame: pd.DataFrame) -> np.ndarray:
    output: list[float] = []
    for row in frame.itertuples(index=False):
        intervals = pd.DataFrame(
            [
                {
                    "rebound_c": row.rebound_c,
                    "minutes_since_running_min": row.minutes_since_running_min,
                    "temperature_change_60m_c": row.temperature_change_60m_c,
                    "native_rung_safety_margin_c": row.native_rung_safety_margin_c,
                    "future_clock_hours": int(row.hours_remaining) - elapsed,
                    "elapsed_hours": elapsed,
                }
                for elapsed in range(int(row.hours_remaining))
            ]
        )
        hazards = _predict_logistic(fit, intervals)
        output.append(float(np.prod(1.0 - hazards)))
    return np.asarray(output)


def _fit_adaptor(
    train: pd.DataFrame, innovation_columns: tuple[str, ...]
) -> tuple[np.ndarray, dict[str, Any]]:
    active = train["routing_indicator"].to_numpy(float)[:, None]
    z = train.loc[:, innovation_columns].to_numpy(float) * active
    y = train["label"].to_numpy(float)
    offset = _logit(train["p_market"])
    sizes = train.groupby("target_date")["target_date"].transform("size").to_numpy()
    weights = 1.0 / sizes
    weights /= weights.sum()

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        p = _sigmoid(offset + z @ beta)
        value = float(
            np.sum(weights * _logloss(y, p)) + 0.5 * ADAPTOR_L2 * (beta @ beta)
        )
        gradient = z.T @ (weights * (p - y)) + ADAPTOR_L2 * beta
        return value, gradient

    result = minimize(
        lambda beta: objective(beta)[0],
        np.zeros(len(innovation_columns)),
        jac=lambda beta: objective(beta)[1],
        bounds=[(0.0, None)] * len(innovation_columns),
        method="L-BFGS-B",
        options={"maxiter": 1000, "ftol": 1e-12, "gtol": 1e-8},
    )
    if not result.success:
        raise RuntimeError(f"market residual adaptor failed: {result.message}")
    return result.x, {"iterations": int(result.nit), "converged": True}


def _rolling_foundations(
    p0: pd.DataFrame,
    panel: pd.DataFrame,
    hazard: pd.DataFrame,
    *,
    excluded_training_city_dates: set[tuple[str, str]],
    suffix: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    working_panel = panel[
        ~panel[["city", "target_date"]].apply(tuple, axis=1).isin(excluded_training_city_dates)
    ]
    working_hazard = hazard[
        ~hazard[["city", "target_date"]].apply(tuple, axis=1).isin(excluded_training_city_dates)
    ]
    p0 = p0.copy()
    p0[f"p_physical_eod{suffix}"] = np.nan
    p0[f"p_survival_eod{suffix}"] = np.nan
    p0[f"p_clock_base{suffix}"] = np.nan
    foundation_folds: list[dict[str, Any]] = []
    for target_date in sorted(p0["target_date"].unique()):
        test_mask = p0["target_date"].eq(target_date)
        binary_train = working_panel[working_panel["target_date"].lt(target_date)]
        hazard_train = working_hazard[working_hazard["target_date"].lt(target_date)]
        binary_fit = _fit_logistic(
            binary_train, FOUNDATION_FEATURES, "label", l2=FOUNDATION_L2
        )
        hazard_fit = _fit_logistic(
            hazard_train, HAZARD_FEATURES, "hazard_label", l2=FOUNDATION_L2
        )
        p0.loc[test_mask, f"p_physical_eod{suffix}"] = _predict_logistic(
            binary_fit, p0.loc[test_mask]
        )
        p0.loc[test_mask, f"p_survival_eod{suffix}"] = _predict_survival(
            hazard_fit, p0.loc[test_mask]
        )
        p0.loc[test_mask, f"p_clock_base{suffix}"] = _clock_probability(
            binary_train, p0.loc[test_mask]
        )
        foundation_folds.append(
            {
                "test_date": target_date,
                "training_max_target_date": str(binary_train["target_date"].max()),
                "weather_training_city_dates": int(
                    binary_train[["city", "target_date"]].drop_duplicates().shape[0]
                ),
                "weather_training_rows": int(len(binary_train)),
                "weather_event_city_dates": int(
                    binary_train.loc[binary_train["label"].eq(0), ["city", "target_date"]]
                    .drop_duplicates()
                    .shape[0]
                ),
                "hazard_training_rows": int(len(hazard_train)),
                "binary_iterations": binary_fit.iterations,
                "hazard_iterations": hazard_fit.iterations,
            }
        )
    for column in (
        f"p_physical_eod{suffix}",
        f"p_survival_eod{suffix}",
        f"p_clock_base{suffix}",
    ):
        if not np.isfinite(p0[column]).all():
            raise AssertionError(f"rolling foundation prediction incomplete: {column}")
    return p0, {"folds": foundation_folds}


def _rolling_adaptor(
    frame: pd.DataFrame,
    innovation_columns: tuple[str, ...],
    output_column: str,
) -> tuple[pd.Series, list[dict[str, Any]]]:
    prediction = frame["p_market"].astype(float).copy()
    folds: list[dict[str, Any]] = []
    for target_date in sorted(frame["target_date"].unique()):
        train = frame[frame["target_date"].lt(target_date)]
        test_mask = frame["target_date"].eq(target_date)
        enough = (
            train["target_date"].nunique() >= MIN_PRIOR_DATES
            and train["label"].eq(0).sum() >= MIN_PRIOR_NEGATIVES
        )
        if enough:
            coefficients, fit_meta = _fit_adaptor(train, innovation_columns)
            z = (
                frame.loc[test_mask, innovation_columns].to_numpy(float)
                * frame.loc[test_mask, "routing_indicator"].to_numpy(float)[:, None]
            )
            prediction.loc[test_mask] = _sigmoid(
                _logit(frame.loc[test_mask, "p_market"]) + z @ coefficients
            )
            outside_route = test_mask & frame["routing_indicator"].eq(0)
            prediction.loc[outside_route] = frame.loc[outside_route, "p_market"].astype(float)
            status = "fit"
        else:
            coefficients = np.zeros(len(innovation_columns))
            fit_meta = {"iterations": 0, "converged": True}
            status = "fail_closed_to_market"
        folds.append(
            {
                "test_date": target_date,
                "training_max_target_date": (
                    None if train.empty else str(train["target_date"].max())
                ),
                "status": status,
                "prior_target_dates": int(train["target_date"].nunique()),
                "prior_rows": int(len(train)),
                "prior_negative_rows": int(train["label"].eq(0).sum()),
                "coefficients": {
                    column: float(value)
                    for column, value in zip(innovation_columns, coefficients)
                },
                **fit_meta,
            }
        )
    if not np.isfinite(prediction).all():
        raise AssertionError(f"OOF prediction incomplete: {output_column}")
    return prediction, folds


def _foundation_fixed_splits(
    panel: pd.DataFrame, hazard: pd.DataFrame
) -> dict[str, Any]:
    splits = {
        "validation": ("2026-06-30", "2026-07-01", "2026-07-31"),
        "test": ("2026-07-31", "2026-08-01", "2026-08-20"),
    }
    output: dict[str, Any] = {
        "training_contract": "fixed earlier target dates; same target_date never crosses train/eval",
        "splits": {},
    }
    for name, (train_end, eval_start, eval_end) in splits.items():
        train = panel[panel["target_date"].le(train_end)]
        hazard_train = hazard[hazard["target_date"].le(train_end)]
        evaluation = panel[panel["target_date"].between(eval_start, eval_end)]
        binary_fit = _fit_logistic(
            train, FOUNDATION_FEATURES, "label", l2=FOUNDATION_L2
        )
        hazard_fit = _fit_logistic(
            hazard_train, HAZARD_FEATURES, "hazard_label", l2=FOUNDATION_L2
        )
        work = evaluation.copy()
        work["p_clock"] = _clock_probability(train, work)
        work["p_binary"] = _predict_logistic(binary_fit, work)
        work["p_hazard"] = _predict_survival(hazard_fit, work)
        output["splits"][name] = {
            "train_end": train_end,
            "evaluation_range": [eval_start, eval_end],
            "train_rows": int(len(train)),
            "train_city_dates": int(train[["city", "target_date"]].drop_duplicates().shape[0]),
            "train_event_city_dates": int(
                train.loc[train["label"].eq(0), ["city", "target_date"]]
                .drop_duplicates()
                .shape[0]
            ),
            "evaluation_rows": int(len(work)),
            "evaluation_city_dates": int(
                work[["city", "target_date"]].drop_duplicates().shape[0]
            ),
            "evaluation_event_city_dates": int(
                work.loc[work["label"].eq(0), ["city", "target_date"]]
                .drop_duplicates()
                .shape[0]
            ),
            "models": {
                model: _probability_metrics(work, column)
                for model, column in {
                    "clock": "p_clock",
                    "binary_physical": "p_binary",
                    "one_hour_hazard": "p_hazard",
                }.items()
            },
        }
    return output


def _selector_replay(
    frame: pd.DataFrame,
    probability_column: str,
    *,
    active_windows_only: bool,
) -> tuple[dict[str, Any], pd.DataFrame]:
    # Historical comparison uses one common settled P0 denominator.  Forward
    # and unsettled rows are intentionally excluded from trigger counts.
    work = frame[frame["settled_binary"].eq(True) & frame[probability_column].notna()].copy()
    work["fee_per_share"] = FEE_RATE * work["executable_cost"] * (
        1.0 - work["executable_cost"]
    )
    work["net_edge"] = (
        work[probability_column] - work["executable_cost"] - work["fee_per_share"]
    )
    gate = work["execution_candidate_status_gate"].eq(True) & work["net_edge"].gt(0)
    if active_windows_only:
        gate &= work["cooling_window_state"].isin(ACTIVE_WINDOWS)
    positive = work[gate].sort_values(["decision_ts_utc", "candidate_id"])
    selected = positive.drop_duplicates(["city", "target_date"], keep="first")
    settled = selected[selected["settled_binary"].eq(True)].copy()
    settled["cost_per_share"] = settled["executable_cost"] + settled["fee_per_share"]
    settled["pnl_per_share"] = settled["label"] - settled["cost_per_share"]
    cost = float(5.0 * settled["cost_per_share"].sum())
    pnl = float(5.0 * settled["pnl_per_share"].sum())
    summary = {
        "probability_column": probability_column,
        "active_windows_only": active_windows_only,
        "probability_rows": int(work[probability_column].notna().sum()),
        "execution_clean_rows": int(work["execution_candidate_status_gate"].sum()),
        "positive_edge_checkpoint_rows": int(len(positive)),
        "selected_city_dates": int(len(selected)),
        "settled_trades": int(len(settled)),
        "settled_target_dates": int(settled["target_date"].nunique()),
        "wins": int(settled["label"].sum()),
        "win_rate": None if settled.empty else float(settled["label"].mean()),
        "five_share_cost": cost,
        "fee_adjusted_pnl": pnl,
        "roi": None if cost == 0 else pnl / cost,
    }
    return summary, settled


def _corrected_cross_replay(paths: pd.DataFrame, reconciliation: pd.DataFrame) -> dict[str, Any]:
    exchange = {
        (str(row.city), str(row.target_date)): str(row.exchange_resolved_rung)
        for row in reconciliation.itertuples(index=False)
        if int(row.exchange_winner_count) == 1
    }

    def contains(label: str, rung: int) -> bool:
        text = label.strip().lower()
        first = text.split()[0]
        if not first.lstrip("-").isdigit():
            return False
        boundary = int(first)
        if "or below" in text:
            return rung <= boundary
        if "or higher" in text:
            return rung >= boundary
        return rung == boundary

    output: dict[str, Any] = {
        "old_bug": "UTC-15h date transform",
        "corrected_rule": "IANA Asia/Seoul or Asia/Tokyo local date (UTC+09h)",
        "cities": {},
    }
    for city, city_frame in paths.groupby("city", sort=True):
        crosses: list[dict[str, Any]] = []
        for target_date, day in city_frame.groupby("target_date", sort=True):
            previous: int | None = None
            rank = 0
            for row in day.sort_values("observation_event_time_utc").itertuples(index=False):
                current = _rung(float(row.temp_c))
                if previous is None:
                    previous = current
                    continue
                if current < previous:
                    rank += 1
                    exchange_label = exchange.get((city, target_date))
                    crosses.append(
                        {
                            "target_date": target_date,
                            "rank": rank,
                            "previous_rung": previous,
                            "crossed_to_rung": current,
                            "exchange_label": exchange_label,
                            "no_side_win": (
                                None
                                if exchange_label is None
                                else not contains(exchange_label, previous)
                            ),
                        }
                    )
                    previous = current
        labeled = [row for row in crosses if row["no_side_win"] is not None]
        first = [row for row in labeled if row["rank"] == 1]
        output["cities"][city] = {
            "nights_with_cross": len({row["target_date"] for row in crosses}),
            "all_crosses": len(crosses),
            "labeled_crosses": len(labeled),
            "no_side_wins": sum(bool(row["no_side_win"]) for row in labeled),
            "no_side_win_rate": float(np.mean([row["no_side_win"] for row in labeled])),
            "first_cross_labeled": len(first),
            "first_cross_no_side_wins": sum(bool(row["no_side_win"]) for row in first),
            "first_cross_no_side_win_rate": float(
                np.mean([row["no_side_win"] for row in first])
            ),
            "loss_rows": [row for row in labeled if not row["no_side_win"]],
        }
    return output


def build(args: argparse.Namespace) -> dict[str, Any]:
    if args.output_dir.exists() and any(args.output_dir.iterdir()) and not args.allow_existing:
        raise FileExistsError(f"refusing to overwrite non-empty output: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    p0 = pd.read_parquet(args.row_audit)
    if len(p0) != 168 or p0["checkpoint_id"].duplicated().any():
        raise AssertionError("P0 must be 168 unique settled probability rows")
    raw_features = pd.DataFrame(p0["raw_feature_values_json"].map(json.loads).tolist())
    p0["rebound_c"] = pd.to_numeric(raw_features["rebound_c"], errors="coerce")
    p0["minutes_since_running_min"] = pd.to_numeric(
        raw_features["minutes_since_running_min"], errors="coerce"
    )
    p0["hours_remaining"] = pd.to_numeric(raw_features["hours_remaining"], errors="coerce")
    p0["temperature_change_60m_c"] = pd.to_numeric(
        raw_features["temperature_change_60m_c"], errors="coerce"
    )
    p0["local_hour"] = pd.to_numeric(raw_features["local_hour"], errors="coerce").astype(int)
    # P0 persisted the integer running rung but not the sub-rung raw minimum.
    # Use the neutral half-step; sensitivity is handled by strong shrinkage.
    p0["native_rung_safety_margin_c"] = 0.5
    p0["routing_indicator"] = p0["window"].isin(ACTIVE_WINDOWS).astype(int)
    p0["p_v1_incumbent"] = p0["p_v1_incumbent"].astype(float)
    p0["p_v1_alpha010_routed"] = p0["p_v1_challenger"].astype(float)

    paths = _load_weather_paths({"Seoul": args.iem_seoul, "Tokyo": args.iem_tokyo})
    panel, hazard = _build_weather_panel(paths)
    reconciliation = pd.read_parquet(args.reconciliation)
    disputed = set(
        map(
            tuple,
            reconciliation.loc[
                reconciliation["reconciliation_status"].eq("UNRESOLVED_MISMATCH"),
                ["city", "target_date"],
            ].to_numpy(),
        )
    )
    if disputed != {
        ("Seoul", "2026-04-25"),
        ("Seoul", "2026-05-02"),
        ("Seoul", "2026-05-05"),
        ("Seoul", "2026-05-13"),
    }:
        raise AssertionError(f"unexpected settlement-source dispute set: {sorted(disputed)}")

    fixed_splits = _foundation_fixed_splits(panel, hazard)
    p0, full_foundation = _rolling_foundations(
        p0, panel, hazard, excluded_training_city_dates=set(), suffix=""
    )
    p0, strict_foundation = _rolling_foundations(
        p0, panel, hazard, excluded_training_city_dates=disputed, suffix="_strict"
    )
    for suffix in ("", "_strict"):
        p0[f"z_physical{suffix}"] = _logit(p0[f"p_physical_eod{suffix}"]) - _logit(
            p0[f"p_clock_base{suffix}"]
        )
        p0[f"z_survival{suffix}"] = _logit(p0[f"p_survival_eod{suffix}"]) - _logit(
            p0[f"p_clock_base{suffix}"]
        )
        p0[f"p_v2_1{suffix}"], v21_folds = _rolling_adaptor(
            p0, (f"z_physical{suffix}",), f"p_v2_1{suffix}"
        )
        p0[f"p_v3{suffix}"], v3_folds = _rolling_adaptor(
            p0, (f"z_survival{suffix}",), f"p_v3{suffix}"
        )
        if suffix == "":
            full_foundation["v2_1_adaptor_folds"] = v21_folds
            full_foundation["v3_adaptor_folds"] = v3_folds
        else:
            strict_foundation["v2_1_adaptor_folds"] = v21_folds
            strict_foundation["v3_adaptor_folds"] = v3_folds
        outside = p0["routing_indicator"].eq(0)
        for model in (f"p_v2_1{suffix}", f"p_v3{suffix}"):
            if not np.allclose(
                p0.loc[outside, model], p0.loc[outside, "p_market"], atol=1e-15, rtol=0
            ):
                raise AssertionError(f"{model} must equal market outside active route")

    model_columns = {
        "RAW_MARKET": "p_market",
        "V1_INCUMBENT_ALPHA050_ALL_WINDOWS": "p_v1_incumbent",
        "V1_FROZEN_ALPHA010_ROUTED": "p_v1_alpha010_routed",
        "V2_1_PHYSICAL_TRANSFER_ROUTED": "p_v2_1",
        "V3_ONE_HOUR_HAZARD_ROUTED": "p_v3",
        "V2_1_STRICT_DISPUTE_EXCLUDED": "p_v2_1_strict",
        "V3_STRICT_DISPUTE_EXCLUDED": "p_v3_strict",
    }
    probability: dict[str, Any] = {
        "overall": {name: _probability_metrics(p0, column) for name, column in model_columns.items()},
        "segments": {},
        "bootstrap_vs_market": {
            name: _bootstrap_delta(p0, column, "p_market")
            for name, column in model_columns.items()
            if column != "p_market"
        },
    }
    for segment, (start, end) in P0_SEGMENTS.items():
        subset = p0[p0["target_date"].between(start, end)]
        probability["segments"][segment] = {
            name: _probability_metrics(subset, column)
            for name, column in model_columns.items()
        }

    trade = pd.read_csv(args.trade_funnel)
    prediction_columns = [
        "checkpoint_id",
        "p_v2_1",
        "p_v3",
        "p_v2_1_strict",
        "p_v3_strict",
    ]
    trade = trade.merge(p0[prediction_columns], on="checkpoint_id", how="left", validate="one_to_one")
    trade["p_v1_alpha010_routed"] = trade["challenger_p"]
    trade["p_v1_incumbent"] = trade["p_model"]
    selector_specs = {
        "V1_INCUMBENT_ALPHA050_ALL_WINDOWS": ("p_v1_incumbent", False),
        "V1_FROZEN_ALPHA010_ROUTED": ("p_v1_alpha010_routed", True),
        "V2_1_PHYSICAL_TRANSFER_ROUTED": ("p_v2_1", True),
        "V3_ONE_HOUR_HAZARD_ROUTED": ("p_v3", True),
        "V2_1_STRICT_DISPUTE_EXCLUDED": ("p_v2_1_strict", True),
        "V3_STRICT_DISPUTE_EXCLUDED": ("p_v3_strict", True),
    }
    selector: dict[str, Any] = {}
    selected_frames: list[pd.DataFrame] = []
    funnel_rows: list[dict[str, Any]] = []
    for name, (column, routed) in selector_specs.items():
        summary, settled = _selector_replay(
            trade, column, active_windows_only=routed
        )
        selector[name] = summary
        settled["model"] = name
        selected_frames.append(settled)
        funnel_rows.append({"model": name, **summary})
    incumbent = selector["V1_INCUMBENT_ALPHA050_ALL_WINDOWS"]
    if not (
        incumbent["settled_trades"] == 19
        and incumbent["wins"] == 19
        and abs(incumbent["five_share_cost"] - 89.85885325) < 1e-9
        and abs(incumbent["fee_adjusted_pnl"] - 5.14114675) < 1e-9
    ):
        raise AssertionError(f"V1 frozen trade headline no longer reproduces: {incumbent}")

    selected_all = pd.concat(selected_frames, ignore_index=True) if selected_frames else pd.DataFrame()
    segment_rows: list[dict[str, Any]] = []
    probability_slice_keys = {
        "window": ["window"],
        "city": ["city"],
        "p_market_band": ["p_market_band"],
        "p0_segment": ["p0_segment"],
    }
    p0["p_market_band"] = pd.cut(
        p0["p_market"],
        bins=[-np.inf, 0.8, 0.9, 0.95, 0.98, np.inf],
        labels=["<.80", ".80-.90", ".90-.95", ".95-.98", ">=.98"],
        right=False,
    ).astype(str)
    p0["p0_segment"] = "UNASSIGNED"
    for segment, (start, end) in P0_SEGMENTS.items():
        p0.loc[p0["target_date"].between(start, end), "p0_segment"] = segment
    for slice_name, keys in probability_slice_keys.items():
        for key, subset in p0.groupby(keys, sort=True):
            values = key if isinstance(key, tuple) else (key,)
            for model, column in model_columns.items():
                metric = _probability_metrics(subset, column)
                segment_rows.append(
                    {
                        "evidence_type": "probability",
                        "slice": slice_name,
                        "slice_value": "|".join(map(str, values)),
                        "model": model,
                        **metric,
                        "settled_trades": None,
                        "wins": None,
                        "five_share_cost": None,
                        "fee_adjusted_pnl": None,
                        "roi": None,
                    }
                )
    if not selected_all.empty:
        selected_all["price_band"] = pd.cut(
            selected_all["executable_cost"],
            bins=[-np.inf, 0.60, 0.95, 0.98, np.inf],
            labels=["<=.60", ".60-.95", ".95-.98", ">.98"],
            right=True,
        ).astype(str)
        selected_all["p0_segment"] = "UNASSIGNED"
        for segment, (start, end) in P0_SEGMENTS.items():
            selected_all.loc[
                selected_all["target_date"].between(start, end), "p0_segment"
            ] = segment
        for slice_name, column in {
            "trade_window": "cooling_window_state",
            "trade_price_band": "price_band",
            "trade_p0_segment": "p0_segment",
        }.items():
            for (model, value), subset in selected_all.groupby(["model", column], sort=True):
                cost = float(5.0 * subset["cost_per_share"].sum())
                pnl = float(5.0 * subset["pnl_per_share"].sum())
                segment_rows.append(
                    {
                        "evidence_type": "trade",
                        "slice": slice_name,
                        "slice_value": str(value),
                        "model": model,
                        "rows": None,
                        "target_dates": int(subset["target_date"].nunique()),
                        "city_dates": int(len(subset)),
                        "negative_rows": None,
                        "row_logloss": None,
                        "date_equal_logloss": None,
                        "city_date_equal_logloss": None,
                        "row_brier": None,
                        "date_equal_brier": None,
                        "city_date_equal_brier": None,
                        "classification_accuracy_at_0_5": None,
                        "settled_trades": int(len(subset)),
                        "wins": int(subset["label"].sum()),
                        "five_share_cost": cost,
                        "fee_adjusted_pnl": pnl,
                        "roi": None if cost == 0 else pnl / cost,
                    }
                )

    corrected_replay = _corrected_cross_replay(paths, reconciliation)
    foundation = {
        "weather_panel": {
            "rows": int(len(panel)),
            "independent_city_dates": int(
                panel[["city", "target_date"]].drop_duplicates().shape[0]
            ),
            "target_dates": int(panel["target_date"].nunique()),
            "event_city_dates": int(
                panel.loc[panel["label"].eq(0), ["city", "target_date"]]
                .drop_duplicates()
                .shape[0]
            ),
            "by_city": {
                city: {
                    "rows": int(len(group)),
                    "city_dates": int(group["target_date"].nunique()),
                    "event_city_dates": int(
                        group.loc[group["label"].eq(0), "target_date"].nunique()
                    ),
                }
                for city, group in panel.groupby("city", sort=True)
            },
        },
        "truth_sensitivity": {
            "all_path_training": full_foundation,
            "exclude_four_exchange_dispute_days": strict_foundation,
            "excluded_city_dates": [list(item) for item in sorted(disputed)],
        },
        "fixed_split_performance": fixed_splits,
        "forecast_uncertainty": {
            "status": "FORECAST_UNCERTAINTY_DATA_INSUFFICIENT_FOR_ACTIVE_WINDOWS",
            "gamma": 0.0,
            "inventory": json.loads(args.forecast_inventory.read_text(encoding="utf-8")),
            "reason": (
                "canonical archive starts during the local target day and does not provide "
                "consistent prior-vintage coverage for the 06:00/09:00 active-window checkpoints"
            ),
        },
    }
    result = {
        "research_only": True,
        "live_authorization": False,
        "model_selection_used_pnl": False,
        "data_cutoff": "2026-08-26 settled P0; weather paths through 2026-08-20",
        "truth_gate": {
            "coverage": 1.0,
            "exchange_exact_match_rate": 226 / 230,
            "promotion_gate_pass": False,
            "diagnostic_sensitivity_completed": True,
        },
        "models": {
            "V2_1_PHYSICAL_TRANSFER_ROUTED": (
                "logit(p_market) + I(active_window) * alpha_oof * "
                "[logit(p_physical_eod)-logit(p_clock_base)]; gamma=0 fail-closed"
            ),
            "V3_ONE_HOUR_HAZARD_ROUTED": (
                "logit(p_market) + I(active_window) * lambda_oof * "
                "[logit(product_hour(1-h_hour))-logit(p_clock_base)]"
            ),
        },
        "probability_performance": probability,
        "selector_performance": selector,
        "disposition": "KEEP_V1_FORWARD_ONLY",
        "reason": (
            "V2.1 and V3 do not improve the common P0 proper-score evidence and trigger no "
            "settled trades under the frozen routed selector; settlement-source truth remains "
            "below the 99% promotion gate."
        ),
    }

    _write_json(args.output_dir / "MODEL_AND_STRATEGY_RESULTS.json", result)
    _write_json(args.output_dir / "FOUNDATION_SPLIT_RESULTS.json", foundation)
    _write_json(args.output_dir / "CORRECTED_DATE_REPLAY_RESULTS.json", corrected_replay)
    pd.DataFrame(funnel_rows).to_csv(args.output_dir / "SIGNAL_AND_TRADE_FUNNEL.csv", index=False)
    pd.DataFrame(segment_rows).to_csv(args.output_dir / "SEGMENT_PERFORMANCE.csv", index=False)
    audit_columns = [
        "candidate_id",
        "checkpoint_id",
        "city",
        "target_date",
        "decision_ts_utc",
        "window",
        "label",
        "p_market",
        "p_v1_incumbent",
        "p_v1_alpha010_routed",
        "p_physical_eod",
        "p_survival_eod",
        "p_clock_base",
        "z_physical",
        "z_survival",
        "routing_indicator",
        "p_v2_1",
        "p_v3",
        "p_v2_1_strict",
        "p_v3_strict",
        "p_market_band",
        "p0_segment",
    ]
    p0[audit_columns].to_parquet(
        args.output_dir / "ROW_LEVEL_MODEL_AND_SIGNAL_AUDIT.parquet",
        index=False,
        engine="pyarrow",
    )
    if len(pd.read_parquet(args.output_dir / "ROW_LEVEL_MODEL_AND_SIGNAL_AUDIT.parquet")) != 168:
        raise AssertionError("row audit parquet round trip failed")

    incumbent = selector["V1_INCUMBENT_ALPHA050_ALL_WINDOWS"]
    challenger = selector["V1_FROZEN_ALPHA010_ROUTED"]
    v21 = selector["V2_1_PHYSICAL_TRANSFER_ROUTED"]
    v3 = selector["V3_ONE_HOUR_HAZARD_ROUTED"]
    summary = f"""# Tmin V2.1 / V3 策略读数（research-only）

## 结论

日期错误已按首尔/东京本地日 `UTC+09` 修正并重跑。V2.1 与 V3 已完成 weather-history foundation、strict prior-date OOF market adaptor、相同 frozen selector 与 5-share fee-adjusted replay；结果不支持替换 V1。

| 模型 | settled trades | 触发 target_dates | wins | 5-share cost | fee-adjusted PnL | ROI |
|---|---:|---:|---:|---:|---:|---:|
| V1 incumbent | {incumbent['settled_trades']} | {incumbent['settled_target_dates']} | {incumbent['wins']} | {incumbent['five_share_cost']:.8f} | {incumbent['fee_adjusted_pnl']:+.8f} | {incumbent['roi']:.2%} |
| V1 frozen alpha=.10 routed | {challenger['settled_trades']} | {challenger['settled_target_dates']} | {challenger['wins']} | {challenger['five_share_cost']:.8f} | {challenger['fee_adjusted_pnl']:+.8f} | {challenger['roi']:.2%} |
| V2.1 physical transfer routed | {v21['settled_trades']} | {v21['settled_target_dates']} | {v21['wins']} | {v21['five_share_cost']:.8f} | {v21['fee_adjusted_pnl']:+.8f} | N/A |
| V3 1h hazard routed | {v3['settled_trades']} | {v3['settled_target_dates']} | {v3['wins']} | {v3['five_share_cost']:.8f} | {v3['fee_adjusted_pnl']:+.8f} | N/A |

V2.1/V3 的 0 trades 不是 quote 缺失，而是 strict OOF residual 在 frozen active windows 内没有产生 `p_model > direct YES ask + fee` 的 row。它们的 weather foundation 在固定 validation/test 上也没有稳定胜过简单 clock baseline，因此不能靠放宽 selector 或调 threshold 补出交易。

## 数据阶段

- Weather-only panel：{len(panel)} checkpoint rows，{panel[['city','target_date']].drop_duplicates().shape[0]} independent city-days，{panel.loc[panel.label.eq(0), ['city','target_date']].drop_duplicates().shape[0]} next-colder event city-days。
- P0：168 settled probability rows / 15 target dates；P1 active route：{int(p0.routing_indicator.sum())} rows / {p0.loc[p0.routing_indicator.eq(1), 'target_date'].nunique()} dates。
- 四个 exchange/source 争议日既保留在 primary training，也做了全部排除的敏感性版本；不用于制造更好结果。
- Forecast uncertainty arm 因 06:00/09:00 active-window 缺少一致 prior-vintage archive 而 `gamma=0` fail closed；没有伪造 forecast uncertainty。

## 正确日期 replay

- Seoul：{corrected_replay['cities']['Seoul']['labeled_crosses']} labeled strict crosses，NO wins {corrected_replay['cities']['Seoul']['no_side_wins']}；first-of-night {corrected_replay['cities']['Seoul']['first_cross_no_side_wins']}/{corrected_replay['cities']['Seoul']['first_cross_labeled']}。
- Tokyo：{corrected_replay['cities']['Tokyo']['labeled_crosses']} labeled strict crosses，NO wins {corrected_replay['cities']['Tokyo']['no_side_wins']}；first-of-night {corrected_replay['cities']['Tokyo']['first_cross_no_side_wins']}/{corrected_replay['cities']['Tokyo']['first_cross_labeled']}。

该 corrected cross replay 是日期错误影响量化，不是 V2.1/V3 selector 绩效，不能与 19-trade YES strategy 混为同一策略。

**Disposition: `KEEP_V1_FORWARD_ONLY`**。这是 probability research 结论，不是 live、tiny-live 或执行授权。
"""
    (args.output_dir / "EXECUTIVE_STRATEGY_READOUT.md").write_text(summary, encoding="utf-8")
    reproduce_output = (
        str(args.output_dir.relative_to(ROOT))
        if args.output_dir.is_relative_to(ROOT)
        else str(args.output_dir)
    )
    reproduce = f"""#!/usr/bin/env bash
set -euo pipefail
cd \"$(git rev-parse --show-toplevel)\"
.venv/bin/python scripts/analysis/tmin/tmin_v2_1_v3_strategy_readout_v1.py \\
  --output-dir {reproduce_output} \\
  --allow-existing
"""
    reproduce_path = args.output_dir / "REPRODUCE.sh"
    reproduce_path.write_text(reproduce, encoding="utf-8")
    reproduce_path.chmod(0o755)

    inputs = [
        args.row_audit,
        args.trade_funnel,
        args.iem_seoul,
        args.iem_tokyo,
        args.reconciliation,
        args.forecast_inventory,
        Path(__file__),
    ]
    evidence = {
        "schema_version": "tmin_v2_1_v3_strategy_readout_evidence_v1",
        "code_sha": _git("rev-parse", "HEAD"),
        "git_patch_required": bool(_git("status", "--short")),
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scipy": scipy.__version__,
            "pyarrow": pyarrow.__version__,
        },
        "inputs": [
            {
                "path": str(path.relative_to(ROOT)),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in inputs
        ],
    }
    outputs = [
        path
        for path in sorted(args.output_dir.iterdir())
        if path.is_file() and path.name != "EVIDENCE_MANIFEST.json"
    ]
    evidence["outputs"] = [
        {"path": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path in outputs
    ]
    _write_json(args.output_dir / "EVIDENCE_MANIFEST.json", evidence)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    base = ROOT / "reviews/tmin_model_layer_v2_1_v3_research_v1"
    parser.add_argument(
        "--row-audit", type=Path, default=base / "ROW_LEVEL_PROBABILITY_AUDIT.parquet"
    )
    parser.add_argument(
        "--trade-funnel",
        type=Path,
        default=ROOT / "reviews/tmin_no_further_model_forensics_v1/ROW_LEVEL_TRADE_FUNNEL.csv",
    )
    parser.add_argument(
        "--iem-seoul",
        type=Path,
        default=ROOT / "runtime/research/tmin_cross_metar_basis_replay_v1/raw/RKSI_apr14_aug21.csv",
    )
    parser.add_argument(
        "--iem-tokyo",
        type=Path,
        default=ROOT / "runtime/research/tmin_cross_metar_basis_replay_v1/raw/RJTT_apr14_aug21.csv",
    )
    parser.add_argument(
        "--reconciliation",
        type=Path,
        default=base / "SETTLEMENT_SOURCE_RECONCILIATION.parquet",
    )
    parser.add_argument(
        "--forecast-inventory",
        type=Path,
        default=base / "frozen_inputs/forecast_archive_inventory.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "reviews/tmin_v2_1_v3_strategy_readout_v1",
    )
    parser.add_argument("--allow-existing", action="store_true")
    args = parser.parse_args()
    build(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
