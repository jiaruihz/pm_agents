"""Research-evidence registry: aggregate generated summary.json files (read-only)."""

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from weather_dashboard.api import research_aggregate

router = APIRouter(prefix="/research/lines", tags=["research-lines"])


def _root() -> Path:
    return Path(os.environ.get("WEATHER_ANALYSIS_ROOT", "docs/analysis"))


@router.get("")
def get_research_lines() -> dict[str, Any]:
    return {"lines": research_aggregate.aggregate_research_lines(_root())}


@router.get("/{line_id}")
def get_research_line(line_id: str) -> dict[str, Any]:
    line = research_aggregate.read_research_line(_root(), line_id)
    if line is None:
        raise HTTPException(status_code=404, detail=f"unknown research line: {line_id}")
    return line
