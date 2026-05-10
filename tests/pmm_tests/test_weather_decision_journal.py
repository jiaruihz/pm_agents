import tempfile
import unittest
from pathlib import Path

from src.strategies.weather_theta_no_v1.tools.decision_journal import WeatherDecisionJournal


class TestWeatherDecisionJournal(unittest.TestCase):
    def test_record_if_changed_dedupes_same_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "weather_decision_journal.db"
            journal = WeatherDecisionJournal(db_path=str(db_path))
            try:
                payload = {
                    "market": {"token_id": "TOKEN1"},
                    "baseline_forecast": {"max_temp_market_unit": 15.0},
                    "latest_forecast": {"max_temp_market_unit": 14.0},
                }
                action = {"action": "avoid_new_entry", "reason": "edge shrank"}
                first_id, first_created = journal.record_decision_if_changed(
                    instance_id="inst1",
                    token_id="TOKEN1",
                    city_key="london",
                    local_date="2026-03-11",
                    market_title="London 14C NO",
                    outcome="No",
                    event_type="weather_action_suggestion",
                    urgency="medium",
                    action_text="avoid_new_entry",
                    prompt_version="v1",
                    baseline_file="watch.json",
                    input_payload=payload,
                    codex_output={"summary": "moved cooler"},
                    action_suggestion=action,
                    alert_message="detail-1",
                )
                second_id, second_created = journal.record_decision_if_changed(
                    instance_id="inst1",
                    token_id="TOKEN1",
                    city_key="london",
                    local_date="2026-03-11",
                    market_title="London 14C NO",
                    outcome="No",
                    event_type="weather_action_suggestion",
                    urgency="medium",
                    action_text="avoid_new_entry",
                    prompt_version="v1",
                    baseline_file="watch.json",
                    input_payload=payload,
                    codex_output={"summary": "moved cooler"},
                    action_suggestion=action,
                    alert_message="detail-1",
                )
                rows = journal.list_decisions(limit=10)
                self.assertTrue(first_created)
                self.assertFalse(second_created)
                self.assertEqual(first_id, second_id)
                self.assertEqual(len(rows), 1)
            finally:
                journal.close()

    def test_resolve_latest_updates_result(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "weather_decision_journal.db"
            journal = WeatherDecisionJournal(db_path=str(db_path))
            try:
                decision_id = journal.record_decision(
                    instance_id="inst1",
                    token_id="TOKEN2",
                    city_key="dallas",
                    local_date="2026-03-11",
                    market_title="Dallas 70-71F NO",
                    outcome="No",
                    event_type="weather_main_range_drift",
                    urgency="high",
                    action_text="reduce risk",
                    prompt_version="v1",
                    baseline_file="watch.json",
                    input_payload={"market": {"token_id": "TOKEN2"}},
                    codex_output={"action": "reduce risk"},
                    action_suggestion={"action": "tighten_exit_or_avoid_new_entry"},
                    alert_message="detail-2",
                )
                resolved_id = journal.resolve_latest_decision(
                    token_id="TOKEN2",
                    event_type="weather_main_range_drift",
                    result_label="good_exit",
                    result_notes="manual exit was correct",
                    result_payload={"final_mid": 0.81},
                )
                rows = journal.list_decisions(limit=10)
                self.assertEqual(resolved_id, decision_id)
                self.assertEqual(rows[0]["status"], "resolved")
                self.assertEqual(rows[0]["result_label"], "good_exit")
                self.assertEqual(rows[0]["result"]["final_mid"], 0.81)
            finally:
                journal.close()


if __name__ == "__main__":
    unittest.main()
