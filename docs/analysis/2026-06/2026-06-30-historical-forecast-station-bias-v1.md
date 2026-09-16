# Historical Forecast Station Bias v1

Generated: 2026-06-30

## Verdict

用户记忆的方向是对的：早期 mid-price / probability-model 不是看单城市单天，而是用历史 forecast-vs-station 误差分布校准概率。这里复现的核心口径是：

`error = station actual daily max - model forecast daily max`

正数表示实际比预报更热；负数表示预报比实际更热。对温度 NO 来说，正尾越厚，越容易被实际高温打穿。

## Data

- Source checkout: `/Users/deepsleep/projects/weather-predict`
- Actual: `cache/wu_obs/wu_obs_<ICAO>.csv`, station daily max from WU/IEM-style station rows.
- Forecast: `cache`, `cache_global`, `cache_global_full`; for each city/model pick the cache with most hourly rows.
- Forecast cache and WU temperatures are in °F; C-market cities are converted to °C error for market-unit summaries.
- Models included here: GFS and ECMWF, because these are the sources relevant to current weather strategy routing.

Important boundary: the old review doc mentions about 735 days, and those two-year GFS v4 files exist for a subset of cities. Full ECMWF coverage is mostly about 354 common days. This report reports the actual cache used per city/model in `forecast_cache_inventory.csv`.

## Overall Best-Model Distribution

| unit | rows | first | last | bias | MAE | RMSE | p10 | p50 | p90 | actual>=fcst+1 | fcst>=actual+1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C | 10005 | 2024-05-01 | 2026-05-07 | 0.58 | 1.20 | 1.57 | -1.11 | 0.67 | 2.22 | 39.0% | 11.6% |
| F | 6911 | 2024-04-30 | 2026-05-06 | 0.52 | 1.30 | 1.72 | -1.40 | 0.50 | 2.40 | 37.8% | 15.2% |

## Focus Cities Best-Model Bias

| city | unit | model | n | range | bias | MAE | RMSE | p10 | p50 | p90 | actual>=fcst+1 | fcst>=actual+1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Chengdu | C | ecmwf | 356 | 2025-05-10..2026-04-30 | 1.12 | 1.47 | 1.85 | -0.67 | 1.11 | 3.08 | 52.2% | 7.0% |
| Tokyo | C | gfs | 737 | 2024-05-01..2026-05-07 | -0.09 | 0.82 | 1.07 | -1.44 | -0.06 | 1.17 | 13.8% | 17.5% |
| NYC | F | gfs | 737 | 2024-04-30..2026-05-06 | -0.27 | 1.09 | 1.49 | -1.90 | -0.40 | 1.40 | 15.6% | 29.7% |
| SanFrancisco | F | ecmwf | 356 | 2025-05-09..2026-04-29 | 1.32 | 2.19 | 2.61 | -1.70 | 1.55 | 4.10 | 59.8% | 18.0% |
| Seattle | F | gfs | 356 | 2025-05-09..2026-04-29 | 0.79 | 1.05 | 1.34 | -0.50 | 0.70 | 2.15 | 41.0% | 3.6% |
| Miami | F | gfs | 737 | 2024-04-30..2026-05-06 | 0.13 | 0.92 | 1.22 | -1.30 | 0.10 | 1.60 | 22.4% | 15.5% |
| Karachi | C | ecmwf | 356 | 2025-05-10..2026-04-30 | -0.12 | 1.19 | 1.56 | -1.86 | 0.00 | 1.50 | 25.6% | 27.5% |
| Jeddah | C | ecmwf | 356 | 2025-05-10..2026-04-30 | -0.97 | 1.25 | 1.63 | -2.53 | -0.89 | 0.36 | 3.9% | 47.8% |
| Lucknow | C | ecmwf | 356 | 2025-05-10..2026-04-30 | 0.08 | 0.83 | 1.17 | -1.00 | 0.22 | 1.28 | 17.7% | 11.0% |
| Chongqing | C | ecmwf | 356 | 2025-05-10..2026-04-30 | 0.80 | 1.35 | 1.84 | -1.11 | 0.56 | 2.78 | 40.7% | 11.8% |
| Manila | C | gfs | 356 | 2025-05-10..2026-04-30 | 1.29 | 1.47 | 1.73 | -0.14 | 1.39 | 2.61 | 64.0% | 3.9% |
| Shanghai | C | gfs | 737 | 2024-05-01..2026-05-07 | 0.95 | 1.22 | 1.54 | -0.44 | 1.00 | 2.28 | 50.7% | 5.0% |

## Hottest Positive-Tail Cities

| city | unit | model | n | bias | p90 | p95 | actual>=fcst+1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SanFrancisco | F | ecmwf | 356 | 1.32 | 4.10 | 4.70 | 59.8% |
| Dallas | F | ecmwf | 356 | 1.11 | 3.45 | 4.30 | 48.3% |
| Austin | F | gfs | 737 | 1.62 | 3.40 | 4.00 | 68.1% |
| Chengdu | C | ecmwf | 356 | 1.12 | 3.08 | 3.51 | 52.2% |
| Lagos | C | ecmwf | 346 | 1.51 | 2.94 | 3.17 | 72.0% |
| Beijing | C | ecmwf | 356 | 1.23 | 2.78 | 3.22 | 58.1% |
| Chongqing | C | ecmwf | 356 | 0.80 | 2.78 | 3.57 | 40.7% |
| Guangzhou | C | gfs | 356 | 0.58 | 2.69 | 3.17 | 42.4% |
| Manila | C | gfs | 356 | 1.29 | 2.61 | 3.01 | 64.0% |
| Atlanta | F | gfs | 356 | 1.15 | 2.50 | 2.90 | 56.2% |
| KualaLumpur | C | ecmwf | 356 | 1.14 | 2.50 | 3.08 | 57.0% |
| Phoenix | F | gfs | 737 | 1.12 | 2.40 | 2.72 | 55.6% |

## Cold/Overforecast-Tail Cities

| city | unit | model | n | bias | p10 | p05 | fcst>=actual+1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Boston | F | gfs | 737 | -0.51 | -3.00 | -3.80 | 37.7% |
| Taipei | C | gfs | 356 | -0.07 | -2.61 | -5.74 | 21.9% |
| Jeddah | C | ecmwf | 356 | -0.97 | -2.53 | -2.92 | 47.8% |
| Houston | F | gfs | 356 | -0.78 | -2.40 | -2.62 | 47.8% |
| PanamaCity | C | gfs | 353 | -0.07 | -2.28 | -3.19 | 25.8% |
| NYC | F | gfs | 737 | -0.27 | -1.90 | -2.50 | 29.7% |
| Karachi | C | ecmwf | 356 | -0.12 | -1.86 | -2.44 | 27.5% |
| SanFrancisco | F | ecmwf | 356 | 1.32 | -1.70 | -2.62 | 18.0% |
| Guangzhou | C | gfs | 356 | 0.58 | -1.61 | -2.43 | 16.0% |
| MexicoCity | C | ecmwf | 356 | -0.30 | -1.53 | -1.83 | 26.4% |
| Tokyo | C | gfs | 737 | -0.09 | -1.44 | -1.83 | 17.5% |
| Miami | F | gfs | 737 | 0.13 | -1.30 | -1.72 | 15.5% |

## Strategy Implication

- 单日复盘只能解释事故，不能给出 source/station margin。策略侧应该使用 city + source 的历史误差分布，至少拿 p10/p50/p90 或 tail probability 当 feature。
- current-bracket NO 的危险不是一个固定 0.5°C/1°F margin，而是该城市/模型在相同站点口径下的正尾概率。
- d2/higher NO 和 capped forecast 也一样：要看 `actual - forecast` 的正尾，而不是只看 forecast peak 离 bracket 有多远。
- 这个层应该是 feature/calibration layer，不是新增 hard gate；是否交易仍由盘口价格、route、执行纪律和实时 regime 一起决定。

## Artifacts

- Daily rows: `docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/daily_error_rows.csv`
- City/model summary: `docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/city_model_error_summary.csv`
- Month summary: `docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/city_model_month_error_summary.csv`
- Focus summary: `docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/focus_best_model_error_summary.csv`
- Cache inventory: `docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/forecast_cache_inventory.csv`
- Generated CSVs are reproducible and ignored by git per `.gitignore`; rerun the script to recreate them.
