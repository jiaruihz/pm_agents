#!/usr/bin/env python3
"""Evaluate zero-notional Range RV shadow journal against pm_history.

This script is intentionally a shadow evaluator, not a live PnL report.  The
input rows are strategy telemetry emitted by scripts/ops/range_rv_shadow_v0.py;
each row is a selected basket at one snapshot timestamp, with
no_order_placed=true.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
TARGET_METRIC = "forecast_bounded_range_rv_shadow_forward_telemetry_v0"
OUT_JSON_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-16-range-rv-shadow-status-v0.json"
OUT_MD_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-16-range-rv-shadow-status-v0.md"


def default_journal_path() -> Path:
    candidates = [
        ROOT / "runtime/weather_edge_v1/remote_pm_agent/range_rv_shadow_v0/shadow_journal.jsonl",
        ROOT / "runtime/weather_edge_v1/range_rv_shadow_v0/shadow_journal.jsonl",
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", default=str(default_journal_path()))
    parser.add_argument("--pm-history-dir", default=str(ROOT / "runtime/weather_edge_v1/market_data/cache/pm_history"))
    parser.add_argument("--db-path", default=str(ROOT / "runtime/weather.db"))
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def pct(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "NA"
    return f"{value * 100:+.1f}%"


def table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def connect_ro(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.exists():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def sql_rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def sql_scalar(conn: sqlite3.Connection, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def mandatory_self_check(db_path: Path) -> dict[str, Any]:
    conn = connect_ro(db_path)
    if conn is None:
        return {"db_path": str(db_path), "error": "missing_db"}
    with conn:
        return {
            "max_fact_built_at_utc": sql_scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_trades"),
            "trade_class_distribution": sql_rows(
                conn,
                "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class",
            ),
            "settlement_status_distribution": sql_rows(
                conn,
                "SELECT COALESCE(settlement_status, '') AS settlement_status, COUNT(*) AS rows "
                "FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
            ),
            "candidate_coverage": sql_rows(
                conn,
                "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
                "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
            )[0],
            "order_fill_coverage": sql_rows(
                conn,
                "SELECT o.status, COUNT(*) AS orders, "
                "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
                "FROM orders o LEFT JOIN fills f USING(execution_id) "
                "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
            ),
        }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def winner_for(pm_history_dir: Path, city: str, event_date: str) -> tuple[str | None, str]:
    path = pm_history_dir / f"{city}_{event_date}.json"
    if not path.exists():
        return None, "missing_event"
    data = json.loads(path.read_text())
    winners = []
    for bracket in data.get("brackets", []):
        price = safe_float(bracket.get("final_price"))
        if price is not None and price >= 0.999:
            winners.append(str(bracket.get("label")))
    if len(winners) != 1:
        return None, f"winner_count_{len(winners)}"
    return winners[0], "settled"


def evaluate_row(row: dict[str, Any], pm_history_dir: Path) -> dict[str, Any]:
    winner, status = winner_for(pm_history_dir, str(row["city"]), str(row["event_date"]))
    legs = row.get("legs") or []
    gross_cost = sum(float(leg["best_ask"]) for leg in legs if leg.get("best_ask") is not None)
    effective_cost = float(row["effective_range_cost"])
    inside = set(map(str, row.get("inside_brackets") or []))
    hit = None
    pnl = None
    if status == "settled":
        hit = winner in inside
        if row["expression"] == "inside_yes":
            payout = 1.0 if hit else 0.0
            pnl = payout - gross_cost
        elif row["expression"] == "outside_no":
            outside = {str(leg.get("bracket")) for leg in legs}
            payout = (len(legs) - 1.0) if winner in outside else float(len(legs))
            pnl = payout - gross_cost
        else:
            raise ValueError(f"unknown expression: {row['expression']}")
    return {
        **row,
        "winner": winner,
        "settlement_eval_status": status,
        "gross_cost": gross_cost,
        "eff_cost": effective_cost,
        "pnl": pnl,
        "hit": hit,
    }


def decision_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row["city"]),
        str(row["event_date"]),
        str(row.get("forecast_source") or ""),
        str(row["model_version"]),
    )


def dedup_latest(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = decision_key(row)
        if key not in by_key or str(row["snapshot_ts_utc"]) > str(by_key[key]["snapshot_ts_utc"]):
            by_key[key] = row
    return list(by_key.values())


def dedup_first(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = decision_key(row)
        if key not in by_key or str(row["snapshot_ts_utc"]) < str(by_key[key]["snapshot_ts_utc"]):
            by_key[key] = row
    return list(by_key.values())


def agg(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cost = sum(float(row["eff_cost"]) for row in rows)
    pnl_rows = [row for row in rows if row.get("pnl") is not None]
    pnl = sum(float(row["pnl"]) for row in pnl_rows)
    hit_rows = [row for row in rows if row.get("hit") is not None]
    return {
        "rows": len(rows),
        "cities": len({row["city"] for row in rows}),
        "event_dates": len({row["event_date"] for row in rows}),
        "unique_decision_groups": len({decision_key(row) for row in rows}),
        "snapshots": len({row["snapshot_ts_utc"] for row in rows}),
        "effective_cost": cost,
        "pnl": pnl,
        "roi": None if cost <= 0 else pnl / cost,
        "hit_rate": None if not hit_rows else sum(1 for row in hit_rows if row["hit"]) / len(hit_rows),
    }


def group_summary(rows: list[dict[str, Any]], key_name: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key_name))].append(row)
    out = []
    for key, group in grouped.items():
        item = agg(group)
        item[key_name] = key
        out.append(item)
    return sorted(out, key=lambda item: (item["event_dates"], item["rows"], item["pnl"]), reverse=True)


def by_event_date(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["event_date"])].append(row)
    out = []
    for event_date, group in sorted(grouped.items()):
        settled = [row for row in group if row["settlement_eval_status"] == "settled"]
        unsettled = [row for row in group if row["settlement_eval_status"] != "settled"]
        out.append(
            {
                "event_date": event_date,
                "shadow_rows": len(group),
                "settled_rows": len(settled),
                "unsettled_rows": len(unsettled),
                "unsettled_status": dict(Counter(row["settlement_eval_status"] for row in unsettled)),
                "raw_settled": agg(settled),
                "dedup_latest_settled": agg(dedup_latest(settled)),
                "dedup_first_settled": agg(dedup_first(settled)),
            }
        )
    return out


def compact_agg(row: dict[str, Any]) -> list[Any]:
    return [
        row["rows"],
        row["unique_decision_groups"],
        row["cities"],
        row["event_dates"],
        f"{row['effective_cost']:.3f}",
        f"{row['pnl']:+.3f}",
        pct(row["roi"]),
        pct(row["hit_rate"]),
    ]


def render_md(payload: dict[str, Any]) -> str:
    event_rows = []
    for row in payload["by_event_date"]:
        latest = row["dedup_latest_settled"]
        event_rows.append(
            [
                row["event_date"],
                row["shadow_rows"],
                row["settled_rows"],
                row["unsettled_rows"],
                latest["rows"],
                latest["unique_decision_groups"],
                f"{latest['pnl']:+.3f}",
                pct(latest["roi"]),
                pct(latest["hit_rate"]),
                json.dumps(row["unsettled_status"], sort_keys=True),
            ]
        )

    city_rows = [
        [row["city"], *compact_agg(row)]
        for row in payload["dedup_latest_by_city"][:30]
    ]
    expr_rows = [
        [row["expression"], *compact_agg(row)]
        for row in payload["dedup_latest_by_expression"]
    ]

    lines = [
        "# Range RV Shadow Status v0",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        f"> target_metric: `{TARGET_METRIC}`",
        f"> journal: `{payload['journal_path']}`",
        f"> pm_history_dir: `{payload['pm_history_dir']}`",
        "",
        "## Data Snapshot",
        "",
        "- Evidence layer: zero-notional shadow telemetry from `scripts/ops/range_rv_shadow_v0.py`, plus `pm_history` settlement truth when available.",
        "- This report is not live PnL. Every journal row must keep `no_order_placed=true`.",
        f"- journal_rows: `{payload['journal']['rows']}`.",
        f"- snapshot range: `{payload['journal']['min_snapshot_ts_utc']}` -> `{payload['journal']['max_snapshot_ts_utc']}`.",
        f"- event_dates: `{', '.join(payload['journal']['event_dates'])}`.",
        f"- all_no_order_placed: `{payload['journal']['all_no_order_placed']}`.",
        "",
        "### Mandatory SQL Self-Check",
        "",
        "```json",
        json.dumps(payload["self_check"], indent=2, sort_keys=True),
        "```",
        "",
        "## Current Verdict",
        "",
        "`inconclusive / keep collecting shadow data`: the N100 runner is healthy, but settled forward evidence is still too thin. Do not convert this to paper/live until the deduped settled sample passes the support, baseline, forward, and capacity gates defined in the live-standard report.",
        "",
        "## Event-Date Funnel",
        "",
        table(
            [
                "event_date",
                "shadow_rows",
                "settled_rows",
                "unsettled_rows",
                "dedup_latest_rows",
                "dedup_groups",
                "dedup_pnl",
                "dedup_roi",
                "dedup_hit",
                "unsettled_status",
            ],
            event_rows,
        ),
        "",
        "## Dedup Latest Settled By City",
        "",
        table(
            ["city", "rows", "groups", "cities", "dates", "cost", "pnl", "roi", "hit"],
            city_rows,
        )
        if city_rows
        else "_No settled dedup latest city rows yet._",
        "",
        "## Dedup Latest Settled By Expression",
        "",
        table(
            ["expression", "rows", "groups", "cities", "dates", "cost", "pnl", "roi", "hit"],
            expr_rows,
        )
        if expr_rows
        else "_No settled dedup latest expression rows yet._",
        "",
        "## Read-Me For Future Runs",
        "",
        "- Primary unit for conclusions: `city + event_date + forecast_source + model_version`, using dedup latest and dedup first as timing sensitivity checks.",
        "- Raw shadow rows are useful for telemetry and persistence, but not for strategy conclusions because the same city-day can trigger every 30 minutes.",
        "- Actual orders remain zero unless a separate deploy changes the execution mode; this report must not be mixed with `live_real` PnL.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    journal_path = Path(args.journal)
    pm_history_dir = Path(args.pm_history_dir)
    rows = load_jsonl(journal_path)
    evaluated = [evaluate_row(row, pm_history_dir) for row in rows]
    settled = [row for row in evaluated if row["settlement_eval_status"] == "settled"]
    dedup_latest_settled = dedup_latest(settled)

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": TARGET_METRIC,
        "journal_path": str(journal_path),
        "pm_history_dir": str(pm_history_dir),
        "self_check": mandatory_self_check(Path(args.db_path)),
        "journal": {
            "rows": len(rows),
            "all_no_order_placed": all(row.get("no_order_placed") is True for row in rows),
            "all_zero_notional_shadow": all(row.get("execution_mode") == "zero_notional_shadow" for row in rows),
            "min_shadow_generated_at_utc": min((str(row.get("shadow_generated_at_utc")) for row in rows), default=None),
            "max_shadow_generated_at_utc": max((str(row.get("shadow_generated_at_utc")) for row in rows), default=None),
            "min_snapshot_ts_utc": min((str(row.get("snapshot_ts_utc")) for row in rows), default=None),
            "max_snapshot_ts_utc": max((str(row.get("snapshot_ts_utc")) for row in rows), default=None),
            "event_dates": sorted({str(row.get("event_date")) for row in rows}),
            "cities": len({str(row.get("city")) for row in rows}),
            "unique_decision_groups": len({decision_key(row) for row in rows}),
        },
        "overall_raw_settled": agg(settled),
        "overall_dedup_latest_settled": agg(dedup_latest_settled),
        "overall_dedup_first_settled": agg(dedup_first(settled)),
        "by_event_date": by_event_date(evaluated),
        "dedup_latest_by_city": group_summary(dedup_latest_settled, "city"),
        "dedup_latest_by_expression": group_summary(dedup_latest_settled, "expression"),
        "dedup_latest_by_model": group_summary(dedup_latest_settled, "model_version"),
    }

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    out_md.write_text(render_md(payload))
    print(json.dumps({"out_json": str(out_json), "out_md": str(out_md), "journal_rows": len(rows)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
