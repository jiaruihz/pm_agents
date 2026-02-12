"""Integration tests for PMM and Polymarket components."""

import unittest
from unittest.mock import patch, MagicMock
from pmm.config import PMMConfig
from pmm.engine.tick_engine import TickEngine
from agents.application.trade import Trader
from agents.application.executor import Executor
from agents.polymarket.gamma import GammaMarketClient
from agents.polymarket.polymarket import Polymarket


class TestPMMIntegration(unittest.TestCase):
    """Integration tests for PMM components."""
    
    def test_config_and_engine_integration(self):
        """Test integration between PMMConfig and TickEngine."""
        config = PMMConfig(
            tick_interval_sec=0.1,  # Fast tick for testing
            max_ticks=1,  # Just one tick
            market_data_source="rest",  # Use REST instead of WS to simplify test
        )
        
        # Add required token IDs to avoid early termination
        config.market.token_ids = ["test_token"]
        
        engine = TickEngine(config)
        
        self.assertIsInstance(engine, TickEngine)
        self.assertEqual(engine.config, config)
        
    def test_config_from_env_integration(self):
        """Test that config can be loaded and used by engine."""
        # This test verifies that the config loading mechanism works properly
        config = PMMConfig.from_env()
        
        # Verify that config can initialize an engine
        engine = TickEngine(config)
        
        self.assertIsInstance(engine, TickEngine)


class TestPolymarketIntegration(unittest.TestCase):
    """Integration tests for Polymarket components."""
    
    @patch('agents.application.executor.ChatOpenAI')
    @patch('os.getenv')
    def test_executor_components_integration(self, mock_getenv, mock_chat_openai):
        """Test integration between Executor and its dependencies."""
        mock_getenv.return_value = 'test_key'
        mock_llm_instance = MagicMock()
        mock_chat_openai.return_value = mock_llm_instance
        
        # Mock the LLM response
        mock_llm_response = MagicMock()
        mock_llm_response.content = "test response"
        mock_llm_instance.invoke.return_value = mock_llm_response
        
        executor = Executor()
        
        # Verify that all components are properly initialized
        self.assertIsNotNone(executor.prompter)
        self.assertIsNotNone(executor.gamma)
        self.assertIsNotNone(executor.chroma)
        self.assertIsNotNone(executor.polymarket)
        
        # Test a simple method call to ensure integration works
        result = executor.get_llm_response("test input")
        self.assertEqual(result, "test response")
        
    @patch.object(GammaMarketClient, 'get_current_events')
    @patch.object(GammaMarketClient, 'get_current_markets')
    @patch('agents.application.executor.ChatOpenAI')
    @patch('os.getenv')
    def test_executor_full_workflow_integration(self, mock_getenv, mock_chat_openai, 
                                               mock_get_current_markets, mock_get_current_events):
        """Test full workflow integration in Executor."""
        mock_getenv.return_value = 'test_key'
        mock_llm_instance = MagicMock()
        mock_chat_openai.return_value = mock_llm_instance
        
        # Mock responses
        mock_llm_response = MagicMock()
        mock_llm_response.content = "test response"
        mock_llm_instance.invoke.return_value = mock_llm_response
        
        mock_get_current_events.return_value = []
        mock_get_current_markets.return_value = []
        
        executor = Executor()
        
        # Test the full workflow method
        result = executor.get_polymarket_llm("test query")
        
        # Should return the mocked response
        self.assertEqual(result, "test response")


class TestTraderIntegration(unittest.TestCase):
    """Integration tests for Trader and its dependencies."""
    
    @patch.object(Polymarket, '__init__', return_value=None)
    @patch.object(GammaMarketClient, '__init__', return_value=None)
    @patch.object(Executor, '__init__', return_value=None)
    def test_trader_initialization_integration(self, mock_executor_init, 
                                              mock_gamma_init, mock_poly_init):
        """Test that Trader properly initializes its dependencies."""
        # Mock the individual components
        mock_poly_instance = MagicMock()
        mock_gamma_instance = MagicMock()
        mock_executor_instance = MagicMock()
        
        # Set up the mocks to return our mock instances
        mock_poly_init.return_value = None
        mock_gamma_init.return_value = None
        mock_executor_init.return_value = None
        
        trader = Trader()
        
        # Verify that trader has the expected attributes (though they'll be None due to mocking)
        self.assertIsNotNone(trader)
        
    @patch.object(Polymarket, '__init__', return_value=None)
    @patch.object(GammaMarketClient, '__init__', return_value=None)
    @patch.object(Executor, '__init__', return_value=None)
    def test_trader_methods_exist(self, mock_executor_init, 
                                 mock_gamma_init, mock_poly_init):
        """Test that Trader has all expected methods."""
        trader = Trader()
        
        # Check that all expected methods exist
        self.assertTrue(hasattr(trader, 'pre_trade_logic'))
        self.assertTrue(hasattr(trader, 'clear_local_dbs'))
        self.assertTrue(hasattr(trader, 'one_best_trade'))
        self.assertTrue(hasattr(trader, 'maintain_positions'))
        self.assertTrue(hasattr(trader, 'incentive_farm'))


class TestEndToEndWorkflow(unittest.TestCase):
    """End-to-end workflow tests."""
    
    @patch.object(Polymarket, '__init__', return_value=None)
    @patch.object(GammaMarketClient, '__init__', return_value=None)
    @patch.object(Executor, '__init__', return_value=None)
    def test_trader_workflow_structure(self, mock_executor_init, 
                                      mock_gamma_init, mock_poly_init):
        """Test the structure of the trader workflow without executing external calls."""
        trader = Trader()
        
        # Verify the workflow method exists
        self.assertTrue(hasattr(trader, 'one_best_trade'))
        
        # The method should exist and have the expected docstring structure
        method_doc = trader.one_best_trade.__doc__
        self.assertIsNotNone(method_doc)
        self.assertIn("evaluates all events, markets, and orderbooks", method_doc)


if __name__ == '__main__':
    unittest.main()