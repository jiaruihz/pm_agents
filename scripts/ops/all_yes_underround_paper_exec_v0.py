#!/usr/bin/env python3
"""Paper executor and gate for all-YES underround baskets.

This script never places live orders. It turns scanner candidates into an
all-leg-or-none paper ledger, then evaluates those baskets against the
canonical `settlements` table by condition_id.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops.all_yes_underround_guards import BasketGuardConfig, check_candidate, decision_to_dict
SCAN_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-14-all-yes-underround-live-prep-v0.json"
DB_DEFAULT = ROOT / "runtime" / "weather.db"
GATE_DEFAULT = ROOT / "runtime" / "_dashboard_logs" / "clob_fill_coverage_gate.json"
RUN_DIR_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / "all_yes_underround_paper_v0"
STRATEGY_ID = "all_yes_underround_basket_v0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["cycle", "eval", "gate", "monitor"])
    parser.add_argument("--scan-json", default=str(SCAN_JSON_DEFAULT))
    parser.add_argument("--db-path", default=str(DB_DEFAULT))
    parser.add_argument("--gate-path", default=str(GATE_DEFAULT))
    parser.add_argument("--run-dir", default=str(RUN_DIR_DEFAULT))
    parser.add_argument("--shares-per-leg", type=float, default=5.0)
    parser.add_argument("--max-baskets-per-cycle", type=int, default=2)
    parser.add_argument("--max-basket-cost-usd", type=float, default=5.0)
    parser.add_argument("--min-underround", type=float, default=0.02)
    parser.add_argument("--max-spread", type=float, default=0.05)
    parser.add_argument("--max-snapshot-age-seconds", type=float, default=180.0)
    parser.add_argument("--min-settled-baskets", type=int, default=20)
    parser.add_argument("--min-settled-active-dates", type=int, default=7)
    parser.add_argument("--min-positive-basket-rate", type=float, default=0.55)
    parser.add_argument("--min-roi", type=float, default=0.02)
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def recording_ttl_audit(basket: dict[str, Any], max_age_seconds: float | None) -> dict[str, Any]:
    recorded_at = parse_utc(basket.get("recorded_at_utc"))
    snapshot_ts = parse_utc(
        basket.get("orderbook_fetched_at_utc_min")
        or basket.get("orderbook_fetched_at_utc_max")
        or basket.get("snapshot_ts_utc")
    )
    if recorded_at is None or snapshot_ts is None:
        return {
            "recording_age_seconds": None,
            "ttl_equivalent": False,
            "ttl_status": "missing_timestamp",
        }
    age = (recorded_at - snapshot_ts).total_seconds()
    if age < -1:
        return {
            "recording_age_seconds": round(age, 3),
            "ttl_equivalent": False,
            "ttl_status": "snapshot_ts_in_future",
        }
    if max_age_seconds is None:
        return {
            "recording_age_seconds": round(age, 3),
            "ttl_equivalent": None,
            "ttl_status": "ttl_not_configured",
        }
    if age > max_age_seconds:
        return {
            "recording_age_seconds": round(age, 3),
            "ttl_equivalent": False,
            "ttl_status": "stale_recording",
        }
    return {
        "recording_age_seconds": round(age, 3),
        "ttl_equivalent": True,
        "ttl_status": "live_equivalent",
    }


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")


def basket_id(scan: dict[str, Any], candidate: dict[str, Any]) -> str:
    snapshot_ts = scan.get("snapshot_summary", {}).get("snapshot_ts_utc_max")
    return "|".join(
        [
            STRATEGY_ID,
            str(snapshot_ts),
            str(candidate.get("event_date")),
            str(candidate.get("city")),
            str(candidate.get("event_slug")),
        ]
    )


def opportunity_key_from_parts(event_date: Any, city: Any, event_slug: Any) -> str:
    return "|".join([str(event_date), str(city), str(event_slug)])


def opportunity_key_from_basket(row: dict[str, Any]) -> str:
    return opportunity_key_from_parts(row.get("event_date"), row.get("city"), row.get("event_slug"))


def leg_order_id(bid: str, condition_id: str) -> str:
    return f"{bid}|{condition_id}"


def cycle(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir)
    basket_path = run_dir / "paper_baskets.jsonl"
    leg_path = run_dir / "paper_leg_orders.jsonl"
    scan = read_json(Path(args.scan_json))
    existing_rows = read_jsonl(basket_path)
    existing = {str(row.get("basket_id")) for row in existing_rows}
    existing_opportunities = {opportunity_key_from_basket(row) for row in existing_rows}
    guard_cfg = BasketGuardConfig(
        min_underround=args.min_underround,
        shares_per_leg=args.shares_per_leg,
        max_basket_cost_usd=args.max_basket_cost_usd,
        max_yes_spread=args.max_spread,
        max_candidates_per_cycle=args.max_baskets_per_cycle,
        max_snapshot_age_seconds=args.max_snapshot_age_seconds,
    )
    baskets_to_append: list[dict[str, Any]] = []
    legs_to_append: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    guard_audit: list[dict[str, Any]] = []

    for candidate in list(scan.get("paper_shadow_candidates") or [])[: args.max_baskets_per_cycle]:
        bid = basket_id(scan, candidate)
        opportunity_key = opportunity_key_from_parts(candidate.get("event_date"), candidate.get("city"), candidate.get("event_slug"))
        decision_ts = now_utc()
        guard = check_candidate(cfg=guard_cfg, repo_root=ROOT, candidate=candidate, decision_ts_utc=decision_ts)
        guard_audit.append(
            {
                "basket_id": bid,
                "city": candidate.get("city"),
                "event_date": candidate.get("event_date"),
                "guard": decision_to_dict(guard),
            }
        )
        if not guard.allow:
            rejected.append(
                {
                    "basket_id": bid,
                    "city": candidate.get("city"),
                    "event_date": candidate.get("event_date"),
                    "guard": decision_to_dict(guard),
                }
            )
            continue
        if bid in existing or opportunity_key in existing_opportunities:
            continue

        basket = {
            "recorded_at_utc": now_utc(),
            "strategy_id": STRATEGY_ID,
            "execution_mode": "paper_all_leg_or_none",
            "no_order_placed": True,
            "basket_id": bid,
            "opportunity_key": opportunity_key,
            "source_scan": scan.get("snapshot_path"),
            "source_report_generated_at_utc": scan.get("generated_at_utc"),
            "snapshot_ts_utc": scan.get("snapshot_summary", {}).get("snapshot_ts_utc_max"),
            "orderbook_fetched_at_utc_min": candidate.get("orderbook_fetched_at_utc_min"),
            "orderbook_fetched_at_utc_max": candidate.get("orderbook_fetched_at_utc_max"),
            "event_date": candidate.get("event_date"),
            "city": candidate.get("city"),
            "event_slug": candidate.get("event_slug"),
            "legs": len(guard.leg_orders),
            "shares_per_leg": guard_cfg.shares_per_leg,
            "total_yes_ask_cost": candidate.get("total_yes_ask_cost"),
            "underround": candidate.get("underround"),
            "basket_cost_usd": guard.basket_cost_usd,
            "gross_profit_if_complete_usd": guard.expected_profit_usd,
            "status": "paper_filled_all_legs",
            "live_blocker": "paper_only_no_partial_fill_engine",
            "guard": decision_to_dict(guard),
        }
        baskets_to_append.append(basket)
        existing_opportunities.add(opportunity_key)
        for leg in guard.leg_orders:
            price = float(leg["price"])
            condition_id = str(leg["condition_id"])
            legs_to_append.append(
                {
                    "recorded_at_utc": basket["recorded_at_utc"],
                    "strategy_id": STRATEGY_ID,
                    "basket_id": bid,
                    "opportunity_key": opportunity_key,
                    "leg_order_id": leg_order_id(bid, condition_id),
                    "event_date": candidate.get("event_date"),
                    "city": candidate.get("city"),
                    "event_slug": candidate.get("event_slug"),
                    "condition_id": condition_id,
                    "bracket": leg.get("bracket"),
                    "side": "BUY_YES",
                    "price": price,
                    "shares": leg.get("shares"),
                    "notional_usd": leg.get("notional_usd"),
                    "available_ask_size": leg.get("available_ask_size"),
                    "status": "paper_filled",
                    "no_order_placed": True,
                }
            )

    append_jsonl(basket_path, baskets_to_append)
    append_jsonl(leg_path, legs_to_append)
    result = {
        "command": "cycle",
        "generated_at_utc": now_utc(),
        "run_dir": str(run_dir),
        "source_scan": str(Path(args.scan_json)),
        "max_snapshot_age_seconds": args.max_snapshot_age_seconds,
        "scanner_candidate_count": len(list(scan.get("paper_shadow_candidates") or [])),
        "appended_baskets": len(baskets_to_append),
        "appended_leg_orders": len(legs_to_append),
        "rejected": rejected,
        "guard_audit": guard_audit,
        "total_baskets": len(read_jsonl(basket_path)),
    }
    (run_dir / "last_cycle.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def connect_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    return conn


def final_yes(price: Any) -> float | None:
    if price is None:
        return None
    value = float(price)
    if value >= 0.999:
        return 1.0
    if value <= 0.001:
        return 0.0
    return None


def load_settlements(conn: sqlite3.Connection, condition_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not condition_ids:
        return {}
    placeholders = ",".join("?" for _ in condition_ids)
    rows = conn.execute(
        f"SELECT condition_id, bracket, final_price, settlement_status FROM settlements WHERE condition_id IN ({placeholders})",
        condition_ids,
    ).fetchall()
    return {str(row["condition_id"]): dict(row) for row in rows}


def sorted_unique_values(rows: list[dict[str, Any]], field: str) -> list[str]:
    return sorted({str(row.get(field)) for row in rows if row.get(field)})


def basket_shape_audit(leg_rows: list[dict[str, Any]]) -> dict[str, Any]:
    blockers: list[str] = []
    brackets = [str(row.get("bracket")).strip() for row in leg_rows if row.get("bracket") is not None]
    condition_ids = [str(row.get("condition_id")).strip() for row in leg_rows if row.get("condition_id")]
    if len(set(condition_ids)) != len(condition_ids):
        blockers.append("duplicate_condition_id")
    if len(set(brackets)) != len(brackets):
        blockers.append("duplicate_bracket")
    if not brackets:
        blockers.append("missing_brackets")
    return {
        "basket_shape_valid": not blockers,
        "basket_shape_blockers": blockers,
        "unique_brackets": len(set(brackets)),
        "leg_brackets": brackets,
    }


def compact_opportunity_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("unique_opportunity_first"):
            continue
        compact.append(
            {
                "recorded_at_utc": row.get("recorded_at_utc"),
                "event_date": row.get("event_date"),
                "city": row.get("city"),
                "event_slug": row.get("event_slug"),
                "ttl_equivalent": row.get("ttl_equivalent"),
                "ttl_status": row.get("ttl_status"),
                "basket_shape_valid": row.get("basket_shape_valid"),
                "basket_shape_blockers": row.get("basket_shape_blockers"),
                "recording_age_seconds": row.get("recording_age_seconds"),
                "settlement_eval_status": row.get("settlement_eval_status"),
                "settled_legs": row.get("settled_legs"),
                "missing_settlement_legs": row.get("missing_settlement_legs"),
                "unresolved_settlement_legs": row.get("unresolved_settlement_legs"),
                "pending_reason": row.get("pending_reason"),
                "winner_count": row.get("winner_count"),
                "winner_brackets": row.get("winner_brackets"),
                "total_yes_ask_cost": row.get("total_yes_ask_cost"),
                "underround": row.get("underround"),
                "basket_cost_usd": row.get("basket_cost_usd"),
                "gross_profit_if_complete_usd": row.get("gross_profit_if_complete_usd"),
                "pnl_usd": row.get("pnl_usd"),
                "roi": row.get("roi"),
            }
        )
    return sorted(compact, key=lambda row: (str(row.get("event_date")), str(row.get("city")), str(row.get("recorded_at_utc"))))


def pending_reason(row: dict[str, Any]) -> str | None:
    if row.get("settlement_eval_status") != "pending":
        return None
    if int(row.get("missing_settlement_legs") or 0) > 0:
        return "missing_settlement_rows"
    if int(row.get("unresolved_settlement_legs") or 0) > 0:
        return "settlement_rows_unresolved"
    if int(row.get("legs") or 0) <= 0:
        return "missing_paper_legs"
    return "pending_unknown"


def count_values(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(field)
        if value is None:
            continue
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def settlement_pending_audit(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    audit: list[dict[str, Any]] = []
    for row in rows:
        audit.append(
            {
                "recorded_at_utc": row.get("recorded_at_utc"),
                "event_date": row.get("event_date"),
                "city": row.get("city"),
                "event_slug": row.get("event_slug"),
                "ttl_equivalent": row.get("ttl_equivalent"),
                "basket_shape_valid": row.get("basket_shape_valid"),
                "pending_reason": row.get("pending_reason"),
                "legs": row.get("legs"),
                "settled_legs": row.get("settled_legs"),
                "missing_settlement_legs": row.get("missing_settlement_legs"),
                "unresolved_settlement_legs": row.get("unresolved_settlement_legs"),
            }
        )
    return sorted(audit, key=lambda row: (str(row.get("event_date")), str(row.get("city")), str(row.get("recorded_at_utc"))))


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir)
    baskets = read_jsonl(run_dir / "paper_baskets.jsonl")
    legs = read_jsonl(run_dir / "paper_leg_orders.jsonl")
    legs_by_basket: dict[str, list[dict[str, Any]]] = {}
    condition_ids: list[str] = []
    for leg in legs:
        legs_by_basket.setdefault(str(leg.get("basket_id")), []).append(leg)
        if leg.get("condition_id"):
            condition_ids.append(str(leg["condition_id"]))
    conn = connect_db(Path(args.db_path))
    settlements = load_settlements(conn, sorted(set(condition_ids)))

    rows: list[dict[str, Any]] = []
    seen_opportunities: set[str] = set()
    for basket in baskets:
        bid = str(basket.get("basket_id"))
        opportunity_key = str(basket.get("opportunity_key") or opportunity_key_from_basket(basket))
        duplicate_opportunity = opportunity_key in seen_opportunities
        if not duplicate_opportunity:
            seen_opportunities.add(opportunity_key)
        ttl = recording_ttl_audit(basket, args.max_snapshot_age_seconds)
        leg_rows = legs_by_basket.get(bid, [])
        missing = [leg for leg in leg_rows if str(leg.get("condition_id")) not in settlements]
        settled_legs = []
        unresolved = []
        winners = []
        for leg in leg_rows:
            settlement = settlements.get(str(leg.get("condition_id")))
            if settlement is None:
                continue
            yes = final_yes(settlement.get("final_price"))
            if settlement.get("settlement_status") != "settled" or yes is None:
                unresolved.append(leg)
                continue
            settled_legs.append(leg)
            if yes == 1.0:
                winners.append(leg)
        all_settled = len(leg_rows) > 0 and not missing and not unresolved and len(settled_legs) == len(leg_rows)
        shape = basket_shape_audit(leg_rows)
        status = "pending"
        payout = None
        pnl = None
        roi = None
        winner_count = None
        if all_settled:
            winner_count = len(winners)
            payout = float(basket.get("shares_per_leg") or 0.0) * winner_count
            pnl = payout - float(basket.get("basket_cost_usd") or 0.0)
            roi = pnl / float(basket.get("basket_cost_usd") or 1.0)
            status = "settled_exactly_one_winner" if winner_count == 1 else "settled_winner_count_anomaly"
        rows.append(
            {
                **basket,
                **ttl,
                **shape,
                "opportunity_key": opportunity_key,
                "unique_opportunity_first": not duplicate_opportunity,
                "duplicate_opportunity": duplicate_opportunity,
                "settlement_eval_status": status,
                "settled_legs": len(settled_legs),
                "missing_settlement_legs": len(missing),
                "unresolved_settlement_legs": len(unresolved),
                "winner_count": winner_count,
                "payout_usd": round(payout, 6) if payout is not None else None,
                "pnl_usd": round(pnl, 6) if pnl is not None else None,
                "roi": round(roi, 6) if roi is not None else None,
                "winner_brackets": [leg.get("bracket") for leg in winners],
            }
        )
        rows[-1]["pending_reason"] = pending_reason(rows[-1])
    decision_rows = [row for row in rows if row.get("unique_opportunity_first")]
    shape_invalid = [row for row in decision_rows if not row.get("basket_shape_valid")]
    live_prep_rows = [row for row in decision_rows if row.get("basket_shape_valid")]
    settled = [row for row in decision_rows if row["settlement_eval_status"].startswith("settled_")]
    exact = [row for row in settled if row["settlement_eval_status"] == "settled_exactly_one_winner"]
    live_prep_settled = [row for row in live_prep_rows if row["settlement_eval_status"].startswith("settled_")]
    live_prep_exact = [row for row in live_prep_settled if row["settlement_eval_status"] == "settled_exactly_one_winner"]
    ttl_equivalent = [row for row in live_prep_rows if row.get("ttl_equivalent") is True]
    ttl_equivalent_exact = [row for row in live_prep_exact if row.get("ttl_equivalent") is True]
    ttl_equivalent_pending = [
        row for row in live_prep_rows
        if row.get("ttl_equivalent") is True and row["settlement_eval_status"] == "pending"
    ]
    pending_rows = [row for row in live_prep_rows if row["settlement_eval_status"] == "pending"]
    stale_recorded = [row for row in live_prep_rows if row.get("ttl_status") == "stale_recording"]
    bad_ttl = [row for row in live_prep_rows if row.get("ttl_equivalent") is False]
    cost = sum(float(row.get("basket_cost_usd") or 0.0) for row in exact)
    pnl = sum(float(row.get("pnl_usd") or 0.0) for row in exact)
    ttl_cost = sum(float(row.get("basket_cost_usd") or 0.0) for row in ttl_equivalent_exact)
    ttl_pnl = sum(float(row.get("pnl_usd") or 0.0) for row in ttl_equivalent_exact)
    ages = [float(row["recording_age_seconds"]) for row in rows if row.get("recording_age_seconds") is not None]
    summary = {
        "command": "eval",
        "generated_at_utc": now_utc(),
        "run_dir": str(run_dir),
        "db_path": str(Path(args.db_path)),
        "max_snapshot_age_seconds": args.max_snapshot_age_seconds,
        "raw_baskets": len(rows),
        "duplicate_opportunity_baskets": len([row for row in rows if row.get("duplicate_opportunity")]),
        "baskets": len(decision_rows),
        "unique_opportunity_baskets": len(decision_rows),
        "shape_valid_baskets": len(live_prep_rows),
        "shape_invalid_baskets": len(shape_invalid),
        "shape_invalid_opportunities": [
            {
                "event_date": row.get("event_date"),
                "city": row.get("city"),
                "event_slug": row.get("event_slug"),
                "basket_shape_blockers": row.get("basket_shape_blockers"),
                "unique_brackets": row.get("unique_brackets"),
                "legs": row.get("legs"),
            }
            for row in shape_invalid
        ],
        "settled": len(settled),
        "settled_exactly_one_winner": len(exact),
        "pending": len([row for row in decision_rows if row["settlement_eval_status"] == "pending"]),
        "live_prep_pending": len(pending_rows),
        "pending_reason_counts": count_values(pending_rows, "pending_reason"),
        "ttl_equivalent_pending_reason_counts": count_values(ttl_equivalent_pending, "pending_reason"),
        "ttl_equivalent_pending_settlement_audit": settlement_pending_audit(ttl_equivalent_pending),
        "pending_missing_settlement_legs": sum(int(row.get("missing_settlement_legs") or 0) for row in pending_rows),
        "pending_unresolved_settlement_legs": sum(int(row.get("unresolved_settlement_legs") or 0) for row in pending_rows),
        "ttl_equivalent_pending_missing_settlement_legs": sum(int(row.get("missing_settlement_legs") or 0) for row in ttl_equivalent_pending),
        "ttl_equivalent_pending_unresolved_settlement_legs": sum(int(row.get("unresolved_settlement_legs") or 0) for row in ttl_equivalent_pending),
        "winner_count_anomaly": len([row for row in decision_rows if row["settlement_eval_status"] == "settled_winner_count_anomaly"]),
        "ttl_equivalent_baskets": len(ttl_equivalent),
        "ttl_non_equivalent_baskets": len(bad_ttl),
        "stale_recorded_baskets": len(stale_recorded),
        "settled_active_event_dates": len(sorted_unique_values(exact, "event_date")),
        "settled_event_dates": sorted_unique_values(exact, "event_date"),
        "ttl_equivalent_pending": len(ttl_equivalent_pending),
        "ttl_equivalent_pending_active_event_dates": len(sorted_unique_values(ttl_equivalent_pending, "event_date")),
        "ttl_equivalent_pending_event_dates": sorted_unique_values(ttl_equivalent_pending, "event_date"),
        "ttl_equivalent_settled_exactly_one_winner": len(ttl_equivalent_exact),
        "ttl_equivalent_settled_active_event_dates": len(sorted_unique_values(ttl_equivalent_exact, "event_date")),
        "ttl_equivalent_settled_event_dates": sorted_unique_values(ttl_equivalent_exact, "event_date"),
        "ttl_recording_age_seconds_max": round(max(ages), 3) if ages else None,
        "settled_cost_usd": round(cost, 6),
        "settled_pnl_usd": round(pnl, 6),
        "settled_roi": round(pnl / cost, 6) if cost else None,
        "positive_basket_rate": round(sum(1 for row in exact if float(row.get("pnl_usd") or 0.0) > 0) / len(exact), 6) if exact else None,
        "ttl_equivalent_settled_cost_usd": round(ttl_cost, 6),
        "ttl_equivalent_settled_pnl_usd": round(ttl_pnl, 6),
        "ttl_equivalent_settled_roi": round(ttl_pnl / ttl_cost, 6) if ttl_cost else None,
        "ttl_equivalent_positive_basket_rate": (
            round(sum(1 for row in ttl_equivalent_exact if float(row.get("pnl_usd") or 0.0) > 0) / len(ttl_equivalent_exact), 6)
            if ttl_equivalent_exact
            else None
        ),
        "rows": rows,
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "eval.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def gate(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir)
    eval_summary = evaluate(args)
    clob_gate = read_json(Path(args.gate_path))
    last_cycle = read_json(run_dir / "last_cycle.json")
    blockers: list[dict[str, Any]] = []
    passed: list[dict[str, Any]] = []
    if not clob_gate.get("gate_pass"):
        blockers.append({"code": "clob_coverage_gate_fail", "message": "CLOB coverage gate must pass before any live readiness claim."})
    else:
        passed.append({"code": "clob_coverage_gate_pass", "message": "CLOB fill coverage gate passes."})
    if eval_summary["baskets"] <= 0:
        blockers.append({"code": "paper_ledger_empty", "message": "No all-YES paper baskets have been recorded."})
    else:
        passed.append({"code": "paper_ledger_exists", "message": "Paper basket ledger has recorded all-leg baskets.", "baskets": eval_summary["baskets"]})
    if eval_summary["ttl_equivalent_baskets"] <= 0:
        blockers.append(
            {
                "code": "live_equivalent_paper_ledger_empty",
                "message": "No paper baskets were recorded within the configured snapshot TTL, so forward evidence is observation-only.",
                "max_snapshot_age_seconds": args.max_snapshot_age_seconds,
            }
        )
    else:
        passed.append(
            {
                "code": "live_equivalent_paper_ledger_exists",
                "message": "Some paper baskets were recorded within the configured snapshot TTL.",
                "ttl_equivalent_baskets": eval_summary["ttl_equivalent_baskets"],
            }
        )
    if eval_summary["ttl_non_equivalent_baskets"]:
        blockers.append(
            {
                "code": "paper_baskets_not_live_equivalent",
                "message": "Some paper baskets were recorded after the snapshot TTL and cannot count toward live-prep forward evidence.",
                "ttl_non_equivalent_baskets": eval_summary["ttl_non_equivalent_baskets"],
                "stale_recorded_baskets": eval_summary["stale_recorded_baskets"],
                "max_recording_age_seconds": eval_summary["ttl_recording_age_seconds_max"],
                "max_snapshot_age_seconds": args.max_snapshot_age_seconds,
            }
        )
    if eval_summary["shape_invalid_baskets"]:
        blockers.append(
            {
                "code": "basket_shape_invalid",
                "message": "Some unique paper opportunities have invalid bracket shape and are excluded from live-prep counts.",
                "shape_invalid_baskets": eval_summary["shape_invalid_baskets"],
                "shape_invalid_opportunities": eval_summary["shape_invalid_opportunities"],
            }
        )
    guard_audit = list(last_cycle.get("guard_audit") or [])
    guard_failures = [row for row in guard_audit if not row.get("guard", {}).get("allow")]
    scanner_candidate_count = last_cycle.get("scanner_candidate_count")
    if scanner_candidate_count == 0:
        blockers.append({"code": "no_current_scanner_candidates", "message": "Latest scanner run found no current all-YES underround candidates."})
    elif not guard_audit:
        blockers.append({"code": "current_guard_audit_missing", "message": "Latest cycle did not audit current scanner candidates through the all-leg guard."})
    elif guard_failures:
        blockers.append({"code": "current_guard_audit_fail", "message": "Some current scanner candidates failed the all-leg guard.", "failures": guard_failures})
    else:
        passed.append({"code": "current_guard_audit_pass", "message": "Current scanner candidates pass the shared all-leg guard.", "candidates": len(guard_audit)})
    if eval_summary["ttl_equivalent_settled_exactly_one_winner"] < args.min_settled_baskets:
        blockers.append(
            {
                "code": "forward_settled_baskets_low",
                "message": "Need more settled live-equivalent forward paper baskets before deploy review.",
                "ttl_equivalent_settled_exactly_one_winner": eval_summary["ttl_equivalent_settled_exactly_one_winner"],
                "required": args.min_settled_baskets,
            }
        )
    else:
        passed.append({"code": "forward_settled_baskets_ready", "message": "Forward paper basket sample is large enough."})
    if eval_summary["ttl_equivalent_settled_active_event_dates"] < args.min_settled_active_dates:
        blockers.append(
            {
                "code": "forward_settled_active_dates_low",
                "message": "Need more active settled event dates before deploy review; same-day baskets are correlated.",
                "ttl_equivalent_settled_active_event_dates": eval_summary["ttl_equivalent_settled_active_event_dates"],
                "ttl_equivalent_settled_event_dates": eval_summary["ttl_equivalent_settled_event_dates"],
                "required": args.min_settled_active_dates,
            }
        )
    else:
        passed.append(
            {
                "code": "forward_settled_active_dates_ready",
                "message": "Forward paper sample spans enough settled event dates.",
                "ttl_equivalent_settled_active_event_dates": eval_summary["ttl_equivalent_settled_active_event_dates"],
            }
        )
    if eval_summary["ttl_equivalent_pending"]:
        blockers.append(
            {
                "code": "forward_settlements_pending",
                "message": "Some live-equivalent paper baskets are still pending settlement evaluation.",
                "ttl_equivalent_pending": eval_summary["ttl_equivalent_pending"],
                "ttl_equivalent_pending_event_dates": eval_summary["ttl_equivalent_pending_event_dates"],
                "ttl_equivalent_pending_reason_counts": eval_summary["ttl_equivalent_pending_reason_counts"],
                "ttl_equivalent_pending_missing_settlement_legs": eval_summary["ttl_equivalent_pending_missing_settlement_legs"],
                "ttl_equivalent_pending_unresolved_settlement_legs": eval_summary["ttl_equivalent_pending_unresolved_settlement_legs"],
            }
        )
    if eval_summary["winner_count_anomaly"]:
        blockers.append({"code": "settlement_winner_count_anomaly", "message": "Some settled baskets did not have exactly one winning leg."})
    if eval_summary["ttl_equivalent_settled_roi"] is None or eval_summary["ttl_equivalent_settled_roi"] < args.min_roi:
        blockers.append(
            {
                "code": "forward_roi_not_ready",
                "message": "Live-equivalent forward paper ROI has not passed the live-prep threshold.",
                "roi": eval_summary["ttl_equivalent_settled_roi"],
                "required": args.min_roi,
            }
        )
    else:
        passed.append({"code": "forward_roi_ready", "message": "Live-equivalent forward paper ROI passes threshold.", "roi": eval_summary["ttl_equivalent_settled_roi"]})
    if eval_summary["ttl_equivalent_positive_basket_rate"] is None or eval_summary["ttl_equivalent_positive_basket_rate"] < args.min_positive_basket_rate:
        blockers.append(
            {
                "code": "positive_basket_rate_not_ready",
                "message": "Live-equivalent forward positive basket rate has not passed the live-prep threshold.",
                "positive_basket_rate": eval_summary["ttl_equivalent_positive_basket_rate"],
                "required": args.min_positive_basket_rate,
            }
        )
    else:
        passed.append({"code": "positive_basket_rate_ready", "message": "Live-equivalent positive basket rate passes threshold.", "positive_basket_rate": eval_summary["ttl_equivalent_positive_basket_rate"]})
    blockers.append(
        {
            "code": "live_executor_missing",
            "message": "This script is paper-only; live requires weather-strategy-deploy plus signed all-leg-or-none execution and partial-fill handling.",
        }
    )
    verdict = "READY_FOR_DEPLOY_REVIEW" if not blockers else "NOT_READY_ACCUMULATE_PAPER_SHADOW"
    result = {
        "command": "gate",
        "generated_at_utc": now_utc(),
        "strategy_id": STRATEGY_ID,
        "verdict": verdict,
        "live_now": False,
        "blockers": blockers,
        "passed": passed,
        "eval": {k: v for k, v in eval_summary.items() if k != "rows"},
        "clob_coverage_gate": {
            "gate_pass": clob_gate.get("gate_pass"),
            "fail_reasons": clob_gate.get("fail_reasons"),
            "db_fill_cost_minus_fact_cost": clob_gate.get("db_fill_cost_minus_fact_cost"),
            "order_caps": clob_gate.get("order_caps"),
        },
        "next_actions": [
            "Keep running scanner + paper cycle on fresh snapshots.",
            "Wait for at least 20 settled exactly-one-winner live-equivalent paper baskets across at least 7 active event dates with positive ROI.",
            "Design signed all-leg-or-none execution and partial-fill cancellation/unwind before any live deployment.",
            "Use weather-strategy-deploy for any N100/live change.",
        ],
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "live_prep_gate.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def monitor(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir)
    gate_result = gate(args)
    last_cycle = read_json(run_dir / "last_cycle.json")
    fresh_cycle = read_json(run_dir / "fresh_cycle.json")
    baskets = read_jsonl(run_dir / "paper_baskets.jsonl")
    eval_rows = read_json(run_dir / "eval.json").get("rows") or []
    unique_opportunities = compact_opportunity_rows(eval_rows)
    pending_rows = [
        row for row in eval_rows
        if row.get("unique_opportunity_first") and row.get("settlement_eval_status") == "pending"
    ]
    city_counts: dict[str, int] = {}
    pending_event_dates: dict[str, int] = {}
    for row in pending_rows:
        city = str(row.get("city"))
        event_date = str(row.get("event_date"))
        city_counts[city] = city_counts.get(city, 0) + 1
        pending_event_dates[event_date] = pending_event_dates.get(event_date, 0) + 1
    result = {
        "generated_at_utc": now_utc(),
        "strategy_id": STRATEGY_ID,
        "verdict": gate_result.get("verdict"),
        "live_now": False,
        "latest_scan_path": last_cycle.get("source_scan"),
        "latest_fresh_cycle": {
            "generated_at_utc": fresh_cycle.get("generated_at_utc"),
            "verdict": fresh_cycle.get("verdict"),
            "reason": fresh_cycle.get("reason"),
            "executed_cycle": fresh_cycle.get("executed_cycle"),
            "snapshot_age_seconds": fresh_cycle.get("snapshot_age_seconds"),
            "snapshot_path": fresh_cycle.get("snapshot_path"),
        },
        "latest_scanner_candidate_count": last_cycle.get("scanner_candidate_count"),
        "latest_appended_baskets": last_cycle.get("appended_baskets"),
        "max_snapshot_age_seconds": last_cycle.get("max_snapshot_age_seconds"),
        "paper_baskets": len(baskets),
        "paper_unique_opportunity_baskets": gate_result.get("eval", {}).get("unique_opportunity_baskets"),
        "paper_duplicate_opportunity_baskets": gate_result.get("eval", {}).get("duplicate_opportunity_baskets"),
        "ttl_equivalent_baskets": gate_result.get("eval", {}).get("ttl_equivalent_baskets"),
        "ttl_non_equivalent_baskets": gate_result.get("eval", {}).get("ttl_non_equivalent_baskets"),
        "stale_recorded_baskets": gate_result.get("eval", {}).get("stale_recorded_baskets"),
        "ttl_recording_age_seconds_max": gate_result.get("eval", {}).get("ttl_recording_age_seconds_max"),
        "ttl_equivalent_settled_exactly_one_winner": gate_result.get("eval", {}).get("ttl_equivalent_settled_exactly_one_winner"),
        "ttl_equivalent_settled_active_event_dates": gate_result.get("eval", {}).get("ttl_equivalent_settled_active_event_dates"),
        "ttl_equivalent_settled_event_dates": gate_result.get("eval", {}).get("ttl_equivalent_settled_event_dates"),
        "ttl_equivalent_pending_active_event_dates": gate_result.get("eval", {}).get("ttl_equivalent_pending_active_event_dates"),
        "ttl_equivalent_pending_event_dates": gate_result.get("eval", {}).get("ttl_equivalent_pending_event_dates"),
        "ttl_equivalent_pending_reason_counts": gate_result.get("eval", {}).get("ttl_equivalent_pending_reason_counts"),
        "ttl_equivalent_pending_settlement_audit": gate_result.get("eval", {}).get("ttl_equivalent_pending_settlement_audit"),
        "ttl_equivalent_pending_missing_settlement_legs": gate_result.get("eval", {}).get("ttl_equivalent_pending_missing_settlement_legs"),
        "ttl_equivalent_pending_unresolved_settlement_legs": gate_result.get("eval", {}).get("ttl_equivalent_pending_unresolved_settlement_legs"),
        "paper_eval": gate_result.get("eval"),
        "unique_opportunities": unique_opportunities,
        "pending_by_city": dict(sorted(city_counts.items())),
        "pending_by_event_date": dict(sorted(pending_event_dates.items())),
        "passed_codes": [item.get("code") for item in gate_result.get("passed", [])],
        "blocker_codes": [item.get("code") for item in gate_result.get("blockers", [])],
        "next_actions": gate_result.get("next_actions"),
    }
    monitor_path = run_dir / "monitor.json"
    history_path = run_dir / "monitor_history.jsonl"
    monitor_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    append_jsonl(history_path, [result])
    return result


def main() -> None:
    args = parse_args()
    if args.command == "cycle":
        result = cycle(args)
    elif args.command == "eval":
        result = evaluate(args)
    elif args.command == "gate":
        result = gate(args)
    else:
        result = monitor(args)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
