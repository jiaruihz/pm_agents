#!/usr/bin/env python3
"""Audit current-YES carry mechanisms, timing, and backtest/live parity.

This is deliberately a research audit, not a new selector.  It uses the
regime-routed carry universe, reduces every executable policy to the first
city-day entry, charges the official Weather taker fee, and keeps evidence
coverage separate from signal rejection.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import glob
import gzip
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.analysis.reheat_risk.research_regime_routed_carry_v1 import (  # noqa: E402
    CONTAMINATED,
    SHARD_GLOB,
    load,
)


OUT_DIR = ROOT / "docs/analysis/2026-07/generated/current_yes_carry_mechanism_timing_audit_v1"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-22-current-yes-carry-mechanism-timing-audit-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-22-current-yes-carry-mechanism-timing-audit-v1.md"
H1_EXECUTION_SUMMARY = ROOT / "docs/analysis/2026-07/generated/h1_late_carry_maker_v1/summary.json"
V31_JSON = ROOT / "docs/analysis/2026-06/generated/current_yes_future_break_hazard_v31/summary.json"

SEED = 20260722
MIN_TRAIN_DATES = 14
BOOTSTRAP_REPS = 5000


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
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def run_clob_fill_coverage_gate() -> dict[str, Any]:
    gate_script = ROOT / "scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py"
    result = subprocess.run(
        [sys.executable, str(gate_script)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"CLOB fill coverage gate failed with exit={result.returncode}: {result.stderr.strip()}"
        )
    payload = json.loads(result.stdout)
    if not payload.get("gate_pass"):
        raise RuntimeError(f"CLOB fill coverage gate did not pass: {payload.get('fail_reasons')}")
    return payload


def first_city_day(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    return (
        frame.sort_values(["target_date", "city", "decision_snapshot_dt", "decision_hour_local"])
        .drop_duplicates(["city", "target_date"], keep="first")
        .copy()
    )


def outcome_forecast_divergence() -> dict[str, Any]:
    """Measure why an explicit current-YES source row is required."""
    columns = [
        "city", "target_date", "decision_hour_local", "bracket", "current_bracket", "outcome",
        "forecast_max_native", "forecast_peak_delta_hours_local", "decision_snapshot_ts_utc",
        "current_yes_ask",
    ]
    frames: list[pd.DataFrame] = []
    for path in sorted(glob.glob(SHARD_GLOB)):
        available = set(pd.read_csv(path, nrows=0).columns)
        frame = pd.read_csv(path, usecols=[column for column in columns if column in available], low_memory=False)
        frame["target_date"] = frame["target_date"].astype(str)
        frames.append(
            frame[
                frame["bracket"].eq(frame["current_bracket"])
                & ~frame["target_date"].isin(CONTAMINATED)
            ]
        )
    rows = pd.concat(frames, ignore_index=True)
    keys = ["city", "target_date", "decision_hour_local"]
    result: dict[str, Any] = {
        "current_bracket_expression_rows": int(len(rows)),
        "city_day_hour_keys": int(rows.groupby(keys).ngroups),
    }
    for column in ["forecast_max_native", "forecast_peak_delta_hours_local", "decision_snapshot_ts_utc", "current_yes_ask"]:
        paired = rows.pivot_table(index=keys, columns="outcome", values=column, aggfunc="first")
        if not {"yes", "no"}.issubset(paired.columns):
            result[f"{column}_paired"] = 0
            result[f"{column}_different"] = 0
            continue
        paired = paired.dropna(subset=["yes", "no"])
        result[f"{column}_paired"] = int(len(paired))
        result[f"{column}_different"] = int(paired["yes"].ne(paired["no"]).sum())
    return result


def loader_semantics_impact(legacy: pd.DataFrame, clean: pd.DataFrame) -> tuple[dict[str, Any], pd.DataFrame]:
    legacy_first = first_city_day(legacy[legacy["route"]]).copy()
    clean_first = first_city_day(clean[clean["route"]]).copy()
    key = ["city", "target_date"]
    keep = key + ["decision_hour_local", "decision_snapshot_ts_utc", "current_yes_ask", "label"]
    joined = legacy_first[keep].merge(
        clean_first[keep], on=key, how="outer", suffixes=("_legacy", "_clean"), indicator=True
    )
    joined["membership"] = joined["_merge"].map(
        {"left_only": "legacy_only", "right_only": "clean_only", "both": "both"}
    ).astype(str)
    joined = joined.drop(columns=["_merge"]).sort_values(["target_date", "city"]).reset_index(drop=True)
    summary = {
        "legacy_first_city_days": int(len(legacy_first)),
        "clean_first_city_days": int(len(clean_first)),
        "overlap_city_days": int(joined["membership"].eq("both").sum()),
        "legacy_only_city_days": int(joined["membership"].eq("legacy_only").sum()),
        "clean_only_city_days": int(joined["membership"].eq("clean_only").sum()),
        "changed_union_share": float(joined["membership"].ne("both").mean()) if len(joined) else None,
    }
    return summary, joined


def roi(frame: pd.DataFrame) -> float | None:
    cost = float(frame["taker_cost"].sum())
    return float((frame["label"] - frame["taker_cost"]).sum() / cost) if cost > 0 else None


def roi_ci(frame: pd.DataFrame, reps: int = BOOTSTRAP_REPS) -> list[float | None]:
    if frame.empty or frame["target_date"].nunique() < 2:
        return [None, None]
    daily = frame.groupby("target_date").apply(
        lambda rows: pd.Series(
            {
                "pnl": float((rows["label"] - rows["taker_cost"]).sum()),
                "cost": float(rows["taker_cost"].sum()),
            }
        ),
        include_groups=False,
    )
    values = daily[["pnl", "cost"]].to_numpy(float)
    rng = np.random.default_rng(SEED)
    draws: list[float] = []
    for _ in range(reps):
        sample = values[rng.integers(0, len(values), len(values))]
        cost = float(sample[:, 1].sum())
        if cost > 0:
            draws.append(float(sample[:, 0].sum() / cost))
    return [float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))]


def selector_metrics(frame: pd.DataFrame, selector: str) -> dict[str, Any]:
    sample = first_city_day(frame)
    return {
        "selector": selector,
        "city_days": int(len(sample)),
        "dates": int(sample["target_date"].nunique()),
        "cities": int(sample["city"].nunique()),
        "wins": int(sample["label"].sum()) if len(sample) else 0,
        "losses": int(sample["label"].eq(0).sum()) if len(sample) else 0,
        "win_rate": float(sample["label"].mean()) if len(sample) else None,
        "avg_ask": float(sample["current_yes_ask"].mean()) if len(sample) else None,
        "avg_effective_cost": float(sample["taker_cost"].mean()) if len(sample) else None,
        "fee_adjusted_taker_roi": roi(sample),
        "target_date_block_ci95": roi_ci(sample),
        "top_ask_size_ge_5_share": float(sample["current_yes_ask_size"].ge(5).mean()) if len(sample) else None,
        "top_ask_size_ge_10_share": float(sample["current_yes_ask_size"].ge(10).mean()) if len(sample) else None,
    }


def paired_roi_delta(immediate: pd.DataFrame, delayed: pd.DataFrame) -> dict[str, Any]:
    left = immediate.set_index(["city", "target_date"])
    right = delayed.set_index(["city", "target_date"])
    common = left.index.intersection(right.index)
    if len(common) == 0:
        return {"paired_city_days": 0}
    a = left.loc[common].copy()
    b = right.loc[common].copy()
    if isinstance(a, pd.Series):
        a = a.to_frame().T
        b = b.to_frame().T
    dates = np.array(sorted({str(index[1]) for index in common}))
    daily_a = a.assign(pnl=a["label"] - a["taker_cost"]).groupby(level="target_date")[["pnl", "taker_cost"]].sum()
    daily_b = b.assign(pnl=b["label"] - b["taker_cost"]).groupby(level="target_date")[["pnl", "taker_cost"]].sum()
    daily_a = daily_a.reindex(dates)
    daily_b = daily_b.reindex(dates)
    values_a = daily_a[["pnl", "taker_cost"]].to_numpy(float)
    values_b = daily_b[["pnl", "taker_cost"]].to_numpy(float)
    rng = np.random.default_rng(SEED)
    draws: list[float] = []
    for _ in range(BOOTSTRAP_REPS):
        chosen = rng.integers(0, len(dates), size=len(dates))
        cost_a = float(values_a[chosen, 1].sum())
        cost_b = float(values_b[chosen, 1].sum())
        if cost_a > 0 and cost_b > 0:
            draws.append(float(values_b[chosen, 0].sum() / cost_b - values_a[chosen, 0].sum() / cost_a))
    delta = None if roi(a) is None or roi(b) is None else roi(b) - roi(a)
    return {
        "paired_city_days": int(len(common)),
        "paired_dates": int(len(dates)),
        "same_bracket_rate": float(
            (a["current_bracket"].astype(str).to_numpy() == b["current_bracket"].astype(str).to_numpy()).mean()
        ),
        "bracket_changed_rate": float(
            (a["current_bracket"].astype(str).to_numpy() != b["current_bracket"].astype(str).to_numpy()).mean()
        ),
        "avg_delay_hours": float(
            (b["decision_snapshot_dt"].to_numpy() - a["decision_snapshot_dt"].to_numpy())
            .astype("timedelta64[s]").astype(float).mean() / 3600.0
        ),
        "avg_ask_delta_delayed_minus_immediate": float(
            b["current_yes_ask"].to_numpy(float).mean() - a["current_yes_ask"].to_numpy(float).mean()
        ),
        "immediate_roi_on_common": roi(a),
        "delayed_roi_on_common": roi(b),
        "delayed_minus_immediate_roi": delta,
        "delta_ci95": [float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))]
        if draws else [None, None],
    }


def timing_policies(universe: pd.DataFrame) -> tuple[list[dict[str, Any]], dict[str, pd.DataFrame]]:
    route_rows = universe[universe["cc_route_valid"]].copy()
    immediate = first_city_day(route_rows)
    groups = {
        key: rows.sort_values(["decision_snapshot_dt", "decision_hour_local"]).copy()
        for key, rows in universe.groupby(["city", "target_date"])
    }
    fixed_wait: list[pd.Series] = []
    reaffirm: list[pd.Series] = []
    no_later_snapshot = 0
    outside_hourly_replay_window = 0
    reaffirm_rejected = 0
    for _, signal in immediate.iterrows():
        key = (signal["city"], signal["target_date"])
        threshold = signal["decision_snapshot_dt"] + pd.Timedelta(hours=1)
        later = groups[key][groups[key]["decision_snapshot_dt"] >= threshold]
        if later.empty:
            if float(signal["decision_hour_local"]) >= 17:
                outside_hourly_replay_window += 1
            else:
                no_later_snapshot += 1
            continue
        next_row = later.iloc[0]
        fixed_wait.append(next_row)
        if bool(next_row["cc_route_valid"]):
            reaffirm.append(next_row)
        else:
            reaffirm_rejected += 1

    columns = list(universe.columns)
    fixed_frame = pd.DataFrame(fixed_wait, columns=columns)
    reaffirm_frame = pd.DataFrame(reaffirm, columns=columns)
    policy_frames = {
        "route_immediate": immediate,
        "fixed_wait_next_hour": fixed_frame,
        "route_reaffirmed_next_hour": reaffirm_frame,
        "route_first_at_15_or_later": first_city_day(route_rows[route_rows["decision_hour_local"].ge(15)]),
        "route_first_at_ask_ge_0p95": first_city_day(route_rows[route_rows["current_yes_ask"].ge(0.95)]),
        "route_plus_mature_fade": first_city_day(
            route_rows[
                route_rows["decline_native"].ge(0.5)
                & route_rows["minutes_since_running_max"].ge(60)
            ]
        ),
    }
    rows: list[dict[str, Any]] = []
    for name, entries in policy_frames.items():
        row = selector_metrics(entries, name)
        row["base_route_city_days"] = int(len(immediate))
        row["selected_share_of_base_route"] = float(len(entries) / len(immediate)) if len(immediate) else None
        row["paired_vs_immediate"] = paired_roi_delta(immediate, entries)
        if name in {"fixed_wait_next_hour", "route_reaffirmed_next_hour"}:
            row["no_later_snapshot_coverage_gap"] = no_later_snapshot
            row["outside_13_17_hourly_replay_window"] = outside_hourly_replay_window
        if name == "route_reaffirmed_next_hour":
            row["later_snapshot_but_route_not_reaffirmed"] = reaffirm_rejected
        rows.append(row)
    return rows, policy_frames


def taker_vwap_audit(entries: pd.DataFrame, quantities: tuple[int, ...] = (5, 10)) -> tuple[dict[str, Any], pd.DataFrame]:
    """Replay fixed-size takers through the archived full ask ladder."""
    file_cache: dict[str, dict[tuple[str, str, str, int], list[dict[str, Any]]]] = {}

    def timestamp_key(value: Any) -> int | None:
        parsed = pd.to_datetime(value, utc=True, errors="coerce")
        return None if pd.isna(parsed) else int(parsed.value)

    def load_file(relative: str) -> dict[tuple[str, str, str, int], list[dict[str, Any]]]:
        if relative in file_cache:
            return file_cache[relative]
        path = Path(relative)
        if not path.is_absolute():
            path = ROOT / path
        indexed: dict[tuple[str, str, str, int], list[dict[str, Any]]] = {}
        if path.exists():
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    ts = timestamp_key(record.get("snapshot_ts_utc"))
                    if record.get("status") != "ok" or ts is None:
                        continue
                    key = (
                        str(record.get("city") or ""),
                        str(record.get("event_date") or ""),
                        str(record.get("bracket") or ""),
                        ts,
                    )
                    if str(record.get("outcome") or "").lower() == "yes":
                        indexed[key] = list((record.get("raw") or {}).get("asks") or [])
        file_cache[relative] = indexed
        return indexed

    rows: list[dict[str, Any]] = []
    for _, entry in entries.iterrows():
        relative = str(entry.get("orderbook_file") or "")
        key_ts = timestamp_key(entry.get("decision_snapshot_ts_utc"))
        asks = []
        if relative and key_ts is not None:
            asks = load_file(relative).get(
                (str(entry["city"]), str(entry["target_date"]), str(entry["current_bracket"]), key_ts), []
            )
        levels: list[tuple[float, float]] = []
        for level in asks:
            try:
                price = float(level.get("price"))
                size = float(level.get("size"))
            except (TypeError, ValueError):
                continue
            if 0 < price < 1 and size > 0:
                levels.append((price, size))
        levels.sort(key=lambda item: item[0])
        raw_best = levels[0][0] if levels else None
        for quantity in quantities:
            remaining = float(quantity)
            principal = 0.0
            fees = 0.0
            for price, available in levels:
                take = min(remaining, available)
                principal += take * price
                fees += take * round(0.05 * price * (1.0 - price), 5)
                remaining -= take
                if remaining <= 1e-9:
                    break
            executable = remaining <= 1e-9
            total_cost = principal + fees if executable else None
            best_ask_cost = (
                quantity * (float(entry["current_yes_ask"]) + round(
                    0.05 * float(entry["current_yes_ask"]) * (1.0 - float(entry["current_yes_ask"])), 5
                ))
            )
            rows.append(
                {
                    "city": entry["city"],
                    "target_date": entry["target_date"],
                    "decision_snapshot_ts_utc": entry["decision_snapshot_ts_utc"],
                    "current_bracket": entry["current_bracket"],
                    "quantity": quantity,
                    "label": int(entry["label"]),
                    "archive_file_present": bool(relative and (ROOT / relative).exists()) if not Path(relative).is_absolute() else Path(relative).exists(),
                    "raw_book_matched": bool(levels),
                    "full_quantity_executable": executable,
                    "feature_best_ask": float(entry["current_yes_ask"]),
                    "raw_best_ask": raw_best,
                    "principal": principal if executable else None,
                    "fee": fees if executable else None,
                    "effective_cost": total_cost,
                    "best_ask_only_cost": best_ask_cost,
                    "slippage_cost_vs_best_ask": total_cost - best_ask_cost if executable else None,
                    "pnl": quantity * int(entry["label"]) - total_cost if executable else None,
                }
            )
    detail = pd.DataFrame(rows)
    summary: dict[str, Any] = {}
    for quantity in quantities:
        sample = detail[detail["quantity"].eq(quantity)].copy()
        full = sample[sample["full_quantity_executable"]].copy()
        cost = float(full["effective_cost"].sum()) if len(full) else 0.0
        best_cost = float(full["best_ask_only_cost"].sum()) if len(full) else 0.0
        summary[str(quantity)] = {
            "signal_city_days": int(len(sample)),
            "raw_book_match_rate": float(sample["raw_book_matched"].mean()) if len(sample) else None,
            "full_quantity_executable_rate": float(sample["full_quantity_executable"].mean()) if len(sample) else None,
            "best_ask_mismatch_rows": int(
                (
                    sample["raw_best_ask"].notna()
                    & sample["raw_best_ask"].sub(sample["feature_best_ask"]).abs().gt(1e-9)
                ).sum()
            ),
            "avg_principal_vwap": float(full["principal"].sum() / (quantity * len(full))) if len(full) else None,
            "avg_effective_cost_per_share": float(cost / (quantity * len(full))) if len(full) else None,
            "avg_slippage_cents_per_share_vs_best_ask": float(
                100 * full["slippage_cost_vs_best_ask"].sum() / (quantity * len(full))
            ) if len(full) else None,
            "fee_adjusted_vwap_roi": float(full["pnl"].sum() / cost) if cost > 0 else None,
            "best_ask_only_roi_same_rows": float(
                (quantity * full["label"].sum() - best_cost) / best_cost
            ) if best_cost > 0 else None,
        }
    return summary, detail


def make_model(features: list[str]) -> Pipeline:
    pre = ColumnTransformer(
        [
            (
                "numeric",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="median")),
                        ("scale", StandardScaler()),
                    ]
                ),
                features,
            )
        ]
    )
    return Pipeline(
        [
            ("pre", pre),
            ("model", LogisticRegression(C=0.1, max_iter=3000, random_state=SEED)),
        ]
    )


def expanding_oof(universe: pd.DataFrame, feature_sets: dict[str, list[str]]) -> pd.DataFrame:
    dates = sorted(universe["target_date"].unique())
    output: list[pd.DataFrame] = []
    for index, target_date in enumerate(dates):
        if index < MIN_TRAIN_DATES:
            continue
        train = universe[universe["target_date"] < target_date].copy()
        test = universe[universe["target_date"] == target_date].copy()
        if train["label"].nunique() < 2 or test.empty:
            continue
        counts = train.groupby(["city", "target_date"])["label"].transform("size")
        weights = 1.0 / counts.clip(lower=1).to_numpy(float)
        result = test[["city", "target_date", "decision_snapshot_dt", "label", "market_mid", "cc_route_valid"]].copy()
        result = result.rename(columns={"cc_route_valid": "route"})
        result["p_market_raw"] = test["market_mid"].clip(1e-5, 1 - 1e-5).to_numpy(float)
        for name, features in feature_sets.items():
            model = make_model(features)
            model.fit(train[features], train["label"], model__sample_weight=weights)
            result[f"p_{name}"] = model.predict_proba(test[features])[:, 1]
        output.append(result)
    return pd.concat(output, ignore_index=True) if output else pd.DataFrame()


def score_loss(frame: pd.DataFrame, p_col: str) -> dict[str, Any]:
    y = frame["label"].to_numpy(int)
    p = frame[p_col].clip(1e-6, 1 - 1e-6).to_numpy(float)
    weights = (
        1.0
        / frame.groupby(["city", "target_date"])["label"].transform("size").clip(lower=1).to_numpy(float)
    )
    return {
        "rows": int(len(frame)),
        "city_days": int(frame.groupby(["city", "target_date"]).ngroups),
        "dates": int(frame["target_date"].nunique()),
        "brier": float(brier_score_loss(y, p, sample_weight=weights)),
        "logloss": float(log_loss(y, p, sample_weight=weights)),
        "mean_pred": float(np.average(p, weights=weights)),
        "actual_rate": float(np.average(y, weights=weights)),
        "score_weighting": "each city-day one total vote; states within city-day share that vote",
    }


def loss_delta_ci(frame: pd.DataFrame, candidate: str, baseline: str) -> dict[str, Any]:
    work = frame.copy()
    y = work["label"].to_numpy(float)
    pc = work[candidate].clip(1e-6, 1 - 1e-6).to_numpy(float)
    pb = work[baseline].clip(1e-6, 1 - 1e-6).to_numpy(float)
    work["brier_delta"] = (pc - y) ** 2 - (pb - y) ** 2
    work["logloss_delta"] = -(y * np.log(pc) + (1 - y) * np.log(1 - pc)) + (
        y * np.log(pb) + (1 - y) * np.log(1 - pb)
    )
    city_day = (
        work.groupby(["city", "target_date"])[["brier_delta", "logloss_delta"]]
        .mean()
        .reset_index()
    )
    dates = sorted(city_day["target_date"].unique())
    blocks = {
        date: city_day[city_day["target_date"].eq(date)][["brier_delta", "logloss_delta"]].to_numpy(float)
        for date in dates
    }
    rng = np.random.default_rng(SEED)
    draws = {"brier": [], "logloss": []}
    for _ in range(BOOTSTRAP_REPS):
        chosen = rng.integers(0, len(dates), len(dates))
        sample = np.concatenate([blocks[dates[index]] for index in chosen], axis=0)
        draws["brier"].append(float(sample[:, 0].mean()))
        draws["logloss"].append(float(sample[:, 1].mean()))
    return {
        "candidate_minus_baseline_brier": float(city_day["brier_delta"].mean()),
        "brier_delta_ci95": [float(np.quantile(draws["brier"], 0.025)), float(np.quantile(draws["brier"], 0.975))],
        "candidate_minus_baseline_logloss": float(city_day["logloss_delta"].mean()),
        "logloss_delta_ci95": [float(np.quantile(draws["logloss"], 0.025)), float(np.quantile(draws["logloss"], 0.975))],
        "city_days": int(len(city_day)),
        "interpretation": "negative delta means candidate improves; city-days equal-weighted and target dates block-bootstrapped",
    }


def probability_audit(universe: pd.DataFrame) -> dict[str, Any]:
    feature_sets = {
        "market_cal": ["market_logit"],
        "market_plus_ceiling": ["market_logit", "gap_debiased_steps", "bias_known_num"],
        "market_plus_path": ["market_logit", "path_faded_num"],
        "market_plus_cc": [
            "market_logit", "gap_debiased_steps", "bias_known_num", "path_faded_num", "route_num",
        ],
        "market_plus_legacy_physics": [
            "market_logit", "decision_hour_local", "decline_steps", "minutes_since_running_max_log",
            "temp_trend_1h_f", "temp_trend_3h_f", "forecast_peak_delta_hours_local",
            "relative_humidity_pct", "sky_cover_code", "dewpoint_depression_f", "wind_speed_kt", "obs_age_min",
        ],
        "market_plus_combined": [
            "market_logit", "gap_debiased_steps", "bias_known_num", "path_faded_num", "route_num",
            "decision_hour_local", "decline_steps", "minutes_since_running_max_log",
            "temp_trend_1h_f", "temp_trend_3h_f", "forecast_peak_delta_hours_local",
            "relative_humidity_pct", "sky_cover_code", "dewpoint_depression_f", "wind_speed_kt", "obs_age_min",
        ],
    }
    oof = expanding_oof(universe, feature_sets)
    oof_dates = sorted(oof["target_date"].unique())
    half_index = max(1, len(oof_dates) // 2)
    early_dates = set(oof_dates[:half_index])
    late_dates = set(oof_dates[half_index:])
    slices = {
        "all_oof_states": oof,
        "oof_early_dates": oof[oof["target_date"].isin(early_dates)],
        "oof_late_dates": oof[oof["target_date"].isin(late_dates)],
        "carry_market_mid_ge_0p80": oof[oof["market_mid"].ge(0.80)],
        "cc_route_states": oof[oof["route"]],
    }
    results: dict[str, Any] = {"feature_sets": feature_sets, "oof_rows": int(len(oof)), "slices": {}}
    for slice_name, frame in slices.items():
        if frame.empty:
            continue
        scores = {"market_raw": score_loss(frame, "p_market_raw")}
        for model_name in feature_sets:
            scores[model_name] = score_loss(frame, f"p_{model_name}")
        deltas: dict[str, Any] = {}
        for model_name in feature_sets:
            deltas[f"{model_name}_vs_market_raw"] = loss_delta_ci(
                frame, f"p_{model_name}", "p_market_raw"
            )
            if model_name != "market_cal":
                deltas[f"{model_name}_vs_market_cal"] = loss_delta_ci(
                    frame, f"p_{model_name}", "p_market_cal"
                )
        deltas["combined_vs_cc"] = loss_delta_ci(
            frame, "p_market_plus_combined", "p_market_plus_cc"
        )
        results["slices"][slice_name] = {"scores": scores, "paired_loss_deltas": deltas}
    oof.to_csv(OUT_DIR / "probability_oof_rows.csv", index=False)
    return results


def fmt_pct(value: Any) -> str:
    return "NA" if value is None else f"{float(value) * 100:+.2f}%"


def fmt_num(value: Any, digits: int = 3) -> str:
    return "NA" if value is None else f"{float(value):.{digits}f}"


def write_report(payload: dict[str, Any]) -> None:
    mechanism = payload["mechanism_selectors"]
    mechanism_by_name = {row["selector"]: row for row in mechanism}
    mechanism_periods = payload["mechanism_period_stability"]
    timing = payload["entry_timing"]
    timing_by_name = {row["selector"]: row for row in timing}
    probability = payload["probability_increment"]
    all_scores = probability["slices"]["all_oof_states"]["scores"]
    all_deltas = probability["slices"]["all_oof_states"]["paired_loss_deltas"]
    evidence = payload["evidence_funnel"]
    lineage = payload["forecast_model_lineage_slices"]
    fixed_route = lineage["fixed_city_model_reconstruction"]
    fixed_periods = mechanism_periods["cc_route_fixed_city_model"]
    h1_no_price = mechanism_by_name["legacy_incumbent_gate_no_price"]
    h1_price = mechanism_by_name["legacy_incumbent_gate_ask_ge_0p95"]
    forecast_coverage_complete = bool(evidence["assigned_forecast_covers_history_end"])
    if forecast_coverage_complete:
        forecast_coverage_line = (
            f"- Per-city 固定 `CITY_MODEL` 的双模型 Single Runs PIT 列覆盖 "
            f"{evidence['assigned_forecast_available_rows']}/{evidence['pit_two_sided_book_rows']} 行、"
            f"一直到历史末日 {evidence['assigned_forecast_available_date_max']}；本次主结果没有再把 forecast coverage "
            "混入 signal funnel。"
        )
    else:
        forecast_coverage_line = (
            f"- Per-city 固定 `CITY_MODEL` 的双模型 Single Runs PIT 列只覆盖 "
            f"{evidence['assigned_forecast_available_rows']} 行 / {evidence['assigned_forecast_available_dates']} 天，"
            f"截止 {evidence['assigned_forecast_available_date_max']}；其后只记 evidence coverage gap，"
            "不进入 CC 概率/timing 主结果。"
        )
    lines = [
        "# Current-YES Carry 机制 / 入场时机 / 执行一致性审计 v1",
        "",
        "Status: `research_audit / no_new_selector / no_live_change`",
        f"Generated: `{payload['generated_at_utc']}`",
        "",
        "## 数据快照",
        "",
        f"- 历史状态/行情总表：`{payload['data_snapshot']['history_source']}`，"
        f"{payload['data_snapshot']['date_min']}..{payload['data_snapshot']['date_max']}，"
        f"{payload['data_snapshot']['rows']} state rows / {payload['data_snapshot']['dates']} target dates / "
        f"{payload['data_snapshot']['cities']} cities。",
        forecast_coverage_line,
        f"- 行情：原始 orderbook snapshot 同时点 direct bid/ask；当前分析表 quote 非空率 "
        f"{payload['data_snapshot']['quote_coverage']:.1%}，但这是 loader 先选 evidence-complete 行的结果，"
        f"不是上游原始 signal 的 100% 覆盖率。天气 observation age 中位数约 "
        f"{payload['data_snapshot']['obs_age_median_min']:.1f} 分钟；"
        "历史每城每小时只保留一帧（通常约 :30），不能声称 5–15 分钟 timing。",
        f"- 结算：{payload['data_snapshot']['settled_rows']}/{payload['data_snapshot']['rows']} settled；"
        f"unsettled={payload['data_snapshot']['unsettled_rows']}；missing_bracket={payload['data_snapshot']['missing_bracket']}。",
        f"- 最近 H1 shadow/execution：`{payload['data_snapshot']['recent_h1_generated_at_utc']}`；"
        f"CLOB coverage gate=`{payload['data_snapshot']['clob_gate_pass']}` / "
        f"{payload['data_snapshot']['clob_gate_live_real_fill_rows']} live_real fill rows。"
        f"数据动作：{payload['data_snapshot']['sync_or_rebuild']}。",
        "- 费用主口径：fresh/direct ask taker，`fee=0.05*p*(1-p)`；maker/rebate 不计入任何 alpha 结论。",
        "",
        "## 结论",
        "",
        f"1. 按固定城市模型重算后，`bias_known ∧ ceiling_busted ∧ path_faded` 只有 "
        f"{fixed_route['city_days']} 个 first city-day，taker ROI {fmt_pct(fixed_route['fee_adjusted_taker_roi'])}，"
        f"95% CI [{fmt_pct(fixed_route['target_date_block_ci95'][0])}, "
        f"{fmt_pct(fixed_route['target_date_block_ci95'][1])}]；机制尚未验证。",
        "2. 简单把 CC 与旧 H1 条件相交没有稳定增量；proper-score OOF 也没有同时、稳定改善 Brier 和 logloss，"
        "不能因为单个 ROI/Brier 数字好看就合成新模型。",
        f"3. 去掉 H1 的 0.95 价格门后是 {h1_no_price['city_days']} 个 city-day、ROI "
        f"{fmt_pct(h1_no_price['fee_adjusted_taker_roi'])}；保留旧 0.95 门是 {h1_price['city_days']} 个、ROI "
        f"{fmt_pct(h1_price['fee_adjusted_taker_roi'])}。价格只应进入 EV/成本，当前证据不支持把 0.95 当机制门。",
        "4. 入场时机目前只能确认一个方向：等待会提高市场确认度，但会买贵并丢失/更换部分 bracket；"
        "小时级 archive 不能决定分钟级最佳等待。",
        "5. 新算法暂不建立。固定模型历史已补齐；剩余工作是把 date-order bias 升级成 availability-time PIT，"
        "并将所有 observed/blocked state、fresh quote/depth 和最终 label 接到同一零仓位 forward collector，"
        "之后才冻结 taker-only residual 实验。",
        "",
        "## 先修正的数据语义",
        "",
        f"旧 loader 没显式选择 current-YES expression：current bracket 的 YES/NO forecast max 在 "
        f"{payload['source_semantics_audit']['forecast_max_native_different']}/"
        f"{payload['source_semantics_audit']['forecast_max_native_paired']} 个可配对 city-hour 不同，peak clock 在 "
        f"{payload['source_semantics_audit']['forecast_peak_delta_hours_local_different']}/"
        f"{payload['source_semantics_audit']['forecast_peak_delta_hours_local_paired']} 个不同。",
        f"同时，旧 bias 按历史 state row 加权且缺失时回填 0；修正为 explicit YES + 每个历史 city-day 等权 + "
        f"bias 缺失记 coverage gap 后，首次 route 从 "
        f"{payload['expression_bias_semantics_impact']['legacy_first_city_days']} 变为 "
        f"{payload['expression_bias_semantics_impact']['clean_first_city_days']}。",
        f"更关键的是 forecast lineage：该中间口径的 {lineage['mixed_forecast_all']['city_days']} 个 route 中，"
        f"只有 {lineage['mixed_forecast_assigned_family_aligned']['city_days']} 个实际 source family 与 `CITY_MODEL` 对齐，"
        f"另 {lineage['mixed_forecast_assigned_family_mismatched']['city_days']} 个不对齐；后者 ROI "
        f"{fmt_pct(lineage['mixed_forecast_assigned_family_mismatched']['fee_adjusted_taker_roi'])}，"
        f"显著抬高了原 headline。改用表内双模型的指定列后 route 为 {fixed_route['city_days']} 个。"
        "这些变化属于数据语义/覆盖影响半径，不是策略漏斗。",
        "",
        "| forecast lineage slice | city-day | win | avg ask | taker ROI | 95% CI |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in [
        "mixed_forecast_all",
        "mixed_forecast_assigned_family_aligned",
        "mixed_forecast_assigned_family_mismatched",
        "fixed_city_model_reconstruction",
    ]:
        row = lineage[name]
        ci = row["target_date_block_ci95"]
        lines.append(
            f"| {name} | {row['city_days']} | {fmt_pct(row['win_rate'])} | {fmt_num(row['avg_ask'])} | "
            f"{fmt_pct(row['fee_adjusted_taker_roi'])} | [{fmt_pct(ci[0])}, {fmt_pct(ci[1])}] |"
        )
    lines += [
        "",
        "## Signal funnel 与机制 selector",
        "",
        "| selector | city-day | win | avg ask | taker ROI | 95% CI | front / back ROI | ask size≥5 / ≥10 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in mechanism:
        ci = row["target_date_block_ci95"]
        periods = mechanism_periods[row["selector"]]
        lines.append(
            f"| {row['selector']} | {row['city_days']} | {fmt_pct(row['win_rate'])} | "
            f"{fmt_num(row['avg_ask'])} | {fmt_pct(row['fee_adjusted_taker_roi'])} | "
            f"[{fmt_pct(ci[0])}, {fmt_pct(ci[1])}] | "
            f"{fmt_pct(periods['front']['fee_adjusted_taker_roi'])} / "
            f"{fmt_pct(periods['back']['fee_adjusted_taker_roi'])} | "
            f"{fmt_pct(row['top_ask_size_ge_5_share'])} / {fmt_pct(row['top_ask_size_ge_10_share'])} |"
        )
    lines += [
        "",
        "前两个 CC 行仅用于影响对照；primary 是 `cc_route_fixed_city_model`。"
        "`bias_known` 与 fixed-model availability 是证据完整性，不是新增 alpha 阈值。",
        "",
        "`ceiling_busted` 或 `path_faded` 单独均未形成可靠 taker alpha；固定模型下两者交集点估也未为正，CI 跨 0。"
        "本轮还比较过多个 regime 组合且未做多重检验校正，所以最多保留为 frozen-forward 候选机制。",
        "",
        "## 概率增量：同 rows expanding OOF",
        "",
        f"本节只用 fixed-model evidence-complete 的 {evidence['assigned_forecast_available_rows']} 行，"
        f"截止 {evidence['assigned_forecast_available_date_max']}。每个测试日只用严格更早日期训练；"
        "训练和评分都让每个 city-day 总权重相等，避免重复状态或覆盖差异过度加权。"
        "所有模型都先看到 market midpoint，再检验天气机制能否提供 residual。固定 6 个模型，未调超参数。",
        "",
        "| model | OOF rows | Brier | Logloss | Brier Δ vs raw / market-cal | Logloss Δ vs raw / market-cal |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, score in all_scores.items():
        raw_delta = all_deltas.get(f"{name}_vs_market_raw")
        cal_delta = all_deltas.get(f"{name}_vs_market_cal")
        lines.append(
            f"| {name} | {score['rows']} | {fmt_num(score['brier'],4)} | {fmt_num(score['logloss'],4)} | "
            f"{fmt_num(raw_delta['candidate_minus_baseline_brier'],4) if raw_delta else '—'} / "
            f"{fmt_num(cal_delta['candidate_minus_baseline_brier'],4) if cal_delta else '—'} | "
            f"{fmt_num(raw_delta['candidate_minus_baseline_logloss'],4) if raw_delta else '—'} / "
            f"{fmt_num(cal_delta['candidate_minus_baseline_logloss'],4) if cal_delta else '—'} |"
        )
    combined_delta = all_deltas["combined_vs_cc"]
    lines += [
        "",
        "负的 loss delta 才代表改善。这里的判定不能只看一个点估：Brier 与 logloss 方向不一致、"
        "或日期 bootstrap CI 跨 0，都视为该机制尚未证明。完整 CI 在 JSON。",
        "",
        "天气增量应先与相同训练过程的 `market_cal` 比，raw market 同时保留为最终外部基线。",
        "",
        "**Combined 相对 market-cal 的时间/目标切片稳定性**",
        "",
        "| slice | rows/dates | Brier Δ [95%CI] | Logloss Δ [95%CI] |",
        "|---|---:|---:|---:|",
    ]
    for slice_name, slice_payload in probability["slices"].items():
        score = slice_payload["scores"]["market_plus_combined"]
        delta = slice_payload["paired_loss_deltas"]["market_plus_combined_vs_market_cal"]
        lines.append(
            f"| {slice_name} | {score['rows']}/{score['dates']} | "
            f"{fmt_num(delta['candidate_minus_baseline_brier'],4)} "
            f"[{fmt_num(delta['brier_delta_ci95'][0],4)}, {fmt_num(delta['brier_delta_ci95'][1],4)}] | "
            f"{fmt_num(delta['candidate_minus_baseline_logloss'],4)} "
            f"[{fmt_num(delta['logloss_delta_ci95'][0],4)}, {fmt_num(delta['logloss_delta_ci95'][1],4)}] |"
        )
    lines += [
        "",
        "旧 V3.1 residual 不能直接复用：它的 weather residual 相对 raw market 虽把 Brier "
        f"从 {fmt_num(payload['legacy_v31_audit']['market_brier'],4)} 改到 {fmt_num(payload['legacy_v31_audit']['v31_brier'],4)}，"
        f"但 logloss 从 {fmt_num(payload['legacy_v31_audit']['market_logloss'],4)} 恶化到 "
        f"{fmt_num(payload['legacy_v31_audit']['v31_logloss'],4)}；主交易规则 ROI "
        f"{fmt_pct(payload['legacy_v31_audit']['primary_roi'])} 且 CI 跨 0。它还使用重复 state rows、旧的无官方 fee 交易口径。",
        "",
        "## 入场时机（小时级可执行回放）",
        "",
        "| policy | selected/base | win | avg ask | taker ROI | CI | paired ask Δ | paired ROI Δ | bracket changed |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in timing:
        paired = row["paired_vs_immediate"]
        ci = row["target_date_block_ci95"]
        lines.append(
            f"| {row['selector']} | {row['city_days']}/{row['base_route_city_days']} | {fmt_pct(row['win_rate'])} | "
            f"{fmt_num(row['avg_ask'])} | {fmt_pct(row['fee_adjusted_taker_roi'])} | "
            f"[{fmt_pct(ci[0])}, {fmt_pct(ci[1])}] | "
            f"{fmt_num(paired.get('avg_ask_delta_delayed_minus_immediate'),4)} | "
            f"{fmt_pct(paired.get('delayed_minus_immediate_roi'))} | "
            f"{fmt_pct(paired.get('bracket_changed_rate'))} |"
        )
    lines += [
        "",
        "固定等一小时与 next-hour route 再确认是不同策略：前者无论机制是否仍成立都进，后者在有下一帧但 route 消失时拒绝。"
        "17 点首次触发后无法在 13–17 研究窗内再等一小时的行单列为 replay-window boundary；"
        "17 点前没有下一帧的才是 archive coverage gap，均不伪装成策略过滤。价格到 0.95 才进也是可执行 timing policy，"
        "但它不能反过来证明 0.95 是天气机制。",
        "",
        "最近 30 秒 shadow 的补充证据：15 点前 H1 为 "
        f"{payload['recent_h1_timing']['before_15_settled']} settled、ROI {fmt_pct(payload['recent_h1_timing']['before_15_roi'])}；"
        f"15–17 点为 {payload['recent_h1_timing']['late_settled']} settled、ROI {fmt_pct(payload['recent_h1_timing']['late_roi'])}。"
        "两者只有 4–5 个日期，而且唯一 SF loss 在早段；这支持继续研究 later timing，不足以设 15 点 hard gate。",
        "",
        "## Historical ↔ Live parity 阻塞项",
        "",
        "| 项目 | 历史研究 | 当前 live/shadow | 结论 |",
        "|---|---|---|---|",
        "| 决策 cadence | 每城每小时一帧 | runner 30 秒，天气源按各城 cadence | 只能研究小时级 policy；分钟级必须 forward |",
        f"| forecast | D-1 12Z Single Runs 的 GFS/ECMWF 双列已补到 {evidence['assigned_forecast_available_date_max']}，"
        "再按 CITY_MODEL 取列 | live curve 按 CITY_MODEL | 历史 coverage 已修；仍需 golden-row 核对 curve 构造，并统一保存 assigned/actual/run/hash |",
        "| forecast bias | 仅按 target_date 严格早日计算，未验证 label available_at | shared bias 模块存在，但 H1 决策未消费 | 目前只是 date-PIT proxy，必须升级 availability-time PIT |",
        "| path state | canonical intraday/running-max labels | live 已有同名 labels | 需要 golden-row parity，而非相信字段同名 |",
        "| weather扩展特征 | 历史缺 solar/雨/风向/TAF transition 完整 PIT | forward 已开始采 | 只能作为 forward ablation，不能事后补历史 |",
        "| 盘口 | 同时点 direct bid/ask/size，小时粒度 | 下单前 fresh CLOB | replay 必须用 fresh ask；无 top-size 时记 execution coverage gap |",
        "| 表达/费用 | exact current bracket YES；官方 taker fee | 相同 | parity 可实现 |",
        "| 去重 | 首次 city-day | family city-day submitted 去重 | selector shadow 也必须记录所有 observed/blocked |",
        "",
        "## 晋级检查表",
        "",
        "| 环节 | 状态 | 当前证据 |",
        "|---|---|---|",
        f"| 机制本身 | FAIL | fixed-model route {fixed_route['city_days']} 个，ROI "
        f"{fmt_pct(fixed_route['fee_adjusted_taker_roi'])}；后段 {fixed_periods['back']['city_days']} 个为 "
        f"{fmt_pct(fixed_periods['back']['fee_adjusted_taker_roi'])} |",
        f"| 相对 market 的概率增量 | FAIL | combined vs market-cal：Brier Δ "
        f"{fmt_num(all_deltas['market_plus_combined_vs_market_cal']['candidate_minus_baseline_brier'],4)}，"
        f"logloss Δ {fmt_num(all_deltas['market_plus_combined_vs_market_cal']['candidate_minus_baseline_logloss'],4)}；"
        "两者日期 bootstrap CI 均跨 0 |",
        f"| 小时级入场时机 | FAIL / 未冻结 | 等到 ask≥0.95 的 paired ROI Δ "
        f"{fmt_pct(timing_by_name['route_first_at_ask_ge_0p95']['paired_vs_immediate']['delayed_minus_immediate_roi'])}；"
        f"next-hour reaffirm Δ {fmt_pct(timing_by_name['route_reaffirmed_next_hour']['paired_vs_immediate']['delayed_minus_immediate_roi'])} |",
        f"| 历史 forecast PIT | PASS-COVERAGE / PARTIAL-PARITY | fixed-model evidence 到 "
        f"{evidence['assigned_forecast_available_date_max']}；bias 仍只有 date-order proxy |",
        "| Taker 可执行性 | PARTIAL | archived 5/10-share 全档 VWAP 已回放；尚无 compute→submission→exchange latency 回放 |",
        "| 生产同分母 forward | FAIL | 新 selector 尚无全量 observed/blocked candidate ledger，也无 golden-row parity |",
        f"| Maker | DEFERRED | 仅 {payload['maker_audit']['opportunities']} 个 paired opportunity，"
        "必须用 planned denominator/ITT，不能用 filled-only 盈利 |",
        f"| 组合风险 | FAIL | 历史信号均值 {fmt_num(payload['portfolio_shape']['signals_per_covered_date'],2)} 个/覆盖日，"
        f"p90/max={fmt_num(payload['portfolio_shape']['signals_p90_active_date'],1)}/"
        f"{payload['portfolio_shape']['signals_max_active_date']}；5-share 最差日约 "
        f"${payload['portfolio_shape']['worst_day_pnl_5_share']:.2f}，尚未冻结相关敞口和 size policy |",
        "",
        "## Taker 第一版：固定数量逐档 VWAP",
        "",
        "| shares | raw book match | full executable | avg VWAP | avg effective cost | slippage vs best ask | VWAP ROI / best-ask ROI |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for quantity in ["5", "10"]:
        row = payload["taker_vwap"][quantity]
        lines.append(
            f"| {quantity} | {fmt_pct(row['raw_book_match_rate'])} | {fmt_pct(row['full_quantity_executable_rate'])} | "
            f"{fmt_num(row['avg_principal_vwap'],4)} | {fmt_num(row['avg_effective_cost_per_share'],4)} | "
            f"{fmt_num(row['avg_slippage_cents_per_share_vs_best_ask'],3)}c | "
            f"{fmt_pct(row['fee_adjusted_vwap_roi'])} / {fmt_pct(row['best_ask_only_roi_same_rows'])} |"
        )
    lines += [
        "",
        "这一步直接吃 archived full ask ladder，不再假设 10 shares 全部能在 best ask 成交。"
        "仍未模拟 signal 计算到订单到达交易所之间的延迟；该 gap 只能由 forward 的 decision/book/submission/exchange 时间戳测量。",
        f"历史 candidate volume 是 {fmt_num(payload['portfolio_shape']['signals_per_covered_date'],2)} 个/覆盖日；"
        f"有信号日期的中位数/p90/max 为 {fmt_num(payload['portfolio_shape']['signals_median_active_date'],1)}/"
        f"{fmt_num(payload['portfolio_shape']['signals_p90_active_date'],1)}/{payload['portfolio_shape']['signals_max_active_date']}。"
        f"按每单 5 shares，最差 target date `{payload['portfolio_shape']['worst_day']}` 的组合 PnL 约 "
        f"${payload['portfolio_shape']['worst_day_pnl_5_share']:.2f}；这是风险测量，不据此新增日上限。",
        "",
        "## Maker 留口，但不进入 v1",
        "",
        f"CLOB gate 已通过。现有同 signal 配对只有 {payload['maker_audit']['opportunities']} 个 opportunity："
        f"passive fill rate {fmt_pct(payload['maker_audit']['passive_fill_rate'])}，filled-only 节省 "
        f"{fmt_num(payload['maker_audit']['saving_per_share'] * 100,3)}c/share；但 planned denominator maker-taker "
        f"ROI delta 为 {fmt_pct(payload['maker_audit']['planned_roi_delta'])}。所以第一版必须按 taker 评估。",
        "",
        "后续 maker 只保留这些字段：同 signal 的 `taker_now` counterfactual、queue-ahead、每次 reprice 的"
        "bid/ask/size、partial fill、cancel reason、真实 exchange fill time、1/5/15m 与 next-weather-epoch markout。"
        "没有这些字段前，maker upper bound 不进入收益。",
        "",
        "## 冻结前研究结论",
        "",
        "```text",
        "significance=FAIL / exploratory mechanisms and timing are not multiplicity-corrected forward evidence",
        "baseline=PARTIAL / same-row market proper-score baseline is now explicit; no combined model has passed both scores",
        "forward=FAIL / fixed-model history coverage repaired, but CC has no production-parity full-denominator forward sample",
        "conclusion=inconclusive; do not create or deploy a new carry selector yet",
        "```",
        "",
        "下一步不是再找一个阈值，而是建立统一 `carry_candidate_frame_v1`：每个 active city-day 的每个 checkpoint"
        "同时保存 market baseline、CC 连续量、旧物理连续量、transition/source uncertainty、fresh taker book 和最终 label。"
        "冻结后只做四组同 rows A/B：market、market+CC、market+旧物理、market+combined；只有 combined 在 proper score、"
        "taker residual、前瞻和 execution parity 同时过关，才命名为新算法。",
        "",
        "## 8 环覆盖",
        "",
        "- 已覆盖：描述性绩效、日期 bootstrap、机制判别、proper score、taker 执行价格、top-ask 容量覆盖、"
        "同日相关性、market baseline。",
        "- 未覆盖到可晋升：availability-time bias、独立 frozen forward、分钟级 timing、"
        "完整 transition PIT 历史、maker queue/fill hazard、"
        "taker submission latency 与更大数量的容量。",
        "",
        "## 产物",
        "",
        f"- Script: `{payload['outputs']['script']}`",
        f"- JSON: `{payload['outputs']['json']}`",
        f"- OOF rows: `{payload['outputs']['oof_rows']}`",
        f"- Timing rows: `{payload['outputs']['timing_rows']}`",
        f"- Expression/bias semantics impact: `{payload['outputs']['expression_bias_semantics_impact']}`",
        f"- Forecast-model semantics impact: `{payload['outputs']['forecast_model_semantics_impact']}`",
        f"- Taker VWAP rows: `{payload['outputs']['taker_vwap_rows']}`",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    universe = load().copy()
    mixed_forecast_universe = load(audit_mixed_forecast=True).copy()
    legacy_universe = load(legacy_semantics=True).copy()
    universe["decision_snapshot_dt"] = pd.to_datetime(
        universe["decision_snapshot_ts_utc"], utc=True, errors="coerce"
    )
    legacy_universe["decision_snapshot_dt"] = pd.to_datetime(
        legacy_universe["decision_snapshot_ts_utc"], utc=True, errors="coerce"
    )
    mixed_forecast_universe["decision_snapshot_dt"] = pd.to_datetime(
        mixed_forecast_universe["decision_snapshot_ts_utc"], utc=True, errors="coerce"
    )
    universe["market_mid"] = (universe["current_yes_bid"] + universe["current_yes_ask"]) / 2.0
    universe["market_logit"] = np.log(
        universe["market_mid"].clip(1e-5, 1 - 1e-5)
        / (1 - universe["market_mid"].clip(1e-5, 1 - 1e-5))
    )
    universe["gap_debiased_steps"] = np.where(
        universe["bias_known"], universe["gap_debiased"] / universe["_step"], np.nan
    )
    universe["decline_steps"] = universe["decline_native"] / universe["_step"]
    universe["minutes_since_running_max_log"] = np.log1p(
        universe["minutes_since_running_max"].clip(lower=0, upper=720)
    )
    universe["bias_known_num"] = universe["bias_known"].astype(int)
    universe["path_faded_num"] = universe["path_faded"].astype(int)
    universe["cc_route_valid"] = universe["route"] & universe["bias_known"]
    universe["route_num"] = universe["cc_route_valid"].astype(int)

    selector_masks = {
        "ceiling_busted_only": universe["ceiling_busted"],
        "path_faded_only": universe["path_faded"],
        "cc_route_fixed_city_model": universe["cc_route_valid"],
        "legacy_incumbent_gate_no_price": universe["incumbent_gate"],
        "legacy_incumbent_gate_ask_ge_0p95": universe["incumbent_gate"]
        & universe["current_yes_ask"].ge(0.95),
        "cc_and_legacy_incumbent": universe["cc_route_valid"] & universe["incumbent_gate"],
        "cc_and_mature_fade": universe["cc_route_valid"]
        & universe["decline_native"].ge(0.5)
        & universe["minutes_since_running_max"].ge(60),
        "cc_and_not_warming": universe["cc_route_valid"] & universe["temp_trend_1h_f"].le(0),
    }
    legacy_route_metrics = selector_metrics(
        legacy_universe[legacy_universe["route"]], "cc_route_legacy_loader_audit_only"
    )
    mixed_route_metrics = selector_metrics(
        mixed_forecast_universe[mixed_forecast_universe["route"]],
        "cc_route_expression_bias_clean_mixed_forecast_audit_only",
    )
    mechanism_rows = [legacy_route_metrics, mixed_route_metrics] + [
        selector_metrics(universe[mask], name) for name, mask in selector_masks.items()
    ]
    mechanism_periods = {
        "cc_route_legacy_loader_audit_only": {
            "front": selector_metrics(
                legacy_universe[legacy_universe["route"] & legacy_universe["front"]], "legacy_front"
            ),
            "back": selector_metrics(
                legacy_universe[legacy_universe["route"] & ~legacy_universe["front"]], "legacy_back"
            ),
        },
        "cc_route_expression_bias_clean_mixed_forecast_audit_only": {
            "front": selector_metrics(
                mixed_forecast_universe[
                    mixed_forecast_universe["route"] & mixed_forecast_universe["front"]
                ],
                "mixed_forecast_front",
            ),
            "back": selector_metrics(
                mixed_forecast_universe[
                    mixed_forecast_universe["route"] & ~mixed_forecast_universe["front"]
                ],
                "mixed_forecast_back",
            ),
        },
        **{
        name: {
            "front": selector_metrics(universe[mask & universe["front"]], name + "_front"),
            "back": selector_metrics(universe[mask & ~universe["front"]], name + "_back"),
        }
        for name, mask in selector_masks.items()
        },
    }
    expression_impact, expression_impact_rows = loader_semantics_impact(
        legacy_universe, mixed_forecast_universe
    )
    forecast_impact, forecast_impact_rows = loader_semantics_impact(
        mixed_forecast_universe, universe
    )
    expression_impact_rows.to_csv(OUT_DIR / "expression_bias_semantics_impact.csv", index=False)
    forecast_impact_rows.to_csv(OUT_DIR / "forecast_model_semantics_impact.csv", index=False)
    timing_rows, timing_frames = timing_policies(universe)
    timing_frames["route_immediate"].to_csv(OUT_DIR / "route_immediate_rows.csv", index=False)
    for name, frame in timing_frames.items():
        frame.to_csv(OUT_DIR / f"timing_{name}.csv", index=False)
    taker_vwap, taker_vwap_rows = taker_vwap_audit(timing_frames["route_immediate"])
    taker_vwap_rows.to_csv(OUT_DIR / "taker_vwap_rows.csv", index=False)
    route_entries = timing_frames["route_immediate"].copy()
    route_entries["pnl_per_one_share"] = route_entries["label"] - route_entries["taker_cost"]
    route_daily = route_entries.groupby("target_date").agg(
        signals=("city", "size"),
        pnl_per_one_share=("pnl_per_one_share", "sum"),
    )
    worst_day = str(route_daily["pnl_per_one_share"].idxmin()) if len(route_daily) else None
    portfolio_shape = {
        "history_covered_dates": int(universe["target_date"].nunique()),
        "active_signal_dates": int(len(route_daily)),
        "signals_per_covered_date": float(len(route_entries) / universe["target_date"].nunique()),
        "signals_per_active_date": float(route_daily["signals"].mean()),
        "signals_median_active_date": float(route_daily["signals"].median()),
        "signals_p90_active_date": float(route_daily["signals"].quantile(0.90)),
        "signals_max_active_date": int(route_daily["signals"].max()),
        "worst_day": worst_day,
        "worst_day_pnl_per_one_share": float(route_daily.loc[worst_day, "pnl_per_one_share"]),
        "worst_day_pnl_5_share": float(5 * route_daily.loc[worst_day, "pnl_per_one_share"]),
        "note": "candidate volume, not expected fills; no daily cap inferred",
    }

    probability = probability_audit(universe[universe["assigned_forecast_available"]].copy())
    h1 = json.loads(H1_EXECUTION_SUMMARY.read_text(encoding="utf-8")) if H1_EXECUTION_SUMMARY.exists() else {}
    v31 = json.loads(V31_JSON.read_text(encoding="utf-8")) if V31_JSON.exists() else {}
    v31_metrics = {row["model"]: row for row in v31.get("holdout_model_metrics", [])}
    v31_rules = {row["rule"]: row for row in v31.get("holdout_rule_comparison", [])}
    market_v31 = v31_metrics.get("market_price_as_probability", {})
    residual_v31 = v31_metrics.get("v31_market_cal_plus_weather_residual", {})
    primary_v31 = v31_rules.get("v31_live_like_edge_ge_02", {})
    recent_slices = {row["slice"]: row for row in h1.get("recent_shadow_descriptive_slices", [])}
    maker = h1.get("paired_live_execution", {})
    clob_gate = run_clob_fill_coverage_gate()

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "research_audit_no_new_selector_no_live_change",
        "data_snapshot": {
            "history_source": "intraday regime atlas shards via explicit current-YES + per-city assigned-model loader",
            "rows": int(len(universe)),
            "dates": int(universe["target_date"].nunique()),
            "cities": int(universe["city"].nunique()),
            "date_min": str(universe["target_date"].min()),
            "date_max": str(universe["target_date"].max()),
            "settled_rows": int(universe["settlement_status"].eq("settled").sum()),
            "unsettled_rows": int(universe["settlement_status"].ne("settled").sum()),
            "missing_bracket": int(universe["label"].isna().sum()),
            "quote_coverage": float(
                universe[["current_yes_bid", "current_yes_ask", "current_yes_ask_size"]].notna().all(axis=1).mean()
            ),
            "obs_age_median_min": float(universe["obs_age_min"].median()),
            "contaminated_dates_excluded": ["2026-07-02", "2026-07-03", "2026-07-04", "2026-07-05"],
            "decision_cadence": "one retained state per city-target_date-local-hour",
            "recent_h1_generated_at_utc": h1.get("generated_at_utc"),
            "clob_gate_pass": bool(clob_gate["gate_pass"]),
            "clob_gate_live_real_fill_rows": int(clob_gate["fact_trades_live_real"]["rows"]),
            "clob_gate_fail_reasons": clob_gate.get("fail_reasons", []),
            "sync_or_rebuild": "fixed-model Single Runs backfill refreshed through 2026-07-08; affected feature shard and atlas rebuilt; canonical DB was not full-rebuilt",
        },
        "signal_funnel": {
            "universe_state_rows": int(len(universe)),
            "universe_city_days": int(universe.groupby(["city", "target_date"]).ngroups),
            "ceiling_busted_first_city_days": next(row["city_days"] for row in mechanism_rows if row["selector"] == "ceiling_busted_only"),
            "path_faded_first_city_days": next(row["city_days"] for row in mechanism_rows if row["selector"] == "path_faded_only"),
            "cc_route_legacy_loader_first_city_days": legacy_route_metrics["city_days"],
            "cc_route_expression_bias_clean_mixed_forecast_first_city_days": mixed_route_metrics["city_days"],
            "cc_route_fixed_city_model_first_city_days": next(
                row["city_days"] for row in mechanism_rows if row["selector"] == "cc_route_fixed_city_model"
            ),
            "cc_bias_unknown_evidence_gap_city_days": int(
                len(first_city_day(universe[~universe["bias_known"]]))
            ),
        },
        "evidence_funnel": {
            "pit_two_sided_book_rows": int(len(universe)),
            "settled_rows": int(universe["settlement_status"].eq("settled").sum()),
            "assigned_forecast_available_rows": int(universe["assigned_forecast_available"].sum()),
            "assigned_forecast_available_dates": int(
                universe.loc[universe["assigned_forecast_available"], "target_date"].nunique()
            ),
            "assigned_forecast_available_date_max": str(
                universe.loc[universe["assigned_forecast_available"], "target_date"].max()
            ),
            "assigned_forecast_missing_rows": int((~universe["assigned_forecast_available"]).sum()),
            "assigned_forecast_covers_history_end": bool(
                universe["assigned_forecast_available"].all()
                and universe.loc[universe["assigned_forecast_available"], "target_date"].max()
                == universe["target_date"].max()
            ),
            "top_ask_size_ge_5_rows": int(universe["current_yes_ask_size"].ge(5).sum()),
            "top_ask_size_ge_10_rows": int(universe["current_yes_ask_size"].ge(10).sum()),
            "actual_fill_rows_for_new_selector": 0,
        },
        "mechanism_selectors": mechanism_rows,
        "mechanism_period_stability": mechanism_periods,
        "source_semantics_audit": outcome_forecast_divergence(),
        "expression_bias_semantics_impact": expression_impact,
        "forecast_model_semantics_impact": forecast_impact,
        "forecast_model_lineage_slices": {
            "mixed_forecast_all": mixed_route_metrics,
            "mixed_forecast_assigned_family_aligned": selector_metrics(
                mixed_forecast_universe[
                    mixed_forecast_universe["route"]
                    & mixed_forecast_universe["forecast_source_original"].fillna("").str.lower().map(
                        lambda source: "ecmwf" if "ecmwf" in source else ("gfs" if "gfs" in source else "missing")
                    ).eq(mixed_forecast_universe["forecast_assigned_model"])
                ],
                "mixed_forecast_assigned_family_aligned",
            ),
            "mixed_forecast_assigned_family_mismatched": selector_metrics(
                mixed_forecast_universe[
                    mixed_forecast_universe["route"]
                    & ~mixed_forecast_universe["forecast_source_original"].fillna("").str.lower().map(
                        lambda source: "ecmwf" if "ecmwf" in source else ("gfs" if "gfs" in source else "missing")
                    ).eq(mixed_forecast_universe["forecast_assigned_model"])
                ],
                "mixed_forecast_assigned_family_mismatched",
            ),
            "fixed_city_model_reconstruction": next(
                row for row in mechanism_rows if row["selector"] == "cc_route_fixed_city_model"
            ),
        },
        "probability_increment": probability,
        "entry_timing": timing_rows,
        "taker_vwap": taker_vwap,
        "portfolio_shape": portfolio_shape,
        "legacy_v31_audit": {
            "market_brier": market_v31.get("brier"),
            "market_logloss": market_v31.get("logloss"),
            "v31_brier": residual_v31.get("brier"),
            "v31_logloss": residual_v31.get("logloss"),
            "primary_roi": primary_v31.get("roi"),
            "primary_ci95": primary_v31.get("bootstrap_roi_ci95"),
            "issues": [
                "state-row rather than first city-day trade denominator",
                "old trade summary omitted official Weather taker fee",
                "Brier improved slightly while logloss worsened",
                "primary residual rule ROI CI crossed zero",
            ],
        },
        "recent_h1_timing": {
            "before_15_settled": recent_slices.get("decision_time:<15", {}).get("settled_rows"),
            "before_15_roi": recent_slices.get("decision_time:<15", {}).get("fee_adjusted_roi"),
            "late_settled": recent_slices.get("decision_time:15-17", {}).get("settled_rows"),
            "late_roi": recent_slices.get("decision_time:15-17", {}).get("fee_adjusted_roi"),
            "warning": "descriptive only; one San Francisco loss drives the early slice and there are only 4-5 dates",
        },
        "maker_audit": {
            "opportunities": maker.get("opportunities", 0),
            "passive_fill_rate": maker.get("passive_fill_rate_opportunities"),
            "saving_per_share": maker.get("passive_saving_per_common_share"),
            "planned_roi_delta": maker.get("planned_notional_roi_delta"),
            "baseline_for_v1": "taker only",
        },
        "multiple_testing": {
            "models_compared": 6,
            "selector_variants_reported": len(selector_masks),
            "timing_policies_reported": len(timing_rows),
            "correction": "none; exploratory audit, therefore no selector promotion",
        },
        "verdict": {
            "significance": "FAIL_EXPLORATORY_NOT_FROZEN_FORWARD",
            "baseline": "PARTIAL_SAME_ROW_MARKET_PROPER_SCORE_ADDED",
            "forward": "FAIL_NO_PRODUCTION_PARITY_FULL_DENOMINATOR",
            "conclusion": "inconclusive",
            "live_action": "none",
            "next_artifact": "carry_candidate_frame_v1 zero-notional full-denominator collector with golden-row parity",
        },
        "eight_ring_coverage": {
            "covered": [1, 2, 3, 4, 5, 6, 7, 8],
            "not_sufficient": [
                "independent frozen forward",
                "minute-level timing",
                "historical PIT transition completeness",
                "availability-time forecast-bias lineage",
                "production-parity full-denominator candidate ledger",
                "maker queue/fill hazard",
                "taker submission latency and capacity above 10 shares",
            ],
        },
        "outputs": {
            "script": str(Path(__file__).relative_to(ROOT)),
            "json": str(OUT_JSON.relative_to(ROOT)),
            "report": str(OUT_MD.relative_to(ROOT)),
            "oof_rows": str((OUT_DIR / "probability_oof_rows.csv").relative_to(ROOT)),
            "timing_rows": str((OUT_DIR / "route_immediate_rows.csv").relative_to(ROOT)),
            "expression_bias_semantics_impact": str(
                (OUT_DIR / "expression_bias_semantics_impact.csv").relative_to(ROOT)
            ),
            "forecast_model_semantics_impact": str(
                (OUT_DIR / "forecast_model_semantics_impact.csv").relative_to(ROOT)
            ),
            "taker_vwap_rows": str((OUT_DIR / "taker_vwap_rows.csv").relative_to(ROOT)),
        },
    }
    OUT_JSON.write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(payload)
    print(
        json.dumps(
            json_ready(
                {
                    "mechanism_selectors": mechanism_rows,
                    "probability_increment": probability,
                    "entry_timing": timing_rows,
                    "verdict": payload["verdict"],
                }
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
