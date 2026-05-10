import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from src.strategies.weather_theta_no_v1.tools.weather_edge_market_data import (
    WeatherEdgeMarketData,
    normalize_event_summary,
    token_ids_from_event,
    weather_event_slug,
)


class TestWeatherEdgeMarketData(unittest.TestCase):
    def test_weather_event_slug(self):
        self.assertEqual(
            weather_event_slug("new-york", date(2026, 4, 30)),
            "highest-temperature-in-new-york-on-april-30-2026",
        )

    def test_token_ids_from_event_dedupes_yes_no_tokens(self):
        event = {
            "markets": [
                {"clobTokenIds": '["yes1","no1"]'},
                {"clobTokenIds": ["yes2", "no2", "yes1"]},
            ]
        }
        self.assertEqual(token_ids_from_event(event), ["yes1", "no1", "yes2", "no2"])

    def test_normalize_event_summary(self):
        event = {
            "id": "evt1",
            "slug": "weather-event",
            "title": "Weather Event",
            "markets": [{"clobTokenIds": ["yes", "no"]}],
        }
        summary = normalize_event_summary(
            event,
            city="Paris",
            target_date="2026-04-30",
            slug="highest-temperature-in-paris-on-april-30-2026",
        )
        self.assertEqual(summary["city"], "Paris")
        self.assertEqual(summary["event_id"], "evt1")
        self.assertEqual(summary["market_count"], 1)
        self.assertEqual(summary["token_ids"], ["yes", "no"])

    def test_fetch_event_writes_cache_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = WeatherEdgeMarketData(data_root=Path(tmp))
            fake_event = {
                "id": "evt1",
                "slug": "highest-temperature-in-paris-on-april-30-2026",
                "markets": [{"clobTokenIds": ["yes", "no"]}],
            }
            with patch.object(manager.gamma, "fetch_event_by_id_or_slug", return_value=fake_event):
                event = manager.fetch_event(city="Paris", target_date=date(2026, 4, 30), use_cache=False)

            self.assertEqual(event["id"], "evt1")
            self.assertTrue((Path(tmp) / "gamma_events" / "2026-04-30" / "Paris.json").exists())
            self.assertTrue((Path(tmp) / "manifest.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
