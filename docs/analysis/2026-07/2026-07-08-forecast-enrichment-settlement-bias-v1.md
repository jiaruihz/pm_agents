# Forecast Enrichment Settlement Bias v1

Generated: 2026-07-08T04:07:18+00:00

## Question

The old production forecast layer mostly used city-assigned GFS/ECMWF. The new
`forecast_enrichment` capture contains many more per-model max-temperature
forecasts. This report asks whether any of those models are closer to final
WU/Polymarket settlement for specific cities.

## Verdict

This is useful, but current evidence is **candidate-level only**. The available
settled enrichment sample is one target date, 2026-07-07, and the capture started
intra-day. Use this as a shortlist for shadow reliability features, not as a
live model switch.

## Data

```json
{
  "files": [
    "/Volumes/jrs/weather_data_feed_service_runtime/output/forecast_enrichment/2026-07-07/forecast_enrichment.jsonl"
  ],
  "raw_records": 880,
  "joined_records": 614,
  "prediction_rows": 7047,
  "city_dates": 39,
  "models_seen": [
    "AI-GFS",
    "AROME HD",
    "ECMWF",
    "ECMWF AIFS",
    "GDPS",
    "GEM",
    "GFS",
    "GFS Global",
    "HRDPS",
    "HRRR",
    "ICON",
    "ICON-D2",
    "ICON-EU",
    "JMA",
    "NAM",
    "NBM",
    "RDPS"
  ],
  "source_statuses": [
    {
      "source": "open_meteo_weather_context",
      "status": "ok",
      "rows": 616
    },
    {
      "source": "open_meteo_multi_model",
      "status": "ok",
      "rows": 615
    },
    {
      "source": "aviationweather_taf",
      "status": "ok",
      "rows": 573
    },
    {
      "source": "aviationweather_taf",
      "status": "fetch_failed",
      "rows": 45
    },
    {
      "source": "open_meteo_multi_model",
      "status": "fetch_failed",
      "rows": 3
    },
    {
      "source": "open_meteo_weather_context",
      "status": "fetch_failed",
      "rows": 2
    }
  ],
  "no_settlement_cities": {},
  "no_models": 4
}
```

Settlement is represented by the winning bracket midpoint in Fahrenheit. For
Celsius exact brackets, the Celsius integer winner is converted to Fahrenheit,
so there is unavoidable rounding noise versus true WU raw temperature.

## Earliest Snapshot Per City-Date

| model | n | MAE F | bias F | RMSE F | underforecast >=1F % | overforecast >=1F % |
| --- | --- | --- | --- | --- | --- | --- |
| AROME HD | 6 | 1.1 | -0.433 | 1.288 | 16.7 | 33.3 |
| NAM | 11 | 1.564 | 0.909 | 1.798 | 54.5 | 18.2 |
| ICON-D2 | 5 | 1.74 | -0.18 | 1.989 | 40.0 | 20.0 |
| ICON-EU | 9 | 1.767 | 1.167 | 2.142 | 55.6 | 11.1 |
| RDPS | 17 | 2.071 | 0.788 | 2.541 | 47.1 | 23.5 |
| GEM | 39 | 2.136 | 0.603 | 2.633 | 48.7 | 23.1 |
| ICON | 39 | 2.169 | 0.656 | 2.539 | 51.3 | 25.6 |
| GDPS | 39 | 2.203 | 1.351 | 2.691 | 61.5 | 15.4 |
| NBM | 11 | 2.273 | 1.509 | 2.585 | 63.6 | 9.1 |
| HRRR | 11 | 2.627 | 0.755 | 3.22 | 45.5 | 36.4 |
| ECMWF | 39 | 2.756 | 1.064 | 3.51 | 56.4 | 17.9 |
| AI-GFS | 39 | 2.931 | 1.469 | 3.582 | 56.4 | 15.4 |
| HRDPS | 3 | 2.967 | -1.633 | 3.09 | 33.3 | 66.7 |
| GFS | 39 | 3.018 | 0.9 | 3.692 | 48.7 | 38.5 |

### Best City/Model Improvements

| city | unit | settled bracket | best model | pred F | err F | GFS abs | ECMWF abs | gain vs old-best F |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Guangzhou | C | 31 | GDPS | 88.1 | -0.3 | 8.9 | 5.9 | 5.6 |
| Munich | C | 30 | AROME HD | 85.7 | 0.3 | 5.7 | 5.3 | 5.0 |
| Karachi | C | 35 | GDPS | 95.1 | -0.1 | 6.4 | 4.0 | 3.9 |
| Houston | F | 92-93 | NBM | 92.7 | -0.2 | 5.5 | 3.9 | 3.7 |
| Milan | C | 33 | GDPS | 91.6 | -0.2 | 3.8 | 4.3 | 3.6 |
| Miami | F | 92-93 | GFS Global | 92.6 | -0.1 | 5.7 | 3.7 | 3.6 |
| BuenosAires | C | 18 | ECMWF AIFS | 62.6 | 1.8 | 7.8 | 5.1 | 3.3 |
| Beijing | C | 33 | AI-GFS | 91.9 | -0.5 | 5.3 | 3.3 | 2.8 |
| Dallas | F | 100-101 | NAM | 100.7 | -0.2 | 2.7 | 3.0 | 2.5 |
| Paris | C | 33 | AROME HD | 91.6 | -0.2 | 3.1 | 2.5 | 2.3 |
| Shanghai | C | 34 | ICON | 92.9 | 0.3 | 3.3 | 2.0 | 1.7 |
| Chongqing | C | 37 | ECMWF AIFS | 97.4 | 1.2 | 5.2 | 2.7 | 1.5 |
| Wuhan | C | 32 | GDPS | 88.2 | 1.4 | 2.8 | 3.2 | 1.4 |
| Seattle | F | 74-75 | AI-GFS | 74.6 | -0.1 | 1.8 | 1.4 | 1.3 |
| Lucknow | C | 34 | GDPS | 93.9 | -0.7 | 3.2 | 2.0 | 1.3 |
| Tokyo | C | 24 | JMA | 76.0 | -0.8 | 4.7 | 2.0 | 1.2 |
| CapeTown | C | 19 | AI-GFS | 66.1 | 0.1 | 1.3 | 1.7 | 1.2 |
| Austin | F | 96-97 | GEM | 97.0 | -0.5 | 1.9 | 1.7 | 1.2 |
| Busan | C | 29 | ECMWF AIFS | 84.1 | 0.1 | 3.0 | 1.2 | 1.1 |
| Helsinki | C | 17 | GEM | 61.2 | 1.4 | 2.4 | 3.8 | 1.0 |

## Latest Snapshot Per City-Date

| model | n | MAE F | bias F | RMSE F | underforecast >=1F % | overforecast >=1F % |
| --- | --- | --- | --- | --- | --- | --- |
| NAM | 11 | 0.918 | -0.536 | 1.425 | 0.0 | 27.3 |
| AROME HD | 6 | 1.0 | -0.133 | 1.092 | 33.3 | 33.3 |
| RDPS | 17 | 1.529 | 0.706 | 1.913 | 41.2 | 11.8 |
| ICON-EU | 9 | 1.656 | 1.344 | 1.877 | 77.8 | 11.1 |
| ICON-D2 | 5 | 1.68 | -0.04 | 1.825 | 40.0 | 20.0 |
| GEM | 39 | 1.869 | 0.644 | 2.33 | 43.6 | 20.5 |
| NBM | 11 | 1.982 | 1.255 | 2.203 | 63.6 | 18.2 |
| GDPS | 39 | 2.036 | 1.374 | 2.433 | 59.0 | 12.8 |
| HRRR | 11 | 2.055 | 0.927 | 2.444 | 45.5 | 27.3 |
| ICON | 39 | 2.113 | 0.795 | 2.436 | 53.8 | 25.6 |
| HRDPS | 3 | 2.3 | -2.3 | 2.753 | 0.0 | 66.7 |
| ECMWF | 39 | 2.746 | 1.028 | 3.783 | 56.4 | 17.9 |
| AI-GFS | 39 | 2.769 | 1.318 | 3.343 | 56.4 | 17.9 |
| GFS | 39 | 2.813 | 0.977 | 3.5 | 48.7 | 35.9 |

### Latest Snapshot Best City/Model Improvements

| city | unit | settled bracket | best model | pred F | err F | GFS abs | ECMWF abs | gain vs old-best F |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Guangzhou | C | 31 | GDPS | 88.1 | -0.3 | 8.9 | 5.9 | 5.6 |
| Munich | C | 30 | ICON | 86.8 | -0.8 | 6.2 | 5.2 | 4.4 |
| Dallas | F | 100-101 | NAM | 100.3 | 0.2 | 4.5 | 12.0 | 4.3 |
| Karachi | C | 35 | GDPS | 95.1 | -0.1 | 6.4 | 4.0 | 3.9 |
| Milan | C | 33 | GDPS | 91.1 | 0.3 | 3.6 | 4.2 | 3.3 |
| BuenosAires | C | 18 | ICON | 62.2 | 2.2 | 7.8 | 5.5 | 3.3 |
| Miami | F | 92-93 | GFS Global | 93.2 | -0.7 | 3.6 | 3.7 | 2.9 |
| Beijing | C | 33 | AI-GFS | 91.9 | -0.5 | 5.3 | 3.3 | 2.8 |
| Shanghai | C | 34 | ICON | 92.9 | 0.3 | 3.3 | 2.0 | 1.7 |
| Chongqing | C | 37 | ECMWF AIFS | 97.4 | 1.2 | 5.2 | 2.7 | 1.5 |
| Wuhan | C | 32 | GDPS | 88.2 | 1.4 | 2.8 | 3.2 | 1.4 |
| Paris | C | 33 | AROME HD | 91.5 | -0.1 | 2.1 | 1.5 | 1.4 |
| LA | F | 74-75 | GFS Global | 74.5 | 0.0 | 1.4 | 10.4 | 1.4 |
| Lucknow | C | 34 | GDPS | 93.9 | -0.7 | 3.2 | 2.0 | 1.3 |
| Warsaw | C | 20 | JMA | 68.1 | -0.1 | 1.6 | 1.3 | 1.2 |
| Tokyo | C | 24 | JMA | 76.0 | -0.8 | 4.7 | 2.0 | 1.2 |
| Busan | C | 29 | ECMWF AIFS | 84.1 | 0.1 | 3.0 | 1.2 | 1.1 |
| Madrid | C | 40 | AROME HD | 105.0 | -1.0 | 2.0 | 3.1 | 1.0 |
| CapeTown | C | 19 | AI-GFS | 66.1 | 0.1 | 1.1 | 1.7 | 1.0 |
| NYC | F | 72-73 | AI-GFS | 72.3 | 0.2 | 2.8 | 0.9 | 0.7 |

## Output Files

- `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/forecast_enrichment_settlement_bias_v1/prediction_rows.csv`
- `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/forecast_enrichment_settlement_bias_v1/model_summary_earliest.csv`
- `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/forecast_enrichment_settlement_bias_v1/model_summary_latest.csv`
- `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/forecast_enrichment_settlement_bias_v1/city_best_earliest.csv`
- `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/forecast_enrichment_settlement_bias_v1/city_best_latest.csv`
