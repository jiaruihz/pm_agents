# Intraday Weather Regime Atlas v1

## 数据快照

- 数据源：`runtime/weather.db` + atlas feature-factory 分片；CLOB coverage gate=`True`。
- 生成时间 UTC：`2026-07-22T07:17:34+00:00`。
- feature rows：`144567`；state rows(city/date/hour)：`14369`；日期：`2026-05-19`..`2026-07-08`。
- 覆盖城市：`36`；小时：`[10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]`。
- fact 自检：`fact_signal_candidates` 62258 rows / max event_date `2026-07-23`；`settlement_outcomes` max target_date `2026-07-21`。

## 结论

第一版 atlas 已经把日内天气状态从单策略里抽出来：每个 city/date/hour 有 `day_regime`、`intraday_state`、`moisture_cloud_regime`、`running_max_state` 和 `composite_regime`。这些 label 是天气结构，不等于买/不买。

最重要的事实是：同一个 regime 对不同表达的含义相反。`day_open_runway` 描述的是物理上更容易继续打穿，不等于 YES 或 lottery 自动有正 EV；`day_forecast_capped` / `day_forecast_busted` 描述的是封顶结构，也不等于 NO 自动便宜。payoff 表只用于校准这些天气结构是否已被市场价格吸收，不是 live gate。

## Top Day Regimes

| day_regime | states | reheat_step | capped_day | avg_remaining | current_yes_roi | current_bracket_no_roi | d1_no_roi | lottery_yes_roi |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `day_open_runway` | 4705 | 68.9% | 26.8% | 2.90 | -3.3% | -3.8% | -2.1% | -33.3% |
| `day_forecast_busted` | 4461 | 15.5% | 81.1% | 0.23 | -0.2% | -19.7% | -1.1% | -40.3% |
| `day_forecast_capped` | 2841 | 30.7% | 65.5% | 0.54 | -5.1% | -6.1% | -3.4% | -46.3% |
| `day_marginal_runway` | 2362 | 48.3% | 50.8% | 1.10 | -7.0% | -1.2% | -1.0% | -14.7% |

## Top Intraday States

| intraday_state | states | reheat_step | capped_day | avg_remaining | current_yes_roi | current_bracket_no_roi | d1_no_roi |
|---|---:|---:|---:|---:|---:|---:|---:|
| `active_warming` | 5055 | 78.6% | 23.7% | 2.81 | -9.3% | -2.6% | -1.3% |
| `mature_fade` | 3588 | 2.7% | 86.3% | 0.07 | 0.2% | -32.7% | 0.1% |
| `fresh_high` | 2247 | 43.0% | 57.7% | 0.98 | -2.2% | -10.7% | -4.4% |
| `pullback_uncertain` | 1321 | 4.8% | 92.2% | 0.08 | -0.1% | -29.2% | -0.3% |
| `plateau_near_high` | 831 | 44.5% | 52.2% | 0.76 | -6.3% | -6.1% | -5.1% |
| `false_fade_risk` | 700 | 55.6% | 43.4% | 1.82 | -3.4% | -7.7% | -4.6% |
| `reheating_after_dip` | 396 | 14.9% | 79.8% | 0.31 | -3.8% | -0.4% | -4.7% |
| `state_unknown` | 131 | 2.3% | 4.6% | 0.18 | -82.3% | 25.6% | 36.9% |
| `slow_warming` | 76 | 30.3% | 69.7% | 0.42 | -6.8% | 1.3% | -2.6% |
| `flat_or_cooling` | 24 | 12.5% | 87.5% | 0.21 | -2.4% | -26.9% | 5.5% |

## Expression Matrix By Day Regime

| day_regime | expression | states | active_dates | avg_ask | hit_rate | roi |
|---|---|---:|---:|---:|---:|---:|
| `day_forecast_busted` | `current_bracket_no` | 4172 | 49 | 0.16 | 13.2% | -19.7% |
| `day_forecast_busted` | `current_yes` | 3786 | 50 | 0.84 | 83.7% | -0.2% |
| `day_forecast_busted` | `d1_no` | 3456 | 50 | 0.87 | 86.4% | -1.1% |
| `day_forecast_busted` | `d2_no` | 2465 | 50 | 0.95 | 94.6% | -0.5% |
| `day_forecast_busted` | `lottery_yes` | 3977 | 49 | 0.02 | 1.2% | -40.3% |
| `day_forecast_capped` | `current_bracket_no` | 2597 | 49 | 0.30 | 28.4% | -6.1% |
| `day_forecast_capped` | `current_yes` | 2505 | 50 | 0.71 | 67.1% | -5.1% |
| `day_forecast_capped` | `d1_no` | 2418 | 50 | 0.80 | 77.2% | -3.4% |
| `day_forecast_capped` | `d2_no` | 1899 | 50 | 0.91 | 89.2% | -2.4% |
| `day_forecast_capped` | `lottery_yes` | 2679 | 48 | 0.02 | 0.8% | -46.3% |
| `day_marginal_runway` | `current_bracket_no` | 2165 | 49 | 0.45 | 44.6% | -1.2% |
| `day_marginal_runway` | `current_yes` | 2142 | 50 | 0.54 | 50.3% | -7.0% |
| `day_marginal_runway` | `d1_no` | 2055 | 50 | 0.77 | 76.6% | -1.0% |
| `day_marginal_runway` | `d2_no` | 1800 | 50 | 0.83 | 77.6% | -6.4% |
| `day_marginal_runway` | `lottery_yes` | 2233 | 50 | 0.02 | 1.7% | -14.7% |
| `day_open_runway` | `current_bracket_no` | 3205 | 49 | 0.63 | 60.7% | -3.8% |
| `day_open_runway` | `current_yes` | 3400 | 50 | 0.35 | 34.0% | -3.3% |
| `day_open_runway` | `d1_no` | 3688 | 50 | 0.83 | 81.5% | -2.1% |
| `day_open_runway` | `d2_no` | 3746 | 50 | 0.82 | 81.1% | -0.9% |
| `day_open_runway` | `lottery_yes` | 4524 | 50 | 0.01 | 1.0% | -33.3% |

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
