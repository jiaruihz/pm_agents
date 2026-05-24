import json
import tempfile
import unittest
from pathlib import Path

from src.strategies.weather_edge_v1.tools.execution_pipeline import (
    ExecutorConfig,
    PlannerConfig,
    build_trade_plan,
    execute_trade_plans,
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
            "city_pool": "t1_trading",
            "target_date": "2026-05-10",
            "unit": "C",
            "market_id": "m1",
            "market_slug": "paris-14c",
            "bracket": "14",
            "token_id": "yes-token",
            "side": "BUY_YES",
            "model_probability_yes": 0.62,
            "market_price": 0.40,
            "best_bid": 0.39,
            "best_ask": 0.41,
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
        self.assertEqual(signal["best_bid"], 0.39)
        self.assertEqual(signal["best_ask"], 0.41)
        self.assertAlmostEqual(signal["spread"], 0.02)

    def test_normalize_signal_accepts_existing_weather_signal_side(self):
        signal = normalize_signal(
            {
                **self._paper_decision(),
                "record_type": "weather_edge_signal",
                "side": "",
                "signal_side": "BUY_NO",
                "signal_id": "sig-1",
            }
        )

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal["signal_side"], "BUY_NO")

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
        self.assertEqual(plan["execution_policy"], "mid_price_core_v1")
        self.assertEqual(plan["entry_price_window"], "0.25-0.75")
        self.assertEqual(plan["city_pool"], "t1_trading")
        self.assertEqual(plan["best_bid"], 0.39)
        self.assertEqual(plan["best_ask"], 0.41)
        self.assertEqual(plan["quote_status"], "accepted")
        self.assertEqual(plan["quote_mode"], "legacy_snapshot_price")

    def test_notional_sizing_uses_configured_order_budget(self):
        signal = normalize_signal(self._paper_decision())
        assert signal is not None
        plan = build_trade_plan(signal, PlannerConfig(max_order_notional=5.0, min_edge=0.10))

        self.assertEqual(plan["status"], "accepted")
        self.assertEqual(plan["sizing_mode"], "notional")
        self.assertAlmostEqual(plan["size"], 12.5)
        self.assertAlmostEqual(plan["notional"], 5.0)

    def test_build_trade_plan_supports_fixed_share_sizing(self):
        signal = normalize_signal(self._paper_decision())
        assert signal is not None
        plan = build_trade_plan(
            signal,
            PlannerConfig(
                max_order_notional=10.0,
                sizing_mode="fixed_shares",
                fixed_order_shares=10.0,
                min_edge=0.10,
            ),
        )

        self.assertEqual(plan["status"], "accepted")
        self.assertEqual(plan["sizing_mode"], "fixed_shares")
        self.assertEqual(plan["size"], 10.0)
        self.assertEqual(plan["notional"], 4.0)

    def test_build_trade_plan_rejects_outside_mid_price_window(self):
        low_signal = normalize_signal({**self._paper_decision(), "market_price": 0.24})
        high_signal = normalize_signal({**self._paper_decision(), "market_price": 0.75})
        assert low_signal is not None
        assert high_signal is not None

        low_plan = build_trade_plan(low_signal, PlannerConfig(max_order_notional=2.0, min_edge=0.10))
        high_plan = build_trade_plan(high_signal, PlannerConfig(max_order_notional=2.0, min_edge=0.10))

        self.assertEqual(low_plan["status"], "rejected")
        self.assertEqual(low_plan["risk_reason"], "entry_price_below_min")
        self.assertEqual(high_plan["status"], "rejected")
        self.assertEqual(high_plan["risk_reason"], "entry_price_at_or_above_max")

    def test_maker_queue_policy_improves_narrow_bid_and_preserves_edge(self):
        signal = normalize_signal(self._paper_decision())
        assert signal is not None
        plan = build_trade_plan(
            signal,
            PlannerConfig(
                max_order_notional=2.0,
                min_edge=0.10,
                execution_policy="maker_queue_v1",
                tick_size=0.01,
                min_quote_edge=0.03,
                max_quote_spread=0.12,
            ),
        )

        self.assertEqual(plan["status"], "accepted")
        self.assertEqual(plan["execution_policy"], "maker_queue_v1")
        self.assertEqual(plan["quote_mode"], "improve_bid")
        self.assertAlmostEqual(plan["limit_price"], 0.40)
        self.assertGreaterEqual(plan["quote_edge"], plan["required_quote_edge"])

    def test_maker_queue_policy_rejects_wide_spread_when_edge_is_thin(self):
        signal = normalize_signal(
            {
                **self._paper_decision(),
                "model_probability_yes": 0.12,
                "best_bid": 0.30,
                "best_ask": 0.50,
                "spread": 0.20,
            }
        )
        assert signal is not None
        plan = build_trade_plan(
            signal,
            PlannerConfig(
                max_order_notional=2.0,
                min_edge=0.10,
                execution_policy="maker_queue_v1",
                tick_size=0.01,
                max_quote_spread=0.12,
            ),
        )

        self.assertEqual(plan["status"], "rejected")
        self.assertEqual(plan["risk_reason"], "quote_edge_below_required")
        self.assertEqual(plan["quote_mode"], "shade_below_bid_wide_spread")

    def test_maker_queue_policy_can_shade_below_bid_on_wide_spread(self):
        signal = normalize_signal(
            {
                **self._paper_decision(),
                "model_probability_yes": 0.90,
                "best_bid": 0.30,
                "best_ask": 0.50,
                "spread": 0.20,
            }
        )
        assert signal is not None
        plan = build_trade_plan(
            signal,
            PlannerConfig(
                max_order_notional=2.0,
                min_edge=0.10,
                execution_policy="maker_queue_v1",
                tick_size=0.01,
                max_quote_spread=0.12,
                wide_spread_shade_ticks=1,
            ),
        )

        self.assertEqual(plan["status"], "accepted")
        self.assertEqual(plan["quote_mode"], "shade_below_bid_wide_spread")
        self.assertAlmostEqual(plan["limit_price"], 0.29)

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
            self.assertEqual(result["all_plans"], 1)
            self.assertEqual(result["rejected"], 1)
            self.assertEqual(result["written"], 0)

    def test_plan_trades_missing_signal_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plans.jsonl"
            result = plan_trades(
                signal_path=Path(tmp) / "missing_signals.jsonl",
                out_path=out,
                config=PlannerConfig(max_order_notional=1.0, min_edge=0.10),
                include_rejected=False,
            )

            self.assertEqual(result["signals"], 0)
            self.assertEqual(result["plans"], 0)
            self.assertEqual(result["written"], 0)

    def test_execute_trade_plans_writes_paper_only_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = normalize_signal(self._paper_decision())
            assert signal is not None
            plan = build_trade_plan(signal, PlannerConfig(max_order_notional=2.0, min_edge=0.10))
            plans = Path(tmp) / "plans.jsonl"
            paper = Path(tmp) / "paper.jsonl"
            live = Path(tmp) / "live.jsonl"
            plans.write_text(json.dumps(plan) + "\n")

            result = execute_trade_plans(
                plan_path=plans,
                paper_out=paper,
                live_out=live,
                config=ExecutorConfig(),
            )

            self.assertEqual(result["paper_written"], 1)
            self.assertEqual(result["live_orders"], 0)
            self.assertEqual(len(paper.read_text().splitlines()), 1)
            self.assertFalse(live.exists())

    def test_execute_trade_plans_requires_live_enabled_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = normalize_signal(self._paper_decision())
            assert signal is not None
            plan = build_trade_plan(signal, PlannerConfig(max_order_notional=2.0, min_edge=0.10, live_enabled=False))
            plans = Path(tmp) / "plans.jsonl"
            paper = Path(tmp) / "paper.jsonl"
            live = Path(tmp) / "live.jsonl"
            plans.write_text(json.dumps(plan) + "\n")
            calls = []

            result = execute_trade_plans(
                plan_path=plans,
                paper_out=paper,
                live_out=live,
                config=ExecutorConfig(live=True, confirm_live=True),
                live_place_fn=lambda p: calls.append(p) or {"order_id": "live-1"},
            )

            self.assertEqual(result["live_skipped_disabled"], 1)
            self.assertEqual(calls, [])
            self.assertFalse(live.exists())

    def test_execute_trade_plans_can_call_live_for_enabled_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = normalize_signal(self._paper_decision())
            assert signal is not None
            plan = build_trade_plan(signal, PlannerConfig(max_order_notional=2.0, min_edge=0.10, live_enabled=True))
            plans = Path(tmp) / "plans.jsonl"
            paper = Path(tmp) / "paper.jsonl"
            live = Path(tmp) / "live.jsonl"
            plans.write_text(json.dumps(plan) + "\n")

            result = execute_trade_plans(
                plan_path=plans,
                paper_out=paper,
                live_out=live,
                config=ExecutorConfig(live=True, confirm_live=True),
                live_place_fn=lambda p: {"order_id": "live-1"},
            )

            self.assertEqual(result["live_written"], 1)
            rows = [json.loads(line) for line in live.read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["signal_side"], "BUY_YES")
            self.assertEqual(rows[0]["city_pool"], "t1_trading")
            self.assertEqual(rows[0]["best_bid"], 0.39)
            self.assertEqual(rows[0]["best_ask"], 0.41)
            self.assertEqual(rows[0]["requested_price"], rows[0]["limit_price"])
            self.assertEqual(rows[0]["posted_notional"], 0.0)
            self.assertEqual(rows[0]["quote_status"], "accepted")

    def test_execute_trade_plans_preserves_live_error_diagnostics(self):
        class DiagnosticError(RuntimeError):
            weather_execution_response = {
                "error_classification": "post_only_crosses_book",
                "error_reason": "post_order_failed",
                "requested_price": 0.41,
                "attempted_price": 0.40,
                "best_bid": 0.39,
                "best_ask": 0.40,
                "quote_mode": "executor_clamp",
            }

        with tempfile.TemporaryDirectory() as tmp:
            signal = normalize_signal(self._paper_decision())
            assert signal is not None
            plan = build_trade_plan(signal, PlannerConfig(max_order_notional=2.0, min_edge=0.10, live_enabled=True))
            plans = Path(tmp) / "plans.jsonl"
            paper = Path(tmp) / "paper.jsonl"
            live = Path(tmp) / "live.jsonl"
            plans.write_text(json.dumps(plan) + "\n")

            result = execute_trade_plans(
                plan_path=plans,
                paper_out=paper,
                live_out=live,
                config=ExecutorConfig(live=True, confirm_live=True),
                live_place_fn=lambda p: (_ for _ in ()).throw(DiagnosticError("crossed")),
            )

            self.assertEqual(result["live_errors"], 1)
            rows = [json.loads(line) for line in live.read_text().splitlines()]
            self.assertEqual(rows[0]["status"], "error")
            self.assertEqual(rows[0]["exchange_response"]["error_classification"], "post_only_crosses_book")
            self.assertEqual(rows[0]["exchange_response"]["attempted_price"], 0.40)
            self.assertEqual(rows[0]["best_bid"], 0.39)
            self.assertEqual(rows[0]["best_ask"], 0.40)


if __name__ == "__main__":
    unittest.main()
