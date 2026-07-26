"""Current probe health from the strategy-instance control plane.

The legacy ``weather_strategy_runtime_registry`` contains historical N100 mirrors
and is intentionally not a dashboard truth source.  This endpoint reads the
current ``strategy_instance`` desired state plus ``strategy_instance_runtime``
actual state.  Freshness is calculated from timestamps at request time rather
than trusting a stale ``snapshot_age_min`` value serialized in an old summary.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from weather_dashboard.api import probe_pulse
from weather_dashboard.api.deps import get_db
from weather_dashboard.api.routers.strategy_runtime import _read_recent_jsonl

router = APIRouter(prefix="/probes", tags=["probes"])

Db = Annotated[sqlite3.Connection, Depends(get_db)]

# Repo root, so registry-relative paths (summary_path / runtime_dir) resolve.
ROOT = Path(__file__).resolve().parents[3]


def _root() -> Path:
    return Path(os.environ.get("WEATHER_RUNTIME_ROOT", "runtime/weather_edge_v1"))


def _abs(path: str | None) -> Path | None:
    """Resolve a registry-stored path (may be repo-relative) to absolute."""
    if not path:
        return None
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def _instance_rows(db: Db) -> list[dict]:
    """Return active deployment probes, including unhealthy ones for attention."""
    rows = db.execute(
        """
        SELECT
            si.instance_id AS strategy_instance,
            si.display_name,
            si.lifecycle_status,
            si.execution_mode,
            si.desired_status,
            si.runtime_dir,
            si.expected_live,
            rt.process_status,
            rt.health_status,
            rt.heartbeat_at_utc,
            rt.last_tick_ts_utc,
            rt.last_data_ts_utc,
            rt.candidate_rows,
            rt.plan_rows,
            rt.live_order_rows,
            rt.blocker_count,
            rt.summary_path,
            rt.primary_journal_path,
            rt.summary_json
        FROM strategy_instance si
        LEFT JOIN strategy_instance_runtime rt ON rt.instance_id=si.instance_id
        WHERE si.desired_status='enabled'
          AND (
              si.expected_live=1
              OR si.lifecycle_status IN ('tiny_live_probe', 'shadow', 'monitor')
          )
        ORDER BY
            CASE WHEN rt.process_status='running' THEN 0 ELSE 1 END,
            si.lifecycle_status,
            si.instance_id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _read_summary_for(reg: dict, root: Path) -> dict | None:
    """Read optional runner detail without letting it define freshness."""
    summary_path = _abs(reg.get("summary_path"))
    if summary_path is not None:
        return probe_pulse.read_summary_at(summary_path)
    inst = reg.get("strategy_instance")
    return probe_pulse.read_latest_summary(root, inst) if inst else None


def _runtime_dir_for(reg: dict, root: Path) -> Path:
    rd = _abs(reg.get("runtime_dir"))
    if rd is not None:
        return rd
    return root / (reg.get("strategy_instance") or "")


def _parse_summary(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(str(raw))
    except (TypeError, ValueError):
        return {}


def _age_min(timestamp: str | None, now: datetime) -> float | None:
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds() / 60.0)


def _probe_row(reg: dict, *, now: datetime, root: Path) -> dict:
    """Normalize one current deployment for the v2 probe screens."""
    summary = _parse_summary(reg.get("summary_json"))
    heartbeat_ts = reg.get("last_tick_ts_utc") or reg.get("heartbeat_at_utc")
    data_ts = reg.get("last_data_ts_utc")
    heartbeat_age = _age_min(heartbeat_ts, now)
    snapshot_age = _age_min(data_ts, now)
    status = str(reg.get("process_status") or "unknown")
    if status == "running":
        status = str(summary.get("status") or "running")

    # Raw summary remains useful for diagnosis and caps; actual counts come
    # from supervisor-pushed runtime state.
    top_audit = probe_pulse._top_audit(summary)
    caps = summary.get("caps") or {
        key: summary[key]
        for key in ("base_notional", "daily_gross_cap", "fixed_shares", "max_daily_cost_usd")
        if key in summary
    }
    return {
        "strategy_instance": reg.get("strategy_instance"),
        "display_name": reg.get("display_name"),
        "lifecycle_status": reg.get("lifecycle_status"),
        "execution_mode": reg.get("execution_mode"),
        "desired_status": reg.get("desired_status"),
        "process_status": reg.get("process_status") or "unknown",
        "health_status": reg.get("health_status") or "unknown",
        "heartbeat_age_min": heartbeat_age,
        "status": status,
        "snapshot_age_min": snapshot_age,
        "snapshot_ts_utc": data_ts,
        "freshness": probe_pulse.classify_freshness(snapshot_age, 20.0, 120.0),
        "candidate_rows": reg.get("candidate_rows"),
        "execution_eligible": reg.get("plan_rows"),
        "live_order_rows": reg.get("live_order_rows"),
        "alert_count": reg.get("blocker_count"),
        "critical_alerts": None,
        "warning_alerts": None,
        "top_audit": top_audit,
        "caps": caps or None,
    }


@router.get("/health")
def get_probe_health(db: Db) -> dict[str, Any]:
    root = _root()
    now = datetime.now(timezone.utc)
    out = [_probe_row(reg, now=now, root=root) for reg in _instance_rows(db)]
    return {"probes": out, "generated_at_utc": datetime.now(timezone.utc).isoformat()}


@router.get("/{instance}")
def get_probe(instance: str, db: Db) -> dict[str, Any]:
    root = _root()
    reg = next((r for r in _instance_rows(db) if r.get("strategy_instance") == instance), None)
    if reg is None:
        raise HTTPException(status_code=404, detail=f"unknown probe: {instance}")
    summary = _read_summary_for(reg, root)
    base = _runtime_dir_for(reg, root)
    candidates = (summary or {}).get("candidates")
    if candidates is None:
        candidates = _read_recent_jsonl(base / "latest_candidates.json", 1)
    return {
        "row": _probe_row(reg, now=datetime.now(timezone.utc), root=root),
        "history": _read_recent_jsonl(base / "summary_history.jsonl", 50),
        "candidates": candidates,
        "live_orders": _read_recent_jsonl(base / "live_orders.jsonl", 20),
    }
