"""Content-addressed inputs shared by legacy observed-max diagnostics."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from scripts.analysis.versioned_artifact_output import (
    resolve_content_addressed_artifact,
)
from src.strategies.runtime.production import load_production_spec


M3_ORDERBOOK_BEST_ASK_SHA256 = (
    "77a88851ee101e6c5d023d9f50e7b7b1503204644d0676c59383539790f63d02"
)
PM_HISTORY_MANIFEST = "pm_agents_d1_pm_history_expanded_v2_cleanup_20260807.json"
PM_HISTORY_MANIFEST_SHA256 = (
    "e5dd2ed04ff36ee9a2c2c693e170c61c3a9098aa5f0354b1123a1623e53bb615"
)


def m3_orderbook_best_ask_quotes() -> Path:
    return resolve_content_addressed_artifact(M3_ORDERBOOK_BEST_ASK_SHA256)


@lru_cache(maxsize=1)
def _pm_history_index() -> dict[str, str]:
    root = load_production_spec().research_artifact_root
    manifest = root / "manifests" / PM_HISTORY_MANIFEST
    payload = manifest.read_bytes()
    actual = hashlib.sha256(payload).hexdigest()
    if actual != PM_HISTORY_MANIFEST_SHA256:
        raise ValueError(
            f"historical pm_history manifest drift: {manifest} "
            f"expected={PM_HISTORY_MANIFEST_SHA256} actual={actual}"
        )
    rows = json.loads(payload)["files"]
    return {Path(row["path"]).name: str(row["sha256"]) for row in rows}


@lru_cache(maxsize=None)
def pm_history_winner_label(city: str, target_date: str) -> str | None:
    """Return the single settled bracket from the pinned historical snapshot."""
    sha256 = _pm_history_index().get(f"{city}_{target_date}.json")
    if sha256 is None:
        return None
    payload = json.loads(resolve_content_addressed_artifact(sha256).read_text())
    if not isinstance(payload, dict):
        return None
    winners = [
        str(bracket.get("label", "")).strip()
        for bracket in payload.get("brackets") or []
        if float(bracket.get("final_price") or 0) >= 0.99
    ]
    return winners[0] if len(winners) == 1 else None


@lru_cache(maxsize=None)
def pm_history_winner(city: str, target_date: str) -> tuple[str, str] | None:
    """Return the pinned snapshot's single winning label and question."""
    sha256 = _pm_history_index().get(f"{city}_{target_date}.json")
    if sha256 is None:
        return None
    payload = json.loads(resolve_content_addressed_artifact(sha256).read_text())
    if not isinstance(payload, dict):
        return None
    winners = [
        bracket
        for bracket in payload.get("brackets") or []
        if float(bracket.get("final_price") or 0) >= 0.99
    ]
    if len(winners) != 1:
        return None
    winner = winners[0]
    return str(winner.get("label") or ""), str(winner.get("question") or "")
