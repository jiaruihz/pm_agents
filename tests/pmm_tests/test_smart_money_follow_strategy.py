import json
import tempfile
import unittest
from pathlib import Path

from src.strategies.pmm.config import PMMConfig
from src.platform.quote_runtime.strategy_base import StrategyQuoteInput
from src.strategies.pmm.variants.single_level_v1 import SingleLevelV1Strategy
from src.strategies.smart_money_follow_v1.pmm_adapter import SmartMoneyFollowV1Strategy


def _anchor_quotes(
    target_bid: float,
    target_ask: float,
    best_bid: float,
    best_ask: float,
    join_epsilon: float,
    fair_value: float,
    min_edge: float,
):
    return target_bid, target_ask


def _quantize_pair(bid: float, ask: float, tick: float, mode: str):
    return bid, ask


def _target_sizes(
    config: PMMConfig,
    position: float,
    usdc_balance: float,
    bid_price: float,
    open_buy_qty: float = 0.0,
    open_sell_qty: float = 0.0,
):
    del open_buy_qty, open_sell_qty
    return 10.0, 10.0


def _by_side(quotes):
    return {q.side: q for q in quotes}


class TestSmartMoneyFollowV1Strategy(unittest.TestCase):
    def setUp(self):
        self.quote_input = StrategyQuoteInput(
            token_id="YES",
            mid=0.50,
            adaptive_spread=0.08,
            inventory_signal=0.0,
            best_bid=0.49,
            best_ask=0.51,
            position=0.0,
            effective_usdc_balance=1000.0,
            open_buy_qty=0.0,
            open_sell_qty=0.0,
        )
        self.single = SingleLevelV1Strategy(
            anchor_quotes_fn=_anchor_quotes,
            quantize_pair_fn=_quantize_pair,
            target_sizes_fn=_target_sizes,
        )

    def test_matches_single_level_when_no_smart_money_signal(self):
        cfg = PMMConfig()
        cfg.strategy_params = {}
        smart = SmartMoneyFollowV1Strategy(
            anchor_quotes_fn=_anchor_quotes,
            quantize_pair_fn=_quantize_pair,
            target_sizes_fn=_target_sizes,
        )
        base_quotes = _by_side(self.single.generate_quotes(self.quote_input, cfg))
        sm_quotes = _by_side(smart.generate_quotes(self.quote_input, cfg))
        self.assertEqual(set(base_quotes.keys()), set(sm_quotes.keys()))
        for side in base_quotes.keys():
            self.assertAlmostEqual(
                base_quotes[side].price, sm_quotes[side].price, places=8
            )
            self.assertAlmostEqual(
                base_quotes[side].size, sm_quotes[side].size, places=8
            )

    def test_positive_signal_increases_buy_bias(self):
        base_cfg = PMMConfig()
        base_cfg.strategy_params = {}
        base_quotes = _by_side(self.single.generate_quotes(self.quote_input, base_cfg))

        cfg = PMMConfig()
        cfg.strategy_params = {
            "smart_money_min_abs_signal": 0.0,
            "smart_money_token_signals": {"YES": 0.8},
            "smart_money_price_tilt_factor": 0.4,
            "smart_money_size_tilt_factor": 1.0,
        }
        smart = SmartMoneyFollowV1Strategy(
            anchor_quotes_fn=_anchor_quotes,
            quantize_pair_fn=_quantize_pair,
            target_sizes_fn=_target_sizes,
        )
        sm_quotes = _by_side(smart.generate_quotes(self.quote_input, cfg))

        self.assertGreater(sm_quotes["BUY"].price, base_quotes["BUY"].price)
        self.assertGreater(sm_quotes["SELL"].price, base_quotes["SELL"].price)
        self.assertGreater(sm_quotes["BUY"].size, base_quotes["BUY"].size)
        self.assertLess(sm_quotes["SELL"].size, base_quotes["SELL"].size)

    def test_high_conviction_turns_one_side_only(self):
        cfg = PMMConfig()
        cfg.strategy_params = {
            "smart_money_min_abs_signal": 0.0,
            "smart_money_token_signals": {"YES": -0.95},
            "smart_money_one_side_only_threshold": 0.9,
        }
        smart = SmartMoneyFollowV1Strategy(
            anchor_quotes_fn=_anchor_quotes,
            quantize_pair_fn=_quantize_pair,
            target_sizes_fn=_target_sizes,
        )
        sm_quotes = smart.generate_quotes(self.quote_input, cfg)
        sides = {q.side for q in sm_quotes}
        self.assertEqual(sides, {"SELL"})

    def test_signal_file_hot_reload(self):
        now = [1000.0]
        with tempfile.TemporaryDirectory() as td:
            signal_file = Path(td) / "signals.json"
            signal_file.write_text(
                json.dumps({"token_signals": {"YES": 0.90}}, ensure_ascii=False),
                encoding="utf-8",
            )

            cfg = PMMConfig()
            cfg.strategy_params = {
                "smart_money_min_abs_signal": 0.0,
                "smart_money_signal_file": str(signal_file),
                "smart_money_signal_reload_sec": 0.1,
                "smart_money_file_signal_weight": 1.0,
                "smart_money_one_side_only_threshold": 0.99,
            }
            smart = SmartMoneyFollowV1Strategy(
                anchor_quotes_fn=_anchor_quotes,
                quantize_pair_fn=_quantize_pair,
                target_sizes_fn=_target_sizes,
                time_fn=lambda: now[0],
            )

            first = _by_side(smart.generate_quotes(self.quote_input, cfg))
            first_buy_price = first["BUY"].price

            signal_file.write_text(
                json.dumps({"token_signals": {"YES": -0.90}}, ensure_ascii=False),
                encoding="utf-8",
            )
            now[0] += 1.0
            second = _by_side(smart.generate_quotes(self.quote_input, cfg))
            second_buy_price = second["BUY"].price

            self.assertLess(second_buy_price, first_buy_price)


if __name__ == "__main__":
    unittest.main()
