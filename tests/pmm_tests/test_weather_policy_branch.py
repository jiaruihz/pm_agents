import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.ops.weather_policy_branch import _branch_signal_alerts, _merge_today_signals


class TestWeatherPolicyBranch(unittest.TestCase):
    def _write_jsonl(self, path: Path, rows):
        lines = []
        for row in rows:
            if isinstance(row, str):
                lines.append(row)
            else:
                lines.append(json.dumps(row))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_summary(self, live_cycle_dir: Path, run_id: str, signal_path: Path, policy: str = "mid_price_core_v1"):
        summary = {
            "config": {"execution_policy": policy},
            "paths": {"signal": str(signal_path)},
        }
        (live_cycle_dir / f"{run_id}.json").write_text(json.dumps(summary), encoding="utf-8")

    def test_merge_today_signals_uses_today_union_and_latest_market_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live_cycle_dir = root / "live_cycle"
            live_cycle_dir.mkdir()
            today = datetime.now(timezone.utc).strftime("%Y%m%d")
            early = root / "early.jsonl"
            late = root / "late.jsonl"
            out = root / "branch.jsonl"

            self._write_jsonl(
                early,
                [
                    {
                        "record_type": "weather_edge_signal",
                        "market_id": "m1",
                        "city": "Miami",
                        "snapshot_fetched_at_utc": "2026-05-24T01:00:00Z",
                        "model_probability_yes": 0.1,
                    },
                    {
                        "record_type": "weather_edge_signal",
                        "market_id": "m2",
                        "city": "Paris",
                        "snapshot_fetched_at_utc": "2026-05-24T01:00:00Z",
                    },
                    "{bad-json",
                    {
                        "record_type": "weather_edge_signal",
                        "city": "MissingMarket",
                        "snapshot_fetched_at_utc": "2026-05-24T01:00:00Z",
                    },
                ],
            )
            self._write_jsonl(
                late,
                [
                    {
                        "record_type": "weather_edge_signal",
                        "market_id": "m1",
                        "city": "Miami",
                        "snapshot_fetched_at_utc": "2026-05-24T02:00:00Z",
                        "model_probability_yes": 0.9,
                    },
                    {
                        "record_type": "weather_edge_signal",
                        "market_id": "m3",
                        "city": "Warsaw",
                        "snapshot_fetched_at_utc": "",
                    },
                ],
            )
            self._write_summary(live_cycle_dir, f"{today}T010000Z", early)
            self._write_summary(live_cycle_dir, f"{today}T020000Z", late)

            stats = _merge_today_signals(live_cycle_dir, "mid_price_core_v1", out)
            rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
            by_market = {row["market_id"]: row for row in rows}

            self.assertEqual(stats["source_files"], 2)
            self.assertEqual(stats["source_rows"], 6)
            self.assertEqual(stats["merged_signals"], 3)
            self.assertEqual(stats["invalid_json_lines"], 1)
            self.assertEqual(stats["missing_market_id"], 1)
            self.assertEqual(stats["missing_snapshot_ts"], 1)
            self.assertEqual(set(by_market), {"m1", "m2", "m3"})
            self.assertEqual(by_market["m1"]["model_probability_yes"], 0.9)

            alerts = _branch_signal_alerts(stats, signal_count=len(rows))
            self.assertIn("invalid source signal JSON lines", "\n".join(alerts))
            self.assertIn("without market_id", "\n".join(alerts))
            self.assertIn("missing snapshot timestamp", "\n".join(alerts))

    def test_branch_signal_alerts_flags_written_count_mismatch(self):
        alerts = _branch_signal_alerts({"merged_signals": 2}, signal_count=1)
        self.assertIn("signal count mismatch", "\n".join(alerts))


if __name__ == "__main__":
    unittest.main()
