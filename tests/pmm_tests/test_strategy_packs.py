import unittest
from pathlib import Path

from src.strategies.registry import load_strategy_catalog


class TestStrategyPacks(unittest.TestCase):
    def test_strategy_pack_keys(self):
        keys = {x.strategy_key for x in load_strategy_catalog()}
        self.assertEqual(
            keys,
            {
                "single_level_v1",
                "multi_level_v1",
                "rule_lawyer",
                "smart_money_follow_v1",
                "weather_theta_no_v1",
            },
        )

    def test_required_files_exist(self):
        repo_root = Path(__file__).resolve().parents[2]
        for item in load_strategy_catalog():
            strategy_dir = repo_root / "src" / "strategies" / item.strategy_key
            required = {
                "manifest": strategy_dir / "manifest.yaml",
                "readme": strategy_dir / "README.md",
                "params": strategy_dir / "params.example.json",
                "runner": strategy_dir / "run.sh",
            }
            for label, full_path in required.items():
                self.assertTrue(full_path.exists(), f"{item.strategy_key}:{label} missing -> {full_path}")


if __name__ == "__main__":
    unittest.main()
