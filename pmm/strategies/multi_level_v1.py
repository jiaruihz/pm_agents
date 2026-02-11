from __future__ import annotations

from typing import List

from pmm.config import PMMConfig
from pmm.strategy_base import QuoteTarget, StrategyQuoteInput


class MultiLevelV1Strategy:
    key = "multi_level_v1"

    def generate_quotes(
        self,
        quote_input: StrategyQuoteInput,
        config: PMMConfig,
    ) -> List[QuoteTarget]:
        raise NotImplementedError(
            "multi_level_v1 is reserved for future implementation. "
            "Use strategy_key=single_level_v1 for now."
        )

