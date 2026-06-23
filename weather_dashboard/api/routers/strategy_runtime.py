"""Strategy runtime registry endpoints for live/shadow operational status."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from weather_dashboard.api.deps import get_db

router = APIRouter(prefix="/strategy-runtime", tags=["strategy-runtime"])

Db = Annotated[sqlite3.Connection, Depends(get_db)]


def _json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _record_target_date(record: dict[str, Any]) -> str | None:
    for key in ("target_date", "event_date", "local_date", "decision_target_date"):
        value = record.get(key)
        if value:
            return str(value)[:10]
    return None


def _tomorrow() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


def _target_status(row: sqlite3.Row, sample_dates: set[str], target_date: str) -> str:
    health = row["health_status"]
    lifecycle = row["lifecycle_status"]
    if health in {"blocked", "stale", "shelved"}:
        return health
    if target_date in sample_dates:
        return "target_seen"
    if lifecycle == "monitor":
        return "monitoring"
    if lifecycle == "telemetry":
        return "collecting"
    if lifecycle == "live":
        return "live_waiting"
    if lifecycle == "shadow":
        return "shadow_waiting"
    return "unknown"


@router.get("/overview")
def get_strategy_runtime_overview(
    db: Db,
    target_date: str = Query(default_factory=_tomorrow, pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> dict[str, Any]:
    """Return operational registry rows plus target-date hints for the dashboard."""

    registry_rows = db.execute(
        """
        SELECT *
        FROM weather_strategy_runtime_registry
        ORDER BY
          CASE lifecycle_status
            WHEN 'live' THEN 1
            WHEN 'shadow' THEN 2
            WHEN 'telemetry' THEN 3
            WHEN 'monitor' THEN 4
            WHEN 'blocked' THEN 8
            WHEN 'stale' THEN 9
            ELSE 6
          END,
          family,
          strategy_instance
        """
    ).fetchall()

    artifact_rows = db.execute(
        """
        SELECT
          strategy_instance,
          artifact_kind,
          source_path,
          row_count,
          size_bytes,
          latest_record_ts_utc,
          sample_json,
          refreshed_at_utc
        FROM weather_strategy_runtime_artifacts
        ORDER BY strategy_instance, artifact_kind
        """
    ).fetchall()

    artifacts_by_strategy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sample_dates_by_strategy: dict[str, set[str]] = defaultdict(set)
    cities_by_strategy: dict[str, set[str]] = defaultdict(set)
    latest_sample_date_by_strategy: dict[str, str] = {}

    for artifact in artifact_rows:
        record = _json_dict(artifact["sample_json"])
        sample_date = _record_target_date(record)
        city = record.get("city")
        if sample_date:
            sample_dates_by_strategy[artifact["strategy_instance"]].add(sample_date)
            latest_sample_date_by_strategy[artifact["strategy_instance"]] = max(
                latest_sample_date_by_strategy.get(artifact["strategy_instance"], ""),
                sample_date,
            )
        if city:
            cities_by_strategy[artifact["strategy_instance"]].add(str(city))
        artifacts_by_strategy[artifact["strategy_instance"]].append(
            {
                "artifact_kind": artifact["artifact_kind"],
                "source_path": artifact["source_path"],
                "row_count": artifact["row_count"] or 0,
                "size_bytes": artifact["size_bytes"] or 0,
                "latest_record_ts_utc": artifact["latest_record_ts_utc"],
                "sample_target_date": sample_date,
                "sample_city": city,
                "target_date_match": sample_date == target_date,
                "refreshed_at_utc": artifact["refreshed_at_utc"],
            }
        )

    strategies: list[dict[str, Any]] = []
    for row in registry_rows:
        strategy_instance = row["strategy_instance"]
        sample_dates = sample_dates_by_strategy[strategy_instance]
        row_dict = dict(row)
        row_dict["blockers"] = _json_list(row["blockers_json"])
        row_dict["summary"] = _json_dict(row["summary_json"])
        row_dict["latest_sample_target_date"] = latest_sample_date_by_strategy.get(strategy_instance)
        row_dict["sample_target_dates"] = sorted(sample_dates)
        row_dict["sample_cities"] = sorted(cities_by_strategy[strategy_instance])
        row_dict["target_status"] = _target_status(row, sample_dates, target_date)
        row_dict["target_artifacts"] = [
            artifact for artifact in artifacts_by_strategy[strategy_instance]
            if artifact["target_date_match"]
        ]
        row_dict["artifacts"] = artifacts_by_strategy[strategy_instance]
        strategies.append(row_dict)

    queue_rows = db.execute(
        """
        SELECT *
        FROM weather_strategy_shadow_queue
        ORDER BY
          CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
          CASE status WHEN 'active' THEN 1 WHEN 'runner_ready' THEN 2 WHEN 'proposed' THEN 3 WHEN 'blocked' THEN 8 ELSE 9 END,
          shadow_id
        """
    ).fetchall()
    shadow_queue = []
    for row in queue_rows:
        item = dict(row)
        item["required_fields"] = _json_list(row["required_fields_json"])
        item["blockers"] = _json_list(row["blockers_json"])
        shadow_queue.append(item)

    lifecycle_counts = Counter(row["lifecycle_status"] for row in registry_rows)
    health_counts = Counter(row["health_status"] for row in registry_rows)
    target_counts = Counter(item["target_status"] for item in strategies)

    return {
        "target_date": target_date,
        "refreshed_at_utc": max(
            [row["refreshed_at_utc"] for row in registry_rows if row["refreshed_at_utc"]],
            default=None,
        ),
        "summary": {
            "total_strategies": len(strategies),
            "live_strategies": lifecycle_counts["live"],
            "shadow_strategies": lifecycle_counts["shadow"],
            "telemetry_strategies": lifecycle_counts["telemetry"],
            "healthy_strategies": health_counts["healthy"],
            "blocked_strategies": health_counts["blocked"],
            "stale_strategies": health_counts["stale"],
            "target_seen_strategies": target_counts["target_seen"],
            "shadow_queue_items": len(shadow_queue),
            "shadow_queue_high": sum(1 for item in shadow_queue if item["priority"] == "high"),
        },
        "strategies": strategies,
        "shadow_queue": shadow_queue,
    }
