"""Ingestor node: sync markets/events and handle resurrection."""

from typing import Any, Dict, Optional

from ..pipeline import run_sync_gamma
from ..storage import mark_new_markets, resurrect_markets
from .constants import DEFAULT_LIQUIDITY_THRESHOLD


class IngestorNode:
    def __init__(
        self,
        active: bool = True,
        pages: int = 5,
        page_size: int = 100,
        closed: Optional[bool] = None,
        resume: bool = True,
        liquidity_threshold: float = DEFAULT_LIQUIDITY_THRESHOLD,
    ) -> None:
        self.active = active
        self.pages = pages
        self.page_size = page_size
        self.closed = closed
        self.resume = resume
        self.liquidity_threshold = liquidity_threshold

    def execute(self) -> Dict[str, Any]:
        sync_res = run_sync_gamma(
            active=self.active,
            pages=self.pages,
            page_size=self.page_size,
            closed=self.closed,
            resume=self.resume,
        )
        new_count = mark_new_markets()
        resurrected = resurrect_markets(self.liquidity_threshold)
        return {
            **sync_res,
            "status_new": new_count,
            "status_resurrected": resurrected,
        }
