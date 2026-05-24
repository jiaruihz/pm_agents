import json
import tempfile
import unittest
from pathlib import Path

from scripts.ops.weather_live_cycle import _build_live_contract_alerts


class TestWeatherLiveCycleContract(unittest.TestCase):
    def _write_jsonl(self, path: Path, rows):
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    def test_contract_alerts_flag_non_t1_and_bad_notional(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            signals = root / "signals.jsonl"
            plans = root / "plans.jsonl"
            live = root / "live.jsonl"
            self._write_jsonl(
                signals,
                [
                    {
                        "record_type": "weather_edge_signal",
                        "city": "Wuhan",
                        "city_pool": "t2_research",
                    }
                ],
            )
            self._write_jsonl(
                plans,
                [
                    {
                        "record_type": "weather_edge_trade_plan",
                        "status": "accepted",
                        "city": "Wuhan",
                        "city_pool": "t2_research",
                        "entry_price_window": "0.25-0.75",
                        "sizing_mode": "notional",
                        "notional": 4.2,
                    }
                ],
            )
            self._write_jsonl(
                live,
                [
                    {
                        "record_type": "weather_edge_live_order",
                        "status": "submitted",
                        "city": "Wuhan",
                        "city_pool": "t2_research",
                        "entry_price_window": "0.25-0.75",
                        "sizing_mode": "notional",
                        "notional": 4.2,
                    }
                ],
            )

            alerts = _build_live_contract_alerts(
                config={
                    "city_pool": "t1_trading",
                    "sizing_mode": "notional",
                    "max_order_notional": 5.0,
                    "min_entry_price": 0.25,
                    "max_entry_price": 0.75,
                },
                sync={"returncode": 0},
                signal_run={"returncode": 0},
                planner_run={"returncode": 0},
                signals={"signals": 1},
                planner={"accepted": 1},
                executor={"live_errors": 0},
                signal_path=signals,
                plan_path=plans,
                live_path=live,
                errors=[],
            )

            text = "\n".join(alerts)
            self.assertIn("非 t1_trading city_pool", text)
            self.assertIn("notional 偏离 5.00", text)

    def test_contract_alerts_flag_count_mismatches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            signals = root / "signals.jsonl"
            plans = root / "plans.jsonl"
            live = root / "live.jsonl"
            self._write_jsonl(
                signals,
                [
                    {
                        "record_type": "weather_edge_signal",
                        "city": "Miami",
                        "city_pool": "t1_trading",
                    }
                ],
            )
            self._write_jsonl(
                plans,
                [
                    {
                        "record_type": "weather_edge_trade_plan",
                        "status": "accepted",
                        "city": "Miami",
                        "city_pool": "t1_trading",
                        "entry_price_window": "0.25-0.75",
                        "sizing_mode": "notional",
                        "notional": 5.0,
                    }
                ],
            )
            live.write_text("", encoding="utf-8")

            alerts = _build_live_contract_alerts(
                config={
                    "city_pool": "t1_trading",
                    "sizing_mode": "notional",
                    "max_order_notional": 5.0,
                    "min_entry_price": 0.25,
                    "max_entry_price": 0.75,
                },
                sync={"returncode": 0},
                signal_run={"returncode": 0},
                planner_run={"returncode": 0},
                signals={"signals": 2},
                planner={"signals": 3, "accepted": 2},
                executor={"live_errors": 0},
                signal_path=signals,
                plan_path=plans,
                live_path=live,
                errors=[],
            )

            text = "\n".join(alerts)
            self.assertIn("signals summary/file count mismatch", text)
            self.assertIn("planner input count mismatch", text)
            self.assertIn("planner accepted/file count mismatch", text)


if __name__ == "__main__":
    unittest.main()
