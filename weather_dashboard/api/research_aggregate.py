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


def _best_variant_roi(summary: dict) -> tuple[float | None, float | None, float | None]:
    """From a `variants` list, return (best_roi, ci_low, ci_high) of the best
    non-baseline variant by roi. Honest representative for the index row."""
    variants = summary.get("variants")
    if not isinstance(variants, list):
        return (None, None, None)
    best = None
    for v in variants:
        if not isinstance(v, dict) or v.get("roi") is None:
            continue
        name = str(v.get("variant", "")).lower()
        if "baseline" in name:
            continue
        if best is None or v["roi"] > best["roi"]:
            best = v
    if best is None:
        return (None, None, None)
    return (best.get("roi"), best.get("roi_ci_low"), best.get("roi_ci_high"))


def _normalize(line_id: str, summary: dict, summary_path: Path, analysis_root: Path) -> dict:
    verdict = summary.get("verdict") if isinstance(summary.get("verdict"), dict) else {}

    # ROI/CI: prefer top-level keys, else the best non-baseline variant.
    holdout_roi = _first(summary, _HOLDOUT_KEYS)
    forward_roi = _first(summary, _FORWARD_KEYS)
    ci_low = _first(summary, _CI_LOW_KEYS)
    ci_high = _first(summary, _CI_HIGH_KEYS)
    best_roi = None
    if ci_low is None and ci_high is None:
        best_roi, ci_low, ci_high = _best_variant_roi(summary)

    ci_crosses_zero = ci_low is not None and ci_high is not None and ci_low <= 0 <= ci_high

    # gate: prefer the report's own verdict.live_ready; else infer.
    if "live_ready" in verdict:
        gate_ready = bool(verdict.get("live_ready"))
    else:
        gate_ready = ci_crosses_zero is False and forward_roi is not None and forward_roi > 0

    status = verdict.get("status") or summary.get("status") or "unknown"
    variants = summary.get("variants")
    variant_count = len(variants) if isinstance(variants, list) else 0

    try:
        rel = str(summary_path.relative_to(analysis_root))
    except ValueError:
        rel = str(summary_path)
    return {
        "line_id": line_id,
        "title": summary.get("title") or summary.get("strategy") or line_id,
        "status": status,
        "verdict_reason": verdict.get("reason"),
        "holdout_roi": holdout_roi,
        "forward_roi": forward_roi,
        "repr_roi": best_roi if holdout_roi is None and forward_roi is None else None,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "ci_crosses_zero": ci_crosses_zero,
        "excess_roi_vs_baseline": _first(summary, _EXCESS_KEYS),
        "gate_ready": gate_ready,
        "variant_count": variant_count,
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


# Markers where the human narrative ends and the dense tables/appendix begin.
_NARRATIVE_STOPS = ("## Variant Summary", "## Main Candidate", "## 数据范围", "## Model Metrics")


def find_doc_narrative(analysis_root: Path, line_id: str) -> dict | None:
    """Locate the analysis .md for a line and return its leading narrative
    (title + 结论/思路), i.e. *why* the strategy is built this way. The .md is
    named <date>-<line_id with underscores→hyphens>.md."""
    root = Path(analysis_root)
    slug = line_id.replace("_", "-")
    matches = sorted(root.glob(f"*/*{slug}.md"))
    if not matches:
        return None
    doc = matches[-1]
    text = doc.read_text(encoding="utf-8")
    # Cut at the first dense-table/appendix marker, or first markdown table row.
    cut = len(text)
    for marker in _NARRATIVE_STOPS:
        idx = text.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    table_idx = text.find("\n| ")
    if table_idx != -1:
        cut = min(cut, table_idx)
    narrative = text[:cut].strip()
    try:
        rel = str(doc.relative_to(root))
    except ValueError:
        rel = str(doc)
    return {"doc_path": rel, "narrative_md": narrative}


def read_research_line(analysis_root: Path, line_id: str) -> dict | None:
    """Return the full summary.json (plus resolved path + doc narrative)."""
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
        doc = find_doc_narrative(root, line_id)
        return {
            "line_id": line_id,
            "summary": summary,
            "summary_path": rel,
            "narrative_md": doc["narrative_md"] if doc else None,
            "doc_path": doc["doc_path"] if doc else None,
        }
    return None
