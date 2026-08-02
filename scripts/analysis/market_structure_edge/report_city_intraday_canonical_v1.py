#!/usr/bin/env python3
"""Report WCIR shadow evidence from canonical candidate facts only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.information_events import canonical_json_hash  # noqa: E402


def build_report(
    db_path: Path,
    *,
    strategy_prefix: str = "weather_city_probability:",
) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    try:
        rows = conn.execute(
            """
            SELECT city,
                   COUNT(*) AS candidates,
                   SUM(candidate_status = 'scored') AS scored,
                   SUM(candidate_status = 'blocked') AS blocked,
                   SUM(COALESCE(policy_selected, 0) <> 0) AS selected,
                   SUM(market_probability IS NOT NULL) AS pit_market,
                   SUM(condition_id IS NOT NULL) AS mapped_expression,
                   SUM(decision_entry_price IS NOT NULL) AS executable_expression,
                   COUNT(DISTINCT event_date) AS target_dates,
                   MIN(decision_ts_utc) AS first_decision_ts_utc,
                   MAX(decision_ts_utc) AS last_decision_ts_utc
            FROM fact_signal_candidates
            WHERE candidate_grain_version = 'v2_event_checkpoint'
              AND strategy_key LIKE ?
            GROUP BY city
            ORDER BY city
            """,
            (f"{strategy_prefix}%",),
        ).fetchall()
    finally:
        conn.close()
    cities = [dict(row) for row in rows]
    totals = {
        field: sum(int(row[field] or 0) for row in rows)
        for field in (
            "candidates",
            "scored",
            "blocked",
            "selected",
            "pit_market",
            "mapped_expression",
            "executable_expression",
        )
    }
    report: dict[str, Any] = {
        "schema_version": "weather_city_canonical_shadow_report_v1",
        "strategy_prefix": strategy_prefix,
        "city_rows": cities,
        "signal_funnel": {
            "unit": "expression_checkpoint",
            "raw_candidates": totals["candidates"],
            "scored_candidates": totals["scored"],
            "blocked_candidates": totals["blocked"],
            "policy_selected": totals["selected"],
        },
        "evidence_funnel": {
            "unit": "expression_checkpoint",
            "pit_market": totals["pit_market"],
            "mapped_expression": totals["mapped_expression"],
            "executable_expression": totals["executable_expression"],
            "plan": "not_available_shadow",
            "order": "not_available_shadow",
            "fill": "not_available_shadow",
            "pnl": "not_computed_without_fill",
        },
    }
    report["report_hash"] = canonical_json_hash(report)
    return report


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# WCIR canonical shadow report",
        "",
        "This report is candidate evidence only. It does not infer plans, orders, fills, or PnL.",
        "",
        "| City | Candidates | Scored | Blocked | Selected | PIT market | Mapped | Executable | Target dates |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["city_rows"]:
        lines.append(
            "| {city} | {candidates} | {scored} | {blocked} | {selected} | "
            "{pit_market} | {mapped_expression} | {executable_expression} | "
            "{target_dates} |".format(**row)
        )
    lines.extend(("", f"Report hash: `{report['report_hash']}`", ""))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "runtime/weather.db")
    parser.add_argument("--strategy-prefix", default="weather_city_probability:")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report(args.db, strategy_prefix=args.strategy_prefix)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(payload, encoding="utf-8")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(markdown_report(report), encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
