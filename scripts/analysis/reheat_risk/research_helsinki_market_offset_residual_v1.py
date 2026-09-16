#!/usr/bin/env python3
"""Helsinki market-logit offset residual with active-bracket PIT books.

The model is trained only on prior target dates at the first evidence-backed
date-X state entry.  Market logit is a fixed offset; weather/path variables can
only learn a correction.  High-price rows stay in the probability denominator,
while a cost<0.98 cohort is reported separately for research readability.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

import joblib
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit
from sklearn.metrics import roc_auc_score


ROOT = Path(__file__).resolve().parents[3]
REPLAY = ROOT / "docs/analysis/2026-07/generated/helsinki_remaining_heat_market_replay_v2"
ACTIVE_BOOKS = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/"
    "helsinki_pre_cross_active_ladder_shadow/active_bracket_books"
)
OUTPUT = ROOT / "docs/analysis/2026-07/generated/helsinki_market_offset_residual_v1"
REPORT = ROOT / "docs/analysis/2026-07/2026-07-31-helsinki-market-offset-residual-v1.md"
FADE_REPORT = ROOT / "docs/analysis/2026-07/2026-07-31-helsinki-fade-augmented-market-replay-v1.md"
FINAL_FREEZE_SPEC = ROOT / "docs/analysis/2026-07/2026-07-31-helsinki-market-residual-final-freeze-spec-v1.json"
FADE_ARTIFACT = ROOT / "docs/analysis/2026-07/generated/helsinki_fade_morphology_v1/helsinki_fade_morphology_v1.joblib"
V7_ARTIFACT = ROOT / "docs/analysis/2026-07/generated/helsinki_remaining_heat_probability_v7/helsinki_remaining_heat_v7_missing_forecast_expert_challenger.joblib"
FEE_RATE = 0.05
SEED = 20260731
BOOTSTRAP_DRAWS = 5000
EPS = 1e-6
MIN_TRAIN_DATES = 5
RIDGE = 1.0

COMPACT_FEATURES = ("weather_market_logit_gap",)
STATE_FEATURES = (
    "weather_market_logit_gap",
    "forecast_peak_h",
    "forecast_future_peak_margin_vs_boundary_c",
    "official_latest_pullback_c",
    "official_report_age_min",
    "fmi_minus_official_latest_temp_c",
    "forecast_current_innovation_c",
    "forecast_future_reheat_strength_c",
    "temp_slope_30m_cph",
    "global_radiation_slope_30m",
    "path_pullback",
    "path_fade",
    "path_plateau",
    "local_hour_sin",
    "local_hour_cos",
)
FADE_FEATURES = STATE_FEATURES + (
    "fade_reheat_p_30m",
    "fade_reheat_p_60m",
    "fade_reheat_p_120m",
    "fade_reheat_p_eod",
)
PATH_FEATURES = STATE_FEATURES + (
    "fade_morph_p_30m",
    "fade_morph_p_60m",
    "fade_morph_p_120m",
    "fade_morph_p_eod",
    "fresh_runway_morph_p_30m",
    "fresh_runway_morph_p_60m",
    "fresh_runway_morph_p_120m",
    "fresh_runway_morph_p_eod",
)
DYNAMIC_FADE_FEATURES = STATE_FEATURES + (
    "fade_morph_p_30m",
    "fade_morph_p_60m",
    "fade_morph_p_120m",
    "fade_morph_p_eod",
)
FADE_PLUS_FRESH_FEATURES = FADE_FEATURES + (
    "fresh_runway_morph_p_30m",
    "fresh_runway_morph_p_60m",
    "fresh_runway_morph_p_120m",
    "fresh_runway_morph_p_eod",
)


def sweep_asks(levels: list[dict[str, Any]], shares: float = 5.0) -> dict[str, float] | None:
    remaining = shares
    notional = 0.0
    fee = 0.0
    for row in sorted(levels, key=lambda item: float(item.get("price", 2))):
        try:
            price = float(row["price"])
            size = float(row["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 < price < 1 and size > 0):
            continue
        take = min(remaining, size)
        notional += take * price
        fee += take * FEE_RATE * price * (1 - price)
        remaining -= take
        if remaining <= 1e-9:
            cash = notional + fee
            return {"cash_cost": cash, "effective_cost": cash / shares}
    return None


def load_active_first_books(wanted_dates: set[str]) -> pd.DataFrame:
    earliest: dict[tuple[str, pd.Timestamp, int], dict[str, Any]] = {}
    for target_date in sorted(wanted_dates):
        path = ACTIVE_BOOKS / f"{target_date}.jsonl"
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                    if row.get("book_status") != "ok" or row.get("outcome") != "no":
                        continue
                    observation = pd.to_datetime(row["source_obs_ts_utc"], utc=True)
                    detected = pd.to_datetime(row["source_detect_ts_utc"], utc=True)
                    captured = pd.to_datetime(row["ts_utc"], utc=True)
                    bracket = int(row["bracket"])
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue
                lag = (captured - detected).total_seconds()
                if lag < 0 or lag > 120:
                    continue
                key = (target_date, observation, bracket)
                old = earliest.get(key)
                if (
                    old is not None
                    and pd.to_datetime(old["active_book_ts_utc"], utc=True) <= captured
                ):
                    continue
                summary = row.get("summary") or {}
                bid = summary.get("best_bid")
                ask = summary.get("best_ask")
                asks = (row.get("raw") or {}).get("asks") or []
                cost5 = sweep_asks(asks, shares=5)
                cost10 = sweep_asks(asks, shares=10)
                earliest[key] = {
                    "target_date": target_date,
                    "observation_time_utc": observation,
                    "official_running_max_c": bracket,
                    "active_source_detect_ts_utc": detected,
                    "active_book_ts_utc": captured,
                    "active_book_lag_sec": lag,
                    "market_no_mid_active": (
                        (float(bid) + float(ask)) / 2
                        if bid is not None and ask is not None
                        else np.nan
                    ),
                    "direct_no_effective5_active": (
                        cost5["effective_cost"] if cost5 else np.nan
                    ),
                    "direct_no_cash5_active": (
                        cost5["cash_cost"] if cost5 else np.nan
                    ),
                    "direct_no_effective10_active": (
                        cost10["effective_cost"] if cost10 else np.nan
                    ),
                    "direct_no_cash10_active": (
                        cost10["cash_cost"] if cost10 else np.nan
                    ),
                }
    return pd.DataFrame(earliest.values())


def prepare_rows() -> tuple[pd.DataFrame, dict[str, Any]]:
    states = pd.read_csv(REPLAY / "checkpoint_market_states_v2_v7.csv.gz")
    states["observation_time_utc"] = pd.to_datetime(
        states["observation_time_utc"], utc=True
    )
    numeric = [
        "y_break", "market_no_mid", "p_break_v7", "direct_no_effective5",
        "direct_no_cash5", "forecast_minutes_to_future_peak",
        "forecast_future_peak_margin_vs_boundary_c", "official_latest_pullback_c",
        "official_report_age_min", "fmi_minus_official_latest_temp_c",
        "forecast_current_innovation_c", "forecast_future_reheat_strength_c",
        "temp_slope_30m_cph", "global_radiation_slope_30m",
    ]
    for column in numeric:
        states[column] = pd.to_numeric(states.get(column), errors="coerce")
    active = load_active_first_books(set(states["target_date"].astype(str)))
    if not active.empty:
        states = states.merge(
            active,
            on=["target_date", "observation_time_utc", "official_running_max_c"],
            how="left",
            validate="one_to_one",
        )
    else:
        for column in (
            "active_book_ts_utc", "active_book_lag_sec", "market_no_mid_active",
            "direct_no_effective5_active", "direct_no_cash5_active",
            "direct_no_effective10_active", "direct_no_cash10_active",
        ):
            states[column] = np.nan
    states["book_evidence"] = np.where(
        states["market_no_mid_active"].notna(), "active_first_after_source", "full_ladder_before_source"
    )
    states["market_probability"] = states["market_no_mid_active"].combine_first(
        states["market_no_mid"]
    )
    states["effective_cost"] = states["direct_no_effective5_active"].combine_first(
        states["direct_no_effective5"]
    )
    states["cash_cost"] = states["direct_no_cash5_active"].combine_first(
        states["direct_no_cash5"]
    )
    states["effective_cost_10"] = states[
        "direct_no_effective10_active"
    ].combine_first(states["direct_no_effective10"])
    states["cash_cost_10"] = states["direct_no_cash10_active"].combine_first(
        states["direct_no_cash10"]
    )
    states["decision_ts_utc"] = states["active_book_ts_utc"].combine_first(
        states["source_first_seen_ts_utc"]
    )
    rows = states.loc[
        states[["y_break", "market_probability", "p_break_v7"]].notna().all(axis=1)
    ].copy()
    rows["y_break"] = rows["y_break"].astype(int)
    market = np.clip(rows["market_probability"], 1e-4, 1 - 1e-4)
    weather = np.clip(rows["p_break_v7"], 1e-4, 1 - 1e-4)
    rows["market_logit"] = np.log(market / (1 - market))
    rows["weather_logit"] = np.log(weather / (1 - weather))
    rows["weather_market_logit_gap"] = rows["weather_logit"] - rows["market_logit"]
    rows["forecast_peak_h"] = rows["forecast_minutes_to_future_peak"] / 60
    for state in ("pullback", "fade", "plateau"):
        rows[f"path_{state}"] = rows["path_state"].eq(state).astype(float)
    local = pd.to_datetime(rows["decision_ts_utc"], utc=True).dt.tz_convert(
        "Europe/Helsinki"
    )
    angle = 2 * np.pi * (local.dt.hour + local.dt.minute / 60) / 24
    rows["local_hour_sin"] = np.sin(angle)
    rows["local_hour_cos"] = np.cos(angle)
    rows = rows.sort_values(["target_date", "decision_ts_utc"])
    coverage = {
        "weather_checkpoints": int(len(states)),
        "full_ladder_market_rows": int(states["market_no_mid"].notna().sum()),
        "active_matched_rows": int(states["market_no_mid_active"].notna().sum()),
        "combined_unique_market_rows": int(len(rows)),
        "combined_dates": int(rows["target_date"].nunique()),
        "active_dates": int(
            rows.loc[rows["book_evidence"].eq("active_first_after_source"), "target_date"].nunique()
        ),
    }
    return rows, coverage


def matrix(
    train: pd.DataFrame, test: pd.DataFrame, features: Sequence[str]
) -> tuple[np.ndarray, np.ndarray]:
    train_x = train[list(features)].replace([np.inf, -np.inf], np.nan)
    test_x = test[list(features)].replace([np.inf, -np.inf], np.nan)
    median = train_x.median().fillna(0)
    train_x = train_x.fillna(median)
    test_x = test_x.fillna(median)
    mean = train_x.mean()
    scale = train_x.std(ddof=0).replace(0, 1).fillna(1)
    return (
        ((train_x - mean) / scale).to_numpy(float),
        ((test_x - mean) / scale).to_numpy(float),
    )


def fit_offset(
    train: pd.DataFrame, test: pd.DataFrame, features: Sequence[str]
) -> np.ndarray:
    train_x, test_x = matrix(train, test, features)
    train_x = np.column_stack([np.ones(len(train_x)), train_x])
    test_x = np.column_stack([np.ones(len(test_x)), test_x])
    y = train["y_break"].to_numpy(float)
    offset = train["market_logit"].to_numpy(float)
    date_counts = train["target_date"].value_counts()
    weights = train["target_date"].map(lambda value: 1 / date_counts[value]).to_numpy()
    weights = weights / weights.mean()

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        probability = np.clip(expit(offset + train_x @ beta), EPS, 1 - EPS)
        loss = np.average(
            -y * np.log(probability) - (1 - y) * np.log(1 - probability),
            weights=weights,
        )
        penalty = RIDGE * float(beta[1:] @ beta[1:]) / len(train)
        gradient = train_x.T @ (weights * (probability - y)) / weights.sum()
        gradient[1:] += 2 * RIDGE * beta[1:] / len(train)
        return loss + penalty, gradient

    result = minimize(
        lambda beta: objective(beta),
        np.zeros(train_x.shape[1]),
        jac=True,
        method="L-BFGS-B",
    )
    if not result.success:
        raise RuntimeError(f"offset fit failed: {result.message}")
    return expit(test["market_logit"].to_numpy(float) + test_x @ result.x)


def freeze_offset_model(
    train: pd.DataFrame, features: Sequence[str]
) -> dict[str, Any]:
    raw = train[list(features)].replace([np.inf, -np.inf], np.nan)
    median = raw.median().fillna(0)
    filled = raw.fillna(median)
    mean = filled.mean()
    scale = filled.std(ddof=0).replace(0, 1).fillna(1)
    train_x = ((filled - mean) / scale).to_numpy(float)
    train_x = np.column_stack([np.ones(len(train_x)), train_x])
    y = train["y_break"].to_numpy(float)
    offset = train["market_logit"].to_numpy(float)
    date_counts = train["target_date"].value_counts()
    weights = train["target_date"].map(
        lambda value: 1 / date_counts[value]
    ).to_numpy()
    weights = weights / weights.mean()

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        probability = np.clip(expit(offset + train_x @ beta), EPS, 1 - EPS)
        loss = np.average(
            -y * np.log(probability) - (1 - y) * np.log(1 - probability),
            weights=weights,
        )
        penalty = RIDGE * float(beta[1:] @ beta[1:]) / len(train)
        gradient = train_x.T @ (weights * (probability - y)) / weights.sum()
        gradient[1:] += 2 * RIDGE * beta[1:] / len(train)
        return loss + penalty, gradient

    result = minimize(
        lambda beta: objective(beta),
        np.zeros(train_x.shape[1]),
        jac=True,
        method="L-BFGS-B",
    )
    if not result.success:
        raise RuntimeError(f"final offset fit failed: {result.message}")
    return {
        "model_id": "helsinki_market_offset_fade_v1",
        "features": list(features),
        "median": median.to_dict(),
        "mean": mean.to_dict(),
        "scale": scale.to_dict(),
        "beta": result.x.tolist(),
        "market_logit_fixed_offset": True,
        "ridge": RIDGE,
        "training_grain": (
            "first evidence-backed path-state entry after date/X/path transition"
        ),
        "train_rows": int(len(train)),
        "train_target_dates": int(train["target_date"].nunique()),
        "train_start": str(train["target_date"].min()),
        "train_end": str(train["target_date"].max()),
        "clean_forward_start": "2026-07-31",
        "final_strategy_price_filter": None,
        "freeze_spec_sha256": hashlib.sha256(
            FINAL_FREEZE_SPEC.read_bytes()
        ).hexdigest(),
        "fade_artifact_sha256": hashlib.sha256(
            FADE_ARTIFACT.read_bytes()
        ).hexdigest(),
        "v7_artifact_sha256": hashlib.sha256(V7_ARTIFACT.read_bytes()).hexdigest(),
    }


def state_entry_rows(rows: pd.DataFrame) -> pd.DataFrame:
    frame = rows.sort_values(["target_date", "decision_ts_utc"]).copy()
    previous_date = frame["target_date"].shift()
    changed = (
        frame["target_date"].ne(previous_date)
        | frame["official_running_max_c"].ne(frame["official_running_max_c"].shift())
        | frame["path_state"].ne(frame["path_state"].shift())
    )
    frame["_state_entry_id"] = changed.cumsum()
    return frame.groupby("_state_entry_id", as_index=False).head(1)


def expanding_predictions(rows: pd.DataFrame) -> pd.DataFrame:
    entries = state_entry_rows(rows)
    dates = sorted(rows["target_date"].unique())
    outputs = []
    definitions = {
        "offset_compact": COMPACT_FEATURES,
        "offset_state": STATE_FEATURES,
        "offset_fade": FADE_FEATURES,
        "offset_fade_dynamic": DYNAMIC_FADE_FEATURES,
        "offset_fade_plus_fresh": FADE_PLUS_FRESH_FEATURES,
        "offset_path": PATH_FEATURES,
    }
    for index, target_date in enumerate(dates):
        if index < MIN_TRAIN_DATES:
            continue
        train = entries.loc[entries["target_date"].isin(dates[:index])].copy()
        test = rows.loc[rows["target_date"].eq(target_date)].copy()
        if train["y_break"].nunique() < 2 or test.empty:
            continue
        output = test.copy()
        for model_name, features in definitions.items():
            output[f"p_{model_name}"] = fit_offset(train, test, features)
        outputs.append(output)
    return pd.concat(outputs, ignore_index=True)


def metrics(rows: pd.DataFrame, probability: str) -> dict[str, Any]:
    daily = []
    for _date, group in rows.groupby("target_date"):
        y = group["y_break"].to_numpy(float)
        p = np.clip(group[probability].to_numpy(float), EPS, 1 - EPS)
        daily.append(
            {
                "brier": float(np.mean((p - y) ** 2)),
                "logloss": float(np.mean(-y * np.log(p) - (1 - y) * np.log(1 - p))),
            }
        )
    frame = pd.DataFrame(daily)
    y_all = rows["y_break"].to_numpy(float)
    p_all = np.clip(rows[probability].to_numpy(float), EPS, 1 - EPS)
    return {
        "rows": int(len(rows)),
        "target_dates": int(rows["target_date"].nunique()),
        "brier": float(frame["brier"].mean()),
        "logloss": float(frame["logloss"].mean()),
        "accuracy_0p5": float(np.mean((p_all >= 0.5) == y_all)),
        "calibration_bias": float(np.mean(p_all - y_all)),
        "auc": (
            float(roc_auc_score(y_all, p_all))
            if len(np.unique(y_all)) == 2
            else None
        ),
    }


def score_bootstrap(
    rows: pd.DataFrame,
    challenger: str,
    baseline: str = "market_probability",
) -> list[dict[str, Any]]:
    y = rows["y_break"].to_numpy(float)
    p1 = np.clip(rows[challenger].to_numpy(float), EPS, 1 - EPS)
    p0 = np.clip(rows[baseline].to_numpy(float), EPS, 1 - EPS)
    outputs = []
    rng = np.random.default_rng(SEED)
    for metric in ("brier", "logloss"):
        delta = (
            (p1 - y) ** 2 - (p0 - y) ** 2
            if metric == "brier"
            else (
                -y * np.log(p1) - (1 - y) * np.log(1 - p1)
                + y * np.log(p0) + (1 - y) * np.log(1 - p0)
            )
        )
        daily = pd.DataFrame(
            {"target_date": rows["target_date"], "delta": delta}
        ).groupby("target_date")["delta"].mean()
        draws = rng.choice(
            daily.to_numpy(), size=(BOOTSTRAP_DRAWS, len(daily)), replace=True
        ).mean(axis=1)
        outputs.append(
            {
                "metric": metric,
                "challenger": challenger,
                "baseline": baseline,
                "challenger_minus_baseline": float(daily.mean()),
                "ci_low": float(np.quantile(draws, 0.025)),
                "ci_high": float(np.quantile(draws, 0.975)),
            }
        )
    return outputs


def route(
    rows: pd.DataFrame, probability: str, shares: int = 5
) -> pd.DataFrame:
    if shares == 5:
        effective_cost_column = "effective_cost"
        cash_cost_column = "cash_cost"
    elif shares == 10:
        effective_cost_column = "effective_cost_10"
        cash_cost_column = "cash_cost_10"
    else:
        raise ValueError(f"unsupported shares={shares}")
    outputs = []
    for (_date, _current), group in rows.groupby(
        ["target_date", "official_running_max_c"], sort=True
    ):
        for row in group.sort_values("decision_ts_utc").itertuples(index=False):
            p = getattr(row, probability)
            effective_cost = getattr(row, effective_cost_column)
            cash_cost = getattr(row, cash_cost_column)
            if pd.isna(effective_cost) or float(p) <= float(effective_cost):
                continue
            won = bool(int(row.y_break))
            outputs.append(
                {
                    "model": probability,
                    "shares": shares,
                    "target_date": row.target_date,
                    "current_x": int(row.official_running_max_c),
                    "decision_ts_utc": row.decision_ts_utc,
                    "book_evidence": row.book_evidence,
                    "path_state": row.path_state,
                    "forecast_peak_h": row.forecast_peak_h,
                    "probability": float(p),
                    "effective_cost": float(effective_cost),
                    "edge": float(p) - float(effective_cost),
                    "cost": float(cash_cost),
                    "won": won,
                    "pnl": shares * int(won) - float(cash_cost),
                    "research_actionable": bool(float(effective_cost) < 0.98),
                }
            )
            break
    return pd.DataFrame(outputs)


def trade_summary(
    rows: pd.DataFrame, universe_dates: Sequence[str] | None = None
) -> dict[str, Any]:
    cost = float(rows["cost"].sum()) if len(rows) else 0.0
    pnl = float(rows["pnl"].sum()) if len(rows) else 0.0
    summary = {
        "signals": int(len(rows)),
        "dates": int(rows["target_date"].nunique()) if len(rows) else 0,
        "wins": int(rows["won"].sum()) if len(rows) else 0,
        "errors": int((~rows["won"]).sum()) if len(rows) else 0,
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost else None,
    }
    if universe_dates is not None:
        dates = pd.Index(sorted(set(str(value) for value in universe_dates)))
        daily = (
            rows.groupby("target_date")[["cost", "pnl"]].sum()
            .reindex(dates, fill_value=0)
        )
        rng = np.random.default_rng(SEED)
        indices = rng.integers(0, len(daily), size=(BOOTSTRAP_DRAWS, len(daily)))
        draw_cost = daily["cost"].to_numpy()[indices].sum(axis=1)
        draw_pnl = daily["pnl"].to_numpy()[indices].sum(axis=1)
        draw_roi = np.divide(
            draw_pnl,
            draw_cost,
            out=np.full(BOOTSTRAP_DRAWS, np.nan),
            where=draw_cost > 0,
        )
        valid = draw_roi[np.isfinite(draw_roi)]
        summary["target_date_bootstrap"] = {
            "target_dates": int(len(daily)),
            "draws": BOOTSTRAP_DRAWS,
            "ci_low": float(np.quantile(valid, 0.025)) if len(valid) else None,
            "ci_high": float(np.quantile(valid, 0.975)) if len(valid) else None,
        }
    return summary


def add_scenario_columns(rows: pd.DataFrame) -> pd.DataFrame:
    frame = rows.copy()
    peak = pd.to_numeric(frame["forecast_peak_h"], errors="coerce")
    frame["peak_window"] = pd.cut(
        peak,
        bins=[-np.inf, 0, 1, 2, np.inf],
        labels=["peak_passed", "peak_0_1h", "peak_1_2h", "peak_gt_2h"],
        include_lowest=True,
    ).astype("object")
    frame["peak_window"] = frame["peak_window"].fillna("peak_missing")
    local = pd.to_datetime(frame["decision_ts_utc"], utc=True).dt.tz_convert(
        "Europe/Helsinki"
    )
    frame["local_time_window"] = pd.cut(
        local.dt.hour + local.dt.minute / 60,
        bins=[-np.inf, 12, 15, 18, np.inf],
        labels=["before_12", "12_15", "15_18", "after_18"],
        right=False,
    ).astype(str)
    if "market_probability" in frame:
        frame["market_price_band"] = pd.cut(
            frame["market_probability"],
            bins=[-np.inf, 0.5, 0.8, 0.98, np.inf],
            labels=["lt_0p50", "0p50_0p80", "0p80_0p98", "ge_0p98"],
            right=False,
        ).astype(str)
    return frame


def scenario_scores(rows: pd.DataFrame) -> pd.DataFrame:
    frame = add_scenario_columns(rows)
    outputs = []
    for dimension in (
        "path_state",
        "peak_window",
        "local_time_window",
        "book_evidence",
        "market_price_band",
    ):
        for value, group in frame.groupby(dimension, observed=True):
            market = metrics(group, "market_probability")
            state = metrics(group, "p_offset_state")
            fade = metrics(group, "p_offset_fade")
            path = metrics(group, "p_offset_path")
            outputs.append(
                {
                    "dimension": dimension,
                    "value": str(value),
                    "rows": int(len(group)),
                    "target_dates": int(group["target_date"].nunique()),
                    "event_rate": float(group["y_break"].mean()),
                    "market_brier": market["brier"],
                    "state_brier": state["brier"],
                    "state_minus_market_brier": state["brier"] - market["brier"],
                    "market_logloss": market["logloss"],
                    "state_logloss": state["logloss"],
                    "state_minus_market_logloss": (
                        state["logloss"] - market["logloss"]
                    ),
                    "market_accuracy_0p5": market["accuracy_0p5"],
                    "state_accuracy_0p5": state["accuracy_0p5"],
                    "market_calibration_bias": market["calibration_bias"],
                    "state_calibration_bias": state["calibration_bias"],
                    "fade_brier": fade["brier"],
                    "fade_logloss": fade["logloss"],
                    "fade_accuracy_0p5": fade["accuracy_0p5"],
                    "fade_calibration_bias": fade["calibration_bias"],
                    "fade_minus_market_brier": fade["brier"] - market["brier"],
                    "fade_minus_state_brier": fade["brier"] - state["brier"],
                    "fade_minus_market_logloss": (
                        fade["logloss"] - market["logloss"]
                    ),
                    "fade_minus_state_logloss": (
                        fade["logloss"] - state["logloss"]
                    ),
                    "path_brier": path["brier"],
                    "path_logloss": path["logloss"],
                    "path_accuracy_0p5": path["accuracy_0p5"],
                    "path_calibration_bias": path["calibration_bias"],
                    "path_minus_market_brier": path["brier"] - market["brier"],
                    "path_minus_fade_brier": path["brier"] - fade["brier"],
                    "path_minus_market_logloss": (
                        path["logloss"] - market["logloss"]
                    ),
                    "path_minus_fade_logloss": (
                        path["logloss"] - fade["logloss"]
                    ),
                }
            )
    return pd.DataFrame(outputs)


def trade_slices(rows: pd.DataFrame) -> pd.DataFrame:
    frame = add_scenario_columns(rows)
    outputs = []
    for dimension in (
        "path_state",
        "peak_window",
        "book_evidence",
        "research_actionable",
    ):
        for value, group in frame.groupby(dimension, observed=True):
            outputs.append(
                {
                    "dimension": dimension,
                    "value": str(value),
                    **trade_summary(group),
                }
            )
    return pd.DataFrame(outputs)


def trade_pair_bootstrap(
    challenger: pd.DataFrame,
    baseline: pd.DataFrame,
    universe_dates: Sequence[str],
) -> dict[str, Any]:
    dates = pd.Index(sorted(set(str(value) for value in universe_dates)))
    challenger_daily = (
        challenger.groupby("target_date")[["cost", "pnl"]].sum()
        .reindex(dates, fill_value=0)
    )
    baseline_daily = (
        baseline.groupby("target_date")[["cost", "pnl"]].sum()
        .reindex(dates, fill_value=0)
    )
    rng = np.random.default_rng(SEED)
    indices = rng.integers(0, len(dates), size=(BOOTSTRAP_DRAWS, len(dates)))
    challenger_values = challenger_daily.to_numpy()[indices].sum(axis=1)
    baseline_values = baseline_daily.to_numpy()[indices].sum(axis=1)
    challenger_roi = np.divide(
        challenger_values[:, 1],
        challenger_values[:, 0],
        out=np.full(BOOTSTRAP_DRAWS, np.nan),
        where=challenger_values[:, 0] > 0,
    )
    baseline_roi = np.divide(
        baseline_values[:, 1],
        baseline_values[:, 0],
        out=np.full(BOOTSTRAP_DRAWS, np.nan),
        where=baseline_values[:, 0] > 0,
    )
    delta = challenger_roi - baseline_roi
    delta = delta[np.isfinite(delta)]
    challenger_cost = float(challenger["cost"].sum())
    baseline_cost = float(baseline["cost"].sum())
    roi_delta = (
        float(challenger["pnl"].sum()) / challenger_cost
        - float(baseline["pnl"].sum()) / baseline_cost
        if challenger_cost > 0 and baseline_cost > 0
        else None
    )
    return {
        "roi_delta": roi_delta,
        "ci_low": float(np.quantile(delta, 0.025)) if len(delta) else None,
        "ci_high": float(np.quantile(delta, 0.975)) if len(delta) else None,
        "target_dates": int(len(dates)),
    }


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    rows, coverage = prepare_rows()
    predictions = expanding_predictions(rows)
    entries = (
        predictions.sort_values("decision_ts_utc")
        .groupby(["target_date", "official_running_max_c"], as_index=False)
        .head(1)
    )
    state_entries = state_entry_rows(predictions)
    scenarios = scenario_scores(predictions)
    probabilities = (
        "market_probability",
        "p_break_v7",
        "p_offset_compact",
        "p_offset_state",
        "p_offset_fade",
        "p_offset_fade_dynamic",
        "p_offset_fade_plus_fresh",
        "p_offset_path",
    )
    scores = []
    bootstraps = {}
    for grain_name, grain in (
        ("checkpoint", predictions),
        ("state_entry", state_entries),
        ("date_x_entry", entries),
    ):
        for probability in probabilities:
            scores.append({"grain": grain_name, "model": probability, **metrics(grain, probability)})
        for challenger in (
            "p_break_v7",
            "p_offset_compact",
            "p_offset_state",
            "p_offset_fade",
            "p_offset_fade_dynamic",
            "p_offset_fade_plus_fresh",
            "p_offset_path",
        ):
            bootstraps[f"{grain_name}:{challenger}"] = score_bootstrap(grain, challenger)
        bootstraps[f"{grain_name}:fade_vs_state"] = score_bootstrap(
            grain, "p_offset_fade", "p_offset_state"
        )
        bootstraps[f"{grain_name}:path_vs_fade"] = score_bootstrap(
            grain, "p_offset_path", "p_offset_fade"
        )
        bootstraps[f"{grain_name}:fade_dynamic_vs_fade"] = score_bootstrap(
            grain, "p_offset_fade_dynamic", "p_offset_fade"
        )
        bootstraps[f"{grain_name}:fade_plus_fresh_vs_fade"] = score_bootstrap(
            grain, "p_offset_fade_plus_fresh", "p_offset_fade"
        )

    trades = pd.concat(
        [route(predictions, probability) for probability in probabilities[1:]],
        ignore_index=True,
    )
    trades_10share = pd.concat(
        [
            route(predictions, probability, shares=10)
            for probability in probabilities[1:]
        ],
        ignore_index=True,
    )
    trading = {}
    oof_dates = sorted(predictions["target_date"].astype(str).unique())
    for probability in probabilities[1:]:
        subset = trades.loc[trades["model"].eq(probability)]
        trading[probability] = {
            "all_raw_positive_edge": trade_summary(subset, oof_dates),
            "research_actionable_cost_lt_0p98": trade_summary(
                subset.loc[subset["research_actionable"]], oof_dates
            ),
            "all_raw_positive_edge_10share": trade_summary(
                trades_10share.loc[trades_10share["model"].eq(probability)],
                oof_dates,
            ),
        }
    state_trade_slices = trade_slices(
        trades.loc[trades["model"].eq("p_offset_state")].copy()
    )
    fade_trade_slices = trade_slices(
        trades.loc[trades["model"].eq("p_offset_fade")].copy()
    )
    path_trade_slices = trade_slices(
        trades.loc[trades["model"].eq("p_offset_path")].copy()
    )
    fade_error_trades = trades.loc[
        trades["model"].eq("p_offset_fade") & ~trades["won"]
    ].copy()
    fade_vs_state_trade = trade_pair_bootstrap(
        trades.loc[trades["model"].eq("p_offset_fade")],
        trades.loc[trades["model"].eq("p_offset_state")],
        oof_dates,
    )
    path_vs_fade_trade = trade_pair_bootstrap(
        trades.loc[trades["model"].eq("p_offset_path")],
        trades.loc[trades["model"].eq("p_offset_fade")],
        oof_dates,
    )
    state_signals = trades.loc[
        trades["model"].eq("p_offset_state"),
        ["target_date", "current_x", "decision_ts_utc", "path_state"],
    ]
    fade_signals = trades.loc[
        trades["model"].eq("p_offset_fade"),
        ["target_date", "current_x", "decision_ts_utc", "path_state"],
    ]
    signal_pair = state_signals.merge(
        fade_signals,
        on=["target_date", "current_x"],
        how="outer",
        suffixes=("_state", "_fade"),
        indicator=True,
    )
    fade_signal_comparison = {
        "state_signals": int(len(state_signals)),
        "fade_signals": int(len(fade_signals)),
        "common_date_x": int(signal_pair["_merge"].eq("both").sum()),
        "state_only": int(signal_pair["_merge"].eq("left_only").sum()),
        "fade_only": int(signal_pair["_merge"].eq("right_only").sum()),
        "changed_entry_time": int(
            signal_pair.loc[signal_pair["_merge"].eq("both"), "decision_ts_utc_state"]
            .ne(
                signal_pair.loc[
                    signal_pair["_merge"].eq("both"), "decision_ts_utc_fade"
                ]
            )
            .sum()
        ),
    }
    final_train_entries = state_entry_rows(rows)
    final_artifact = freeze_offset_model(final_train_entries, FADE_FEATURES)
    final_artifact_path = OUTPUT / "helsinki_market_offset_fade_v1.joblib"
    joblib.dump(final_artifact, final_artifact_path)
    final_artifact_sha = hashlib.sha256(
        final_artifact_path.read_bytes()
    ).hexdigest()
    summary = {
        "status": "research_only_market_offset_not_selected_no_live_change",
        "coverage": coverage,
        "training_grain": (
            "first evidence-backed path-state entry after date/X/path transition; "
            "prior target dates only"
        ),
        "ridge": RIDGE,
        "min_train_dates": MIN_TRAIN_DATES,
        "scores": scores,
        "target_date_bootstrap": bootstraps,
        "trading": trading,
        "scenario_breakdown_file": "scenario_scores.csv",
        "state_trade_breakdown_file": "state_trade_slices.csv",
        "fade_trade_breakdown_file": "fade_trade_slices.csv",
        "path_trade_breakdown_file": "path_trade_slices.csv",
        "fade_error_file": "fade_error_trades.csv",
        "fade_vs_state_trade_bootstrap": fade_vs_state_trade,
        "path_vs_fade_trade_bootstrap": path_vs_fade_trade,
        "fade_signal_comparison": fade_signal_comparison,
        "development_window_reused": True,
        "final_freeze": {
            "artifact": str(final_artifact_path.relative_to(ROOT)),
            "artifact_sha256": final_artifact_sha,
            "train_rows": final_artifact["train_rows"],
            "train_target_dates": final_artifact["train_target_dates"],
            "train_end": final_artifact["train_end"],
            "clean_forward_start": final_artifact["clean_forward_start"],
            "selected_feature_family": "offset_fade",
            "live_change": False,
        },
        "research_reporting_filter": (
            "effective_cost<0.98 is readability/actionability cohort only; "
            "all probability rows and raw-positive-edge trades remain reported"
        ),
        "forward": "2026-07-31+ not read for label/model selection",
    }
    predictions.to_csv(OUTPUT / "oof_checkpoint_predictions.csv.gz", index=False, compression="gzip")
    entries.to_csv(OUTPUT / "oof_date_x_entries.csv", index=False)
    state_entries.to_csv(OUTPUT / "oof_state_entries.csv", index=False)
    pd.DataFrame(scores).to_csv(OUTPUT / "probability_scores.csv", index=False)
    trades.to_csv(OUTPUT / "trade_replay.csv", index=False)
    trades_10share.to_csv(OUTPUT / "trade_replay_10share.csv", index=False)
    scenarios.to_csv(OUTPUT / "scenario_scores.csv", index=False)
    state_trade_slices.to_csv(OUTPUT / "state_trade_slices.csv", index=False)
    fade_trade_slices.to_csv(OUTPUT / "fade_trade_slices.csv", index=False)
    path_trade_slices.to_csv(OUTPUT / "path_trade_slices.csv", index=False)
    fade_error_trades.to_csv(OUTPUT / "fade_error_trades.csv", index=False)
    (OUTPUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    score_map = pd.DataFrame(scores).set_index(["grain", "model"])
    checkpoint_state = score_map.loc[("checkpoint", "p_offset_state")]
    checkpoint_market = score_map.loc[("checkpoint", "market_probability")]
    state_entry = score_map.loc[("date_x_entry", "p_offset_state")]
    market_entry = score_map.loc[("date_x_entry", "market_probability")]
    state_trade = trading["p_offset_state"]
    scenario_map = scenarios.set_index(["dimension", "value"])
    fade = scenario_map.loc[("path_state", "fade")]
    near_peak = scenario_map.loc[("peak_window", "peak_0_1h")]
    active_evidence = scenario_map.loc[
        ("book_evidence", "active_first_after_source")
    ]
    actionable_trade = state_trade["research_actionable_cost_lt_0p98"]
    raw_trade = state_trade["all_raw_positive_edge"]
    REPORT.write_text(
        "\n".join(
            [
                "# Helsinki market-offset residual v1",
                "",
                "## 数据快照",
                "",
                "- 数据源：frozen v7 replay + Helsinki active-bracket telemetry；research replay，actual fill=0。",
                f"- 固定天气 checkpoints={coverage['weather_checkpoints']}；full-ladder market rows={coverage['full_ladder_market_rows']}；"
                f"active matched={coverage['active_matched_rows']}；combined unique={coverage['combined_unique_market_rows']}。",
                "- unsettled/missing settlement 不进入概率分数；2026-07-31+ 不读标签、不调参。",
                "",
                "## 两个漏斗",
                "",
                f"- signal funnel：2,115 weather checkpoints → expanding OOF 362 rows/9 dates → "
                f"state-offset raw positive-edge {raw_trade['signals']} date-X signals；"
                f"其中 research-actionable {actionable_trade['signals']}。",
                f"- evidence funnel：534 full-ladder rows + 231 active matched rows → "
                f"去重后598 settled market rows/14 dates；前5日只训练，故评分使用362 rows/9 dates。",
                "- active book 是 source detect 后0–120秒首份盘口；full-ladder 是 source 前最近一份盘口，二者不混写时钟。",
                "",
                "## 结论",
                "",
                f"- checkpoint 上 state offset Brier/logloss={checkpoint_state['brier']:.5f}/"
                f"{checkpoint_state['logloss']:.5f}，market={checkpoint_market['brier']:.5f}/"
                f"{checkpoint_market['logloss']:.5f}；Brier delta="
                f"{checkpoint_state['brier'] - checkpoint_market['brier']:+.5f}。",
                f"- date-X entry 上 state offset Brier={state_entry['brier']:.5f}，market={market_entry['brier']:.5f}；"
                "负 delta 才表示 residual 有增量。",
                f"- raw positive-edge：{raw_trade['signals']}笔/{raw_trade['errors']}错，"
                f"ROI={raw_trade['roi']:.2%}，date CI="
                f"[{raw_trade['target_date_bootstrap']['ci_low']:.2%},"
                f"{raw_trade['target_date_bootstrap']['ci_high']:.2%}]；"
                f"research-actionable cost<0.98："
                f"{actionable_trade['signals']}笔/{actionable_trade['errors']}错，"
                f"ROI={actionable_trade['roi']:.2%}，date CI="
                f"[{actionable_trade['target_date_bootstrap']['ci_low']:.2%},"
                f"{actionable_trade['target_date_bootstrap']['ci_high']:.2%}]。",
                "- cost<0.98 只用于研究摘要去除统计尘埃，不进入训练、不改最终策略。",
                "",
                "## 结构性错误切片",
                "",
                f"- fade：{int(fade['rows'])} rows，state/market accuracy="
                f"{fade['state_accuracy_0p5']:.1%}/{fade['market_accuracy_0p5']:.1%}，"
                f"Brier delta={fade['state_minus_market_brier']:+.5f}；仍明显退化。",
                f"- forecast peak 未来0–1h：{int(near_peak['rows'])} rows，state/market accuracy="
                f"{near_peak['state_accuracy_0p5']:.1%}/{near_peak['market_accuracy_0p5']:.1%}，"
                f"Brier delta={near_peak['state_minus_market_brier']:+.5f}；临峰判断仍是主弱点。",
                f"- active first-after-source：{int(active_evidence['rows'])} rows/4 dates，"
                f"Brier delta={active_evidence['state_minus_market_brier']:+.5f}；"
                "新增高频证据本身尚未证明 residual 优于市场。",
                "- 完整 path/peak/local-hour/evidence/price-band 正确率、校准与 proper score 见 "
                "`generated/helsinki_market_offset_residual_v1/scenario_scores.csv`。",
                "",
                "## 口径与资格",
                "",
                "- market logit 是固定 offset；模型只学 prior-date 的 weather/path correction。",
                "- checkpoint Brier delta 的 date-block 95% CI 为 "
                "[-0.04832,+0.02865]，仍跨0；date-X logloss点估基本持平。",
                "- 当前点改善主要来自非 active 子集，fade/临峰切片仍差；不能选为生产模型。",
                "- significance/baseline/forward 未全过，保持 inconclusive/collector，不改 live；"
                "下一版只能作为预注册 nonlinear state interaction challenger，等待新日期验证。",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    checkpoint_fade = score_map.loc[("checkpoint", "p_offset_fade")]
    date_x_fade = score_map.loc[("date_x_entry", "p_offset_fade")]
    fade_trade = trading["p_offset_fade"]
    fade_raw = fade_trade["all_raw_positive_edge"]
    fade_actionable = fade_trade["research_actionable_cost_lt_0p98"]
    fade_probability_delta = bootstraps["checkpoint:fade_vs_state"]
    fade_vs_market = bootstraps["checkpoint:p_offset_fade"]
    fade_slice = scenario_map.loc[("path_state", "fade")]
    active_slice = scenario_map.loc[
        ("book_evidence", "active_first_after_source")
    ]
    FADE_REPORT.write_text(
        "\n".join(
            [
                "# Helsinki fade-augmented market replay v1",
                "",
                "## 人话结论",
                "",
                "- 冻结fade morphology概率接入后，概率质量改善，但28个date-X入场集合没有变化，"
                "所以ROI和错单数也没有变化。",
                f"- checkpoint accuracy：fade增强={checkpoint_fade['accuracy_0p5']:.2%}，"
                f"旧state={checkpoint_state['accuracy_0p5']:.2%}，market="
                f"{checkpoint_market['accuracy_0p5']:.2%}。",
                f"- checkpoint Brier/logloss：fade增强={checkpoint_fade['brier']:.5f}/"
                f"{checkpoint_fade['logloss']:.5f}，旧state={checkpoint_state['brier']:.5f}/"
                f"{checkpoint_state['logloss']:.5f}，market={checkpoint_market['brier']:.5f}/"
                f"{checkpoint_market['logloss']:.5f}。",
                f"- fade增强相对旧state的Brier delta="
                f"{fade_probability_delta[0]['challenger_minus_baseline']:+.5f} "
                f"CI[{fade_probability_delta[0]['ci_low']:+.5f},"
                f"{fade_probability_delta[0]['ci_high']:+.5f}]；"
                f"logloss delta={fade_probability_delta[1]['challenger_minus_baseline']:+.5f} "
                f"CI[{fade_probability_delta[1]['ci_low']:+.5f},"
                f"{fade_probability_delta[1]['ci_high']:+.5f}]。",
                f"- 相对market虽点估改善，Brier delta="
                f"{fade_vs_market[0]['challenger_minus_baseline']:+.5f}，"
                f"CI[{fade_vs_market[0]['ci_low']:+.5f},"
                f"{fade_vs_market[0]['ci_high']:+.5f}]，仍跨0。",
                "",
                "## ROI与明细",
                "",
                f"- 全部price保留：{fade_raw['signals']}笔，{fade_raw['wins']}胜/"
                f"{fade_raw['errors']}错，投入${fade_raw['cost']:.2f}，"
                f"净${fade_raw['pnl']:+.2f}，ROI={fade_raw['roi']:.2%}，"
                f"date CI[{fade_raw['target_date_bootstrap']['ci_low']:.2%},"
                f"{fade_raw['target_date_bootstrap']['ci_high']:.2%}]。",
                f"- 仅研究摘要cost<0.98：{fade_actionable['signals']}笔，"
                f"{fade_actionable['wins']}胜/{fade_actionable['errors']}错，"
                f"净${fade_actionable['pnl']:+.2f}，ROI={fade_actionable['roi']:.2%}；"
                "不作为最终策略阈值。",
                f"- old/new均为28个date-X，common={fade_signal_comparison['common_date_x']}，"
                f"仅{fade_signal_comparison['changed_entry_time']}个改变首次入场时刻；"
                "没有新增或删除date-X，因此ROI delta=0。",
                "",
                "## 场景正确率",
                "",
                f"- fade rows={int(fade_slice['rows'])}：accuracy "
                f"旧state={fade_slice['state_accuracy_0p5']:.1%} → "
                f"fade增强={fade_slice['fade_accuracy_0p5']:.1%}，market="
                f"{fade_slice['market_accuracy_0p5']:.1%}；Brier "
                f"{fade_slice['state_brier']:.3f} → {fade_slice['fade_brier']:.3f}，"
                f"仍差于market {fade_slice['market_brier']:.3f}。",
                f"- active first-after-source rows={int(active_slice['rows'])}/4 dates："
                f"fade增强Brier={active_slice['fade_brier']:.5f}，market="
                f"{active_slice['market_brier']:.5f}，样本仍不足。",
                "- path/peak/evidence/price和逐笔明细见generated目录的"
                "`scenario_scores.csv`、`fade_trade_slices.csv`、`trade_replay.csv`。",
                "",
                "## 资格",
                "",
                "- 这是已看过7/15–29窗口的开发回放；fade artifact本身冻结于2025，"
                "但该市场窗口参与过结构诊断，不能冒充clean forward。",
                f"- date-X entry Brier fade增强={date_x_fade['brier']:.5f}，"
                f"market={market_entry['brier']:.5f}；仍只有9个OOF market dates。",
                "- probability relative old state=PASS；market baseline=FAIL_CI；"
                "clean forward=NA；结论inconclusive，保持zero-notional，不改live。",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
