#!/usr/bin/env python3
"""Frozen D-1 exact-ladder comparison of pooled, hierarchical, and city-only error models.

Training uses the legacy daily forecast-error cache strictly before the D-1
single-run test window.  Evaluation uses the same city/date/checkpoint/ladder
rows for all weather models and the normalized contemporaneous market ladder.
The historical cache is not represented as strict PIT training lineage; the
forward test forecast rows use the conservative single-run reconstruction.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.special import ndtr

from weather_data_feed.assigned_forecast_models import CITY_MODEL
from weather_dashboard.ingest.settlement_outcomes import normalize_final_price
from scripts.analysis.versioned_artifact_output import (
    prepare_new_run_output,
    resolve_run_output,
)


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FORECASTS = ROOT / "docs/analysis/2026-07/generated/d1_single_runs_backfill_v1/forecast_rows.csv"
DEFAULT_BASKETS = ROOT / "docs/analysis/2026-07/generated/d1_extreme_no_snapshot_history_v4/executable_baskets.csv"
DEFAULT_HISTORY = ROOT / "docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/daily_error_rows.csv"
DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_PM_HISTORY = ROOT / "runtime/weather_edge_v1/market_data/cache/pm_history"
OUTPUT_FAMILY = "d1_cross_city_hierarchy_v1"
DEFAULT_REPORT = ROOT / "docs/analysis/2026-08/2026-08-05-d1-cross-city-hierarchy-v1.md"

POLICIES = ("D-1_12_18_first", "D-1_18_24_first")
MODEL_KEY = {"gfs": "gfs_global", "ecmwf": "ecmwf_ifs025"}
PRIMARY_POLICY = "D-1_18_24_first"
HIERARCHY_LAMBDA = 60.0
FEE_RATE = 0.05
EPS = 1e-8


@dataclass(frozen=True)
class Bracket:
    label: str
    low: float | None
    high: float | None
    bottom: bool
    top: bool


def parse_bracket(label: str, question: str) -> Bracket:
    import re

    lab = str(label).replace("°C", "").replace("°F", "").replace("°", "").strip()
    nums = [float(x) for x in re.findall(r"(?<![\d.])-?\d+(?:\.\d+)?", lab)]
    if not nums:
        raise ValueError(f"unparseable bracket {label!r}")
    q = str(question or "").lower()
    bottom = "or below" in q or "or lower" in q
    top = lab.endswith("+") or "or higher" in q or "or above" in q
    if bottom:
        return Bracket(lab, None, nums[0], True, False)
    if top:
        return Bracket(lab, nums[0], None, False, True)
    if "-" in lab and len(nums) >= 2:
        return Bracket(lab, nums[0], nums[1], False, False)
    return Bracket(lab, nums[0], nums[0], False, False)


def sort_key(bracket: Bracket) -> float:
    if bracket.bottom:
        return -math.inf
    return float(bracket.low)


def weather_fee(price: float) -> float:
    return round(FEE_RATE * price * (1.0 - price), 5)


def bracket_probability(bracket: Bracket, mean: float, sigma: float) -> float:
    if bracket.bottom:
        return float(ndtr((float(bracket.high) + 0.5 - mean) / sigma))
    if bracket.top:
        return float(1.0 - ndtr((float(bracket.low) - 0.5 - mean) / sigma))
    lo = (float(bracket.low) - 0.5 - mean) / sigma
    hi = (float(bracket.high) + 0.5 - mean) / sigma
    return float(ndtr(hi) - ndtr(lo))


def validate_ladder(brackets: list[Bracket]) -> None:
    if len(brackets) < 3 or sum(x.bottom for x in brackets) != 1 or sum(x.top for x in brackets) != 1:
        raise ValueError("ladder does not have exactly one bottom and one top tail")
    for left, right in zip(brackets, brackets[1:]):
        if left.bottom:
            left_high = float(left.high)
        else:
            left_high = float(left.high)
        if right.top:
            right_low = float(right.low)
        else:
            right_low = float(right.low)
        if not math.isclose(right_low, left_high + 1.0, abs_tol=1e-9):
            raise ValueError(f"ladder gap/overlap: {left.label} -> {right.label}")


def load_settlements(
    db_path: Path,
    *,
    target_start: str | None = None,
    target_end: str | None = None,
    cities: list[str] | None = None,
) -> dict[tuple[str, str], str]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    clauses = ["settlement_status='settled'", "final_price > 0.999"]
    params: list[Any] = []
    if target_start is not None:
        clauses.append("target_date >= ?")
        params.append(target_start)
    if target_end is not None:
        clauses.append("target_date <= ?")
        params.append(target_end)
    if cities:
        clauses.append(f"city IN ({','.join('?' for _ in cities)})")
        params.extend(cities)
    frame = pd.read_sql_query(
        f"""
        SELECT city, target_date, bracket
        -- The external JRS volume has very poor random-read latency.  The
        -- date/city index requires one table lookup per matching rung and was
        -- materially slower than one sequential pass in the 2026-08-06 audit.
        FROM settlement_outcomes NOT INDEXED
        WHERE {' AND '.join(clauses)}
        """,
        conn,
        params=params,
    )
    conn.close()
    counts = frame.groupby(["city", "target_date"]).size()
    bad = counts[counts != 1]
    if len(bad):
        raise ValueError(f"non-unique winning settlements: {bad.head().to_dict()}")
    return {(r.city, str(r.target_date)): str(r.bracket) for r in frame.itertuples()}


def load_settlements_from_pm_history(
    root: Path,
    *,
    target_start: str,
    target_end: str,
    cities: list[str],
) -> dict[tuple[str, str], str]:
    settlements: dict[tuple[str, str], str] = {}
    city_set = set(cities)
    pattern = re.compile(r"^(.*)_(\d{4}-\d{2}-\d{2})\.json$")
    # One sequential directory scan is substantially faster on the external
    # APFS volume than thousands of random exists()/stat() calls.
    for entry in os.scandir(root):
        match = pattern.match(entry.name)
        if not match:
            continue
        city, target_date = match.groups()
        if city not in city_set or not (target_start <= target_date <= target_end):
            continue
        path = Path(entry.path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        winners = [
            str(row.get("label"))
            for row in (payload or {}).get("brackets", [])
            if normalize_final_price(row.get("final_price")) == 1.0
        ]
        if len(winners) != 1:
            continue
        settlements[(city, target_date)] = winners[0]
    return settlements


def model_assignments(
    history: pd.DataFrame,
    assignment_policy: str = "legacy_is_best_model",
) -> pd.DataFrame:
    if assignment_policy == "legacy_is_best_model":
        pairs = history.loc[history["is_best_model"], ["city", "model"]].drop_duplicates()
    elif assignment_policy == "authoritative_city_model":
        history_cities = set(history["city"].astype(str))
        pairs = pd.DataFrame(
            [
                {"city": city, "model": model}
                for city, model in CITY_MODEL.items()
                if city in history_cities
            ]
        )
        available = set(zip(history["city"].astype(str), history["model"].astype(str)))
        missing = [
            f"{row.city}:{row.model}"
            for row in pairs.itertuples(index=False)
            if (str(row.city), str(row.model)) not in available
        ]
        if missing:
            raise ValueError(f"authoritative city-model rows absent from history: {missing}")
    else:
        raise ValueError(f"unknown assignment policy: {assignment_policy}")
    multi = pairs.groupby("city").size()
    if (multi != 1).any():
        raise ValueError("historical best-model assignment is not unique by city")
    pairs["model_key"] = pairs["model"].map(MODEL_KEY)
    if pairs["model_key"].isna().any():
        raise ValueError("unknown historical model assignment")
    return pairs


def history_denominator_funnel(
    history: pd.DataFrame,
    test_start: str,
    *,
    months: tuple[int, ...] = (5, 6, 7, 8),
    input_artifact: Path = DEFAULT_HISTORY,
    assignments: pd.DataFrame | None = None,
    assignment_policy: str = "legacy_is_best_model",
) -> dict[str, Any]:
    """Describe the artifact and every filter leading to the training slice."""
    before_test = history.loc[history["date"] < test_start]
    if assignments is None:
        assigned = before_test.loc[before_test["is_best_model"]]
    else:
        assigned = before_test.merge(
            assignments[["city", "model"]],
            on=["city", "model"],
            how="inner",
            validate="many_to_one",
        )
    training_slice = assigned.loc[assigned["month_num"].isin(months)]

    def census(frame: pd.DataFrame) -> dict[str, Any]:
        return {
            "rows": int(len(frame)),
            "dates": int(frame["date"].nunique()),
            "cities": int(frame["city"].nunique()),
            "models": int(frame["model"].nunique()),
            "start": str(frame["date"].min()),
            "end": str(frame["date"].max()),
        }

    return {
        "denominator_scope": "legacy_daily_error_artifact_to_summer_best_model_training_slice"
        if assignment_policy == "legacy_is_best_model"
        else "legacy_daily_error_artifact_to_summer_authoritative_city_model_training_slice",
        "input_artifact": str(input_artifact.resolve().relative_to(ROOT.resolve()))
        if input_artifact.resolve().is_relative_to(ROOT.resolve())
        else str(input_artifact.resolve()),
        "artifact_input": census(history),
        "before_test_start": census(before_test),
        "best_model_only": census(assigned),
        "best_model_summer_training_slice": census(training_slice),
        "assignment_policy": assignment_policy,
        "filters": [
            f"date < {test_start}",
            "is_best_model = true"
            if assignment_policy == "legacy_is_best_model"
            else "city/model = weather_data_feed.assigned_forecast_models.CITY_MODEL",
            f"month in {list(months)}",
        ],
        "project_history_complete": False,
    }


def assigned_history_slice(
    history: pd.DataFrame,
    assignments: pd.DataFrame,
    test_start: str,
) -> pd.DataFrame:
    eligible = history.loc[
        (history["date"] < test_start) & history["month_num"].isin([5, 6, 7, 8])
    ].copy()
    return eligible.merge(
        assignments[["city", "model"]],
        on=["city", "model"],
        how="inner",
        validate="many_to_one",
    )


def fit_error_models(
    history: pd.DataFrame,
    test_start: str,
    assignments: pd.DataFrame | None = None,
) -> tuple[dict[str, Any], pd.DataFrame]:
    if assignments is None:
        assignments = model_assignments(history, "legacy_is_best_model")
    train = assigned_history_slice(history, assignments, test_start)
    if train.empty:
        raise ValueError("empty historical training frame")
    global_stats = train.groupby("model")["error_f_actual_minus_forecast"].agg(["count", "mean", "std"])
    city_stats = train.groupby(["city", "model"])["error_f_actual_minus_forecast"].agg(["count", "mean", "std"])
    specs: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for (city, model), stat in city_stats.iterrows():
        glob = global_stats.loc[model]
        n = float(stat["count"])
        weight = n / (n + HIERARCHY_LAMBDA)
        city_mean = float(stat["mean"])
        city_var = float(stat["std"]) ** 2
        global_mean = float(glob["mean"])
        global_var = float(glob["std"]) ** 2
        hier_mean = weight * city_mean + (1.0 - weight) * global_mean
        hier_var = (
            weight * (city_var + (city_mean - hier_mean) ** 2)
            + (1.0 - weight) * (global_var + (global_mean - hier_mean) ** 2)
        )
        specs[city] = {
            "model": model,
            "pooled": (global_mean, math.sqrt(global_var)),
            "hierarchical": (hier_mean, math.sqrt(hier_var)),
            "city_only": (city_mean, math.sqrt(city_var)),
        }
        rows.append(
            {
                "city": city,
                "model": model,
                "train_rows": int(n),
                "pooled_mean_error_f": global_mean,
                "pooled_sd_error_f": math.sqrt(global_var),
                "city_mean_error_f": city_mean,
                "city_sd_error_f": math.sqrt(city_var),
                "hierarchy_weight_city": weight,
                "hierarchy_mean_error_f": hier_mean,
                "hierarchy_sd_error_f": math.sqrt(hier_var),
            }
        )
    return specs, pd.DataFrame(rows)


def get_snapshot_records(path: str, city: str, target_date: str, cache: dict[str, Any]) -> list[dict[str, Any]]:
    if path not in cache:
        # Snapshot payloads can be several MB.  build_states sorts by source_path,
        # so retaining only the active payload avoids multi-GB growth on a full
        # May--July replay without causing repeat reads.
        cache.clear()
        cache[path] = json.loads(Path(path).read_text(encoding="utf-8"))["records"]
    rows = [r for r in cache[path] if r.get("city") == city and str(r.get("event_date")) == target_date]
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        label = str(row.get("bracket"))
        if label in unique:
            raise ValueError(f"duplicate bracket in snapshot {path}: {city} {target_date} {label}")
        unique[label] = row
    return list(unique.values())


def build_states(
    forecasts: pd.DataFrame,
    baskets: pd.DataFrame,
    assignments: pd.DataFrame,
    settlements: dict[tuple[str, str], str],
    evidence_blockers: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    baskets = baskets.loc[baskets["snapshot_key"].isin(forecasts["snapshot_key"]) & baskets["policy"].isin(POLICIES)].copy()
    joined = baskets.merge(assignments, on="city", how="inner", validate="many_to_one")
    chosen = forecasts[["snapshot_key", "model_key", "forecast_max_f", "decision_time_utc", "lineage_status"]].rename(
        columns={"forecast_max_f": "single_run_forecast_max_f"}
    )
    joined = joined.merge(chosen, on=["snapshot_key", "model_key"], how="inner", validate="one_to_one")
    joined = joined.sort_values(["source_path", "city", "target_date", "policy"])
    cache: dict[str, Any] = {}
    states: list[dict[str, Any]] = []
    counters = {
        "forecast_snapshots": int(forecasts["snapshot_key"].nunique()),
        "basket_snapshots": int(len(baskets)),
        "assigned_model_snapshots": int(len(joined)),
        "missing_settlement": 0,
        "invalid_ladder": 0,
        "duplicate_bracket": 0,
        "ladder_parse_error": 0,
        "ladder_structure_error": 0,
        "winner_not_in_ladder": 0,
        "missing_market_mid": 0,
        "missing_ask": 0,
        "scoreable_states": 0,
    }

    def block(row: Any, reason: str, detail: str | None = None) -> None:
        if evidence_blockers is None:
            return
        evidence_blockers.append(
            {
                "snapshot_key": str(row.snapshot_key),
                "source_path": str(row.source_path),
                "city": str(row.city),
                "target_date": str(row.target_date),
                "policy": str(row.policy),
                "reason": reason,
                "detail": detail,
            }
        )

    for row in joined.itertuples(index=False):
        key = (row.city, str(row.target_date))
        winner = settlements.get(key)
        if winner is None:
            counters["missing_settlement"] += 1
            block(row, "missing_settlement")
            continue
        try:
            records = get_snapshot_records(row.source_path, row.city, str(row.target_date), cache)
        except ValueError as exc:
            counters["invalid_ladder"] += 1
            counters["duplicate_bracket"] += 1
            block(row, "duplicate_bracket", str(exc))
            continue
        try:
            parsed = [(parse_bracket(r["bracket"], r.get("question", "")), r) for r in records]
            parsed.sort(key=lambda item: sort_key(item[0]))
            brackets = [item[0] for item in parsed]
        except (KeyError, TypeError, ValueError) as exc:
            counters["invalid_ladder"] += 1
            counters["ladder_parse_error"] += 1
            block(row, "ladder_parse_error", str(exc))
            continue
        try:
            validate_ladder(brackets)
        except (TypeError, ValueError) as exc:
            counters["invalid_ladder"] += 1
            counters["ladder_structure_error"] += 1
            block(row, "ladder_structure_error", str(exc))
            continue
        labels = [x.label for x in brackets]
        winner_norm = str(winner).replace("°C", "").replace("°F", "").replace("°", "").strip()
        if winner_norm not in labels:
            counters["invalid_ladder"] += 1
            counters["winner_not_in_ladder"] += 1
            block(row, "winner_not_in_ladder", f"winner={winner_norm}")
            continue
        mids: list[float] = []
        asks: list[float] = []
        ask_sizes: list[float] = []
        market_ok = True
        ask_ok = True
        for _bracket, record in parsed:
            bid = pd.to_numeric(record.get("yes_best_bid"), errors="coerce")
            ask = pd.to_numeric(record.get("yes_best_ask"), errors="coerce")
            fallback = pd.to_numeric(record.get("market_yes_price"), errors="coerce")
            if np.isfinite(bid) and np.isfinite(ask) and 0 <= bid <= ask <= 1:
                mid = (float(bid) + float(ask)) / 2.0
            elif np.isfinite(fallback) and 0 <= fallback <= 1:
                mid = float(fallback)
            else:
                market_ok = False
                mid = math.nan
            if not np.isfinite(ask) or not (0 < ask < 1):
                ask_ok = False
            mids.append(mid)
            asks.append(float(ask) if np.isfinite(ask) else math.nan)
            size = pd.to_numeric(record.get("yes_ask_size"), errors="coerce")
            ask_sizes.append(float(size) if np.isfinite(size) else math.nan)
        if not market_ok or sum(mids) <= 0:
            counters["missing_market_mid"] += 1
            block(row, "missing_market_mid")
            continue
        if not ask_ok:
            counters["missing_ask"] += 1
            block(row, "missing_ask_execution_only")
        market_probs = np.asarray(mids, dtype=float)
        market_probs /= market_probs.sum()
        states.append(
            {
                "snapshot_key": row.snapshot_key,
                "city": row.city,
                "target_date": str(row.target_date),
                "policy": row.policy,
                "decision_ts_utc": row.decision_ts_utc,
                "decision_time_utc": row.decision_time_utc,
                "market_unit": row.market_unit,
                "model": row.model,
                "model_key": row.model_key,
                "forecast_max_f": float(row.single_run_forecast_max_f),
                "forecast_lineage_status": row.lineage_status,
                "brackets": brackets,
                "labels": labels,
                "winner_index": labels.index(winner_norm),
                "market_probs": market_probs,
                "asks": np.asarray(asks, dtype=float),
                "ask_sizes": np.asarray(ask_sizes, dtype=float),
            }
        )
    counters["scoreable_states"] = len(states)
    return states, counters


def probability_vector(state: dict[str, Any], error_mean_f: float, error_sd_f: float) -> np.ndarray:
    mean_f = state["forecast_max_f"] + error_mean_f
    if state["market_unit"] == "C":
        mean = (mean_f - 32.0) * 5.0 / 9.0
        sigma = error_sd_f * 5.0 / 9.0
    else:
        mean = mean_f
        sigma = error_sd_f
    values = np.asarray([bracket_probability(b, mean, sigma) for b in state["brackets"]], dtype=float)
    values = np.clip(values, EPS, None)
    values /= values.sum()
    return values


def score_vector(probabilities: np.ndarray, winner: int) -> tuple[float, float, float, float, int]:
    one_hot = np.zeros(len(probabilities), dtype=float)
    one_hot[winner] = 1.0
    logloss = -math.log(max(float(probabilities[winner]), EPS))
    brier = float(np.mean((probabilities - one_hot) ** 2))
    rps = float(np.mean((np.cumsum(probabilities)[:-1] - np.cumsum(one_hot)[:-1]) ** 2))
    return logloss, brier, rps, float(probabilities[winner]), int(np.argmax(probabilities) == winner)


def score_states(states: list[dict[str, Any]], specs: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for state in states:
        vectors = {"market": state["market_probs"]}
        for arm in ("pooled", "hierarchical", "city_only"):
            mean, sigma = specs[state["city"]][arm]
            vectors[arm] = probability_vector(state, mean, sigma)
        for arm, vector in vectors.items():
            logloss, brier, rps, winner_probability, top1 = score_vector(vector, state["winner_index"])
            asks = state["asks"]
            fees = np.asarray([weather_fee(x) if np.isfinite(x) else math.nan for x in asks])
            net_edges = vector - asks - fees
            selected = int(np.nanargmax(net_edges)) if np.isfinite(net_edges).any() else -1
            selected_edge = float(net_edges[selected]) if selected >= 0 else math.nan
            selected_cost = float(asks[selected] + fees[selected]) if selected >= 0 else math.nan
            selected_win = int(selected == state["winner_index"]) if selected >= 0 else 0
            selected_pnl = float(selected_win - selected_cost) if selected >= 0 else math.nan
            rows.append(
                {
                    "snapshot_key": state["snapshot_key"],
                    "city": state["city"],
                    "target_date": state["target_date"],
                    "policy": state["policy"],
                    "decision_ts_utc": state["decision_ts_utc"],
                    "market_unit": state["market_unit"],
                    "forecast_model": state["model"],
                    "forecast_max_f": state["forecast_max_f"],
                    "arm": arm,
                    "rung_count": len(vector),
                    "winner_bracket": state["labels"][state["winner_index"]],
                    "top1_bracket": state["labels"][int(np.argmax(vector))],
                    "logloss": logloss,
                    "brier": brier,
                    "rps": rps,
                    "winner_probability": winner_probability,
                    "top1_accuracy": top1,
                    "probability_sum": float(vector.sum()),
                    "probabilities_json": json.dumps(dict(zip(state["labels"], vector.round(10))), sort_keys=False),
                    "selected_bracket": state["labels"][selected] if selected >= 0 else None,
                    "selected_net_edge": selected_edge,
                    "selected_cost": selected_cost,
                    "selected_win": selected_win,
                    "selected_pnl": selected_pnl,
                    "selected_ask_size": float(state["ask_sizes"][selected]) if selected >= 0 else math.nan,
                }
            )
    return pd.DataFrame(rows)


def date_equal_summary(scored: pd.DataFrame) -> pd.DataFrame:
    metrics = ["logloss", "brier", "rps", "winner_probability", "top1_accuracy"]
    daily = scored.groupby(["policy", "arm", "target_date"], as_index=False)[metrics].mean()
    result = daily.groupby(["policy", "arm"], as_index=False)[metrics].mean()
    counts = scored.groupby(["policy", "arm"]).agg(states=("snapshot_key", "size"), cities=("city", "nunique"), dates=("target_date", "nunique")).reset_index()
    return result.merge(counts, on=["policy", "arm"], validate="one_to_one")


def bootstrap_delta(scored: pd.DataFrame, policy: str, left: str, right: str, metric: str, seed: int = 20260805) -> dict[str, Any]:
    frame = scored.loc[(scored["policy"] == policy) & scored["arm"].isin([left, right])]
    daily = frame.groupby(["target_date", "arm"])[metric].mean().unstack("arm").dropna()
    deltas = (daily[left] - daily[right]).to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    draws = rng.choice(deltas, size=(20000, len(deltas)), replace=True).mean(axis=1)
    return {
        "policy": policy,
        "left": left,
        "right": right,
        "metric": metric,
        "dates": int(len(deltas)),
        "delta": float(deltas.mean()),
        "ci_low": float(np.quantile(draws, 0.025)),
        "ci_high": float(np.quantile(draws, 0.975)),
    }


def trade_summary(scored: pd.DataFrame) -> pd.DataFrame:
    weather = scored.loc[(scored["arm"] != "market") & (scored["selected_net_edge"] > 0)].copy()
    weather["executable_one_share"] = weather["selected_ask_size"] >= 1.0
    weather = weather.loc[weather["executable_one_share"]]
    if weather.empty:
        return pd.DataFrame(columns=["policy", "arm", "tickets", "dates", "cost", "pnl", "roi", "win_rate", "median_edge"])
    grouped = weather.groupby(["policy", "arm"])
    result = grouped.agg(
        tickets=("snapshot_key", "size"),
        dates=("target_date", "nunique"),
        cost=("selected_cost", "sum"),
        pnl=("selected_pnl", "sum"),
        win_rate=("selected_win", "mean"),
        median_edge=("selected_net_edge", "median"),
    ).reset_index()
    result["roi"] = result["pnl"] / result["cost"]
    return result


def city_summary(scored: pd.DataFrame, policy: str = PRIMARY_POLICY) -> pd.DataFrame:
    pivot = scored.loc[scored["policy"] == policy].pivot_table(index="city", columns="arm", values=["logloss", "brier", "rps", "top1_accuracy"], aggfunc="mean")
    pivot.columns = [f"{metric}_{arm}" for metric, arm in pivot.columns]
    pivot = pivot.reset_index()
    for metric in ("logloss", "brier", "rps"):
        pivot[f"hier_minus_pooled_{metric}"] = pivot[f"{metric}_hierarchical"] - pivot[f"{metric}_pooled"]
        pivot[f"hier_minus_city_{metric}"] = pivot[f"{metric}_hierarchical"] - pivot[f"{metric}_city_only"]
        pivot[f"hier_minus_market_{metric}"] = pivot[f"{metric}_hierarchical"] - pivot[f"{metric}_market"]
    counts = scored.loc[(scored["policy"] == policy) & (scored["arm"] == "hierarchical")].groupby("city").agg(states=("snapshot_key", "size"), dates=("target_date", "nunique")).reset_index()
    return pivot.merge(counts, on="city", validate="one_to_one").sort_values("hier_minus_pooled_logloss")


def fmt(value: float, digits: int = 4) -> str:
    return f"{value:.{digits}f}"


def render_report(summary: dict[str, Any], score_table: pd.DataFrame, delta_table: pd.DataFrame, trades: pd.DataFrame, city: pd.DataFrame) -> str:
    primary = score_table.loc[score_table["policy"] == PRIMARY_POLICY].set_index("arm")
    d = delta_table.loc[(delta_table["policy"] == PRIMARY_POLICY) & (delta_table["metric"] == "logloss")]
    trade_primary = trades.loc[trades["policy"] == PRIMARY_POLICY].set_index("arm") if len(trades) else pd.DataFrame()
    best_cities = city.head(8)[["city", "states", "hier_minus_pooled_logloss", "hier_minus_market_logloss"]]
    worst_cities = city.tail(8).sort_values("hier_minus_pooled_logloss", ascending=False)[["city", "states", "hier_minus_pooled_logloss", "hier_minus_market_logloss"]]
    lines = [
        "# D-1 跨城市 pooled / hierarchical / city-only 概率研究 v1",
        "",
        "## 结论",
        "",
    ]
    h = primary.loc["hierarchical"]
    p = primary.loc["pooled"]
    c = primary.loc["city_only"]
    m = primary.loc["market"]
    hp = d.loc[(d["left"] == "hierarchical") & (d["right"] == "pooled")].iloc[0]
    hc = d.loc[(d["left"] == "hierarchical") & (d["right"] == "city_only")].iloc[0]
    hm = d.loc[(d["left"] == "hierarchical") & (d["right"] == "market")].iloc[0]
    lines += [
        f"主口径 `{PRIMARY_POLICY}` 有 **{int(h.states)} 个 city-date checkpoint / {int(h.dates)} 个 target dates / {int(h.cities)} 城**。",
        f"分层模型 logloss={fmt(h.logloss)}，相对 pooled 的差值为 {fmt(hp.delta)}（95% date-block CI {fmt(hp.ci_low)} 到 {fmt(hp.ci_high)}），相对 city-only 为 {fmt(hc.delta)}（{fmt(hc.ci_low)} 到 {fmt(hc.ci_high)}）。负值才是改善。",
        f"但分层模型仍明显输给同时间 market：logloss 差 {fmt(hm.delta)}（{fmt(hm.ci_low)} 到 {fmt(hm.ci_high)}）；market={fmt(m.logloss)}，hierarchical={fmt(h.logloss)}。",
        "因此这版的架构结论不是直接采用 hierarchical：**pooled 是当前最稳的 weather-only baseline；city-only 明显过拟合；hierarchical 虽缓解 city-only，但 logloss 仍显著差于 pooled。** 分层结构只保留为 challenger，等有严格 D-1/D-2 城市样本后再验证。当前纯天气分布不能直接做交易概率源，更不能升 live。",
        "",
        "## 同分母准确度",
        "",
        "| arm | logloss | Brier | RPS | winner P | top-1 accuracy |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for arm in ("market", "pooled", "hierarchical", "city_only"):
        r = primary.loc[arm]
        lines.append(f"| {arm} | {fmt(r.logloss)} | {fmt(r.brier)} | {fmt(r.rps)} | {fmt(r.winner_probability)} | {r.top1_accuracy:.1%} |")
    lines += [
        "",
        "评分先在每个 target_date 内平均，再对日期等权；Brier/RPS 采用每 ladder rung 的均值。market 为同 checkpoint bid/ask mid（缺一边时用 snapshot market price）后整条 ladder 归一化。",
        "",
        "## 交易效果（只作诊断）",
        "",
        "固定表达为：每个 checkpoint 只取 `P(model)-YES ask-official weather fee` 最大且大于 0、top ask size≥1 的一张 YES，立即 taker，持有到结算。没有阈值搜索，也没有 maker 成交假设。",
        "",
        "| arm | tickets | dates | cost | PnL | ROI | win rate | median edge |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    if len(trade_primary):
        for arm in ("pooled", "hierarchical", "city_only"):
            if arm not in trade_primary.index:
                continue
            r = trade_primary.loc[arm]
            lines.append(f"| {arm} | {int(r.tickets)} | {int(r.dates)} | {r.cost:.2f} | {r.pnl:.2f} | {r.roi:.1%} | {r.win_rate:.1%} | {r.median_edge:.3f} |")
    else:
        lines.append("| — | 0 | 0 | 0 | 0 | — | — | — |")
    lines += [
        "",
        "这不是可部署回测：概率训练来自旧 daily cache（非严格 first-seen PIT），测试 forecast 是 conservative single-run reconstruction，且只有 27 个日期；交易数还由模型 edge 自选，不能用正 ROI 反推 alpha。",
        "",
        "## 城市异质性（主 checkpoint）",
        "",
        "分层相对 pooled 最好的城市：",
        "",
        "| city | states | Δ logloss vs pooled | Δ logloss vs market |",
        "|---|---:|---:|---:|",
    ]
    for r in best_cities.itertuples(index=False):
        lines.append(f"| {r.city} | {int(r.states)} | {fmt(r.hier_minus_pooled_logloss)} | {fmt(r.hier_minus_market_logloss)} |")
    lines += ["", "分层相对 pooled 最差的城市：", "", "| city | states | Δ logloss vs pooled | Δ logloss vs market |", "|---|---:|---:|---:|"]
    for r in worst_cities.itertuples(index=False):
        lines.append(f"| {r.city} | {int(r.states)} | {fmt(r.hier_minus_pooled_logloss)} | {fmt(r.hier_minus_market_logloss)} |")
    lines += [
        "",
        "单城只有约二十几个 forward 日期，城市表只能用于找偏移方向，不能逐城挑赢家后上线。",
        "",
        "## 数据与模型合同",
        "",
        f"- 训练：{summary['training']['rows']} rows，{summary['training']['cities']} 城，{summary['training']['start']} 至 {summary['training']['end']}，只用 5–8 月；截止早于测试。",
        f"- 测试：{summary['test']['scoreable_states']} checkpoints；forecast lineage=`single_run_reconstructed_conservative_12h_lag`；日期 {summary['test']['start']} 至 {summary['test']['end']}。",
        f"- pooled：按固定 forecast source（GFS/ECMWF）共享 Normal error distribution；city-only：单城 source-specific error distribution；hierarchical：`w=n/(n+60)` 的分布矩收缩。",
        f"- 公共同分母只保留旧 registry 已有固定模型选择的 {summary['test']['cities']} 城；严格 D-1 原始 47 城中的其余 {summary['funnels']['unassigned_cities']} 城记 coverage gap。",
        "- D-2：没有同等级 single-run PIT + full-ladder checkpoint 数据，本报告不输出 D-2 数字。",
        "",
        "## 双漏斗",
        "",
        f"- signal funnel：strict forecast snapshots {summary['funnels']['forecast_snapshots']} → fixed-model assigned {summary['funnels']['assigned_model_snapshots']}。",
        f"- evidence funnel：assigned {summary['funnels']['assigned_model_snapshots']} → settlement/l完整 ladder/market 同时可评分 {summary['funnels']['scoreable_states']}；missing settlement={summary['funnels']['missing_settlement']}，invalid ladder={summary['funnels']['invalid_ladder']}，missing market={summary['funnels']['missing_market_mid']}。",
        "- actual fills：0；本研究未更改 production / shadow / order。",
        "",
        "## 下一步",
        "",
        "不冻结任何 probability artifact。pooled 只保留为 temporary reference，hierarchical/city-only 都是失败诊断。下一版不在这 27 天上继续调 shrinkage λ，而是先补干净的 D-1/D-2 forecast run archive；随后以 pooled residual shape 为底，只对 city×source mean bias 和必要的 variance 做收缩，再加入 lead/run-age、calendar season、multi-model mean/spread，训练 `weather-only` 和 `market + weather residual` 两个头。只有候选先接近或打赢同 rows market，才预注册新的 frozen forward。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forecasts", type=Path, default=DEFAULT_FORECASTS)
    parser.add_argument("--baskets", type=Path, default=DEFAULT_BASKETS)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--pm-history-dir", type=Path, default=DEFAULT_PM_HISTORY)
    parser.add_argument(
        "--settlement-source",
        choices=("canonical_db", "pm_history_raw"),
        default="canonical_db",
    )
    parser.add_argument("--run-id")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--assignment-policy",
        choices=("authoritative_city_model", "legacy_is_best_model"),
        default="authoritative_city_model",
        help="City/model authority. Legacy mode is retained only for exact reproduction of old artifacts.",
    )
    args = parser.parse_args(argv)
    try:
        output_dir = resolve_run_output(
            OUTPUT_FAMILY,
            run_id=args.run_id,
            explicit_output=args.out,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    forecasts = pd.read_csv(args.forecasts, dtype={"target_date": str})
    baskets = pd.read_csv(args.baskets, dtype={"target_date": str})
    history = pd.read_csv(args.history, dtype={"date": str})
    history["is_best_model"] = history["is_best_model"].astype(str).str.lower().isin(["true", "1"])
    history["month_num"] = pd.to_datetime(history["date"]).dt.month
    assignments = model_assignments(history, args.assignment_policy)
    test_start = str(forecasts["target_date"].min())
    specs, fitted = fit_error_models(history, test_start, assignments)
    settlement_start = str(forecasts["target_date"].min())
    settlement_end = str(forecasts["target_date"].max())
    settlement_cities = sorted(forecasts["city"].astype(str).unique())
    if args.settlement_source == "pm_history_raw":
        settlements = load_settlements_from_pm_history(
            args.pm_history_dir,
            target_start=settlement_start,
            target_end=settlement_end,
            cities=settlement_cities,
        )
    else:
        settlements = load_settlements(
            args.db,
            target_start=settlement_start,
            target_end=settlement_end,
            cities=settlement_cities,
        )
    evidence_blockers: list[dict[str, Any]] = []
    states, funnels = build_states(
        forecasts,
        baskets,
        assignments,
        settlements,
        evidence_blockers=evidence_blockers,
    )
    scored = score_states(states, specs)
    score_table = date_equal_summary(scored)
    deltas = []
    for policy in POLICIES:
        for metric in ("logloss", "brier", "rps"):
            for left, right in (
                ("hierarchical", "pooled"),
                ("hierarchical", "city_only"),
                ("hierarchical", "market"),
                ("pooled", "market"),
                ("city_only", "market"),
            ):
                deltas.append(bootstrap_delta(scored, policy, left, right, metric))
    delta_table = pd.DataFrame(deltas)
    trades = trade_summary(scored)
    cities = city_summary(scored)
    train_used = assigned_history_slice(history, assignments, test_start)
    strict_cities = set(forecasts["city"].unique())
    assigned_cities = set(assignments["city"]) & strict_cities
    summary = {
        "schema_version": "d1_cross_city_hierarchy_research_v1",
        "primary_policy": PRIMARY_POLICY,
        "training": {
            "lineage": "legacy_daily_cache_non_strict_pit_training_prior",
            "denominator": history_denominator_funnel(
                history,
                test_start,
                input_artifact=args.history,
                assignments=assignments,
                assignment_policy=args.assignment_policy,
            ),
            "rows": int(len(train_used)),
            "cities": int(train_used["city"].nunique()),
            "start": str(train_used["date"].min()),
            "end": str(train_used["date"].max()),
            "months": [5, 6, 7, 8],
        },
        "test": {
            "lineage": sorted(forecasts["lineage_status"].dropna().unique().tolist()),
            "settlement_source": args.settlement_source,
            "scoreable_states": int(len(states)),
            "cities": int(len({x["city"] for x in states})),
            "start": min(x["target_date"] for x in states),
            "end": max(x["target_date"] for x in states),
        },
        "model": {
            "error_family": "Normal(actual_max_f - forecast_max_f)",
            "hierarchy_lambda": HIERARCHY_LAMBDA,
            "fee_rate": FEE_RATE,
            "assignment_policy": args.assignment_policy,
        },
        "funnels": {
            **funnels,
            "strict_cities": len(strict_cities),
            "assigned_cities": len(assigned_cities),
            "unassigned_cities": len(strict_cities - assigned_cities),
        },
        "score_summary": score_table.to_dict("records"),
        "paired_deltas": delta_table.to_dict("records"),
        "trade_diagnostic": trades.to_dict("records"),
    }

    prepare_new_run_output(output_dir)
    scored.to_csv(output_dir / "scored_states.csv", index=False)
    pd.DataFrame(evidence_blockers).to_csv(
        output_dir / "evidence_blockers.csv", index=False
    )
    fitted.to_csv(output_dir / "fitted_city_error_models.csv", index=False)
    score_table.to_csv(output_dir / "score_summary.csv", index=False)
    delta_table.to_csv(output_dir / "paired_date_bootstrap_deltas.csv", index=False)
    trades.to_csv(output_dir / "trade_diagnostic.csv", index=False)
    cities.to_csv(output_dir / "city_summary_primary_policy.csv", index=False)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_report(summary, score_table, delta_table, trades, cities), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
