"""Aggregate per-strategy research evidence from generated summary.json files.

Each analysis run writes ``<analysis_root>/<month>/generated/<line_id>/summary.json``.
We normalize those into one row per research line for the evidence registry page.
Field names vary across reports, so every extraction tolerates absence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# summary.json key aliases → normalized field
_HOLDOUT_KEYS = ("holdout_roi", "roi_holdout", "holdout_roi_settled")
_FORWARD_KEYS = ("forward_roi", "roi_forward", "forward_roi_settled")
_CI_LOW_KEYS = ("roi_ci_low", "ci_low", "roi_ci_low_holdout")
_CI_HIGH_KEYS = ("roi_ci_high", "ci_high", "roi_ci_high_holdout")
_EXCESS_KEYS = ("excess_roi_vs_baseline", "excess_roi")


def _first(d: dict, keys: tuple[str, ...]) -> Any:
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return None


def _normalize(line_id: str, summary: dict, summary_path: Path, analysis_root: Path) -> dict:
    ci_low = _first(summary, _CI_LOW_KEYS)
    ci_high = _first(summary, _CI_HIGH_KEYS)
    forward_roi = _first(summary, _FORWARD_KEYS)
    ci_crosses_zero = ci_low is not None and ci_high is not None and ci_low <= 0 <= ci_high
    gate_ready = (ci_crosses_zero is False and forward_roi is not None and forward_roi > 0)
    try:
        rel = str(summary_path.relative_to(analysis_root))
    except ValueError:
        rel = str(summary_path)
    return {
        "line_id": line_id,
        "title": summary.get("title") or line_id,
        "status": summary.get("status") or "unknown",
        "holdout_roi": _first(summary, _HOLDOUT_KEYS),
        "forward_roi": forward_roi,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "ci_crosses_zero": ci_crosses_zero,
        "excess_roi_vs_baseline": _first(summary, _EXCESS_KEYS),
        "gate_ready": gate_ready,
        "generated_at_utc": summary.get("generated_at_utc") or summary.get("generated"),
        "summary_path": rel,
    }


def aggregate_research_lines(analysis_root: Path) -> list[dict]:
    """Walk <analysis_root>/**/generated/*/summary.json into normalized rows."""
    root = Path(analysis_root)
    rows: list[dict] = []
    for summary_path in sorted(root.glob("*/generated/*/summary.json")):
        line_id = summary_path.parent.name
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            summary = {"_parse_error": True}
        if not isinstance(summary, dict):
            summary = {}
        rows.append(_normalize(line_id, summary, summary_path, root))
    return rows


def read_research_line(analysis_root: Path, line_id: str) -> dict | None:
    """Return the full summary.json (plus resolved path) for one line, or None."""
    root = Path(analysis_root)
    for summary_path in root.glob(f"*/generated/{line_id}/summary.json"):
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            summary = {}
        try:
            rel = str(summary_path.relative_to(root))
        except ValueError:
            rel = str(summary_path)
        return {"line_id": line_id, "summary": summary, "summary_path": rel}
    return None
