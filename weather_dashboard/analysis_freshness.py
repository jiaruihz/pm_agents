"""Read-only freshness checks for canonical weather analysis inputs.

This module deliberately does not inspect strategy processes. Production
runtime health and analysis materialization freshness have different owners,
cadences, and remediation paths.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from weather_clock_contract import parse_utc_or_none


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z") if dt else None


def _parse_dt(value: Any) -> datetime | None:
    return parse_utc_or_none(value, field="analysis_freshness_timestamp")


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _alert(kind: str, message: str, detail: dict[str, Any]) -> dict[str, Any]:
    key = hashlib.sha1(kind.encode("utf-8")).hexdigest()[:20]
    return {
        "alert_key": key,
        "severity": "warning",
        "strategy_instance": "weather_analysis_freshness",
        "kind": kind,
        "message": message,
        "detail": detail,
    }


def db_freshness(
    db_path: Path,
    now: datetime,
    *,
    candidate_max_age_min: float = 180.0,
    settlement_max_lag_days: int = 1,
) -> dict[str, Any]:
    result: dict[str, Any] = {"db_path": str(db_path), "status": "healthy", "alerts": []}
    if not db_path.exists():
        result["alerts"].append(_alert("db_missing", "runtime/weather.db is missing", {"db_path": str(db_path)}))
        result["status"] = "warning"
        return result
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=1000")
        candidate = conn.execute(
            """
            SELECT COUNT(*) AS rows, MIN(event_date) AS min_event_date,
                   MAX(event_date) AS max_event_date,
                   MAX(decision_snapshot_ts_utc) AS max_snapshot_ts_utc
            FROM fact_signal_candidates
            """
        ).fetchone()
        settlement = conn.execute(
            """
            SELECT COUNT(*) AS rows, MIN(target_date) AS min_target_date,
                   MAX(target_date) AS max_target_date,
                   COUNT(DISTINCT city || '|' || target_date) AS city_days
            FROM settlement_outcomes
            """
        ).fetchone()
    except Exception as exc:  # noqa: BLE001
        result["alerts"].append(
            _alert("db_query_failed", f"weather.db freshness query failed: {type(exc).__name__}", {"error": str(exc)})
        )
        result["status"] = "warning"
        return result
    finally:
        if "conn" in locals():
            conn.close()

    max_snapshot = _parse_dt(candidate["max_snapshot_ts_utc"] if candidate else None)
    candidate_age = (now - max_snapshot).total_seconds() / 60 if max_snapshot else None
    settlement_date = _parse_date(settlement["max_target_date"] if settlement else None)
    settlement_lag = (now.date() - settlement_date).days if settlement_date else None
    result.update(
        {
            "fact_signal_candidates_rows": int(candidate["rows"] or 0) if candidate else 0,
            "min_event_date": candidate["min_event_date"] if candidate else None,
            "max_event_date": candidate["max_event_date"] if candidate else None,
            "max_decision_snapshot_ts_utc": _iso(max_snapshot),
            "decision_snapshot_age_min": candidate_age,
            "settlement_outcomes_rows": int(settlement["rows"] or 0) if settlement else 0,
            "settlement_outcomes_city_days": int(settlement["city_days"] or 0) if settlement else 0,
            "settlement_outcomes_min_target_date": settlement["min_target_date"] if settlement else None,
            "settlement_outcomes_max_target_date": settlement["max_target_date"] if settlement else None,
            "settlement_outcomes_lag_days": settlement_lag,
        }
    )
    if candidate_age is None or candidate_age > candidate_max_age_min:
        result["alerts"].append(
            _alert(
                "fact_signal_candidates_stale",
                "fact_signal_candidates decision snapshots are stale",
                {"age_min": candidate_age, "max_snapshot_ts_utc": _iso(max_snapshot)},
            )
        )
    if settlement_lag is None or settlement_lag > settlement_max_lag_days:
        result["alerts"].append(
            _alert(
                "settlement_outcomes_stale",
                "settlement_outcomes are stale",
                {"lag_days": settlement_lag, "max_target_date": settlement_date.isoformat() if settlement_date else None},
            )
        )
    if result["alerts"]:
        result["status"] = "warning"
    return result


def orderbook_freshness(
    orderbook_dir: Path,
    now: datetime,
    *,
    max_lag_days: int = 1,
) -> dict[str, Any]:
    result: dict[str, Any] = {"orderbook_dir": str(orderbook_dir), "status": "healthy", "alerts": []}
    if not orderbook_dir.exists():
        result["alerts"].append(
            _alert("orderbook_snapshot_dir_missing", "full orderbook snapshot directory is missing", {"orderbook_dir": str(orderbook_dir)})
        )
        result["status"] = "warning"
        return result

    dates: dict[str, int] = {}
    for day_dir in sorted(orderbook_dir.iterdir()):
        day = _parse_date(day_dir.name) if day_dir.is_dir() else None
        if day is None:
            continue
        count = sum(1 for _ in day_dir.glob("*.jsonl.gz"))
        if count:
            dates[day.isoformat()] = count
    latest = _parse_date(max(dates)) if dates else None
    lag = (now.date() - latest).days if latest else None
    result.update(
        {
            "latest_date": latest.isoformat() if latest else None,
            "latest_date_file_count": dates.get(latest.isoformat(), 0) if latest else 0,
            "lag_days": lag,
            "recent_date_counts": dict(sorted(dates.items())[-7:]),
        }
    )
    if lag is None or lag > max_lag_days:
        result["alerts"].append(
            _alert(
                "full_orderbook_snapshots_stale",
                "full orderbook snapshots are stale",
                {"lag_days": lag, "latest_date": latest.isoformat() if latest else None},
            )
        )
        result["status"] = "warning"
    return result


def summary(db_path: Path, orderbook_dir: Path, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    db = db_freshness(db_path, now)
    orderbook = orderbook_freshness(orderbook_dir, now)
    alerts = [*db["alerts"], *orderbook["alerts"]]
    return {
        "generated_at_utc": _iso(now),
        "monitor": "weather_analysis_freshness",
        "status": "warning" if alerts else "healthy",
        "warning_alerts": len(alerts),
        "db": db,
        "orderbook": orderbook,
        "alerts": alerts,
        "remediation": "scripts/ops/refresh_weather_analysis_incremental.sh",
    }
