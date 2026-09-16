# Forecast Station Error Distribution v1

Status: snapshot
Generated: 2026-06-30T08:36:26+00:00

## Data Snapshot

- Actual max source: data-feed `source_events/sources.jsonl`, station-grain observed max.
- Forecast source: data-feed `paper_snapshots/snapshot_*.json`, deduped to `city + target_date + snapshot_ts + forecast_source`.
- Coverage rows: `1698` forecast snapshots.
- City-days with actual station max: `66`.
- Date range in joined rows: `2026-06-29`..`2026-06-30`.

`error_native = forecast_max_native - actual_station_max_native`.

- Negative error: forecast under actual station max. Dangerous for higher/tail NO.
- Positive error: forecast over actual station max. Dangerous for current-bracket NO pass-through.

## Overall

- mean error: `4.395` native degrees.
- median error: `3.100`.
- p10/p90: `-1.200` / `11.700`.
- under actual by >=1 degree: `13.4%`.
- under actual by >=2 degrees: `7.7%`.
- over actual by >=1 degree: `71.8%`.

## 中文摘要

本报告只研究 forecast ceiling 和实际结算站点高温之间的误差，不是交易回测。

- `error_native < 0`：预报低估实际站点高温，最容易伤害 `higher/tail NO`。
- `error_native > 0`：预报高估实际站点高温，最容易伤害 `current-bracket NO` 的继续穿越逻辑。
- Chengdu 2026-06-30 是典型 forecast under-station：ECMWF 从 30.3 下修到 27.5/28.4，但 ZUUU 实际到 32.0。
- Tokyo 2026-06-30 是相反方向：GFS 预测 28.1/28.3，但 RJTT 截至当前 source-events 只到 27.0。

这说明 forecast reliability 不能只做全局标签。对 `tail/d2 NO`，需要显式估计
`actual_station_max - forecast_max` 的上冲风险；对 `current NO`，需要估计
`forecast_max - actual_station_max` 的穿越失败风险。

## By City

| city | rows | city_dates | mean_error | median_error | under_by_1_rate | under_by_2_rate | over_by_1_rate | min_error | max_error |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Seattle | 54 | 2 | 7.3811 | 10.16 | 0.0 | 0.0 | 0.8519 | -0.66 | 12.56 |
| Dallas | 53 | 2 | 7.6781 | 11.4 | 0.0 | 0.0 | 1.0 | 1.02 | 11.6 |
| Austin | 52 | 2 | 10.5062 | 16.22 | 0.0 | 0.0 | 1.0 | 1.3 | 17.02 |
| Denver | 52 | 2 | 18.395 | 31.44 | 0.0 | 0.0 | 0.6923 | -0.88 | 32.24 |
| Miami | 52 | 2 | 6.1696 | 8.28 | 0.0 | 0.0 | 1.0 | 1.74 | 9.38 |
| SanFrancisco | 52 | 2 | 1.6738 | 6.28 | 0.4038 | 0.4038 | 0.5962 | -6.44 | 7.08 |
| Chicago | 51 | 2 | 10.1129 | 14.36 | 0.0 | 0.0 | 1.0 | 1.96 | 14.96 |
| LA | 51 | 2 | 2.3498 | 2.96 | 0.0 | 0.0 | 0.5882 | -0.56 | 4.96 |
| London | 51 | 2 | 2.9373 | 4.1 | 0.0 | 0.0 | 0.7647 | -0.9 | 6.6 |
| Madrid | 51 | 2 | 4.1216 | 6.9 | 0.0 | 0.0 | 0.6078 | -0.6 | 7.3 |
| Amsterdam | 50 | 2 | 2.174 | 2.4 | 0.0 | 0.0 | 0.78 | 0.6 | 3.7 |
| Houston | 50 | 2 | 7.9512 | 11.04 | 0.0 | 0.0 | 0.9 | 0.38 | 12.94 |
| Karachi | 50 | 2 | 2.066 | 0.6 | 0.0 | 0.0 | 0.38 | -0.5 | 5.9 |
| Paris | 50 | 2 | 4.582 | 5.7 | 0.0 | 0.0 | 1.0 | 1.2 | 7.4 |
| Ankara | 49 | 2 | 4.1122 | 3.3 | 0.0 | 0.0 | 1.0 | 3.1 | 6.1 |
| Atlanta | 49 | 2 | 8.7065 | 11.68 | 0.0 | 0.0 | 0.9592 | 0.78 | 12.48 |
| Munich | 49 | 2 | 2.8184 | 3.6 | 0.0 | 0.0 | 0.8163 | 0.0 | 4.2 |
| Helsinki | 47 | 2 | 1.9979 | 1.6 | 0.0 | 0.0 | 1.0 | 1.3 | 5.2 |
| NYC | 47 | 2 | 8.9472 | 9.82 | 0.0 | 0.0 | 1.0 | 3.98 | 13.32 |
| CapeTown | 46 | 2 | 4.6891 | 4.6 | 0.0 | 0.0 | 1.0 | 4.3 | 5.4 |
| Milan | 46 | 2 | 6.7978 | 6.8 | 0.0 | 0.0 | 1.0 | 5.9 | 8.7 |
| PanamaCity | 46 | 2 | 4.0543 | 5.8 | 0.0 | 0.0 | 0.6739 | -0.2 | 6.4 |
| SaoPaulo | 45 | 2 | 5.46 | 8.8 | 0.3111 | 0.3111 | 0.6889 | -2.5 | 9.3 |
| BuenosAires | 44 | 2 | 7.6227 | 11.2 | 0.2955 | 0.0 | 0.7045 | -1.4 | 11.7 |
| Jeddah | 43 | 2 | 3.6907 | 2.4 | 0.0 | 0.0 | 1.0 | 1.7 | 8.4 |
| Warsaw | 42 | 2 | 6.519 | 7.35 | 0.0 | 0.0 | 1.0 | 3.3 | 7.5 |
| Beijing | 31 | 1 | -2.0032 | -2.3 | 0.6774 | 0.6774 | 0.0 | -2.7 | -0.9 |
| Busan | 31 | 1 | -0.3323 | -0.3 | 0.0 | 0.0 | 0.0 | -0.4 | -0.3 |
| Chongqing | 31 | 1 | -0.3355 | -1.0 | 0.6774 | 0.0 | 0.3226 | -1.2 | 1.3 |
| Guangzhou | 31 | 1 | -2.6258 | -2.8 | 1.0 | 0.871 | 0.0 | -2.9 | -1.9 |
| KualaLumpur | 31 | 1 | -1.0194 | -0.8 | 0.2903 | 0.2903 | 0.0 | -2.0 | -0.4 |
| Lucknow | 31 | 1 | 3.4548 | 3.7 | 0.0 | 0.0 | 1.0 | 2.7 | 3.9 |
| Manila | 31 | 1 | 2.0516 | 2.6 | 0.0 | 0.0 | 0.6452 | 0.1 | 3.3 |
| Shanghai | 31 | 1 | -1.7484 | -2.2 | 0.7742 | 0.6452 | 0.0 | -2.6 | -0.2 |
| Singapore | 30 | 1 | 2.44 | 2.4 | 0.0 | 0.0 | 1.0 | 2.3 | 2.6 |
| Taipei | 30 | 1 | -1.0067 | -1.1 | 0.5333 | 0.0 | 0.0 | -1.2 | -0.7 |
| Tokyo | 30 | 1 | 0.92 | 1.2 | 0.0 | 0.0 | 0.8 | -0.8 | 1.6 |
| Wellington | 30 | 1 | -1.1633 | -1.1 | 1.0 | 0.0 | 0.0 | -1.3 | -1.1 |
| Wuhan | 30 | 1 | 1.24 | 1.1 | 0.0 | 0.0 | 1.0 | 1.0 | 1.7 |
| Chengdu | 28 | 1 | -3.2107 | -3.6 | 1.0 | 0.6429 | 0.0 | -4.5 | -1.7 |

## By Forecast Source

| forecast_source | rows | city_dates | mean_error | median_error | under_by_1_rate | under_by_2_rate | over_by_1_rate | min_error | max_error |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| open_meteo_live_ecmwf | 930 | 37 | 3.1988 | 3.02 | 0.1366 | 0.0892 | 0.7065 | -6.44 | 11.7 |
| open_meteo_live_gfs | 768 | 30 | 5.8443 | 3.3 | 0.1315 | 0.0612 | 0.7318 | -2.9 | 32.24 |

## By Local Decision Bucket

| decision_bucket | rows | city_dates | mean_error | median_error | under_by_1_rate | under_by_2_rate | over_by_1_rate | min_error | max_error |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| h16_plus | 566 | 66 | 5.4784 | 4.1 | 0.0512 | 0.0212 | 0.7898 | -6.44 | 32.24 |
| pre_8 | 558 | 57 | 3.7105 | 3.0 | 0.1577 | 0.086 | 0.7186 | -4.74 | 31.94 |
| h10_12 | 153 | 53 | 4.5835 | 2.6 | 0.1503 | 0.1046 | 0.6601 | -4.74 | 31.44 |
| h12_14 | 149 | 43 | 4.3314 | 2.6 | 0.2148 | 0.1342 | 0.6644 | -6.44 | 31.44 |
| h14_16 | 137 | 43 | 4.1861 | 1.94 | 0.1898 | 0.0803 | 0.6277 | -6.44 | 32.24 |
| h08_10 | 135 | 45 | 2.7551 | 2.4 | 0.2222 | 0.1704 | 0.6296 | -4.74 | 31.44 |

## Worst Under-Forecast Cases

| city | target_date | snapshot_ts_utc | decision_bucket | forecast_source | forecast_max_native | actual_station_max_native | error_native | station | actual_max_report_ts_utc |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SanFrancisco | 2026-06-29 | 2026-06-29T19:52:16Z | h12_14 | open_meteo_live_ecmwf | 66.6 | 73.04 | -6.44 | KSFO | 2026-06-29T22:56:00+00:00 |
| SanFrancisco | 2026-06-29 | 2026-06-29T20:28:37Z | h12_14 | open_meteo_live_ecmwf | 66.6 | 73.04 | -6.44 | KSFO | 2026-06-29T22:56:00+00:00 |
| SanFrancisco | 2026-06-29 | 2026-06-29T21:04:37Z | h14_16 | open_meteo_live_ecmwf | 66.6 | 73.04 | -6.44 | KSFO | 2026-06-29T22:56:00+00:00 |
| SanFrancisco | 2026-06-29 | 2026-06-29T21:40:53Z | h14_16 | open_meteo_live_ecmwf | 66.6 | 73.04 | -6.44 | KSFO | 2026-06-29T22:56:00+00:00 |
| SanFrancisco | 2026-06-29 | 2026-06-29T22:17:08Z | h14_16 | open_meteo_live_ecmwf | 66.6 | 73.04 | -6.44 | KSFO | 2026-06-29T22:56:00+00:00 |
| SanFrancisco | 2026-06-29 | 2026-06-29T22:53:55Z | h14_16 | open_meteo_live_ecmwf | 66.6 | 73.04 | -6.44 | KSFO | 2026-06-29T22:56:00+00:00 |
| SanFrancisco | 2026-06-29 | 2026-06-29T23:30:04Z | h16_plus | open_meteo_live_ecmwf | 66.6 | 73.04 | -6.44 | KSFO | 2026-06-29T22:56:00+00:00 |
| SanFrancisco | 2026-06-29 | 2026-06-30T00:06:57Z | h16_plus | open_meteo_live_ecmwf | 66.6 | 73.04 | -6.44 | KSFO | 2026-06-29T22:56:00+00:00 |
| SanFrancisco | 2026-06-29 | 2026-06-30T00:43:21Z | h16_plus | open_meteo_live_ecmwf | 66.6 | 73.04 | -6.44 | KSFO | 2026-06-29T22:56:00+00:00 |
| SanFrancisco | 2026-06-29 | 2026-06-30T01:19:46Z | h16_plus | open_meteo_live_ecmwf | 66.8 | 73.04 | -6.24 | KSFO | 2026-06-29T22:56:00+00:00 |
| SanFrancisco | 2026-06-29 | 2026-06-30T01:56:36Z | h16_plus | open_meteo_live_ecmwf | 66.8 | 73.04 | -6.24 | KSFO | 2026-06-29T22:56:00+00:00 |
| SanFrancisco | 2026-06-29 | 2026-06-29T14:43:32Z | pre_8 | open_meteo_live_ecmwf | 68.3 | 73.04 | -4.74 | KSFO | 2026-06-29T22:56:00+00:00 |
| SanFrancisco | 2026-06-29 | 2026-06-29T15:18:22Z | h08_10 | open_meteo_live_ecmwf | 68.3 | 73.04 | -4.74 | KSFO | 2026-06-29T22:56:00+00:00 |
| SanFrancisco | 2026-06-29 | 2026-06-29T16:16:39Z | h08_10 | open_meteo_live_ecmwf | 68.3 | 73.04 | -4.74 | KSFO | 2026-06-29T22:56:00+00:00 |
| SanFrancisco | 2026-06-29 | 2026-06-29T16:26:45Z | h08_10 | open_meteo_live_ecmwf | 68.3 | 73.04 | -4.74 | KSFO | 2026-06-29T22:56:00+00:00 |
| SanFrancisco | 2026-06-29 | 2026-06-29T16:36:16Z | h08_10 | open_meteo_live_ecmwf | 68.3 | 73.04 | -4.74 | KSFO | 2026-06-29T22:56:00+00:00 |
| SanFrancisco | 2026-06-29 | 2026-06-29T16:45:19Z | h08_10 | open_meteo_live_ecmwf | 68.3 | 73.04 | -4.74 | KSFO | 2026-06-29T22:56:00+00:00 |
| SanFrancisco | 2026-06-29 | 2026-06-29T17:23:37Z | h10_12 | open_meteo_live_ecmwf | 68.3 | 73.04 | -4.74 | KSFO | 2026-06-29T22:56:00+00:00 |
| SanFrancisco | 2026-06-29 | 2026-06-29T18:00:38Z | h10_12 | open_meteo_live_ecmwf | 68.3 | 73.04 | -4.74 | KSFO | 2026-06-29T22:56:00+00:00 |
| SanFrancisco | 2026-06-29 | 2026-06-29T18:38:06Z | h10_12 | open_meteo_live_ecmwf | 68.3 | 73.04 | -4.74 | KSFO | 2026-06-29T22:56:00+00:00 |

## Chengdu / Tokyo 2026-06-30

| city | target_date | snapshot_ts_utc | decision_bucket | forecast_source | forecast_max_native | actual_station_max_native | error_native | station | actual_max_report_ts_utc |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Chengdu | 2026-06-30 | 2026-06-30T06:13:20Z | h14_16 | open_meteo_live_ecmwf | 28.4 | 32.0 | -3.6 | ZUUU | 2026-06-30T06:00:00+00:00 |
| Tokyo | 2026-06-30 | 2026-06-30T08:02:37Z | h16_plus | open_meteo_live_gfs | 28.3 | 27.0 | 1.3 | RJTT | 2026-06-30T05:00:00+00:00 |

## Interpretation

This report is not a trading rule and does not approve live changes.  It shows
that forecast ceiling reliability is a separate mechanism layer from regime
routing.  Tail/higher NO needs a calibrated station-error buffer; current NO
needs a different buffer for over-forecast pass-through risk.

Generated files:

- `docs/analysis/2026-06/generated/forecast_station_error_distribution_v1/forecast_station_error_rows.csv`
- `docs/analysis/2026-06/generated/forecast_station_error_distribution_v1/summary_by_city.csv`
- `docs/analysis/2026-06/generated/forecast_station_error_distribution_v1/summary_by_forecast_source.csv`
- `docs/analysis/2026-06/generated/forecast_station_error_distribution_v1/summary_by_decision_bucket.csv`
