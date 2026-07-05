# Intraday Weather Regime Atlas v1

## 数据快照

- 数据源：`runtime/weather.db` + atlas feature-factory 分片；CLOB coverage gate=`True`。
- 生成时间 UTC：`2026-07-05T06:21:23+00:00`。
- feature rows：`140308`；state rows(city/date/hour)：`13860`；日期：`2026-05-19`..`2026-07-04`。
- 覆盖城市：`36`；小时：`[10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]`。
- fact 自检：`fact_signal_candidates` 46007 rows / max event_date `2026-07-06`；`settlement_outcomes` max target_date `2026-07-04`。

## 结论

第一版 atlas 已经把日内天气状态从单策略里抽出来：每个 city/date/hour 有 `day_regime`、`intraday_state`、`moisture_cloud_regime`、`running_max_state` 和 `composite_regime`。这些 label 是天气结构，不等于买/不买。

最重要的事实是：同一个 regime 对不同表达的含义相反。`day_open_runway` 描述的是物理上更容易继续打穿，不等于 YES 或 lottery 自动有正 EV；`day_forecast_capped` / `day_forecast_busted` 描述的是封顶结构，也不等于 NO 自动便宜。payoff 表只用于校准这些天气结构是否已被市场价格吸收，不是 live gate。

## Top Day Regimes

| day_regime | states | reheat_step | capped_day | avg_remaining | current_yes_roi | current_bracket_no_roi | d1_no_roi | lottery_yes_roi |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `day_open_runway` | 3979 | 68.9% | 26.9% | 2.87 | -3.7% | -3.4% | -1.8% | -31.3% |
| `day_forecast_busted` | 3772 | 15.0% | 81.9% | 0.22 | -0.2% | -20.6% | -1.1% | -46.0% |
| `day_forecast_capped` | 2444 | 30.6% | 66.7% | 0.52 | -3.4% | -7.8% | -3.3% | -50.1% |
| `day_marginal_runway` | 2023 | 47.6% | 51.8% | 1.09 | -5.9% | -2.3% | -0.8% | -28.2% |
| `day_space_unknown` | 1642 | 41.1% | 54.1% | 1.37 | -3.9% | -4.2% | -4.2% | -6.0% |

## Top Intraday States

| intraday_state | states | reheat_step | capped_day | avg_remaining | current_yes_roi | current_bracket_no_roi | d1_no_roi |
|---|---:|---:|---:|---:|---:|---:|---:|
| `active_warming` | 4847 | 78.5% | 23.8% | 2.79 | -9.4% | -2.5% | -1.5% |
| `mature_fade` | 3497 | 2.7% | 86.5% | 0.07 | 0.2% | -32.3% | 0.2% |
| `fresh_high` | 2170 | 42.7% | 58.1% | 0.98 | -1.6% | -11.6% | -4.2% |
| `pullback_uncertain` | 1271 | 4.8% | 92.1% | 0.08 | -0.2% | -27.4% | -0.3% |
| `plateau_near_high` | 790 | 44.2% | 52.7% | 0.74 | -5.4% | -7.1% | -5.2% |
| `false_fade_risk` | 674 | 55.2% | 43.9% | 1.81 | -3.7% | -7.4% | -4.6% |
| `reheating_after_dip` | 380 | 15.0% | 79.7% | 0.30 | -4.2% | 2.5% | -5.8% |
| `state_unknown` | 131 | 2.3% | 17.6% | 0.18 | -6.9% | 25.6% | -2.7% |
| `slow_warming` | 76 | 30.3% | 69.7% | 0.42 | -6.8% | 1.3% | -2.6% |
| `flat_or_cooling` | 24 | 12.5% | 87.5% | 0.21 | -2.4% | -26.9% | 5.5% |

## Expression Matrix By Day Regime

| day_regime | expression | states | active_dates | avg_ask | hit_rate | roi |
|---|---|---:|---:|---:|---:|---:|
| `day_forecast_busted` | `current_bracket_no` | 3528 | 43 | 0.16 | 12.4% | -20.6% |
| `day_forecast_busted` | `current_yes` | 3181 | 44 | 0.84 | 84.3% | -0.2% |
| `day_forecast_busted` | `d1_no` | 2893 | 44 | 0.88 | 86.7% | -1.1% |
| `day_forecast_busted` | `d2_no` | 2059 | 44 | 0.95 | 94.3% | -1.0% |
| `day_forecast_busted` | `lottery_yes` | 3399 | 44 | 0.02 | 1.1% | -46.0% |
| `day_forecast_capped` | `current_bracket_no` | 2239 | 44 | 0.30 | 28.1% | -7.8% |
| `day_forecast_capped` | `current_yes` | 2153 | 45 | 0.70 | 68.0% | -3.4% |
| `day_forecast_capped` | `d1_no` | 2072 | 45 | 0.79 | 76.7% | -3.3% |
| `day_forecast_capped` | `d2_no` | 1663 | 45 | 0.92 | 89.5% | -2.2% |
| `day_forecast_capped` | `lottery_yes` | 2327 | 45 | 0.02 | 0.8% | -50.1% |
| `day_marginal_runway` | `current_bracket_no` | 1843 | 44 | 0.44 | 43.2% | -2.3% |
| `day_marginal_runway` | `current_yes` | 1830 | 45 | 0.54 | 51.1% | -5.9% |
| `day_marginal_runway` | `d1_no` | 1757 | 45 | 0.78 | 76.9% | -0.8% |
| `day_marginal_runway` | `d2_no` | 1539 | 45 | 0.83 | 77.3% | -6.7% |
| `day_marginal_runway` | `lottery_yes` | 1922 | 45 | 0.02 | 1.5% | -28.2% |
| `day_open_runway` | `current_bracket_no` | 2702 | 44 | 0.63 | 60.4% | -3.4% |
| `day_open_runway` | `current_yes` | 2873 | 45 | 0.35 | 33.9% | -3.7% |
| `day_open_runway` | `d1_no` | 3104 | 45 | 0.83 | 81.4% | -1.8% |
| `day_open_runway` | `d2_no` | 3156 | 45 | 0.82 | 80.5% | -1.2% |
| `day_open_runway` | `lottery_yes` | 3853 | 45 | 0.02 | 1.1% | -31.3% |
| `day_space_unknown` | `current_bracket_no` | 1387 | 12 | 0.38 | 36.0% | -4.2% |
| `day_space_unknown` | `current_yes` | 1361 | 13 | 0.63 | 60.9% | -3.9% |
| `day_space_unknown` | `d1_no` | 1342 | 13 | 0.83 | 79.7% | -4.2% |
| `day_space_unknown` | `d2_no` | 1100 | 13 | 0.87 | 85.3% | -2.2% |
| `day_space_unknown` | `lottery_yes` | 1545 | 13 | 0.01 | 0.8% | -6.0% |

## Forward Recording Protocol

后续 live/shadow runner 不需要先做交易决策；每个 city/date/hour 先记录同一套 regime ledger：

- identity: `generated_at_utc`, `city`, `target_date`, `decision_hour_local`, `decision_snapshot_ts_utc`, `source_profile`, `forecast_source`, `forecast_clock_source`
- PIT mechanism: `current_temp`, `running_max`, `minutes_since_running_max`, `forecast_gap_to_running`, `forecast_peak_delta`, `temp_trend_1h/3h`, `rh`, `dewpoint_depression`, `wind_speed`, `sky_cover`
- labels: `day_regime`, `intraday_state`, `moisture_cloud_regime`, `wind_regime`, `running_max_state`, `composite_regime`
- quotes by expression: current YES ask, real current-bracket NO ask, d1/d2 NO ask, cheapest higher YES ask, ask size/depth when available
- settlement join later only: final max, final winning bracket, expression payoff/ROI

## 输出文件

- State feature table: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv`
- Day-regime summary: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/day_regime_summary.csv`
- Intraday-state summary: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_state_summary.csv`
- Expression matrix: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/expression_matrix_by_regime.csv`
- JSON manifest: `docs/analysis/2026-06/2026-06-24-intraday-weather-regime-atlas-v1.json`

## 口径限制

- 6/21..6/23 的 forecast clock 只有 native fact 覆盖的一部分，缺失 rows 保持 `day_space_unknown`，没有 forward-fill 后验 peak。
- 这里的 ROI 是 quote ask proxy 的表达校准，不是 live fill PnL，也没有做三道门 promotion。
- label 阈值是物理分桶尺度，用来描述 regime；后续若做交易模型，应把这些作为特征/分层，而不是硬 gate 清单。
