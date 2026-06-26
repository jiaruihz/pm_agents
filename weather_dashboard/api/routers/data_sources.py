"""Data-source provenance: what data we fetched, from where, when, which cities.

Two layers:
  - forecast_sources: which forecast feeds actually drove decisions (from fact_trades)
  - market_snapshots: the realtime order-book snapshots captured on disk
"""

import json
import os
import sqlite3
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from weather_dashboard.api.deps import get_db

router = APIRouter(prefix="/data-sources", tags=["data-sources"])

Db = Annotated[sqlite3.Connection, Depends(get_db)]


def _snapshots_dir() -> Path:
    return Path(os.environ.get(
        "WEATHER_SNAPSHOTS_DIR",
        "runtime/weather_edge_v1/market_data/paper_snapshots",
    ))


# Human descriptions for the canonical observation (METAR) sources kept in
# weather_data_feed/observation_sources/aliases.py. Keep in sync if sources move.
_OBS_SOURCE_DESC: dict[str, str] = {
    "aviationweather_metar": "AviationWeather.gov (AWC) 实时 METAR API",
    "aviationweather_cache_csv": "AWC METAR 缓存 CSV（批量）",
    "checkwx_html": "CheckWX METAR（HTML 抓取）",
    "iem_asos": "Iowa Env Mesonet ASOS 历史观测",
    "iem_asos_latest_raw": "IEM ASOS 最新原始 METAR",
    "iem_asos_routine_latest": "IEM ASOS routine（整点周期）最新",
    "iem_asos_madishf_latest": "IEM ASOS MADIS 高频最新",
    "ldm_metar": "LDM METAR 推送流",
    "noaa_tgftp_station_txt": "NOAA tgftp 单站 METAR txt",
    "synopticdata_timeseries": "Synoptic Data (WRH) 时间序列",
    "weather_gov_latest": "weather.gov / NWS API 最新观测",
    "weather_com_current": "weather.com 当前实况",
    "weather_com_history_hourly": "weather.com 逐小时历史",
}


def _observation_sources() -> list[dict[str, Any]]:
    try:
        from weather_data_feed.observation_sources.aliases import SOURCE_ALIASES
    except Exception:
        return []
    grouped: dict[str, list[str]] = {}
    for alias, canonical in SOURCE_ALIASES.items():
        grouped.setdefault(canonical, [])
        if alias != canonical:
            grouped[canonical].append(alias)
    out = []
    for canonical in sorted(grouped):
        out.append({
            "canonical": canonical,
            "aliases": sorted(grouped[canonical]),
            "description": _OBS_SOURCE_DESC.get(canonical, ""),
            "kind": "metar" if ("metar" in canonical or "asos" in canonical or "tgftp" in canonical or "aviation" in canonical) else "other",
        })
    return out


def _snapshot_cadence_min(files: list[Path]) -> float | None:
    """Median spacing between recent snapshot filename timestamps, in minutes."""
    import re
    from datetime import datetime
    stamps = []
    for p in files:
        m = re.search(r"snapshot_(\d{8})_(\d{4})", p.name)
        if m:
            try:
                stamps.append(datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M"))
            except ValueError:
                pass
    stamps.sort()
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(stamps, stamps[1:])]
    if not gaps:
        return None
    gaps.sort()
    return round(gaps[len(gaps) // 2], 1)


@router.get("")
def get_data_sources(db: Db, snapshots: int = Query(12, ge=1, le=100)) -> dict[str, Any]:
    # ── forecast feeds that drove decisions ──────────────────────────────
    try:
        forecast_rows = db.execute(
            """
            SELECT
                forecast_source,
                COUNT(DISTINCT city)   AS cities,
                COUNT(DISTINCT model_version) AS models,
                COUNT(*)               AS rows,
                MAX(snapshot_ts_utc)   AS latest_snapshot_ts_utc,
                MIN(target_date)       AS first_target_date,
                MAX(target_date)       AS last_target_date
            FROM fact_trades
            WHERE forecast_source IS NOT NULL
            GROUP BY forecast_source
            ORDER BY rows DESC
            """
        ).fetchall()
        forecast_sources = [dict(r) for r in forecast_rows]
    except sqlite3.OperationalError:
        forecast_sources = []

    # ── realtime order-book snapshots on disk ────────────────────────────
    market_snapshots: list[dict[str, Any]] = []
    cadence_min: float | None = None
    d = _snapshots_dir()
    if d.exists():
        files = sorted(d.glob("snapshot_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        cadence_min = _snapshot_cadence_min(files[:30])
        for p in files[:snapshots]:
            meta: dict[str, Any] = {
                "file": p.name,
                "mtime_utc": _mtime_iso(p),
                "ts_utc": None,
                "ts_beijing": None,
                "total_records": None,
                "trading_cities": None,
                "research_cities": None,
            }
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    meta["ts_utc"] = data.get("ts_utc")
                    meta["ts_beijing"] = data.get("ts_beijing")
                    meta["total_records"] = data.get("total_records")
                    t1 = data.get("trading_t1_cities")
                    t2 = data.get("research_t2_cities")
                    meta["trading_cities"] = len(t1) if isinstance(t1, list) else None
                    meta["research_cities"] = len(t2) if isinstance(t2, list) else None
            except Exception:
                pass
            market_snapshots.append(meta)

    return {
        "forecast_sources": forecast_sources,
        "observation_sources": _observation_sources(),
        "market_snapshots": market_snapshots,
        "market_snapshot_cadence_min": cadence_min,
    }


def _mtime_iso(p: Path) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()
