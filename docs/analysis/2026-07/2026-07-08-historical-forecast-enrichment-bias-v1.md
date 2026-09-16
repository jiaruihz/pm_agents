# Historical Forecast Enrichment Bias v1

Generated: 2026-07-08T07:43:05+00:00

## Question

Can the newer Open-Meteo multi-model sources be pulled historically and compared
against final WU/Polymarket settlement, using the same official airport station?

## Answer

Yes. This run backfilled historical daily max forecasts by official station
coordinates from `source_profiles.official_station_or_feed`, then joined them to
`settlement_outcomes`.

This is still a forecast-vs-settlement calibration layer, not a live selector.
The label is the settled bracket midpoint, not raw WU tenth-degree readings, so
Celsius markets carry rounding noise.

## Data

```json
{
  "db": "/Users/deepsleep/projects/pm_agents/runtime/weather.db",
  "source_profiles": "/Users/deepsleep/projects/pm_agents/weather_data_feed/source_profiles.json",
  "start": "2026-05-04",
  "end": "2026-07-07",
  "settlement_rows": 2684,
  "cities_with_settlement": 49,
  "daily_error_rows": 26741,
  "models_requested": [
    "ecmwf_ifs025",
    "ecmwf_aifs025_single",
    "gfs_seamless",
    "gfs_global",
    "ncep_hrrr_conus",
    "ncep_nbm_conus",
    "ncep_nam_conus",
    "ncep_gfs_graphcast025",
    "ncep_aigfs025",
    "icon_seamless",
    "icon_eu",
    "icon_d2",
    "gem_seamless",
    "gem_global",
    "gem_regional",
    "gem_hrdps_continental",
    "jma_seamless",
    "meteofrance_arome_france_hd"
  ],
  "models_observed": [
    "ecmwf_aifs025_single",
    "ecmwf_ifs025",
    "gem_global",
    "gem_hrdps_continental",
    "gem_regional",
    "gem_seamless",
    "gfs_global",
    "gfs_seamless",
    "icon_d2",
    "icon_eu",
    "icon_seamless",
    "jma_seamless",
    "meteofrance_arome_france_hd",
    "ncep_aigfs025",
    "ncep_gfs_graphcast025",
    "ncep_hrrr_conus",
    "ncep_nam_conus",
    "ncep_nbm_conus"
  ],
  "fetch_status": {
    "forecast_cache": 45
  },
  "skipped_cities": [
    {
      "city": "HongKong",
      "reason": "missing_station_coords",
      "station": "HKO"
    },
    {
      "city": "Moscow",
      "reason": "missing_station_coords",
      "station": "UNKNOWN_EFFECTIVE_SOURCE"
    },
    {
      "city": "Seoul",
      "reason": "missing_station_coords",
      "station": "UNKNOWN_EFFECTIVE_SOURCE"
    },
    {
      "city": "Shenzhen",
      "reason": "missing_station_coords",
      "station": "UNRESOLVED_WU_FEED"
    }
  ]
}
```

## Overall Model Ranking

| model | key | n | cities | bias F | MAE F | RMSE F | underforecast >=1F % | overforecast >=1F % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ICON-D2 | icon_d2 | 289 | 5 | 0.2273 | 1.0398 | 1.3372 | 30.45 | 17.99 |
| ICON-EU | icon_eu | 635 | 11 | 0.5575 | 1.2554 | 1.6464 | 41.1 | 12.6 |
| AROME HD | meteofrance_arome_france_hd | 351 | 6 | -0.81 | 1.459 | 1.8713 | 13.39 | 46.44 |
| ICON | icon_seamless | 2454 | 45 | 0.5015 | 2.0909 | 3.2228 | 45.44 | 20.7 |
| GFS | gfs_seamless | 2454 | 45 | 0.068 | 2.3928 | 3.5482 | 41.93 | 28.81 |
| HRRR | ncep_hrrr_conus | 624 | 11 | -0.7308 | 2.4436 | 4.4952 | 31.57 | 30.61 |
| GDPS | gem_global | 2454 | 45 | 0.8519 | 2.4793 | 3.5794 | 51.75 | 20.58 |
| GEM | gem_seamless | 2454 | 45 | 0.4842 | 2.4811 | 3.6646 | 46.21 | 25.22 |
| GFS Global | gfs_global | 2454 | 45 | -0.3255 | 2.6043 | 3.8115 | 38.02 | 35.04 |
| RDPS | gem_regional | 909 | 18 | -0.3647 | 2.6861 | 4.2765 | 38.5 | 32.34 |
| ECMWF | ecmwf_ifs025 | 2454 | 45 | 0.4236 | 2.7213 | 3.8261 | 51.67 | 24.82 |
| AI-GFS | ncep_aigfs025 | 2454 | 45 | 1.0303 | 2.7861 | 3.6922 | 57.78 | 21.68 |
| NBM | ncep_nbm_conus | 624 | 11 | -0.2577 | 2.8292 | 4.6658 | 45.19 | 28.53 |
| NAM | ncep_nam_conus | 624 | 11 | -2.0537 | 2.8585 | 4.8589 | 15.87 | 54.49 |
| ECMWF AIFS | ecmwf_aifs025_single | 2454 | 45 | 1.6504 | 2.9543 | 4.0363 | 65.73 | 13.85 |
| JMA | jma_seamless | 2454 | 45 | 2.6479 | 3.7892 | 4.9307 | 70.78 | 13.24 |
| HRDPS | gem_hrdps_continental | 175 | 3 | -2.716 | 4.2017 | 6.8773 | 22.86 | 58.86 |
| GFS GraphCast | ncep_gfs_graphcast025 | 424 | 45 | -0.2967 | 5.4943 | 7.2579 | 46.23 | 41.98 |

## City Models Beating Old GFS/ECMWF

| city | station | n | best model | best MAE F | GFS MAE F | ECMWF MAE F | gain F | bias F |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Munich | EDDM | 54 | ICON | 1.1667 | 3.9463 | 3.213 | 2.0463 | 0.6296 |
| Karachi | OPKC | 56 | ICON | 1.2661 | 2.9429 | 2.3429 | 1.0768 | 0.0089 |
| Ankara | LTAC | 55 | GDPS | 1.58 | 2.5873 | 3.4 | 1.0073 | 0.9945 |

## Output Files

- `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/historical_forecast_enrichment_bias_v1/daily_error_rows.csv`
- `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/historical_forecast_enrichment_bias_v1/city_model_summary.csv`
- `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/historical_forecast_enrichment_bias_v1/overall_model_summary.csv`
- `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/historical_forecast_enrichment_bias_v1/city_best_model.csv`
- `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/historical_forecast_enrichment_bias_v1/station_coordinates.csv`
