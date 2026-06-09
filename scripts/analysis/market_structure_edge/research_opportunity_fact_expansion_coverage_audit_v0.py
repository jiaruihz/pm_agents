#!/usr/bin/env python3
"""Audit local data coverage for expanding weather opportunity facts.

This is a coverage audit only. It does not compute strategy PnL from raw files
and does not change N100/live configuration. Strategy conclusions remain bound
to runtime/weather.db fact tables.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import sqlite3
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[3]
DB_DEFAULT = ROOT / "runtime" / "weather.db"
MARKET_DATA = ROOT / "runtime" / "weather_edge_v1" / "market_data"
OUT_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-10-opportunity-fact-expansion-coverage-audit-v0.json"
OUT_MD_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-10-opportunity-fact-expansion-coverage-audit-v0.md"

DATE_RE = re.compile(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})")
PM_HISTORY_CITY_DAY_RE = re.compile(r"_(20\d{2}-\d{2}-\d{2})\.json$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DB_DEFAULT))
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    return parser.parse_args()


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
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


def iso_mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def date_from_text(text: str) -> str | None:
    match = DATE_RE.search(text)
    if not match:
        return None
    candidate = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    try:
        datetime.strptime(candidate, "%Y-%m-%d")
    except ValueError:
        return None
    return candidate


def date_from_pm_history_city_day(text: str) -> str | None:
    match = PM_HISTORY_CITY_DAY_RE.search(text)
    if not match:
        return None
    candidate = match.group(1)
    try:
        datetime.strptime(candidate, "%Y-%m-%d")
    except ValueError:
        return None
    return candidate


def date_range(values: Iterable[str | None]) -> dict[str, Any]:
    dates = sorted({value for value in values if value})
    return {
        "min_date": dates[0] if dates else None,
        "max_date": dates[-1] if dates else None,
        "distinct_dates": len(dates),
    }


def count_files(root: Path, pattern: str) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob(pattern) if path.is_file())


def summarize_files(
    root: Path,
    pattern: str,
    *,
    label: str,
    date_extractor=date_from_text,
) -> dict[str, Any]:
    files = count_files(root, pattern)
    dates = [date_extractor(str(path.relative_to(root))) for path in files]
    return {
        "label": label,
        "path": str(root),
        "pattern": pattern,
        "exists": root.exists(),
        "files": len(files),
        "dated_files": sum(1 for date in dates if date),
        "latest_mtime_utc": max((iso_mtime(path) for path in files), default=None),
        **date_range(dates),
        "sample_files": [str(path.relative_to(root)) for path in files[:5]],
    }


def summarize_db(conn: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
    return {
        "db_path": str(db_path),
        "db_last_modified_utc": iso_mtime(db_path),
        "max_fact_built_at_utc": scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_trades"),
        "trade_class_distribution": rows(
            conn, "SELECT trade_class, COUNT(*) AS n FROM fact_trades GROUP BY trade_class ORDER BY trade_class"
        ),
        "settlement_status_distribution": rows(
            conn,
            "SELECT settlement_status, COUNT(*) AS n FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
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
        "fact_signal_candidates_range": rows(
            conn,
            "SELECT MIN(event_date) AS min_event_date, MAX(event_date) AS max_event_date, "
            "COUNT(DISTINCT event_date) AS event_dates, COUNT(*) AS rows FROM fact_signal_candidates",
        )[0],
        "fact_signal_candidates_usable_main": rows(
            conn,
            "SELECT MIN(event_date) AS min_event_date, MAX(event_date) AS max_event_date, "
            "COUNT(*) AS rows, COUNT(DISTINCT event_date) AS event_dates, "
            "COUNT(DISTINCT city || '|' || event_date) AS city_days "
            "FROM fact_signal_candidates "
            "WHERE eligible=1 AND final_yes IS NOT NULL AND decision_window_missing=0",
        )[0],
        "fact_trades_range": rows(
            conn,
            "SELECT MIN(target_date) AS min_target_date, MAX(target_date) AS max_target_date, "
            "COUNT(DISTINCT target_date) AS target_dates, COUNT(*) AS rows FROM fact_trades",
        )[0],
        "decision_window_missing": rows(
            conn,
            "SELECT decision_window_missing, COUNT(*) AS rows FROM fact_signal_candidates "
            "GROUP BY decision_window_missing ORDER BY decision_window_missing",
        ),
    }


def summarize_paper_orders(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False, "rows": 0}
    rows_n = 0
    dates: list[str | None] = []
    pools: Counter[str] = Counter()
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows_n += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            dates.append(rec.get("event_date") or rec.get("target_date") or date_from_text(line))
            if rec.get("city_pool") is not None:
                pools[str(rec.get("city_pool"))] += 1
    return {
        "path": str(path),
        "exists": True,
        "rows": rows_n,
        **date_range(dates),
        "city_pool_distribution": dict(pools.most_common()),
    }


def csv_date_coverage(path: Path, max_rows: int = 2_000_000) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    dates: list[str | None] = []
    rows_n = 0
    try:
        with path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            date_cols = [
                col
                for col in (reader.fieldnames or [])
                if col and col.lower() in {"date", "day", "valid", "valid_time", "timestamp", "time", "datetime"}
            ]
            for rec in reader:
                rows_n += 1
                value = None
                for col in date_cols:
                    value = date_from_text(str(rec.get(col, "")))
                    if value:
                        break
                dates.append(value)
                if rows_n >= max_rows:
                    break
    except Exception as exc:
        return {"path": str(path), "exists": True, "error": str(exc), "rows_scanned": rows_n}
    return {"path": str(path), "exists": True, "rows_scanned": rows_n, **date_range(dates)}


def summarize_weather_csv_dir(root: Path, pattern: str, label: str) -> dict[str, Any]:
    files = count_files(root, pattern)
    filename_dates = [date_from_text(str(path.relative_to(root))) for path in files]
    sampled = [csv_date_coverage(path) for path in files[:10]]
    sampled_dates: list[str | None] = []
    for item in sampled:
        sampled_dates.extend([item.get("min_date"), item.get("max_date")])
    return {
        "label": label,
        "path": str(root),
        "pattern": pattern,
        "exists": root.exists(),
        "files": len(files),
        "latest_mtime_utc": max((iso_mtime(path) for path in files), default=None),
        "filename_date_range": date_range(filename_dates),
        "sampled_content_date_range": date_range(sampled_dates),
        "sampled_files": sampled,
    }


def builder_inputs() -> dict[str, Any]:
    builder = ROOT / "scripts" / "etl" / "build_weather_signal_candidates.py"
    text = builder.read_text(encoding="utf-8") if builder.exists() else ""
    return {
        "builder_path": str(builder),
        "exists": builder.exists(),
        "uses_paper_snapshots": "paper_snapshots" in text,
        "uses_paper_orders": "paper_orders" in text,
        "uses_fact_trades": "fact_trades" in text,
        "uses_settlements": "settlement" in text.lower(),
        "hardcoded_snapshot_dir": "SNAPSHOT_DIR" in text,
        "hardcoded_paper_orders_path": "PAPER_ORDERS_PATH" in text,
    }


def build_assessment(report: dict[str, Any]) -> dict[str, Any]:
    db_range = report["db"]["fact_signal_candidates_range"]
    paper = report["sources"]["paper_snapshots"]
    orderbook = report["sources"]["orderbook_snapshots"]
    pm_history = report["sources"]["pm_history"]
    iem = report["sources"]["iem_cache"]
    wu = report["sources"]["wu_obs"]
    return {
        "can_expand_opportunity_fact_from_local_runtime_now": False,
        "main_blocker": (
            "Local paper_snapshots/orderbook/pm_history coverage is near-window. "
            "Weather observation/model caches are longer, but they do not contain decision-time market prices."
        ),
        "fact_signal_candidates_range": db_range,
        "market_snapshot_range": {
            "paper_snapshots": {
                "files": paper["files"],
                "min_date": paper["min_date"],
                "max_date": paper["max_date"],
                "distinct_dates": paper["distinct_dates"],
            },
            "orderbook_snapshots": {
                "files": orderbook["files"],
                "min_date": orderbook["min_date"],
                "max_date": orderbook["max_date"],
                "distinct_dates": orderbook["distinct_dates"],
            },
            "pm_history": {
                "files": pm_history["files"],
                "min_date": pm_history["min_date"],
                "max_date": pm_history["max_date"],
                "distinct_dates": pm_history["distinct_dates"],
            },
        },
        "weather_cache_range": {
            "iem_filename": iem["filename_date_range"],
            "iem_sampled_content": iem["sampled_content_date_range"],
            "wu_sampled_content": wu["sampled_content_date_range"],
        },
        "minimum_viable_expansion_path": [
            "Recover or ingest historical Polymarket paper_snapshots/orderbook snapshots with decision timestamps.",
            "Ensure condition_id/market_id/bracket/event_date mapping is available for those historical markets.",
            "Ensure pm_history/settlement labels cover the same event dates.",
            "Re-run build_weather_signal_candidates.py so expanded data lands in runtime/weather.db fact_signal_candidates.",
            "Only then rerun adjacent3 matched-baseline/readiness gates.",
        ],
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def table(headers: list[str], rows_in: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows_in:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def write_md(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sources = report["sources"]
    source_rows = [
        [
            item["label"],
            f"{item.get('files', item.get('rows', 'NA'))}/{item.get('dated_files', 'NA')}",
            item.get("min_date"),
            item.get("max_date"),
            item.get("distinct_dates"),
            item.get("path"),
        ]
        for item in [
            sources["paper_snapshots"],
            sources["orderbook_snapshots"],
            sources["pm_history"],
            sources["research_files"],
        ]
    ]
    weather_rows = [
        [
            sources["iem_cache"]["label"],
            sources["iem_cache"]["files"],
            sources["iem_cache"]["filename_date_range"].get("min_date"),
            sources["iem_cache"]["filename_date_range"].get("max_date"),
            sources["iem_cache"]["sampled_content_date_range"].get("min_date"),
            sources["iem_cache"]["sampled_content_date_range"].get("max_date"),
        ],
        [
            sources["wu_obs"]["label"],
            sources["wu_obs"]["files"],
            sources["wu_obs"]["filename_date_range"].get("min_date"),
            sources["wu_obs"]["filename_date_range"].get("max_date"),
            sources["wu_obs"]["sampled_content_date_range"].get("min_date"),
            sources["wu_obs"]["sampled_content_date_range"].get("max_date"),
        ],
    ]
    lines = [
        "# Opportunity Fact Expansion Coverage Audit v0",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> git_sha: `{report['git_sha']}`",
        "> Scope: local coverage audit only; no raw-file strategy PnL; no N100/live config changed.",
        "",
        "## 数据快照",
        "",
        "- 策略结论授权源仍是 `runtime/weather.db.fact_signal_candidates` / `fact_trades`。",
        f"- fact_signal_candidates range：`{report['db']['fact_signal_candidates_range']}`。",
        f"- 主合规 opportunity 分母：`{report['db']['fact_signal_candidates_usable_main']}`。",
        "",
        "### 强制 5 行 SQL 自检",
        "",
        "```json",
        json.dumps(
            {
                "max_fact_built_at_utc": report["db"]["max_fact_built_at_utc"],
                "trade_class_distribution": report["db"]["trade_class_distribution"],
                "settlement_status_distribution": report["db"]["settlement_status_distribution"],
                "candidate_coverage": report["db"]["candidate_coverage"],
                "order_fill_coverage": report["db"]["order_fill_coverage"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "## 市场数据覆盖",
        "",
        table(["source", "files/dated", "min date", "max date", "distinct dates", "path"], source_rows),
        "",
        "## 天气缓存覆盖",
        "",
        table(
            ["source", "files", "filename min", "filename max", "sample content min", "sample content max"],
            weather_rows,
        ),
        "",
        "## Builder 输入",
        "",
        "```json",
        json.dumps(report["builder_inputs"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## 人话结论",
        "",
        "- 本机现在不能把交易 opportunity fact 合规扩成两年历史；关键缺口是历史 Polymarket decision-time snapshot/orderbook，不是天气观测。",
        "- IEM/WU/forecast cache 可以支持模型质量、校准、季节误差研究，但不能直接证明策略 ROI 或可成交 edge。",
        "- 要回答两年里 adjacent3/side-band 是否有交易 edge，必须先把历史市场价格、condition_id、盘口和结算标签重建进 `fact_signal_candidates` 同粒度表。",
        "- 因此当前不应拿“两年天气数据”批准或否定 live；现在只能说近窗 fact 下 non-all-YES 策略未过 live-test 三门。",
        "",
        "## 最小扩样路线",
        "",
        "\n".join(f"{idx + 1}. {step}" for idx, step in enumerate(report["assessment"]["minimum_viable_expansion_path"])),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)
    conn = connect(str(db_path))
    sources = {
        "paper_snapshots": summarize_files(MARKET_DATA / "paper_snapshots", "*.json", label="paper_snapshots"),
        "orderbook_snapshots": summarize_files(MARKET_DATA / "orderbook_snapshots", "*.jsonl.gz", label="orderbook_snapshots"),
        "pm_history": summarize_files(
            MARKET_DATA / "cache" / "pm_history",
            "*.json",
            label="pm_history",
            date_extractor=date_from_pm_history_city_day,
        ),
        "research_files": summarize_files(MARKET_DATA / "research", "*", label="research_files"),
        "iem_cache": summarize_weather_csv_dir(MARKET_DATA / "cache" / "iem", "*.csv", "iem_cache"),
        "wu_obs": summarize_weather_csv_dir(MARKET_DATA / "cache" / "wu_obs", "*.csv", "wu_obs"),
        "paper_orders": summarize_paper_orders(MARKET_DATA / "paper_trades" / "paper_orders.jsonl"),
    }
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha(),
        "db": summarize_db(conn, db_path),
        "sources": sources,
        "builder_inputs": builder_inputs(),
    }
    report["assessment"] = build_assessment(report)
    write_json(Path(args.out_json), report)
    write_md(Path(args.out_md), report)
    print(args.out_json)
    print(args.out_md)


if __name__ == "__main__":
    main()
