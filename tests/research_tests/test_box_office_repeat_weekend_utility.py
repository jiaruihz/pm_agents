from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts/analysis/box_office"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = spec_from_file_location(
    "box_office_repeat_utility", SCRIPT_DIR / "analyze_repeat_weekend_v1_utility.py"
)
MODULE = module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def sample_ledger():
    return MODULE.enrich(
        [
            {
                "event_id": "1", "movie": "A", "week": 2, "target_friday": "2026-01-02",
                "bracket": "<10m", "model_probability": 0.3, "historical_price_proxy": 0.02,
                "model_edge": 0.28, "fee": 0.0, "spread_stress": 0.0,
                "cost": 0.02, "won": 0, "pnl": -0.02,
            },
            {
                "event_id": "2", "movie": "B", "week": 3, "target_friday": "2026-01-09",
                "bracket": "20m+", "model_probability": 0.8, "historical_price_proxy": 0.60,
                "model_edge": 0.20, "fee": 0.0, "spread_stress": 0.0,
                "cost": 0.60, "won": 1, "pnl": 0.40,
            },
        ]
    )


def test_constant_share_and_fixed_notional_are_distinct_utilities():
    ledger = sample_ledger()
    one_share = MODULE.simulate_sizing(
        ledger, {"name": "one", "kind": "one_share"}
    )
    fixed_notional = MODULE.simulate_sizing(
        ledger,
        {"name": "fixed", "kind": "fixed_notional", "notional": 1.0, "day_cap": 1.0},
    )
    assert one_share["return"] > 0
    assert fixed_notional["return"] < 0


def test_filter_experiments_are_marked_posthoc():
    rows = MODULE.filter_experiments(sample_ledger(), "test")
    status = {row["filter"]: row["posthoc_exploratory"] for row in rows}
    assert status["all"] is False
    assert status["price_at_least_5c"] is True
