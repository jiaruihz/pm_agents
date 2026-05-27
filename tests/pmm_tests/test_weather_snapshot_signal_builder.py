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
                dry_run=False,
            )

            self.assertEqual(result["signals"], 0)
            self.assertEqual(out.read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
