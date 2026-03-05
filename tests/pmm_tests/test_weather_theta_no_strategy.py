import unittest

from src.strategies.pmm.config import PMMConfig
from src.strategies.pmm.core.strategy_base import StrategyQuoteInput
from src.strategies.pmm.variants.weather_theta_no_v1 import WeatherThetaNoV1Strategy


def _quantize_pair(bid: float, ask: float, tick: float, mode: str):
    return bid, ask


def _mk_input(
    *,
    token_id: str = "NO_TOKEN",
    mid: float = 0.90,
    best_bid: float = 0.89,
    best_ask: float = 0.91,
    position: float = 0.0,
    usdc: float = 1000.0,
    open_buy_qty: float = 0.0,
    open_sell_qty: float = 0.0,
) -> StrategyQuoteInput:
    return StrategyQuoteInput(
        token_id=token_id,
        mid=mid,
        adaptive_spread=0.04,
        inventory_signal=0.0,
        best_bid=best_bid,
        best_ask=best_ask,
        position=position,
        effective_usdc_balance=usdc,
        open_buy_qty=open_buy_qty,
        open_sell_qty=open_sell_qty,
    )


class TestWeatherThetaNoV1Strategy(unittest.TestCase):
    def setUp(self):
        self.now = [1_000.0]
        self.strategy = WeatherThetaNoV1Strategy(
            quantize_pair_fn=_quantize_pair,
            time_fn=lambda: self.now[0],
        )
        self.cfg = PMMConfig()
        self.cfg.strategy_params = {
            "weather_entry_min_price": 0.80,
            "weather_entry_max_price": 0.96,
            "weather_position_pct": 0.05,
            "weather_take_profit_abs": 0.01,
            "weather_stop_loss_abs": 0.03,
            "weather_min_hold_hours": 0.0,
            "weather_max_hold_hours": 48.0,
            "weather_reentry_cooldown_hours": 2.0,
        }

    def test_entry_buy_quote_when_mid_in_range(self):
        quotes = self.strategy.generate_quotes(_mk_input(position=0.0), self.cfg)
        self.assertEqual(len(quotes), 1)
        q = quotes[0]
        self.assertEqual(q.side, "BUY")
        self.assertGreaterEqual(q.price, 0.80)
        self.assertLessEqual(q.price, 0.96)
        self.assertAlmostEqual(q.price * q.size, 50.0, places=5)

    def test_no_entry_when_mid_outside_range(self):
        quotes = self.strategy.generate_quotes(_mk_input(mid=0.70, best_bid=0.69, best_ask=0.71), self.cfg)
        self.assertEqual(quotes, [])

    def test_take_profit_exit_after_hold(self):
        self.strategy.generate_quotes(_mk_input(position=8.0, mid=0.90), self.cfg)
        self.now[0] += 7200.0
        quotes = self.strategy.generate_quotes(
            _mk_input(position=8.0, mid=0.915, best_bid=0.914, best_ask=0.916),
            self.cfg,
        )
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].side, "SELL")
        self.assertAlmostEqual(quotes[0].size, 8.0, places=8)

    def test_stop_loss_exit_is_aggressive(self):
        self.strategy.generate_quotes(_mk_input(position=6.0, mid=0.90), self.cfg)
        self.now[0] += 60.0
        quotes = self.strategy.generate_quotes(
            _mk_input(position=6.0, mid=0.86, best_bid=0.86, best_ask=0.862),
            self.cfg,
        )
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].side, "SELL")
        self.assertLess(quotes[0].price, 0.86)

    def test_reentry_cooldown_blocks_immediate_rebuy(self):
        self.strategy.generate_quotes(_mk_input(position=5.0, mid=0.90), self.cfg)
        self.now[0] += 30.0
        self.strategy.generate_quotes(_mk_input(position=0.0, mid=0.90), self.cfg)
        self.now[0] += 1800.0
        quotes = self.strategy.generate_quotes(_mk_input(position=0.0, mid=0.90), self.cfg)
        self.assertEqual(quotes, [])


if __name__ == "__main__":
    unittest.main()
