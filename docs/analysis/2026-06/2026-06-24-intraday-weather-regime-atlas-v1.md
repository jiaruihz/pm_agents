# Intraday Weather Regime Atlas v1

## 数据快照

- 数据源：`runtime/weather.db` + atlas feature-factory 分片；CLOB coverage gate=`True`。
- 生成时间 UTC：`2026-07-03T16:22:46+00:00`。
- feature rows：`137832`；state rows(city/date/hour)：`13575`；日期：`2026-05-19`..`2026-07-02`。
- 覆盖城市：`36`；小时：`[10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]`。
- fact 自检：`fact_signal_candidates` 44655 rows / max event_date `2026-07-05`；`settlement_outcomes` max target_date `2026-07-02`。

## 结论

第一版 atlas 已经把日内天气状态从单策略里抽出来：每个 city/date/hour 有 `day_regime`、`intraday_state`、`moisture_cloud_regime`、`running_max_state` 和 `composite_regime`。这些 label 是天气结构，不等于买/不买。

最重要的事实是：同一个 regime 对不同表达的含义相反。`day_open_runway` 描述的是物理上更容易继续打穿，不等于 YES 或 lottery 自动有正 EV；`day_forecast_capped` / `day_forecast_busted` 描述的是封顶结构，也不等于 NO 自动便宜。payoff 表只用于校准这些天气结构是否已被市场价格吸收，不是 live gate。

## Top Day Regimes

| day_regime | states | reheat_step | capped_day | avg_remaining | current_yes_roi | current_bracket_no_roi | d1_no_roi | lottery_yes_roi |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `day_open_runway` | 3636 | 74.8% | 22.4% | 3.21 | -4.9% | -2.7% | 0.6% | -22.7% |
| `day_forecast_busted` | 3299 | 12.4% | 81.9% | 0.16 | 0.7% | -26.0% | -0.6% | -42.1% |
| `day_forecast_capped` | 2827 | 25.2% | 72.2% | 0.38 | -0.6% | -15.7% | -2.3% | -53.8% |
| `day_marginal_runway` | 2221 | 50.9% | 47.0% | 1.03 | -10.1% | 2.2% | -5.9% | -38.0% |
| `day_space_unknown` | 1592 | 41.1% | 45.5% | 1.35 | -3.2% | -4.9% | -4.0% | -41.8% |

## Top Intraday States

| intraday_state | states | reheat_step | capped_day | avg_remaining | current_yes_roi | current_bracket_no_roi | d1_no_roi |
|---|---:|---:|---:|---:|---:|---:|---:|
| `active_warming` | 4788 | 78.5% | 23.2% | 2.78 | -8.4% | -2.9% | -1.2% |
| `mature_fade` | 3304 | 2.1% | 83.2% | 0.06 | 0.5% | -37.7% | 0.1% |
| `fresh_high` | 2976 | 42.9% | 54.3% | 0.90 | -2.6% | -10.4% | -4.3% |
| `pullback_uncertain` | 1414 | 5.7% | 89.1% | 0.10 | -0.4% | -25.2% | -0.0% |
| `false_fade_risk` | 673 | 55.3% | 41.9% | 1.81 | -4.1% | -6.9% | -4.8% |
| `reheating_after_dip` | 371 | 15.1% | 74.7% | 0.31 | -4.5% | 4.2% | -5.9% |
| `plateau_near_high` | 33 | 27.3% | 57.6% | 0.84 | 4.9% | -19.8% | 3.0% |
| `state_unknown` | 16 | 18.8% | 37.5% | 1.44 | -16.7% | 43.7% | 2.9% |

## Expression Matrix By Day Regime

| day_regime | expression | states | active_dates | avg_ask | hit_rate | roi |
|---|---|---:|---:|---:|---:|---:|
| `day_forecast_busted` | `current_bracket_no` | 3030 | 40 | 0.15 | 10.9% | -26.0% |
| `day_forecast_busted` | `current_yes` | 2692 | 39 | 0.86 | 86.3% | 0.7% |
| `day_forecast_busted` | `d1_no` | 2422 | 40 | 0.88 | 87.6% | -0.6% |
| `day_forecast_busted` | `d2_no` | 1694 | 40 | 0.96 | 96.2% | -0.0% |
| `day_forecast_busted` | `lottery_yes` | 2885 | 40 | 0.02 | 1.1% | -42.1% |
| `day_forecast_capped` | `current_bracket_no` | 2596 | 40 | 0.25 | 21.4% | -15.7% |
| `day_forecast_capped` | `current_yes` | 2460 | 40 | 0.75 | 74.8% | -0.6% |
| `day_forecast_capped` | `d1_no` | 2353 | 40 | 0.82 | 80.5% | -2.3% |
| `day_forecast_capped` | `d2_no` | 1861 | 40 | 0.93 | 91.9% | -1.1% |
| `day_forecast_capped` | `lottery_yes` | 2643 | 40 | 0.02 | 0.7% | -53.8% |
| `day_marginal_runway` | `current_bracket_no` | 1972 | 40 | 0.46 | 47.1% | 2.2% |
| `day_marginal_runway` | `current_yes` | 1948 | 40 | 0.53 | 47.9% | -10.1% |
| `day_marginal_runway` | `d1_no` | 1887 | 40 | 0.75 | 70.7% | -5.9% |
| `day_marginal_runway` | `d2_no` | 1671 | 40 | 0.83 | 77.4% | -6.7% |
| `day_marginal_runway` | `lottery_yes` | 2063 | 40 | 0.02 | 1.5% | -38.0% |
| `day_open_runway` | `current_bracket_no` | 2376 | 40 | 0.68 | 65.8% | -2.7% |
| `day_open_runway` | `current_yes` | 2573 | 40 | 0.30 | 28.9% | -4.9% |
| `day_open_runway` | `d1_no` | 2785 | 40 | 0.83 | 83.6% | 0.6% |
| `day_open_runway` | `d2_no` | 2851 | 40 | 0.81 | 78.9% | -2.3% |
| `day_open_runway` | `lottery_yes` | 3440 | 40 | 0.02 | 1.2% | -22.7% |
| `day_space_unknown` | `current_bracket_no` | 1097 | 8 | 0.36 | 34.1% | -4.9% |
| `day_space_unknown` | `current_yes` | 1071 | 8 | 0.65 | 62.9% | -3.2% |
| `day_space_unknown` | `d1_no` | 1042 | 8 | 0.84 | 80.6% | -4.0% |
| `day_space_unknown` | `d2_no` | 846 | 8 | 0.88 | 86.3% | -1.9% |
| `day_space_unknown` | `lottery_yes` | 1202 | 8 | 0.01 | 0.4% | -41.8% |

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
