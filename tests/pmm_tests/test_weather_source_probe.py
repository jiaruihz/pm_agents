import unittest
from unittest.mock import patch

from src.strategies.weather_edge_v1.tools.source_probe import (
    build_source_targets,
    probe_source,
    render_markdown_table,
)


class DummyResponse:
    def __init__(self, *, status_code=200, json_payload=None, text="", url="https://example.com"):
        self.status_code = status_code
        self._json_payload = json_payload
        self.text = text
        self.url = url

    def raise_for_status(self):
        return None

    def json(self):
        return self._json_payload


class DummySession:
    def __init__(self, response):
        self.response = response

    def get(self, url, timeout=20):
        return self.response

    def close(self):
        return None


class TestWeatherSourceProbe(unittest.TestCase):
    def test_build_source_targets_includes_supported_and_local_official(self):
        targets = build_source_targets("seoul", "2026-03-16")
        keys = {row.source_key for row in targets}
        self.assertIn("aviationweather_metar", keys)
        self.assertIn("aviationweather_taf", keys)
        self.assertIn("wunderground_history_daily", keys)
        self.assertIn("wunderground_hourly", keys)
        self.assertIn("checkwx_metar", keys)
        self.assertIn("checkwx_taf", keys)
        self.assertIn("korea_amo_realtime", keys)

    def test_probe_source_metar_extracts_fields(self):
        target = [row for row in build_source_targets("seoul", "2026-03-16") if row.source_key == "aviationweather_metar"][0]
        response = DummyResponse(
            json_payload=[
                {
                    "icaoId": "RKSI",
                    "name": "Seoul/Incheon Intl",
                    "reportTime": "2026-03-16T15:00:00.000Z",
                    "temp": 3,
                    "dewp": -2,
                    "wdir": 260,
                    "wspd": 3,
                    "rawOb": "METAR RKSI ...",
                }
            ]
        )
        result = probe_source(target, session=DummySession(response))
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["station_match"])
        self.assertEqual(result["extracted"]["temp_c"], 3)
        self.assertIn("report_time", result["extracted"])

    def test_probe_source_wu_html_extracts_title_and_canonical(self):
        target = [row for row in build_source_targets("seoul", "2026-03-16") if row.source_key == "wunderground_history_daily"][0]
        html = """
        <html><head>
        <title>Incheon, South Korea Weather History | Weather Underground</title>
        <link rel="canonical" href="https://www.wunderground.com/history/daily/RKSI/date/2026-03-16" />
        </head><body>RKSI 2026-03-16</body></html>
        """
        result = probe_source(target, session=DummySession(DummyResponse(text=html)))
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["station_match"])
        self.assertIn("canonical_url", result["extracted"])

    def test_render_markdown_table_renders_rows(self):
        table = render_markdown_table(
            [
                {
                    "city_key": "seoul",
                    "station_code": "RKSI",
                    "source_key": "aviationweather_metar",
                    "status": "ok",
                    "station_match": True,
                    "extracted": {"temp_c": 3},
                    "notes": "",
                }
            ]
        )
        self.assertIn("| city | station | source |", table)
        self.assertIn("aviationweather_metar", table)
        self.assertIn("seoul", table)


if __name__ == "__main__":
    unittest.main()
