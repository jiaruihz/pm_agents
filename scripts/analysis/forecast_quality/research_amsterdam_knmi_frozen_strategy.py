#!/usr/bin/env python3
"""One-shot frozen-forward weather and taker replay for Amsterdam KNMI models."""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import pickle
import sqlite3
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


def paired_probability_delta(rows: pd.DataFrame) -> dict[str, Any]:
    if rows.empty:
        return {"rows": 0}
    p_model = np.clip(rows["p_model"].to_numpy(float), 1e-9, 1 - 1e-9)
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
        "roi_ci95": [float(value) for value in np.quantile(roi, [.025, .975])],
        "probability_roi_gt_zero": float((roi > 0).mean()),
    }


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
    sources = source_rows(args.runtime_root, args.start, args.end)
    frame = feature_frame(args.runtime_root, sources)
    if any(name in artifact["features"] for name in FORECAST_PATH_FEATURES):
        if args.forecast_path is None:
            raise ValueError("artifact requires --forecast-path")
        frame = add_fixed_lead_forecast_path_features(
            frame, pd.read_csv(args.forecast_path)
        )
    missing = sorted(set(artifact["features"]) - set(frame.columns))
    if missing:
        raise ValueError(f"frozen frame missing artifact columns: {missing}")
    matrix = frame[artifact["features"]].apply(pd.to_numeric, errors="coerce")
    raw = artifact["estimator"].predict_proba(matrix)[:, 1]
    if artifact.get("calibrator") is not None:
        clipped = np.clip(raw, 1e-7, 1 - 1e-7)
        raw = artifact["calibrator"].predict_proba(np.log(clipped/(1-clipped)).reshape(-1, 1))[:, 1]
    frame["p_model"] = raw
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
    record_rows = []
    for policy in POLICIES:
        summary, records = replay_policy(common, policy)
        summary["target_date_bootstrap"] = trade_date_bootstrap(records, universe_dates)
        strategy[policy["id"]] = summary
        record_rows.extend({"policy_id": policy["id"], **row} for row in records)
    market_summary, market_records = replay_policy(common, POLICIES[0], market_only=True)
    market_summary["target_date_bootstrap"] = trade_date_bootstrap(
        market_records, universe_dates
    )
    summary = {
        "schema_version": "amsterdam_knmi_v8_frozen_strategy_v1",
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
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
