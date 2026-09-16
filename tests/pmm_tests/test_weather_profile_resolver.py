import unittest

from src.strategies.weather_edge_v1.tools.profile_resolver import infer_season, load_profiles, resolve_daily_plan


class TestWeatherProfileResolver(unittest.TestCase):
    def test_load_profiles_reads_station_config_from_config_dir(self):
        bundle = load_profiles(city_key="paris", local_date="2026-03-10")
        self.assertEqual(bundle.station["station_code"], "LFPG")
        self.assertEqual(bundle.station["timezone"], "Europe/Paris")

    def test_infer_season_lucknow_monsoon(self):
        self.assertEqual(infer_season("lucknow", "2026-07-15"), "monsoon")

    def test_resolve_daily_plan_returns_overrides_and_action(self):
        payload = resolve_daily_plan(
            city_key="paris",
            local_date="2026-03-10",
            baseline_forecast={
                "max_temp_c": 17.8,
                "edge_to_bucket": 3.0,
                "tail_distance_bins": 3,
            },
            latest_forecast={
                "max_temp_c": 16.2,
                "edge_to_bucket": 1.2,
                "tail_distance_bins": 2,
                "conditions": ["stable_high_pressure"],
                "main_range_shift_bins": 0,
                "peak_hour_shift_minutes": 15,
                "precipitation_probability_max": 20,
            },
            latest_observation={"temp_c": 11.0},
            latest_orderbook={"best_bid": 0.91, "best_ask": 0.93},
        )
        self.assertEqual(payload["city_key"], "paris")
        self.assertIn("daily_overrides", payload)
        self.assertIn("action_suggestion", payload)
        self.assertEqual(payload["daily_overrides"]["season"], "spring")
        self.assertIn(payload["action_suggestion"]["action"], {"maker_entry_ok", "size_down", "review_only"})

    def test_high_risk_flags_can_block_entry(self):
        payload = resolve_daily_plan(
            city_key="london",
            local_date="2026-01-10",
            baseline_forecast={"max_temp_c": 5.0},
            latest_forecast={
                "max_temp_c": 4.5,
                "risk_flags": ["fog", "low_cloud", "frontal_shift"],
                "edge_to_bucket": 0.8,
                "tail_distance_bins": 1,
                "precipitation_probability_max": 80,
            },
            latest_observation={"conditions": ["fog"]},
            latest_orderbook={"best_bid": 0.90, "best_ask": 0.96},
        )
        self.assertIn(
            payload["action_suggestion"]["action"],
            {"avoid_new_entry", "tighten_exit_or_avoid_new_entry"},
        )


if __name__ == "__main__":
    unittest.main()
