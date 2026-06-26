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
    d = _snapshots_dir()
    if d.exists():
        files = sorted(d.glob("snapshot_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
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

    return {"forecast_sources": forecast_sources, "market_snapshots": market_snapshots}


def _mtime_iso(p: Path) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()
