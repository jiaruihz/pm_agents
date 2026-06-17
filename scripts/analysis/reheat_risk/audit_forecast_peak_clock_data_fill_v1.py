#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "runtime/weather.db"
CACHE_ROOT = ROOT / "runtime/weather_edge_v1/market_data/cache"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-17-forecast-peak-clock-data-fill-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-17-forecast-peak-clock-data-fill-v1.md"
FEATURE_FACTORY_JSON = ROOT / "docs/analysis/2026-06/2026-06-16-reheat-feature-factory-v1.json"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def query_rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def cache_summary() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for source, folder_name in {"gfs": "gfs_v4", "ecmwf": "ecmwf_v4"}.items():
        folder = CACHE_ROOT / folder_name
        files = sorted(folder.glob(f"{folder_name}_*.json")) if folder.exists() else []
        ranges: list[tuple[str, str, str]] = []
        for path in files:
            try:
                payload = json.loads(path.read_text())
            except Exception:
                continue
            times = payload.get("hourly", {}).get("time") or []
            if not times:
                continue
            ranges.append((str(times[0])[:10], str(times[-1])[:10], path.name))
        out[source] = {
            "folder": str(folder.relative_to(ROOT)),
            "files": len(files),
            "hourly_files": len(ranges),
            "min_date": min((r[0] for r in ranges), default=None),
            "max_date": max((r[1] for r in ranges), default=None),
            "latest_files": [
                {"start": start, "end": end, "file": name}
                for start, end, name in sorted(ranges, key=lambda x: x[1])[-8:]
            ],
        }
    return out


def feature_factory_peak_coverage() -> dict[str, Any]:
    if not FEATURE_FACTORY_JSON.exists():
        return {"exists": False}
    data = json.loads(FEATURE_FACTORY_JSON.read_text())
    return {
        "exists": True,
        "generated_at_utc": data.get("generated_at_utc"),
        "feature_rows": data.get("funnel", {}).get("feature_rows"),
        "state_rows": data.get("funnel", {}).get("state_rows_date_city_hour"),
        "forecast_peak_hour_feature_row_rate": data.get("forecast_gap", {}).get("forecast_peak_hour_feature_row_rate"),
        "forecast_values_hash_feature_row_rate": data.get("forecast_gap", {}).get("forecast_values_hash_feature_row_rate"),
    }


def build_report() -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    try:
        self_check = {
            "fact_trades_max_built_at_utc": query_rows(conn, "SELECT MAX(fact_built_at_utc) AS value FROM fact_trades")[0]["value"],
            "fact_trades_by_class": query_rows(conn, "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class"),
            "fact_trades_by_settlement_status": query_rows(conn, "SELECT settlement_status, COUNT(*) AS rows FROM fact_trades GROUP BY settlement_status"),
            "fact_signal_candidate_coverage": query_rows(
                conn,
                "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, SUM(live_filled) AS live_filled FROM fact_signal_candidates",
            )[0],
            "clob_order_fill_join": query_rows(
                conn,
                "SELECT o.status, COUNT(*) AS orders, SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
                "FROM orders o LEFT JOIN fills f USING(execution_id) WHERE o.venue='polymarket_clob' GROUP BY o.status",
            ),
        }
        candidate_overall = query_rows(
            conn,
            "SELECT COUNT(*) AS rows, "
            "SUM(forecast_peak_hour_local IS NOT NULL) AS with_peak_hour, "
            "SUM(forecast_values_hash IS NOT NULL) AS with_hash, "
            "SUM(forecast_max_f IS NOT NULL) AS with_forecast_max_f, "
            "MIN(event_date) AS min_event_date, MAX(event_date) AS max_event_date "
            "FROM fact_signal_candidates",
        )[0]
        by_date = query_rows(
            conn,
            "SELECT event_date, COUNT(*) AS rows, "
            "SUM(forecast_peak_hour_local IS NOT NULL) AS with_peak_hour, "
            "SUM(forecast_values_hash IS NOT NULL) AS with_hash "
            "FROM fact_signal_candidates GROUP BY event_date "
            "HAVING with_peak_hour > 0 ORDER BY event_date",
        )
        by_source = query_rows(
            conn,
            "SELECT forecast_source, COUNT(*) AS rows, "
            "SUM(forecast_peak_hour_local IS NOT NULL) AS with_peak_hour, "
            "SUM(forecast_values_hash IS NOT NULL) AS with_hash "
            "FROM fact_signal_candidates GROUP BY forecast_source ORDER BY forecast_source",
        )
        examples = query_rows(
            conn,
            "SELECT city, event_date, forecast_source, forecast_peak_hour_local, "
            "forecast_peak_time_local, forecast_peak_hour_utc, forecast_values_hash "
            "FROM fact_signal_candidates WHERE forecast_peak_hour_local IS NOT NULL "
            "ORDER BY event_date, city LIMIT 12",
        )
    finally:
        conn.close()

    rows = int(candidate_overall["rows"] or 0)
    with_peak = int(candidate_overall["with_peak_hour"] or 0)
    candidate_overall["peak_hour_coverage"] = with_peak / rows if rows else None
    return {
        "generated_at_utc": now_utc(),
        "target_metric": "forecast_peak_clock_data_fill_v1",
        "evidence_layer": "fact_signal_candidates enrichment and forecast cache coverage audit; not live fills",
        "self_check": self_check,
        "candidate_overall": candidate_overall,
        "candidate_by_date_with_peak": by_date,
        "candidate_by_source": by_source,
        "examples": examples,
        "cache_summary": cache_summary(),
        "feature_factory": feature_factory_peak_coverage(),
        "verdict": {
            "code_path": "fact_signal_candidates builder now derives forecast_peak_* fields from mirrored hourly cache when available",
            "current_coverage": "low_sample",
            "blocking_gap": "N100 weather-predict hourly forecast cache and paper snapshots do not yet cover the 2026-05-19..06-14 reheat replay window with peak/hash fields",
            "next_action": "deploy/enable weather-predict forecast peak clock snapshot producer, sync fresh snapshots, then rebuild fact_signal_candidates and reheat feature factory",
        },
    }


def write_markdown(report: dict[str, Any]) -> None:
    overall = report["candidate_overall"]
    rows = int(overall["rows"] or 0)
    with_peak = int(overall["with_peak_hour"] or 0)
    pct = with_peak / rows * 100 if rows else 0.0
    ff = report["feature_factory"]
    lines = [
        "# Forecast Peak Clock Data Fill v1",
        "",
        f"Generated: `{report['generated_at_utc']}`",
        "",
        "## Human Verdict",
        "",
        "这次已经把 `pm_agent` 的 fact builder 补上了：当 snapshot 里没有 `forecast_peak_*` / `forecast_values_hash` 时，builder 会从镜像 hourly forecast cache 派生这些字段。",
        "",
        f"当前本地 `fact_signal_candidates` 只有 `{with_peak}` / `{rows}` 行有 peak/hash，覆盖率 `{pct:.1f}%`。这些行只落在 `2026-05-06` 和 `2026-05-07`，和 reheat factory 的 `2026-05-19..2026-06-14` 主窗口没有交集，所以 current-YES forecast-peak-clock 仍不能正式回测。",
        "",
        "真正的下一步在 upstream：N100 `weather-predict` 现在产出的 latest paper snapshots 仍是 `v2_cross_section`，只有 `forecast_max_f`，没有 peak/hash。需要让生产 snapshot producer 写出 v3 forecast peak fields，然后同步回本机重建。",
        "",
        "## Mandatory SQL Self-Check",
        "",
        "```json",
        json.dumps(report["self_check"], indent=2, ensure_ascii=False),
        "```",
        "",
        "## Candidate Coverage",
        "",
        "| scope | rows | with peak hour | with hash |",
        "|---|---:|---:|---:|",
        f"| all fact_signal_candidates | {rows} | {with_peak} | {int(overall['with_hash'] or 0)} |",
        "",
        "### By Source",
        "",
        "| forecast_source | rows | with peak hour | with hash |",
        "|---|---:|---:|---:|",
    ]
    for row in report["candidate_by_source"]:
        lines.append(
            f"| `{row['forecast_source']}` | {int(row['rows'] or 0)} | "
            f"{int(row['with_peak_hour'] or 0)} | {int(row['with_hash'] or 0)} |"
        )
    lines.extend([
        "",
        "### Dates With Peak Fields",
        "",
        "| event_date | rows | with peak hour | with hash |",
        "|---|---:|---:|---:|",
    ])
    for row in report["candidate_by_date_with_peak"]:
        lines.append(
            f"| `{row['event_date']}` | {int(row['rows'] or 0)} | "
            f"{int(row['with_peak_hour'] or 0)} | {int(row['with_hash'] or 0)} |"
        )
    lines.extend([
        "",
        "## Cache Coverage",
        "",
        "| source | files | hourly files | date range |",
        "|---|---:|---:|---|",
    ])
    for source, row in report["cache_summary"].items():
        lines.append(
            f"| `{source}` | {row['files']} | {row['hourly_files']} | "
            f"{row['min_date']}..{row['max_date']} |"
        )
    lines.extend([
        "",
        "## Reheat Factory Impact",
        "",
        f"- feature factory exists: `{ff.get('exists')}`",
        f"- feature rows: `{ff.get('feature_rows')}`",
        f"- state rows: `{ff.get('state_rows')}`",
        f"- forecast peak feature-row rate: `{ff.get('forecast_peak_hour_feature_row_rate')}`",
        f"- forecast hash feature-row rate: `{ff.get('forecast_values_hash_feature_row_rate')}`",
        "",
        "## Next Actions",
        "",
        "1. Deploy or enable the `weather-predict` snapshot producer version that emits `forecast_peak_hour_local`, `forecast_peak_time_local`, `forecast_peak_hour_utc`, `forecast_values_hash`, and `forecast_peak_delta_hours_local`.",
        "2. Sync fresh N100 snapshots back into `pm_agent`.",
        "3. Rebuild `fact_signal_candidates`; the new builder will automatically preserve snapshot peak fields or derive them from hourly cache.",
        "4. Re-run `reheat_feature_factory_v1`, then re-run current-YES peak-clock research.",
        "",
    ])
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    report = build_report()
    OUT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(report)
    print(json.dumps({"out_json": str(OUT_JSON), "out_md": str(OUT_MD)}, indent=2))


if __name__ == "__main__":
    main()
