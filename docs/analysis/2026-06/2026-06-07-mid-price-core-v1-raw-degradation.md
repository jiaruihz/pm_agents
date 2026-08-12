# mid_price_core_v1 / v1_25_75 raw probability 退化归因

## 数据快照

- 数据源：`/home/rui/projects/pm_agent/runtime/weather.db`，只读 `fact_trades` / `fact_signal_candidates`。
- DB mtime UTC：`2026-06-07T05:49:19.463464+00:00`；`MAX(fact_built_at_utc)`：`2026-06-07T05:48:57.787507+00:00`。
- CLOB gate：本次分析前已单独运行 `python3 scripts/analysis/weather_clob_fill_coverage_gate.py`，结果 `gate_pass=true`，DB/cache/fact cost diff = 0。
- 目标样本：`mid_price_core_v1_25_75` / `strategy_id=live_weather_edge_v1_4ef9b3ec3e2e` / `trade_class=live_real` / `settlement_status=settled`。

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
| paper | filled | 2285 | 2285 |
| paper | simulated_open | 1077 | 1077 |
| polymarket_clob | error | 151 | 0 |
| polymarket_clob | submitted | 1524 | 1302 |
| snapshot_replay | filled | 636 | 636 |

目标策略样本：
| strategy_id | strategy_name | trade_class | fills | min_target_date | max_target_date |
| --- | --- | --- | --- | --- | --- |
| live_weather_edge_v1_4ef9b3ec3e2e | t1_trading_mid_price_core_v1_notional_$5.0_shares_10.0_entry_0.25-0.75 | live_real | 859 | 2026-05-16 | 2026-06-07 |
| live_weather_edge_v1_4ef9b3ec3e2e | t1_trading_mid_price_core_v1_notional_$5.0_shares_10.0_entry_0.25-0.75 | live_simulated | 593 | 2026-05-16 | 2026-06-07 |

## 目标指标与分母

`mid_price_core_v1_25_75_degradation` = v1 25-75 raw edge 入场的真实已成交已结算 fill，从 `target_date < 2026-06-01` 到 `target_date >= 2026-06-01` 的退化。Realized PnL 只用 `fact_trades.pnl_usd_at_fill`；raw edge / market implied / blended edge 只用于解释和分桶，不重新计算成交 PnL。

market implied YES probability 口径：BUY_YES 用 `market_price`，BUY_NO 用 `1 - market_price`。raw side edge 用成交价作 overlay：BUY_YES=`model_p_yes-fill_price`，BUY_NO=`(1-model_p_yes)-fill_price`；原始 fact edge 另以 `edge` 均值列出作 sanity check。

## 结论

- 真实机制（较强）：退化主要不是 25-75 entry band 本身消失，而是 raw weather probability 的边际 raw edge 在 6 月后失去 payoff。主样本 6 月前 430 fills，PnL `$99.06`、ROI 8.2%；6 月后 277 fills，PnL `$-89.06`、ROI -12.1%。6 月后 `raw_edge_at_fill` 均值只从 0.216 降到 0.199，但 win rate 从 58.8% 降到 43.7%，说明主要是 raw 概率排序/校准失效，而不是入场价区间消失。
- 真实机制（较强）：不是单侧事故，BUY_YES 和 BUY_NO 都变差；BUY_YES 从 `$60.73` 到 `$-50.08`（delta `$-110.81`），BUY_NO 从 `$38.33` 到 `$-38.98`（delta `$-77.31`）。BUY_YES 的绝对亏损和退化幅度更大，BUY_NO 仍然由高胜率低 payoff/尾部 loser 拖累。
- 统计相关（中等）：`abs(model_p_yes-market_implied_p_yes)` 没有扩大，均值反而从 0.278 降到 0.190；6 月后亏损集中在 `0.10-0.20` 中等分歧和 `raw_edge_at_fill<=0.25` 的边际信号，高分歧/高 raw edge 样本仍略正。blender 拦截有效，更像近期把边际 raw edge 过滤掉，而不是证明“分歧越大越亏”。
- 统计相关（中等）：3 个城市从盈利转亏，最差为 `BuenosAires`（6 月后 `$-28.59`）；4 个城市仍保持盈利，说明不是所有城市同步 regime shift。
- 尾部结构（中等）：6 月后最差 3 个 city-day 占 gross losses 13.5%；剔除它们后 6 月后 PnL 为 `$-46.83`。这说明亏损集中，但不完全是单一事故。
- 真实机制（中等）：GFS 不是主要拖累；6 月后 GFS PnL 为正，ECMWF 明显为负。timing 上 `>T-28` 和 `T-26-28` 拖累，`T-22-24` 仍为正。
- 样本不足/字段不足：forecast_jump 和 side_flip 没有明确 fact 字段；candidate edge jump proxy 与 opposite-side eligible rate 在 6 月前后接近，当前不能把它们定性为主因。

### Calibration/code-break audit（合并自同日平行报告）

- fact 层没有找到 6/01 单点版本断裂：目标实例在前后窗口均记录
  `code_version=live-cycle-migration`、相同 strategy ID 和 0.25–0.75 entry
  window。但该 code version 不是 git SHA，所以只能说明“没有被 fact
  记录的断点”，不能证明运行代码没有变化。
- 当时最可疑的提交是 `127b1a0`（per-side entry band + min-edge default）和
  `5dd7fc3`（explicit strategy instances）。它们可能改变候选集合或实例归属，
  但现有证据不能把概率退化归因给其中任一提交；需要 raw signal → plan →
  order → fact 的逐层 lineage 才能确认。
- raw side-probability calibration 并不单调：6 月后 `<=0.45` bucket 为 43
  fills / -59.9% ROI，`0.55–0.65` 为 21 / +86.6%，`0.65–0.75` 为 34 /
  -15.3%，`0.75–0.85` 为 50 / +15.2%，`>0.85` 为 87 / -21.3%。这与
  下方 raw-edge 结果一致：不能用单个 probability threshold 修复。
- candidate universe 只部分复现 fill 退化。6 月后 raw-edge `0.10–0.15`
  有 33 eligible / 32 fills、counterfactual PnL +25.75；`0.15–0.25` 有
  37 / 33、-22.55；`>0.25` 有 35 / 28、-6.94。候选与真实成交不完全
  同向，是 execution selection、timing 或实例归属仍需审计的证据，而不是
  为边际 raw signal 洗白。

## 1. 6 月前后总览

| period | fills | active_days | cost_usd | pnl_usd | roi | win_rate | avg_raw_edge | avg_abs_yes_divergence | avg_hours_to_settle | pnl_ex_top3_wins |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | 277 | 5 | 738.52 | -89.06 | -12.1% | 43.7% | 0.199 | 0.190 | 29.954 | -123.52 |
| pre_2026_06_01 | 430 | 14 | 1211.27 | 99.06 | 8.2% | 58.8% | 0.216 | 0.278 | 28.265 | 58.70 |

读法：`pnl_ex_top3_wins` 是剔除各 period 最大 3 笔盈利 fill 后的 PnL，用于观察是否靠少数赢家支撑。

## 2. BUY_YES / BUY_NO 方向

| period | side | fills | active_days | cost_usd | pnl_usd | roi | win_rate | avg_raw_edge | avg_abs_yes_divergence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | BUY_YES | 105 | 5 | 212.89 | -50.08 | -23.5% | 24.8% | 0.177 | 0.172 |
| post_2026_06_01 | BUY_NO | 172 | 5 | 525.63 | -38.98 | -7.4% | 55.2% | 0.212 | 0.201 |
| pre_2026_06_01 | BUY_YES | 124 | 14 | 276.15 | 60.73 | 22.0% | 41.1% | 0.182 | 0.170 |
| pre_2026_06_01 | BUY_NO | 306 | 14 | 935.13 | 38.33 | 4.1% | 66.0% | 0.231 | 0.322 |

## 3. 城市退化分类

| city | class | pre_fills | pre_days | pre_pnl | pre_roi | post_fills | post_days | post_pnl | post_roi | delta_pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BuenosAires | persistently_weak | 13 | 4 | -3.06 | -8.7% | 14 | 5 | -28.59 | -63.6% | -25.53 |
| Amsterdam | sample_insufficient | 11 | 3 | +8.60 | 28.9% | 7 | 2 | -19.92 | -100.0% | -28.52 |
| Munich | persistently_weak | 8 | 3 | -16.85 | -67.6% | 15 | 4 | -18.05 | -52.0% | -1.20 |
| Jeddah | persistently_weak | 11 | 5 | -3.42 | -13.7% | 18 | 5 | -17.81 | -45.0% | -14.40 |
| Karachi | persistently_weak | 18 | 5 | -1.86 | -3.5% | 16 | 5 | -14.96 | -37.7% | -13.11 |
| NYC | persistently_weak | 35 | 10 | -18.92 | -21.3% | 22 | 5 | -13.27 | -25.0% | +5.65 |
| Moscow | persistently_weak | 10 | 5 | -6.78 | -20.5% | 17 | 5 | -11.71 | -23.2% | -4.92 |
| Lucknow | sample_insufficient | 3 | 1 | -1.61 | -16.7% | 4 | 2 | -10.00 | -100.0% | -8.38 |
| Ankara | persistently_weak | 15 | 5 | -9.95 | -22.8% | 15 | 5 | -8.02 | -20.4% | +1.93 |
| Istanbul | profit_to_loss | 10 | 4 | +8.13 | 24.8% | 7 | 3 | -4.89 | -25.0% | -13.02 |
| Chengdu | sample_insufficient | 4 | 3 | +5.74 | 38.3% | 8 | 4 | -4.50 | -19.3% | -10.24 |
| Guangzhou | persistently_weak | 7 | 3 | -1.69 | -7.2% | 8 | 5 | -3.35 | -10.0% | -1.66 |
| Singapore | sample_insufficient | 6 | 3 | -4.38 | -21.9% | 7 | 1 | -2.95 | -29.5% | +1.44 |
| London | profit_to_loss | 33 | 12 | +24.82 | 24.4% | 16 | 5 | -2.70 | -5.8% | -27.52 |
| Seattle | sample_insufficient | 12 | 4 | +22.54 | 84.0% | 3 | 1 | -1.80 | -18.1% | -24.35 |
| Manila | sample_insufficient | 10 | 2 | -17.99 | -72.7% | 13 | 4 | -0.91 | -2.3% | +17.08 |
| Warsaw | profit_to_loss | 34 | 11 | +44.05 | 56.8% | 15 | 5 | -0.89 | -2.5% | -44.93 |
| Austin | sample_insufficient | 16 | 5 | +3.58 | 7.2% | 0 | 0 | +0.00 |  | -3.58 |
| Beijing | sample_insufficient | 13 | 6 | -17.08 | -44.0% | 0 | 0 | +0.00 |  | +17.08 |
| Chicago | sample_insufficient | 4 | 2 | -0.70 | -4.7% | 0 | 0 | +0.00 |  | +0.70 |
| Paris | sample_insufficient | 27 | 9 | +7.34 | 9.3% | 0 | 0 | +0.00 |  | -7.34 |
| Tokyo | stable_profitable | 31 | 11 | +24.56 | 29.7% | 8 | 4 | +8.70 | 44.6% | -15.86 |
| Miami | stable_profitable | 41 | 13 | +16.92 | 15.3% | 21 | 4 | +12.51 | 29.2% | -4.41 |
| Shanghai | improved_or_mixed | 8 | 5 | -0.26 | -0.9% | 10 | 4 | +12.54 | 36.7% | +12.80 |
| Madrid | stable_profitable | 13 | 7 | +8.07 | 18.5% | 14 | 5 | +17.71 | 45.1% | +9.64 |
| LA | stable_profitable | 37 | 11 | +29.27 | 30.1% | 19 | 5 | +23.80 | 44.6% | -5.46 |

### 6 月后城市亏损排行

| period | city | fills | active_days | cost_usd | pnl_usd | roi | win_rate | avg_abs_yes_divergence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | BuenosAires | 14 | 5 | 44.97 | -28.59 | -63.6% | 14.3% | 0.142 |
| post_2026_06_01 | Amsterdam | 7 | 2 | 19.92 | -19.92 | -100.0% | 0.0% | 0.210 |
| post_2026_06_01 | Munich | 15 | 4 | 34.69 | -18.05 | -52.0% | 26.7% | 0.164 |
| post_2026_06_01 | Jeddah | 18 | 5 | 39.62 | -17.81 | -45.0% | 33.3% | 0.189 |
| post_2026_06_01 | Karachi | 16 | 5 | 39.73 | -14.96 | -37.7% | 43.8% | 0.162 |
| post_2026_06_01 | NYC | 22 | 5 | 53.04 | -13.27 | -25.0% | 31.8% | 0.161 |
| post_2026_06_01 | Moscow | 17 | 5 | 50.40 | -11.71 | -23.2% | 35.3% | 0.185 |
| post_2026_06_01 | Lucknow | 4 | 2 | 10.00 | -10.00 | -100.0% | 0.0% | 0.307 |
| post_2026_06_01 | Ankara | 15 | 5 | 39.41 | -8.02 | -20.4% | 33.3% | 0.230 |
| post_2026_06_01 | Istanbul | 7 | 3 | 19.59 | -4.89 | -25.0% | 28.6% | 0.123 |
| post_2026_06_01 | Chengdu | 8 | 4 | 23.29 | -4.50 | -19.3% | 50.0% | 0.205 |
| post_2026_06_01 | Guangzhou | 8 | 5 | 33.48 | -3.35 | -10.0% | 62.5% | 0.154 |
| post_2026_06_01 | Singapore | 7 | 1 | 9.99 | -2.95 | -29.5% | 28.6% | 0.127 |
| post_2026_06_01 | London | 16 | 5 | 46.84 | -2.70 | -5.8% | 43.8% | 0.160 |
| post_2026_06_01 | Seattle | 3 | 1 | 9.99 | -1.80 | -18.1% | 66.7% | 0.285 |

## 4. raw probability vs market implied probability 分歧

按 `abs(model_p_yes - market_implied_p_yes)` 分桶：

| period | divergence_bin | fills | cost_usd | pnl_usd | roi | win_rate | avg_raw_edge | avg_abs_yes_divergence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | >0.20 | 102 | 289.17 | 8.84 | 3.1% | 56.9% | 0.295 | 0.282 |
| post_2026_06_01 | 0.10-0.20 | 175 | 449.35 | -97.90 | -21.8% | 36.0% | 0.143 | 0.136 |
| pre_2026_06_01 | 0.05-0.10 | 1 | 5.00 | -5.00 | -100.0% | 0.0% | 0.116 | 0.080 |
| pre_2026_06_01 | <=0.05 | 1 | 4.21 | -4.21 | -100.0% | 0.0% | 0.495 | 0.020 |
| pre_2026_06_01 | 0.10-0.20 | 206 | 512.39 | 62.50 | 12.2% | 54.9% | 0.158 | 0.146 |
| pre_2026_06_01 | >0.20 | 222 | 689.67 | 45.77 | 6.6% | 63.1% | 0.270 | 0.403 |

按 raw edge 分桶：

| period | raw_edge_bin | fills | cost_usd | pnl_usd | roi | win_rate | avg_raw_edge | avg_abs_yes_divergence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | >0.25 | 70 | 192.07 | 7.02 | 3.7% | 55.7% | 0.328 | 0.313 |
| post_2026_06_01 | 0.15-0.25 | 86 | 247.77 | -42.13 | -17.0% | 47.7% | 0.194 | 0.185 |
| post_2026_06_01 | 0.10-0.15 | 121 | 298.68 | -53.95 | -18.1% | 33.9% | 0.127 | 0.122 |
| pre_2026_06_01 | 0.10-0.15 | 109 | 277.42 | 49.65 | 17.9% | 60.6% | 0.124 | 0.163 |
| pre_2026_06_01 | >0.25 | 112 | 351.44 | 31.03 | 8.8% | 58.0% | 0.346 | 0.411 |
| pre_2026_06_01 | 0.15-0.25 | 209 | 582.41 | 18.38 | 3.2% | 58.4% | 0.196 | 0.267 |

按 raw side probability 相对 market side probability 的优势分桶：

| period | side_divergence_bin | fills | cost_usd | pnl_usd | roi | win_rate | avg_raw_edge | avg_abs_yes_divergence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | >0.25 | 62 | 172.71 | 3.02 | 1.7% | 53.2% | 0.337 | 0.323 |
| post_2026_06_01 | 0.15-0.25 | 84 | 237.21 | -25.93 | -10.9% | 52.4% | 0.205 | 0.196 |
| post_2026_06_01 | 0.05-0.15 | 131 | 328.60 | -66.15 | -20.1% | 33.6% | 0.129 | 0.123 |
| pre_2026_06_01 | <=0.05 | 3 | 14.21 | -1.06 | -7.4% | 33.3% | 0.240 | 0.075 |
| pre_2026_06_01 | 0.05-0.15 | 111 | 269.58 | 33.68 | 12.5% | 53.2% | 0.134 | 0.121 |
| pre_2026_06_01 | 0.15-0.25 | 145 | 378.58 | 20.12 | 5.3% | 59.3% | 0.200 | 0.192 |
| pre_2026_06_01 | >0.25 | 171 | 548.90 | 46.31 | 8.4% | 62.6% | 0.283 | 0.457 |

## 5. Forecast timing / GFS / side flip proxy

fact 表当前没有显式 `forecast_jump` / `side_flip` 字段；本节只使用授权 fact 字段做代理检验：`hours_to_settle`、`model_version`、`snapshot_hour_utc`、candidate 的 `edge_max-edge`、以及同一 market/bracket 是否出现 eligible opposite side。该结论应视为机制线索，不是完整逐 snapshot 血缘。

按入场窗口：

| period | hours_bin | fills | cost_usd | pnl_usd | roi | win_rate | avg_hours_to_settle |
| --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | <T-22 | 14 | 36.94 | -21.91 | -59.3% | 21.4% | 20.908 |
| post_2026_06_01 | T-24-26 | 24 | 59.14 | 1.75 | 3.0% | 50.0% | 25.274 |
| post_2026_06_01 | T-26-28 | 35 | 72.79 | -29.59 | -40.7% | 25.7% | 27.484 |
| post_2026_06_01 | T-22-24 | 38 | 85.75 | 53.26 | 62.1% | 63.2% | 22.564 |
| post_2026_06_01 | >T-28 | 166 | 483.91 | -92.57 | -19.1% | 44.0% | 33.606 |
| pre_2026_06_01 | T-26-28 | 6 | 20.25 | 9.76 | 48.2% | 50.0% | 27.810 |
| pre_2026_06_01 | <T-22 | 18 | 44.50 | 29.06 | 65.3% | 94.4% | 19.593 |
| pre_2026_06_01 | T-22-24 | 57 | 148.70 | 10.77 | 7.2% | 45.6% | 23.001 |
| pre_2026_06_01 | >T-28 | 171 | 465.24 | -37.95 | -8.2% | 55.0% | 33.594 |
| pre_2026_06_01 | T-24-26 | 178 | 532.59 | 87.41 | 16.4% | 63.5% | 25.723 |

按模型版本：

| period | model_version | fills | cost_usd | pnl_usd | roi | win_rate | avg_abs_yes_divergence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | gfs | 112 | 300.43 | 30.27 | 10.1% | 54.5% | 0.209 |
| post_2026_06_01 | ecmwf | 165 | 438.09 | -119.33 | -27.2% | 36.4% | 0.177 |
| pre_2026_06_01 | ecmwf | 196 | 563.52 | 38.80 | 6.9% | 58.7% | 0.240 |
| pre_2026_06_01 | gfs | 234 | 647.75 | 60.26 | 9.3% | 59.0% | 0.310 |

已成交样本 joined candidate timing proxy：

| period | fills_with_candidate | avg_candidate_hours_to_settle | avg_n_snapshots | avg_edge_jump_proxy | opposite_side_eligible_rate | avg_slippage_vs_paper |
| --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | 238 | 23.03 | 59.03 | 0.180 | 84.2% | -0.0030 |
| pre_2026_06_01 | 408 | 22.96 | 63.46 | 0.180 | 85.7% | -0.0063 |

eligible candidate universe timing proxy：

| period | eligible | paper_ordered | live_filled | decision_window_missing_rate | opposite_side_eligible_rate | avg_abs_yes_divergence | avg_edge_jump_proxy |
| --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | 2822 | 438 | 215 | 45.3% | 70.7% | 0.061 | 0.064 |
| pre_2026_06_01 | 4745 | 622 | 282 | 42.6% | 70.9% | 0.068 | 0.070 |

## 6. 尾部日期/城市事件

- 6 月后全部亏损 city-day 的 gross loss：`$312.54`。
- 最差 3 个 city-day gross loss：`$42.23`，占 6 月后 gross losses `13.5%`。
- 6 月后整体 PnL：`$-89.06`；剔除最差 3 个 city-day 后 PnL：`$-46.83`。

| target_date | city | fills | cost_usd | pnl_usd | sides |
| --- | --- | --- | --- | --- | --- |
| 2026-06-01 | BuenosAires | 6 | 19.99 | -19.99 | BUY_NO,BUY_YES |
| 2026-06-04 | LA | 3 | 12.24 | -12.24 | BUY_NO,BUY_YES |
| 2026-06-05 | Karachi | 3 | 10.00 | -10.00 | BUY_NO,BUY_YES |
| 2026-06-04 | London | 2 | 10.00 | -10.00 | BUY_NO,BUY_YES |
| 2026-06-05 | Jeddah | 4 | 10.00 | -10.00 | BUY_NO,BUY_YES |
| 2026-06-05 | Miami | 3 | 10.00 | -10.00 | BUY_NO,BUY_YES |
| 2026-06-05 | BuenosAires | 4 | 10.00 | -10.00 | BUY_NO,BUY_YES |
| 2026-06-01 | Amsterdam | 4 | 9.99 | -9.99 | BUY_NO,BUY_YES |
| 2026-06-02 | Miami | 4 | 9.99 | -9.99 | BUY_NO,BUY_YES |
| 2026-06-02 | Amsterdam | 3 | 9.93 | -9.93 | BUY_NO,BUY_YES |
| 2026-06-02 | Madrid | 2 | 9.88 | -9.88 | BUY_NO,BUY_YES |
| 2026-06-02 | Ankara | 5 | 9.81 | -9.81 | BUY_NO,BUY_YES |

## 7. Blended gate 拦截的亏损特征

这里不是证明 blended gate 稳定赚钱，只看它在当前样本中拦下的 v1 亏损是否对应可解释特征。

| period | all_fills | all_pnl | kept_fills | kept_pnl | filtered_fills | filtered_pnl | delta_if_filter | filtered_avg_blended_edge | filtered_avg_abs_divergence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | 277 | -89.06 | 43 | -2.50 | 234 | -86.56 | +86.56 | 0.055 | 0.164 |
| pre_2026_06_01 | 430 | +99.06 | 64 | +31.42 | 366 | +67.63 | -67.63 | -0.006 | 0.270 |

6 月后被 blended gate 拦截的亏损按方向：

| side | fills | cost_usd | pnl_usd | roi | win_rate | avg_abs_yes_divergence |
| --- | --- | --- | --- | --- | --- | --- |
| BUY_YES | 90 | 188.41 | -62.62 | -33.2% | 20.0% | 0.144 |
| BUY_NO | 144 | 451.78 | -23.94 | -5.3% | 57.6% | 0.177 |

6 月后被 blended gate 拦截的亏损按城市：

| city | fills | cost_usd | pnl_usd | roi | win_rate | avg_abs_yes_divergence |
| --- | --- | --- | --- | --- | --- | --- |
| BuenosAires | 14 | 44.97 | -28.59 | -63.6% | 14.3% | 0.142 |
| Munich | 15 | 34.69 | -18.05 | -52.0% | 26.7% | 0.164 |
| Karachi | 16 | 39.73 | -14.96 | -37.7% | 43.8% | 0.162 |
| Amsterdam | 5 | 14.92 | -14.92 | -100.0% | 0.0% | 0.163 |
| NYC | 22 | 53.04 | -13.27 | -25.0% | 31.8% | 0.161 |
| Jeddah | 15 | 34.62 | -12.81 | -37.0% | 40.0% | 0.159 |
| Miami | 11 | 29.71 | -11.34 | -38.2% | 36.4% | 0.185 |
| Lucknow | 4 | 10.00 | -10.00 | -100.0% | 0.0% | 0.307 |
| Moscow | 15 | 40.82 | -9.07 | -22.2% | 33.3% | 0.175 |
| Chengdu | 6 | 19.84 | -6.05 | -30.5% | 33.3% | 0.177 |
| Istanbul | 7 | 19.59 | -4.89 | -25.0% | 28.6% | 0.123 |
| London | 15 | 43.44 | -4.30 | -9.9% | 40.0% | 0.151 |

6 月后被 blended gate 拦截的亏损按分歧/时间窗口：

| divergence_bin | fills | cost_usd | pnl_usd | roi | win_rate | avg_abs_yes_divergence |
| --- | --- | --- | --- | --- | --- | --- |
| 0.10-0.20 | 175 | 449.35 | -97.90 | -21.8% | 36.0% | 0.136 |
| >0.20 | 59 | 190.84 | 11.34 | 5.9% | 64.4% | 0.248 |

| hours_bin | fills | cost_usd | pnl_usd | roi | win_rate | avg_hours_to_settle |
| --- | --- | --- | --- | --- | --- | --- |
| >T-28 | 146 | 435.34 | -69.88 | -16.1% | 45.9% | 33.421 |
| T-26-28 | 34 | 69.39 | -31.19 | -44.9% | 23.5% | 27.513 |
| <T-22 | 12 | 27.36 | -12.32 | -45.1% | 25.0% | 21.146 |
| T-24-26 | 22 | 55.96 | 4.92 | 8.8% | 54.5% | 25.300 |
| T-22-24 | 20 | 52.15 | 21.91 | 42.0% | 55.0% | 22.635 |

## 交易动作建议

1. **临时关停 `mid_price_core_v1_25_75` live，或至少降到 shadow/极小 size**。6 月后 BUY_YES/BUY_NO 都转负，继续原 size live 没有量化依据。
2. **若必须保留探索，只允许受限 shadow 组合**：优先看 `raw_edge_at_fill>0.25`、`T-22-24/T-24-26`、GFS、以及 LA/Miami/Tokyo/Madrid/Shanghai 这类近期稳定城市；不要把它作为已验证 live alpha。
3. **城市层面先 blacklist/降 size 样本充分且 6 月后弱的城市**：BuenosAires、Munich、Jeddah、Karachi、NYC、Moscow、Ankara；London/Warsaw/Istanbul 属于 profit-to-loss，需要至少 shadow 复核。Amsterdam/Lucknow/Seattle 等样本不足但亏损尖锐，只降级观察不做强结论。
4. **不要用 `abs(model_p_yes-market_implied_p_yes)>0.20` 作为简单黑名单**；本样本高分歧并不亏。更合理的近期风控是 `blended_edge<0.10`、`raw_edge<=0.25`、以及不利 timing/city 的组合过滤，但它仍只是 recent drift filter。
5. **下一步工程动作**：把 forecast_jump、side_flip、同 market opposite-side transition 物化进 `fact_signal_candidates`，再做 T-22/T-28 的逐 snapshot 血缘复盘。

## 口径限制

- 本报告没有同步 N100 重建 DB；按用户指定使用当前 `runtime/weather.db`，DB build time 见数据快照。
- `forecast_jump` / `side_flip` 没有被物化为 fact 字段；本报告只用 candidate proxy，不给强机制结论。
- 城市少于 5 个 settled fills 或少于 3 个 active target days 的分类均降级为 `sample_insufficient`。
- blended gate 的正 delta 是 recent drift filter 线索，不是稳定 alpha 证明。
- 本报告已吸收同日 `raw-calibration-drift` 的独有 code-break 与 calibration
  结论；详细历史表仍可由 git 历史恢复，不再作为第二份当前证据入口。
