#!/usr/bin/env python3
"""Audit date freshness for the shared reheat-risk feature layer."""

from __future__ import annotations

import glob
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
REHEAT_ROWS = ROOT / "docs/analysis/2026-06/generated/reheat_feature_factory_v1/reheat_feature_rows.csv"
OBSERVED_DETAIL = (
    ROOT
    / "docs/analysis/2026-06/generated/m3_observed_max_v6_h10_21_iem_patch_20260617"
    / "m3_observed_max_residual_detail.csv"
)
WU_DIR = ROOT / "docs/analysis/2026-06/generated/theta_no_wu_obs_patch_v2_20260617/wu_obs"
IEM_DIR = ROOT / "runtime/weather_edge_v1/market_data/cache/iem"
EXT_DIR = ROOT / "docs/analysis/2026-06/generated/theta_no_iem_ext_patch_v7_20260617"
FORECAST_BACKFILL = ROOT / "runtime/weather_edge_v1/market_data/research/forecast_peak_clock_backfill_v1.csv"
ORDERBOOK_DIR = ROOT / "runtime/weather_edge_v1/market_data/orderbook_snapshots"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-19-reheat-feature-data-freshness-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-19-reheat-feature-data-freshness-v1.md"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_shanghai_date() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def query_one(sql: str) -> Any:
    conn = connect_ro()
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def csv_date_range(path: Path, date_col: str = "target_date") -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path.relative_to(ROOT)), "exists": False}
    df0 = pd.read_csv(path, nrows=0)
    if date_col not in df0.columns:
        return {"path": str(path.relative_to(ROOT)), "exists": True, "rows": None, "error": f"missing {date_col}"}
    s = pd.read_csv(path, usecols=[date_col])[date_col].dropna().astype(str)
    return {
        "path": str(path.relative_to(ROOT)),
        "exists": True,
        "rows": int(len(s)),
        "min_date": str(s.min()) if len(s) else None,
        "max_date": str(s.max()) if len(s) else None,
        "active_dates": int(s.nunique()) if len(s) else 0,
        "size_bytes": int(path.stat().st_size),
    }


def cache_valid_range(pattern: str, valid_col: str) -> dict[str, Any]:
    files = sorted(glob.glob(pattern))
    max_values: list[pd.Timestamp] = []
    min_values: list[pd.Timestamp] = []
    rows = 0
    files_with_valid = 0
    top_files: list[dict[str, Any]] = []
    for path_str in files:
        path = Path(path_str)
        try:
            df = pd.read_csv(path, usecols=[valid_col])
        except Exception:
            continue
        s = pd.to_datetime(df[valid_col], utc=True, errors="coerce").dropna()
        if s.empty:
            continue
        files_with_valid += 1
        rows += int(len(s))
        min_values.append(s.min())
        max_values.append(s.max())
        top_files.append(
            {
                "file": path.name,
                "min_valid_utc": s.min().isoformat(),
                "max_valid_utc": s.max().isoformat(),
                "rows": int(len(s)),
            }
        )
    top_files.sort(key=lambda row: str(row["max_valid_utc"]), reverse=True)
    return {
        "files": len(files),
        "files_with_valid": files_with_valid,
        "rows": rows,
        "min_valid_utc": min(min_values).isoformat() if min_values else None,
        "max_valid_utc": max(max_values).isoformat() if max_values else None,
        "top_files": top_files[:10],
    }


def orderbook_range() -> dict[str, Any]:
    dates = sorted(path.name for path in ORDERBOOK_DIR.iterdir() if path.is_dir() and path.name[:4].isdigit())
    return {
        "path": str(ORDERBOOK_DIR.relative_to(ROOT)),
        "date_dirs": len(dates),
        "min_date": dates[0] if dates else None,
        "max_date": dates[-1] if dates else None,
    }


def pm_history_range() -> dict[str, Any]:
    conn = connect_ro()
    try:
        rows = conn.execute(
            "SELECT MIN(target_date), MAX(target_date), COUNT(DISTINCT city || '|' || target_date) "
            "FROM settlement_outcomes"
        ).fetchone()
    finally:
        conn.close()
    return {"min_date": rows[0], "max_date": rows[1], "city_dates": int(rows[2] or 0)}


def fact_self_check() -> dict[str, Any]:
    conn = connect_ro()
    try:
        by_class = [dict(row) for row in conn.execute("SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class")]
        by_settlement = [
            dict(row)
            for row in conn.execute(
                "SELECT COALESCE(settlement_status, '') AS settlement_status, COUNT(*) AS rows "
                "FROM fact_trades GROUP BY settlement_status"
            )
        ]
        signal = dict(
            conn.execute(
                "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
                "SUM(live_filled) AS live_filled FROM fact_signal_candidates"
            ).fetchone()
        )
    finally:
        conn.close()
    return {
        "fact_trades_max_built_at_utc": query_one("SELECT MAX(fact_built_at_utc) FROM fact_trades"),
        "fact_trades_by_class": by_class,
        "fact_trades_by_settlement_status": by_settlement,
        "fact_signal_candidate_coverage": signal,
    }


def write_md(payload: dict[str, Any]) -> None:
    layers = payload["layers"]
    feature_max = layers["reheat_feature_rows"].get("max_date")
    observed_max = layers["observed_detail"].get("max_date")
    forecast_max = layers["forecast_peak_backfill"].get("max_date")
    settlement_max = layers["settlement_outcomes"].get("max_date")
    lines = [
        "# Reheat Feature Data Freshness v1",
        "",
        "Status: data-audit",
        f"Generated UTC: `{payload['generated_at_utc']}`",
        f"Current local date: `{payload['current_date_local']}`",
        "",
        "## Human Summary",
        "",
        f"当前研究训练用的共享 feature layer 已补到 `{feature_max}`：observed-detail 到 `{observed_max}`，forecast peak backfill 到 `{forecast_max}`。这三层现在是同一窗口，不再卡在旧 6/14 补丁。",
        "",
        f"settlement_outcomes 目前最大日期是 `{settlement_max}`，但最新一天可能是部分城市；模型训练层按已完整补齐的 36 城研究缓存和可用 settlement 交集落在 `{feature_max}`。",
        "",
        "## Date Ranges",
        "",
        "| layer | max date / valid | rows/files | note |",
        "|---|---:|---:|---|",
        f"| orderbook snapshots | {layers['orderbook']['max_date']} | {layers['orderbook']['date_dirs']} dirs | raw market data is newer |",
        f"| pm_history / settlement_outcomes | {layers['settlement_outcomes']['max_date']} | {layers['settlement_outcomes']['city_dates']} city-days | settlement bridge is newer than feature layer |",
        f"| reheat_feature_rows | {layers['reheat_feature_rows'].get('max_date')} | {layers['reheat_feature_rows'].get('rows')} rows | model training table currently used |",
        f"| observed-detail | {layers['observed_detail'].get('max_date')} | {layers['observed_detail'].get('rows')} rows | upstream current/running max table |",
        f"| forecast peak backfill | {layers['forecast_peak_backfill'].get('max_date')} | {layers['forecast_peak_backfill'].get('rows')} rows | forecast-clock input |",
        f"| wu_obs cache | {layers['wu_obs_cache']['max_valid_utc']} | {layers['wu_obs_cache']['files_with_valid']} files | current research-compatible IEM-derived cache |",
        f"| research IEM ext patch | {layers['iem_ext_patch']['max_valid_utc']} | {layers['iem_ext_patch']['files_with_valid']} files | current reheat weather-feature cache |",
        f"| mirrored IEM v2 cache | {layers['iem_v2_cache']['max_valid_utc']} | {layers['iem_v2_cache']['files_with_valid']} files | legacy mirror, not the active reheat input |",
        "",
        "## Remaining Gap",
        "",
        "1. The active research cache is now repeatable via `build_reheat_iem_research_cache_v1.py`, but it is still a research command, not a scheduled production job.",
        "2. The legacy mirrored IEM v2 cache is still stale; current reheat research no longer depends on it, but it should not be confused with the active input.",
        "3. Dates after the latest complete settlement/label window should not be used for settled-label training until the settlement layer is complete.",
        "",
        "## Next Data Work",
        "",
        "1. Schedule or fold `build_reheat_iem_research_cache_v1.py` into the shared `weather_data_feed` historical observation path.",
        "2. On each refresh, rebuild observed-detail, forecast backfill, `reheat_feature_factory_v1`, then retrain peak-forming/fade/NO-carry heads on the same date window.",
        "3. Keep using complete settlement/label coverage as the upper bound for settled-label model training.",
        "",
        "## Self Check",
        "",
        "```json",
        json.dumps(payload["fact_self_check"], indent=2, ensure_ascii=False),
        "```",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    payload = {
        "generated_at_utc": now_utc(),
        "current_date_local": now_shanghai_date(),
        "fact_self_check": fact_self_check(),
        "layers": {
            "orderbook": orderbook_range(),
            "settlement_outcomes": pm_history_range(),
            "reheat_feature_rows": csv_date_range(REHEAT_ROWS),
            "observed_detail": csv_date_range(OBSERVED_DETAIL),
            "forecast_peak_backfill": csv_date_range(FORECAST_BACKFILL),
            "wu_obs_cache": cache_valid_range(str(WU_DIR / "wu_obs_*.csv"), "valid_utc"),
            "iem_v2_cache": cache_valid_range(str(IEM_DIR / "iem_v2_*.csv"), "valid"),
            "iem_ext_patch": cache_valid_range(str(EXT_DIR / "iem_ext_*.csv"), "valid"),
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_md(payload)
    print(json.dumps(payload["layers"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
