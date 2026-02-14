"""Unit tests for PMM Data module."""

import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import asyncio
from src.domains.pmm.data.orderbook import best_bid_ask, mid_price, spread


class TestOrderbookUtils(unittest.TestCase):
    """Test cases for orderbook utility functions."""
    
    def test_best_bid_ask_both_present(self):
        """Test best_bid_ask when both bids and asks are present."""
        orderbook = {
            'bids': [{'price': 100, 'size': 1}, {'price': 99, 'size': 2}],
            'asks': [{'price': 101, 'size': 1}, {'price': 102, 'size': 2}]
        }
        
        result = best_bid_ask(orderbook)
        self.assertEqual(result['best_bid'], 100.0)
        self.assertEqual(result['best_ask'], 101.0)
        
    def test_best_bid_ask_only_bids(self):
        """Test best_bid_ask when only bids are present."""
        orderbook = {
            'bids': [{'price': 100, 'size': 1}, {'price': 99, 'size': 2}],
            'asks': []
        }
        
        result = best_bid_ask(orderbook)
        self.assertEqual(result['best_bid'], 100.0)
        self.assertEqual(result['best_ask'], 0.0)
        
    def test_best_bid_ask_only_asks(self):
        """Test best_bid_ask when only asks are present."""
        orderbook = {
            'bids': [],
            'asks': [{'price': 101, 'size': 1}, {'price': 102, 'size': 2}]
        }
        
        result = best_bid_ask(orderbook)
        self.assertEqual(result['best_bid'], 0.0)
        self.assertEqual(result['best_ask'], 101.0)
        
    def test_best_bid_ask_none(self):
        """Test best_bid_ask when both are empty."""
        orderbook = {
            'bids': [],
            'asks': []
        }
        
        result = best_bid_ask(orderbook)
        self.assertEqual(result['best_bid'], 0.0)
        self.assertEqual(result['best_ask'], 0.0)
        
    def test_mid_price_calculation(self):
        """Test mid_price calculation."""
        orderbook = {
            'bids': [{'price': 100, 'size': 1}],
            'asks': [{'price': 110, 'size': 1}]
        }
        
        mid = mid_price(orderbook)
        self.assertEqual(mid, 105.0)
        
    def test_mid_price_empty_book(self):
        """Test mid_price with empty book."""
        orderbook = {'bids': [], 'asks': []}
        
        mid = mid_price(orderbook)
        self.assertEqual(mid, 0.0)
        
    def test_spread_calculation(self):
        """Test spread calculation."""
        orderbook = {
            'bids': [{'price': 100, 'size': 1}],
            'asks': [{'price': 110, 'size': 1}]
        }
        
        spr = spread(orderbook)
        self.assertEqual(spr, 10.0)
        
    def test_spread_empty_book(self):
        """Test spread with empty book."""
        orderbook = {'bids': [], 'asks': []}
        
        spr = spread(orderbook)
        self.assertEqual(spr, 0.0)


# Test HTTP Client
class TestHttpClient(unittest.TestCase):
    """Test cases for HTTP client."""

    def test_client_initialization(self):
        """Test HTTP client initialization."""
        from src.domains.pmm.data.http_client import ToolServiceClient

        client = ToolServiceClient("http://test.url", "test_key")
        self.assertEqual(client.base_url, "http://test.url")
        self.assertEqual(client.api_key, "test_key")

    def test_get_balance(self):
        """Test get_balance method exists."""
        from src.domains.pmm.data.http_client import ToolServiceClient

        client = ToolServiceClient("http://test.url", "test_key")

        # Just verify the method exists and is callable
        self.assertTrue(callable(client.get_balance))


if __name__ == '__main__':
    unittest.main()