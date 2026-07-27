import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.strategies.weather_edge_v1.execution.lifecycle import attach_data_update_lifecycle
from src.strategies.weather_edge_v1.tools.execution_pipeline import (
    ExecutorConfig,
    PlannerConfig,
    build_trade_plan,
    build_trade_plans_for_signal,
    build_execution_comparison_plans,
    cancel_expired_live_orders,
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

    def test_maker_queue_v2_policy_improves_narrow_bid_and_preserves_edge(self):
        signal = normalize_signal(self._paper_decision())
        assert signal is not None
        plan = build_trade_plan(
            signal,
            PlannerConfig(
                max_order_notional=2.0,
                min_edge=0.10,
                execution_policy="maker_queue_v2",
                tick_size=0.01,
                min_quote_edge=0.03,
                max_quote_spread=0.12,
            ),
        )

        self.assertEqual(plan["status"], "accepted")
        self.assertEqual(plan["execution_policy"], "maker_queue_v2")
        self.assertEqual(plan["quote_mode"], "improve_bid")
        self.assertAlmostEqual(plan["limit_price"], 0.40)
        self.assertGreaterEqual(plan["quote_edge"], plan["required_quote_edge"])

    def test_execution_profiles_build_comparable_taker_and_single_side_maker_plans(self):
        now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
        signal = normalize_signal(
            attach_data_update_lifecycle(
                self._paper_decision(),
                data_source="metar",
                data_epoch_ref="metar:LFPG:2026-07-16T12:00Z",
                data_epoch_ts_utc=now,
                next_data_update_due_utc=now + timedelta(minutes=30),
                cancel_buffer_sec=90,
                now=now,
            )
        )
        assert signal is not None

        plans = build_execution_comparison_plans(
            signal,
            PlannerConfig(
                max_order_notional=2.0,
                min_edge=0.10,
                min_entry_price=0.25,
                max_entry_price=0.75,
                tick_size=0.01,
                min_quote_edge=0.03,
            ),
        )

        self.assertEqual(len(plans), 2)
        by_profile = {plan["execution_profile"]: plan for plan in plans}
        taker = by_profile["taker_now_v1"]
        maker = by_profile["single_side_maker_v1"]
        self.assertEqual(taker["execution_policy"], "taker_top_ask_v1")
        self.assertEqual(taker["order_lifecycle_policy"], "taker_now")
        self.assertFalse(taker["maker_only"])
        self.assertEqual(taker["limit_price"], 0.41)
        self.assertEqual(maker["execution_policy"], "maker_queue_v2")
        self.assertEqual(maker["order_lifecycle_policy"], "maker_until_data_update")
        self.assertTrue(maker["maker_only"])
        self.assertEqual(maker["limit_price"], 0.40)
        self.assertEqual(maker["cancel_buffer_sec"], 90)
        self.assertEqual(maker["cancel_reason"], "pre_data_update")
        self.assertEqual(taker["comparison_group_id"], maker["comparison_group_id"])
        self.assertFalse(taker["live_enabled"])
        self.assertFalse(maker["live_enabled"])

    def test_single_side_maker_profile_allows_cancel_buffer_override(self):
        signal = normalize_signal(self._paper_decision())
        assert signal is not None
        plan = build_trade_plan(
            signal,
            PlannerConfig(
                max_order_notional=2.0,
                min_edge=0.10,
                execution_profile="single_side_maker_v1",
                cancel_buffer_sec=180,
            ),
        )

        self.assertEqual(plan["execution_policy"], "maker_queue_v2")
        self.assertEqual(plan["order_lifecycle_policy"], "maker_until_data_update")
        self.assertEqual(plan["cancel_buffer_sec"], 180)

    def test_strategy_specific_multi_leg_profile_is_not_silently_flattened(self):
        signal = normalize_signal(self._paper_decision())
        assert signal is not None
        with self.assertRaisesRegex(ValueError, "multi-leg orchestrator"):
            build_trade_plan(
                signal,
                PlannerConfig(
                    max_order_notional=2.0,
                    min_edge=0.10,
                    execution_profile="split_taker_maker_chase_v1",
                ),
            )

    def test_maker_queue_v2_policy_rejects_wide_spread_when_edge_is_thin(self):
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
                execution_policy="maker_queue_v2",
                tick_size=0.01,
                max_quote_spread=0.12,
            ),
        )

        self.assertEqual(plan["status"], "rejected")
        self.assertEqual(plan["risk_reason"], "quote_edge_below_required")
        self.assertEqual(plan["quote_mode"], "improve_bid")

    def test_maker_queue_v2_policy_improves_wide_spread_when_edge_is_sufficient(self):
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
                execution_policy="maker_queue_v2",
                tick_size=0.01,
                max_quote_spread=0.12,
                wide_spread_shade_ticks=1,
            ),
        )

        self.assertEqual(plan["status"], "accepted")
        self.assertEqual(plan["quote_mode"], "improve_bid")
        self.assertAlmostEqual(plan["limit_price"], 0.31)

    def test_mid_price_core_v2_splits_low_price_high_edge_order(self):
        signal = normalize_signal(
            {
                **self._paper_decision(),
                "market_price": 0.30,
                "best_bid": 0.29,
                "best_ask": 0.31,
                "model_probability_yes": 0.48,
                "edge": 0.18,
            }
        )
        assert signal is not None
        plans = build_trade_plans_for_signal(
            signal,
            PlannerConfig(
                max_order_notional=5.0,
                min_edge=0.10,
                execution_policy="mid_price_core_v2",
                tick_size=0.01,
                split_min_edge=0.10,
                taker_fraction=0.50,
            ),
        )

        self.assertEqual(len(plans), 2)
        by_role = {plan["child_order_role"]: plan for plan in plans}
        self.assertEqual(set(by_role), {"taker", "maker"})
        self.assertEqual(by_role["taker"]["status"], "accepted")
        self.assertEqual(by_role["maker"]["status"], "accepted")
        self.assertFalse(by_role["taker"]["maker_only"])
        self.assertTrue(by_role["maker"]["maker_only"])
        self.assertEqual(by_role["taker"]["quote_mode"], "split_taker_cross_ask")
        self.assertAlmostEqual(by_role["taker"]["limit_price"], 0.31)
        self.assertAlmostEqual(by_role["maker"]["limit_price"], 0.30)
        self.assertAlmostEqual(by_role["taker"]["notional"], 2.5, places=5)
        self.assertAlmostEqual(by_role["maker"]["notional"], 2.5, places=5)
        self.assertNotEqual(by_role["taker"]["plan_id"], by_role["maker"]["plan_id"])

    def test_mid_price_core_v2_high_band_requires_stronger_edge_and_reduces_size(self):
        weak_signal = normalize_signal(
            {
                **self._paper_decision(),
                "market_price": 0.60,
                "best_bid": 0.59,
                "best_ask": 0.61,
                "model_probability_yes": 0.70,
                "edge": 0.10,
            }
        )
        strong_signal = normalize_signal(
            {
                **self._paper_decision(),
                "market_price": 0.60,
                "best_bid": 0.59,
                "best_ask": 0.61,
                "model_probability_yes": 0.80,
                "edge": 0.20,
            }
        )
        assert weak_signal is not None
        assert strong_signal is not None

        weak_plan = build_trade_plan(
            weak_signal,
            PlannerConfig(max_order_notional=5.0, min_edge=0.10, execution_policy="mid_price_core_v2"),
        )
        strong_plan = build_trade_plan(
            strong_signal,
            PlannerConfig(max_order_notional=5.0, min_edge=0.10, execution_policy="mid_price_core_v2"),
        )

        self.assertEqual(weak_plan["status"], "rejected")
        self.assertEqual(weak_plan["risk_reason"], "high_band_edge_below_min")
        self.assertEqual(strong_plan["status"], "accepted")
        self.assertEqual(strong_plan["quote_mode"], "high_band_shade_narrow")
        self.assertAlmostEqual(strong_plan["order_notional_cap"], 3.0)
        self.assertAlmostEqual(strong_plan["notional"], 3.0, places=5)

    def test_mid_price_core_v2_defers_split_when_no_live_book(self):
        # Production signals carry no two-sided book (bid=0/ask=0). The split
        # decision must still happen at planner stage from market_price+edge,
        # emitting two deferred child plans for the executor to re-price.
        signal = normalize_signal(
            {
                **self._paper_decision(),
                "market_price": 0.30,
                "best_bid": 0.0,
                "best_ask": 0.0,
                "model_probability_yes": 0.48,
                "edge": 0.18,
            }
        )
        assert signal is not None
        plans = build_trade_plans_for_signal(
            signal,
            PlannerConfig(
                max_order_notional=5.0,
                min_edge=0.10,
                execution_policy="mid_price_core_v2",
                tick_size=0.01,
                split_min_edge=0.10,
                taker_fraction=0.50,
            ),
        )

        self.assertEqual(len(plans), 2)
        by_role = {plan["child_order_role"]: plan for plan in plans}
        self.assertEqual(set(by_role), {"taker", "maker"})
        for role in ("taker", "maker"):
            self.assertEqual(by_role[role]["status"], "accepted")
            self.assertEqual(by_role[role]["quote_mode"], "defer_to_executor")
            self.assertEqual(
                by_role[role]["quote_reason"], "defer_to_executor_missing_two_sided_book"
            )
        self.assertFalse(by_role["taker"]["maker_only"])
        self.assertTrue(by_role["maker"]["maker_only"])
        self.assertAlmostEqual(by_role["taker"]["notional_fraction"], 0.50)
        self.assertAlmostEqual(by_role["maker"]["notional_fraction"], 0.50)
        self.assertAlmostEqual(by_role["taker"]["notional"], 2.5, places=5)
        self.assertAlmostEqual(by_role["maker"]["notional"], 2.5, places=5)
        self.assertNotEqual(by_role["taker"]["plan_id"], by_role["maker"]["plan_id"])

    def test_mid_price_core_v2_no_book_low_band_below_split_edge_single_defer(self):
        # Low band but edge under split_min_edge → no split, single deferred maker.
        signal = normalize_signal(
            {
                **self._paper_decision(),
                "market_price": 0.30,
                "best_bid": 0.0,
                "best_ask": 0.0,
                "model_probability_yes": 0.38,
                "edge": 0.11,
            }
        )
        assert signal is not None
        plans = build_trade_plans_for_signal(
            signal,
            PlannerConfig(
                max_order_notional=5.0,
                min_edge=0.10,
                execution_policy="mid_price_core_v2",
                tick_size=0.01,
                split_min_edge=0.10,
                taker_fraction=0.50,
            ),
        )
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["child_order_role"], "single")
        self.assertEqual(plans[0]["status"], "accepted")
        self.assertEqual(plans[0]["quote_mode"], "defer_to_executor")
        self.assertTrue(plans[0]["maker_only"])

    def test_mid_price_core_v2_no_book_high_band_edge_gate(self):
        weak = normalize_signal(
            {
                **self._paper_decision(),
                "market_price": 0.60,
                "best_bid": 0.0,
                "best_ask": 0.0,
                "model_probability_yes": 0.70,
                "edge": 0.10,
            }
        )
        strong = normalize_signal(
            {
                **self._paper_decision(),
                "market_price": 0.60,
                "best_bid": 0.0,
                "best_ask": 0.0,
                "model_probability_yes": 0.80,
                "edge": 0.20,
            }
        )
        assert weak is not None
        assert strong is not None
        weak_plans = build_trade_plans_for_signal(
            weak,
            PlannerConfig(max_order_notional=5.0, min_edge=0.10, execution_policy="mid_price_core_v2"),
        )
        strong_plans = build_trade_plans_for_signal(
            strong,
            PlannerConfig(max_order_notional=5.0, min_edge=0.10, execution_policy="mid_price_core_v2"),
        )

        self.assertEqual(len(weak_plans), 1)
        self.assertEqual(weak_plans[0]["status"], "rejected")
        self.assertEqual(weak_plans[0]["risk_reason"], "high_band_edge_below_min")

        self.assertEqual(len(strong_plans), 1)
        self.assertEqual(strong_plans[0]["status"], "accepted")
        self.assertEqual(strong_plans[0]["child_order_role"], "single")
        self.assertEqual(strong_plans[0]["quote_mode"], "defer_to_executor")
        self.assertAlmostEqual(strong_plans[0]["size_multiplier"], 0.60)
        self.assertAlmostEqual(strong_plans[0]["order_notional_cap"], 3.0)

    def test_mid_price_core_v2_high_band_floors_to_min_order_shares(self):
        # High band $3 budget at price 0.65 -> 4.615 shares < 5-share minimum.
        # Floor up to 5 shares and bump order_notional_cap to match (5 * 0.65).
        strong = normalize_signal(
            {
                **self._paper_decision(),
                "market_price": 0.65,
                "best_bid": 0.0,
                "best_ask": 0.0,
                "model_probability_yes": 0.85,
                "edge": 0.20,
            }
        )
        assert strong is not None
        plans = build_trade_plans_for_signal(
            strong,
            PlannerConfig(
                max_order_notional=5.0,
                min_edge=0.10,
                min_order_shares=5.0,
                execution_policy="mid_price_core_v2",
            ),
        )
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["status"], "accepted")
        self.assertEqual(plans[0]["child_order_role"], "single")
        self.assertAlmostEqual(plans[0]["size"], 5.0)
        self.assertAlmostEqual(plans[0]["order_notional_cap"], 3.25)

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
            plan = {
                **plan,
                "entry_profile": "peak_forming_micro",
                "running_max_obs_utc": "2026-06-18T11:20:00+00:00",
                "snapshot_ts_utc": "2026-06-18T11:30:53Z",
            }
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
            self.assertEqual(rows[0]["entry_profile"], "peak_forming_micro")
            self.assertEqual(rows[0]["running_max_obs_utc"], "2026-06-18T11:20:00+00:00")
            self.assertEqual(rows[0]["snapshot_ts_utc"], "2026-06-18T11:30:53Z")

    def test_execute_trade_plans_skips_existing_live_signal_before_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = normalize_signal(self._paper_decision())
            assert signal is not None
            plan = build_trade_plan(signal, PlannerConfig(max_order_notional=2.0, min_edge=0.10, live_enabled=True))
            plans = Path(tmp) / "plans.jsonl"
            paper = Path(tmp) / "paper.jsonl"
            live = Path(tmp) / "live.jsonl"
            plans.write_text(json.dumps(plan) + "\n")
            live.write_text(json.dumps({"status": "submitted", "signal_id": plan["signal_id"]}) + "\n")
            calls = []

            result = execute_trade_plans(
                plan_path=plans,
                paper_out=paper,
                live_out=live,
                config=ExecutorConfig(live=True, confirm_live=True),
                live_place_fn=lambda p: calls.append(p) or {"order_id": "should-not-place"},
            )

            self.assertEqual(result["live_skipped_existing_signal"], 1)
            self.assertEqual(result["live_written"], 0)
            self.assertEqual(calls, [])
            self.assertEqual(len(live.read_text().splitlines()), 1)

    def test_execute_trade_plans_skips_existing_live_opportunity_before_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = normalize_signal(self._paper_decision())
            assert signal is not None
            plan = build_trade_plan(signal, PlannerConfig(max_order_notional=2.0, min_edge=0.10, live_enabled=True))
            plan = {
                **plan,
                "signal_id": "new-snapshot-signal-id",
                "opportunity_id": "stable-city-date-token-expression",
            }
            plans = Path(tmp) / "plans.jsonl"
            paper = Path(tmp) / "paper.jsonl"
            live = Path(tmp) / "live.jsonl"
            plans.write_text(json.dumps(plan) + "\n")
            live.write_text(
                json.dumps(
                    {
                        "status": "submitted",
                        "signal_id": "old-snapshot-signal-id",
                        "opportunity_id": "stable-city-date-token-expression",
                    }
                )
                + "\n"
            )
            calls = []

            result = execute_trade_plans(
                plan_path=plans,
                paper_out=paper,
                live_out=live,
                config=ExecutorConfig(live=True, confirm_live=True),
                live_place_fn=lambda p: calls.append(p) or {"order_id": "should-not-place"},
            )

            self.assertEqual(result["live_skipped_existing_signal"], 0)
            self.assertEqual(result["live_skipped_existing_opportunity"], 1)
            self.assertEqual(result["live_written"], 0)
            self.assertEqual(calls, [])
            self.assertEqual(len(live.read_text().splitlines()), 1)

    def test_execute_trade_plans_skips_duplicate_live_signal_in_same_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = normalize_signal(self._paper_decision())
            assert signal is not None
            plan = build_trade_plan(signal, PlannerConfig(max_order_notional=2.0, min_edge=0.10, live_enabled=True))
            plans = Path(tmp) / "plans.jsonl"
            paper = Path(tmp) / "paper.jsonl"
            live = Path(tmp) / "live.jsonl"
            plans.write_text(json.dumps(plan) + "\n" + json.dumps(plan) + "\n")
            calls = []

            result = execute_trade_plans(
                plan_path=plans,
                paper_out=paper,
                live_out=live,
                config=ExecutorConfig(live=True, confirm_live=True),
                live_place_fn=lambda p: calls.append(p) or {"order_id": "live-1"},
            )

            self.assertEqual(result["live_written"], 1)
            self.assertEqual(result["live_skipped_existing_signal"], 1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(live.read_text().splitlines()), 1)

    def test_execute_trade_plans_allows_explicit_lifecycle_duplicate_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = normalize_signal(self._paper_decision())
            assert signal is not None
            plan = build_trade_plan(signal, PlannerConfig(max_order_notional=2.0, min_edge=0.10, live_enabled=True))
            plan = {
                **plan,
                "allow_duplicate_signal_id": True,
                "execution_action": "maker_lifecycle_reprice_maker",
                "cancel_before_order_id": "old-order-1",
                "source_order_id": "old-order-1",
                "source_execution_id": "old-exec-1",
            }
            plans = Path(tmp) / "plans.jsonl"
            paper = Path(tmp) / "paper.jsonl"
            live = Path(tmp) / "live.jsonl"
            plans.write_text(json.dumps(plan) + "\n")
            live.write_text(json.dumps({"status": "submitted", "signal_id": plan["signal_id"]}) + "\n")
            calls = []
            cancels = []

            result = execute_trade_plans(
                plan_path=plans,
                paper_out=paper,
                live_out=live,
                config=ExecutorConfig(live=True, confirm_live=True),
                live_place_fn=lambda p: calls.append(p) or {"order_id": "new-order-1"},
                live_cancel_fn=lambda order_id: cancels.append(order_id) or {"cancel": {"canceled": [order_id], "not_canceled": {}}},
            )

            self.assertEqual(result["live_skipped_existing_signal"], 0)
            self.assertEqual(result["live_written"], 1)
            self.assertEqual(cancels, ["old-order-1"])
            self.assertEqual(len(calls), 1)
            rows = [json.loads(line) for line in live.read_text().splitlines()]
            self.assertEqual(rows[-1]["execution_action"], "maker_lifecycle_reprice_maker")
            self.assertEqual(rows[-1]["source_order_id"], "old-order-1")
            self.assertEqual(rows[-1]["exchange_response"]["pre_place_cancel_order_id"], "old-order-1")

    def test_execute_trade_plans_supports_cancel_only_without_placing_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = normalize_signal(self._paper_decision())
            assert signal is not None
            plan = build_trade_plan(signal, PlannerConfig(max_order_notional=2.0, min_edge=0.10, live_enabled=True))
            plan = {
                **plan,
                "allow_duplicate_signal_id": True,
                "execution_action": "maker_lifecycle_cancel_stale_thesis",
                "cancel_only": True,
                "cancel_before_order_id": "old-order-1",
                "source_order_id": "old-order-1",
                "paper_enabled": False,
            }
            plans = Path(tmp) / "plans.jsonl"
            paper = Path(tmp) / "paper.jsonl"
            live = Path(tmp) / "live.jsonl"
            plans.write_text(json.dumps(plan) + "\n")
            cancels = []

            result = execute_trade_plans(
                plan_path=plans,
                paper_out=paper,
                live_out=live,
                config=ExecutorConfig(live=True, confirm_live=True),
                live_place_fn=None,
                live_cancel_fn=lambda order_id: cancels.append(order_id)
                or {"cancel": {"canceled": [order_id], "not_canceled": {}}},
            )

            self.assertEqual(cancels, ["old-order-1"])
            self.assertEqual(result["live_written"], 1)
            row = json.loads(live.read_text().splitlines()[-1])
            self.assertEqual(row["status"], "cancelled")
            self.assertEqual(row["execution_action"], "maker_lifecycle_cancel_stale_thesis")

    def test_cancel_only_terminal_unknown_is_not_retried_as_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = normalize_signal(self._paper_decision())
            assert signal is not None
            plan = build_trade_plan(signal, PlannerConfig(max_order_notional=2.0, min_edge=0.10, live_enabled=True))
            plan = {
                **plan,
                "allow_duplicate_signal_id": True,
                "execution_action": "maker_lifecycle_cancel_stale_thesis",
                "cancel_only": True,
                "cancel_before_order_id": "old-order-1",
                "source_order_id": "old-order-1",
                "paper_enabled": False,
            }
            plans = Path(tmp) / "plans.jsonl"
            live = Path(tmp) / "live.jsonl"
            plans.write_text(json.dumps(plan) + "\n")

            result = execute_trade_plans(
                plan_path=plans,
                paper_out=Path(tmp) / "paper.jsonl",
                live_out=live,
                config=ExecutorConfig(live=True, confirm_live=True),
                live_place_fn=None,
                live_cancel_fn=lambda order_id: {
                    "cancel": {
                        "canceled": [],
                        "not_canceled": {order_id: "order can't be found - already canceled or matched"},
                    }
                },
            )

            self.assertEqual(result["live_guard_blocks"], 0)
            row = json.loads(live.read_text().splitlines()[-1])
            self.assertEqual(row["status"], "cancelled")
            self.assertEqual(
                row["exchange_response"]["error_classification"],
                "source_order_terminal_no_cancel_needed",
            )

    def test_live_order_persists_decision_snapshot_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = normalize_signal(self._paper_decision())
            assert signal is not None
            plan = build_trade_plan(signal, PlannerConfig(max_order_notional=2.0, min_edge=0.10, live_enabled=True))
            plan["decision_snapshot_ts_utc"] = "2026-07-14T15:31:02Z"
            plans = Path(tmp) / "plans.jsonl"
            paper = Path(tmp) / "paper.jsonl"
            live = Path(tmp) / "live.jsonl"
            plans.write_text(json.dumps(plan) + "\n")

            execute_trade_plans(
                plan_path=plans,
                paper_out=paper,
                live_out=live,
                config=ExecutorConfig(live=True, confirm_live=True),
                live_place_fn=lambda _plan: {"order_id": "live-1"},
            )

            row = json.loads(live.read_text().splitlines()[-1])
            self.assertEqual(row["decision_snapshot_ts_utc"], "2026-07-14T15:31:02Z")

    def test_execute_trade_plans_blocks_lifecycle_replacement_when_cancel_not_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = normalize_signal(self._paper_decision())
            assert signal is not None
            plan = build_trade_plan(signal, PlannerConfig(max_order_notional=2.0, min_edge=0.10, live_enabled=True))
            plan = {
                **plan,
                "allow_duplicate_signal_id": True,
                "execution_action": "maker_lifecycle_reprice_maker",
                "cancel_before_order_id": "old-order-1",
                "source_order_id": "old-order-1",
                "source_execution_id": "old-exec-1",
            }
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
                live_place_fn=lambda p: calls.append(p) or {"order_id": "new-order-1"},
                live_cancel_fn=lambda order_id: {
                    "cancel": {
                        "canceled": [],
                        "not_canceled": {order_id: "temporary cancel failure"},
                    }
                },
            )

            self.assertEqual(result["live_written"], 1)
            self.assertEqual(result["live_guard_blocks"], 1)
            self.assertEqual(calls, [])
            rows = [json.loads(line) for line in live.read_text().splitlines()]
            self.assertEqual(rows[-1]["status"], "blocked")
            self.assertEqual(rows[-1]["exchange_response"]["error_classification"], "pre_place_cancel_not_confirmed")
            self.assertIn("temporary cancel failure", rows[-1]["exchange_response"]["error_reason"])

    def test_terminal_unknown_source_order_never_places_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = normalize_signal(self._paper_decision())
            assert signal is not None
            plan = build_trade_plan(signal, PlannerConfig(max_order_notional=2.0, min_edge=0.10, live_enabled=True))
            plan = {
                **plan,
                "allow_duplicate_signal_id": True,
                "execution_action": "maker_lifecycle_reprice_maker",
                "cancel_before_order_id": "old-order-1",
                "source_order_id": "old-order-1",
                "replacement_requires_order_state": True,
            }
            plans = Path(tmp) / "plans.jsonl"
            live = Path(tmp) / "live.jsonl"
            plans.write_text(json.dumps(plan) + "\n")
            placed = []

            result = execute_trade_plans(
                plan_path=plans,
                paper_out=Path(tmp) / "paper.jsonl",
                live_out=live,
                config=ExecutorConfig(live=True, confirm_live=True),
                live_place_fn=lambda child: placed.append(child) or {"order_id": "replacement"},
                live_cancel_fn=lambda order_id: {
                    "cancel": {
                        "canceled": [],
                        "not_canceled": {order_id: "order can't be found - already canceled or matched"},
                    }
                },
            )

            self.assertEqual(placed, [])
            self.assertEqual(result["live_guard_blocks"], 1)
            row = json.loads(live.read_text().splitlines()[-1])
            self.assertEqual(row["status"], "cancelled")
            self.assertEqual(
                row["exchange_response"]["error_classification"],
                "source_order_terminal_no_replacement",
            )

    def test_execute_trade_plans_blocks_partial_fill_replacement_below_exchange_minimum(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = normalize_signal(self._paper_decision())
            assert signal is not None
            plan = build_trade_plan(signal, PlannerConfig(max_order_notional=2.0, min_edge=0.10, live_enabled=True))
            plan = {
                **plan,
                "allow_duplicate_signal_id": True,
                "execution_action": "maker_lifecycle_reprice_maker",
                "cancel_before_order_id": "old-order-1",
                "source_order_id": "old-order-1",
                "replacement_requires_order_state": True,
                "min_order_shares": 5.0,
                "size": 5.0,
            }
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
                live_place_fn=lambda p: calls.append(p) or {"order_id": "new-order-1"},
                live_cancel_fn=lambda order_id: {
                    "cancel": {"canceled": [order_id], "not_canceled": {}},
                    "order_after_cancel": {"original_size": "5", "size_matched": "3"},
                },
            )

            self.assertEqual(result["live_guard_blocks"], 1)
            self.assertEqual(calls, [])
            row = json.loads(live.read_text().splitlines()[-1])
            self.assertEqual(row["status"], "blocked")
            self.assertEqual(
                row["exchange_response"]["error_classification"],
                "replacement_remaining_below_minimum_after_cancel",
            )
            self.assertEqual(row["exchange_response"]["authoritative_matched_shares"], 3.0)
            self.assertEqual(row["exchange_response"]["replacement_shares"], 2.0)

    def test_execute_trade_plans_places_only_authoritative_remaining_shares(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = normalize_signal(self._paper_decision())
            assert signal is not None
            plan = build_trade_plan(signal, PlannerConfig(max_order_notional=2.0, min_edge=0.10, live_enabled=True))
            plan = {
                **plan,
                "allow_duplicate_signal_id": True,
                "execution_action": "maker_lifecycle_reprice_maker",
                "cancel_before_order_id": "old-order-1",
                "source_order_id": "old-order-1",
                "replacement_requires_order_state": True,
                "min_order_shares": 5.0,
                "size": 8.0,
                "limit_price": 0.10,
            }
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
                live_place_fn=lambda p: calls.append(p) or {"order_id": "new-order-1"},
                live_cancel_fn=lambda order_id: {
                    "cancel": {"canceled": [order_id], "not_canceled": {}},
                    "order_after_cancel": {"original_size": "8", "size_matched": "3"},
                },
            )

            self.assertEqual(result["live_guard_blocks"], 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["size"], 5.0)
            self.assertEqual(calls[0]["notional"], 0.5)
            self.assertEqual(calls[0]["source_filled_shares"], 3.0)

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

    def test_execute_trade_plans_persists_each_live_order_before_next_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = normalize_signal(self._paper_decision())
            assert signal is not None
            plan1 = build_trade_plan(signal, PlannerConfig(max_order_notional=2.0, min_edge=0.10, live_enabled=True))
            signal2 = normalize_signal({**self._paper_decision(), "signal_id": "sig-2", "paper_id": "paper-2"})
            assert signal2 is not None
            plan2 = build_trade_plan(signal2, PlannerConfig(max_order_notional=2.0, min_edge=0.10, live_enabled=True))
            plan2 = {**plan2, "token_id": "token-2", "market_id": "market-2"}
            plans = Path(tmp) / "plans.jsonl"
            paper = Path(tmp) / "paper.jsonl"
            live = Path(tmp) / "live.jsonl"
            plans.write_text(json.dumps(plan1) + "\n" + json.dumps(plan2) + "\n")
            calls = []

            def place(plan):
                calls.append(plan["plan_id"])
                if len(calls) == 2:
                    rows = [json.loads(line) for line in live.read_text().splitlines()]
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]["status"], "submitted")
                    raise RuntimeError("second order failed")
                return {"order_id": "live-1"}

            result = execute_trade_plans(
                plan_path=plans,
                paper_out=paper,
                live_out=live,
                config=ExecutorConfig(live=True, confirm_live=True),
                live_place_fn=place,
            )

            self.assertEqual(result["live_errors"], 1)
            self.assertEqual(result["live_written"], 2)
            rows = [json.loads(line) for line in live.read_text().splitlines()]
            self.assertEqual([row["status"] for row in rows], ["submitted", "error"])

    def test_execute_trade_plans_blocks_over_executor_notional_ceiling(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = normalize_signal(self._paper_decision())
            assert signal is not None
            plan = build_trade_plan(signal, PlannerConfig(max_order_notional=2.0, min_edge=0.10, live_enabled=True))
            plans = Path(tmp) / "plans.jsonl"
            paper = Path(tmp) / "paper.jsonl"
            live = Path(tmp) / "live.jsonl"
            plans.write_text(json.dumps(plan) + "\n")
            calls = []

            result = execute_trade_plans(
                plan_path=plans,
                paper_out=paper,
                live_out=live,
                config=ExecutorConfig(live=True, confirm_live=True, max_live_order_notional_usd=1.0),
                live_place_fn=lambda p: calls.append(p) or {"order_id": "should-not-place"},
            )

            self.assertEqual(calls, [])
            self.assertEqual(result["live_guard_blocks"], 1)
            self.assertEqual(result["live_written"], 1)
            rows = [json.loads(line) for line in live.read_text().splitlines()]
            self.assertEqual(rows[0]["status"], "blocked")
            self.assertEqual(rows[0]["exchange_response"]["error_reason"], "max_live_order_notional_exceeded")

    def test_execute_trade_plans_cancels_expired_live_orders(self):
        with tempfile.TemporaryDirectory() as tmp:
            plans = Path(tmp) / "plans.jsonl"
            paper = Path(tmp) / "paper.jsonl"
            live = Path(tmp) / "live.jsonl"
            cancels = Path(tmp) / "live_cancels.jsonl"
            plans.write_text("")
            live.write_text(
                json.dumps(
                    {
                        "record_type": "weather_edge_live_order",
                        "execution_id": "exec-1",
                        "plan_id": "plan-1",
                        "status": "submitted",
                        "strategy_instance": "test-instance",
                        "expires_at_utc": "2020-01-01T00:00:00+00:00",
                        "exchange_response": {"place": {"orderID": "order-1"}},
                    }
                )
                + "\n"
            )
            calls = []

            result = execute_trade_plans(
                plan_path=plans,
                paper_out=paper,
                live_out=live,
                config=ExecutorConfig(live=True, confirm_live=True, cancel_expired=True, cancel_log_path=cancels),
                live_cancel_fn=lambda order_id: calls.append(order_id) or {"cancelled": order_id},
            )

            self.assertEqual(calls, ["order-1"])
            self.assertEqual(result["cancel_expired_checked"], True)
            self.assertEqual(result["cancel_attempted"], 1)
            self.assertEqual(result["cancel_written"], 1)
            rows = [json.loads(line) for line in cancels.read_text().splitlines()]
            self.assertEqual(rows[0]["record_type"], "weather_edge_live_order_cancel")
            self.assertEqual(rows[0]["status"], "cancel_confirmed")
            self.assertEqual(rows[0]["order_id"], "order-1")

    def test_maker_until_data_update_fields_flow_to_plan_and_live_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            deadline = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
            signal = normalize_signal(
                {
                    **self._paper_decision(),
                    "data_epoch_ref": "metar:paris:2026-05-10T12:00Z",
                    "data_epoch_ts_utc": "2026-05-10T12:00:00+00:00",
                    "next_data_update_due_utc": "2026-05-10T12:30:00+00:00",
                    "cancel_before_data_update_utc": deadline,
                    "post_update_reprice_required": True,
                }
            )
            assert signal is not None
            plan = build_trade_plan(
                signal,
                PlannerConfig(
                    max_order_notional=2.0,
                    min_edge=0.10,
                    live_enabled=True,
                    order_lifecycle_policy="maker_until_data_update",
                    cancel_buffer_sec=90,
                ),
            )
            self.assertEqual(plan["expires_at_utc"], deadline)
            self.assertEqual(plan["cancel_reason"], "pre_data_update")

            plans = Path(tmp) / "plans.jsonl"
            paper = Path(tmp) / "paper.jsonl"
            live = Path(tmp) / "live.jsonl"
            plans.write_text(json.dumps(plan) + "\n")
            result = execute_trade_plans(
                plan_path=plans,
                paper_out=paper,
                live_out=live,
                config=ExecutorConfig(live=True, confirm_live=True),
                live_place_fn=lambda _plan: {"order_id": "live-epoch-1"},
            )

            self.assertEqual(result["live_guard_blocks"], 0)
            row = json.loads(live.read_text().splitlines()[0])
            self.assertEqual(row["order_lifecycle_policy"], "maker_until_data_update")
            self.assertEqual(row["data_epoch_ref"], "metar:paris:2026-05-10T12:00Z")
            self.assertEqual(row["cancel_before_data_update_utc"], deadline)
            self.assertEqual(row["expires_at_utc"], deadline)

    def test_live_executor_blocks_maker_until_data_update_without_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = normalize_signal(self._paper_decision())
            assert signal is not None
            plan = build_trade_plan(
                signal,
                PlannerConfig(
                    max_order_notional=2.0,
                    min_edge=0.10,
                    live_enabled=True,
                    order_lifecycle_policy="maker_until_data_update",
                ),
            )
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
                live_place_fn=lambda item: calls.append(item) or {"order_id": "should-not-place"},
            )

            self.assertEqual(calls, [])
            self.assertEqual(result["live_guard_blocks"], 1)
            row = json.loads(live.read_text().splitlines()[0])
            self.assertEqual(row["status"], "blocked")
            self.assertEqual(row["exchange_response"]["error_reason"], "missing_cancel_before_data_update_utc")

    def test_unconfirmed_expiry_cancel_is_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "live.jsonl"
            cancels = Path(tmp) / "live_cancels.jsonl"
            live.write_text(
                json.dumps(
                    {
                        "record_type": "weather_edge_live_order",
                        "execution_id": "exec-epoch-1",
                        "plan_id": "plan-epoch-1",
                        "status": "submitted",
                        "order_lifecycle_policy": "maker_until_data_update",
                        "cancel_reason": "pre_data_update",
                        "expires_at_utc": "2020-01-01T00:00:00+00:00",
                        "cancel_before_data_update_utc": "2020-01-01T00:00:00+00:00",
                        "exchange_response": {"place": {"orderID": "order-epoch-1"}},
                    }
                )
                + "\n"
            )
            calls = []

            def cancel(order_id):
                calls.append(order_id)
                if len(calls) == 1:
                    return {"cancel": {"canceled": [], "not_canceled": {order_id: "temporary failure"}}}
                return {"cancel": {"canceled": [order_id], "not_canceled": {}}}

            first = cancel_expired_live_orders(
                live_out=live,
                cancel_out=cancels,
                live_cancel_fn=cancel,
            )
            second = cancel_expired_live_orders(
                live_out=live,
                cancel_out=cancels,
                live_cancel_fn=cancel,
            )

            self.assertEqual(first["cancel_errors"], 1)
            self.assertEqual(second["cancel_errors"], 0)
            self.assertEqual(calls, ["order-epoch-1", "order-epoch-1"])
            rows = [json.loads(line) for line in cancels.read_text().splitlines()]
            self.assertEqual([row["status"] for row in rows], ["cancel_error", "cancel_confirmed"])
            self.assertEqual(rows[-1]["cancel_reason"], "pre_data_update")

    def test_expired_fully_matched_order_is_terminal_and_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "live.jsonl"
            cancels = Path(tmp) / "live_cancels.jsonl"
            order_id = "matched-order-1"
            live.write_text(
                json.dumps(
                    {
                        "record_type": "weather_edge_live_order",
                        "execution_id": "exec-matched-1",
                        "plan_id": "plan-matched-1",
                        "status": "submitted",
                        "expires_at_utc": "2020-01-01T00:00:00+00:00",
                        "exchange_response": {"place": {"orderID": order_id}},
                    }
                )
                + "\n"
            )
            calls = []

            def cancel(value):
                calls.append(value)
                return {
                    "cancel": {
                        "canceled": [],
                        "not_canceled": {value: "order can't be found - already canceled or matched"},
                    },
                    "order_after_cancel": {
                        "id": value,
                        "original_size": "10",
                        "size_matched": "10",
                        "status": "MATCHED",
                    },
                }

            first = cancel_expired_live_orders(
                live_out=live,
                cancel_out=cancels,
                live_cancel_fn=cancel,
            )
            second = cancel_expired_live_orders(
                live_out=live,
                cancel_out=cancels,
                live_cancel_fn=cancel,
            )

            self.assertEqual(calls, [order_id])
            self.assertEqual(first["cancel_errors"], 0)
            self.assertEqual(first["cancel_not_needed_terminal"], 1)
            self.assertEqual(second["cancel_skipped_duplicate"], 1)
            rows = [json.loads(line) for line in cancels.read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "cancel_not_needed")
            self.assertEqual(rows[0]["cancel_status_reason"], "order_terminal_matched")


if __name__ == "__main__":
    unittest.main()
