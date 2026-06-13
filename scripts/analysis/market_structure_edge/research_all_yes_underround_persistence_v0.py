#!/usr/bin/env python3
"""Snapshot persistence scan for all-YES underround candidates.

This is live-readiness research, not execution. It scans mirrored orderbook
snapshots and measures how often equal-share all-YES underround baskets pass
the current guard, and how long the same event remains actionable.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge.research_all_yes_underround_live_prep_v0 import (  # noqa: E402
    data_self_check,
    group_baskets,
    read_json,
    read_snapshot,
    snapshot_summary,
)
from scripts.ops.all_yes_underround_guards import BasketGuardConfig, check_candidate, decision_to_dict  # noqa: E402


DB_DEFAULT = ROOT / "runtime" / "weather.db"
SNAPSHOT_ROOT_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / "market_data" / "orderbook_snapshots"
GATE_DEFAULT = ROOT / "runtime" / "_dashboard_logs" / "clob_fill_coverage_gate.json"
OUT_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-14-all-yes-underround-persistence-v0.json"
OUT_MD_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-14-all-yes-underround-persistence-v0.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DB_DEFAULT))
    parser.add_argument("--snapshot-root", default=str(SNAPSHOT_ROOT_DEFAULT))
    parser.add_argument("--snapshot-date", default="2026-06-14")
    parser.add_argument("--gate-path", default=str(GATE_DEFAULT))
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    parser.add_argument("--min-underround", type=float, default=0.02)
    parser.add_argument("--min-leg-count", type=int, default=5)
    parser.add_argument("--min-shares", type=float, default=5.0)
    parser.add_argument("--max-spread", type=float, default=0.05)
    parser.add_argument("--max-basket-cost-usd", type=float, default=5.0)
    return parser.parse_args()


def connect_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    return conn


def snapshot_files(root: Path, date: str) -> list[Path]:
    return sorted((root / date).glob("orderbook_snapshot_*.jsonl.gz"))


def scan_snapshot(path: Path, args: argparse.Namespace, guard_cfg: BasketGuardConfig) -> dict[str, Any]:
    rows = read_snapshot(path)
    basket_args = SimpleNamespace(
        min_underround=args.min_underround,
        min_leg_count=args.min_leg_count,
        min_shares=args.min_shares,
        max_spread=args.max_spread,
        top_n=20,
    )
    baskets = group_baskets(rows, basket_args)
    guarded = []
    for basket in baskets:
        if basket.get("paper_shadow_candidate"):
            decision = check_candidate(cfg=guard_cfg, repo_root=ROOT, candidate=basket)
            guarded.append({**basket, "guard": decision_to_dict(decision), "guard_pass": decision.allow})
    passing = [row for row in guarded if row["guard_pass"]]
    summary = snapshot_summary(rows)
    return {
        "path": str(path),
        "snapshot_ts_utc": summary.get("snapshot_ts_utc_max"),
        "rows": summary.get("rows"),
        "yes_events": summary.get("yes_event_count"),
        "event_dates": summary.get("event_dates"),
        "candidate_count": len(passing),
        "guarded_candidate_count": len(guarded),
        "top_basket_count": len(baskets),
        "candidates": passing,
        "top_rejections": [row for row in baskets[:10] if not row.get("paper_shadow_candidate")],
    }


def event_key(row: dict[str, Any]) -> str:
    return "|".join([str(row.get("event_date")), str(row.get("city")), str(row.get("event_slug"))])


def build_sequences(snapshot_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for snap in snapshot_results:
        for candidate in snap["candidates"]:
            by_event[event_key(candidate)].append({"snapshot_ts_utc": snap["snapshot_ts_utc"], **candidate})
    sequences = []
    for key, rows in by_event.items():
        rows = sorted(rows, key=lambda row: row["snapshot_ts_utc"] or "")
        sample = rows[0]
        sequences.append(
            {
                "event_key": key,
                "event_date": sample.get("event_date"),
                "city": sample.get("city"),
                "event_slug": sample.get("event_slug"),
                "observations": len(rows),
                "first_snapshot_ts_utc": rows[0].get("snapshot_ts_utc"),
                "last_snapshot_ts_utc": rows[-1].get("snapshot_ts_utc"),
                "max_underround": max(float(row.get("underround") or 0.0) for row in rows),
                "min_total_yes_ask_cost": min(float(row.get("total_yes_ask_cost") or 99.0) for row in rows),
                "max_min_ask_size": max(float(row.get("min_ask_size") or 0.0) for row in rows),
                "rows": rows,
            }
        )
    return sorted(sequences, key=lambda row: (row["observations"], row["max_underround"]), reverse=True)


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{value * 100:+.1f}%"


def write_md(path: Path, report: dict[str, Any]) -> None:
    latest = report["snapshots"][-1] if report["snapshots"] else {}
    latest_candidate_note = (
        f"The latest snapshot has {latest.get('candidate_count')} guard-passing candidate(s), led by "
        + ", ".join(f"{row['city']} {fmt_pct(row.get('underround'))}" for row in (latest.get("candidates") or [])[:3])
        if latest.get("candidate_count")
        else "The latest snapshot has no guard-passing candidates."
    )
    lines = [
        "# All-YES Underround Snapshot Persistence v0",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> target_metric: `{report['target_metric']}`",
        f"> verdict: `{report['verdict']}`",
        "> Scope: local research only; no N100/live config changed and no orders placed.",
        "",
        "## Data Snapshot",
        "",
        f"- DB: `{report['db_path']}`",
        f"- Snapshot date folder: `{report['snapshot_date']}`; files scanned `{report['snapshot_files_scanned']}`.",
        f"- Latest snapshot: `{latest.get('path')}`; ts `{latest.get('snapshot_ts_utc')}`; current candidates `{latest.get('candidate_count')}`.",
        f"- CLOB fill coverage gate: `gate_pass={report['clob_fill_coverage_gate'].get('gate_pass')}`; fail_reasons `{report['clob_fill_coverage_gate'].get('fail_reasons')}`.",
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
        "## Persistence Summary",
        "",
        f"- Snapshots with candidates: `{report['summary']['snapshots_with_candidates']}` / `{report['summary']['snapshots_scanned']}`.",
        f"- Total candidate observations: `{report['summary']['candidate_observations']}`.",
        f"- Unique candidate events: `{report['summary']['unique_candidate_events']}`.",
        f"- Max observations for one event: `{report['summary']['max_observations_per_event']}` snapshots.",
        f"- Latest scanner state: `{report['summary']['latest_state']}`.",
        "",
        "| snapshot_ts_utc | rows | yes_events | candidates | candidate cities |",
        "|---|---:|---:|---:|---|",
    ]
    for snap in report["snapshots"]:
        cities = ", ".join(f"{row['city']}({fmt_pct(row.get('underround'))})" for row in snap["candidates"][:5])
        lines.append(
            f"| `{snap['snapshot_ts_utc']}` | {snap['rows']} | {snap['yes_events']} | {snap['candidate_count']} | `{cities}` |"
        )
    lines.extend(
        [
            "",
            "## Candidate Sequences",
            "",
            "| city | event_date | observations | first | last | max underround | min cost | max min-size |",
            "|---|---|---:|---|---|---:|---:|---:|",
        ]
    )
    for seq in report["sequences"]:
        lines.append(
            f"| `{seq['city']}` | {seq['event_date']} | {seq['observations']} | `{seq['first_snapshot_ts_utc']}` | "
            f"`{seq['last_snapshot_ts_utc']}` | {fmt_pct(seq['max_underround'])} | {seq['min_total_yes_ask_cost']:.3f} | {seq['max_min_ask_size']:.2f} |"
        )
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            "- `all_yes_underround_basket_v0` remains the leading market-structure direction, but this scan still does not approve live trading.",
            f"- {latest_candidate_note}",
            "- Candidate recurrence is real but sparse across the sampled snapshots, so future evidence must come from fresh live-equivalent paper capture rather than delayed local sync observations.",
            "- Any future live implementation must be snapshot-driven and all-leg-or-none; stale baskets must not be carried forward into live orders.",
            "- Continue low-latency paper monitoring until enough TTL-valid baskets settle.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    files = snapshot_files(Path(args.snapshot_root), args.snapshot_date)
    guard_cfg = BasketGuardConfig(
        min_underround=args.min_underround,
        min_legs=args.min_leg_count,
        shares_per_leg=args.min_shares,
        max_basket_cost_usd=args.max_basket_cost_usd,
        max_yes_spread=args.max_spread,
    )
    snapshots = [scan_snapshot(path, args, guard_cfg) for path in files]
    sequences = build_sequences(snapshots)
    candidate_counts = [snap["candidate_count"] for snap in snapshots]
    latest_state = "NO_CURRENT_EXECUTABLE_BASKET"
    if snapshots and snapshots[-1]["candidate_count"] > 0:
        latest_state = "CURRENT_EXECUTABLE_BASKET"
    conn = connect_db(Path(args.db_path))
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_metric": "all_yes_underround_snapshot_persistence",
        "db_path": str(Path(args.db_path)),
        "snapshot_date": args.snapshot_date,
        "snapshot_files_scanned": len(files),
        "parameters": {
            "min_underround": args.min_underround,
            "min_leg_count": args.min_leg_count,
            "min_shares": args.min_shares,
            "max_spread": args.max_spread,
            "max_basket_cost_usd": args.max_basket_cost_usd,
        },
        "data_self_check": data_self_check(conn),
        "clob_fill_coverage_gate": {
            "gate_pass": read_json(Path(args.gate_path)).get("gate_pass"),
            "fail_reasons": read_json(Path(args.gate_path)).get("fail_reasons"),
        },
        "summary": {
            "snapshots_scanned": len(snapshots),
            "snapshots_with_candidates": sum(1 for count in candidate_counts if count > 0),
            "candidate_observations": sum(candidate_counts),
            "unique_candidate_events": len(sequences),
            "max_observations_per_event": max((seq["observations"] for seq in sequences), default=0),
            "latest_state": latest_state,
        },
        "snapshots": snapshots,
        "sequences": sequences,
        "verdict": "PAPER_SHADOW_FLICKERY_OPPORTUNITY" if sequences else "NO_PERSISTENCE_EVIDENCE",
    }
    Path(args.out_json).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_md(Path(args.out_md), report)
    print(f"wrote {args.out_json}")
    print(f"wrote {args.out_md}")
    print(
        f"verdict={report['verdict']} snapshots={len(snapshots)} "
        f"with_candidates={report['summary']['snapshots_with_candidates']} latest={latest_state}"
    )


if __name__ == "__main__":
    main()
