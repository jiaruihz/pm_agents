import unittest
from unittest.mock import patch

from src.strategies.weather_edge_v1.tools.airport_weather_tool import AirportWeatherTool


class TestAirportWeatherTool(unittest.TestCase):
    @patch("src.strategies.weather_edge_v1.tools.airport_weather_tool.resolve_daily_plan")
    def test_build_snapshot_includes_taf_and_multi_model_layers(self, mock_resolve_daily_plan):
        mock_resolve_daily_plan.return_value = {"daily_overrides": {}, "action_suggestion": {"action": "review_only"}}
        tool = AirportWeatherTool()

        with patch.object(
            tool,
            "fetch_station_info",
            return_value={"icaoId": "RKSI", "iataId": "ICN", "site": "Seoul/Incheon Intl", "lat": 37.469, "lon": 126.451},
        ), patch.object(
            tool,
            "fetch_metar",
            return_value={
                "reportTime": "2026-03-16T15:00:00.000Z",
                "temp": 3,
                "dewp": -2,
                "wdir": 260,
                "wspd": 3,
                "rawOb": "METAR RKSI 161500Z ...",
                "fltCat": "VFR",
            },
        ), patch.object(
            tool,
            "fetch_taf",
            return_value={
                "issueTime": "2026-03-16T11:00:00.000Z",
                "validTimeFrom": 1773662400,
                "validTimeTo": 1773770400,
                "rawTAF": "TAF RKSI 161100Z 1612/1718 30006KT 6000 NSC TN01/1621Z TX10/1705Z",
            },
        ), patch.object(
            tool,
            "fetch_forecast",
            return_value={
                "forecast_date": "2026-03-16",
                "max_temp_c": 5.3,
                "min_temp_c": 1.2,
                "precipitation_probability_max": 12,
                "hourly_temps_c": [1.0, 2.0, 3.5, 5.3, 4.8],
                "peak_hour_local": "14:00",
                "raw": {},
            },
        ), patch.object(
            tool,
            "fetch_model_forecasts",
            return_value={
                "status": "ok",
                "available_model_count": 4,
                "requested_model_count": 4,
                "max_temp_spread_c": 1.1,
                "peak_window_start_local": "13:00",
                "peak_window_end_local": "15:00",
                "peak_hour_spread_minutes": 120,
                "models": {
                    "ecmwf_ifs04": {"status": "ok", "max_temp_c": 5.2, "peak_hour_local": "14:00"},
                    "jma_seamless": {"status": "ok", "max_temp_c": 6.3, "peak_hour_local": "15:00"},
                },
            },
        ), patch.object(
            tool,
            "fetch_secondary_validation",
            return_value={
                "wunderground_hourly": {"status": "ok", "station_match": True},
                "wunderground_history_daily": {"status": "ok", "station_match": True},
                "checkwx_metar": {"status": "ok", "station_match": True},
                "checkwx_taf": {"status": "ok", "station_match": True},
                "local_official_rows": [{"status": "ok", "station_match": True}],
                "validation_flags": [],
            },
        ):
            snapshot = tool.build_snapshot(city_key="seoul", local_date="2026-03-16")

        self.assertEqual(snapshot["taf_summary"]["forecast_max_temp_c"], 10)
        self.assertEqual(snapshot["taf_summary"]["forecast_min_temp_c"], 1)
        self.assertEqual(snapshot["multi_model_forecast"]["available_model_count"], 4)
        self.assertEqual(snapshot["latest_forecast"]["multi_model_max_temp_spread_c"], 1.1)
        self.assertEqual(snapshot["latest_forecast"]["multi_model_peak_window_start_local"], "13:00")
        self.assertEqual(snapshot["secondary_validation"]["wunderground_hourly"]["status"], "ok")
        self.assertNotIn("taf_missing_tx", snapshot["latest_forecast"]["risk_flags"])


if __name__ == "__main__":
    unittest.main()
