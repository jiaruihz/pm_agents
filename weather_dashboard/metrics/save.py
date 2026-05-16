"""
save.py

Compute metrics for a run and persist them to runs.metrics (JSON) and
runs.metrics_at_utc. Safe to call multiple times (idempotent update).
"""

import json
import sqlite3
from datetime import datetime, timezone

from weather_dashboard.metrics.calc import compute_metrics


def save_metrics(conn: sqlite3.Connection, run_id: str) -> dict:
    """
    Compute metrics for run_id and write to runs.metrics + runs.metrics_at_utc.
    Returns the computed metrics dict.
    """
    m = compute_metrics(conn, run_id)
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        "UPDATE runs SET metrics = ?, metrics_at_utc = ? WHERE run_id = ?",
        (json.dumps(m), now, run_id),
    )
    conn.commit()
    return m


def save_all_metrics(conn: sqlite3.Connection) -> dict[str, dict]:
    """
    Compute and save metrics for every run in the DB.
    Returns {run_id: metrics} for all runs.
    """
    run_ids = [r[0] for r in conn.execute("SELECT run_id FROM runs").fetchall()]
    return {rid: save_metrics(conn, rid) for rid in run_ids}
