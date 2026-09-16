# v1 raw regime filter walk-forward 控制变量研究

## 数据快照

- 数据源：`/home/rui/projects/pm_agent/runtime/weather.db`，只读 `fact_trades` / `fact_signal_candidates`；本报告不使用 legacy DB。
- 生成时间 UTC：`2026-06-07T14:22:10.366819+00:00`。
- DB mtime UTC：`2026-06-07T05:49:19.463464+00:00`。
- `MAX(fact_built_at_utc)`：`2026-06-07T05:48:57.787507+00:00`。
- CLOB coverage gate：`gate_pass=True`；`missing_order_rows=0`；`over_order_keys=0`；`db_fill_cost_minus_fact_cost=0.0`。
- 目标样本：`mid_price_core_v1_25_75` / `strategy_id=live_weather_edge_v1_4ef9b3ec3e2e` / `trade_class=live_real` / `settlement_status=settled`，共 `707` fills，`2026-05-16 -> 2026-06-05`。

### 5 行 SQL 自检

trade_class 分布：
| trade_class | rows |
| --- | --- |
| live_real | 1302 |
| live_simulated | 1077 |
| paper | 2285 |
| snapshot_replay | 636 |

settlement_status 分布：
| settlement_status | rows |
| --- | --- |
| [NULL] | 388 |
| settled | 4912 |

fact_signal_candidates 覆盖：
| rows | eligible | paper_ordered | live_filled |
| --- | --- | --- | --- |
| 23299 | 7567 | 2795 | 503 |

orders/fills by venue/status：
| venue | status | orders | with_fill |
| --- | --- | --- | --- |
| polymarket_clob | error | 151 | 0 |
| polymarket_clob | submitted | 1524 | 1302 |

目标策略样本：
| strategy_id | strategy_name | trade_class | fills | min_target_date | max_target_date |
| --- | --- | --- | --- | --- | --- |
| live_weather_edge_v1_4ef9b3ec3e2e | t1_trading_mid_price_core_v1_notional_$5.0_shares_10.0_entry_0.25-0.75 | live_real | 859 | 2026-05-16 | 2026-06-07 |
| live_weather_edge_v1_4ef9b3ec3e2e | t1_trading_mid_price_core_v1_notional_$5.0_shares_10.0_entry_0.25-0.75 | live_simulated | 593 | 2026-05-16 | 2026-06-07 |

## 目标指标与方法

`regime_filter_walkforward` = 对原始 `mid_price_core_v1_25_75` 真实已成交已结算 fill 做控制变量 overlay：成交价、size、结算结果固定，只改变 gate 是否会保留这笔 fill。

- `pre` = `target_date < 2026-06-01`，视作训练/历史稳定期。
- `post` = `target_date >= 2026-06-01`，视作退化观察期。
- `delta_pnl_if_filter = - filtered_pnl`；为正表示 gate 过滤掉的是净亏损。
- `avoided_loss` 是被过滤 loser 的亏损绝对值；`missed_profit` 是被过滤 winner 的盈利。
- 后验 gate 标为 `hindsight=yes`，只能做诊断，不能作为上线依据。

## 结论

- **原始 v1_25_75 明确出现 regime shift**：pre PnL `$+99.06`，post PnL `$-89.06`。
- **最强的非后验 walk-forward 线索是城市层风控**：`pre_profitable_cities_only` 的 pre delta `$+104.56`、post delta `$+117.08`；`exclude_pre_weak_cities` 的 pre delta `$+84.25`、post delta `$+106.17`。这说明 v1 raw 的失效更像城市/数据源 regime 分化，而不是全局 raw edge 失效。
- **单纯 blended gate 是有效止血，但不是稳定增强**：pre delta `$-67.63`，post delta `$+86.56`。它 6 月后避开亏损，但 6 月前会过滤掉净盈利。
- **如果要用 blender，更合理的候选不是纯 blended gate，而是 `market_confirm_or_raw_edge_gt_0.25`**：允许高 raw edge 例外后，pre delta `$-39.44`，post delta `$+96.08`；它比纯 blended 更少伤害训练期，但日期级 median delta 仍接近 0，不足以单独上线。
- **raw_edge>0.25 本身仍有信号，但样本少**：post kept PnL `$+7.02`，post kept fills `70`；这支持“不要简单黑掉高分歧/高 raw edge”。
- **timing 过滤有交易含义，但不能单独上线**：组合 gate `combo_blended_or_highraw_T22_28` 的 post delta `$+125.35`，但 pre delta `$-43.21`，说明 timing 可作为 size/risk 条件，不足以单独证明 alpha。
- **下一步不是直接 live 替换**：先 paper/shadow `blended_filter_25_75_v0`，同时物化 `forecast_jump` / `side_flip` / snapshot transition 字段，才能解释 raw 为什么在某些窗口失效。

## Gate 总表

| gate | family | pre_delta | post_delta | pre_kept_pnl | post_kept_pnl | post_kept_fills | post_filtered_fills | post_avoided_loss | post_missed_profit | full_delta | hindsight |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| no_gate | baseline | $-0.00 | $-0.00 | $+99.06 | $-89.06 | 277 | 0 | $0.00 | $0.00 | $-0.00 |  |
| blended_edge_ge_0.10 | market_confirmation | $-67.63 | $+86.56 | $+31.42 | $-2.50 | 43 | 234 | $326.28 | $239.72 | $+18.93 |  |
| market_confirm_or_raw_edge_gt_0.25 | market_confirmation | $-39.44 | $+96.08 | $+59.61 | $+7.02 | 70 | 207 | $298.70 | $202.62 | $+56.64 |  |
| raw_edge_gt_0.25 | raw_edge | $-68.03 | $+96.08 | $+31.03 | $+7.02 | 70 | 207 | $298.70 | $202.62 | $+28.06 |  |
| raw_edge_ge_0.15 | raw_edge | $-49.65 | $+53.95 | $+49.41 | $-35.11 | 156 | 121 | $162.01 | $108.05 | $+4.30 |  |
| timing_T22_26 | timing | $-98.31 | $+144.06 | $+0.74 | $+55.00 | 62 | 215 | $317.83 | $173.77 | $+45.75 |  |
| timing_T22_28 | timing | $+8.89 | $+114.47 | $+107.94 | $+25.41 | 97 | 180 | $273.34 | $158.86 | $+123.36 |  |
| exclude_gt_T28 | timing | $+37.95 | $+92.57 | $+137.00 | $+3.51 | 111 | 166 | $246.39 | $153.82 | $+130.52 |  |
| model_gfs_only | model_version | $-38.80 | $+119.33 | $+60.26 | $+30.27 | 112 | 165 | $250.75 | $131.42 | $+80.53 |  |
| model_ecmwf_only | model_version | $-60.26 | $-30.27 | $+38.80 | $-119.33 | 165 | 112 | $127.64 | $157.91 | $-90.53 |  |
| pre_profitable_cities_only | city | $+104.56 | $+117.08 | $+203.62 | $+28.02 | 118 | 159 | $235.65 | $118.57 | $+221.64 |  |
| exclude_pre_weak_cities | city | $+84.25 | $+106.17 | $+183.31 | $+17.11 | 135 | 142 | $211.12 | $104.95 | $+190.42 |  |
| buy_no_only | side | $-60.73 | $+50.08 | $+38.33 | $-38.98 | 172 | 105 | $158.89 | $108.81 | $-10.65 |  |
| buy_yes_only | side | $-38.33 | $+38.98 | $+60.73 | $-50.08 | 105 | 172 | $219.50 | $180.52 | $+0.65 |  |
| combo_blended_or_highraw_T22_28 | combo | $-43.21 | $+125.35 | $+55.85 | $+36.29 | 25 | 252 | $367.81 | $242.46 | $+82.14 |  |
| post_weak_city_blacklist_DIAGNOSTIC | hindsight | $+60.84 | $+112.41 | $+159.89 | $+23.35 | 160 | 117 | $183.41 | $71.00 | $+173.25 | yes |

## Pre 训练期明细

| gate | fills | pnl | kept_fills | kept_pnl | kept_roi | filtered_pnl | delta | filtered_winners | filtered_losers | ex_top5_kept |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| no_gate | 430 | $+99.06 | 430 | $+99.06 | +8.2% | $+0.00 | $-0.00 | 0 | 0 | $+37.67 |
| blended_edge_ge_0.10 | 430 | $+99.06 | 64 | $+31.42 | +18.3% | $+67.63 | $-67.63 | 214 | 152 | $-10.11 |
| market_confirm_or_raw_edge_gt_0.25 | 430 | $+99.06 | 126 | $+59.61 | +15.6% | $+39.44 | $-39.44 | 177 | 127 | $+6.32 |
| raw_edge_gt_0.25 | 430 | $+99.06 | 112 | $+31.03 | +8.8% | $+68.03 | $-68.03 | 188 | 130 | $-21.08 |
| raw_edge_ge_0.15 | 430 | $+99.06 | 321 | $+49.41 | +5.3% | $+49.65 | $-49.65 | 66 | 43 | $-11.46 |
| timing_T22_26 | 430 | $+99.06 | 116 | $+0.74 | +0.2% | $+98.31 | $-98.31 | 193 | 121 | $-42.04 |
| timing_T22_28 | 430 | $+99.06 | 241 | $+107.94 | +15.4% | $-8.89 | $+8.89 | 111 | 78 | $+47.07 |
| exclude_gt_T28 | 430 | $+99.06 | 259 | $+137.00 | +18.4% | $-37.95 | $+37.95 | 94 | 77 | $+75.62 |
| model_gfs_only | 430 | $+99.06 | 234 | $+60.26 | +9.3% | $+38.80 | $-38.80 | 115 | 81 | $+5.45 |
| model_ecmwf_only | 430 | $+99.06 | 196 | $+38.80 | +6.9% | $+60.26 | $-60.26 | 138 | 96 | $-12.60 |
| pre_profitable_cities_only | 430 | $+99.06 | 269 | $+203.62 | +27.3% | $-104.56 | $+104.56 | 68 | 93 | $+142.23 |
| exclude_pre_weak_cities | 430 | $+99.06 | 286 | $+183.31 | +23.0% | $-84.25 | $+84.25 | 63 | 81 | $+121.92 |
| buy_no_only | 430 | $+99.06 | 306 | $+38.33 | +4.1% | $+60.73 | $-60.73 | 51 | 73 | $+4.89 |
| buy_yes_only | 430 | $+99.06 | 124 | $+60.73 | +22.0% | $+38.33 | $-38.33 | 202 | 104 | $-0.66 |
| combo_blended_or_highraw_T22_28 | 430 | $+99.06 | 85 | $+55.85 | +21.7% | $+43.21 | $-43.21 | 203 | 142 | $+4.43 |
| post_weak_city_blacklist_DIAGNOSTIC | 430 | $+99.06 | 320 | $+159.89 | +17.6% | $-60.84 | $+60.84 | 50 | 60 | $+98.51 |

## Post 退化期明细

| gate | fills | pnl | kept_fills | kept_pnl | kept_roi | filtered_pnl | delta | filtered_winners | filtered_losers | ex_top5_kept |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| no_gate | 277 | $-89.06 | 277 | $-89.06 | -12.1% | $+0.00 | $-0.00 | 0 | 0 | $-137.49 |
| blended_edge_ge_0.10 | 277 | $-89.06 | 43 | $-2.50 | -2.5% | $-86.56 | $+86.56 | 101 | 133 | $-26.79 |
| market_confirm_or_raw_edge_gt_0.25 | 277 | $-89.06 | 70 | $+7.02 | +3.7% | $-96.08 | $+96.08 | 82 | 125 | $-18.03 |
| raw_edge_gt_0.25 | 277 | $-89.06 | 70 | $+7.02 | +3.7% | $-96.08 | $+96.08 | 82 | 125 | $-18.03 |
| raw_edge_ge_0.15 | 277 | $-89.06 | 156 | $-35.11 | -8.0% | $-53.95 | $+53.95 | 41 | 80 | $-73.13 |
| timing_T22_26 | 277 | $-89.06 | 62 | $+55.00 | +38.0% | $-144.06 | $+144.06 | 85 | 130 | $+15.73 |
| timing_T22_28 | 277 | $-89.06 | 97 | $+25.41 | +11.7% | $-114.47 | $+114.47 | 76 | 104 | $-13.86 |
| exclude_gt_T28 | 277 | $-89.06 | 111 | $+3.51 | +1.4% | $-92.57 | $+92.57 | 73 | 93 | $-35.77 |
| model_gfs_only | 277 | $-89.06 | 112 | $+30.27 | +10.1% | $-119.33 | $+119.33 | 60 | 105 | $-16.68 |
| model_ecmwf_only | 277 | $-89.06 | 165 | $-119.33 | -27.2% | $+30.27 | $-30.27 | 61 | 51 | $-147.18 |
| pre_profitable_cities_only | 277 | $-89.06 | 118 | $+28.02 | +9.0% | $-117.08 | $+117.08 | 60 | 99 | $-13.43 |
| exclude_pre_weak_cities | 277 | $-89.06 | 135 | $+17.11 | +4.8% | $-106.17 | $+106.17 | 52 | 90 | $-24.33 |
| buy_no_only | 277 | $-89.06 | 172 | $-38.98 | -7.4% | $-50.08 | $+50.08 | 26 | 79 | $-63.36 |
| buy_yes_only | 277 | $-89.06 | 105 | $-50.08 | -23.5% | $-38.98 | $+38.98 | 95 | 77 | $-98.51 |
| combo_blended_or_highraw_T22_28 | 277 | $-89.06 | 25 | $+36.29 | +72.6% | $-125.35 | $+125.35 | 103 | 149 | $+11.99 |
| post_weak_city_blacklist_DIAGNOSTIC | 277 | $-89.06 | 160 | $+23.35 | +5.3% | $-112.41 | $+112.41 | 37 | 80 | $-25.08 |

## 日期鲁棒性

重点看非后验 gate。`positive_delta_dates` 表示某天过滤有帮助，`negative_delta_dates` 表示某天过滤伤害收益。

| gate | dates | positive_delta_dates | negative_delta_dates | median_delta |
| --- | --- | --- | --- | --- |
| no_gate | 19 | 0 | 0 | $+0.00 |
| blended_edge_ge_0.10 | 19 | 9 | 10 | $-1.79 |
| market_confirm_or_raw_edge_gt_0.25 | 19 | 9 | 10 | $-0.26 |
| raw_edge_gt_0.25 | 19 | 8 | 11 | $-4.03 |
| raw_edge_ge_0.15 | 19 | 7 | 12 | $-5.20 |
| timing_T22_26 | 19 | 8 | 11 | $-6.51 |
| timing_T22_28 | 19 | 8 | 6 | $+0.00 |
| exclude_gt_T28 | 19 | 8 | 4 | $+0.00 |
| model_gfs_only | 19 | 10 | 8 | $+2.41 |
| model_ecmwf_only | 19 | 5 | 14 | $-4.39 |
| pre_profitable_cities_only | 19 | 14 | 4 | $+4.96 |
| exclude_pre_weak_cities | 19 | 14 | 4 | $+4.92 |
| buy_no_only | 19 | 7 | 12 | $-5.87 |
| buy_yes_only | 19 | 5 | 14 | $-3.66 |
| combo_blended_or_highraw_T22_28 | 19 | 9 | 10 | $-0.26 |

## 推荐候选 gate 的 Post 下钻

候选口径：`market_confirm_or_raw_edge_gt_0.25`。它保留 market confirmation，也给高 raw edge 一个例外，避免把 raw 还有效的尾部信号一起砍掉。注意：从 walk-forward 强度看，城市层 gate 比这个 blended 变体更强；本节下钻 blended 变体，是为了给 `blended_filter_25_75_v0` 的 paper/shadow 参数提供依据。

按 side：
| side | fills | pnl | kept_pnl | filtered_pnl | delta | avoided_loss | missed_profit | kept_fills | filtered_fills |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BUY_YES | 105 | $-50.08 | $+9.88 | $-59.96 | $+59.96 | $141.75 | $81.79 | 16 | 89 |
| BUY_NO | 172 | $-38.98 | $-2.86 | $-36.12 | $+36.12 | $156.95 | $120.83 | 54 | 118 |

按 model_version：
| model_version | fills | pnl | kept_pnl | filtered_pnl | delta | avoided_loss | missed_profit | kept_fills | filtered_fills |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ecmwf | 165 | $-119.33 | $-18.24 | $-101.09 | $+101.09 | $206.55 | $105.46 | 29 | 136 |
| gfs | 112 | $+30.27 | $+25.26 | $+5.01 | $-5.01 | $92.15 | $97.16 | 41 | 71 |

按 timing：
| hours_bin | fills | pnl | kept_pnl | filtered_pnl | delta | avoided_loss | missed_profit | kept_fills | filtered_fills |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| >T-28 | 166 | $-92.57 | $-15.22 | $-77.35 | $+77.35 | $194.52 | $117.17 | 39 | 127 |
| T-26-28 | 35 | $-29.59 | $+1.60 | $-31.19 | $+31.19 | $44.49 | $13.30 | 1 | 34 |
| T-22-24 | 38 | $+53.26 | $+35.29 | $+17.97 | $-17.97 | $19.99 | $37.96 | 21 | 17 |
| <T-22 | 14 | $-21.91 | $-14.04 | $-7.86 | $+7.86 | $9.71 | $1.85 | 6 | 8 |
| T-24-26 | 24 | $+1.75 | $-0.60 | $+2.35 | $-2.35 | $29.99 | $32.33 | 3 | 21 |

按城市（按过滤影响绝对值排序）：
| city | fills | pnl | kept_pnl | filtered_pnl | delta | avoided_loss | missed_profit | kept_fills | filtered_fills |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LA | 19 | $+23.80 | $-7.92 | $+31.72 | $-31.72 | $0.00 | $31.72 | 13 | 6 |
| BuenosAires | 14 | $-28.59 | $+0.00 | $-28.59 | $+28.59 | $34.98 | $6.39 | 0 | 14 |
| Munich | 15 | $-18.05 | $+4.09 | $-22.14 | $+22.14 | $24.71 | $2.57 | 1 | 14 |
| Madrid | 14 | $+17.71 | $+0.00 | $+17.71 | $-17.71 | $14.64 | $32.35 | 0 | 14 |
| NYC | 22 | $-13.27 | $+2.57 | $-15.84 | $+15.84 | $29.70 | $13.86 | 1 | 21 |
| Miami | 21 | $+12.51 | $+27.79 | $-15.28 | $+15.28 | $19.99 | $4.71 | 13 | 8 |
| Karachi | 16 | $-14.96 | $+0.00 | $-14.96 | $+14.96 | $24.99 | $10.03 | 0 | 16 |
| Amsterdam | 7 | $-19.92 | $-5.00 | $-14.92 | $+14.92 | $14.92 | $0.00 | 2 | 5 |
| Jeddah | 18 | $-17.81 | $-5.00 | $-12.81 | $+12.81 | $19.84 | $7.03 | 3 | 15 |
| Shanghai | 10 | $+12.54 | $+2.04 | $+10.50 | $-10.50 | $9.23 | $19.73 | 2 | 8 |
| Moscow | 17 | $-11.71 | $-2.93 | $-8.78 | $+8.78 | $14.99 | $6.21 | 5 | 12 |
| Warsaw | 15 | $-0.89 | $+6.95 | $-7.83 | $+7.83 | $14.51 | $6.68 | 4 | 11 |
| Tokyo | 8 | $+8.70 | $+1.86 | $+6.83 | $-6.83 | $0.00 | $6.83 | 5 | 3 |
| Chengdu | 8 | $-4.50 | $+1.55 | $-6.05 | $+6.05 | $9.85 | $3.80 | 2 | 6 |
| Lucknow | 4 | $-10.00 | $-5.00 | $-5.00 | $+5.00 | $5.00 | $0.00 | 2 | 2 |
| Istanbul | 7 | $-4.89 | $+0.00 | $-4.89 | $+4.89 | $9.67 | $4.78 | 0 | 7 |

## 交易建议

1. **原始 `mid_price_core_v1_25_75` 不建议继续原 size live**。post 已从正收益转为明显负收益，且 BUY_YES/BUY_NO 都退化；继续裸跑没有量化依据。
2. **下一步主研究方向应转向城市层 regime / 数据源质量**。非后验 city gate 同时改善 pre 和 post，优先级高于继续调 blended 阈值。
3. **`blended_filter_25_75_v0` 可以 paper/shadow，暂不建议直接 live**。理由是 pure blended gate post 有效，但 pre 明显伤害收益；它更像 recent drift filter，不是稳定 alpha。
4. **paper/shadow 候选参数**：主线用城市风险 gate 控 size/城市池；blender 侧优先测试 `blended_edge>=0.10 OR raw_edge>0.25`，再叠加 `T-22 到 T-28` 加权更高、`>T-28` 降 size。
5. **上线前 gate**：至少要等 1 周新 shadow 样本，并要求 `missed_profit <= avoided_loss`、日期级 median delta 不为负、city/side/model_version 不集中靠单一尾部事件。
6. **研究工程下一步**：把 `forecast_jump`、`side_flip`、同 market opposite-side transition 物化进 `fact_signal_candidates`，再做逐 snapshot 血缘复盘；现在的 fact 表只能做成交后 overlay，无法完整解释 raw 失效机制。

## 口径限制

- 本报告没有模拟真实成交率变化；如果 gate 影响订单排队、盘口滑点或资金占用，真实 paper/live 会偏离 overlay。
- gate 阈值不是最终参数；本报告只用于收敛候选研究方向。
- `post_weak_city_blacklist_DIAGNOSTIC` 是后验黑名单，只用于估计亏损城市贡献，不能作为上线依据。
- 当前 fact 表没有显式 `forecast_jump` / `side_flip` 字段，因此机制解释仍需下一轮血缘研究。
