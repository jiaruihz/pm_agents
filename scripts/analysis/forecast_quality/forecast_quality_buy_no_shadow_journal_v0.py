#!/usr/bin/env python3
"""Materialize the forecast-quality BUY_NO candidate as a zero-notional shadow journal.

This is local research only. It reads the fresh shadow candidate CSV produced by
research_forecast_quality_live_candidate_v0.py and writes an idempotent JSONL
journal plus Markdown/JSON summaries. It never changes N100/live config and
never submits orders.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
DB_DEFAULT = ROOT / "runtime/weather.db"
SOURCE_JSON_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-13-forecast-quality-live-candidate-v0.json"
SOURCE_CSV_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-13-forecast-quality-shadow-candidates-v0.csv"
OUT_JSON_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-13-forecast-quality-buy-no-shadow-journal-v0.json"
OUT_JSONL_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-13-forecast-quality-buy-no-shadow-journal-v0.jsonl"
OUT_MD_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-13-forecast-quality-buy-no-shadow-journal-v0.md"
GATE_JSON = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"

JOURNAL_SCHEMA_VERSION = "forecast_quality_buy_no_shadow_journal_v0"
SHADOW_RULE_ID = "ecmwf_buy_no_exclude_low_edge010_cost40_75_cityday_top1_v0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DB_DEFAULT))
    parser.add_argument("--source-json", default=str(SOURCE_JSON_DEFAULT))
    parser.add_argument("--source-csv", default=str(SOURCE_CSV_DEFAULT))
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-jsonl", default=str(OUT_JSONL_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql).fetchall()]


def scalar(conn: sqlite3.Connection, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def load_gate_status() -> dict[str, Any]:
    if not GATE_JSON.exists():
        return {"gate_pass": None, "missing": True, "path": str(GATE_JSON)}
    try:
        data = json.loads(GATE_JSON.read_text())
    except json.JSONDecodeError as exc:
        return {"gate_pass": None, "error": str(exc), "path": str(GATE_JSON)}
    return {
        "gate_pass": data.get("gate_pass"),
        "fail_reasons": data.get("fail_reasons", []),
        "path": str(GATE_JSON),
    }


def data_self_check(conn: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
    return {
        "db_path": str(db_path),
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, timezone.utc).isoformat()
        if db_path.exists()
        else None,
        "fact_trades_max_built_at_utc": scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_trades"),
        "trade_class_distribution": rows(
            conn, "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class"
        ),
        "settlement_status_distribution": rows(
            conn,
            "SELECT COALESCE(settlement_status, '') AS settlement_status, COUNT(*) AS rows "
            "FROM fact_trades GROUP BY COALESCE(settlement_status, '') ORDER BY settlement_status",
        ),
        "candidate_coverage": rows(
            conn,
            "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
            "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
        )[0],
        "order_fill_coverage": rows(
            conn,
            "SELECT o.status, COUNT(*) AS orders, "
            "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
            "FROM orders o LEFT JOIN fills f USING(execution_id) "
            "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
        ),
        "fact_signal_candidates_max_built_at_utc": scalar(
            conn, "SELECT MAX(fact_built_at_utc) FROM fact_signal_candidates"
        ),
    }


def load_source_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def load_candidate_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def shadow_decision_id(row: dict[str, Any]) -> str:
    raw = "|".join(
        [
            SHADOW_RULE_ID,
            str(row["event_date"]),
            str(row["city"]),
            str(row["bracket"]),
            str(row["direction"]),
            str(row["decision_snapshot_ts_utc"]),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def build_journal_rows(source_rows: list[dict[str, Any]], source_payload: dict[str, Any], recorded_at: str) -> list[dict[str, Any]]:
    profile = source_payload.get("final_profile", {})
    out = []
    for row in source_rows:
        no_cost = float(row["no_cost"])
        shares = float(row["shares_at_5usd"])
        out.append(
            {
                "journal_schema_version": JOURNAL_SCHEMA_VERSION,
                "shadow_rule_id": SHADOW_RULE_ID,
                "shadow_decision_id": shadow_decision_id(row),
                "recorded_at_utc": recorded_at,
                "action": "zero_notional_shadow_no_order",
                "order_type": "zero_notional_shadow",
                "event_date": row["event_date"],
                "target_date": row["event_date"],
                "city": row["city"],
                "bracket": row["bracket"],
                "side": "BUY_NO",
                "direction": row["direction"],
                "model_version": profile.get("model", "ecmwf"),
                "forecast_quality_low": int(row["forecast_quality_low"]),
                "decision_snapshot_ts_utc": row["decision_snapshot_ts_utc"],
                "no_cost": no_cost,
                "no_edge": float(row["no_edge"]),
                "hypothetical_order_size_usd": float(row["notional_usd"]),
                "hypothetical_shares_at_5usd": shares,
                "meets_min_5_shares": shares >= 5.0,
                "profile_constraints": {
                    "side": "BUY_NO",
                    "model_version": "ecmwf",
                    "forecast_quality_low": 0,
                    "no_cost_min": 0.40,
                    "no_cost_max": 0.75,
                    "no_edge_min": 0.10,
                    "cityday_top1": True,
                },
                "source_csv": str(SOURCE_CSV_DEFAULT),
                "source_json": str(SOURCE_JSON_DEFAULT),
            }
        )
    return sorted(out, key=lambda item: (item["event_date"], item["city"], item["bracket"]))


def read_existing_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def merge_by_id(existing: list[dict[str, Any]], new_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {row["shadow_decision_id"]: row for row in existing}
    for row in new_rows:
        merged[row["shadow_decision_id"]] = row
    return sorted(merged.values(), key=lambda item: (item["event_date"], item["city"], item["bracket"]))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, payload_rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in payload_rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def table(headers: list[str], body: list[list[Any]]) -> str:
    return "\n".join(
        ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
        + ["| " + " | ".join(str(cell) for cell in row) + " |" for row in body]
    )


def summarize_journal(journal_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in journal_rows:
        by_date[row["event_date"]].append(row)
    return {
        "rows": len(journal_rows),
        "active_dates": len(by_date),
        "cities": sorted({row["city"] for row in journal_rows}),
        "min_shares_at_5usd": min((row["hypothetical_shares_at_5usd"] for row in journal_rows), default=None),
        "max_daily_hypothetical_notional_usd": max(
            (sum(row["hypothetical_order_size_usd"] for row in rows_in) for rows_in in by_date.values()),
            default=0.0,
        ),
        "by_date": [
            {
                "event_date": date,
                "rows": len(rows_in),
                "cities": sorted({row["city"] for row in rows_in}),
                "hypothetical_notional_usd": sum(row["hypothetical_order_size_usd"] for row in rows_in),
            }
            for date, rows_in in sorted(by_date.items())
        ],
    }


def write_md(path: Path, report: dict[str, Any]) -> None:
    summary = report["journal_summary"]
    lines = [
        "# Forecast Quality BUY_NO Shadow Journal v0",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> journal_schema_version: `{JOURNAL_SCHEMA_VERSION}`",
        "> scope: local zero-notional shadow only; no N100/live config changed; no live orders.",
        "",
        "## 数据快照",
        "",
        f"- DB: `{report['data_self_check']['db_path']}`",
        f"- DB last_modified_utc: `{report['data_self_check']['db_last_modified_utc']}`",
        f"- fact_trades MAX(fact_built_at_utc): `{report['data_self_check']['fact_trades_max_built_at_utc']}`",
        f"- fact_signal_candidates MAX(fact_built_at_utc): `{report['data_self_check']['fact_signal_candidates_max_built_at_utc']}`",
        f"- CLOB coverage gate: `{report['clob_coverage_gate'].get('gate_pass')}`; live_real PnL/ROI/rank/curve not published.",
        "",
        "### 5 行 SQL 自检",
        "",
        "```json",
        json.dumps(
            {
                "fact_trades_max_built_at_utc": report["data_self_check"]["fact_trades_max_built_at_utc"],
                "trade_class_distribution": report["data_self_check"]["trade_class_distribution"],
                "settlement_status_distribution": report["data_self_check"]["settlement_status_distribution"],
                "candidate_coverage": report["data_self_check"]["candidate_coverage"],
                "order_fill_coverage": report["data_self_check"]["order_fill_coverage"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "## Shadow Rule",
        "",
        "```text",
        SHADOW_RULE_ID,
        "BUY_NO only; model_version=ecmwf; forecast_quality_low=0;",
        "0.40<=no_cost<=0.75; no_edge>=0.10; city-date top1; hypothetical $5/order;",
        "order_type=zero_notional_shadow",
        "```",
        "",
        "## Journal Summary",
        "",
        f"- new rows this run: `{report['new_rows_this_run']}`",
        f"- journal rows after merge: `{summary['rows']}`",
        f"- active dates: `{summary['active_dates']}`",
        f"- cities: `{', '.join(summary['cities']) if summary['cities'] else 'none'}`",
        f"- max daily hypothetical notional: `${summary['max_daily_hypothetical_notional_usd']:.2f}`",
        f"- min shares @ $5/order: `{summary['min_shares_at_5usd']:.2f}`"
        if summary["min_shares_at_5usd"] is not None
        else "- min shares @ $5/order: `NA`",
        "",
        table(
            ["event_date", "rows", "cities", "hypothetical notional"],
            [
                [
                    row["event_date"],
                    row["rows"],
                    ",".join(row["cities"]),
                    f"${row['hypothetical_notional_usd']:.2f}",
                ]
                for row in summary["by_date"]
            ],
        ),
        "",
        "## Readiness",
        "",
        "- 这是 forward shadow journal 的第一批记录，不构成 live edge 结论。",
        "- 因上一轮 fresh rerun 是 `shadow_only` 且 CLOB gate=false，当前只记录，不进入 $5/order tiny live。",
        "",
        "## Files",
        "",
        f"- JSON summary: `{report['out_json']}`",
        f"- JSONL journal: `{report['out_jsonl']}`",
        f"- source CSV: `{report['source_csv']}`",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)
    source_json = Path(args.source_json)
    source_csv = Path(args.source_csv)
    out_json = Path(args.out_json)
    out_jsonl = Path(args.out_jsonl)
    out_md = Path(args.out_md)
    generated_at = datetime.now(timezone.utc).isoformat()

    conn = connect(db_path)
    self_check = data_self_check(conn, db_path)
    gate = load_gate_status()
    source_payload = load_source_payload(source_json)
    candidate_rows = load_candidate_rows(source_csv)
    new_journal_rows = build_journal_rows(candidate_rows, source_payload, generated_at)
    merged_rows = merge_by_id(read_existing_jsonl(out_jsonl), new_journal_rows)
    summary = summarize_journal(merged_rows)

    report = {
        "generated_at_utc": generated_at,
        "git_sha": git_sha(),
        "journal_schema_version": JOURNAL_SCHEMA_VERSION,
        "shadow_rule_id": SHADOW_RULE_ID,
        "source_json": str(source_json),
        "source_csv": str(source_csv),
        "out_json": str(out_json),
        "out_jsonl": str(out_jsonl),
        "data_self_check": self_check,
        "clob_coverage_gate": gate,
        "source_verdict": source_payload.get("verdict"),
        "source_verdict_reasons": source_payload.get("verdict_reasons"),
        "new_rows_this_run": len(new_journal_rows),
        "journal_summary": summary,
        "journal_sample": merged_rows[:20],
    }
    write_json(out_json, report)
    write_jsonl(out_jsonl, merged_rows)
    write_md(out_md, report)
    print(json.dumps({"wrote": [str(out_json), str(out_jsonl), str(out_md)], "summary": summary}, indent=2))


if __name__ == "__main__":
    main()
