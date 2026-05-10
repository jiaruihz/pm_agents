import tempfile
import unittest
from pathlib import Path

from src.strategies.weather_theta_no_v1.tools.case_record import CaseRecordWriter, build_case_record_entry


class TestWeatherCaseRecord(unittest.TestCase):
    def test_build_entry_prefers_human_readable_sections(self):
        entry = build_case_record_entry(
            {
                "city_key": "paris",
                "local_date": "2026-03-15",
                "run_time_local": "2026-03-15 22:55 CET",
                "human_summary": "14 No 还没完全脱离风险区，因为 LFPG 还在峰值窗口附近。",
                "current_judgment": "不能把 14 No 当成已经稳了。",
                "action_suggestion": "已有仓位减一点，不加仓。",
                "conclusion": "继续看下一笔机场观测。",
                "source_summary": [{"name": "WU Hourly", "role": "trading_anchor", "status": "usable"}],
            },
            run_no=1,
        )
        self.assertIn("### 给人先看的结论", entry)
        self.assertIn("14 No 还没完全脱离风险区", entry)
        self.assertIn("### 数据源", entry)
        self.assertIn("`WU Hourly` / role=trading_anchor / status=usable", entry)

    def test_append_entry_creates_and_appends_runs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = CaseRecordWriter(case_dir=Path(tmpdir))
            payload = {
                "city_key": "paris",
                "local_date": "2026-03-15",
                "run_time_local": "2026-03-15 22:55 CET",
                "human_summary": "第一条摘要。",
            }
            path = writer.append_entry(payload)
            writer.append_entry({**payload, "run_time_local": "2026-03-15 23:10 CET", "human_summary": "第二条摘要。"})
            text = path.read_text(encoding="utf-8")
            self.assertIn("# PARIS Case File — 2026-03-15", text)
            self.assertIn("## Run 01 — 2026-03-15 22:55 CET", text)
            self.assertIn("## Run 02 — 2026-03-15 23:10 CET", text)


if __name__ == "__main__":
    unittest.main()
