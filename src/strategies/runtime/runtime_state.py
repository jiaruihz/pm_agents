"""Runtime-state write path for weather strategy instances.

`strategy_instance` is the desired/control state. `strategy_instance_runtime`
is the actual state pushed by a runner or by the local supervisor reconcile
loop. Dashboard runtime views should read this table, not infer status by
scanning files directly.
"""

from __future__ import annotations

import json
import socket
import sqlite3
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def json_text(value: Any, default: str = "{}") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


RUNTIME_COLUMNS = [
    "instance_id",
    "process_status",
    "pid",
    "supervisor_id",
    "heartbeat_at_utc",
    "last_tick_ts_utc",
    "last_data_ts_utc",
    "latest_summary_ts_utc",
    "latest_artifact_mtime_utc",
    "heartbeat_age_min",
    "candidate_rows",
    "plan_rows",
    "live_order_rows",
    "paper_order_rows",
    "shadow_rows",
    "telemetry_rows",
    "fact_trade_rows",
    "fact_live_real_rows",
    "fact_cost_usd",
    "first_target_date",
    "last_target_date",
    "latest_fill_ts_utc",
    "health_status",
    "live_enabled",
    "summary_path",
    "primary_journal_path",
    "blocker_count",
    "blockers_json",
    "summary_json",
    "refreshed_at_utc",
]


def push_runtime_state(
    conn: sqlite3.Connection,
    *,
    instance_id: str,
    process_status: str = "unknown",
    health_status: str = "unknown",
    pid: int | None = None,
    supervisor_id: str | None = None,
    heartbeat_at_utc: str | None = None,
    last_tick_ts_utc: str | None = None,
    last_data_ts_utc: str | None = None,
    latest_summary_ts_utc: str | None = None,
    latest_artifact_mtime_utc: str | None = None,
    heartbeat_age_min: float | None = None,
    candidate_rows: int = 0,
    plan_rows: int = 0,
    live_order_rows: int = 0,
    paper_order_rows: int = 0,
    shadow_rows: int = 0,
    telemetry_rows: int = 0,
    fact_trade_rows: int = 0,
    fact_live_real_rows: int = 0,
    fact_cost_usd: float | None = None,
    first_target_date: str | None = None,
    last_target_date: str | None = None,
    latest_fill_ts_utc: str | None = None,
    live_enabled: int | None = None,
    summary_path: str | None = None,
    primary_journal_path: str | None = None,
    blocker_count: int = 0,
    blockers_json: Any = None,
    summary_json: Any = None,
    refreshed_at_utc: str | None = None,
) -> None:
    """Upsert one actual-state row."""
    now = refreshed_at_utc or utc_now()
    values = {
        "instance_id": instance_id,
        "process_status": process_status,
        "pid": pid,
        "supervisor_id": supervisor_id or socket.gethostname(),
        "heartbeat_at_utc": heartbeat_at_utc or now,
        "last_tick_ts_utc": last_tick_ts_utc,
        "last_data_ts_utc": last_data_ts_utc,
        "latest_summary_ts_utc": latest_summary_ts_utc,
        "latest_artifact_mtime_utc": latest_artifact_mtime_utc,
        "heartbeat_age_min": heartbeat_age_min,
        "candidate_rows": int(candidate_rows or 0),
        "plan_rows": int(plan_rows or 0),
        "live_order_rows": int(live_order_rows or 0),
        "paper_order_rows": int(paper_order_rows or 0),
        "shadow_rows": int(shadow_rows or 0),
        "telemetry_rows": int(telemetry_rows or 0),
        "fact_trade_rows": int(fact_trade_rows or 0),
        "fact_live_real_rows": int(fact_live_real_rows or 0),
        "fact_cost_usd": fact_cost_usd,
        "first_target_date": first_target_date,
        "last_target_date": last_target_date,
        "latest_fill_ts_utc": latest_fill_ts_utc,
        "health_status": health_status,
        "live_enabled": live_enabled,
        "summary_path": summary_path,
        "primary_journal_path": primary_journal_path,
        "blocker_count": int(blocker_count or 0),
        "blockers_json": json_text(blockers_json, "[]"),
        "summary_json": json_text(summary_json, "{}"),
        "refreshed_at_utc": now,
    }
    placeholders = ", ".join("?" for _ in RUNTIME_COLUMNS)
    update_set = ", ".join(
        f"{col}=excluded.{col}" for col in RUNTIME_COLUMNS if col != "instance_id"
    )
    conn.execute(
        f"""
        INSERT INTO strategy_instance_runtime ({', '.join(RUNTIME_COLUMNS)})
        VALUES ({placeholders})
        ON CONFLICT(instance_id) DO UPDATE SET {update_set}
        """,
        [values[col] for col in RUNTIME_COLUMNS],
    )


def seed_runtime_from_legacy_registry(conn: sqlite3.Connection) -> int:
    """One-shot migration from legacy registry rows into the push-state table.

    This is not a dashboard fallback. It writes actual runtime rows so the new
    runtime API has a complete table while existing runner processes are still
    being moved under the supervisor.
    """
    tables = {
        str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "weather_strategy_runtime_registry" not in tables:
        return 0
    rows = conn.execute("SELECT * FROM weather_strategy_runtime_registry").fetchall()
    count = 0
    for row in rows:
        d = dict(row)
        iid = d.get("strategy_instance")
        if not iid:
            continue
        push_runtime_state(
            conn,
            instance_id=str(iid),
            process_status=d.get("process_status") or "unknown",
            health_status=d.get("health_status") or "unknown",
            heartbeat_at_utc=d.get("latest_summary_ts_utc") or d.get("refreshed_at_utc"),
            last_data_ts_utc=d.get("latest_data_ts_utc"),
            latest_summary_ts_utc=d.get("latest_summary_ts_utc"),
            latest_artifact_mtime_utc=d.get("latest_artifact_mtime_utc"),
            heartbeat_age_min=d.get("heartbeat_age_min"),
            candidate_rows=d.get("candidate_rows") or 0,
            plan_rows=d.get("plan_rows") or 0,
            live_order_rows=d.get("live_order_rows") or 0,
            paper_order_rows=d.get("paper_order_rows") or 0,
            shadow_rows=d.get("shadow_rows") or 0,
            telemetry_rows=d.get("telemetry_rows") or 0,
            fact_trade_rows=d.get("fact_trade_rows") or 0,
            fact_live_real_rows=d.get("fact_live_real_rows") or 0,
            fact_cost_usd=d.get("fact_cost_usd"),
            first_target_date=d.get("first_target_date"),
            last_target_date=d.get("last_target_date"),
            latest_fill_ts_utc=d.get("latest_fill_ts_utc"),
            live_enabled=d.get("live_enabled"),
            summary_path=d.get("summary_path"),
            primary_journal_path=d.get("primary_journal_path"),
            blocker_count=d.get("blocker_count") or 0,
            blockers_json=d.get("blockers_json") or "[]",
            summary_json=d.get("summary_json") or "{}",
            refreshed_at_utc=d.get("refreshed_at_utc") or utc_now(),
        )
        count += 1
    return count


def mark_process_state(
    conn: sqlite3.Connection,
    *,
    instance_id: str,
    process_status: str,
    pid: int | None = None,
    supervisor_id: str | None = None,
) -> None:
    """Update only process/supervisor fields without clobbering runner metrics."""
    now = utc_now()
    row = conn.execute(
        "SELECT 1 FROM strategy_instance_runtime WHERE instance_id=?",
        (instance_id,),
    ).fetchone()
    if row is None:
        push_runtime_state(
            conn,
            instance_id=instance_id,
            process_status=process_status,
            pid=pid,
            supervisor_id=supervisor_id,
            heartbeat_at_utc=now,
            refreshed_at_utc=now,
        )
        return
    conn.execute(
        """
        UPDATE strategy_instance_runtime
        SET process_status=?, pid=?, supervisor_id=?, heartbeat_at_utc=?, refreshed_at_utc=?
        WHERE instance_id=?
        """,
        (process_status, pid, supervisor_id or socket.gethostname(), now, now, instance_id),
    )
