# Intraday Weather Regime Atlas v1

## 数据快照

- 数据源：`runtime/weather.db` + atlas feature-factory 分片；CLOB coverage gate=`True`。
- 生成时间 UTC：`2026-06-25T04:53:49+00:00`。
- feature rows：`121129`；state rows(city/date/hour)：`11980`；日期：`2026-05-19`..`2026-06-24`。
- 覆盖城市：`36`；小时：`[10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]`。
- fact 自检：`fact_signal_candidates` 37655 rows / max event_date `2026-06-26`；`settlement_outcomes` max target_date `2026-06-23`。

## 结论

第一版 atlas 已经把日内天气状态从单策略里抽出来：每个 city/date/hour 有 `day_regime`、`intraday_state`、`moisture_cloud_regime`、`running_max_state` 和 `composite_regime`。这些 label 是天气结构，不等于买/不买。

最重要的事实是：同一个 regime 对不同表达的含义相反。`day_open_runway` 描述的是物理上更容易继续打穿，不等于 YES 或 lottery 自动有正 EV；`day_forecast_capped` / `day_forecast_busted` 描述的是封顶结构，也不等于 NO 自动便宜。payoff 表只用于校准这些天气结构是否已被市场价格吸收，不是 live gate。

## Top Day Regimes

| day_regime | states | reheat_step | capped_day | avg_remaining | current_yes_roi | current_bracket_no_roi | d1_no_roi | lottery_yes_roi |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `day_open_runway` | 3396 | 75.0% | 22.7% | 3.21 | -4.7% | -2.8% | 0.2% | -22.4% |
| `day_forecast_busted` | 3146 | 12.8% | 83.1% | 0.16 | 0.7% | -25.5% | -0.7% | -42.6% |
| `day_forecast_capped` | 2734 | 25.3% | 71.6% | 0.39 | -0.6% | -15.6% | -2.4% | -52.4% |
| `day_marginal_runway` | 2077 | 50.6% | 48.4% | 1.04 | -8.6% | 0.7% | -5.3% | -36.0% |
| `day_space_unknown` | 627 | 42.7% | 45.6% | 1.53 | -4.0% | -2.3% | -3.2% | 2.7% |

## Top Intraday States

| intraday_state | states | reheat_step | capped_day | avg_remaining | current_yes_roi | current_bracket_no_roi | d1_no_roi |
|---|---:|---:|---:|---:|---:|---:|---:|
| `active_warming` | 4230 | 78.0% | 24.0% | 2.75 | -7.7% | -3.2% | -1.0% |
| `mature_fade` | 3018 | 2.6% | 86.6% | 0.07 | 0.5% | -36.1% | 0.3% |
| `fresh_high` | 1720 | 43.8% | 56.0% | 1.04 | -0.9% | -12.5% | -3.8% |
| `pullback_uncertain` | 1149 | 4.7% | 88.9% | 0.09 | -0.0% | -29.6% | -0.1% |
| `plateau_near_high` | 771 | 46.0% | 51.6% | 0.76 | -5.7% | -6.7% | -5.4% |
| `false_fade_risk` | 600 | 54.7% | 43.5% | 1.79 | -2.6% | -8.1% | -4.9% |
| `reheating_after_dip` | 326 | 15.3% | 77.9% | 0.32 | -4.2% | 2.6% | -6.0% |
| `slow_warming` | 112 | 30.4% | 62.5% | 0.45 | -4.1% | -7.9% | -1.5% |
| `flat_or_cooling` | 40 | 12.5% | 80.0% | 0.18 | -2.7% | -22.8% | 1.9% |
| `state_unknown` | 14 | 14.3% | 21.4% | 1.21 | -21.2% | 44.9% | 2.9% |

## Expression Matrix By Day Regime

| day_regime | expression | states | active_dates | avg_ask | hit_rate | roi |
|---|---|---:|---:|---:|---:|---:|
| `day_forecast_busted` | `current_bracket_no` | 2940 | 36 | 0.15 | 11.1% | -25.5% |
| `day_forecast_busted` | `current_yes` | 2609 | 35 | 0.85 | 86.0% | 0.7% |
| `day_forecast_busted` | `d1_no` | 2354 | 36 | 0.88 | 87.4% | -0.7% |
| `day_forecast_busted` | `d2_no` | 1644 | 36 | 0.96 | 96.0% | -0.1% |
| `day_forecast_busted` | `lottery_yes` | 2815 | 36 | 0.02 | 1.1% | -42.6% |
| `day_forecast_capped` | `current_bracket_no` | 2502 | 36 | 0.26 | 21.8% | -15.6% |
| `day_forecast_capped` | `current_yes` | 2368 | 36 | 0.75 | 74.3% | -0.6% |
| `day_forecast_capped` | `d1_no` | 2264 | 36 | 0.82 | 80.2% | -2.4% |
| `day_forecast_capped` | `d2_no` | 1809 | 36 | 0.93 | 91.8% | -1.1% |
| `day_forecast_capped` | `lottery_yes` | 2554 | 36 | 0.02 | 0.7% | -52.4% |
| `day_marginal_runway` | `current_bracket_no` | 1873 | 36 | 0.46 | 46.3% | 0.7% |
| `day_marginal_runway` | `current_yes` | 1853 | 36 | 0.53 | 48.6% | -8.6% |
| `day_marginal_runway` | `d1_no` | 1793 | 36 | 0.75 | 71.2% | -5.3% |
| `day_marginal_runway` | `d2_no` | 1597 | 36 | 0.83 | 78.0% | -5.9% |
| `day_marginal_runway` | `lottery_yes` | 1967 | 36 | 0.02 | 1.5% | -36.0% |
| `day_open_runway` | `current_bracket_no` | 2241 | 36 | 0.67 | 65.6% | -2.8% |
| `day_open_runway` | `current_yes` | 2433 | 36 | 0.30 | 28.9% | -4.7% |
| `day_open_runway` | `d1_no` | 2631 | 36 | 0.83 | 83.2% | 0.2% |
| `day_open_runway` | `d2_no` | 2700 | 36 | 0.81 | 78.7% | -2.4% |
| `day_open_runway` | `lottery_yes` | 3281 | 36 | 0.02 | 1.2% | -22.4% |
| `day_space_unknown` | `current_bracket_no` | 437 | 3 | 0.36 | 34.8% | -2.3% |
| `day_space_unknown` | `current_yes` | 430 | 3 | 0.64 | 61.6% | -4.0% |
| `day_space_unknown` | `d1_no` | 431 | 3 | 0.84 | 81.4% | -3.2% |
| `day_space_unknown` | `d2_no` | 352 | 3 | 0.87 | 86.9% | -0.5% |
| `day_space_unknown` | `lottery_yes` | 499 | 3 | 0.01 | 0.8% | 2.7% |

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
