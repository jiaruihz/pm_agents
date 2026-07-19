#!/usr/bin/env python3
"""Audit H1 late-carry maker execution on a planned-opportunity denominator.

The live comparison pairs the taker and maker children of the same signal.
Maker fallbacks remain maker-chain outcomes but are not counted as passive
maker fills.  Recent shadow signals are reduced to the first valid strong
state with a direct H1 quote for each city/target-date.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import random
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from weather_execution_module_compare import build_report


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INSTANCE = "current_yes_heat_death_tiny_live_h1_late_carry_v1"
DEFAULT_FULL_LADDER_DIR = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/full_ladder_output/orderbook_snapshots"
)


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def observation_valid(row: dict[str, Any]) -> bool:
    age = finite(row.get("obs_age_minutes") or row.get("obs_age_min"))
    cadence = finite(row.get("expected_report_cadence") or row.get("observation_cadence_min"))
    return bool(
        str(row.get("obs_status") or "").lower() == "ok"
        and str(row.get("station_gap_state") or "") == "within_expected_cadence"
        and parse_utc(row.get("source_report_ts_utc")) is not None
        and age is not None
        and age >= 0
        and cadence is not None
        and cadence > 0
    )


def load_shadow_denominators(path: Path) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
    raw_rows = 0
    first_strong: dict[tuple[str, str], dict[str, Any]] = {}
    first_h1: dict[tuple[str, str], dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            raw_rows += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or not row.get("physical_confirmation_strong"):
                continue
            if not observation_valid(row):
                continue
            city = str(row.get("city") or "")
            target_date = str(row.get("target_date") or "")
            timestamp = str(row.get("decision_snapshot_ts_utc") or "")
            if not city or not target_date or not timestamp:
                continue
            key = (city, target_date)
            if key not in first_strong or timestamp < str(first_strong[key].get("decision_snapshot_ts_utc") or ""):
                first_strong[key] = row
            ask = finite(row.get("current_yes_ask"))
            if ask is None or not 0.95 <= ask <= 0.99:
                continue
            if str(row.get("current_yes_book_status") or "") != "ok":
                continue
            if key not in first_h1 or timestamp < str(first_h1[key].get("decision_snapshot_ts_utc") or ""):
                first_h1[key] = row
    return raw_rows, list(first_strong.values()), list(first_h1.values())


def settlement_map(conn: sqlite3.Connection) -> dict[tuple[str, str, str], float]:
    rows = conn.execute(
        """
        SELECT city, target_date, bracket, final_price
        FROM settlement_outcomes
        WHERE source_system='pm_history' AND settlement_status='settled'
        """
    ).fetchall()
    return {
        (str(row["city"]), str(row["target_date"]), str(row["bracket"])): float(row["final_price"])
        for row in rows
    }


def taker_fee_per_share(price: float) -> float:
    return round(0.05 * price * (1.0 - price), 5)


def block_bootstrap_roi(rows: list[dict[str, Any]], *, samples: int = 20_000) -> list[float] | None:
    blocks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("settled"):
            blocks[str(row["target_date"])].append(row)
    dates = sorted(blocks)
    if len(dates) < 2:
        return None
    rng = random.Random(20260720)
    values: list[float] = []
    for _ in range(samples):
        sampled_dates = [rng.choice(dates) for _ in dates]
        sample_rows = [row for date in sampled_dates for row in blocks[date]]
        cost = sum(float(row["effective_cost_per_share"]) for row in sample_rows)
        pnl = sum(float(row["pnl_per_share"]) for row in sample_rows)
        if cost > 0:
            values.append(pnl / cost)
    values.sort()
    if not values:
        return None
    return [values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))]]


def slice_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [row for row in rows if row["settled"]]
    cost = sum(float(row["effective_cost_per_share"]) for row in settled)
    pnl = sum(float(row["pnl_per_share"]) for row in settled)
    return {
        "rows": len(rows),
        "settled_rows": len(settled),
        "dates": len({row["target_date"] for row in settled}),
        "cities": len({row["city"] for row in settled}),
        "wins": sum(1 for row in settled if float(row["final_yes"]) == 1.0),
        "avg_ask": sum(float(row["ask"]) for row in settled) / len(settled) if settled else None,
        "fee_adjusted_roi": pnl / cost if cost else None,
        "target_date_block_ci95": block_bootstrap_roi(settled),
    }


def descriptive_shadow_slices(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    definitions = {
        "decision_time:<15": lambda row: float(row["decision_hour_local"]) < 15,
        "decision_time:15-17": lambda row: float(row["decision_hour_local"]) >= 15,
        "ask:0.950-0.969": lambda row: float(row["ask"]) < 0.97,
        "ask:0.970-0.979": lambda row: 0.97 <= float(row["ask"]) < 0.98,
        "ask:0.980-0.989": lambda row: 0.98 <= float(row["ask"]) < 0.99,
        "ask:0.990": lambda row: float(row["ask"]) >= 0.99,
        "spread:<=1c": lambda row: row["spread"] is not None and float(row["spread"]) <= 0.01,
        "spread:1-2c": lambda row: row["spread"] is not None and 0.01 < float(row["spread"]) <= 0.02,
        "spread:2-4c": lambda row: row["spread"] is not None and 0.02 < float(row["spread"]) <= 0.04,
        "spread:>4c": lambda row: row["spread"] is not None and float(row["spread"]) > 0.04,
        "ask_depth:<10": lambda row: row["ask_size"] is not None and float(row["ask_size"]) < 10,
        "ask_depth:10-49": lambda row: row["ask_size"] is not None and 10 <= float(row["ask_size"]) < 50,
        "ask_depth:>=50": lambda row: row["ask_size"] is not None and float(row["ask_size"]) >= 50,
    }
    output: list[dict[str, Any]] = []
    for name, predicate in definitions.items():
        sample = [row for row in rows if predicate(row)]
        output.append({"slice": name, **slice_metrics(sample)})
    return output


def enrich_shadow(rows: list[dict[str, Any]], settlements: dict[tuple[str, str, str], float]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: (str(item.get("target_date")), str(item.get("city")))):
        ask = float(row["current_yes_ask"])
        bracket = str(row.get("current_bracket") or "")
        final_yes = settlements.get((str(row.get("city")), str(row.get("target_date")), bracket))
        fee = taker_fee_per_share(ask)
        effective_cost = ask + fee
        settled = final_yes is not None
        output.append(
            {
                "target_date": str(row.get("target_date") or ""),
                "city": str(row.get("city") or ""),
                "bracket": bracket,
                "decision_snapshot_ts_utc": str(row.get("decision_snapshot_ts_utc") or ""),
                "decision_hour_local": finite(row.get("decision_hour_local")),
                "ask": ask,
                "bid": finite(row.get("current_yes_bid")),
                "spread": ask - float(row["current_yes_bid"]) if finite(row.get("current_yes_bid")) is not None else None,
                "ask_size": finite(row.get("current_yes_ask_size")),
                "support_count": int(finite(row.get("physical_support_count")) or 0),
                "forecast_peak_delta_hours_local": finite(row.get("forecast_peak_delta_hours_local")),
                "minutes_since_running_max": finite(row.get("minutes_since_running_max")),
                "decline_native": finite(row.get("decline_native")),
                "final_yes": final_yes,
                "settled": settled,
                "effective_cost_per_share": effective_cost,
                "pnl_per_share": (final_yes - effective_cost) if settled else None,
            }
        )
    return output


def role_for_fill(row: sqlite3.Row) -> str:
    role = str(row["child_order_role"] or "")
    if bool(row["maker_only"]):
        return "passive_maker"
    if "fallback" in role:
        return "taker_fallback"
    if role == "taker":
        return "taker"
    return role or "other"


def paired_live_rows(conn: sqlite3.Connection, execution_report: dict[str, Any], instance: str) -> list[dict[str, Any]]:
    fact_rows = conn.execute(
        """
        SELECT signal_id, target_date, city, bracket, child_order_role, maker_only,
               fill_price, fill_qty, fees_usd, pnl_usd_at_fill, settlement_status, final_yes
        FROM fact_trades
        WHERE instance_id=?
        """,
        (instance,),
    ).fetchall()
    fills: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in fact_rows:
        fills[str(row["signal_id"])].append(row)
    chain_by_signal_role: dict[tuple[str, str], dict[str, Any]] = {}
    for chain in execution_report["chains"]:
        role = str(chain.get("root_child_order_role") or "")
        if role in {"maker", "taker"}:
            chain_by_signal_role[(str(chain["signal_id"]), role)] = chain

    output: list[dict[str, Any]] = []
    for pair in execution_report["paired_taker_maker"]["pairs"]:
        signal = str(pair["signal_id"])
        maker_chain = chain_by_signal_role[(signal, "maker")]
        signal_fills = fills.get(signal, [])
        taker_fills = [row for row in signal_fills if role_for_fill(row) == "taker"]
        passive_fills = [row for row in signal_fills if role_for_fill(row) == "passive_maker"]
        fallback_fills = [row for row in signal_fills if role_for_fill(row) == "taker_fallback"]
        maker_fills = [*passive_fills, *fallback_fills]
        taker_pnl = sum(float(row["pnl_usd_at_fill"] or 0.0) for row in taker_fills)
        maker_pnl = sum(float(row["pnl_usd_at_fill"] or 0.0) for row in maker_fills)
        taker_fees = sum(float(row["fees_usd"] or 0.0) for row in taker_fills)
        maker_fees = sum(float(row["fees_usd"] or 0.0) for row in maker_fills)
        if passive_fills:
            route = "passive_fill"
        elif fallback_fills:
            route = "taker_fallback"
        else:
            route = "unfilled"
        output.append(
            {
                "target_date": pair["target_date"],
                "city": pair["city"],
                "signal_id": signal,
                "root_best_bid": maker_chain.get("root_best_bid"),
                "root_best_ask": maker_chain.get("root_best_ask"),
                "root_spread": (
                    float(maker_chain["root_best_ask"]) - float(maker_chain["root_best_bid"])
                    if maker_chain.get("root_best_ask") is not None and maker_chain.get("root_best_bid") is not None
                    else None
                ),
                "root_maker_limit": maker_chain.get("root_limit_price"),
                "attempt_count": maker_chain.get("attempt_count"),
                "maker_route": route,
                "taker_price": pair.get("taker_average_fill_price"),
                "maker_price": pair.get("maker_average_fill_price"),
                "taker_fees": taker_fees,
                "maker_fees": maker_fees,
                "taker_pnl": taker_pnl,
                "maker_chain_pnl": maker_pnl,
                "maker_minus_taker_pnl": maker_pnl - taker_pnl,
                "maker_filled_shares": sum(float(row["fill_qty"] or 0.0) for row in maker_fills),
                "passive_filled_shares": sum(float(row["fill_qty"] or 0.0) for row in passive_fills),
                "fallback_filled_shares": sum(float(row["fill_qty"] or 0.0) for row in fallback_fills),
                "settled": bool(taker_fills and all(row["settlement_status"] == "settled" for row in taker_fills)),
                "final_yes": float(taker_fills[0]["final_yes"]) if taker_fills else None,
            }
        )
    return output


def h1_fill_opportunities(conn: sqlite3.Connection, instance: str) -> list[dict[str, Any]]:
    fact_rows = conn.execute(
        """
        SELECT f.target_date, f.city, f.bracket, s.token_id,
               MIN(f.fill_ts_utc) AS first_fill_ts_utc,
               MIN(f.fill_price) AS min_fill_price,
               MAX(f.fill_price) AS max_fill_price,
               COUNT(DISTINCT f.fill_id) AS fill_rows
        FROM fact_trades f
        JOIN signals s USING(signal_id)
        WHERE f.instance_id=?
        GROUP BY f.target_date, f.city, f.bracket, s.token_id
        ORDER BY f.target_date, f.city
        """,
        (instance,),
    ).fetchall()
    order_rows = conn.execute(
        """
        SELECT s.target_date, s.city, s.bracket, s.token_id,
               o.placed_at_utc, o.child_order_role, o.status, o.clob_status,
               o.best_bid, o.best_ask, o.posted_price, o.maker_only
        FROM orders o
        JOIN plans p USING(plan_id)
        JOIN signals s USING(signal_id)
        WHERE o.instance_id=?
        ORDER BY o.placed_at_utc
        """,
        (instance,),
    ).fetchall()
    orders_by_key: dict[tuple[str, str, str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in order_rows:
        key = (str(row["target_date"]), str(row["city"]), str(row["bracket"]), str(row["token_id"] or ""))
        orders_by_key[key].append(row)

    output: list[dict[str, Any]] = []
    for fact in fact_rows:
        key = (
            str(fact["target_date"]),
            str(fact["city"]),
            str(fact["bracket"]),
            str(fact["token_id"] or ""),
        )
        orders = orders_by_key.get(key, [])
        submitted = [row for row in orders if str(row["status"] or "") == "submitted"]
        quoted = [
            row
            for row in orders
            if finite(row["best_ask"]) is not None and float(row["best_ask"]) > 0
            and finite(row["best_bid"]) is not None and float(row["best_bid"]) > 0
        ]
        maker_orders = [
            row
            for row in submitted
            if bool(row["maker_only"])
            or str(row["child_order_role"] or "") in {"maker", "h1_maker_reprice"}
        ]
        maker_quoted = [
            row
            for row in maker_orders
            if finite(row["best_ask"]) is not None and float(row["best_ask"]) > 0
            and finite(row["best_bid"]) is not None and float(row["best_bid"]) > 0
        ]
        entry_quote = maker_quoted[0] if maker_quoted else (quoted[0] if quoted else None)
        maker_prices = [float(row["posted_price"]) for row in maker_orders if finite(row["posted_price"]) is not None]
        maker_lifecycle_quotes = [
            row for row in maker_orders
            if finite(row["best_bid"]) is not None and float(row["best_bid"]) > 0
            and finite(row["best_ask"]) is not None and float(row["best_ask"]) > 0
        ]
        entry_bid = finite(entry_quote["best_bid"]) if entry_quote else None
        entry_ask = finite(entry_quote["best_ask"]) if entry_quote else None
        first_maker = maker_prices[0] if maker_prices else None
        output.append(
            {
                "target_date": key[0],
                "city": key[1],
                "bracket": key[2],
                "token_id": key[3],
                "first_order_ts_utc": str(submitted[0]["placed_at_utc"] or "") if submitted else "",
                "entry_best_bid": entry_bid,
                "entry_best_ask": entry_ask,
                "entry_spread": entry_ask - entry_bid if entry_ask is not None and entry_bid is not None else None,
                "initial_maker_price": first_maker,
                "maker_headroom_to_ask": entry_ask - first_maker
                if entry_ask is not None and first_maker is not None else None,
                "initial_maker_minus_best_bid": first_maker - entry_bid
                if entry_bid is not None and first_maker is not None else None,
                "max_maker_price": max(maker_prices) if maker_prices else None,
                "maker_attempts": len(maker_prices),
                "maker_lifecycle_quotes_with_ask": len(maker_lifecycle_quotes),
                "maker_lifecycle_rows_missing_book_fields": len(maker_orders) - len(maker_lifecycle_quotes),
                "min_fill_price": float(fact["min_fill_price"]),
                "max_fill_price": float(fact["max_fill_price"]),
                "fill_rows": int(fact["fill_rows"]),
                "first_fill_ts_utc": str(fact["first_fill_ts_utc"] or ""),
            }
        )
    return output


def snapshot_files_for_dates(base: Path, target_dates: set[str]) -> list[Path]:
    folder_dates: set[str] = set()
    for value in target_dates:
        parsed = date.fromisoformat(value)
        folder_dates.add(parsed.isoformat())
        folder_dates.add((parsed + timedelta(days=1)).isoformat())
    return sorted(
        path
        for folder_date in folder_dates
        for path in (base / folder_date).glob("*.jsonl.gz")
    )


def load_full_ladder_rows(base: Path, opportunities: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    tokens = {str(row["token_id"]) for row in opportunities if row.get("token_id")}
    rows_by_token: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in snapshot_files_for_dates(base, {str(row["target_date"]) for row in opportunities}):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                token = str(row.get("token_id") or "")
                if token not in tokens:
                    continue
                status = str(row.get("status") or "")
                raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
                asks = raw.get("asks") if isinstance(raw.get("asks"), list) else None
                bids = raw.get("bids") if isinstance(raw.get("bids"), list) else None
                summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
                rows_by_token[token].append(
                    {
                        "snapshot_ts_utc": str(row.get("snapshot_ts_utc") or ""),
                        "status": status,
                        "best_bid": finite(summary.get("best_bid")),
                        "best_ask": finite(summary.get("best_ask")),
                        "bid_levels": len(bids) if bids is not None else None,
                        "ask_levels": len(asks) if asks is not None else None,
                        "explicit_empty_ask": bool(status == "ok" and asks is not None and len(asks) == 0),
                        "source_file": str(path),
                    }
                )
    for token, rows in rows_by_token.items():
        deduped = {str(row["snapshot_ts_utc"]): row for row in rows}
        rows_by_token[token] = sorted(deduped.values(), key=lambda row: str(row["snapshot_ts_utc"]))
    return rows_by_token


def empty_ask_runs(rows: list[dict[str, Any]], entry_ts: datetime | None) -> list[dict[str, Any]]:
    valid = [row for row in rows if parse_utc(row.get("snapshot_ts_utc")) is not None]
    runs: list[dict[str, Any]] = []
    index = 0
    while index < len(valid):
        if not valid[index]["explicit_empty_ask"]:
            index += 1
            continue
        start = index
        while index + 1 < len(valid) and valid[index + 1]["explicit_empty_ask"]:
            index += 1
        end = index
        first_ts = parse_utc(valid[start]["snapshot_ts_utc"])
        last_ts = parse_utc(valid[end]["snapshot_ts_utc"])
        previous_ts = parse_utc(valid[start - 1]["snapshot_ts_utc"]) if start > 0 else None
        next_ts = parse_utc(valid[end + 1]["snapshot_ts_utc"]) if end + 1 < len(valid) else None
        assert first_ts is not None and last_ts is not None
        runs.append(
            {
                "first_empty_snapshot_ts_utc": first_ts.isoformat(),
                "last_empty_snapshot_ts_utc": last_ts.isoformat(),
                "previous_non_empty_snapshot_ts_utc": previous_ts.isoformat() if previous_ts else None,
                "previous_best_ask": valid[start - 1]["best_ask"] if start > 0 else None,
                "next_non_empty_snapshot_ts_utc": next_ts.isoformat() if next_ts else None,
                "next_best_ask": valid[end + 1]["best_ask"] if end + 1 < len(valid) else None,
                "empty_snapshot_count": end - start + 1,
                "observed_lower_bound_min": (last_ts - first_ts).total_seconds() / 60.0,
                "transition_interval_upper_bound_min": (
                    (next_ts - previous_ts).total_seconds() / 60.0
                    if previous_ts is not None and next_ts is not None else None
                ),
                "left_censored": previous_ts is None,
                "right_censored": next_ts is None,
                "starts_after_entry": bool(entry_ts is not None and first_ts >= entry_ts),
                "minutes_from_entry_to_first_empty": (
                    (first_ts - entry_ts).total_seconds() / 60.0 if entry_ts is not None else None
                ),
            }
        )
        index += 1
    return runs


def enrich_book_structure(
    opportunities: list[dict[str, Any]], rows_by_token: dict[str, list[dict[str, Any]]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    output: list[dict[str, Any]] = []
    all_runs: list[dict[str, Any]] = []
    for opportunity in opportunities:
        rows = rows_by_token.get(str(opportunity["token_id"]), [])
        entry_ts = parse_utc(opportunity.get("first_order_ts_utc"))
        before = [row for row in rows if parse_utc(row["snapshot_ts_utc"]) <= entry_ts] if entry_ts else []
        after = [row for row in rows if parse_utc(row["snapshot_ts_utc"]) >= entry_ts] if entry_ts else []
        prior = before[-1] if before else None
        following = after[0] if after else None
        runs = empty_ask_runs(rows, entry_ts)
        post_entry_runs = [row for row in runs if row["starts_after_entry"]]
        for run in runs:
            all_runs.append(
                {
                    "target_date": opportunity["target_date"],
                    "city": opportunity["city"],
                    "bracket": opportunity["bracket"],
                    **run,
                }
            )
        output.append(
            {
                **opportunity,
                "archive_snapshot_rows": len(rows),
                "archive_ok_rows": sum(1 for row in rows if row["status"] == "ok"),
                "archive_non_ok_rows": sum(1 for row in rows if row["status"] != "ok"),
                "prior_snapshot_ts_utc": prior["snapshot_ts_utc"] if prior else None,
                "prior_snapshot_best_bid": prior["best_bid"] if prior else None,
                "prior_snapshot_best_ask": prior["best_ask"] if prior else None,
                "prior_snapshot_empty_ask": prior["explicit_empty_ask"] if prior else None,
                "next_snapshot_ts_utc": following["snapshot_ts_utc"] if following else None,
                "next_snapshot_best_bid": following["best_bid"] if following else None,
                "next_snapshot_best_ask": following["best_ask"] if following else None,
                "next_snapshot_empty_ask": following["explicit_empty_ask"] if following else None,
                "empty_ask_snapshot_rows": sum(1 for row in rows if row["explicit_empty_ask"]),
                "empty_ask_runs": len(runs),
                "post_entry_empty_ask_runs": len(post_entry_runs),
                "first_post_entry_empty_ts_utc": (
                    post_entry_runs[0]["first_empty_snapshot_ts_utc"] if post_entry_runs else None
                ),
                "minutes_entry_to_first_empty": (
                    post_entry_runs[0]["minutes_from_entry_to_first_empty"] if post_entry_runs else None
                ),
                "max_post_entry_empty_observed_lower_bound_min": max(
                    (float(row["observed_lower_bound_min"]) for row in post_entry_runs), default=None
                ),
                "post_entry_empty_right_censored": any(bool(row["right_censored"]) for row in post_entry_runs),
            }
        )
    return output, all_runs


def write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    materialized = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not materialized:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(materialized[0]))
        writer.writeheader()
        writer.writerows(materialized)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="runtime/weather.db")
    parser.add_argument(
        "--shadow-decisions",
        default="runtime/weather_edge_v1/current_yes_heat_death_shadow_v1/state_decisions.jsonl",
    )
    parser.add_argument("--instance", default=DEFAULT_INSTANCE)
    parser.add_argument(
        "--full-ladder-dir",
        default=str(DEFAULT_FULL_LADDER_DIR),
        help="Full-ladder snapshot root; explicit empty ask lists are treated as book evidence.",
    )
    parser.add_argument(
        "--output-dir",
        default="docs/analysis/2026-07/generated/h1_late_carry_maker_v1",
    )
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    shadow_path = Path(args.shadow_decisions).resolve()
    full_ladder_dir = Path(args.full_ladder_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    if not full_ladder_dir.is_dir():
        parser.error(f"full-ladder directory does not exist: {full_ladder_dir}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    try:
        raw_rows, first_strong, first_h1 = load_shadow_denominators(shadow_path)
        shadow_rows = enrich_shadow(first_h1, settlement_map(conn))
        execution_report = build_report(conn, instances=[args.instance])
        live_pairs = paired_live_rows(conn, execution_report, args.instance)
        fill_opportunities = h1_fill_opportunities(conn, args.instance)
        self_check = {
            "fact_built_at_utc": conn.execute("SELECT MAX(fact_built_at_utc) FROM fact_trades").fetchone()[0],
            "latest_fill_ts_utc": conn.execute("SELECT MAX(fill_ts_utc) FROM fact_trades").fetchone()[0],
            "trade_class": dict(conn.execute("SELECT trade_class,COUNT(*) FROM fact_trades GROUP BY trade_class")),
            "settlement_status": dict(
                conn.execute("SELECT settlement_status,COUNT(*) FROM fact_trades GROUP BY settlement_status")
            ),
        }
    finally:
        conn.close()

    ladder_rows = load_full_ladder_rows(full_ladder_dir, fill_opportunities)
    book_structure, empty_runs = enrich_book_structure(fill_opportunities, ladder_rows)
    opportunity_by_token = {str(row["token_id"]): row for row in fill_opportunities}
    book_timeline = [
        {
            "target_date": opportunity_by_token[token]["target_date"],
            "city": opportunity_by_token[token]["city"],
            "bracket": opportunity_by_token[token]["bracket"],
            **row,
        }
        for token, rows in ladder_rows.items()
        for row in rows
    ]
    book_timeline.sort(key=lambda row: (str(row["target_date"]), str(row["city"]), str(row["snapshot_ts_utc"])))

    settled_shadow = [row for row in shadow_rows if row["settled"]]
    shadow_cost = sum(float(row["effective_cost_per_share"]) for row in settled_shadow)
    shadow_pnl = sum(float(row["pnl_per_share"]) for row in settled_shadow)
    passive_pairs = [row for row in live_pairs if row["maker_route"] == "passive_fill"]
    fallback_pairs = [row for row in live_pairs if row["maker_route"] == "taker_fallback"]
    unfilled_pairs = [row for row in live_pairs if row["maker_route"] == "unfilled"]
    paired_taker_pnl = sum(float(row["taker_pnl"]) for row in live_pairs)
    paired_maker_pnl = sum(float(row["maker_chain_pnl"]) for row in live_pairs)
    passive_common_shares = sum(float(row["passive_filled_shares"]) for row in passive_pairs)
    passive_saving = sum(float(row["maker_minus_taker_pnl"]) for row in passive_pairs)
    maker_summary = next(
        row for row in execution_report["summary"]
        if row["root_child_order_role"] == "maker" and row["execution_profile"] == "split_taker_maker_chase_v1"
    )
    taker_summary = next(
        row for row in execution_report["summary"]
        if row["root_child_order_role"] == "taker" and row["execution_profile"] == "split_taker_maker_chase_v1"
    )
    shadow_slices = descriptive_shadow_slices(shadow_rows)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "inputs": {
            "db": str(db_path),
            "db_mtime_utc": datetime.fromtimestamp(db_path.stat().st_mtime, timezone.utc).isoformat(),
            "shadow_decisions": str(shadow_path),
            "shadow_mtime_utc": datetime.fromtimestamp(shadow_path.stat().st_mtime, timezone.utc).isoformat(),
            "full_ladder_dir": str(full_ladder_dir),
            "instance": args.instance,
        },
        "self_check": self_check,
        "signal_funnel": {
            "raw_shadow_decision_rows": raw_rows,
            "first_valid_strong_city_days": len(first_strong),
            "first_valid_strong_dates": len({row["target_date"] for row in first_strong}),
            "first_valid_strong_cities": len({row["city"] for row in first_strong}),
            "first_direct_h1_quote_city_days": len(shadow_rows),
            "first_direct_h1_quote_dates": len({row["target_date"] for row in shadow_rows}),
            "first_direct_h1_quote_cities": len({row["city"] for row in shadow_rows}),
        },
        "evidence_funnel": {
            "h1_direct_quote_city_days": len(shadow_rows),
            "h1_settled_city_days": len(settled_shadow),
            "live_submitted_opportunities": len({chain["signal_id"] for chain in execution_report["chains"]}),
            "paired_split_opportunities": len(live_pairs),
            "taker_filled_pairs": sum(1 for row in live_pairs if row["taker_pnl"] is not None),
            "passive_maker_filled_pairs": len(passive_pairs),
            "maker_taker_fallback_pairs": len(fallback_pairs),
            "maker_unfilled_pairs": len(unfilled_pairs),
        },
        "recent_shadow_h1": {
            "settled_rows": len(settled_shadow),
            "wins": sum(1 for row in settled_shadow if float(row["final_yes"]) == 1.0),
            "fee_adjusted_roi": shadow_pnl / shadow_cost if shadow_cost else None,
            "target_date_block_ci95": block_bootstrap_roi(shadow_rows),
            "cost_usd_per_one_share_each": shadow_cost,
            "pnl_usd_per_one_share_each": shadow_pnl,
        },
        "recent_shadow_descriptive_slices": shadow_slices,
        "paired_live_execution": {
            "opportunities": len(live_pairs),
            "passive_fill_rate_opportunities": len(passive_pairs) / len(live_pairs) if live_pairs else None,
            "maker_chain_completion_rate": (len(passive_pairs) + len(fallback_pairs)) / len(live_pairs) if live_pairs else None,
            "passive_saving_usd": passive_saving,
            "passive_common_shares": passive_common_shares,
            "passive_saving_per_common_share": passive_saving / passive_common_shares if passive_common_shares else None,
            "taker_pnl_usd": paired_taker_pnl,
            "maker_chain_pnl_usd": paired_maker_pnl,
            "maker_minus_taker_pnl_usd": paired_maker_pnl - paired_taker_pnl,
            "maker_planned_notional_usd": maker_summary["planned_notional_usd"],
            "taker_planned_notional_usd": taker_summary["planned_notional_usd"],
            "maker_realized_pnl_on_planned_notional": maker_summary["realized_pnl_on_planned_notional"],
            "taker_realized_pnl_on_planned_notional": taker_summary["realized_pnl_on_planned_notional"],
            "planned_notional_roi_delta": (
                float(maker_summary["realized_pnl_on_planned_notional"])
                - float(taker_summary["realized_pnl_on_planned_notional"])
            ),
            "historical_maker_planned_shares_per_opportunity": 5,
            "current_process_maker_shares_per_opportunity": 10,
            "current_10_share_maker_evidence_rows": 0,
            "fill_timestamp_note": (
                "clob_fills filled_at may be cache-reconciliation time; do not use first_fill_latency as exchange event time"
            ),
        },
        "historical_fill_book_structure": {
            "opportunities": len(book_structure),
            "entry_ask_at_099": sum(
                1 for row in book_structure if row["entry_best_ask"] is not None
                and math.isclose(float(row["entry_best_ask"]), 0.99, abs_tol=1e-9)
            ),
            "entry_ask_below_099": sum(
                1 for row in book_structure if row["entry_best_ask"] is not None
                and float(row["entry_best_ask"]) < 0.99
            ),
            "entry_ask_above_099": sum(
                1 for row in book_structure if row["entry_best_ask"] is not None
                and float(row["entry_best_ask"]) > 0.99
            ),
            "entry_quotes_with_ask": sum(1 for row in book_structure if row["entry_best_ask"] is not None),
            "opportunities_with_post_entry_empty_ask": sum(
                1 for row in book_structure if int(row["post_entry_empty_ask_runs"]) > 0
            ),
            "full_ladder_empty_duration_note": (
                "Snapshots are interval-censored. observed_lower_bound is first-to-last empty snapshot; "
                "transition_interval_upper_bound spans the surrounding non-empty snapshots when both exist."
            ),
        },
        "execution_report_freshness": execution_report["freshness"],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "recent_h1_shadow_signals.csv", shadow_rows)
    write_csv(output_dir / "recent_h1_shadow_slices.csv", shadow_slices)
    write_csv(output_dir / "live_paired_execution.csv", live_pairs)
    write_csv(output_dir / "historical_fill_book_structure.csv", book_structure)
    write_csv(output_dir / "historical_empty_ask_runs.csv", empty_runs)
    write_csv(output_dir / "historical_fill_book_timeline.csv", book_timeline)
    (output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
