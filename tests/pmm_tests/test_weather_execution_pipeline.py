import json
import tempfile
import unittest
from pathlib import Path

from src.strategies.weather_edge_v1.tools.execution_pipeline import (
    PlannerConfig,
    build_trade_plan,
    import_signals,
    normalize_signal,
    plan_trades,
)


class TestWeatherExecutionPipeline(unittest.TestCase):
    def _paper_decision(self):
        return {
            "record_type": "paper_decision",
            "paper_id": "paper-1",
            "profile": "weather_edge_b0p_v1",
            "combo": "locked+equal",
            "city": "Paris",
            "target_date": "2026-05-10",
            "unit": "C",
            "market_id": "m1",
            "market_slug": "paris-14c",
            "bracket": "14",
            "token_id": "yes-token",
            "side": "BUY_YES",
            "model_probability_yes": 0.62,
            "market_price": 0.40,
            "edge": 0.22,
            "min_edge": 0.10,
            "price_source": "orderbook_best_ask",
            "snapshot_fetched_at_utc": "2026-05-09T00:00:00+00:00",
        }

    def test_normalize_signal_from_paper_decision(self):
        signal = normalize_signal(self._paper_decision())
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal["record_type"], "weather_edge_signal")
        self.assertEqual(signal["source_id"], "paper-1")
        self.assertEqual(signal["signal_side"], "BUY_YES")
        self.assertEqual(signal["order_side"], "BUY")

    def test_import_signals_dedups(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "paper.jsonl"
            out = Path(tmp) / "signals.jsonl"
            src.write_text(json.dumps(self._paper_decision()) + "\n" + json.dumps(self._paper_decision()) + "\n")
            result = import_signals(input_paths=[src], out_path=out)
            self.assertEqual(result["valid_signals"], 1)
            self.assertEqual(result["written"], 1)
            self.assertEqual(len(out.read_text().splitlines()), 1)

    def test_build_trade_plan_passes_guard(self):
        signal = normalize_signal(self._paper_decision())
        assert signal is not None
        plan = build_trade_plan(signal, PlannerConfig(max_order_notional=2.0, min_edge=0.10))
        self.assertEqual(plan["record_type"], "weather_edge_trade_plan")
        self.assertEqual(plan["status"], "accepted")
        self.assertEqual(plan["risk_status"], "passed")
        self.assertEqual(plan["paper_enabled"], True)
        self.assertEqual(plan["live_enabled"], False)

    def test_plan_trades_can_filter_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = normalize_signal({**self._paper_decision(), "edge": 0.01})
            assert signal is not None
            src = Path(tmp) / "signals.jsonl"
            out = Path(tmp) / "plans.jsonl"
            src.write_text(json.dumps(signal) + "\n")
            result = plan_trades(
                signal_path=src,
                out_path=out,
                config=PlannerConfig(max_order_notional=1.0, min_edge=0.10),
                include_rejected=False,
            )
            self.assertEqual(result["plans"], 0)
            self.assertEqual(result["written"], 0)


if __name__ == "__main__":
    unittest.main()
