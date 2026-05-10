import unittest

from src.strategies.weather_edge_v1.tools.codex_weather_advisor import _normalize_analysis


class TestWeatherCodexAdvisor(unittest.TestCase):
    def test_guardrails_clamp_action_and_urgency(self):
        payload = {
            "decision_guardrails": {
                "integrity_ok": True,
                "allow_stop_loss_review": False,
                "max_allowed_urgency": "medium",
                "preferred_action_set": ["hold", "avoid_new_entry", "no_add", "partial_take_profit"],
            }
        }
        result = _normalize_analysis(
            {
                "title": "London risk",
                "summary": "forecast drifted",
                "signal_class": "hard_risk",
                "judgment": "needs stop loss review",
                "action": "stop_loss_review",
                "urgency": "high",
                "confidence_note": "test",
                "key_facts": ["a", "b", "c"],
            },
            payload,
        )
        self.assertEqual(result["action"], "hold")
        self.assertEqual(result["urgency"], "medium")
        self.assertEqual(result["signal_class"], "soft_risk")

    def test_integrity_failure_forces_manual_check(self):
        payload = {
            "decision_guardrails": {
                "integrity_ok": False,
                "allow_stop_loss_review": True,
                "max_allowed_urgency": "medium",
                "preferred_action_set": ["manual_check"],
            }
        }
        result = _normalize_analysis(
            {
                "title": "bad data",
                "summary": "",
                "signal_class": "info",
                "judgment": "",
                "action": "hold",
                "urgency": "low",
                "confidence_note": "",
                "key_facts": [],
            },
            payload,
        )
        self.assertEqual(result["action"], "manual_check")
        self.assertEqual(result["urgency"], "high")
        self.assertEqual(result["signal_class"], "data_integrity_error")


if __name__ == "__main__":
    unittest.main()
