"""Unit tests for PMM Order Manager module."""

import unittest
from pmm.execution.order_manager import OrderManager, MultiDiffDecision


class TestOrderManager(unittest.TestCase):
    """Test cases for OrderManager class."""

    def setUp(self):
        """Set up test fixtures."""
        self.order_manager = OrderManager(deadband=0.01)  # 1% deadband

    def test_should_replace_true(self):
        """Test should_replace when replacement is needed."""
        from pmm.execution.order_manager import ManagedOrder
        old_order = ManagedOrder(
            order_id="1",
            token_id="token1",
            side="BUY",
            price=100.0,
            size=1.0,
            raw={}
        )
        # Price difference is 0.02, which is greater than deadband (0.01)
        should_replace = self.order_manager.should_replace(old_order, 100.02, 1.0)
        self.assertTrue(should_replace)

    def test_should_replace_false(self):
        """Test should_replace when replacement is not needed."""
        from pmm.execution.order_manager import ManagedOrder
        old_order = ManagedOrder(
            order_id="1",
            token_id="token1",
            side="BUY",
            price=100.0,
            size=1.0,
            raw={}
        )
        # Price difference is 0.005, which is less than deadband (0.01)
        # Size difference is 0.0, which is less than max(0.01, 1.0*0.1)=0.1
        should_replace = self.order_manager.should_replace(old_order, 100.005, 1.0)
        self.assertFalse(should_replace)

    def test_side_order_ids(self):
        """Test extracting order IDs for a specific side and token."""
        open_orders = [
            {"id": "1", "asset_id": "token1", "side": "BUY", "price": 100, "size": 1},
            {"id": "2", "asset_id": "token1", "side": "SELL", "price": 101, "size": 1},
            {"id": "3", "asset_id": "token2", "side": "BUY", "price": 99, "size": 1},
            {"id": "4", "asset_id": "token1", "side": "BUY", "price": 102, "size": 1},
        ]

        buy_ids = self.order_manager.side_order_ids(open_orders, "token1", "BUY")
        expected_buy_ids = ["1", "4"]
        self.assertEqual(sorted(buy_ids), sorted(expected_buy_ids))

        sell_ids = self.order_manager.side_order_ids(open_orders, "token1", "SELL")
        expected_sell_ids = ["2"]
        self.assertEqual(sell_ids, expected_sell_ids)

    def test_diff_multi_no_targets(self):
        """Test diff_multi when no targets are provided."""
        open_orders = [
            {"id": "1", "asset_id": "token1", "side": "BUY", "price": 99.0, "size": 1.0},
        ]

        targets = []

        decision = self.order_manager.diff_multi(
            open_orders=open_orders,
            token_id="token1",
            side="BUY",
            targets=targets
        )

        # Should cancel all open orders since there are no targets
        self.assertEqual(len(decision.cancel_ids), 1)
        self.assertEqual(decision.cancel_ids[0], "1")
        self.assertEqual(len(decision.create_targets), 0)

    def test_diff_multi_with_targets(self):
        """Test diff_multi when targets match existing orders."""
        open_orders = [
            {"id": "1", "asset_id": "token1", "side": "BUY", "price": 99.0, "size": 1.0},
        ]

        targets = [
            {"price": 99.0, "size": 1.0, "level": 0},
        ]

        decision = self.order_manager.diff_multi(
            open_orders=open_orders,
            token_id="token1",
            side="BUY",
            targets=targets
        )

        # Should keep the existing order since it matches the target
        self.assertEqual(len(decision.cancel_ids), 0)
        self.assertEqual(len(decision.create_targets), 0)
        self.assertEqual(len(decision.kept_order_ids), 1)
        self.assertEqual(decision.kept_order_ids[0], "1")

    def test_diff_multi_replace_needed(self):
        """Test diff_multi when replacement is needed."""
        open_orders = [
            {"id": "1", "asset_id": "token1", "side": "BUY", "price": 98.0, "size": 1.0},
        ]

        targets = [
            {"price": 99.0, "size": 1.0, "level": 0},
        ]

        decision = self.order_manager.diff_multi(
            open_orders=open_orders,
            token_id="token1",
            side="BUY",
            targets=targets
        )

        # Should cancel old order and create new one
        self.assertEqual(len(decision.cancel_ids), 1)
        self.assertEqual(decision.cancel_ids[0], "1")
        self.assertEqual(len(decision.create_targets), 1)
        self.assertEqual(decision.create_targets[0]["price"], 99.0)
        self.assertEqual(len(decision.kept_order_ids), 0)


if __name__ == '__main__':
    unittest.main()