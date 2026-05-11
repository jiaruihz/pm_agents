import unittest

from src.platform.engine.base import StrategyContext
from src.platform.engine.events import MarketTickEvent
from src.platform.engine.models import OrderSide
from src.strategies.pmm.config import PMMConfig
from src.platform.quote_runtime.strategy_base import StrategyQuoteInput
from src.strategies.weather_edge_v1.core import DEFAULT_PARAMS, WeatherDecisionParams
from src.strategies.weather_edge_v1.pmm_adapter import WeatherEdgeV1Strategy
from src.strategies.weather_edge_v1.tools.unified_strategy import UnifiedWeatherEdgeStrategy


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


class _StubWeatherProvider:
    def __init__(self, forecast_map):
        self._forecast_map = dict(forecast_map)

    def get_daily_max_temperature(self, token_id: str):
        return self._forecast_map.get(token_id)


class TestWeatherEdgeV1Strategy(unittest.TestCase):
    def setUp(self):
        self.now = [1_000.0]
        self.strategy = WeatherEdgeV1Strategy(
            quantize_pair_fn=_quantize_pair,
            time_fn=lambda: self.now[0],
        )
        self.strategy._weather_provider = _StubWeatherProvider({"NO_TOKEN": 16.0})  # type: ignore[attr-defined]
        self.strategy._weather_target_meta = {  # type: ignore[attr-defined]
            "NO_TOKEN": {"unit": "C", "bucket_value": 12.0}
        }
        self.cfg = PMMConfig()
        self.cfg.strategy_params = {
            "weather_entry_min_price": 0.80,
            "weather_entry_max_price": 0.97,
            "weather_position_pct": 0.05,
            "weather_take_profit_abs": 0.02,
            "weather_stop_loss_abs": 0.03,
            "weather_min_hold_hours": 0.0,
            "weather_max_hold_hours": 48.0,
            "weather_reentry_cooldown_hours": 2.0,
            "weather_min_forecast_edge": 2.0,
            "weather_exit_forecast_edge": 1.0,
        }

    def test_entry_buy_quote_when_mid_in_range(self):
        quotes = self.strategy.generate_quotes(_mk_input(position=0.0), self.cfg)
        self.assertEqual(len(quotes), 1)
        q = quotes[0]
        self.assertEqual(q.side, "BUY")
        self.assertGreaterEqual(q.price, 0.80)
        self.assertLessEqual(q.price, 0.97)
        self.assertAlmostEqual(q.price * q.size, 50.0, places=5)

    def test_shared_default_params_match_expected_live_values(self):
        params = WeatherDecisionParams.from_mapping({})
        self.assertEqual(DEFAULT_PARAMS["weather_entry_max_price"], 0.97)
        self.assertEqual(DEFAULT_PARAMS["weather_take_profit_abs"], 0.02)
        self.assertEqual(params.entry_max, 0.97)
        self.assertEqual(params.take_profit_abs, 0.02)

    def test_entry_size_subtracts_open_buy_exposure(self):
        self.cfg.max_position = 10.0
        quotes = self.strategy.generate_quotes(
            _mk_input(position=0.0, open_buy_qty=8.5, mid=0.90, best_bid=0.89, best_ask=0.91),
            self.cfg,
        )
        self.assertEqual(len(quotes), 1)
        self.assertAlmostEqual(quotes[0].size, 1.5, places=8)

        quotes = self.strategy.generate_quotes(
            _mk_input(position=0.0, open_buy_qty=10.0, mid=0.90, best_bid=0.89, best_ask=0.91),
            self.cfg,
        )
        self.assertEqual(quotes, [])

    def test_no_entry_when_mid_outside_range(self):
        quotes = self.strategy.generate_quotes(_mk_input(mid=0.70, best_bid=0.69, best_ask=0.71), self.cfg)
        self.assertEqual(quotes, [])

    def test_no_entry_when_forecast_too_close_to_bucket(self):
        self.strategy._weather_provider = _StubWeatherProvider({"NO_TOKEN": 12.5})  # type: ignore[attr-defined]
        quotes = self.strategy.generate_quotes(_mk_input(position=0.0), self.cfg)
        self.assertEqual(quotes, [])

    def test_take_profit_exit_after_hold(self):
        self.strategy.generate_quotes(_mk_input(position=8.0, mid=0.90), self.cfg)
        self.now[0] += 7200.0
        quotes = self.strategy.generate_quotes(
            _mk_input(position=8.0, mid=0.925, best_bid=0.924, best_ask=0.926),
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

    def test_weather_exit_when_forecast_moves_into_bucket(self):
        self.strategy.generate_quotes(_mk_input(position=6.0, mid=0.90), self.cfg)
        self.strategy._weather_provider = _StubWeatherProvider({"NO_TOKEN": 12.2})  # type: ignore[attr-defined]
        self.now[0] += 60.0
        quotes = self.strategy.generate_quotes(
            _mk_input(position=6.0, mid=0.90, best_bid=0.899, best_ask=0.901),
            self.cfg,
        )
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].side, "SELL")

    def test_reentry_cooldown_blocks_immediate_rebuy(self):
        self.strategy.generate_quotes(_mk_input(position=5.0, mid=0.90), self.cfg)
        self.now[0] += 30.0
        self.strategy.generate_quotes(_mk_input(position=0.0, mid=0.90), self.cfg)
        self.now[0] += 1800.0
        quotes = self.strategy.generate_quotes(_mk_input(position=0.0, mid=0.90), self.cfg)
        self.assertEqual(quotes, [])


class TestUnifiedWeatherEdgeStrategy(unittest.IsolatedAsyncioTestCase):
    async def test_unified_adapter_uses_shared_entry_defaults(self):
        strategy = UnifiedWeatherEdgeStrategy(time_fn=lambda: 1_000.0)
        strategy._weather_provider = _StubWeatherProvider({"NO_TOKEN": 16.0})  # type: ignore[attr-defined]
        strategy._weather_target_meta = {  # type: ignore[attr-defined]
            "NO_TOKEN": {"unit": "C", "bucket_value": 12.0}
        }
        context = StrategyContext(
            instance_id="weather-test",
            strategy_key="weather_edge_v1",
            run_params={"usdc_balance": 1000.0, "max_position": 100.0},
        )
        event = MarketTickEvent(
            token_id="NO_TOKEN",
            mid_price=0.965,
            best_bid=0.964,
            best_ask=0.966,
        )

        commands = await strategy.on_market_tick(context, event)

        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].side, OrderSide.BUY)


if __name__ == "__main__":
    unittest.main()
