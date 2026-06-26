"""Live/shadow probe health from registry + runtime JSONL pulse (read-only).

The probe allowlist is the ``weather_strategy_runtime_registry`` table — never a
directory glob — so smoke/tmp runners are excluded. The realtime pulse comes from
each probe's ``latest_summary.json`` under ``WEATHER_RUNTIME_ROOT``.
"""

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


def _root() -> Path:
    return Path(os.environ.get("WEATHER_RUNTIME_ROOT", "runtime/weather_edge_v1"))


def _registry_rows(db: Db) -> list[dict]:
    try:
        rows = db.execute("SELECT * FROM weather_strategy_runtime_registry").fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


@router.get("/health")
def get_probe_health(db: Db) -> dict[str, Any]:
    root = _root()
    out: list[dict[str, Any]] = []
    for reg in _registry_rows(db):
        inst = reg.get("strategy_instance")
        summary = probe_pulse.read_latest_summary(root, inst) if inst else None
        out.append(probe_pulse.normalize_probe_row(reg, summary))
    return {"probes": out, "generated_at_utc": datetime.now(timezone.utc).isoformat()}


@router.get("/{instance}")
def get_probe(instance: str, db: Db) -> dict[str, Any]:
    root = _root()
    reg = next((r for r in _registry_rows(db) if r.get("strategy_instance") == instance), None)
    if reg is None:
        raise HTTPException(status_code=404, detail=f"unknown probe: {instance}")
    summary = probe_pulse.read_latest_summary(root, instance)
    base = root / instance
    candidates = (summary or {}).get("candidates")
    if candidates is None:
        candidates = _read_recent_jsonl(base / "latest_candidates.json", 1)
    return {
        "row": probe_pulse.normalize_probe_row(reg, summary),
        "history": _read_recent_jsonl(base / "summary_history.jsonl", 50),
        "candidates": candidates,
        "live_orders": _read_recent_jsonl(base / "live_orders.jsonl", 20),
    }
