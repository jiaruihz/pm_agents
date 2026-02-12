"""Unit tests for Polymarket components."""

import unittest
from unittest.mock import patch, MagicMock
import sys
from io import StringIO
from agents.polymarket.polymarket import Polymarket
from agents.polymarket.gamma import GammaMarketClient
from agents.application.executor import Executor
from agents.application.trade import Trader


class TestPolymarket(unittest.TestCase):
    """Test cases for Polymarket class."""
    
    @patch('os.getenv')
    def test_init(self, mock_getenv):
        """Test Polymarket initialization."""
        mock_getenv.return_value = 'test_key'
        
        poly = Polymarket()
        
        self.assertEqual(poly.gamma_url, "https://gamma-api.polymarket.com")
        self.assertEqual(poly.clob_url, "https://clob.polymarket.com")
        self.assertEqual(poly.gamma_markets_endpoint, "https://gamma-api.polymarket.com/markets")
    
    
class TestGammaMarketClient(unittest.TestCase):
    """Test cases for GammaMarketClient class."""
    
    def test_init(self):
        """Test GammaMarketClient initialization."""
        gamma = GammaMarketClient()
        
        self.assertEqual(gamma.gamma_url, "https://gamma-api.polymarket.com")
        self.assertEqual(gamma.gamma_markets_endpoint, "https://gamma-api.polymarket.com/markets")
    
    
class TestExecutor(unittest.TestCase):
    """Test cases for Executor class."""
    
    @patch('os.getenv')
    @patch('agents.application.executor.ChatOpenAI')
    def test_init(self, mock_chat_openai, mock_getenv):
        """Test Executor initialization."""
        mock_getenv.return_value = 'test_key'
        mock_llm_instance = MagicMock()
        mock_chat_openai.return_value = mock_llm_instance
        
        executor = Executor()
        
        self.assertIsNotNone(executor.prompter)
        self.assertEqual(executor.openai_api_key, 'test_key')
        self.assertIsInstance(executor.gamma, GammaMarketClient)
    
    
class TestTrader(unittest.TestCase):
    """Test cases for Trader class."""
    
    def test_init(self):
        """Test Trader initialization."""
        # We'll mock the dependencies to avoid external API calls
        with patch.object(Polymarket, '__init__', return_value=None):
            with patch.object(GammaMarketClient, '__init__', return_value=None):
                with patch.object(Executor, '__init__', return_value=None):
                    
                    trader = Trader()
                    
                    # Since we mocked the __init__ methods, the attributes won't be set
                    # But we can still test that the constructor runs without error
                    self.assertIsNotNone(trader)
    
    
class TestUtilityFunctions(unittest.TestCase):
    """Test cases for utility functions."""
    
    def test_retain_keys(self):
        """Test the retain_keys function."""
        from agents.application.executor import retain_keys

        # Test simple case
        data = {
            'keep1': 'value1',
            'remove1': 'value2',
            'keep2': 'value3'
        }

        keys_to_keep = ['keep1', 'keep2']
        result = retain_keys(data, keys_to_keep)

        # Check that only specified keys are retained
        self.assertIn('keep1', result)
        self.assertIn('keep2', result)
        self.assertNotIn('remove1', result)
        self.assertEqual(len(result), 2)

        # Test nested case - the same filter is applied to nested dictionaries
        nested_data = {
            'outer': {
                'inner_keep': 'value1',
                'inner_remove': 'value2'
            },
            'other': 'value3'
        }

        # To preserve inner keys, they must also be in the filter
        result_nested = retain_keys(nested_data, ['outer', 'inner_keep'])
        self.assertIn('outer', result_nested)
        outer_content = result_nested['outer']
        # Since 'inner_remove' wasn't in the filter, it gets removed
        self.assertIn('inner_keep', outer_content)
        self.assertNotIn('inner_remove', outer_content)


if __name__ == '__main__':
    unittest.main()