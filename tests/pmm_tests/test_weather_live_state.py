import json
import tempfile
import unittest
from pathlib import Path

from src.strategies.weather_edge_v1.tools.live_state import pause_live, read_live_state, resume_live, status_text


class TestWeatherLiveState(unittest.TestCase):
    def test_pause_resume_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            self.assertFalse(read_live_state(state_dir)["paused"])

            paused = pause_live(state_dir, reason="manual check", source="test", paused_at_utc="2026-05-15T00:00:00Z")
            self.assertTrue(paused["paused"])
            self.assertEqual(paused["reason"], "manual check")
            self.assertEqual(paused["source"], "test")
            self.assertIn("已暂停", status_text(paused))

            raw = json.loads((state_dir / "PAUSED").read_text())
            self.assertEqual(raw["paused_at_utc"], "2026-05-15T00:00:00Z")

            resumed = resume_live(state_dir)
            self.assertFalse(resumed["paused"])
            self.assertFalse((state_dir / "PAUSED").exists())

    def test_reads_legacy_pause_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            (state_dir / "PAUSED").write_text("2026-05-14T09:28:12Z", encoding="utf-8")
            state = read_live_state(state_dir)
            self.assertTrue(state["paused"])
            self.assertEqual(state["paused_at_utc"], "2026-05-14T09:28:12Z")
            self.assertEqual(state["source"], "legacy")


if __name__ == "__main__":
    unittest.main()
