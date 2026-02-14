"""Investigator node: stubbed evidence collection."""

from typing import Any, Dict, List

from ..storage import get_markets_by_status, save_evidence_locker, update_market_statuses
from .constants import STATUS_INVESTIGATED, STATUS_READY_TO_SEARCH


class InvestigatorNode:
    def __init__(self, limit: int = 100) -> None:
        self.limit = limit

    def execute(self) -> Dict[str, Any]:
        markets = get_markets_by_status([STATUS_READY_TO_SEARCH], limit=self.limit)
        if not markets:
            return {"investigated": 0}

        records = []
        for m in markets:
            records.append(
                {
                    "market_id": m["market_id"],
                    "search_summary": "",
                    "verification_result": "UNCERTAIN",
                    "source_links_json": "[]",
                }
            )

        saved = save_evidence_locker(records)
        updated = update_market_statuses([m["market_id"] for m in markets], STATUS_INVESTIGATED)
        return {"investigated": updated, "evidence_saved": saved}
