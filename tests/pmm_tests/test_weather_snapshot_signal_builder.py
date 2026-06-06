import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts.ops import weather_snapshot_signal_builder as builder


class _FakeGamma:
    def fetch_market_by_id_or_slug(self, *, market_id):
        return {"outcomes": ["Yes", "No"], "clobTokenIds": ["yes-token", "no-token"]}

    def fetch_market_by_condition_id(self, condition_id):
        return {"outcomes": ["Yes", "No"], "clobTokenIds": ["yes-token", "no-token"]}


class TestWeatherSnapshotSignalBuilder(unittest.TestCase):
    def _snapshot(self, path: Path, records):
        path.write_text(json.dumps({"records": records}), encoding="utf-8")

    def _record(self, *, edge=-0.101, entry_price=0.695, market_id="2351103"):
        settle_utc = datetime.now(timezone.utc) + timedelta(hours=25)
        return {
            "record_type": "edge_signal",
            "city": "Guangzhou",
            "city_pool": "t1_trading",
            "event_date": "2026-05-27",
            "unit": "C",
            "event_slug": "highest-temperature-in-guangzhou-on-may-27-2026",
            "market_id": market_id,
            "condition_id": f"condition-{market_id}",
            "question": "Will the highest temperature in Guangzhou be 36C on May 27?",
            "bracket": "36",
            "side": "BUY_NO",
            "model_prob": 0.899,
            "edge": edge,
            "abs_edge": abs(edge),
            "entry_price": entry_price,
            "time_bucket": "pre_t24",
            "hours_to_settle": 26.5,
            "settle_utc": settle_utc.isoformat(),
            "forecast_source": "open_meteo_live_gfs",
            "model": "gfs",
            "ts_utc": datetime.now(timezone.utc).isoformat(),
        }

    @patch.object(builder, "PolymarketGammaClient", _FakeGamma)
    def test_lookback_captures_non_latest_snapshot_and_settlement_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            older = root / "snapshot_20260526_2000.json"
            latest = root / "snapshot_20260526_2030.json"
            out = root / "signals.jsonl"
            self._snapshot(older, [self._record()])
            self._snapshot(latest, [])
            os.utime(older, (1_000_000, 1_000_000))
            os.utime(latest, (1_000_000 + 30 * 60, 1_000_000 + 30 * 60))

            result = builder.build_signals(
                snapshot_path=latest,
                snapshot_paths=builder._recent_snapshots(root, 90),
                out_path=out,
                city_pool="t1_trading",
                min_edge=0.10,
                min_entry_price=0.25,
                max_entry_price=0.75,
                min_hours_to_settle=22.0,
                max_hours_to_settle=28.0,
                dry_run=False,
            )

            self.assertEqual(result["signals"], 1)
            rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["city"], "Guangzhou")
            self.assertEqual(rows[0]["signal_side"], "BUY_NO")
            self.assertEqual(rows[0]["token_id"], "no-token")
            self.assertEqual(rows[0]["source_run_id"], "snapshot_20260526_2000")

    @patch.object(builder, "PolymarketGammaClient", _FakeGamma)
    def test_latest_only_default_remains_latest_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            older = root / "snapshot_20260526_2000.json"
            latest = root / "snapshot_20260526_2030.json"
            out = root / "signals.jsonl"
            self._snapshot(older, [self._record()])
            self._snapshot(latest, [])
            os.utime(older, (1_000_000, 1_000_000))
            os.utime(latest, (1_000_000 + 30 * 60, 1_000_000 + 30 * 60))

            result = builder.build_signals(
                snapshot_path=latest,
                snapshot_paths=builder._recent_snapshots(root, 0),
                out_path=out,
                city_pool="t1_trading",
                min_edge=0.10,
                min_entry_price=0.25,
                max_entry_price=0.75,
                min_hours_to_settle=22.0,
                max_hours_to_settle=28.0,
                dry_run=False,
            )

            self.assertEqual(result["signals"], 0)
            self.assertEqual(out.read_text(encoding="utf-8"), "")

    @patch.object(builder, "PolymarketGammaClient", _FakeGamma)
    def test_allowed_cities_filters_after_city_pool(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "snapshot_20260526_2000.json"
            out = root / "signals.jsonl"
            blocked = self._record(market_id="blocked")
            blocked["city"] = "Ankara"
            allowed = self._record(market_id="allowed")
            allowed["city"] = "London"
            self._snapshot(snapshot, [blocked, allowed])

            result = builder.build_signals(
                snapshot_path=snapshot,
                snapshot_paths=[snapshot],
                out_path=out,
                city_pool="t1_trading",
                allowed_cities={"London", "Tokyo"},
                min_edge=0.10,
                min_entry_price=0.25,
                max_entry_price=0.75,
                min_hours_to_settle=22.0,
                max_hours_to_settle=28.0,
                dry_run=False,
            )

            self.assertEqual(result["signals"], 1)
            self.assertEqual(result["skipped"].get("city_not_allowed"), 1)
            rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["city"] for row in rows], ["London"])

    @patch.object(builder, "PolymarketGammaClient", _FakeGamma)
    def test_blocked_city_sides_filter_specific_side_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "snapshot_20260604_2000.json"
            out = root / "signals.jsonl"
            yes = self._record(market_id="nyc-yes", edge=0.25, entry_price=0.32)
            yes["city"] = "NYC"
            yes["side"] = "BUY_YES"
            no = self._record(market_id="nyc-no", edge=-0.25, entry_price=0.62)
            no["city"] = "NYC"
            no["side"] = "BUY_NO"
            self._snapshot(snapshot, [yes, no])

            result = builder.build_signals(
                snapshot_path=snapshot,
                snapshot_paths=[snapshot],
                out_path=out,
                city_pool="t1_trading",
                blocked_city_sides={("NYC", "BUY_YES")},
                min_edge=0.10,
                min_entry_price=0.25,
                max_entry_price=0.75,
                min_hours_to_settle=22.0,
                max_hours_to_settle=28.0,
                dry_run=False,
            )

            self.assertEqual(result["signals"], 1)
            self.assertEqual(result["skipped"].get("city_side_blocked"), 1)
            self.assertEqual(result["blocked_city_sides"], ["NYC:BUY_YES"])
            rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["city"], "NYC")
            self.assertEqual(rows[0]["signal_side"], "BUY_NO")

    @patch.object(builder, "PolymarketGammaClient", _FakeGamma)
    def test_lookback_keeps_latest_side_per_market(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            older = root / "snapshot_20260531_2030.json"
            latest = root / "snapshot_20260531_2200.json"
            out = root / "signals.jsonl"
            old_no = self._record(market_id="lucknow-34", edge=-0.1957, entry_price=0.64)
            old_no["side"] = "BUY_NO"
            old_no["model_prob"] = 0.1643
            old_no["abs_edge"] = 0.1957
            new_yes = self._record(market_id="lucknow-34", edge=0.1368, entry_price=0.325)
            new_yes["side"] = "BUY_YES"
            new_yes["model_prob"] = 0.4618
            new_yes["abs_edge"] = 0.1368
            self._snapshot(older, [old_no])
            self._snapshot(latest, [new_yes])
            os.utime(older, (1_000_000, 1_000_000))
            os.utime(latest, (1_000_000 + 90 * 60, 1_000_000 + 90 * 60))

            result = builder.build_signals(
                snapshot_path=latest,
                snapshot_paths=builder._recent_snapshots(root, 120),
                out_path=out,
                city_pool="t1_trading",
                min_edge=0.10,
                min_entry_price=0.25,
                max_entry_price=0.75,
                min_hours_to_settle=22.0,
                max_hours_to_settle=28.0,
                dry_run=False,
            )

            self.assertEqual(result["signals"], 1)
            self.assertEqual(result["skipped"].get("older_duplicate_candidate"), 1)
            rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["signal_side"], "BUY_YES")
            self.assertEqual(rows[0]["token_id"], "yes-token")
            self.assertEqual(rows[0]["source_run_id"], "snapshot_20260531_2200")


if __name__ == "__main__":
    unittest.main()
