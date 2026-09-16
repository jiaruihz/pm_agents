# mid_price_core_v1 forecast timing degradation lineage

## 数据快照

- 数据源：`/home/rui/projects/pm_agent/runtime/weather.db`；realized PnL 只读 `fact_trades.pnl_usd_at_fill`。
- Raw snapshot lineage：`/home/rui/projects/pm_agent/runtime/weather_edge_v1/market_data/paper_snapshots`，只用于解释 forecast / side / market 后续变化。
- DB mtime UTC：`2026-06-07T16:25:31.071431+00:00`；`MAX(fact_built_at_utc)`：`2026-06-07T16:24:35.186637+00:00`。
- 本次复核：2026-06-08 北京时间已执行 `scripts/ops/sync_weather_remote.sh`，随后执行 `scripts/weather_dashboard/run_stack.sh` 完成 DB rebuild、fact_trades、fact_signal_candidates 和 CLOB coverage gate；最后 API 启动阶段因端口占用报 `Errno 98`，不影响本离线报告取数。
- CLOB fill coverage gate：`gate_pass=True`，`missing_order_rows=0`，`over_order_keys=0`，`db_fill_cost_minus_fact_cost=0.0`。
- 目标样本：`mid_price_core_v1_25_75` / `strategy_id=live_weather_edge_v1_4ef9b3ec3e2e` / `trade_class=live_real` / `settlement_status=settled`。

### 强制 5 行 SQL 自检

trade_class 分布：
| trade_class | rows |
| --- | --- |
| live_real | 1319 |
| live_simulated | 1093 |
| paper | 2285 |
| snapshot_replay | 636 |

settlement_status 分布：
| settlement_status | rows |
| --- | --- |
| [NULL] | 300 |
| settled | 5033 |

fact_signal_candidates 覆盖：
| rows | eligible | paper_ordered | live_filled |
| --- | --- | --- | --- |
| 23485 | 7641 | 2835 | 512 |

CLOB orders/fills：
| status | orders | with_fill |
| --- | --- | --- |
| error | 151 | 0 |
| submitted | 1546 | 1319 |

目标策略样本：
| strategy_id | strategy_name | trade_class | fills | min_target_date | max_target_date |
| --- | --- | --- | --- | --- | --- |
| live_weather_edge_v1_4ef9b3ec3e2e | t1_trading_mid_price_core_v1_notional_$5.0_shares_10.0_entry_0.25-0.75 | live_real | 871 | 2026-05-16 | 2026-06-08 |
| live_weather_edge_v1_4ef9b3ec3e2e | t1_trading_mid_price_core_v1_notional_$5.0_shares_10.0_entry_0.25-0.75 | live_simulated | 608 | 2026-05-16 | 2026-06-08 |

## 目标指标与分母

`timing_independent_effect` = 在 `live_real + settled + mid_price_core_v1 + entry_price_window=0.25-0.75` 的真实成交样本中，固定 `city + side + model_version + raw_edge_bin` 后，比较 `T-22-24`、`T-26-28`、`>T-28` 的 realized PnL/ROI，并用 raw snapshot 序列解释后续 forecast jump、side flip、market implied move。

城市日 aggregation 使用 `(target_date, city, hours_bin)`，避免一个 city-day 多个 fill 把同一日重复放大。policy simulation 只看 6 月后样本，`delta` 是“无替代成交”口径，即过滤掉的 fill 不被替换。

## 结论

- **timing 是独立解释变量的证据中等偏强，但严格 matched 仍受样本限制**：6 月后 `T-22-24` 为 +62.43 / ROI 65.2%，`>T-28` 为 -91.52 / ROI -15.6%。严格固定 `city+side+model_version+raw_edge_bin` 时，`T-22-24` 与 `>T-28` 没有重叠 cell（matched cells=0），所以不能把它说成严格 city-level 因果识别。降级到 `side+model_version+raw_edge_bin` 后，`T-22-24` 为 +39.89，`>T-28` 为 -17.97。
- **显著性口径**：city-day bootstrap 的 `T-22-24 ROI - >T-28 ROI` 均值 78.6%，5/50/95 分位为 16.1%/79.4%/137.0%，正差概率 97.9%；样本支持方向，但还不是可当成单因子定律的强统计显著。
- **`>T-28` 的亏损不能简单归因成 BuenosAires 或 2026-06-01 单点事故**：leave-one-city/date 后保留组合仍为负。但“不是 city/model/side mix 偶然造成”只能给中等置信度，因为最严格 matched 没有足够重叠。机制上，亏损 fill 同时暴露在 forecast jump、side flip 和 market adverse move 中，不能只归因为 forecast stale。
- **`T-26-28` 是坏窗口但样本偏小**：6 月后 35 fills，PnL -29.59 / ROI -40.7%。LOO 显示对单城市/日期敏感，建议先 shadow/drop 观察，不建议仅凭当前样本永久禁用。
- **只改 timing 的 6 月后改善上限**：`drop_>T-28` 的无替代成交改善约 +91.52，`only_T-22-26` 改善约 +146.49；`high_raw_edge_>0.25_plus_drop_>T-28` 样本更少，适合作 shadow 组合门，不适合直接替代 baseline。

## 1. 6 月后 timing 总览

| period | hours_bin | fills | city_days | cost_usd | pnl_usd | roi | win_rate | avg_hours_to_settle | avg_raw_edge | lineage_found_rate | avg_entry_run_age | avg_first_to_entry_hours | avg_max_abs_forecast_jump_f | forecast_jump_ge_1f_rate | side_flip_rate | avg_worst_side_market_delta |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | <T-22 | 22 | 7 | +56.92 | -25.38 | -44.6% | 31.8% | 20.663 | 0.195 | 100.0% | 7.136 | 11.435 | 2.855 | 100.0% | 72.7% | -0.261 |
| post_2026_06_01 | T-22-24 | 43 | 10 | +95.75 | +62.43 | 65.2% | 67.4% | 22.671 | 0.238 | 100.0% | 8.012 | 15.917 | 2.956 | 88.4% | 90.7% | -0.219 |
| post_2026_06_01 | T-24-26 | 30 | 12 | +68.36 | -7.48 | -10.9% | 40.0% | 25.216 | 0.215 | 100.0% | 5.967 | 18.713 | 2.503 | 93.3% | 80.0% | -0.295 |
| post_2026_06_01 | T-26-28 | 35 | 13 | +72.79 | -29.59 | -40.7% | 25.7% | 27.484 | 0.152 | 100.0% | 8.500 | 12.458 | 1.520 | 74.3% | 51.4% | -0.288 |
| post_2026_06_01 | >T-28 | 214 | 81 | +587.86 | -91.52 | -15.6% | 45.8% | 33.656 | 0.195 | 100.0% | 9.084 | 11.672 | 1.879 | 67.3% | 36.9% | -0.320 |

## 2. matched slice

### 2.1 严格 matched: city + side + model_version + raw_edge_bin

- matched cells：`0`；matched fills：`0`。

_No rows._

严格口径无重叠时，不把它当作 city-level 因果识别证据。

### 2.2 降级 matched: city + side + model_version

- matched cells：`0`；matched fills：`0`。

_No rows._

最差 matched cells：
_No rows._

### 2.3 降级 matched: side + model_version + raw_edge_bin

- matched cells：`7`；matched fills：`140`。

| hours_bin | fills | city_days | cost_usd | pnl_usd | roi | win_rate | avg_raw_edge | avg_entry_run_age | avg_max_abs_forecast_jump_f | side_flip_rate | avg_worst_side_market_delta |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T-22-24 | 32 | 10 | +81.27 | +39.89 | 49.1% | 65.6% | 0.201 | 8.188 | 2.378 | 87.5% | -0.227 |
| >T-28 | 108 | 48 | +305.68 | -17.97 | -5.9% | 49.1% | 0.154 | 7.569 | 1.873 | 52.8% | -0.292 |

## 3. city-day aggregation 与 bootstrap

| hours_bin | fills | city_days | cost_usd | pnl_usd | roi | win_rate |
| --- | --- | --- | --- | --- | --- | --- |
| <T-22 | 22 | 7 | +56.92 | -25.38 | -44.6% | 14.3% |
| T-22-24 | 43 | 10 | +95.75 | +62.43 | 65.2% | 60.0% |
| T-24-26 | 30 | 12 | +68.36 | -7.48 | -10.9% | 33.3% |
| T-26-28 | 35 | 13 | +72.79 | -29.59 | -40.7% | 46.2% |
| >T-28 | 214 | 81 | +587.86 | -91.52 | -15.6% | 42.0% |

Bootstrap `T-22-24 ROI - >T-28 ROI`：
| a_bin | b_bin | a_city_days | b_city_days | delta_roi_mean | delta_roi_p05 | delta_roi_p50 | delta_roi_p95 | prob_delta_positive |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T-22-24 | >T-28 | 10 | 81 | 0.786 | 0.161 | 0.794 | 1.370 | 97.9% |

## 4. >T-28 亏损机制拆分

| mechanism_flag | loss_fills | gross_loss | share_of_post_gross_loss | avg_entry_run_age | avg_jump_f | side_flip_rate | avg_worst_market_delta |
| --- | --- | --- | --- | --- | --- | --- | --- |
| entry_run_age>=6h | 72 | +167.33 | 58.5% | 11.174 | 1.626 | 23.6% | -0.490 |
| forecast_jump>=1F | 71 | +177.21 | 61.9% | 9.183 | 2.242 | 46.5% | -0.507 |
| side_flip_after_entry | 39 | +102.31 | 35.8% | 7.231 | 2.246 | 100.0% | -0.413 |
| market_side_adverse>=10c | 115 | +281.06 | 98.3% | 8.787 | 1.656 | 33.9% | -0.481 |
| model_side_prob_adverse>=15pp | 34 | +97.88 | 34.2% | 8.618 | 2.503 | 73.5% | -0.452 |

读法：这些 flag 会重叠，share 不是互斥归因。`market_side_adverse>=10c` 表示入场后同一 side 的 implied price 曾经下移至少 10c；`forecast_jump>=1F` 表示同 market/bracket 后续 forecast max 相对入场变化至少 1°F。

## 4.5 Forecast run age / local time

按 `period + model_version + hours_bin`：

| period | model_version | hours_bin | fills | cost_usd | pnl_usd | roi | avg_entry_run_age | run_age_ge_5h_rate | run_age_ge_6h_rate | avg_entry_local_hour | avg_max_abs_forecast_jump_f | side_flip_rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pre_2026_06_01 | gfs | <T-22 | 18 | +44.50 | +29.06 | 65.3% | 7.389 | 94.4% | 83.3% | 21.851 | 1.589 | 44.4% |
| post_2026_06_01 | gfs | <T-22 | 22 | +56.92 | -25.38 | -44.6% | 7.136 | 68.2% | 68.2% | 21.109 | 2.855 | 72.7% |
| post_2026_06_01 | ecmwf | T-22-24 | 5 | +14.99 | -6.80 | -45.4% | 13.500 | 100.0% | 100.0% | 22.515 | 0.780 | 40.0% |
| pre_2026_06_01 | ecmwf | T-22-24 | 21 | +54.19 | +4.80 | 8.9% | 11.643 | 100.0% | 100.0% | 22.610 | 1.138 | 52.4% |
| pre_2026_06_01 | gfs | T-22-24 | 36 | +94.51 | +5.97 | 6.3% | 7.194 | 100.0% | 91.7% | 19.543 | 2.356 | 86.1% |
| post_2026_06_01 | gfs | T-22-24 | 38 | +80.76 | +69.23 | 85.7% | 7.289 | 100.0% | 100.0% | 18.646 | 3.242 | 97.4% |
| post_2026_06_01 | ecmwf | T-24-26 | 6 | +7.49 | +18.66 | 249.0% | 10.333 | 100.0% | 100.0% | 23.359 | 1.033 | 33.3% |
| post_2026_06_01 | gfs | T-24-26 | 24 | +60.87 | -26.14 | -42.9% | 4.875 | 75.0% | 0.0% | 17.890 | 2.871 | 91.7% |
| pre_2026_06_01 | ecmwf | T-24-26 | 51 | +170.14 | +39.19 | 23.0% | 8.755 | 100.0% | 96.1% | 20.223 | 1.504 | 35.3% |
| pre_2026_06_01 | gfs | T-24-26 | 127 | +362.45 | +48.22 | 13.3% | 6.169 | 75.6% | 55.9% | 19.822 | 2.394 | 55.1% |
| pre_2026_06_01 | ecmwf | T-26-28 | 6 | +20.25 | +9.76 | 48.2% | 8.167 | 100.0% | 100.0% | 20.190 | 1.700 | 83.3% |
| post_2026_06_01 | ecmwf | T-26-28 | 35 | +72.79 | -29.59 | -40.7% | 8.500 | 100.0% | 100.0% | 21.173 | 1.520 | 51.4% |
| pre_2026_06_01 | gfs | >T-28 | 53 | +146.29 | -22.99 | -15.7% | 6.698 | 79.2% | 58.5% | 18.449 | 1.587 | 24.5% |
| post_2026_06_01 | gfs | >T-28 | 54 | +165.85 | +6.27 | 3.8% | 7.213 | 77.8% | 64.8% | 18.598 | 2.308 | 46.3% |
| pre_2026_06_01 | ecmwf | >T-28 | 118 | +318.95 | -14.96 | -4.7% | 10.068 | 81.4% | 66.1% | 17.810 | 1.581 | 44.1% |
| post_2026_06_01 | ecmwf | >T-28 | 160 | +422.00 | -97.79 | -23.2% | 9.715 | 84.4% | 65.6% | 18.295 | 1.738 | 33.8% |

6 月后 `>T-28` 按 city + model：

| city | model_version | fills | cost_usd | pnl_usd | roi | avg_entry_run_age | run_age_ge_5h_rate | run_age_ge_6h_rate | avg_entry_local_hour | avg_max_abs_forecast_jump_f |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Amsterdam | ecmwf | 16 | +29.92 | -21.30 | -71.2% | 4.719 | 43.8% | 0.0% | 17.733 | 1.087 |
| Munich | ecmwf | 14 | +34.70 | -18.67 | -53.8% | 6.464 | 64.3% | 28.6% | 17.767 | 2.471 |
| Jeddah | ecmwf | 17 | +39.61 | -17.80 | -44.9% | 14.147 | 94.1% | 94.1% | 17.874 | 1.406 |
| Karachi | ecmwf | 19 | +49.72 | -17.61 | -35.4% | 12.289 | 100.0% | 89.5% | 18.568 | 1.216 |
| Istanbul | ecmwf | 11 | +19.66 | -12.42 | -63.2% | 10.682 | 81.8% | 81.8% | 19.151 | 1.609 |
| Ankara | ecmwf | 18 | +49.32 | -10.37 | -21.0% | 13.028 | 100.0% | 100.0% | 18.713 | 1.450 |
| Moscow | ecmwf | 15 | +55.09 | -9.46 | -17.2% | 12.267 | 93.3% | 86.7% | 18.487 | 1.620 |
| Lucknow | ecmwf | 6 | +14.69 | -7.69 | -52.3% | 10.333 | 100.0% | 100.0% | 19.348 | 3.000 |
| London | ecmwf | 16 | +43.37 | -5.54 | -12.8% | 5.744 | 93.8% | 43.8% | 17.765 | 1.637 |
| Chengdu | gfs | 1 | +5.00 | -5.00 | -100.0% | 5.000 | 100.0% | 0.0% | 19.015 |  |
| Guangzhou | gfs | 8 | +33.48 | -3.35 | -10.0% | 6.250 | 62.5% | 50.0% | 18.761 | 2.275 |
| Manila | gfs | 15 | +48.90 | -2.97 | -6.1% | 5.667 | 60.0% | 33.3% | 18.885 | 2.467 |
| Singapore | gfs | 9 | +14.98 | -0.71 | -4.7% | 8.611 | 100.0% | 100.0% | 19.963 | 0.400 |
| Madrid | ecmwf | 8 | +29.28 | +1.54 | 5.3% | 5.938 | 75.0% | 50.0% | 18.952 | 0.713 |
| Chengdu | ecmwf | 8 | +23.30 | +2.63 | 11.3% | 11.125 | 100.0% | 100.0% | 19.139 | 3.812 |
| Tokyo | gfs | 8 | +19.50 | +8.70 | 44.6% | 8.875 | 100.0% | 100.0% | 17.888 | 1.600 |
| Shanghai | gfs | 13 | +44.00 | +9.60 | 21.8% | 7.769 | 76.9% | 69.2% | 17.628 | 3.900 |
| Warsaw | ecmwf | 12 | +33.35 | +18.90 | 56.7% | 7.333 | 66.7% | 25.0% | 17.351 | 2.542 |

## 5. leave-one-out 稳定性

### >T-28 leave-one-city-out

| excluded_city | kept_fills | kept_pnl | kept_roi | removed_pnl | removed_fills |
| --- | --- | --- | --- | --- | --- |
| Warsaw | 202 | -110.42 | -19.9% | +18.90 | 12 |
| Shanghai | 201 | -101.12 | -18.6% | +9.60 | 13 |
| Tokyo | 206 | -100.22 | -17.6% | +8.70 | 8 |
| Madrid | 206 | -93.06 | -16.7% | +1.54 | 8 |
| Singapore | 205 | -90.81 | -15.9% | -0.71 | 9 |
| Chengdu | 205 | -89.16 | -15.9% | -2.36 | 9 |
| Manila | 199 | -88.55 | -16.4% | -2.97 | 15 |
| Guangzhou | 206 | -88.17 | -15.9% | -3.35 | 8 |
| London | 198 | -85.98 | -15.8% | -5.54 | 16 |
| Lucknow | 208 | -83.83 | -14.6% | -7.69 | 6 |
| Moscow | 199 | -82.06 | -15.4% | -9.46 | 15 |
| Ankara | 196 | -81.15 | -15.1% | -10.37 | 18 |

### >T-28 leave-one-date-out

| excluded_target_date | kept_fills | kept_pnl | kept_roi | removed_pnl | removed_fills |
| --- | --- | --- | --- | --- | --- |
| 2026-06-03 | 187 | -104.95 | -20.9% | +13.43 | 27 |
| 2026-06-06 | 166 | -92.57 | -19.1% | +1.05 | 48 |
| 2026-06-01 | 183 | -78.07 | -15.8% | -13.45 | 31 |
| 2026-06-02 | 178 | -73.35 | -15.3% | -18.17 | 36 |
| 2026-06-04 | 182 | -56.78 | -11.3% | -34.74 | 32 |
| 2026-06-05 | 174 | -51.90 | -10.8% | -39.62 | 40 |

### T-26-28 leave-one-city-out

| excluded_city | kept_fills | kept_pnl | kept_roi | removed_pnl | removed_fills |
| --- | --- | --- | --- | --- | --- |
| London | 33 | -34.25 | -53.2% | +4.66 | 2 |
| Munich | 32 | -32.16 | -47.4% | +2.57 | 3 |
| Istanbul | 34 | -32.13 | -47.3% | +2.54 | 1 |
| Madrid | 33 | -31.53 | -46.5% | +1.94 | 2 |
| Moscow | 29 | -24.59 | -36.3% | -5.00 | 6 |
| Jeddah | 29 | -24.59 | -36.3% | -5.00 | 6 |
| Warsaw | 29 | -20.07 | -31.7% | -9.52 | 6 |
| BuenosAires | 26 | -7.80 | -18.2% | -21.79 | 9 |

### T-26-28 leave-one-date-out

| excluded_target_date | kept_fills | kept_pnl | kept_roi | removed_pnl | removed_fills |
| --- | --- | --- | --- | --- | --- |
| 2026-06-02 | 28 | -28.76 | -48.4% | -0.83 | 7 |
| 2026-06-04 | 31 | -28.27 | -44.7% | -1.32 | 4 |
| 2026-06-05 | 30 | -22.13 | -38.2% | -7.46 | 5 |
| 2026-06-01 | 31 | -21.54 | -37.3% | -8.05 | 4 |
| 2026-06-03 | 20 | -17.66 | -33.5% | -11.93 | 15 |

## 6. timing policy simulation（6 月后，无替代成交）

| policy | kept_fills | kept_city_days | kept_cost | kept_pnl | kept_roi | dropped_fills | dropped_pnl | delta_pnl_vs_baseline | saved_loss_if_no_replacement |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_v1 | 344 | 106 | +881.67 | -91.54 | -10.4% | 0 | +0.00 | +0.00 | -0.00 |
| only_T-22-26 | 73 | 19 | +164.11 | +54.95 | 33.5% | 271 | -146.49 | +146.49 | +146.49 |
| drop_>T-28 | 130 | 35 | +293.82 | -0.02 | -0.0% | 214 | -91.52 | +91.52 | +91.52 |
| drop_T-26-28 | 309 | 103 | +808.89 | -61.95 | -7.7% | 35 | -29.59 | +29.59 | +29.59 |
| high_raw_edge_>0.25_plus_drop_>T-28 | 33 | 11 | +77.22 | +17.25 | 22.3% | 311 | -108.79 | +108.79 | +108.79 |

## 7. 交易动作建议与执行状态

1. **`>T-28` 不再作为 v1 live 入场来源**：6 月后 `>T-28` 的 settled overlay 为 -91.52，且在 leave-one-city/date 后仍为负。上一轮已经通过 `codex/stop-stale-weather-timing` 的 `ac99018244db745c851e79527b5b9d0e266f4108` 给 source snapshot `hours_to_settle > 28` 加硬过滤；本报告支持保留该止损。
2. **`T-26-28` 先 shadow/drop，不作为永久禁用定律**：当前 6 月后为 35 fills / -29.59 / ROI -40.7%，方向很差，但 LOO 对 BuenosAires、2026-06-03 等单点敏感，样本还不足以单独定义长期规则。
3. **不要把 run age 写成单变量硬黑名单**：6 月后 `>T-28 + ECMWF` 是主要亏损组合，GFS `>T-28` 仍小正；GFS 第 5-6 小时“等下一轮 forecast”应先做 shadow rule，与 city/model/timing 组合一起评估。
4. **下一步工程化字段**：把 `entry_model_run_age_hours`、`forecast_jump`、`side_flip_after_entry`、`worst_side_market_delta` 物化进 `fact_signal_candidates` 或对应 lineage fact，避免每次扫 raw snapshots 才能复现 timing 归因。

## 8. 限制与下一步

- 本报告用 raw snapshot 序列补 lineage，但 DB 里仍未物化 `forecast_jump` / `side_flip` 字段；后续应把这些字段写进 `fact_signal_candidates`，避免每次扫 1.9G snapshots。
- policy simulation 是历史 fill overlay，不包含错过成交后的资金再部署，也不代表订单簿容量变化。
- `T-26-28` 当前样本小，建议先 shadow/drop 观察；`>T-28` 可以作为更明确的 v1 timing filter 候选。
