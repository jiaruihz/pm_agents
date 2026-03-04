import unittest
from pathlib import Path

from src.domains.pmm.strategy_packs.registry import STRATEGY_PACKS


class TestStrategyPacks(unittest.TestCase):
    def test_strategy_pack_keys(self):
        keys = {x.key for x in STRATEGY_PACKS}
        self.assertEqual(
            keys,
            {
                "single_level_v1",
                "multi_level_v1",
                "rule_lawyer_v1",
                "smart_money_follow_v1",
                "weather_theta_no_v1",
            },
        )

    def test_required_files_exist(self):
        repo_root = Path(__file__).resolve().parents[2]
        for pack in STRATEGY_PACKS:
            required = pack.required_files()
            for label, rel_path in required.items():
                full = repo_root / rel_path
                self.assertTrue(full.exists(), f"{pack.key}:{label} missing -> {rel_path}")


if __name__ == "__main__":
    unittest.main()
