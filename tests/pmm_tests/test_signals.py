"""Unit tests for PMM Signals module."""

import unittest
from src.domains.pmm.core.signals import (
    fair_mid as _fair_mid,
    weighted_mid as _weighted_mid,
    inventory_signal as _inventory_signal,
    realized_vol as _realized_vol,
    momentum as _momentum,
    order_flow_imbalance as _order_flow_imbalance,
    required_spread as _required_spread,
    depth_near_mid as _depth_near_mid
)
from src.platform.market_data.orderbook import mid_price, spread, best_bid_ask


class TestSignals(unittest.TestCase):
    """Test cases for PMM signals functions."""
    
    def test_mid_price(self):
        """Test mid price calculation."""
        orderbook = {
            'bids': [{'price': 100, 'size': 1}],
            'asks': [{'price': 110, 'size': 1}]
        }
        mid = mid_price(orderbook)
        self.assertEqual(mid, 105.0)

    def test_mid_price_empty_book(self):
        """Test mid price calculation with empty book."""
        orderbook = {'bids': [], 'asks': []}
        mid = mid_price(orderbook)
        self.assertEqual(mid, 0.0)

    def test_best_bid_ask(self):
        """Test best bid and ask extraction."""
        orderbook = {
            'bids': [{'price': 100, 'size': 1}],
            'asks': [{'price': 110, 'size': 1}]
        }
        best = best_bid_ask(orderbook)
        self.assertEqual(best['best_bid'], 100.0)
        self.assertEqual(best['best_ask'], 110.0)

    def test_spread(self):
        """Test spread calculation."""
        orderbook = {
            'bids': [{'price': 100, 'size': 1}],
            'asks': [{'price': 110, 'size': 1}]
        }
        spr = spread(orderbook)
        self.assertEqual(spr, 10.0)
        
    def test_fair_mid_weighted(self):
        """Test fair mid calculation with weighted mode."""
        orderbook = {
            'bids': [{'price': 100, 'size': 2}, {'price': 90, 'size': 1}],
            'asks': [{'price': 110, 'size': 1}, {'price': 120, 'size': 2}]
        }
        mid = _fair_mid(orderbook, mode='weighted')
        # Weighted mid calculation: weighted average of bid/ask sides
        self.assertGreaterEqual(mid, 90.0)
        self.assertLessEqual(mid, 120.0)
        
    def test_fair_mid_midpoint(self):
        """Test fair mid calculation with midpoint mode."""
        orderbook = {
            'bids': [{'price': 100, 'size': 1}],
            'asks': [{'price': 110, 'size': 1}]
        }
        mid = _fair_mid(orderbook, mode='midpoint')
        self.assertEqual(mid, 105.0)
        
    def test_inventory_signal(self):
        """Test inventory signal calculation."""
        # Test with balanced positions
        signal = _inventory_signal(
            token_id='token1',
            token_ids=['token1', 'token2'],
            positions={'token1': 10, 'token2': -10},
            max_position=100,
            sigmoid_k=4.0
        )
        # Signal should be close to 0 for balanced positions
        self.assertGreaterEqual(signal, -1.0)
        self.assertLessEqual(signal, 1.0)
        
    def test_realized_vol(self):
        """Test realized volatility calculation."""
        prices = [100, 101, 99, 102, 100]
        vol = _realized_vol(prices)
        # Vol should be positive
        self.assertGreaterEqual(vol, 0.0)
        
    def test_momentum(self):
        """Test momentum calculation."""
        from collections import deque
        prices = deque([90, 95, 100, 105, 110])  # Upward trend
        mom = _momentum(prices, min_points=2)
        # Momentum should be positive for upward trend
        self.assertGreaterEqual(mom, 0.0)
        
    def test_order_flow_imbalance(self):
        """Test order flow imbalance calculation."""
        orderbook = {
            'bids': [{'price': 100, 'size': 10}, {'price': 99, 'size': 5}],
            'asks': [{'price': 101, 'size': 8}, {'price': 102, 'size': 7}]
        }
        ofi = _order_flow_imbalance(orderbook, delta=0.01)
        # OFI should be between -1 and 1
        self.assertGreaterEqual(ofi, -1.0)
        self.assertLessEqual(ofi, 1.0)
        
    def test_required_spread(self):
        """Test required spread calculation."""
        from src.domains.pmm.config import PMMConfig
        config = PMMConfig()
        vol = 0.02
        inv_signal = 0.1
        req_spread = _required_spread(config, vol, inv_signal)
        # Required spread should be positive
        self.assertGreaterEqual(req_spread, 0.0)


if __name__ == '__main__':
    unittest.main()