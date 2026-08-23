#!/usr/bin/env python3
"""Evaluate the frozen Tmin no-further-cooling zero-notional shadow.

The runtime journal is snapshotted at read time and filtered to one immutable
model artifact.  Settlement truth is joined by condition_id from the canonical
weather DB; city/date/bracket joins are deliberately not supported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.city_calendar import CITY_TIMEZONE
from weather_model_evaluation.probability import (
    binary_loss_values,
    date_block_bootstrap_delta,
)


SCHEMA_VERSION = "weather_tmin_no_further_cooling_shadow_performance_v1"
FEE_RATE = 0.05
BOOTSTRAP_DRAWS = 20_000
BOOTSTRAP_SEED = 20260824


def _snapshot_jsonl(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        payload = handle.read(size)
    complete_size = len(payload) if payload.endswith(b"\n") else payload.rfind(b"\n") + 1
    complete = payload[:complete_size]
    rows = [json.loads(line) for line in complete.decode("utf-8").splitlines() if line]
    return rows, {
        "path": str(path),
        "snapshot_size_bytes": size,
        "complete_size_bytes": complete_size,
        "complete_sha256": hashlib.sha256(complete).hexdigest(),
        "rows": len(rows),
    }


def _db_identity(path: Path, connection: sqlite3.Connection) -> dict[str, Any]:
    stat = os.stat(path)
    max_fact = connection.execute("SELECT MAX(fact_built_at_utc) FROM fact_trades").fetchone()[0]
    max_settlement = connection.execute("SELECT MAX(target_date) FROM settlements").fetchone()[0]
    return {
        "path": str(path),
        "realpath": str(path.resolve()),
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "size_bytes": stat.st_size,
        "fact_trades_max_fact_built_at_utc": max_fact,
        "settlements_max_target_date": max_settlement,
    }


def _settlement_map(
    connection: sqlite3.Connection, condition_ids: list[str]
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    unique = sorted(set(condition_ids))
    for start in range(0, len(unique), 500):
        chunk = unique[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = connection.execute(
            f"""SELECT condition_id, target_date, bracket, final_price,
                       settlement_status, created_at_utc
                FROM settlements WHERE condition_id IN ({placeholders})""",
            chunk,
        ).fetchall()
        for condition_id, target_date, bracket, final_price, status, created_at in rows:
            output[str(condition_id)] = {
                "target_date": str(target_date),
                "bracket": str(bracket),
                "label": float(final_price),
                "status": str(status),
                "created_at_utc": str(created_at),
            }
    return output


def _fee(price: float) -> float:
    return FEE_RATE * price * (1.0 - price)


def _local_hour(row: pd.Series) -> int:
    timestamp = pd.Timestamp(row["decision_ts_utc"])
    return int(timestamp.tz_convert(ZoneInfo(CITY_TIMEZONE[str(row["city"])])).hour)


def _wilson_interval(wins: int, total: int) -> list[float | None]:
    if total == 0:
        return [None, None]
    z = 1.959963984540054
    p = wins / total
    denominator = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / denominator
    half = z * math.sqrt(p * (1.0 - p) / total + z * z / (4.0 * total * total)) / denominator
    return [center - half, center + half]


def _roi_summary(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "rows": 0,
            "active_target_dates": 0,
            "wins": 0,
            "losses": 0,
            "cost_per_share": 0.0,
            "fee_per_share": 0.0,
            "pnl_per_share": 0.0,
            "roi": None,
            "target_date_bootstrap_ci95": [None, None],
        }
    cost = float(frame["cost_per_share"].sum())
    pnl = float(frame["pnl_per_share"].sum())
    daily = frame.groupby("target_date", sort=True)[["pnl_per_share", "cost_per_share"]].sum()
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    indices = rng.integers(0, len(daily), size=(BOOTSTRAP_DRAWS, len(daily)))
    sampled_pnl = daily["pnl_per_share"].to_numpy()[indices].sum(axis=1)
    sampled_cost = daily["cost_per_share"].to_numpy()[indices].sum(axis=1)
    draws = sampled_pnl / sampled_cost
    low, high = np.quantile(draws, [0.025, 0.975])
    wins = int(frame["label"].sum())
    return {
        "rows": int(len(frame)),
        "active_target_dates": int(frame["target_date"].nunique()),
        "city_dates": int(frame[["city", "target_date"]].drop_duplicates().shape[0]),
        "wins": wins,
        "losses": int(len(frame) - wins),
        "win_rate": wins / len(frame),
        "win_rate_wilson_ci95": _wilson_interval(wins, len(frame)),
        "cost_per_share": cost,
        "fee_per_share": float(frame["fee_per_share"].sum()),
        "pnl_per_share": pnl,
        "five_share_cost_usd": 5.0 * cost,
        "five_share_pnl_usd": 5.0 * pnl,
        "roi": pnl / cost,
        "target_date_bootstrap_ci95": [float(low), float(high)],
    }


def _breakdown(frame: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    output = []
    for value, group in frame.groupby(column, sort=True, dropna=False, observed=True):
        summary = _roi_summary(group)
        output.append({column: str(value), **summary})
    return output


def evaluate(
    *, candidates_path: Path, db_path: Path, artifact_id: str
) -> dict[str, Any]:
    raw_rows, snapshot = _snapshot_jsonl(candidates_path)
    rows = [row for row in raw_rows if str(row.get("model_artifact_id") or "") == artifact_id]
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError(f"model artifact not present in journal: {artifact_id}")
    frame["decision_ts_utc"] = pd.to_datetime(frame["decision_ts_utc"], utc=True)
    frame["local_hour"] = frame.apply(_local_hour, axis=1)
    frame["beijing_hour"] = frame["decision_ts_utc"].dt.tz_convert("Asia/Shanghai").dt.hour

    condition_ids = [str(value) for value in frame["condition_id"].dropna().unique()]
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 1000")
    try:
        identity = _db_identity(db_path, connection)
        settlements = _settlement_map(connection, condition_ids)
    finally:
        connection.close()

    frame["label"] = frame["condition_id"].map(
        lambda value: settlements.get(str(value), {}).get("label") if pd.notna(value) else None
    )
    frame["settlement_status"] = frame["condition_id"].map(
        lambda value: settlements.get(str(value), {}).get("status") if pd.notna(value) else None
    )
    scored = frame[
        frame["candidate_status"].eq("scored")
        & frame["p_model"].notna()
        & frame["market_p"].notna()
    ].copy()
    paired = scored[
        scored["settlement_status"].eq("settled") & scored["label"].isin([0.0, 1.0])
    ].copy()
    labels = paired["label"].astype(int).to_numpy()
    model_probability = paired["p_model"].astype(float).to_numpy()
    market_probability = paired["market_p"].astype(float).to_numpy()
    model_logloss = binary_loss_values(labels, model_probability, metric="logloss")
    market_logloss = binary_loss_values(labels, market_probability, metric="logloss")
    model_brier = binary_loss_values(labels, model_probability, metric="brier")
    market_brier = binary_loss_values(labels, market_probability, metric="brier")

    probability = {
        "rows": int(len(paired)),
        "target_dates": int(paired["target_date"].nunique()),
        "cities": sorted(str(value) for value in paired["city"].unique()),
        "model_logloss": float(np.mean(model_logloss)),
        "market_logloss": float(np.mean(market_logloss)),
        "model_brier": float(np.mean(model_brier)),
        "market_brier": float(np.mean(market_brier)),
        "model_minus_market_logloss": date_block_bootstrap_delta(
            paired, model_logloss, market_logloss, draws=BOOTSTRAP_DRAWS, seed=BOOTSTRAP_SEED
        ),
        "model_minus_market_brier": date_block_bootstrap_delta(
            paired, model_brier, market_brier, draws=BOOTSTRAP_DRAWS, seed=BOOTSTRAP_SEED + 1
        ),
    }

    selected = paired[paired["selected"].eq(True)].copy()
    selected["ask"] = selected["executable_cost"].astype(float)
    selected["fee_per_share"] = selected.apply(
        lambda row: float((row.get("metadata") or {}).get("official_weather_fee_per_share") or _fee(row["ask"])),
        axis=1,
    )
    selected["cost_per_share"] = selected["ask"] + selected["fee_per_share"]
    selected["pnl_per_share"] = selected["label"] - selected["cost_per_share"]
    selected["price_bucket"] = pd.cut(
        selected["ask"],
        bins=[-np.inf, 0.60, 0.95, 0.98, 1.0 + 1e-9],
        labels=["<=0.60", "0.60-0.95", "0.95-0.98", ">0.98"],
        right=True,
    )
    trade = _roi_summary(selected)
    if not selected.empty:
        top = selected.sort_values("pnl_per_share", ascending=False).iloc[0]
        without_top = selected.drop(index=top.name)
        trade["largest_winner"] = {
            "city": str(top["city"]),
            "target_date": str(top["target_date"]),
            "local_hour": int(top["local_hour"]),
            "ask": float(top["ask"]),
            "pnl_per_share": float(top["pnl_per_share"]),
            "share_of_total_pnl": float(top["pnl_per_share"] / selected["pnl_per_share"].sum()),
        }
        trade["top_winner_removed"] = _roi_summary(without_top)
        stressed_pnl = float(selected["pnl_per_share"].sum()) - 1.0
        trade["one_additional_loss_stress"] = {
            "pnl_per_share": stressed_pnl,
            "roi": stressed_pnl / float(selected["cost_per_share"].sum()),
            "note": "changing any one binary win to a loss reduces portfolio PnL by exactly $1/share",
        }

    selected_entries = []
    for _, row in selected.sort_values(["decision_ts_utc", "candidate_id"]).iterrows():
        selected_entries.append(
            {
                "city": str(row["city"]),
                "target_date": str(row["target_date"]),
                "decision_ts_utc": row["decision_ts_utc"].isoformat(),
                "decision_ts_beijing": row["decision_ts_utc"].tz_convert("Asia/Shanghai").isoformat(),
                "local_hour": int(row["local_hour"]),
                "bracket": str(row["bracket"]),
                "condition_id": str(row["condition_id"]),
                "market_p": float(row["market_p"]),
                "p_model": float(row["p_model"]),
                "ask": float(row["ask"]),
                "fee_per_share": float(row["fee_per_share"]),
                "label": int(row["label"]),
                "pnl_per_share": float(row["pnl_per_share"]),
            }
        )

    all_dates = sorted(str(value) for value in frame["target_date"].unique())
    selected_dates = sorted(str(value) for value in selected["target_date"].unique())
    blocker_counts = Counter(str(value) for value in frame.loc[frame["candidate_status"].eq("blocked"), "blocker_reason"])
    market_available = frame["market_evidence_status"].eq("available")
    duplicate_city_dates = (
        selected.groupby(["city", "target_date"]).size().loc[lambda values: values > 1]
        if not selected.empty
        else pd.Series(dtype=int)
    )

    logloss_ci = probability["model_minus_market_logloss"]
    brier_ci = probability["model_minus_market_brier"]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "strategy_identity": {
            "instance_id": "weather_tmin_no_further_cooling_shadow_v1",
            "strategy_key": "weather.tmin.no_further_cooling",
            "model_id": str(frame["model_id"].iloc[0]),
            "model_artifact_id": artifact_id,
            "policy_id": str(frame["policy_id"].iloc[0]),
            "execution_mode": "zero_notional_shadow",
        },
        "input_snapshot": snapshot,
        "canonical_db": identity,
        "denominator_scope": {
            "unit": "fixed local checkpoint current-exact-YES expression",
            "start_target_date": min(all_dates),
            "end_target_date": max(all_dates),
            "rows": int(len(frame)),
            "target_dates": len(all_dates),
            "cities": sorted(str(value) for value in frame["city"].unique()),
            "active_trade_dates": len(selected_dates),
            "trade_class": "zero_notional_shadow_hypothetical",
            "settlement_join": "condition_id",
        },
        "signal_funnel": {
            "artifact_checkpoint_rows": int(len(frame)),
            "scored_rows": int(len(scored)),
            "positive_edge_policy_selected_rows": int(frame["selected"].eq(True).sum()),
            "selected_settled_rows": int(len(selected)),
            "selected_city_date_duplicates_within_artifact": int(len(duplicate_city_dates)),
        },
        "evidence_funnel": {
            "pit_observation_and_forecast_refs": int(
                frame["input_refs"].map(
                    lambda refs: {str(item.get("kind")) for item in (refs or [])}
                    >= {"observation_archive", "forecast_curve"}
                ).sum()
            ),
            "market_evidence_available": int(market_available.sum()),
            "scored_with_binary_settlement": int(len(paired)),
            "selected_with_direct_ask": int(selected["ask"].notna().sum()),
            "selected_with_recorded_depth": 0,
            "actual_fills": 0,
            "unsettled_scored_rows": int(len(scored) - len(paired)),
        },
        "blocker_reason_counts": dict(sorted(blocker_counts.items())),
        "probability_quality": probability,
        "fee_adjusted_trade_performance": trade,
        "entry_breakdown": {
            "by_city": _breakdown(selected, "city"),
            "by_local_hour": _breakdown(selected, "local_hour"),
            "by_beijing_hour": _breakdown(selected, "beijing_hour"),
            "by_ask_bucket": _breakdown(selected, "price_bucket"),
        },
        "selected_entries": selected_entries,
        "multiple_testing": {
            "policy_variants_tested_in_this_review": 1,
            "descriptive_slices": int(selected["city"].nunique() + selected["local_hour"].nunique() + selected["beijing_hour"].nunique() + selected["price_bucket"].nunique()),
            "correction": "none; slices are attribution only and are not promotion selectors",
        },
        "gates": {
            "same_denominator_probability_baseline": (
                "PASS" if logloss_ci["ci_high"] < 0 and brier_ci["ci_high"] < 0 else "FAIL"
            ),
            "fee_adjusted_significance": (
                "PROVISIONAL_PASS_DEGENERATE_ALL_WIN_SAMPLE"
                if trade["target_date_bootstrap_ci95"][0] is not None
                and trade["target_date_bootstrap_ci95"][0] > 0
                else "FAIL"
            ),
            "frozen_forward_min_30_target_dates": (
                "PASS" if len(all_dates) >= 30 else f"FAIL_{len(all_dates)}_OF_30_TARGET_DATES"
            ),
            "direct_execution_depth": "FAIL_NO_RECORDED_ASK_DEPTH_OR_ACTUAL_FILLS",
        },
        "decision": "inconclusive_keep_zero_notional_shadow_do_not_promote_live",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--artifact-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = evaluate(
        candidates_path=args.candidates,
        db_path=args.db,
        artifact_id=args.artifact_id,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
