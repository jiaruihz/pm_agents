"""Data-source management: profiles, monitor instances, dynamic health, and legacy provenance.

Layers:
  - source_profiles: city × source configuration from DB
  - monitor_instances: running monitor tasks from DB
  - dynamic_health: computed from monitor_instance.latest_path / latest.json
  - forecast_sources: which forecast feeds drove decisions (from fact_trades, legacy compat)
  - market_snapshots: the realtime order-book snapshots on disk (legacy compat)
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
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


def _mtime_iso(p: Path) -> str:
    return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()


# ── Dynamic health from latest.json ─────────────────────────────────────────

def _compact_sample(value: Any, *, max_chars: int = 4000) -> Any:
    """Return a JSON-safe sample without turning the endpoint into a data dump."""
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return None
    if len(encoded) <= max_chars:
        return value
    return {"truncated": True, "json_prefix": encoded[:max_chars]}


def _payload_rows(data: dict[str, Any]) -> int | None:
    for key in ("rows", "candidate_rows", "latest_quote_rows"):
        value = data.get(key)
        if isinstance(value, int):
            return value
    for key in ("records", "events", "opportunities", "quote_snapshots", "latest_opportunities"):
        value = data.get(key)
        if isinstance(value, list):
            return len(value)
    return None


def _payload_sample(data: dict[str, Any]) -> Any:
    for key in ("records", "events", "opportunities", "quote_snapshots", "latest_opportunities"):
        value = data.get(key)
        if isinstance(value, list) and value:
            return _compact_sample(value[0])
    return _compact_sample({k: v for k, v in data.items() if k not in {"records", "events", "opportunities", "quote_snapshots", "latest_opportunities"}})


def _payload_cities(data: dict[str, Any]) -> list[Any]:
    for key in ("cities", "active_job_cities", "live_cities", "source_cities", "metar_cities"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _has_status(source_statuses: dict[str, Any], wanted: set[str]) -> bool:
    return any(str(status) in wanted for status in source_statuses.values())


def compute_dynamic_health(monitor_instances: list[dict]) -> list[dict[str, Any]]:
    """Compute health for each monitor instance by reading its latest_path."""
    now = datetime.now(timezone.utc)
    results = []
    for mi in monitor_instances:
        mi_id = mi["monitor_instance_id"]
        latest_path = mi.get("latest_path")
        entry: dict[str, Any] = {
            "monitor_instance_id": mi_id,
            "display_name": mi["display_name"],
            "feed_kind": mi["feed_kind"],
            "status": "unknown",
            "latest_generated_at_utc": None,
            "latest_file_mtime_utc": None,
            "age_sec": None,
            "rows": None,
            "source_statuses": {},
            "source_errors": {},
            "cities": [],
            "sources": [],
            "sample_keys": [],
            "sample_json": None,
        }

        if not latest_path:
            entry["status"] = "missing"
            results.append(entry)
            continue

        lp = Path(latest_path)
        if not lp.is_file():
            entry["status"] = "missing"
            results.append(entry)
            continue

        entry["latest_file_mtime_utc"] = _mtime_iso(lp)

        try:
            with open(lp, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            entry["status"] = "fetch_failed"
            results.append(entry)
            continue

        gen_at = data.get("generated_at_utc")
        entry["latest_generated_at_utc"] = gen_at
        entry["sample_keys"] = sorted(data.keys())[:20]
        entry["sample_json"] = _payload_sample(data)

        if gen_at:
            try:
                gen_dt = datetime.fromisoformat(str(gen_at).replace("Z", "+00:00"))
                if gen_dt.tzinfo is None:
                    gen_dt = gen_dt.replace(tzinfo=timezone.utc)
                age = (now - gen_dt).total_seconds()
                entry["age_sec"] = round(age, 1)

                scan_interval = mi.get("scan_interval_sec") or 300
                if age < scan_interval * 5:
                    entry["status"] = "fresh"
                else:
                    entry["status"] = "stale"
            except (ValueError, TypeError):
                entry["status"] = "unknown"
        else:
            entry["status"] = "unknown"

        entry["rows"] = _payload_rows(data)

        entry["cities"] = _payload_cities(data)

        if isinstance(data.get("sources"), list):
            entry["sources"] = data["sources"]

        if isinstance(data.get("source_statuses"), dict):
            entry["source_statuses"] = data["source_statuses"]
        if isinstance(data.get("source_errors"), dict):
            entry["source_errors"] = data["source_errors"]

        ok = data.get("ok_sources")
        non_ok = data.get("non_ok_sources")
        if non_ok and isinstance(non_ok, int) and non_ok > 0:
            entry["source_statuses"]["_ok"] = ok
            entry["source_statuses"]["_non_ok"] = non_ok

        auth_issues = data.get("auth_required_sources") or data.get("auth_required")
        if auth_issues:
            entry["source_statuses"]["_auth_required"] = auth_issues

        if auth_issues or _has_status(entry["source_statuses"], {"auth_required"}):
            entry["status"] = "auth_required"
        elif entry["source_errors"] or (isinstance(non_ok, int) and non_ok > 0) or _has_status(
            entry["source_statuses"],
            {"fetch_failed", "failed", "error", "not_implemented"},
        ):
            entry["status"] = "fetch_failed"

        results.append(entry)
    return results


def _query_source_profiles(db: sqlite3.Connection) -> list[dict]:
    try:
        rows = db.execute(
            """SELECT profile_id, feed_kind, city, source_key, source_kind,
                      station_or_feed, icao, runway, source_role, timezone_name,
                      expected_cadence_sec, staleness_max_age_sec, active_window_json,
                      requires_auth, auth_ref, strategy_eligible, live_eligible,
                      observed_median_lag_sec, observed_p95_lag_sec, notes,
                      updated_at_utc
               FROM weather_data_source_profile
               ORDER BY city, feed_kind, source_role"""
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def _query_monitor_instances(db: sqlite3.Connection) -> list[dict]:
    try:
        rows = db.execute(
            """SELECT monitor_instance_id, display_name, feed_kind, sources_json,
                      cities_json, scan_interval_sec, active_window_json, output_dir,
                      latest_path, journal_paths_json, state_path, proxy_policy,
                      auth_refs_json, desired_status, host, tmux_session,
                      start_command, summary_json, updated_at_utc
               FROM weather_data_monitor_instance
               ORDER BY feed_kind, monitor_instance_id"""
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


@router.get("")
def get_data_sources(db: Db, snapshots: int = Query(12, ge=1, le=100)) -> dict[str, Any]:
    # ── source profiles from DB ─────────────────────────────────────────
    source_profiles = _query_source_profiles(db)
    monitor_instances = _query_monitor_instances(db)
    dynamic_health = compute_dynamic_health(monitor_instances)

    # ── forecast feeds that drove decisions (legacy compat) ─────────────
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

    # ── realtime order-book snapshots on disk (legacy compat) ───────────
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
                file_data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(file_data, dict):
                    meta["ts_utc"] = file_data.get("ts_utc")
                    meta["ts_beijing"] = file_data.get("ts_beijing")
                    meta["total_records"] = file_data.get("total_records")
                    t1 = file_data.get("trading_t1_cities")
                    t2 = file_data.get("research_t2_cities")
                    meta["trading_cities"] = len(t1) if isinstance(t1, list) else None
                    meta["research_cities"] = len(t2) if isinstance(t2, list) else None
            except Exception:
                pass
            market_snapshots.append(meta)

    return {
        "source_profiles": source_profiles,
        "monitor_instances": monitor_instances,
        "dynamic_health": dynamic_health,
        "forecast_sources": forecast_sources,
        "observation_sources": _observation_sources(),
        "market_snapshots": market_snapshots,
        "market_snapshot_cadence_min": cadence_min,
    }
