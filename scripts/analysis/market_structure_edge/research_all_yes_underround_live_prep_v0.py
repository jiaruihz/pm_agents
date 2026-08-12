#!/usr/bin/env python3
"""Current all-YES underround live-prep scanner v0.

This script does not place orders. It reads the latest mirrored orderbook
snapshot, groups weather temperature brackets by event, and checks whether an
equal-share BUY_YES basket is currently below $1 total ask cost.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
DB_DEFAULT = ROOT / "runtime" / "weather.db"
SNAPSHOT_ROOT_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / "market_data" / "orderbook_snapshots"
GATE_DEFAULT = ROOT / "runtime" / "_dashboard_logs" / "clob_fill_coverage_gate.json"
STATION_BASIS_GATE_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / "station_basis_shadow_v1" / "live_prep_gate.json"
PAPER_GATE_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / "all_yes_underround_paper_v0" / "live_prep_gate.json"
PAPER_MONITOR_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / "all_yes_underround_paper_v0" / "monitor.json"
FRESH_CYCLE_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / "all_yes_underround_paper_v0" / "fresh_cycle.json"
OUT_JSON_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / "all_yes_underround_paper_v0" / "latest_scan.json"
OUT_MD_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / "all_yes_underround_paper_v0" / "latest_scan.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DB_DEFAULT))
    parser.add_argument("--snapshot-root", default=str(SNAPSHOT_ROOT_DEFAULT))
    parser.add_argument("--snapshot-path")
    parser.add_argument("--gate-path", default=str(GATE_DEFAULT))
    parser.add_argument("--station-basis-gate-path", default=str(STATION_BASIS_GATE_DEFAULT))
    parser.add_argument("--paper-gate-path", default=str(PAPER_GATE_DEFAULT))
    parser.add_argument("--paper-monitor-path", default=str(PAPER_MONITOR_DEFAULT))
    parser.add_argument("--fresh-cycle-path", default=str(FRESH_CYCLE_DEFAULT))
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    parser.add_argument("--min-underround", type=float, default=0.02)
    parser.add_argument("--min-leg-count", type=int, default=5)
    parser.add_argument("--min-shares", type=float, default=5.0)
    parser.add_argument("--max-spread", type=float, default=0.05)
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--telemetry-underround-thresholds", default="0.005,0.01,0.02")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    return json.loads(path.read_text(encoding="utf-8"))


def latest_snapshot(root: Path) -> Path:
    files = sorted(root.glob("**/orderbook_snapshot_*.jsonl.gz"))
    if not files:
        raise FileNotFoundError(f"no orderbook snapshots found under {root}")
    return files[-1]


def connect_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    return conn


def data_self_check(conn: sqlite3.Connection) -> dict[str, Any]:
    trade_classes = [dict(row) for row in conn.execute("SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class")]
    settlements = [dict(row) for row in conn.execute("SELECT settlement_status, COUNT(*) AS rows FROM fact_trades GROUP BY settlement_status")]
    coverage = dict(
        conn.execute(
            "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
            "SUM(live_filled) AS live_filled FROM fact_signal_candidates"
        ).fetchone()
    )
    order_rows = [
        dict(row)
        for row in conn.execute(
            "SELECT o.status, COUNT(*) AS orders, "
            "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
            "FROM orders o LEFT JOIN fills f USING(execution_id) "
            "WHERE o.venue='polymarket_clob' GROUP BY o.status"
        )
    ]
    return {
        "fact_built_at_utc": conn.execute("SELECT MAX(fact_built_at_utc) FROM fact_trades").fetchone()[0],
        "fact_trades_by_class": trade_classes,
        "fact_trades_by_settlement": settlements,
        "fact_signal_candidates_coverage": coverage,
        "clob_orders_with_fills": order_rows,
    }


def read_snapshot(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def yes_summary(row: dict[str, Any]) -> dict[str, Any]:
    summary = row.get("summary") or {}
    return {
        "best_ask": summary.get("best_ask"),
        "best_bid": summary.get("best_bid"),
        "ask_size": summary.get("ask_size"),
        "bid_size": summary.get("bid_size"),
    }


def bracket_sort_key(value: Any) -> tuple[int, str]:
    text = str(value)
    try:
        return (0, f"{float(text):08.3f}")
    except ValueError:
        return (1, text)


def group_baskets(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("outcome") == "yes":
            key = (str(row.get("event_date")), str(row.get("city")), str(row.get("event_slug")))
            groups[key].append(row)

    baskets: list[dict[str, Any]] = []
    for (event_date, city, event_slug), legs in groups.items():
        legs = sorted(legs, key=lambda row: bracket_sort_key(row.get("bracket")))
        snapshot_ts_values = sorted({row.get("snapshot_ts_utc") for row in legs if row.get("snapshot_ts_utc")})
        fetched_at_values = sorted({row.get("fetched_at_utc") for row in legs if row.get("fetched_at_utc")})
        statuses = Counter(str(row.get("status")) for row in legs)
        missing_asks = 0
        total_cost = 0.0
        min_ask_size: float | None = None
        max_spread: float | None = None
        leg_rows = []
        condition_ids: list[str] = []
        brackets: list[str] = []
        for row in legs:
            summary = yes_summary(row)
            ask = summary["best_ask"]
            bid = summary["best_bid"]
            ask_size = summary["ask_size"]
            if row.get("condition_id"):
                condition_ids.append(str(row.get("condition_id")).strip())
            if row.get("bracket") is not None:
                brackets.append(str(row.get("bracket")).strip())
            if ask is None:
                missing_asks += 1
                continue
            ask_f = float(ask)
            total_cost += ask_f
            if ask_size is not None:
                size_f = float(ask_size)
                min_ask_size = size_f if min_ask_size is None else min(min_ask_size, size_f)
            if bid is not None:
                spread = ask_f - float(bid)
                max_spread = spread if max_spread is None else max(max_spread, spread)
            leg_rows.append(
                {
                    "bracket": row.get("bracket"),
                    "condition_id": row.get("condition_id"),
                    "best_ask": ask,
                    "best_bid": bid,
                    "ask_size": ask_size,
                }
            )

        underround = 1.0 - total_cost if missing_asks == 0 else None
        capacity_shares = min_ask_size or 0.0
        min_share_cost = total_cost * args.min_shares
        min_share_payout = args.min_shares
        min_share_profit = (underround or 0.0) * args.min_shares
        top_cost = total_cost * capacity_shares
        top_profit = (underround or 0.0) * capacity_shares
        blockers = []
        if len(legs) < args.min_leg_count:
            blockers.append("leg_count_below_live_floor")
        if len(set(condition_ids)) != len(condition_ids):
            blockers.append("duplicate_condition_id")
        if len(set(brackets)) != len(brackets):
            blockers.append("duplicate_bracket")
        if missing_asks:
            blockers.append("missing_best_ask")
        if underround is None or underround < args.min_underround:
            blockers.append("underround_below_threshold")
        if capacity_shares < args.min_shares:
            blockers.append("top_of_book_capacity_below_min_shares")
        if max_spread is not None and max_spread > args.max_spread:
            blockers.append("spread_above_threshold")
        if any(status != "ok" for status in statuses):
            blockers.append("non_ok_snapshot_status")

        baskets.append(
            {
                "event_date": event_date,
                "city": city,
                "event_slug": event_slug,
                "snapshot_ts_utc": snapshot_ts_values[-1] if snapshot_ts_values else None,
                "orderbook_fetched_at_utc_min": fetched_at_values[0] if fetched_at_values else None,
                "orderbook_fetched_at_utc_max": fetched_at_values[-1] if fetched_at_values else None,
                "legs": len(legs),
                "ask_legs": len(leg_rows),
                "missing_asks": missing_asks,
                "total_yes_ask_cost": round(total_cost, 6),
                "underround": round(underround, 6) if underround is not None else None,
                "min_ask_size": round(capacity_shares, 4),
                "max_yes_spread": round(max_spread, 6) if max_spread is not None else None,
                "statuses": dict(statuses),
                "min_share_basket": {
                    "shares_per_leg": args.min_shares,
                    "cost_usd": round(min_share_cost, 4),
                    "payout_if_any_leg_wins_usd": round(min_share_payout, 4),
                    "gross_profit_usd": round(min_share_profit, 4),
                },
                "top_of_book_capacity": {
                    "shares_per_leg": round(capacity_shares, 4),
                    "cost_usd": round(top_cost, 4),
                    "gross_profit_usd": round(top_profit, 4),
                },
                "blockers": blockers,
                "paper_shadow_candidate": not blockers,
                "legs_detail": leg_rows,
            }
        )
    return sorted(
        baskets,
        key=lambda row: (
            row["underround"] if row["underround"] is not None else -999.0,
            row["min_ask_size"],
        ),
        reverse=True,
    )


def snapshot_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    snapshot_ts_values = sorted({row.get("snapshot_ts_utc") for row in rows if row.get("snapshot_ts_utc")})
    fetched_at_values = sorted({row.get("fetched_at_utc") for row in rows if row.get("fetched_at_utc")})
    event_dates = Counter(str(row.get("event_date")) for row in rows)
    yes_events = {
        (row.get("event_date"), row.get("city"), row.get("event_slug"))
        for row in rows
        if row.get("outcome") == "yes"
    }
    return {
        "rows": len(rows),
        "snapshot_ts_utc_min": snapshot_ts_values[0] if snapshot_ts_values else None,
        "snapshot_ts_utc_max": snapshot_ts_values[-1] if snapshot_ts_values else None,
        "orderbook_fetched_at_utc_min": fetched_at_values[0] if fetched_at_values else None,
        "orderbook_fetched_at_utc_max": fetched_at_values[-1] if fetched_at_values else None,
        "event_dates": dict(sorted(event_dates.items())),
        "yes_event_count": len(yes_events),
        "yes_rows": sum(1 for row in rows if row.get("outcome") == "yes"),
    }


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{value * 100:+.1f}%"


def parse_thresholds(value: str) -> list[float]:
    thresholds: list[float] = []
    for item in value.split(","):
        text = item.strip()
        if not text:
            continue
        thresholds.append(float(text))
    return sorted(set(thresholds))


def underround_threshold_telemetry(baskets: list[dict[str, Any]], thresholds: list[float]) -> list[dict[str, Any]]:
    telemetry: list[dict[str, Any]] = []
    for threshold in thresholds:
        eligible_rows = []
        for row in baskets:
            blockers = [item for item in row.get("blockers", []) if item != "underround_below_threshold"]
            underround = row.get("underround")
            if blockers or underround is None or float(underround) < threshold:
                continue
            eligible_rows.append(row)
        telemetry.append(
            {
                "threshold": threshold,
                "candidate_count": len(eligible_rows),
                "top_candidates": [
                    {
                        "event_date": row.get("event_date"),
                        "city": row.get("city"),
                        "underround": row.get("underround"),
                        "total_yes_ask_cost": row.get("total_yes_ask_cost"),
                        "min_ask_size": row.get("min_ask_size"),
                        "max_yes_spread": row.get("max_yes_spread"),
                    }
                    for row in eligible_rows[:5]
                ],
            }
        )
    return telemetry


def candidate_line(row: dict[str, Any]) -> str:
    blockers = ",".join(row["blockers"]) if row["blockers"] else ""
    return (
        f"| {row['event_date']} | `{row['city']}` | {row['legs']} | "
        f"{row['total_yes_ask_cost']:.3f} | {fmt_pct(row['underround'])} | "
        f"{row['min_ask_size']:.2f} | {row['max_yes_spread'] if row['max_yes_spread'] is not None else 'NA'} | "
        f"{row['min_share_basket']['cost_usd']:.3f} | {row['min_share_basket']['gross_profit_usd']:.3f} | "
        f"{row['paper_shadow_candidate']} | `{blockers}` |"
    )


def write_md(path: Path, report: dict[str, Any]) -> None:
    args = report["parameters"]
    station = report["station_basis_gate"]
    paper_gate = report["paper_execution_gate"]
    gate = report["clob_fill_coverage_gate"]
    candidates = report["paper_shadow_candidates"]
    top = report["top_baskets"]
    paper_passed = [item.get("code") for item in (paper_gate.get("passed") or [])]
    paper_monitor = report["paper_monitor"]
    current_state_note = (
        "latest snapshot has executable-looking baskets, led by "
        + ", ".join(f"{row['city']} {row['event_date']}" for row in candidates[:3])
        if candidates
        else "latest snapshot has no executable all-YES underround basket under the current gates"
    )
    paper_eval = paper_gate.get("eval") or {}
    fresh_cycle = report["fresh_cycle"]
    telemetry = report["underround_threshold_telemetry"]
    lines = [
        "# All-YES Underround Live-Prep v0",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> target_metric: `{report['target_metric']}`",
        f"> verdict: `{report['verdict']}`",
        "> Scope: scanner/live-prep only; no N100/live config changed and no orders placed.",
        "",
        "## Data Snapshot",
        "",
        f"- DB: `{report['db_path']}`",
        f"- latest orderbook snapshot: `{report['snapshot_path']}`",
        f"- snapshot rows: `{report['snapshot_summary']['rows']}`; YES rows `{report['snapshot_summary']['yes_rows']}`; YES events `{report['snapshot_summary']['yes_event_count']}`.",
        f"- snapshot ts UTC: `{report['snapshot_summary']['snapshot_ts_utc_min']}` to `{report['snapshot_summary']['snapshot_ts_utc_max']}`.",
        f"- event_date rows: `{report['snapshot_summary']['event_dates']}`.",
        f"- fact built at: `{report['data_self_check']['fact_built_at_utc']}`.",
        f"- CLOB fill coverage gate: `gate_pass={gate.get('gate_pass')}`; fail_reasons `{gate.get('fail_reasons')}`.",
        "",
        "### Mandatory 5-Line SQL Self-Check",
        "",
        "```text",
        f"MAX(fact_built_at_utc) = {report['data_self_check']['fact_built_at_utc']}",
        f"fact_trades by trade_class = {report['data_self_check']['fact_trades_by_class']}",
        f"fact_trades by settlement_status = {report['data_self_check']['fact_trades_by_settlement']}",
        f"fact_signal_candidates coverage = {report['data_self_check']['fact_signal_candidates_coverage']}",
        f"CLOB orders with fills = {report['data_self_check']['clob_orders_with_fills']}",
        "```",
        "",
        "## Current Scanner Gates",
        "",
        f"- Candidate threshold: underround >= `{args['min_underround']}`, legs >= `{args['min_leg_count']}`, min top-of-book shares >= `{args['min_shares']}`, max YES spread <= `{args['max_spread']}`.",
        f"- Current paper/shadow candidates: `{len(candidates)}`.",
        f"- Same-family threshold telemetry: `{telemetry}`.",
        f"- Station-basis comparison gate: `{station.get('verdict')}` with settled `{station.get('settled')}` / pending `{station.get('pending')}`.",
        f"- All-YES paper execution gate: `{paper_gate.get('verdict')}` with baskets `{paper_eval.get('baskets')}` / settled `{paper_eval.get('settled_exactly_one_winner')}`.",
        f"- Live-equivalent paper baskets: `{paper_eval.get('ttl_equivalent_baskets')}`; stale/observation-only baskets `{paper_eval.get('ttl_non_equivalent_baskets')}`; max record age `{paper_eval.get('ttl_recording_age_seconds_max')}` seconds.",
        f"- Live-equivalent settled exactly-one-winner baskets: `{paper_eval.get('ttl_equivalent_settled_exactly_one_winner')}`; ROI `{paper_eval.get('ttl_equivalent_settled_roi')}`.",
        f"- All-YES paper passed checks: `{paper_passed}`.",
        f"- All-YES monitor blockers: `{paper_monitor.get('blocker_codes')}`; pending by date `{paper_monitor.get('pending_by_event_date')}`.",
        f"- Stale basket guard: max snapshot age `{paper_monitor.get('max_snapshot_age_seconds')}` seconds before paper/live candidate recording.",
        f"- Fresh paper cycle: verdict `{fresh_cycle.get('verdict')}`; executed `{fresh_cycle.get('executed_cycle')}`; latest snapshot age `{fresh_cycle.get('snapshot_age_seconds')}` seconds; reason `{fresh_cycle.get('reason')}`.",
        "- Repeatable local command: `scripts/ops/run_all_yes_underround_paper_v0.sh`.",
        "- Low-latency forward-paper command: `scripts/ops/run_all_yes_underround_fresh_paper_v0.sh`; it only records baskets while the latest snapshot is inside the TTL.",
        "- Guard coverage: `scripts/ops/all_yes_underround_guards.py test` and `pytest tests/pmm_tests/test_all_yes_underround_guards.py` cover all-leg completeness, depth, spread, cost, duplicate legs, underround mismatch, and kill switch.",
        "",
        "## Passing Paper/Shadow Candidates",
        "",
        "| event_date | city | legs | YES ask cost | underround | min ask size | max spread | 5-share basket cost | 5-share gross profit | shadow candidate | blockers |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in candidates:
        lines.append(candidate_line(row))
    lines.extend(
        [
            "",
            "## Top Baskets And Rejections",
            "",
            "Rows with blockers are shown to make the funnel explicit; they are not executable candidates.",
            "",
            "| event_date | city | legs | YES ask cost | underround | min ask size | max spread | 5-share basket cost | 5-share gross profit | shadow candidate | blockers |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for row in top:
        lines.append(candidate_line(row))
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            "The live direction is stronger for `all_yes_underround_basket_v0` than for the current BUY_NO forecast-quality sleeve or station-basis v1, but it is not a direct live switch yet.",
            "",
            "- Offline evidence: confirmed in the 2026-06-09 robust all-YES underround run, including time-aligned executable thresholds.",
            f"- Current market state: {current_state_note}.",
            f"- Paper ledger state: `{paper_eval.get('baskets')}` baskets recorded, `{paper_eval.get('pending')}` pending, `{paper_eval.get('settled_exactly_one_winner')}` settled exactly-one-winner.",
            f"- Live-equivalent forward state: `{paper_eval.get('ttl_equivalent_baskets')}` baskets recorded within the `{paper_eval.get('max_snapshot_age_seconds')}` second TTL; stale paper observations remain useful for opportunity discovery but do not count toward live-prep forward evidence.",
            f"- Fresh-cycle state: `{fresh_cycle.get('verdict')}`; this path must run immediately after snapshot capture, preferably on the same host as the orderbook snapshot writer, before live-equivalent forward baskets can accumulate.",
            "- Minimum-size check: any passing candidates must have enough top-of-book size for an equal-share 5-share basket.",
            "- Paper execution: local all-leg-or-none paper ledger records current candidates, and the shared guard fails closed on missing legs, shallow depth, wide spread, cost cap, duplicate condition ids, underround mismatch, or kill switch.",
            "- Blocking risk: forward settlements are still pending, and live execution still needs signed CLOB order placement plus partial-fill cancellation/unwind orchestration.",
            "",
            "Recommended next action: keep running the scanner plus local all-leg-or-none paper cycle on fresh snapshots, then wait for settled exactly-one-winner baskets. Any N100/live deployment must first add signed all-leg execution with partial-fill cancellation/unwind rules and go through `weather-strategy-deploy`.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    snapshot_path = Path(args.snapshot_path) if args.snapshot_path else latest_snapshot(Path(args.snapshot_root))
    rows = read_snapshot(snapshot_path)
    baskets = group_baskets(rows, args)
    paper_candidates = [row for row in baskets if row["paper_shadow_candidate"]]
    thresholds = parse_thresholds(args.telemetry_underround_thresholds)
    conn = connect_db(Path(args.db_path))
    gate = read_json(Path(args.gate_path))
    station_gate = read_json(Path(args.station_basis_gate_path))
    paper_gate = read_json(Path(args.paper_gate_path))
    paper_monitor = read_json(Path(args.paper_monitor_path))
    fresh_cycle = read_json(Path(args.fresh_cycle_path))

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_metric": "current_all_yes_underround_equal_share_basket",
        "db_path": str(Path(args.db_path)),
        "snapshot_path": str(snapshot_path),
        "parameters": {
            "min_underround": args.min_underround,
            "min_leg_count": args.min_leg_count,
            "min_shares": args.min_shares,
            "max_spread": args.max_spread,
            "top_n": args.top_n,
            "telemetry_underround_thresholds": thresholds,
        },
        "snapshot_summary": snapshot_summary(rows),
        "data_self_check": data_self_check(conn),
        "clob_fill_coverage_gate": {
            "gate_pass": gate.get("gate_pass"),
            "fail_reasons": gate.get("fail_reasons"),
            "missing_order_rows": gate.get("missing_order_rows"),
            "over_order_keys": gate.get("over_order_keys"),
            "db_fill_cost_minus_fact_cost": gate.get("db_fill_cost_minus_fact_cost"),
            "order_caps": gate.get("order_caps"),
        },
        "station_basis_gate": {
            "verdict": station_gate.get("verdict"),
            "live_now": station_gate.get("live_now"),
            "entries": station_gate.get("entries"),
            "settled": station_gate.get("settled"),
            "pending": station_gate.get("pending"),
            "blockers": station_gate.get("blockers"),
        },
        "paper_execution_gate": {
            "verdict": paper_gate.get("verdict"),
            "live_now": paper_gate.get("live_now"),
            "blockers": paper_gate.get("blockers"),
            "passed": paper_gate.get("passed"),
            "eval": paper_gate.get("eval"),
        },
        "paper_monitor": {
            "verdict": paper_monitor.get("verdict"),
            "latest_scanner_candidate_count": paper_monitor.get("latest_scanner_candidate_count"),
            "paper_baskets": paper_monitor.get("paper_baskets"),
            "pending_by_city": paper_monitor.get("pending_by_city"),
            "pending_by_event_date": paper_monitor.get("pending_by_event_date"),
            "max_snapshot_age_seconds": paper_monitor.get("max_snapshot_age_seconds"),
            "passed_codes": paper_monitor.get("passed_codes"),
            "blocker_codes": paper_monitor.get("blocker_codes"),
        },
        "fresh_cycle": {
            "verdict": fresh_cycle.get("verdict"),
            "executed_cycle": fresh_cycle.get("executed_cycle"),
            "reason": fresh_cycle.get("reason"),
            "snapshot_path": fresh_cycle.get("snapshot_path"),
            "snapshot_age_seconds": fresh_cycle.get("snapshot_age_seconds"),
            "max_snapshot_age_seconds": fresh_cycle.get("max_snapshot_age_seconds"),
            "generated_at_utc": fresh_cycle.get("generated_at_utc"),
        },
        "event_count": len(baskets),
        "paper_shadow_candidate_count": len(paper_candidates),
        "underround_threshold_telemetry": underround_threshold_telemetry(baskets, thresholds),
        "paper_shadow_candidates": paper_candidates,
        "top_baskets": baskets[: args.top_n],
        "verdict": "PAPER_SHADOW_ENGINEERING_CANDIDATE" if paper_candidates else "NO_CURRENT_EXECUTABLE_BASKET",
        "live_now": False,
        "live_blockers": [
            "no_all_leg_basket_executor",
            "no_partial_fill_cancel_or_unwind_rule",
            "no_forward_paper_ledger_for_current_scanner",
            "deployment_requires_weather_strategy_deploy",
        ],
    }
    Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out_json).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_md(Path(args.out_md), report)
    print(f"wrote {args.out_json}")
    print(f"wrote {args.out_md}")
    print(
        f"verdict={report['verdict']} candidates={len(paper_candidates)} "
        f"top={[(row['city'], row['event_date'], row['underround']) for row in paper_candidates[:5]]}"
    )


if __name__ == "__main__":
    main()
