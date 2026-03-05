"""Pipeline executor for PAP state machine."""

import asyncio
from typing import Any, Dict, Optional

from ..db import init_db
from ..pipeline import run_enrich
from .builder import PromptBuilder
from .filter import FilterNode
from .ingestor import IngestorNode
from .investigator import InvestigatorNode
from .parser import ParserNode
from .quant import QuantNode
from .constants import STATUS_READY_TO_PARSE


class PipelineExecutor:
    def __init__(
        self,
        pages: int = 5,
        page_size: int = 100,
        closed: Optional[bool] = None,
        resume: bool = True,
        enrich_limit: int = 500,
        enrich_top_n: int = 20,
        enrich_two_stage: bool = True,
        enrich_price_min: float = 0.05,
        enrich_price_max: float = 0.95,
        enrich_prices: bool = True,
        enrich_orderbooks: bool = True,
        require_best_bid_ask: bool = True,
        metadata_only_filter: bool = True,
        parse_batch: int = 100,
        investigate_limit: int = 100,
        edge_threshold: float = 0.15,
        output_path: str = "output/pap_candidates.md",
    ) -> None:
        self.ingestor = IngestorNode(pages=pages, page_size=page_size, closed=closed, resume=resume)
        self.enrich_limit = enrich_limit
        self.enrich_top_n = enrich_top_n
        self.enrich_two_stage = enrich_two_stage
        self.enrich_price_min = enrich_price_min
        self.enrich_price_max = enrich_price_max
        self.enrich_prices = enrich_prices
        self.enrich_orderbooks = enrich_orderbooks
        self.filter = FilterNode(
            require_best_bid_ask=require_best_bid_ask,
            price_min=enrich_price_min,
            price_max=enrich_price_max,
            metadata_only=metadata_only_filter,
        )
        self.parser = ParserNode(batch=parse_batch)
        self.investigator = InvestigatorNode(limit=investigate_limit)
        self.quant = QuantNode(edge_threshold=edge_threshold)
        self.builder = PromptBuilder(output_path=output_path)

    def run(self) -> Dict[str, Any]:
        init_db()
        res: Dict[str, Any] = {}
        res["ingestor"] = self.ingestor.execute()
        res["filter"] = self.filter.execute()
        res["enrich"] = run_enrich(
            limit=self.enrich_limit,
            top_n=self.enrich_top_n,
            archive_books=False,
            fetch_prices=self.enrich_prices,
            fetch_books=self.enrich_orderbooks,
            two_stage=self.enrich_two_stage,
            orderbook_min_mid=self.enrich_price_min,
            orderbook_max_mid=self.enrich_price_max,
            market_statuses=[STATUS_READY_TO_PARSE],
        )
        res["parser"] = asyncio.run(self.parser.execute())
        res["investigator"] = self.investigator.execute()
        candidates = self.quant.evaluate()
        res["candidates"] = {"count": len(candidates)}
        res["output_path"] = self.builder.generate(candidates)
        return res
