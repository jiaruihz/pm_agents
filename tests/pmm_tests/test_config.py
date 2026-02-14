"""Unit tests for PMM Config module."""

import os
import unittest
from unittest.mock import patch
from src.domains.pmm.config import PMMConfig, MarketConfig


class TestMarketConfig(unittest.TestCase):
    """Test cases for MarketConfig class."""
    
    def test_market_config_creation(self):
        """Test MarketConfig creation with token IDs."""
        token_ids = ["token1", "token2"]
        market_config = MarketConfig(token_ids=token_ids, symbol="TEST")
        
        self.assertEqual(market_config.token_ids, token_ids)
        self.assertEqual(market_config.symbol, "TEST")


class TestPMMConfig(unittest.TestCase):
    """Test cases for PMMConfig class."""
    
    def test_default_config_creation(self):
        """Test PMMConfig creation with default values."""
        config = PMMConfig()
        
        self.assertEqual(config.api_base_url, "http://localhost:8000")
        self.assertEqual(config.tick_interval_sec, 2.0)
        self.assertEqual(config.execution_mode, "live")
        self.assertEqual(config.strategy_key, "single_level_v1")
        self.assertEqual(config.base_spread, 0.04)
        self.assertEqual(config.max_position, 100.0)
        
    def test_effective_quote_levels_single_level(self):
        """Test effective_quote_levels for single level strategy."""
        config = PMMConfig(strategy_key="single_level_v1", quote_levels=3)
        
        self.assertEqual(config.effective_quote_levels(), 1)
        
    def test_effective_quote_levels_multi_level(self):
        """Test effective_quote_levels for multi level strategy."""
        config = PMMConfig(strategy_key="multi_level_v1", quote_levels=3)
        
        self.assertEqual(config.effective_quote_levels(), 3)
        
    def test_effective_quote_levels_multi_level_disabled(self):
        """Test effective_quote_levels for multi level disabled."""
        config = PMMConfig(strategy_key="single_level_v1", quote_levels=3, multi_level_quote_enabled=True)
        
        self.assertEqual(config.effective_quote_levels(), 3)
        
    @patch.dict(os.environ, {
        'PMM_API_BASE_URL': 'http://test.url',
        'PMM_BASE_SPREAD': '0.05',
        'PMM_MAX_POSITION': '200',
        'PMM_TOKEN_IDS': 'token1,token2',
        'PMM_SYMBOL': 'TEST'
    })
    def test_config_from_env(self):
        """Test PMMConfig creation from environment variables."""
        config = PMMConfig.from_env()
        
        self.assertEqual(config.api_base_url, 'http://test.url')
        self.assertEqual(config.base_spread, 0.05)
        self.assertEqual(config.max_position, 200.0)
        self.assertEqual(config.market.token_ids, ['token1', 'token2'])
        self.assertEqual(config.market.symbol, 'TEST')


if __name__ == '__main__':
    unittest.main()