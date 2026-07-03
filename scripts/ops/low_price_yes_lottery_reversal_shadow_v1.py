#!/usr/bin/env python3
"""Append low-price YES lottery reversal candidates to a zero-notional journal.

This is an independent research/shadow head. It never submits orders. The
selector mirrors the fact-level `edge_ge_20c` lottery rule from
`research_reversal_lottery_lab_v2.py` and records one PIT candidate per
city-date for forward settlement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.tools.low_price_yes_tail_telemetry import (
    TailTelemetryResources,
    build_low_price_yes_tail_telemetry,
    load_tail_telemetry_resources_soft,
)

DB_DEFAULT = ROOT / "runtime/weather.db"
JOURNAL_DEFAULT = ROOT / "runtime/weather_edge_v1/low_price_yes_lottery_reversal_v1/shadow_candidates.jsonl"
SUMMARY_DEFAULT = ROOT / "runtime/weather_edge_v1/low_price_yes_lottery_reversal_v1/latest_summary.json"
SUMMARY_HISTORY_DEFAULT = ROOT / "runtime/weather_edge_v1/low_price_yes_lottery_reversal_v1/summary_history.jsonl"

STRATEGY_ID = "low_price_yes_lottery_reversal_shadow_v1"
RULE_ID = "buy_yes_ask_001_025_edge_ge_020_city_date_first_v1"
SOURCE_REPORT = "docs/analysis/2026-07/2026-07-01-reversal-lottery-lab-v2.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_DEFAULT))
    parser.add_argument("--journal", default=str(JOURNAL_DEFAULT))
    parser.add_argument("--summary", default=str(SUMMARY_DEFAULT))
    parser.add_argument("--summary-history", default=str(SUMMARY_HISTORY_DEFAULT))
    parser.add_argument("--min-event-date", default=None, help="Default: latest unsettled matching event_date.")
    parser.add_argument("--max-event-date", default=None)
    parser.add_argument("--min-ask", type=float, default=0.01)
    parser.add_argument("--max-ask", type=float, default=0.25)
    parser.add_argument("--min-edge", type=float, default=0.20)
    parser.add_argument("--hypothetical-notional-usd", type=float, default=5.0)
    parser.add_argument("--max-candidates-per-run", type=int, default=200)
    parser.add_argument("--allow-settled", action="store_true", help="Allow already-settled rows for backfill/debug.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def json_ready(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    return conn


def scalar(conn: sqlite3.Connection, query: str, params: dict[str, Any]) -> Any:
    return conn.execute(query, params).fetchone()[0]


def effective_min_event_date(conn: sqlite3.Connection, args: argparse.Namespace) -> str | None:
    if args.min_event_date:
        return str(args.min_event_date)
    status_filter = "" if args.allow_settled else "AND COALESCE(settlement_status, '') <> 'settled' AND final_yes IS NULL"
    query = f"""
        SELECT MAX(event_date)
        FROM fact_signal_candidates
        WHERE side = 'BUY_YES'
          AND decision_entry_price BETWEEN :min_ask AND :max_ask
          AND edge >= :min_edge
          {status_filter}
    """
    params = {"min_ask": args.min_ask, "max_ask": args.max_ask, "min_edge": args.min_edge}
    return scalar(conn, query, params)


def count_raw(conn: sqlite3.Connection, args: argparse.Namespace, min_event_date: str | None) -> dict[str, Any]:
    status_filter = "" if args.allow_settled else "AND COALESCE(settlement_status, '') <> 'settled' AND final_yes IS NULL"
    max_filter = "AND event_date <= :max_event_date" if args.max_event_date else ""
    query = f"""
        SELECT
          COUNT(*) AS rows,
          COUNT(DISTINCT event_date) AS dates,
          COUNT(DISTINCT city) AS cities,
          MIN(event_date) AS min_event_date,
          MAX(event_date) AS max_event_date,
          AVG(decision_entry_price) AS avg_ask,
          AVG(edge) AS avg_edge,
          MAX(fact_built_at_utc) AS fact_built_at_utc
        FROM fact_signal_candidates
        WHERE side = 'BUY_YES'
          AND decision_entry_price BETWEEN :min_ask AND :max_ask
          AND edge >= :min_edge
          AND (:min_event_date IS NULL OR event_date >= :min_event_date)
          {max_filter}
          {status_filter}
    """
    params = {
        "min_ask": args.min_ask,
        "max_ask": args.max_ask,
        "min_edge": args.min_edge,
        "min_event_date": min_event_date,
        "max_event_date": args.max_event_date,
    }
    return dict(conn.execute(query, params).fetchone())


def load_candidates(conn: sqlite3.Connection, args: argparse.Namespace, min_event_date: str | None) -> list[sqlite3.Row]:
    status_filter = "" if args.allow_settled else "AND COALESCE(settlement_status, '') <> 'settled' AND final_yes IS NULL"
    max_filter = "AND event_date <= :max_event_date" if args.max_event_date else ""
    query = f"""
        WITH base AS (
          SELECT
            candidate_id,
            condition_id,
            market_id,
            side,
            event_date,
            bracket,
            city,
            city_pool,
            icao,
            unit,
            forecast_source,
            forecast_max_f,
            forecast_max_native,
            forecast_peak_hour_local,
            forecast_peak_time_local,
            forecast_peak_hour_utc,
            forecast_peak_time_utc,
            forecast_hourly_count,
            forecast_peak_source,
            forecast_timezone,
            forecast_peak_delta_hours_local,
            forecast_max_in_bracket,
            forecast_max_above_bracket_f,
            forecast_max_below_bracket_f,
            model_version,
            time_bucket,
            window,
            decision_window_label,
            decision_hours_to_settle,
            decision_snapshot_ts_utc,
            model_p_yes,
            market_yes_price,
            edge,
            abs_edge,
            decision_entry_price,
            yes_spread,
            no_spread,
            yes_depth_ask_5c,
            no_depth_ask_5c,
            first_seen_ts_utc,
            last_seen_ts_utc,
            n_snapshots,
            edge_max,
            edge_mean,
            best_entry_price,
            settlement_status,
            final_yes,
            bracket_hit,
            fact_built_at_utc,
            ROW_NUMBER() OVER (
              PARTITION BY event_date, city
              ORDER BY decision_snapshot_ts_utc ASC, decision_entry_price ASC, edge DESC, bracket ASC, candidate_id ASC
            ) AS city_date_rank
          FROM fact_signal_candidates
          WHERE side = 'BUY_YES'
            AND decision_entry_price BETWEEN :min_ask AND :max_ask
            AND edge >= :min_edge
            AND (:min_event_date IS NULL OR event_date >= :min_event_date)
            {max_filter}
            {status_filter}
        )
        SELECT *
        FROM base
        WHERE city_date_rank = 1
        ORDER BY event_date ASC, decision_snapshot_ts_utc ASC, city ASC
        LIMIT :limit
    """
    params = {
        "min_ask": args.min_ask,
        "max_ask": args.max_ask,
        "min_edge": args.min_edge,
        "min_event_date": min_event_date,
        "max_event_date": args.max_event_date,
        "limit": args.max_candidates_per_run,
    }
    return list(conn.execute(query, params))


def existing_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    if not path.exists():
        return ids
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            shadow_id = row.get("shadow_decision_id")
            if shadow_id:
                ids.add(str(shadow_id))
    return ids


def decision_id(row: sqlite3.Row) -> str:
    raw = "|".join(
        [
            RULE_ID,
            str(row["event_date"]),
            str(row["city"]),
            str(row["decision_snapshot_ts_utc"]),
            str(row["bracket"]),
            str(row["condition_id"]),
            str(row["candidate_id"]),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def journal_row(row: sqlite3.Row, args: argparse.Namespace, tail_telemetry_resources: TailTelemetryResources | None) -> dict[str, Any]:
    ask = float(row["decision_entry_price"])
    notional = float(args.hypothetical_notional_usd)
    shares = notional / ask if ask > 0 else None
    row_dict = dict(row)
    tail_telemetry = build_low_price_yes_tail_telemetry(row_dict, tail_telemetry_resources)
    return {
        "record_type": "low_price_yes_lottery_reversal_shadow_candidate",
        "journal_schema_version": 1,
        "strategy_family": "low_price_yes_lottery_reversal",
        "strategy_id": STRATEGY_ID,
        "rule_id": RULE_ID,
        "shadow_decision_id": decision_id(row),
        "created_at_utc": now_utc(),
        "execution_mode": "zero_notional_shadow",
        "no_order_placed": True,
        "shadow_notional_usd": 0.0,
        "hypothetical_notional_usd": notional,
        "hypothetical_shares": shares,
        "city": json_ready(row["city"]),
        "city_pool": json_ready(row["city_pool"]),
        "icao": json_ready(row["icao"]),
        "target_date": json_ready(row["event_date"]),
        "event_date": json_ready(row["event_date"]),
        "unit": json_ready(row["unit"]),
        "bracket": json_ready(row["bracket"]),
        "side": "BUY_YES",
        "condition_id": json_ready(row["condition_id"]),
        "market_id": json_ready(row["market_id"]),
        "candidate_id": json_ready(row["candidate_id"]),
        "decision_snapshot_ts_utc": json_ready(row["decision_snapshot_ts_utc"]),
        "first_seen_ts_utc": json_ready(row["first_seen_ts_utc"]),
        "last_seen_ts_utc": json_ready(row["last_seen_ts_utc"]),
        "n_snapshots": json_ready(row["n_snapshots"]),
        "time_bucket": json_ready(row["time_bucket"]),
        "window": json_ready(row["window"]),
        "decision_window_label": json_ready(row["decision_window_label"]),
        "decision_hours_to_settle": json_ready(row["decision_hours_to_settle"]),
        "decision_entry_price": ask,
        "ask": ask,
        "market_yes_price": json_ready(row["market_yes_price"]),
        "model_p_yes": json_ready(row["model_p_yes"]),
        "edge": json_ready(row["edge"]),
        "abs_edge": json_ready(row["abs_edge"]),
        "edge_max": json_ready(row["edge_max"]),
        "edge_mean": json_ready(row["edge_mean"]),
        "best_entry_price": json_ready(row["best_entry_price"]),
        "yes_spread": json_ready(row["yes_spread"]),
        "no_spread": json_ready(row["no_spread"]),
        "yes_depth_ask_5c": json_ready(row["yes_depth_ask_5c"]),
        "no_depth_ask_5c": json_ready(row["no_depth_ask_5c"]),
        "forecast_source": json_ready(row["forecast_source"]),
        "forecast_peak_source": json_ready(row["forecast_peak_source"]),
        "forecast_timezone": json_ready(row["forecast_timezone"]),
        "forecast_max_f": json_ready(row["forecast_max_f"]),
        "forecast_max_native": json_ready(row["forecast_max_native"]),
        "forecast_peak_hour_local": json_ready(row["forecast_peak_hour_local"]),
        "forecast_peak_time_local": json_ready(row["forecast_peak_time_local"]),
        "forecast_peak_hour_utc": json_ready(row["forecast_peak_hour_utc"]),
        "forecast_peak_time_utc": json_ready(row["forecast_peak_time_utc"]),
        "forecast_hourly_count": json_ready(row["forecast_hourly_count"]),
        "forecast_peak_delta_hours_local": json_ready(row["forecast_peak_delta_hours_local"]),
        "forecast_max_in_bracket": json_ready(row["forecast_max_in_bracket"]),
        "forecast_max_above_bracket_f": json_ready(row["forecast_max_above_bracket_f"]),
        "forecast_max_below_bracket_f": json_ready(row["forecast_max_below_bracket_f"]),
        "model_version": json_ready(row["model_version"]),
        **tail_telemetry,
        "settlement_status_at_capture": json_ready(row["settlement_status"]),
        "final_yes_at_capture": json_ready(row["final_yes"]),
        "bracket_hit_at_capture": json_ready(row["bracket_hit"]),
        "settlement_key": {
            "city": json_ready(row["city"]),
            "target_date": json_ready(row["event_date"]),
            "bracket": json_ready(row["bracket"]),
        },
        "settlement_source_expected": "settlement_outcomes city/date/bracket fallback",
        "config": {
            "min_ask": float(args.min_ask),
            "max_ask": float(args.max_ask),
            "min_edge": float(args.min_edge),
            "dedupe": "one_per_city_date_first_snapshot_then_lowest_ask",
            "allow_settled": bool(args.allow_settled),
        },
        "source_db": rel(Path(args.db)),
        "source_report": SOURCE_REPORT,
        "fact_built_at_utc": json_ready(row["fact_built_at_utc"]),
    }


def summarize_rows(rows: list[sqlite3.Row]) -> dict[str, Any]:
    if not rows:
        return {
            "selected_rows_before_dedupe": 0,
            "selected_dates": 0,
            "selected_cities": 0,
            "selected_min_event_date": None,
            "selected_max_event_date": None,
            "selected_avg_ask": None,
            "selected_avg_edge": None,
            "selected_hypothetical_cost_usd": 0.0,
        }
    asks = [float(row["decision_entry_price"]) for row in rows]
    edges = [float(row["edge"]) for row in rows]
    dates = [str(row["event_date"]) for row in rows]
    cities = {str(row["city"]) for row in rows}
    return {
        "selected_rows_before_dedupe": len(rows),
        "selected_dates": len(set(dates)),
        "selected_cities": len(cities),
        "selected_min_event_date": min(dates),
        "selected_max_event_date": max(dates),
        "selected_avg_ask": sum(asks) / len(asks),
        "selected_avg_edge": sum(edges) / len(edges),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    journal_path = Path(args.journal)
    summary_path = Path(args.summary)
    history_path = Path(args.summary_history)

    generated_at = now_utc()
    with connect(db_path) as conn:
        min_event_date = effective_min_event_date(conn, args)
        raw_counts = count_raw(conn, args, min_event_date)
        rows = load_candidates(conn, args, min_event_date)

    tail_telemetry_resources = load_tail_telemetry_resources_soft()
    seen = existing_ids(journal_path)
    appended = 0
    skipped_existing = 0
    entries: list[dict[str, Any]] = []
    for row in rows:
        entry = journal_row(row, args, tail_telemetry_resources)
        if entry["shadow_decision_id"] in seen:
            skipped_existing += 1
            continue
        entries.append(entry)
        seen.add(entry["shadow_decision_id"])
        appended += 1

    if not args.dry_run:
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with journal_path.open("a", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")

    selected_summary = summarize_rows(rows)
    selected_summary["selected_hypothetical_cost_usd"] = float(len(rows) * args.hypothetical_notional_usd)
    summary = {
        "generated_at_utc": generated_at,
        "strategy_family": "low_price_yes_lottery_reversal",
        "strategy_id": STRATEGY_ID,
        "rule_id": RULE_ID,
        "execution_mode": "zero_notional_shadow",
        "no_order_placed": True,
        "dry_run": bool(args.dry_run),
        "db": rel(db_path),
        "journal": rel(journal_path),
        "summary": rel(summary_path),
        "source_report": SOURCE_REPORT,
        "effective_min_event_date": min_event_date,
        "effective_max_event_date": args.max_event_date,
        "raw_matching_rows": raw_counts,
        "appended": appended,
        "skipped_existing": skipped_existing,
        "known_shadow_decision_ids": len(seen),
        "hypothetical_notional_usd": float(args.hypothetical_notional_usd),
        "tail_telemetry_status_counts": {
            status: sum(1 for entry in entries if str(entry.get("tail_telemetry_status") or "") == status)
            for status in sorted({str(entry.get("tail_telemetry_status") or "") for entry in entries})
        },
        "tail_telemetry_model_artifact": str(entries[0].get("tail_telemetry_model_artifact") or "") if entries else "",
        "tail_telemetry_bias_source": str(entries[0].get("tail_telemetry_bias_source") or "") if entries else "",
        "config": {
            "min_ask": float(args.min_ask),
            "max_ask": float(args.max_ask),
            "min_edge": float(args.min_edge),
            "max_candidates_per_run": int(args.max_candidates_per_run),
            "allow_settled": bool(args.allow_settled),
            "dedupe": "one_per_city_date_first_snapshot_then_lowest_ask",
        },
        **selected_summary,
    }
    if not args.dry_run:
        write_json(summary_path, summary)
        append_jsonl(history_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
