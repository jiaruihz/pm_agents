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
import json
import math
import random
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from weather_execution_module_compare import build_report


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INSTANCE = "current_yes_heat_death_tiny_live_h1_late_carry_v1"


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
        "--output-dir",
        default="docs/analysis/2026-07/generated/h1_late_carry_maker_v1",
    )
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    shadow_path = Path(args.shadow_decisions).resolve()
    output_dir = Path(args.output_dir).resolve()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    try:
        raw_rows, first_strong, first_h1 = load_shadow_denominators(shadow_path)
        shadow_rows = enrich_shadow(first_h1, settlement_map(conn))
        execution_report = build_report(conn, instances=[args.instance])
        live_pairs = paired_live_rows(conn, execution_report, args.instance)
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
        "execution_report_freshness": execution_report["freshness"],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "recent_h1_shadow_signals.csv", shadow_rows)
    write_csv(output_dir / "recent_h1_shadow_slices.csv", shadow_slices)
    write_csv(output_dir / "live_paired_execution.csv", live_pairs)
    (output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
