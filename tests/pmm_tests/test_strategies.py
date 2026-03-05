"""Unit tests for PMM Strategy modules."""

import unittest
from src.strategies.pmm.core.strategy_base import StrategyQuoteInput, QuoteTarget, MarketMakingStrategy
from src.strategies.pmm.core.strategy_registry import StrategyRegistry
from src.strategies.pmm.config import PMMConfig


class MockStrategy:
    """Mock strategy for testing purposes."""

    def __init__(self):
        self.key = "mock_strategy"

    def generate_quotes(self, input_data: StrategyQuoteInput, config: PMMConfig):
        """Generate mock quotes."""
        return [
            QuoteTarget(
                token_id=input_data.token_id,
                side="BUY",
                price=input_data.mid * 0.99,
                size=1.0,
                level=0,
                target_price=input_data.mid * 0.99
            ),
            QuoteTarget(
                token_id=input_data.token_id,
                side="SELL",
                price=input_data.mid * 1.01,
                size=1.0,
                level=0,
                target_price=input_data.mid * 1.01
            )
        ]


class TestStrategyBase(unittest.TestCase):
    """Test cases for strategy base classes."""

    def test_strategy_quote_input(self):
        """Test StrategyQuoteInput creation."""
        input_data = StrategyQuoteInput(
            token_id="test_token",
            mid=100.0,
            adaptive_spread=0.02,
            inventory_signal=0.1,
            best_bid=99.0,
            best_ask=101.0,
            position=10.0,
            effective_usdc_balance=1000.0,
            open_buy_qty=5.0,
            open_sell_qty=3.0
        )

        self.assertEqual(input_data.token_id, "test_token")
        self.assertEqual(input_data.mid, 100.0)
        self.assertEqual(input_data.adaptive_spread, 0.02)

    def test_quote_target(self):
        """Test QuoteTarget creation."""
        quote = QuoteTarget(
            token_id="test_token",
            side="BUY",
            price=99.0,
            size=1.0,
            level=0,
            target_price=99.0
        )

        self.assertEqual(quote.side, "BUY")
        self.assertEqual(quote.price, 99.0)
        self.assertEqual(quote.size, 1.0)
        self.assertEqual(quote.token_id, "test_token")

    def test_mock_strategy(self):
        """Test MockStrategy functionality."""
        strategy = MockStrategy()

        self.assertEqual(strategy.key, "mock_strategy")

        input_data = StrategyQuoteInput(
            token_id="test_token",
            mid=100.0,
            adaptive_spread=0.02,
            inventory_signal=0.1,
            best_bid=99.0,
            best_ask=101.0,
            position=10.0,
            effective_usdc_balance=1000.0,
            open_buy_qty=5.0,
            open_sell_qty=3.0
        )

        config = PMMConfig()
        quotes = strategy.generate_quotes(input_data, config)

        self.assertEqual(len(quotes), 2)
        self.assertEqual(quotes[0].side, "BUY")
        self.assertEqual(quotes[1].side, "SELL")
        self.assertEqual(quotes[0].token_id, "test_token")
        self.assertEqual(quotes[1].token_id, "test_token")


class TestStrategyRegistry(unittest.TestCase):
    """Test cases for StrategyRegistry."""
    
    def test_registry_register_and_get(self):
        """Test registering and retrieving strategies."""
        registry = StrategyRegistry()
        strategy = MockStrategy()
        
        # Register the strategy
        registry.register(strategy)
        
        # Retrieve the strategy
        retrieved = registry.get("mock_strategy")
        
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.key, "mock_strategy")
        
    def test_registry_available_keys(self):
        """Test getting available strategy keys."""
        registry = StrategyRegistry()
        strategy = MockStrategy()
        
        registry.register(strategy)
        
        available_keys = registry.available_keys()
        
        self.assertIn("mock_strategy", available_keys)
        
    def test_registry_get_nonexistent(self):
        """Test getting a nonexistent strategy."""
        registry = StrategyRegistry()
        
        result = registry.get("nonexistent")
        
        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()