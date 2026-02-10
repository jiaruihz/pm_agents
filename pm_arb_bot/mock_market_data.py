from __future__ import annotations

from typing import Any, Dict

from pm_arb_bot.config import ArbConfig


class MockMarketDataFeed:
    """Deterministic mock top-of-book generator for arb simulation.

    It generates 1-level orderbooks (bids/asks) per token id, shaped so that:
    - merge_arb: ask_yes + ask_no + fee_buffer < 1.0
    - split_arb: bid_yes + bid_no > 1.0 + fee_buffer
    - neutral: no arb
    - toggle: alternates merge_arb and split_arb each tick
    """

    def __init__(self, config: ArbConfig) -> None:
        self.config = config
        self._tick = 0

    def next_orderbooks(self) -> Dict[str, Dict[str, Any]]:
        self._tick += 1
        scenario = (self.config.mock_scenario or "toggle").strip().lower()
        if scenario == "toggle":
            # odd ticks: merge arb, even ticks: split arb
            scenario = "merge_arb" if (self._tick % 2 == 1) else "split_arb"

        spread = max(0.0001, float(self.config.mock_spread))
        sz = max(0.0001, float(self.config.mock_level_size))
        fee = max(0.0, float(self.config.fee_buffer))
        margin = max(0.0, float(self.config.mock_edge_margin))

        out: Dict[str, Dict[str, Any]] = {}
        for pair in self.config.pairs:
            yes_id = pair.yes_token_id
            no_id = pair.no_token_id

            if scenario == "merge_arb":
                # Make asks cheap enough to buy both and still be <1 after fee.
                target_sum = 1.0 - fee - margin
                ask_yes = max(0.01, min(0.99, target_sum / 2.0))
                ask_no = max(0.01, min(0.99, target_sum - ask_yes))
                bid_yes = max(0.0001, ask_yes - spread)
                bid_no = max(0.0001, ask_no - spread)
            elif scenario == "split_arb":
                # Make bids rich enough so selling both >1 after fee.
                target_sum = 1.0 + fee + margin
                bid_yes = max(0.01, min(0.99, target_sum / 2.0))
                bid_no = max(0.01, min(0.99, target_sum - bid_yes))
                ask_yes = min(0.9999, bid_yes + spread)
                ask_no = min(0.9999, bid_no + spread)
            else:
                # neutral
                mid_yes = 0.50
                mid_no = 0.50
                bid_yes = max(0.0001, mid_yes - spread / 2.0)
                ask_yes = min(0.9999, mid_yes + spread / 2.0)
                bid_no = max(0.0001, mid_no - spread / 2.0)
                ask_no = min(0.9999, mid_no + spread / 2.0)

            out[yes_id] = {
                "bids": [{"price": bid_yes, "size": sz}],
                "asks": [{"price": ask_yes, "size": sz}],
            }
            out[no_id] = {
                "bids": [{"price": bid_no, "size": sz}],
                "asks": [{"price": ask_no, "size": sz}],
            }

        return out

