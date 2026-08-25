#!/usr/bin/env python3
"""Evaluate the frozen Tmin no-further-cooling zero-notional shadow.

The runtime journal is snapshotted at read time and filtered to one immutable
model artifact.  Settlement truth is joined by condition_id from the canonical
weather DB; city/date/bracket joins are deliberately not supported.
"""

from __future__ import annotations

import argparse
import gzip
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


SCHEMA_VERSION = "weather_tmin_no_further_cooling_shadow_performance_v2"
FEE_RATE = 0.05
BOOTSTRAP_DRAWS = 20_000
BOOTSTRAP_SEED = 20260824


def _snapshot_jsonl(
    path: Path, *, snapshot_size_bytes: int | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_size = path.stat().st_size
    size = source_size if snapshot_size_bytes is None else min(source_size, snapshot_size_bytes)
    with path.open("rb") as handle:
        payload = handle.read(size)
    complete_size = len(payload) if payload.endswith(b"\n") else payload.rfind(b"\n") + 1
    complete = payload[:complete_size]
    rows = [json.loads(line) for line in complete.decode("utf-8").splitlines() if line]
    return rows, {
        "path": str(path),
        "source_size_at_read_bytes": source_size,
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


def _execution_book_evidence(row: pd.Series, shares: float = 5.0) -> dict[str, Any]:
    """Reconstruct the exact PIT YES book referenced by one candidate."""

    references = [
        item
        for item in (row.get("input_refs") or [])
        if str(item.get("kind") or "") == "market_book_batch"
    ]
    if not references:
        return {"book_evidence_status": "missing_market_book_ref"}
    path = Path(str(references[-1].get("path") or ""))
    if not path.exists():
        return {"book_evidence_status": "missing_market_book_file", "book_path": str(path)}

    decision = pd.Timestamp(row["decision_ts_utc"])
    matches: list[tuple[pd.Timestamp, dict[str, Any]]] = []
    with gzip.open(path, mode="rt", encoding="utf-8") as handle:
        for line in handle:
            candidate = json.loads(line)
            if (
                str(candidate.get("condition_id") or "") != str(row.get("condition_id") or "")
                or str(candidate.get("outcome") or "").lower() != "yes"
            ):
                continue
            available_raw = candidate.get("available_at_utc") or candidate.get("fetched_at_utc")
            if not available_raw:
                continue
            available = pd.Timestamp(available_raw)
            if available.tzinfo is None:
                available = available.tz_localize("UTC")
            else:
                available = available.tz_convert("UTC")
            if available <= decision:
                matches.append((available, candidate))
    if not matches:
        return {"book_evidence_status": "no_pit_yes_book_match", "book_path": str(path)}

    available, source = max(matches, key=lambda item: item[0])
    summary = source.get("summary") or {}
    levels = summary.get("asks") or (source.get("raw") or {}).get("asks") or []
    asks = sorted(
        (
            {"price": float(level["price"]), "size": float(level["size"])}
            for level in levels
            if level.get("price") is not None and level.get("size") is not None
        ),
        key=lambda level: level["price"],
    )
    remaining = float(shares)
    notional = 0.0
    fees = 0.0
    for level in asks:
        take = min(remaining, level["size"])
        notional += take * level["price"]
        fees += take * _fee(level["price"])
        remaining -= take
        if remaining <= 1e-12:
            break
    fillable = remaining <= 1e-12
    best_ask = None if not asks else asks[0]["price"]
    candidate_ask = float(row["executable_cost"])
    top_matches_candidate = best_ask is not None and abs(best_ask - candidate_ask) <= 1e-12
    return {
        "book_evidence_status": "available",
        "book_path": str(path),
        "book_capture_id": source.get("book_capture_id"),
        "execution_snapshot_id_matches_book_capture_id": (
            str(row.get("execution_book_snapshot_id") or "")
            == str(source.get("book_capture_id") or "")
        ),
        "book_available_at_utc": available.isoformat(),
        "book_age_minutes": float((decision - available).total_seconds() / 60.0),
        "best_ask": best_ask,
        "best_ask_size": None if not asks else asks[0]["size"],
        "depth_ask_5c": summary.get("depth_ask_5c"),
        "depth_ask_10c": summary.get("depth_ask_10c"),
        "spread": summary.get("spread"),
        "candidate_ask_matches_book": top_matches_candidate,
        "five_share_fillable": fillable,
        "five_share_vwap": notional / shares if fillable else None,
        "five_share_fee_per_share": fees / shares if fillable else None,
        "five_share_slippage_vs_candidate_ask": (
            notional / shares - candidate_ask if fillable else None
        ),
    }


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
    *,
    candidates_path: Path,
    db_path: Path,
    artifact_id: str,
    snapshot_size_bytes: int | None = None,
) -> dict[str, Any]:
    raw_rows, snapshot = _snapshot_jsonl(
        candidates_path, snapshot_size_bytes=snapshot_size_bytes
    )
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
    book_evidence = pd.DataFrame(
        [_execution_book_evidence(row) for _, row in selected.iterrows()], index=selected.index
    )
    selected = selected.join(book_evidence)
    executable_selected = selected[
        selected["book_evidence_status"].eq("available")
        & selected["candidate_ask_matches_book"].eq(True)
        & selected["five_share_fillable"].eq(True)
    ].copy()
    executable_selected["fee_per_share"] = executable_selected["five_share_fee_per_share"].astype(float)
    executable_selected["cost_per_share"] = (
        executable_selected["five_share_vwap"].astype(float)
        + executable_selected["fee_per_share"]
    )
    executable_selected["pnl_per_share"] = (
        executable_selected["label"] - executable_selected["cost_per_share"]
    )
    executable_selected["price_bucket"] = pd.cut(
        executable_selected["ask"],
        bins=[-np.inf, 0.60, 0.95, 0.98, 1.0 + 1e-9],
        labels=["<=0.60", "0.60-0.95", "0.95-0.98", ">0.98"],
        right=True,
    )
    trade = _roi_summary(executable_selected)
    trade["execution_evidence_class"] = "pit_rest_full_book_static_snapshot"
    trade["selected_with_reconstructable_book"] = int(
        selected["book_evidence_status"].eq("available").sum()
    )
    trade["selected_with_raw_book_capture_id_persisted_as_execution_snapshot_id"] = int(
        selected["execution_snapshot_id_matches_book_capture_id"].eq(True).sum()
    )
    trade["selected_five_share_fillable_at_snapshot"] = int(len(executable_selected))
    trade["selected_five_share_at_best_ask_without_slippage"] = int(
        executable_selected["five_share_slippage_vs_candidate_ask"].abs().le(1e-12).sum()
    )
    trade["min_best_ask_size"] = (
        None if executable_selected.empty else float(executable_selected["best_ask_size"].min())
    )
    trade["median_best_ask_size"] = (
        None if executable_selected.empty else float(executable_selected["best_ask_size"].median())
    )
    trade["max_book_age_minutes"] = (
        None if executable_selected.empty else float(executable_selected["book_age_minutes"].max())
    )
    trade["actual_fills"] = 0
    trade["execution_limit"] = (
        "static PIT depth supports the hypothetical 5-share sweep; zero-notional shadow does not "
        "establish post-decision liquidity, queue, latency, or an actual fill"
    )
    if not executable_selected.empty:
        top = executable_selected.sort_values("pnl_per_share", ascending=False).iloc[0]
        without_top = executable_selected.drop(index=top.name)
        trade["largest_winner"] = {
            "city": str(top["city"]),
            "target_date": str(top["target_date"]),
            "local_hour": int(top["local_hour"]),
            "ask": float(top["ask"]),
            "pnl_per_share": float(top["pnl_per_share"]),
            "share_of_total_pnl": float(
                top["pnl_per_share"] / executable_selected["pnl_per_share"].sum()
            ),
        }
        trade["top_winner_removed"] = _roi_summary(without_top)
        stressed_pnl = float(executable_selected["pnl_per_share"].sum()) - 1.0
        trade["one_additional_loss_stress"] = {
            "pnl_per_share": stressed_pnl,
            "roi": stressed_pnl / float(executable_selected["cost_per_share"].sum()),
            "note": "changing any one binary win to a loss reduces portfolio PnL by exactly $1/share",
        }

    selected_entries = []
    for _, row in executable_selected.sort_values(["decision_ts_utc", "candidate_id"]).iterrows():
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
                "best_ask_size": float(row["best_ask_size"]),
                "depth_ask_5c": float(row["depth_ask_5c"]),
                "book_capture_id": str(row["book_capture_id"]),
                "candidate_execution_book_snapshot_id": str(
                    row["execution_book_snapshot_id"]
                ),
                "execution_snapshot_id_matches_book_capture_id": bool(
                    row["execution_snapshot_id_matches_book_capture_id"]
                ),
                "book_age_minutes": float(row["book_age_minutes"]),
                "five_share_vwap": float(row["five_share_vwap"]),
                "five_share_slippage_vs_candidate_ask": float(
                    row["five_share_slippage_vs_candidate_ask"]
                ),
                "fee_per_share": float(row["fee_per_share"]),
                "label": int(row["label"]),
                "pnl_per_share": float(row["pnl_per_share"]),
            }
        )

    all_dates = sorted(str(value) for value in frame["target_date"].unique())
    settled_scored_dates = sorted(str(value) for value in paired["target_date"].unique())
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
            "settled_scored_target_dates": len(settled_scored_dates),
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
            "selected_with_reconstructable_full_book": int(
                selected["book_evidence_status"].eq("available").sum()
            ),
            "selected_with_recorded_depth": int(
                selected["best_ask_size"].notna().sum()
            ),
            "selected_five_share_fillable_at_snapshot": int(len(executable_selected)),
            "actual_fills": 0,
            "unsettled_scored_rows": int(len(scored) - len(paired)),
        },
        "blocker_reason_counts": dict(sorted(blocker_counts.items())),
        "probability_quality": probability,
        "fee_adjusted_trade_performance": trade,
        "entry_breakdown": {
            "by_city": _breakdown(executable_selected, "city"),
            "by_local_hour": _breakdown(executable_selected, "local_hour"),
            "by_beijing_hour": _breakdown(executable_selected, "beijing_hour"),
            "by_ask_bucket": _breakdown(executable_selected, "price_bucket"),
        },
        "selected_entries": selected_entries,
        "multiple_testing": {
            "policy_variants_tested_in_this_review": 1,
            "descriptive_slices": int(
                executable_selected["city"].nunique()
                + executable_selected["local_hour"].nunique()
                + executable_selected["beijing_hour"].nunique()
                + executable_selected["price_bucket"].nunique()
            ),
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
            "frozen_forward_min_30_settled_target_dates": (
                "PASS"
                if len(settled_scored_dates) >= 30
                else f"FAIL_{len(settled_scored_dates)}_OF_30_SETTLED_TARGET_DATES"
            ),
            "direct_execution_depth": (
                f"PASS_STATIC_PIT_5_SHARE_DEPTH_{len(executable_selected)}_OF_{len(selected)}"
                if len(executable_selected) == len(selected) and len(selected) > 0
                else f"FAIL_STATIC_PIT_5_SHARE_DEPTH_{len(executable_selected)}_OF_{len(selected)}"
            ),
            "actual_fill_or_post_decision_execution": (
                "FAIL_ZERO_NOTIONAL_NO_ACTUAL_FILL_OR_POST_DECISION_EXECUTION_BOOK"
            ),
            "execution_book_lineage_identity": (
                "PARTIAL_BATCH_HASH_AND_INPUT_REF_RESOLVE_RAW_BOOK_BUT_BOOK_CAPTURE_ID_NOT_PERSISTED"
            ),
        },
        "decision": "inconclusive_keep_zero_notional_shadow_do_not_promote_live",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--artifact-id", required=True)
    parser.add_argument(
        "--snapshot-size-bytes",
        type=int,
        help="Freeze an append-only journal at an already recorded byte boundary.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = evaluate(
        candidates_path=args.candidates,
        db_path=args.db,
        artifact_id=args.artifact_id,
        snapshot_size_bytes=args.snapshot_size_bytes,
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
