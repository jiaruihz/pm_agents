"""Research artifact endpoints for weather strategy analysis."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/research", tags=["research"])

ROOT = Path(__file__).resolve().parents[3]
ANALYSIS_DIR = ROOT / "docs" / "analysis"


def _latest(pattern: str) -> Path | None:
    matches = sorted(
        ANALYSIS_DIR.glob(f"*/{pattern}"),
        key=lambda p: (p.name, p.stat().st_mtime),
        reverse=True,
    )
    return matches[0] if matches else None


def _load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/weather-edge-v2/latest")
def latest_weather_edge_v2() -> dict[str, Any]:
    """Return the latest compact weather_edge_v2 research artifacts."""
    lineage_path = _latest("*weather-edge-v2-shadow-lineage.json")
    research_path = _latest("*weather-edge-v2-filtered-operational-base-research.json")
    if lineage_path is None and research_path is None:
        raise HTTPException(status_code=404, detail="No weather_edge_v2 research artifacts found")

    lineage = _load_json(lineage_path)
    research = _load_json(research_path)

    return {
        "lineage_path": str(lineage_path) if lineage_path else None,
        "research_path": str(research_path) if research_path else None,
        "lineage": lineage,
        "research": research,
    }
