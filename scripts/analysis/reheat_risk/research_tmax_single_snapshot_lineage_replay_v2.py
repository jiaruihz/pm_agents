#!/usr/bin/env python3
"""Strict single-snapshot lineage replay for the legacy Tmax models.

This is a read-only data-lineage audit.  A state is selected at
``city/target_date/local_hour`` by taking the *last captured paper snapshot*
for that grain, before inspecting its ladder or quote completeness.  Every
rung, forecast and path field used by the state therefore comes from that one
payload.  No atlas/P3 field, city-date forecast reconstruction, future archive
weather, or full-window city effect is permitted.

The old full-feature and coherent-quote outputs are deliberately not treated
as clean predictions: their required path features were assembled outside the
saved paper snapshot.  They are retained only as the historical mixed
denominator used for the contamination-radius audit.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import joblib
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.market_brackets import MarketBracket, parse_market_bracket  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_single_snapshot_lineage_replay_v2"
JSON_PATH = ROOT / "docs/analysis/2026-07/2026-07-11-tmax-single-snapshot-lineage-replay-v2.json"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-11-tmax-single-snapshot-lineage-replay-v2.md"
DB_PATH = ROOT / "runtime/weather.db"
OLD_FULL_POLICY = ROOT / "docs/analysis/2026-07/generated/tmax_lineage_repair_replay_v1/policy_replay_rows.csv"
OLD_COHERENT_POLICY = ROOT / "docs/analysis/2026-07/generated/tmax_coherent_expression_calibrator_v1/policy_rows.csv"

# Later roots take precedence when the same captured timestamp exists.
SNAPSHOT_ROOTS = [
    ROOT / "runtime/n100_recovery_20260705/weather_data_feed_service_runtime/output/paper_snapshots",
    Path("/Volumes/jrs/weather_data_feed_service_runtime/targeted_output/paper_snapshots"),
    ROOT / "runtime/weather_edge_v1/market_data/paper_snapshots",
]

FORWARD_START = "2026-06-21"
FEE_RATE = 0.05
ASK_FLOOR = 0.40
ASK_CEILING = 0.99
EDGE_THRESHOLD = 0.02
ACTIVE_EXPRESSIONS = ("current_no", "d1_no", "d2_no", "d1_yes", "d2_yes")
BUCKETS = ("below", "current", "d1", "d2", "tail")

# These values are supplied by source-event/forecast-enrichment reconstruction
# in the legacy feature builder, not by a saved paper snapshot record.
RAW_PARITY_MISSING_FIELDS = (
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "relative_humidity_pct",
    "wind_speed_kt",
    "sky_cover_code",
    "minutes_since_running_max",
    "gfs_gap_to_running_native",
    "ecmwf_gap_to_running_native",
    "forecast_peak_hour_spread",
)
EPS = 1e-9
UNIT_INVARIANT_FEATURES = [
    "forecast_gap_to_running_f", "current_minus_running_f", "forecast_peak_delta_hours_local",
    "temp_trend_1h_f", "temp_trend_3h_f",
    "max_age_min", "minutes_since_running_max",
    *[f"market_p_{bucket}" for bucket in BUCKETS],
]
FEATURE_SPECS = {
    "unit_invariant_no_decision_hour": UNIT_INVARIANT_FEATURES,
    "unit_invariant_with_decision_hour_transition": ["decision_hour_local", *UNIT_INVARIANT_FEATURES],
}
STATE_RECORD_FIELDS = (
    "bracket", "question", "city", "target_date", "event_date", "ts_local", "unit",
    "metar_current_max_f", "metar_latest_temp_f", "metar_latest_ts_utc", "metar_obs_count_today", "metar_icao",
    "forecast_max_native", "forecast_source",
    "forecast_peak_hour_local", "forecast_peak_delta_hours_local",
    "yes_best_bid", "yes_best_ask", "no_best_bid", "no_best_ask",
    "yes_bid_size", "yes_ask_size", "yes_depth_ask_5c", "yes_depth_ask_10c",
    "no_bid_size", "no_ask_size", "no_depth_ask_5c", "no_depth_ask_10c",
)


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def parse_utc(value: Any) -> pd.Timestamp | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = pd.Timestamp(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("UTC")
    else:
        parsed = parsed.tz_convert("UTC")
    return parsed


def native_running_max(record: dict[str, Any]) -> float | None:
    value_f = finite(record.get("metar_current_max_f"))
    unit = str(record.get("unit") or "").upper()
    if value_f is None or unit not in {"C", "F"}:
        return None
    return value_f if unit == "F" else (value_f - 32.0) * 5.0 / 9.0


def bracket_key(record: dict[str, Any]) -> str | None:
    parsed = parse_market_bracket(str(record.get("bracket") or ""), str(record.get("question") or ""))
    return parsed.label if parsed is not None else None


def bracket_sort_key(record: dict[str, Any]) -> tuple[float, float]:
    parsed = parse_market_bracket(str(record.get("bracket") or ""), str(record.get("question") or ""))
    if parsed is None:
        return (math.inf, math.inf)
    low = -math.inf if parsed.bottom else float(parsed.low) if parsed.low is not None else math.inf
    high = math.inf if parsed.top else float(parsed.high) if parsed.high is not None else math.inf
    return (low, high)


def interval_contains(parsed: MarketBracket, value: float) -> bool:
    lower_ok = True if parsed.bottom else parsed.low is not None and value >= float(parsed.low) - 1e-9
    upper_ok = True if parsed.top else parsed.high is not None and value <= float(parsed.high) + 1e-9
    return bool(lower_ok and upper_ok)


def yes_quote(record: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    yes_bid = finite(record.get("yes_best_bid"))
    yes_ask = finite(record.get("yes_best_ask"))
    no_bid = finite(record.get("no_best_bid"))
    no_ask = finite(record.get("no_best_ask"))
    bid_candidates = [item for item in (yes_bid, None if no_ask is None else 1.0 - no_ask) if item is not None]
    ask_candidates = [item for item in (yes_ask, None if no_bid is None else 1.0 - no_bid) if item is not None]
    bid = max(bid_candidates) if bid_candidates else None
    ask = min(ask_candidates) if ask_candidates else None
    mid = (bid + ask) / 2.0 if bid is not None and ask is not None else bid if bid is not None else ask
    return bid, ask, mid


def normalize(weights: dict[str, float]) -> dict[str, float]:
    clipped = {key: max(EPS, float(value)) for key, value in weights.items()}
    total = sum(clipped.values())
    return {key: value / total for key, value in clipped.items()}


def official_fee(price: float) -> float:
    return FEE_RATE * price * (1.0 - price)


def snapshot_inventory() -> tuple[list[dict[str, Any]], pd.DataFrame, dict[str, int]]:
    """Stream raw payloads and retain only the pre-registered selected pointer.

    Paper snapshots can be large.  Keeping all payloads in a DataFrame makes
    the lineage audit itself memory-prohibitive, so the first pass retains only
    a file pointer/timestamp for the last candidate at each state grain.
    """
    selected: dict[tuple[str, str, int], tuple[pd.Timestamp, int, Path, list[dict[str, Any]]]] = {}
    inventory: list[dict[str, Any]] = []
    candidate_state_rows = 0
    readable_payloads = 0
    for priority, root in enumerate(SNAPSHOT_ROOTS):
        files = sorted(root.glob("snapshot_*.json")) if root.exists() else []
        inventory.append({"root": str(root), "files": len(files), "exists": root.exists(), "priority": priority})
        for path in files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            ts = parse_utc(payload.get("ts_utc"))
            if ts is None:
                continue
            readable_payloads += 1
            grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
            for record in payload.get("records", []):
                city = str(record.get("city") or "")
                target_date = str(record.get("target_date") or record.get("event_date") or "")
                local_ts = str(record.get("ts_local") or "")
                try:
                    local_hour = int(local_ts[11:13])
                except (TypeError, ValueError):
                    continue
                if city and target_date:
                    compact = {field: record.get(field) for field in STATE_RECORD_FIELDS}
                    grouped.setdefault((city, target_date, local_hour), []).append(compact)
            candidate_state_rows += len(grouped)
            for grain, records in grouped.items():
                previous = selected.get(grain)
                if previous is None or (ts, priority) >= (previous[0], previous[1]):
                    selected[grain] = (ts, priority, path, records)

    rows = [
        {
            "city": city,
            "target_date": target_date,
            "decision_hour_local": local_hour,
            "snapshot_ts_utc": ts.isoformat(),
            "snapshot_path": str(path),
            "snapshot_root_priority": priority,
            "records": records,
        }
        for (city, target_date, local_hour), (ts, priority, path, records) in selected.items()
    ]
    return inventory, pd.DataFrame(rows), {"candidate_state_rows": candidate_state_rows, "readable_payloads": readable_payloads}


def load_settlement_winners() -> dict[tuple[str, str], str]:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    try:
        rows = conn.execute(
            """
            SELECT city, target_date, bracket, question
            FROM settlement_outcomes
            WHERE settlement_status = 'settled' AND final_price > 0.5
            """
        ).fetchall()
    finally:
        conn.close()
    winners: dict[tuple[str, str], str] = {}
    duplicate: Counter[tuple[str, str]] = Counter()
    for city, target_date, bracket, question in rows:
        parsed = parse_market_bracket(str(bracket or ""), str(question or ""))
        if parsed is None:
            continue
        key = (str(city), str(target_date))
        duplicate[key] += 1
        winners[key] = parsed.label
    return {key: winner for key, winner in winners.items() if duplicate[key] == 1}


def selected_state(candidate: pd.Series, winners: dict[tuple[str, str], str]) -> dict[str, Any]:
    """Construct the relative state from a single already-selected payload."""
    city, target_date = str(candidate["city"]), str(candidate["target_date"])
    records = list(candidate["records"])
    ladder_by_key: dict[str, dict[str, Any]] = {}
    for record in records:
        key = bracket_key(record)
        if key is not None:
            ladder_by_key.setdefault(key, record)
    ladder = sorted(ladder_by_key.values(), key=bracket_sort_key)
    base: dict[str, Any] = {
        "city": city,
        "target_date": target_date,
        "decision_hour_local": int(candidate["decision_hour_local"]),
        "snapshot_ts_utc": candidate["snapshot_ts_utc"],
        "snapshot_path": candidate["snapshot_path"],
        "snapshot_root_priority": int(candidate["snapshot_root_priority"]),
        "snapshot_record_count": len(records),
        "snapshot_distinct_rungs": len(ladder),
        "rung_snapshot_ts_unique": 1,
        "pre_registered_selection": "last_snapshot_per_city_date_local_hour",
        "state_status": "unknown",
    }
    if not ladder:
        return {**base, "state_status": "no_parseable_rungs"}
    running = native_running_max(ladder[0])
    if running is None:
        return {**base, "state_status": "missing_saved_running_max"}
    anchor_index = None
    for index, record in enumerate(ladder):
        parsed = parse_market_bracket(str(record.get("bracket") or ""), str(record.get("question") or ""))
        if parsed is not None and interval_contains(parsed, running):
            anchor_index = index
            break
    if anchor_index is None or anchor_index + 2 >= len(ladder):
        return {**base, "state_status": "insufficient_saved_rungs_after_current", "running_native": running}

    current, d1, d2 = ladder[anchor_index : anchor_index + 3]
    current_key, d1_key, d2_key = (bracket_key(item) for item in (current, d1, d2))
    if len({current_key, d1_key, d2_key}) != 3:
        return {**base, "state_status": "invalid_anchor_rung_geometry", "running_native": running}

    current_bid, current_yes_ask, current_mid = yes_quote(current)
    d1_yes_bid, d1_yes_ask, d1_mid = yes_quote(d1)
    d2_yes_bid, d2_yes_ask, d2_mid = yes_quote(d2)
    no_asks = [finite(item.get("no_best_ask")) for item in (current, d1, d2)]
    no_bids = [finite(item.get("no_best_bid")) for item in (current, d1, d2)]
    quote_ready = all(value is not None for value in [current_mid, d1_mid, d2_mid, *no_asks, *no_bids])
    winner = winners.get((city, target_date))
    if winner is None:
        actual_bucket, label_status = None, "missing_or_ambiguous_settlement_winner"
    elif winner == current_key:
        actual_bucket, label_status = "current", "settlement_label"
    elif winner == d1_key:
        actual_bucket, label_status = "d1", "settlement_label"
    elif winner == d2_key:
        actual_bucket, label_status = "d2", "settlement_label"
    else:
        winner_record = next((item for item in ladder if bracket_key(item) == winner), None)
        winner_pos = ladder.index(winner_record) if winner_record is not None else None
        if winner_pos is not None and winner_pos > anchor_index + 2:
            actual_bucket, label_status = "tail", "settlement_label"
        else:
            actual_bucket, label_status = None, "settlement_winner_not_in_forward_relative_ladder"

    market_probs = None
    if quote_ready:
        tail_mids = [yes_quote(item)[2] for item in ladder[anchor_index + 3 :]]
        if all(value is not None for value in tail_mids):
            market_probs = normalize(
                {
                    "current": float(current_mid),
                    "d1": float(d1_mid),
                    "d2": float(d2_mid),
                    "tail": max(EPS, sum(float(value) for value in tail_mids)),
                }
            )

    field_presence = {field: field in current and finite(current.get(field)) is not None for field in RAW_PARITY_MISSING_FIELDS}
    parity_missing = [field for field, present in field_presence.items() if not present]
    status = "ready_market_local" if quote_ready and actual_bucket is not None and market_probs is not None else "missing_quote_or_settlement_label"
    return {
        **base,
        "state_status": status,
        "label_status": label_status,
        "actual_bucket": actual_bucket,
        "settlement_winning_bracket_label": winner,
        "unit": str(current.get("unit") or ""),
        "running_native": running,
        "forecast_max_native_saved": finite(current.get("forecast_max_native")),
        "forecast_source_saved": current.get("forecast_source"),
        "forecast_peak_hour_local_saved": finite(current.get("forecast_peak_hour_local")),
        "forecast_peak_delta_hours_local_saved": finite(current.get("forecast_peak_delta_hours_local")),
        "current_bracket": current_key,
        "d1_bracket": d1_key,
        "d2_bracket": d2_key,
        "current_yes_ask": current_yes_ask,
        "current_no_ask": no_asks[0],
        "current_no_bid": no_bids[0],
        "d1_no_ask": no_asks[1],
        "d1_no_bid": no_bids[1],
        "d2_no_ask": no_asks[2],
        "d2_no_bid": no_bids[2],
        "d1_yes_effective_ask": d1_yes_ask,
        "d2_yes_effective_ask": d2_yes_ask,
        "market_p_current": None if market_probs is None else market_probs["current"],
        "market_p_d1": None if market_probs is None else market_probs["d1"],
        "market_p_d2": None if market_probs is None else market_probs["d2"],
        "market_p_tail": None if market_probs is None else market_probs["tail"],
        "raw_parity_missing_fields": "|".join(parity_missing),
        "raw_full_feature_parity": not parity_missing,
        "raw_coherent_quote_parity": not parity_missing,
        "policy_path_field_available": False,
        "policy_not_run_reason": "saved_paper_snapshot_has_no_temp_trend_3h_f",
    }


def score_market_local(states: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    eligible = states[states["state_status"].eq("ready_market_local")].copy()
    if eligible.empty:
        return eligible, pd.DataFrame(columns=["scope", "rows", "dates", "logloss", "brier"])
    probs = eligible[[f"market_p_{bucket}" for bucket in BUCKETS]].to_numpy(dtype=float)
    bucket_index = {bucket: idx for idx, bucket in enumerate(BUCKETS)}
    actual = eligible["actual_bucket"].map(bucket_index).to_numpy(dtype=int)
    winner = probs[np.arange(len(eligible)), actual]
    target = np.eye(len(BUCKETS))[actual]
    eligible["market_local_logloss"] = -np.log(np.maximum(EPS, winner))
    eligible["market_local_brier"] = np.square(probs - target).sum(axis=1)
    eligible["scope"] = np.where(eligible["target_date"].lt(FORWARD_START), "inner_pre_2026_06_21", "retrospective_expanding_walk_forward_diagnostic")
    daily = (
        eligible.groupby(["scope", "target_date"], as_index=False)
        .agg(rows=("city", "size"), cities=("city", "nunique"), logloss=("market_local_logloss", "mean"), brier=("market_local_brier", "mean"))
        .sort_values(["scope", "target_date"])
    )
    summary = (
        eligible.groupby("scope", as_index=False)
        .agg(rows=("city", "size"), dates=("target_date", "nunique"), logloss=("market_local_logloss", "mean"), brier=("market_local_brier", "mean"))
    )
    return eligible, pd.concat([summary.assign(target_date="ALL", cities=np.nan), daily], ignore_index=True, sort=False)


def old_mixed_policy() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path, source, variants in [
        (OLD_FULL_POLICY, "legacy_full_feature_replay", {"historical_full_features"}),
        (OLD_COHERENT_POLICY, "legacy_coherent_quote_replay", {"coherent_cal_quote"}),
    ]:
        if not path.exists():
            continue
        frame = pd.read_csv(path, low_memory=False)
        frame = frame[frame["variant"].isin(variants)].copy()
        frame["old_source"] = source
        frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["old_source", "city", "target_date", "decision_hour_local", "expression", "pnl", "cost"])
    return pd.concat(frames, ignore_index=True)


def contamination_transitions(old: pd.DataFrame, states: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    clean = states.drop(columns=["records"], errors="ignore").copy()
    keys = ["city", "target_date", "decision_hour_local"]
    joined = old.merge(clean, on=keys, how="left", suffixes=("_old", "_clean"), indicator=True)
    joined["clean_replay_expression"] = ""
    joined["clean_replay_pnl"] = 0.0
    joined["transition"] = np.where(
        joined["_merge"].eq("left_only"),
        "cancelled_no_clean_last_snapshot_state",
        "cancelled_policy_path_not_saved",
    )
    joined["pnl_delta_clean_minus_old"] = joined["clean_replay_pnl"] - pd.to_numeric(joined["pnl"], errors="coerce").fillna(0.0)
    summary = (
        joined.groupby(["old_source", "transition"], as_index=False)
        .agg(
            old_rows=("city", "size"),
            dates=("target_date", "nunique"),
            old_pnl=("pnl", "sum"),
            old_cost=("cost", "sum"),
            clean_rows=("clean_replay_expression", lambda x: int(x.ne("").sum())),
            pnl_delta_clean_minus_old=("pnl_delta_clean_minus_old", "sum"),
        )
        .sort_values(["old_source", "transition"])
    )
    summary["added_clean_rows"] = 0
    summary["switched_expression_rows"] = 0
    return joined, summary


def markdown_table(frame: pd.DataFrame, columns: list[str]) -> list[str]:
    if frame.empty:
        return ["_No rows._"]
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in frame.reindex(columns=columns).to_dict("records"):
        values = []
        for value in row.values():
            if value is None or (isinstance(value, float) and not math.isfinite(value)):
                values.append("NA")
            elif isinstance(value, (float, np.floating)):
                values.append(f"{float(value):.4f}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return lines


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    if isinstance(value, (np.integer, np.floating)):
        return json_ready(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _legacy_four_bucket_run_disabled(output_dir: Path) -> dict[str, Any]:
    raise RuntimeError("legacy four-bucket/no-rows report path is disabled; use run_review_v3")
    # Kept temporarily below only as inert provenance while v2 artifacts are reviewed.
    output_dir.mkdir(parents=True, exist_ok=True)
    roots, candidates, inventory_counts = snapshot_inventory()
    if candidates.empty:
        raise RuntimeError("No readable raw paper snapshot candidate state")

    # ``snapshot_inventory`` already applied the selection before this point.
    chosen = candidates.copy()
    winners = load_settlement_winners()
    states = pd.DataFrame([selected_state(row, winners) for _, row in chosen.iterrows()])
    states["target_date"] = states["target_date"].astype(str)
    for column in ("raw_full_feature_parity", "raw_coherent_quote_parity", "policy_path_field_available"):
        states[column] = states.get(column, pd.Series(False, index=states.index)).fillna(False).astype(bool)
    states["scope"] = np.where(states["target_date"].lt(FORWARD_START), "inner_pre_2026_06_21", "retrospective_expanding_walk_forward_diagnostic")
    states["raw_snapshot_only"] = True
    states["uses_atlas_or_p3"] = False
    states["uses_city_date_forecast_backfill"] = False
    states["uses_future_archive_weather"] = False
    states["uses_full_window_city_bias"] = False

    market_states, market_scores = score_market_local(states)
    old = old_mixed_policy()
    transitions, transition_summary = contamination_transitions(old, states)
    status_summary = (
        states.groupby(["scope", "state_status"], as_index=False)
        .agg(rows=("city", "size"), dates=("target_date", "nunique"), cities=("city", "nunique"))
        .sort_values(["scope", "rows"], ascending=[True, False])
    )
    funnel = pd.DataFrame(
        [
            {"stage": "raw_paper_snapshot_payloads", "rows": inventory_counts["readable_payloads"], "dates": int(candidates["target_date"].nunique()), "cities": int(candidates["city"].nunique()), "note": "readable raw payloads"},
            {"stage": "city_date_local_hour_candidates", "rows": inventory_counts["candidate_state_rows"], "dates": int(candidates["target_date"].nunique()), "cities": int(candidates["city"].nunique()), "note": "candidate snapshots before pre-registered selection"},
            {"stage": "last_snapshot_per_city_date_local_hour", "rows": int(len(states)), "dates": int(states["target_date"].nunique()), "cities": int(states["city"].nunique()), "note": "selection happens before inspecting rung completeness"},
            {"stage": "single_snapshot_market_local_labelled", "rows": int(len(market_states)), "dates": int(market_states["target_date"].nunique()), "cities": int(market_states["city"].nunique()), "note": "all current/d1/d2/tail inputs and label available"},
            {"stage": "raw_full_feature_parity", "rows": int(states["raw_full_feature_parity"].sum()), "dates": int(states.loc[states["raw_full_feature_parity"], "target_date"].nunique()), "cities": int(states.loc[states["raw_full_feature_parity"], "city"].nunique()), "note": "requires legacy external path/forecast fields in one raw snapshot"},
            {"stage": "active_policy_execution_answerable", "rows": 0, "dates": 0, "cities": 0, "note": "active expression policy requires temp_trend_3h_f; raw paper snapshot does not save it"},
        ]
    )
    model_comparison = pd.DataFrame(
        [
            {
                "model": "market_local_single_snapshot",
                "same_clean_denominator_rows": int(len(market_states)),
                "probability_status": "scored_from_raw_single_snapshot" if not market_states.empty else "not_run_no_clean_rows",
                "execution_status": "not_run_data_not_answerable_active_policy_path_missing",
                "inner_wf_regularization": "not_applicable_no_fit",
                "logloss": float(market_states["market_local_logloss"].mean()) if not market_states.empty else math.nan,
                "brier": float(market_states["market_local_brier"].mean()) if not market_states.empty else math.nan,
                "fee_adjusted_roi": math.nan,
                "reason": "raw quote-only benchmark; no active-policy replay without saved trend3h",
            },
            {
                "model": "legacy_historical_full_features",
                "same_clean_denominator_rows": int(len(market_states)),
                "probability_status": "not_run_data_not_answerable_raw_feature_parity_missing",
                "execution_status": "not_run_data_not_answerable",
                "inner_wf_regularization": "not_run",
                "logloss": math.nan,
                "brier": math.nan,
                "fee_adjusted_roi": math.nan,
                "reason": "would require non-snapshot source-event/forecast-enrichment fields; no atlas/P3/backfill substitute allowed",
            },
            {
                "model": "legacy_coherent_cal_quote",
                "same_clean_denominator_rows": int(len(market_states)),
                "probability_status": "not_run_data_not_answerable_base_parity_missing",
                "execution_status": "not_run_data_not_answerable",
                "inner_wf_regularization": "not_run",
                "logloss": math.nan,
                "brier": math.nan,
                "fee_adjusted_roi": math.nan,
                "reason": "second stage cannot have parity when its legacy full-feature base cannot be reconstructed from the saved snapshot",
            },
        ]
    )
    execution_summary = pd.DataFrame(
        [
            {
                "scope": scope,
                "route": "market_local_single_snapshot",
                "side": side,
                "rows": 0,
                "dates": 0,
                "cost": math.nan,
                "pnl_fee_adjusted": math.nan,
                "roi_fee_adjusted": math.nan,
                "date_block_ci_low": math.nan,
                "date_block_ci_high": math.nan,
                "status": "not_run_data_not_answerable_active_policy_path_missing",
                "reason": "temp_trend_3h_f is required by the frozen active policy and absent from every raw selected snapshot",
            }
            for scope in ("all_clean", "retrospective_expanding_walk_forward_diagnostic")
            for side in ("ALL", "YES", "NO")
        ]
    )
    execution_daily = pd.DataFrame(
        columns=[
            "target_date", "route", "side", "rows", "cost", "pnl_fee_adjusted", "roi_fee_adjusted",
            "status", "reason",
        ]
    )
    field_audit = pd.DataFrame(
        [
            {"field_family": "market_ladder_and_quotes", "source": "selected paper snapshot records", "status": "used", "note": "current/d1/d2/tail from one selected payload"},
            {"field_family": "forecast", "source": "selected paper snapshot forecast_* fields", "status": "available_but_not_backfilled", "note": "saved fields retained only; no city-date reconstruction"},
            {"field_family": "path_trend_and_observation_context", "source": "selected paper snapshot", "status": "missing_for_active_policy", "note": "temp_trend_3h_f and legacy context fields are absent"},
            {"field_family": "settlement", "source": "settlement_outcomes final_price", "status": "label_only", "note": "selects outcome bucket only; never feature or selection input"},
            {"field_family": "atlas_p3_city_date_forecast_backfill", "source": "historical materializations", "status": "excluded", "note": "explicitly forbidden"},
            {"field_family": "future_archive_weather", "source": "archive", "status": "excluded", "note": "explicitly forbidden"},
            {"field_family": "full_window_city_bias", "source": "all-date outcomes", "status": "excluded", "note": "explicitly forbidden"},
        ]
    )

    # Result assertions: these are research-boundary checks, not live gates.
    assert states["rung_snapshot_ts_unique"].eq(1).all(), "mixed-snapshot rung state escaped selection"
    assert not states[["uses_atlas_or_p3", "uses_city_date_forecast_backfill", "uses_future_archive_weather", "uses_full_window_city_bias"]].any().any(), "forbidden feature source used"
    assert int(states["raw_full_feature_parity"].sum()) == 0, "raw full-feature parity unexpectedly became available; inspect contract"
    assert model_comparison.loc[model_comparison["model"].ne("market_local_single_snapshot"), "probability_status"].str.startswith("not_run").all()
    assert (transitions["clean_replay_expression"] == "").all(), "active policy replay must not bypass missing path field"

    candidates.drop(columns=["records"], errors="ignore").to_csv(output_dir / "snapshot_candidates.csv", index=False)
    states.to_csv(output_dir / "clean_state_audit.csv", index=False)
    funnel.to_csv(output_dir / "funnel.csv", index=False)
    status_summary.to_csv(output_dir / "state_status_summary.csv", index=False)
    market_scores.to_csv(output_dir / "market_local_daily_scores.csv", index=False)
    model_comparison.to_csv(output_dir / "model_comparison.csv", index=False)
    execution_summary.to_csv(output_dir / "execution_not_run_summary.csv", index=False)
    execution_daily.to_csv(output_dir / "execution_daily_not_run.csv", index=False)
    field_audit.to_csv(output_dir / "pit_field_audit.csv", index=False)
    transitions.drop(columns=["records"], errors="ignore").to_csv(output_dir / "old_mixed_to_clean_transition.csv", index=False)
    transition_summary.to_csv(output_dir / "old_mixed_to_clean_summary.csv", index=False)
    pd.DataFrame(roots).to_csv(output_dir / "snapshot_root_inventory.csv", index=False)

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": "offline raw-paper-snapshot single-snapshot lineage replay; no live/config/database mutation",
        "parameters": {
            "selection": "last snapshot per city/target_date/local_hour before rung completeness inspection",
            "forward_start": FORWARD_START,
            "active_expressions": list(ACTIVE_EXPRESSIONS),
            "ask_policy": {"floor": ASK_FLOOR, "ceiling": ASK_CEILING, "fee_adjusted_edge_threshold": EDGE_THRESHOLD},
            "official_fee_formula": "shares * 0.05 * price * (1 - price)",
            "first_city_day": True,
        },
        "counts": {
            "candidate_states_before_selection": inventory_counts["candidate_state_rows"],
            "clean_last_snapshot_states": int(len(states)),
            "clean_market_labelled_states": int(len(market_states)),
            "raw_full_feature_parity_states": int(states["raw_full_feature_parity"].sum()),
            "active_policy_execution_rows": 0,
            "old_mixed_policy_rows": int(len(old)),
        },
        "result": "data_answerability_not_execution_replay",
        "reason": "saved raw paper snapshots do not contain temp_trend_3h_f or the legacy full-feature path context; no prohibited proxy/backfill was used",
        "funnel": funnel.to_dict("records"),
        "models": model_comparison.to_dict("records"),
        "execution_reporting": execution_summary.to_dict("records"),
        "old_mixed_to_clean": transition_summary.to_dict("records"),
    }
    JSON_PATH.write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# Tmax Single-Snapshot Lineage Replay v2",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        "> Scope: offline lineage/data-answerability audit only. No live runner, config, order, or database was changed.",
        "",
        "## 结论",
        "",
        f"- **这轮不能把旧 coherent/full-feature 的 ROI 重新发布为 clean replay。** last-snapshot clean state 有 `{len(states)}` 条，market-local + settlement label 有 `{len(market_states)}` 条，但 active expression policy 必需的 `temp_trend_3h_f` 没保存在 raw paper snapshot，故固定 first-city-day / fee / ask / edge replay 为 `not_run_data_answerability`。",
        f"- legacy full-feature raw parity = `{int(states['raw_full_feature_parity'].sum())}`：没有用 P3/atlas city-date forecast backfill、future archive 或 full-window city bias 补齐。因此 full base 与 coherent quote 都是 `NA`，不是零分或负收益。",
        "- settlement 仅从 `settlement_outcomes.final_price > 0.5` 映射 relative label；没有进入 feature、模型选择或交易选择。模型正则也没有在 6/21+ 触碰，因为没有可 PIT parity 的候选模型可做 inner WF。",
        "",
        "## Clean Funnel",
        "",
        *markdown_table(funnel, ["stage", "rows", "dates", "cities", "note"]),
        "",
        "## Same Clean Denominator",
        "",
        *markdown_table(model_comparison, ["model", "same_clean_denominator_rows", "probability_status", "execution_status", "logloss", "brier", "fee_adjusted_roi", "reason"]),
        "",
        "## Old Mixed -> Clean Radius",
        "",
        "clean policy side is empty because the existing `trend3h` policy cannot be evaluated from the saved state. Therefore old rows are cancelled for lineage reasons, not counted as a new strategy loss; `pnl_delta_clean_minus_old` is a coverage accounting delta, **not** a valid counterfactual PnL comparison.",
        "",
        *markdown_table(transition_summary, ["old_source", "transition", "old_rows", "dates", "old_pnl", "old_cost", "clean_rows", "added_clean_rows", "switched_expression_rows", "pnl_delta_clean_minus_old"]),
        "",
        "## Frozen Execution Reporting",
        "",
        "固定 active expression、official taker fee、ask/edge policy、first city-day 均没有被改写；因为 required `trend3h` 不在原始 selected snapshot，ALL/YES/NO、route、daily、fee-adjusted ROI 和 date-block CI 全部明确为 `NA`，不能以零填充。",
        "",
        *markdown_table(execution_summary, ["scope", "route", "side", "rows", "roi_fee_adjusted", "date_block_ci_low", "date_block_ci_high", "status"]),
        "",
        "## PIT Boundary",
        "",
        "- All rungs are drawn from the same selected `snapshot_ts_utc`; selection chose the last snapshot for each city/date/local-hour before looking at ladder completeness. No rung-level timestamp selection is performed.",
        "- Allowed: selected snapshot bracket/book/METAR/forecast fields. Excluded: P3/atlas forecast backfill, historical archive weather, non-snapshot path reconstruction, and full-window city effects.",
        "- Existing old replay rows remain audit evidence only. They are not silently joined onto clean state or used to fill a missing raw feature.",
        "",
        "## Verdict",
        "",
        "significance=NA; baseline=NA; forward=FAIL; conclusion=`data_answerability_not_execution_replay`。",
        "",
        "## Artifacts",
        "",
        "- `docs/analysis/2026-07/generated/tmax_single_snapshot_lineage_replay_v2/funnel.csv`",
        "- `docs/analysis/2026-07/generated/tmax_single_snapshot_lineage_replay_v2/clean_state_audit.csv`",
        "- `docs/analysis/2026-07/generated/tmax_single_snapshot_lineage_replay_v2/model_comparison.csv`",
        "- `docs/analysis/2026-07/generated/tmax_single_snapshot_lineage_replay_v2/execution_not_run_summary.csv`",
        "- `docs/analysis/2026-07/generated/tmax_single_snapshot_lineage_replay_v2/execution_daily_not_run.csv`",
        "- `docs/analysis/2026-07/generated/tmax_single_snapshot_lineage_replay_v2/old_mixed_to_clean_transition.csv`",
        "- `docs/analysis/2026-07/generated/tmax_single_snapshot_lineage_replay_v2/old_mixed_to_clean_summary.csv`",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    return payload


def _raw_files() -> list[tuple[int, Path, pd.Timestamp]]:
    out = []
    for priority, root in enumerate(SNAPSHOT_ROOTS):
        for path in sorted(root.glob("snapshot_*.json")) if root.exists() else []:
            try:
                ts = parse_utc(json.loads(path.read_text(encoding="utf-8")).get("ts_utc"))
            except (OSError, json.JSONDecodeError):
                continue
            if ts is not None:
                out.append((priority, path, ts))
    return sorted(out, key=lambda item: (item[2], item[0], str(item[1])))


def _observation_history() -> tuple[pd.DataFrame, list[tuple[int, Path, pd.Timestamp]]]:
    events: dict[tuple[str, str, str], dict[str, Any]] = {}
    files: list[tuple[int, Path, pd.Timestamp]] = []
    for priority, root in enumerate(SNAPSHOT_ROOTS):
        for path in sorted(root.glob("snapshot_*.json")) if root.exists() else []:
            try:
                payload = json.loads(path.read_text(encoding="utf-8")); snapshot_ts = parse_utc(payload.get("ts_utc"))
            except (OSError, json.JSONDecodeError):
                continue
            if snapshot_ts is None:
                continue
            files.append((priority, path, snapshot_ts))
            for record in payload.get("records", []):
                city = str(record.get("city") or "")
                target_date = str(record.get("target_date") or record.get("event_date") or "")
                obs_ts = parse_utc(record.get("metar_latest_ts_utc"))
                temp_f = finite(record.get("metar_latest_temp_f"))
                if not city or not target_date or obs_ts is None or temp_f is None:
                    continue
                key = (city, target_date, obs_ts.isoformat())
                existing = events.get(key)
                if existing is None or snapshot_ts < existing["first_seen_snapshot_ts_utc"]:
                    events[key] = {
                        "city": city, "target_date": target_date, "obs_ts_utc": obs_ts,
                        "temp_f": temp_f, "first_seen_snapshot_ts_utc": snapshot_ts,
                        "metar_icao": record.get("metar_icao"),
                    }
    return pd.DataFrame(events.values()), sorted(files, key=lambda item: (item[2], item[0], str(item[1])))


def _source_stats() -> pd.DataFrame:
    rows = []
    for priority, root in enumerate(SNAPSHOT_ROOTS):
        for path in sorted(root.glob("snapshot_*.json")) if root.exists() else []:
            stat = path.stat()
            rows.append({"source_path": str(path), "root_priority": priority, "source_size": stat.st_size, "source_mtime_ns": stat.st_mtime_ns})
    return pd.DataFrame(rows).sort_values("source_path").reset_index(drop=True)


def _cache_matches(manifest_path: Path, cache_path: Path, event_path: Path, current: pd.DataFrame) -> bool:
    if not manifest_path.exists() or not cache_path.exists() or not event_path.exists():
        return False
    old = pd.read_csv(manifest_path, usecols=["source_path", "root_priority", "source_size", "source_mtime_ns"])
    old = old.sort_values("source_path").reset_index(drop=True)
    return old.equals(current)


def _build_or_load_raw_cache(output_dir: Path) -> tuple[pd.DataFrame, Path, pd.DataFrame, bool]:
    manifest_path = output_dir / "raw_snapshot_manifest.csv"
    cache_path = output_dir / "raw_decision_groups.jsonl"
    legacy_gzip_path = output_dir / "raw_decision_groups.jsonl.gz"
    event_path = output_dir / "embedded_observation_events.csv"
    if manifest_path.exists() and cache_path.exists() and event_path.exists():
        manifest = pd.read_csv(manifest_path)
        events = pd.read_csv(event_path, parse_dates=["obs_ts_utc", "first_seen_snapshot_ts_utc"])
        return manifest, cache_path, events, True
    current = _source_stats()
    if not cache_path.exists() and _cache_matches(manifest_path, legacy_gzip_path, event_path, current):
        with gzip.open(legacy_gzip_path, "rb") as source, cache_path.open("wb") as target:
            shutil.copyfileobj(source, target, length=8 * 1024 * 1024)
    if _cache_matches(manifest_path, cache_path, event_path, current):
        manifest = pd.read_csv(manifest_path)
        events = pd.read_csv(event_path, parse_dates=["obs_ts_utc", "first_seen_snapshot_ts_utc"])
        return manifest, cache_path, events, True

    manifest_rows: list[dict[str, Any]] = []
    events: dict[tuple[str, str, str], dict[str, Any]] = {}
    with cache_path.open("w", encoding="utf-8") as cache:
        for source in current.to_dict("records"):
            path = Path(source["source_path"])
            try:
                raw = path.read_bytes()
                payload = json.loads(raw)
            except (OSError, json.JSONDecodeError):
                continue
            snapshot_ts = parse_utc(payload.get("ts_utc"))
            records = payload.get("records")
            if snapshot_ts is None or not isinstance(records, list):
                continue
            payload_hash = hashlib.sha256(raw).hexdigest()
            manifest_rows.append({**source, "payload_hash": payload_hash, "snapshot_ts_utc": snapshot_ts.isoformat(), "record_count": len(records)})
            grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for record in records:
                if not isinstance(record, dict):
                    continue
                city = str(record.get("city") or "")
                target_date = str(record.get("target_date") or record.get("event_date") or "")
                if not city or not target_date:
                    continue
                compact = {field: record.get(field) for field in STATE_RECORD_FIELDS}
                compact["city"] = city
                compact["target_date"] = target_date
                grouped.setdefault((city, target_date), []).append(compact)
                obs_ts = parse_utc(record.get("metar_latest_ts_utc"))
                temp_f = finite(record.get("metar_latest_temp_f"))
                if obs_ts is not None and temp_f is not None:
                    key = (city, target_date, obs_ts.isoformat())
                    existing = events.get(key)
                    if existing is None or snapshot_ts < existing["first_seen_snapshot_ts_utc"]:
                        events[key] = {"city": city, "target_date": target_date, "obs_ts_utc": obs_ts, "temp_f": temp_f, "first_seen_snapshot_ts_utc": snapshot_ts, "metar_icao": record.get("metar_icao")}
            for (city, target_date), group in grouped.items():
                cache.write(json.dumps({"source_path": str(path), "payload_hash": payload_hash, "root_priority": source["root_priority"], "snapshot_ts_utc": snapshot_ts.isoformat(), "city": city, "target_date": target_date, "records": group}, ensure_ascii=False, separators=(",", ":")) + "\n")
    manifest = pd.DataFrame(manifest_rows).sort_values("source_path")
    event_frame = pd.DataFrame(events.values()).sort_values(["city", "target_date", "obs_ts_utc"])
    manifest.to_csv(manifest_path, index=False)
    event_frame.to_csv(event_path, index=False)
    return manifest, cache_path, event_frame, False


def _path_asof(events: list[dict[str, Any]], decision: pd.Timestamp, unit: str) -> dict[str, float] | None:
    available = [item for item in events if item["first_seen_snapshot_ts_utc"] <= decision and item["obs_ts_utc"] <= decision]
    if not available:
        return None
    latest = max(available, key=lambda item: item["obs_ts_utc"])
    convert = lambda f: f if unit == "F" else (f - 32.0) * 5.0 / 9.0
    latest_native = convert(float(latest["temp_f"]))
    running = max(available, key=lambda item: convert(float(item["temp_f"])))
    running_native = convert(float(running["temp_f"]))
    def trend(hours: int) -> float | None:
        prior = [item for item in available if item["obs_ts_utc"] <= latest["obs_ts_utc"] - pd.Timedelta(hours=hours)]
        return None if not prior else (latest_native - convert(float(max(prior, key=lambda item: item["obs_ts_utc"])["temp_f"]))) * (9.0 / 5.0 if unit == "C" else 1.0)
    return {
        "current_native": latest_native, "running_native": running_native,
        "temp_trend_1h_f": trend(1), "temp_trend_3h_f": trend(3),
        "max_age_min": (decision - latest["obs_ts_utc"]).total_seconds() / 60.0,
        "minutes_since_running_max": (decision - running["obs_ts_utc"]).total_seconds() / 60.0,
        "latest_obs_ts_utc": latest["obs_ts_utc"].isoformat(),
    }


def _native_state(records: list[dict[str, Any]], decision: pd.Timestamp, history: dict[tuple[str, str], list[dict[str, Any]]], winners: dict[tuple[str, str], str], priority: int, path: Path) -> dict[str, Any] | None:
    first = records[0]
    city = str(first["city"])
    target_date = str(first.get("target_date") or first.get("event_date") or "")
    unit = str(first.get("unit") or "").upper()
    if unit not in {"C", "F"}:
        return None
    path_state = _path_asof(history.get((city, target_date), []), decision, unit)
    if path_state is None:
        return None
    ladder = sorted({bracket_key(item): item for item in records if bracket_key(item) is not None}.values(), key=bracket_sort_key)
    anchor = next((index for index, item in enumerate(ladder) if (parsed := parse_market_bracket(str(item.get("bracket") or ""), str(item.get("question") or ""))) is not None and interval_contains(parsed, path_state["running_native"])), None)
    if anchor is None or anchor + 2 >= len(ladder):
        return None
    current, d1, d2 = ladder[anchor:anchor + 3]
    cur_key, d1_key, d2_key = (bracket_key(item) for item in (current, d1, d2))
    cur_yes_bid, cur_yes_ask, _ = yes_quote(current)
    no_asks = [finite(item.get("no_best_ask")) for item in (current, d1, d2)]
    no_bids = [finite(item.get("no_best_bid")) for item in (current, d1, d2)]
    if any(value is None for value in [cur_yes_bid, cur_yes_ask, *no_asks, *no_bids]):
        return None
    rung_mids = [yes_quote(item)[2] for item in ladder]
    if any(rung_mids[index] is None for index in (anchor, anchor + 1, anchor + 2)):
        return None
    market_score_ready = all(value is not None for value in rung_mids)
    probs = normalize({
        "below": sum(float(value) for value in rung_mids[:anchor]),
        "current": float(rung_mids[anchor]),
        "d1": float(rung_mids[anchor + 1]),
        "d2": float(rung_mids[anchor + 2]),
        "tail": sum(float(value) for value in rung_mids[anchor + 3:]),
    }) if market_score_ready else {bucket: math.nan for bucket in BUCKETS}
    winner = winners.get((city, target_date))
    if winner == cur_key: actual = "current"
    elif winner == d1_key: actual = "d1"
    elif winner == d2_key: actual = "d2"
    else:
        winner_index = next((index for index, item in enumerate(ladder) if bracket_key(item) == winner), None)
        actual = "below" if winner_index is not None and winner_index < anchor else "tail" if winner_index is not None and winner_index > anchor + 2 else None
    if actual is None:
        return None
    forecast_native = finite(current.get("forecast_max_native"))
    delta_to_f = 1.0 if unit == "F" else 9.0 / 5.0
    return {
        "city": city, "target_date": target_date, "decision_hour_local": int(str(first.get("ts_local") or "")[11:13]),
        "decision_snapshot_ts_utc": decision.isoformat(), "snapshot_path": str(path), "snapshot_root_priority": priority,
        "rung_count": len(ladder), "rung_snapshot_ts_unique": 1, "anchor_rung_index": anchor, "winner_rung_index": winner_index if winner not in {cur_key, d1_key, d2_key} else anchor if winner == cur_key else anchor + 1 if winner == d1_key else anchor + 2,
        "label_rule": "winner_rung_below_anchor" if actual == "below" else "winner_exact_anchor" if actual == "current" else "winner_exact_d1" if actual == "d1" else "winner_exact_d2" if actual == "d2" else "winner_rung_above_d2",
        "actual_bucket": actual, "current_bracket": cur_key, "d1_bracket": d1_key, "d2_bracket": d2_key,
        "current_no_ask": no_asks[0], "d1_no_ask": no_asks[1], "d2_no_ask": no_asks[2],
        "current_no_ask_size": finite(current.get("no_ask_size")), "d1_no_ask_size": finite(d1.get("no_ask_size")), "d2_no_ask_size": finite(d2.get("no_ask_size")),
        "d1_yes_direct_ask": finite(d1.get("yes_best_ask")), "d2_yes_direct_ask": finite(d2.get("yes_best_ask")),
        "d1_yes_direct_ask_size": finite(d1.get("yes_ask_size")), "d2_yes_direct_ask_size": finite(d2.get("yes_ask_size")),
        "d1_yes_direct_depth_5c": finite(d1.get("yes_depth_ask_5c")), "d2_yes_direct_depth_5c": finite(d2.get("yes_depth_ask_5c")),
        "forecast_gap_to_running_f": None if forecast_native is None else (forecast_native - path_state["running_native"]) * delta_to_f,
        "current_minus_running_f": (path_state["current_native"] - path_state["running_native"]) * delta_to_f,
        "forecast_peak_delta_hours_local": finite(current.get("forecast_peak_delta_hours_local")),
        "forecast_peak_hour_local": finite(current.get("forecast_peak_hour_local")), "forecast_source": current.get("forecast_source"), "unit": unit,
        "market_score_ready": market_score_ready,
        "exec_market_p_current": float(rung_mids[anchor]), "exec_market_p_d1": float(rung_mids[anchor + 1]), "exec_market_p_d2": float(rung_mids[anchor + 2]),
        **path_state, **{f"market_p_{key}": value for key, value in probs.items()},
    }


def _materialize_snapshot_native(output_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    manifest, cache_path, observations, cache_reused = _build_or_load_raw_cache(output_dir)
    state_path = output_dir / "execution_first_eligible_candidates.csv"
    hourly_path = output_dir / "state_score_hourly_last_snapshot.csv"
    required_state_columns = {"actual_bucket", "rung_snapshot_ts_unique", "market_score_ready", "exec_market_p_current", "forecast_gap_to_running_f", "current_minus_running_f", "d1_yes_direct_ask", "d2_yes_direct_ask", "winner_rung_index", "anchor_rung_index"}
    if state_path.exists() and hourly_path.exists():
        cached_states = pd.read_csv(state_path, low_memory=False)
        cached_hourly = pd.read_csv(hourly_path, low_memory=False)
        if required_state_columns.issubset(cached_states.columns) and str(cached_states.target_date.max()) >= "2026-07-10":
            for frame in (cached_states, cached_hourly):
                frame["market_score_ready"] = frame["market_score_ready"].astype(str).str.lower().eq("true")
            return cached_states, cached_hourly, {"cache_reused": cache_reused, "state_cache_reused": True, "manifest_files": len(manifest), "observation_events": len(observations)}
    history = {key: group.to_dict("records") for key, group in observations.groupby(["city", "target_date"])}
    winners = load_settlement_winners()
    rows = []
    with cache_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            group = json.loads(line)
            decision = parse_utc(group.get("snapshot_ts_utc"))
            if decision is None:
                continue
            state = _native_state(group["records"], decision, history, winners, int(group["root_priority"]), Path(group["source_path"]))
            if state is not None:
                rows.append(state)
    all_states = pd.DataFrame(rows)
    if all_states.empty:
        raise RuntimeError("No clean five-bucket snapshot-native states materialized")
    all_states = all_states.sort_values(["city", "target_date", "decision_snapshot_ts_utc", "snapshot_root_priority"]).drop_duplicates(["city", "target_date", "decision_snapshot_ts_utc"], keep="last")
    assert all_states.rung_snapshot_ts_unique.eq(1).all(), "cross-snapshot ladder mix detected"
    below = all_states[all_states.actual_bucket.eq("below")]
    assert (below.winner_rung_index < below.anchor_rung_index).all(), "below label did not come from winner rung below anchor"
    all_states.to_csv(state_path, index=False)
    hourly = all_states.sort_values(["city", "target_date", "decision_hour_local", "decision_snapshot_ts_utc"]).groupby(["city", "target_date", "decision_hour_local"], as_index=False).tail(1)
    hourly.to_csv(hourly_path, index=False)
    return all_states, hourly, {"cache_reused": cache_reused, "state_cache_reused": False, "manifest_files": len(manifest), "observation_events": len(observations)}


def _model_pipeline(c_value: float, feature_columns: list[str]) -> Pipeline:
    return Pipeline([("features", ColumnTransformer([("num", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), feature_columns)])), ("clf", LogisticRegression(C=c_value, max_iter=1000))])


def _expanding_predictions(rows: pd.DataFrame, c_value: float, feature_columns: list[str]) -> pd.DataFrame:
    frames = []
    for date in sorted(rows["target_date"].unique()):
        train, test = rows[rows.target_date < date], rows[rows.target_date == date]
        if train.target_date.nunique() < 5 or train.actual_bucket.nunique() < 2:
            continue
        model = _model_pipeline(c_value, feature_columns); model.fit(train, train.actual_bucket)
        probabilities = model.predict_proba(test)
        out = test.copy(); classes = list(model.named_steps["clf"].classes_)
        expanded = np.full((len(test), len(BUCKETS)), EPS, dtype=float)
        for position, bucket in enumerate(BUCKETS):
            if bucket in classes:
                expanded[:, position] = probabilities[:, classes.index(bucket)]
        expanded /= expanded.sum(axis=1, keepdims=True)
        for position, bucket in enumerate(BUCKETS):
            out[f"fusion_p_{bucket}"] = expanded[:, position]
        frames.append(out)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _score(frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
    index = {bucket: position for position, bucket in enumerate(BUCKETS)}
    probs = frame[[f"{prefix}_p_{bucket}" for bucket in BUCKETS]].to_numpy(float); actual = frame.actual_bucket.map(index).to_numpy(int)
    out = frame[["city", "target_date", "decision_hour_local", "decision_snapshot_ts_utc", "actual_bucket", "unit"]].copy()
    out["route"] = prefix; out["logloss"] = -np.log(np.maximum(EPS, probs[np.arange(len(out)), actual])); out["brier"] = np.square(probs - np.eye(len(BUCKETS))[actual]).sum(axis=1)
    return out


def _date_block_roi(group: pd.DataFrame, seed: int = 20260711) -> tuple[float, float, float]:
    if group.empty:
        return math.nan, math.nan, math.nan
    point = float(group["pnl"].sum() / group["cost"].sum())
    daily = group.groupby("target_date", as_index=False).agg(pnl=("pnl", "sum"), cost=("cost", "sum"))
    if len(daily) < 3:
        return point, math.nan, math.nan
    values = daily[["pnl", "cost"]].to_numpy(float)
    rng = np.random.default_rng(seed)
    samples = []
    for _ in range(5000):
        sample = values[rng.integers(0, len(values), len(values))]
        samples.append(sample[:, 0].sum() / sample[:, 1].sum())
    return point, float(np.percentile(samples, 2.5)), float(np.percentile(samples, 97.5))


def _execution_summary(executions: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    rows = []
    for keys, group in executions.groupby(columns, dropna=False):
        keys = keys if isinstance(keys, tuple) else (keys,)
        roi, low, high = _date_block_roi(group)
        rows.append({**dict(zip(columns, keys)), "rows": len(group), "dates": group.target_date.nunique(), "wins": group.win.sum(), "cost": group.cost.sum(), "pnl": group.pnl.sum(), "roi": roi, "roi_ci_low": low, "roi_ci_high": high})
    return pd.DataFrame(rows)


def _policy(frame: pd.DataFrame, probability_route: str, arm: str, exclude_trend3h_flat: bool) -> pd.DataFrame:
    rows = []
    for item in frame.to_dict("records"):
        trend = finite(item.get("temp_trend_3h_f"))
        if exclude_trend3h_flat and (trend is None or -0.5 <= trend < 0.5):
            continue
        for expression in ACTIVE_EXPRESSIONS:
            ask = {"current_no": item["current_no_ask"], "d1_no": item["d1_no_ask"], "d2_no": item["d2_no_ask"], "d1_yes": item.get("d1_yes_direct_ask"), "d2_yes": item.get("d2_yes_direct_ask")}[expression]
            ask_size = {"current_no": item.get("current_no_ask_size"), "d1_no": item.get("d1_no_ask_size"), "d2_no": item.get("d2_no_ask_size"), "d1_yes": item.get("d1_yes_direct_ask_size"), "d2_yes": item.get("d2_yes_direct_ask_size")}[expression]
            ask = finite(ask)
            ask_size = finite(ask_size)
            if ask is None or ask_size is None or ask_size < 5.0:
                continue
            bucket = "current" if expression == "current_no" else "d1" if expression.startswith("d1") else "d2"
            probability_field = f"exec_market_p_{bucket}" if probability_route == "market" else f"fusion_p_{bucket}"
            p = item[probability_field]; p = 1.0 - p if expression.endswith("_no") else p
            fee = official_fee(float(ask)); edge = p - float(ask) - fee
            if ASK_FLOOR <= ask <= ASK_CEILING and edge >= EDGE_THRESHOLD:
                win = float(item["actual_bucket"] != bucket) if expression.endswith("_no") else float(item["actual_bucket"] == bucket)
                rows.append({"route": probability_route, "arm": arm, "city": item["city"], "target_date": item["target_date"], "unit": item["unit"], "decision_snapshot_ts_utc": item["decision_snapshot_ts_utc"], "expression": expression, "side": "NO" if expression.endswith("_no") else "YES", "shares": 5, "ask": ask, "ask_size": ask_size, "fee_per_share": fee, "cost": 5.0 * (ask + fee), "edge": edge, "p_win": p, "win": win, "pnl": 5.0 * (win - ask - fee)})
    eligible = pd.DataFrame(rows)
    if eligible.empty: return eligible
    selected = eligible.sort_values(["city", "target_date", "decision_snapshot_ts_utc", "edge"], ascending=[True, True, True, False]).groupby(["city", "target_date", "decision_snapshot_ts_utc"], as_index=False).head(1)
    return selected.sort_values(["city", "target_date", "decision_snapshot_ts_utc"]).groupby(["city", "target_date"], as_index=False).head(1)


def run_review_v3(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    states, hourly, cache_meta = _materialize_snapshot_native(output_dir)
    model_states = states[states.market_score_ready].copy()
    hourly_score = hourly[hourly.market_score_ready].copy()
    pre = model_states[model_states.target_date < FORWARD_START]
    grid = []
    for feature_spec, feature_columns in FEATURE_SPECS.items():
        for c_value in (0.03, 0.1, 0.3):
            pred = _expanding_predictions(pre, c_value, feature_columns)
            scored = _score(pred, "fusion") if not pred.empty else pd.DataFrame()
            grid.append({"feature_spec": feature_spec, "c": c_value, "rows": len(pred), "logloss": scored.logloss.mean() if not scored.empty else math.nan, "brier": scored.brier.mean() if not scored.empty else math.nan})
    grid_frame = pd.DataFrame(grid)
    selected_grid = grid_frame.dropna(subset=["logloss"])
    selected_row = selected_grid.sort_values(["logloss", "brier", "feature_spec", "c"]).iloc[0] if not selected_grid.empty else None
    selected_c = float(selected_row.c) if selected_row is not None else 0.1
    selected_feature_spec = str(selected_row.feature_spec) if selected_row is not None else "unit_invariant_no_decision_hour"
    selected_features = FEATURE_SPECS[selected_feature_spec]
    preselected = not selected_grid.empty
    grid_frame.to_csv(output_dir / "snapshot_native_inner_wf_grid.csv", index=False)
    predictions = _expanding_predictions(model_states, selected_c, selected_features)
    hourly_keys = hourly_score[["city", "target_date", "decision_hour_local", "decision_snapshot_ts_utc"]]
    hourly_pred = predictions.merge(hourly_keys, on=list(hourly_keys), how="inner") if not predictions.empty else pd.DataFrame()
    score_frames = [_score(hourly_score, "market").assign(denominator="market_all_hourly")]
    if not hourly_pred.empty:
        score_frames.extend([_score(hourly_pred, "market").assign(denominator="fusion_available_paired"), _score(hourly_pred, "fusion").assign(denominator="fusion_available_paired")])
    score_rows = pd.concat(score_frames, ignore_index=True)
    score_rows["scope"] = np.where(score_rows.target_date < FORWARD_START, "inner_pre_2026_06_21", "retrospective_expanding_walk_forward_diagnostic")
    score_summary = score_rows.groupby(["scope", "denominator", "route"], as_index=False).agg(rows=("city", "size"), dates=("target_date", "nunique"), logloss=("logloss", "mean"), brier=("brier", "mean"))
    score_unit = score_rows.groupby(["scope", "denominator", "route", "unit"], as_index=False).agg(rows=("city", "size"), dates=("target_date", "nunique"), logloss=("logloss", "mean"), brier=("brier", "mean"))
    score_daily = score_rows.groupby(["scope", "denominator", "route", "target_date"], as_index=False).agg(rows=("city", "size"), logloss=("logloss", "mean"), brier=("brier", "mean"))
    score_rows.to_csv(output_dir / "state_score_rows.csv", index=False)
    score_summary.to_csv(output_dir / "state_score_summary.csv", index=False)
    score_unit.to_csv(output_dir / "state_score_unit_summary.csv", index=False)
    score_daily.to_csv(output_dir / "state_score_daily.csv", index=False)

    execution_frames = [
        _policy(states, "market", "old_policy_parity_exclude_trend3h_flat", True),
        _policy(states, "market", "clean_mechanism_trend_continuous", False),
    ]
    if not predictions.empty:
        execution_frames.extend([_policy(predictions, "fusion", "old_policy_parity_exclude_trend3h_flat", True), _policy(predictions, "fusion", "clean_mechanism_trend_continuous", False)])
    executions = pd.concat([frame for frame in execution_frames if not frame.empty], ignore_index=True) if any(not frame.empty for frame in execution_frames) else pd.DataFrame()
    if not executions.empty:
        yes_rows = executions[executions.side.eq("YES")]
        assert yes_rows.ask.notna().all(), "YES execution lacks direct ask"
        assert yes_rows.expression.isin(["d1_yes", "d2_yes"]).all()
    executions.to_csv(output_dir / "execution_first_lock_rows.csv", index=False)
    daily = executions.groupby(["arm", "route", "target_date", "side"], as_index=False).agg(rows=("city", "size"), cost=("cost", "sum"), pnl=("pnl", "sum")) if not executions.empty else pd.DataFrame(columns=["arm", "route", "target_date", "side", "rows", "cost", "pnl"])
    daily["roi"] = daily.pnl / daily.cost
    daily.to_csv(output_dir / "execution_daily.csv", index=False)
    if not executions.empty:
        summary = pd.concat([
            _execution_summary(executions, ["arm", "route", "side"]),
            _execution_summary(executions, ["arm", "route"]).assign(side="ALL"),
        ], ignore_index=True)
    else:
        summary = pd.DataFrame()
    expression_summary = _execution_summary(executions, ["arm", "route", "expression"]) if not executions.empty else pd.DataFrame()
    summary.to_csv(output_dir / "execution_summary.csv", index=False)
    expression_summary.to_csv(output_dir / "execution_expression_summary.csv", index=False)
    execution_unit = _execution_summary(executions, ["arm", "route", "unit"]) if not executions.empty else pd.DataFrame()
    execution_unit.to_csv(output_dir / "execution_unit_summary.csv", index=False)
    route_coverage_rows = []
    for arm in ("old_policy_parity_exclude_trend3h_flat", "clean_mechanism_trend_continuous"):
        for route in ("market", "fusion"):
            group = executions[(executions.arm == arm) & (executions.route == route)] if not executions.empty else pd.DataFrame()
            route_coverage_rows.append({"arm": arm, "route": route, "first_lock_rows": len(group), "dates": group.target_date.nunique() if not group.empty else 0, "status": "replayed_selected" if not group.empty else "replayed_zero_eligible"})
    route_coverage = pd.DataFrame(route_coverage_rows)
    route_coverage.to_csv(output_dir / "execution_route_coverage.csv", index=False)

    frozen = model_states[model_states.target_date <= "2026-07-10"].copy()
    pipeline_path = output_dir / "snapshot_native_frozen_shadow_candidate.joblib"
    artifact: dict[str, Any] = {"artifact_status": "not_trainable", "candidate_status": "frozen_shadow_candidate", "promotion": "FAIL_fresh_forward_0", "feature_spec": selected_feature_spec, "feature_order": selected_features, "selected_c": selected_c, "train_through": "2026-07-10", "model_identity": "snapshot_native_global_no_city_fusion_not_legacy_parity"}
    if frozen.target_date.nunique() >= 5 and frozen.actual_bucket.nunique() >= 2:
        model = _model_pipeline(selected_c, selected_features)
        model.fit(frozen, frozen.actual_bucket)
        clf = model.named_steps["clf"]
        numeric = model.named_steps["features"].named_transformers_["num"]
        joblib.dump(model, pipeline_path)
        pipeline_sha256 = hashlib.sha256(pipeline_path.read_bytes()).hexdigest()
        reloaded = joblib.load(pipeline_path)
        parity_rows = frozen.head(min(256, len(frozen)))
        parity_delta = float(np.max(np.abs(model.predict_proba(parity_rows) - reloaded.predict_proba(parity_rows))))
        assert parity_delta <= 1e-12, f"reloaded frozen pipeline parity failed: {parity_delta}"
        artifact.update({
            "artifact_status": "trained", "train_rows": len(frozen), "train_dates": frozen.target_date.nunique(),
            "pipeline_path": str(pipeline_path.relative_to(ROOT)), "pipeline_sha256": pipeline_sha256,
            "class_order": [str(value) for value in clf.classes_], "class_to_index": {str(value): index for index, value in enumerate(clf.classes_)},
            "imputer_statistics": numeric.named_steps["impute"].statistics_.tolist(),
            "scaler_mean": numeric.named_steps["scale"].mean_.tolist(), "scaler_scale": numeric.named_steps["scale"].scale_.tolist(),
            "reload_prediction_max_abs_delta": parity_delta,
        })
    (output_dir / "snapshot_native_frozen_shadow_candidate.json").write_text(json.dumps(json_ready(artifact), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
    settlement_coverage = pd.read_sql_query("SELECT target_date, COUNT(DISTINCT city) AS cities, COUNT(*) AS rows FROM settlement_outcomes WHERE settlement_status='settled' AND target_date BETWEEN '2026-07-08' AND '2026-07-10' GROUP BY target_date ORDER BY target_date", conn)
    conn.close()
    settlement_coverage.to_csv(output_dir / "settlement_label_coverage.csv", index=False)

    fusion_evidence = "retrospective_expanding_walk_forward_diagnostic"
    paired = score_summary[(score_summary.scope == "retrospective_expanding_walk_forward_diagnostic") & (score_summary.denominator == "fusion_available_paired")]
    paired_map = paired.set_index("route") if not paired.empty else pd.DataFrame()
    paired_logloss_delta = float(paired_map.loc["fusion", "logloss"] - paired_map.loc["market", "logloss"]) if set(paired.route) == {"market", "fusion"} else math.nan
    all_ci_cross_zero = bool(summary.empty or ((summary.roi_ci_low.fillna(-math.inf) <= 0) & (summary.roi_ci_high.fillna(math.inf) >= 0)).all())
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "verdict": "inconclusive_no_live",
        "cache": cache_meta,
        "selected_c_inner_pre_2026_06_21": selected_c if preselected else None,
        "selected_feature_spec_inner_proper_score": selected_feature_spec if preselected else None,
        "inner_selection_status": "selected_by_pre_2026_06_21_proper_score" if preselected else f"not_answerable_only_{pre.target_date.nunique()}_pre_cutoff_dates_default_no_hour_development_artifact",
        "fusion_evidence_scope": fusion_evidence,
        "counts": {"execution_relative_states": len(states), "hourly_checkpoints": len(hourly), "five_bucket_hourly_state_score": len(hourly_score), "below_label_rows": int(states.actual_bucket.eq("below").sum()), "predicted_states": len(predictions), "hourly_predicted_states": len(hourly_pred), "first_lock_executions": len(executions), "raw_state_min_date": states.target_date.min(), "raw_state_max_date": states.target_date.max(), "fresh_forward_dates": 0},
        "settlement_coverage_2026_07_08_10": settlement_coverage.to_dict("records"),
        "score_summary": score_summary.to_dict("records"),
        "score_unit_summary": score_unit.to_dict("records"),
        "execution_summary": summary.to_dict("records"),
        "execution_unit_summary": execution_unit.to_dict("records"),
        "execution_route_coverage": route_coverage.to_dict("records"),
        "paired_fusion_minus_market_logloss": paired_logloss_delta,
        "artifact": artifact,
        "three_gates": {"significance": "FAIL_all_execution_date_block_ci_cross_zero" if all_ci_cross_zero else "PARTIAL", "baseline": "FAIL_fusion_logloss_worse_than_market" if math.isfinite(paired_logloss_delta) and paired_logloss_delta > 0 else "PASS", "forward": "FAIL_fresh_forward_0", "conclusion": "inconclusive_no_live"},
    }
    JSON_PATH.write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = [
        "# Tmax Single-Snapshot Lineage Replay v2", "", f"> generated_at_utc: `{payload['generated_at_utc']}`", "", "## 结论", "",
        f"- clean five-bucket states `{len(states)}`，日期 `{states.target_date.min()}..{states.target_date.max()}`；settlement label 已覆盖到 `{settlement_coverage.target_date.max() if not settlement_coverage.empty else 'NA'}`。",
        f"- snapshot-native fusion evidence=`{fusion_evidence}`；frozen artifact=`{artifact['artifact_status']}`，但 fresh-forward dates=0，promotion=`FAIL`，不改 live。",
        f"- `<6/21` clean dates=`{pre.target_date.nunique()}`，不足以做 inner C/decision-hour 选择；frozen artifact 使用默认 no-hour spec，仅供 development shadow。它是 snapshot-native global no-city fusion，**不是**旧 full-feature/coherent parity。",
        "- market proper-score 与 fusion proper-score只在相同 hourly paired denominator 比较；交易 ROI 另按逐 snapshot first-eligible、5 shares、official fee 计算。",
        "", "## Denominators", "",
        "- state-score：每 city/date/local-hour 最后一份 snapshot；只作概率评分。所有 6/21+ 统一标 `retrospective_expanding_walk_forward_diagnostic`，fresh forward=0。",
        "- execution：每 city-day 按 snapshot_ts 逐份扫描，首次满足 fixed expression/ask/fee/edge 即 first-lock；不按 hourly-last 或 best ask。",
        "- A arm 保留 frozen `exclude_trend3h_flat`；B arm trend/path 只作连续模型特征，不以 B 结果修改 A。四个 arm/route 先各自 `_policy` first-lock，再 concat 汇总，互不抢第一笔。",
        "- outcome space=`below/current/d1/d2/tail`；below/tail 来自同一 snapshot lower/upper sibling YES mids。d1/d2 YES execution只认 direct YES ask，cross-side仅进入 market prior。",
        f"- 历史 clean states 中 `below_label_rows={int(states.actual_bucket.eq('below').sum())}`；五桶空间保留 below，单测验证 winner rung < anchor 时映射 below，但本窗口没有实际 below label。",
        f"- fusion feature spec=`{selected_feature_spec}`：只用统一 °F delta/path/age/五桶 market probability；绝对 forecast/current/running 温度不入模。decision_hour 仅为 inner proper-score 消融候选，solar-time 历史仍不可答，不按 ROI 选择。",
        "", "## Proper Score", "", *markdown_table(score_summary, list(score_summary.columns)),
        "", "## C/F Proper-Score Sanity", "", *markdown_table(score_unit, list(score_unit.columns)),
        "", "## Execution 5-Share", "", *markdown_table(summary, list(summary.columns) if not summary.empty else []),
        "", "## First-Lock Route Coverage", "", *markdown_table(route_coverage, list(route_coverage.columns)),
        "", "## C/F ROI Sanity", "", *markdown_table(execution_unit, list(execution_unit.columns) if not execution_unit.empty else []),
        "", "## Settlement Coverage", "", *markdown_table(settlement_coverage, list(settlement_coverage.columns)),
        "", "## Three Gates", "", f"significance={'FAIL' if all_ci_cross_zero else 'PARTIAL'}（execution date-block CI）；baseline={'FAIL' if math.isfinite(paired_logloss_delta) and paired_logloss_delta > 0 else 'PASS'}（paired fusion-market logloss delta={paired_logloss_delta:+.4f}）；forward=FAIL（fresh-forward=0）；conclusion=`inconclusive_no_live`。",
        "", "逐日、表达和逐笔：`execution_daily.csv`、`execution_expression_summary.csv`、`execution_first_lock_rows.csv`。模型 artifact：`snapshot_native_frozen_shadow_candidate.json`。", "",
    ]
    REPORT_PATH.write_text("\n".join(report), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    payload = run_review_v3(args.out_dir)
    print(json.dumps(json_ready({"result": payload["verdict"], "counts": payload["counts"]}), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
