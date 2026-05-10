import tempfile
import unittest
from pathlib import Path

from src.strategies.weather_edge_v1.tools.weather_predict_bridge import WeatherPredictBridge


class TestWeatherPredictBridge(unittest.TestCase):
    def test_inventory_reports_missing_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = WeatherPredictBridge(Path(tmp))
            inv = bridge.inventory()
            self.assertTrue(inv["root_exists"])
            self.assertFalse(inv["ready"])
            self.assertFalse(inv["scripts"]["pm_edge_compare.py"]["exists"])

    def test_run_script_missing_is_structured_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = WeatherPredictBridge(Path(tmp))
            result = bridge.run_script("missing.py")
            self.assertFalse(result.ok)
            self.assertEqual(result.returncode, 127)
            self.assertIn("missing script", result.stderr)


if __name__ == "__main__":
    unittest.main()
