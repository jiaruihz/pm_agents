"""Compare endpoint: side-by-side metrics for multiple runs."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Query
import sqlite3

from weather_dashboard.api.deps import get_db
from weather_dashboard.api.schemas import CompareResponse
from weather_dashboard.metrics.calc import compute_metrics

router = APIRouter(prefix="/compare", tags=["compare"])

Db = Annotated[sqlite3.Connection, Depends(get_db)]


@router.get("", response_model=CompareResponse)
def compare_runs(
    db: Db,
    run_ids: str = Query(..., description="Comma-separated list of run_ids to compare"),
):
    """Return metrics for each requested run_id side-by-side."""
    ids = [r.strip() for r in run_ids.split(",") if r.strip()]

    result = []
    for run_id in ids:
        row = db.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            result.append({"run_id": run_id, "error": "not found", "metrics": None})
            continue

        metrics = compute_metrics(db, run_id)

        def _parse_tags(raw):
            try:
                return json.loads(raw) if raw else []
            except Exception:
                return []

        result.append({
            "run_id": run_id,
            "config_id": row["config_id"],
            "execution_mode": row["execution_mode"],
            "state": row["state"],
            "date_range_start": row["date_range_start"],
            "date_range_end": row["date_range_end"],
            "tags": _parse_tags(row["tags"]),
            "metrics": metrics,
        })

    return {"runs": result}
