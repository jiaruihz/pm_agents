# mid_price_core_v1 raw probability 校准漂移与代码断点检查

## 数据快照

- 数据源：`/home/rui/projects/pm_agent/runtime/weather.db`。
- DB mtime UTC：`2026-06-07T05:49:19.463464+00:00`；`MAX(fact_built_at_utc)`：`2026-06-07T05:48:57.787507+00:00`。
- 目标：`mid_price_core_v1_25_75` / `strategy_id=live_weather_edge_v1_4ef9b3ec3e2e` / live_real settled fills。
- PnL 只读 `fact_trades.pnl_usd_at_fill`；候选机会只读 `fact_signal_candidates.counterfactual_pnl`，不混作实盘 PnL。

trade_class：
| trade_class | rows |
| --- | --- |
| live_real | 1302 |
| live_simulated | 1077 |
| paper | 2285 |
| snapshot_replay | 636 |

settlement_status：
| settlement_status | rows |
| --- | --- |
| [NULL] | 388 |
| settled | 4912 |

candidate coverage：
| rows | eligible | paper_ordered | live_filled |
| --- | --- | --- | --- |
| 23299 | 7567 | 2795 | 503 |

## 先答：像不像中间代码改坏？

- 目前不像一个简单的 6/1 单点 code_version 断裂：目标样本的 `strategy_id=live_weather_edge_v1_4ef9b3ec3e2e` 和 `entry_price_window=0.25-0.75` 连续覆盖 2026-05-16 到 2026-06-07；`fact_trades.code_version` 在本 DB 中只有 1 个取值（`live-cycle-migration`），6 月前后相同。
- 但 `code_version=live-cycle-migration` 不是精确 git SHA，所以它只能说明 fact 层没有记录出版本断点，不能排除真实运行代码在 5/31 已变。
- 代码层最可疑的是 `127b1a0 strategy: per-side entry band + min_edge gate` 和 `5dd7fc3 ops: run weather live strategies as explicit instances`。`127b1a0` 的提交说明明确写到 live_cycle defaults baked per-side band strategy，且修改了 signal builder 的 per-side gate；这类改动可能改变候选进入 planner 的集合、实例归属或 `entry_price_window` 记录，而不一定体现在 `execution_policy=mid_price_core_v1`。
- 因此结论是：**不像 PnL 公式或 fact 表单点断裂，但存在实例/默认参数层改动嫌疑**。要最终排除，需要抽样 raw live signal -> plan -> order -> fact 四层，验证 6/1 后 `v1_25_75` 是否真的仍是 flat 0.25-0.75/min_edge 0.10，而不是被 per-side defaults 或实例启动脚本污染。

### v1_25_75 按 code_version / period

| period | code_version | fills | runs | min_target_date | max_target_date | cost_usd | pnl_usd | roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | live-cycle-migration | 277 | 78 | 2026-06-01 | 2026-06-05 | 738.52 | -89.06 | -0.1206 |
| pre_2026_06_01 | live-cycle-migration | 430 | 131 | 2026-05-16 | 2026-05-31 | 1211.27 | 99.06 | 0.0818 |

### v1_25_75 按 target_date / code_version

| target_date | code_version | fills | runs | avg_model_p_yes | avg_market_price | avg_fact_edge | pnl_usd |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-16 | live-cycle-migration | 16 | 6 | 0.3875 | 0.4781 | 0.1199 | 8.99 |
| 2026-05-17 | live-cycle-migration | 9 | 4 | 0.2249 | 0.4083 | 0.0146 | 19.16 |
| 2026-05-20 | live-cycle-migration | 12 | 6 | 0.2192 | 0.3725 | -0.1533 | 16.92 |
| 2026-05-21 | live-cycle-migration | 18 | 8 | 0.2976 | 0.3314 | -0.0338 | -2.04 |
| 2026-05-22 | live-cycle-migration | 22 | 9 | 0.23 | 0.3868 | -0.0673 | 34.24 |
| 2026-05-23 | live-cycle-migration | 17 | 6 | 0.1345 | 0.4085 | -0.274 | 11.84 |
| 2026-05-24 | live-cycle-migration | 27 | 6 | 0.3073 | 0.3726 | -0.0653 | 28.5 |
| 2026-05-25 | live-cycle-migration | 25 | 9 | 0.2455 | 0.3604 | -0.1149 | 12.89 |
| 2026-05-26 | live-cycle-migration | 25 | 10 | 0.2341 | 0.4174 | -0.0813 | -11.51 |
| 2026-05-27 | live-cycle-migration | 39 | 12 | 0.2054 | 0.5282 | 0.1554 | 9.42 |
| 2026-05-28 | live-cycle-migration | 45 | 9 | 0.212 | 0.5711 | 0.1617 | -11.45 |
| 2026-05-29 | live-cycle-migration | 59 | 17 | 0.2236 | 0.4926 | 0.1332 | 57.95 |
| 2026-05-30 | live-cycle-migration | 56 | 13 | 0.2366 | 0.5479 | 0.1866 | -0.97 |
| 2026-05-31 | live-cycle-migration | 60 | 16 | 0.287 | 0.5132 | 0.1705 | -74.86 |
| 2026-06-01 | live-cycle-migration | 67 | 11 | 0.3108 | 0.5092 | 0.2046 | 14.9 |
| 2026-06-02 | live-cycle-migration | 55 | 18 | 0.2627 | 0.5412 | 0.1615 | -18.12 |
| 2026-06-03 | live-cycle-migration | 47 | 17 | 0.2838 | 0.4828 | 0.1644 | 29.85 |
| 2026-06-04 | live-cycle-migration | 54 | 18 | 0.2638 | 0.511 | 0.2094 | -46.19 |
| 2026-06-05 | live-cycle-migration | 54 | 14 | 0.3432 | 0.5072 | 0.2029 | -69.49 |

### 相关代码提交窗口

| sha | date | subject |
| --- | --- | --- |
| 8c6f9d2 | 2026-06-01 | strategy: retire maker_queue_v1 active paths |
| e957b03 | 2026-06-01 | Fix weather live exposure guards |
| 7a3d68c | 2026-05-31 | fix: validate weather strategy instance notional caps |
| 5dd7fc3 | 2026-05-31 | ops: run weather live strategies as explicit instances |
| 04f611c | 2026-05-31 | fix: clarify live dedup skip reason |
| 127b1a0 | 2026-05-31 | strategy: per-side entry band + min_edge gate (BUY_YES vs BUY_NO) |
| c4ad8cc | 2026-05-30 | fix(weather-exec): prevent TypeError crash when split leg has no matching quote |
| 7943a37 | 2026-05-30 | feat(weather-exec): floor sub-minimum orders to min_order_shares |
| f7895a2 | 2026-05-30 | fix(weather): defer mid_price_core_v2 quotes per-leg when no live book |
| f17b0c2 | 2026-05-30 | strategy: add mid_price_core_v2 execution policy (split taker/maker on low-price high-edge) |
| ed20065 | 2026-05-29 | feat: add weather signal candidates analysis layer |
| 86daa20 | 2026-05-29 | feat(fact_trades): migrate PnL computation to fact_trades as single source of truth |
| 3903edc | 2026-05-29 | feat: add fact_trades builder — unified fill-grain PnL fact table |
| c4bd996 | 2026-05-28 | feat: add helper functions to snapshot signal builder |
| a88cbd2 | 2026-05-28 | fix: restore defer_to_executor path for maker_queue when no live book in signal |
| 20b41ad | 2026-05-27 | feat: add maker_queue_v2 execution policy |

## raw probability 失效机制

- 6 月后退化的核心是 raw probability 的排序/校准失效：已成交样本从 `+99.06` 变 `-89.06`，win rate 从 58.8% 降到 43.7%。
- 最关键断点在 raw edge 低/中段：6 月前 `0.10-0.15` 为 `+49.65`，6 月后为 `-53.95`；6 月前 `0.15-0.25` 为 `+18.38`，6 月后为 `-42.13`。高 raw edge `>0.25` 6 月后仍为 `+7.02`。
- 这说明不是所有已成交 raw 信号失效，而是原 v1 live 接受的边际 raw edge 现在噪声过大。候选机会宇宙只部分支持这个结论：`0.15-0.25` 同样转弱，但 `0.10-0.15` 在候选表里 6 月后反而正，说明执行选择/实例归属/timing 仍可能参与了退化。不中止实盘时，应先切掉边际段并做四层审计，而不是全盘否定 raw model。

## 1. 已成交样本总览

| period | fills | days | cost_usd | pnl_usd | roi | win_rate | avg_side_p_raw | avg_raw_edge | avg_abs_yes_divergence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | 277 | 5 | 738.52 | -89.06 | -12.1% | 43.7% | 0.701 | 0.199 | 0.190 |
| pre_2026_06_01 | 430 | 14 | 1211.27 | 99.06 | 8.2% | 58.8% | 0.751 | 0.216 | 0.278 |

## 2. raw side probability 校准桶

这里用 side 视角：BUY_YES=`model_p_yes`，BUY_NO=`1-model_p_yes`。若 raw probability 可靠，`side_p_bin` 越高，win rate/ROI 应大体越好。

| period | side_p_bin | fills | days | cost_usd | pnl_usd | roi | win_rate | avg_side_p_raw | avg_raw_edge |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | 0.45-0.55 | 42 | 5 | 83.92 | -20.03 | -23.9% | 28.6% | 0.496 | 0.151 |
| post_2026_06_01 | 0.55-0.65 | 21 | 4 | 42.20 | 36.53 | 86.6% | 66.7% | 0.611 | 0.246 |
| post_2026_06_01 | 0.65-0.75 | 34 | 5 | 103.46 | -15.79 | -15.3% | 38.2% | 0.698 | 0.166 |
| post_2026_06_01 | 0.75-0.85 | 50 | 5 | 144.98 | 22.09 | 15.2% | 76.0% | 0.793 | 0.179 |
| post_2026_06_01 | <=0.45 | 43 | 5 | 89.01 | -53.31 | -59.9% | 4.7% | 0.399 | 0.134 |
| post_2026_06_01 | >0.85 | 87 | 5 | 274.94 | -58.56 | -21.3% | 48.3% | 0.919 | 0.267 |
| pre_2026_06_01 | 0.45-0.55 | 51 | 11 | 121.02 | 24.00 | 19.8% | 43.1% | 0.493 | 0.187 |
| pre_2026_06_01 | 0.55-0.65 | 25 | 11 | 68.14 | 63.20 | 92.7% | 76.0% | 0.614 | 0.194 |
| pre_2026_06_01 | 0.65-0.75 | 44 | 11 | 117.55 | 7.43 | 6.3% | 56.8% | 0.699 | 0.192 |
| pre_2026_06_01 | 0.75-0.85 | 82 | 10 | 229.65 | 28.04 | 12.2% | 72.0% | 0.801 | 0.175 |
| pre_2026_06_01 | <=0.45 | 49 | 8 | 98.08 | -19.85 | -20.2% | 24.5% | 0.404 | 0.135 |
| pre_2026_06_01 | >0.85 | 179 | 14 | 576.83 | -3.77 | -0.7% | 64.8% | 0.928 | 0.275 |

## 3. raw edge 单调性

| period | raw_edge_bin | fills | days | cost_usd | pnl_usd | roi | win_rate | avg_side_p_raw | avg_raw_edge |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | 0.10-0.15 | 121 | 5 | 298.68 | -53.95 | -18.1% | 33.9% | 0.583 | 0.127 |
| post_2026_06_01 | 0.15-0.25 | 86 | 5 | 247.77 | -42.13 | -17.0% | 47.7% | 0.732 | 0.194 |
| post_2026_06_01 | >0.25 | 70 | 5 | 192.07 | 7.02 | 3.7% | 55.7% | 0.866 | 0.328 |
| pre_2026_06_01 | 0.10-0.15 | 109 | 14 | 277.42 | 49.65 | 17.9% | 60.6% | 0.605 | 0.124 |
| pre_2026_06_01 | 0.15-0.25 | 209 | 14 | 582.41 | 18.38 | 3.2% | 58.4% | 0.745 | 0.196 |
| pre_2026_06_01 | >0.25 | 112 | 13 | 351.44 | 31.03 | 8.8% | 58.0% | 0.903 | 0.346 |

## 4. side / model / city 交互

side：
| period | side | fills | days | cost_usd | pnl_usd | roi | win_rate | avg_side_p_raw | avg_raw_edge |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | BUY_NO | 172 | 5 | 525.63 | -38.98 | -7.4% | 55.2% | 0.828 | 0.212 |
| post_2026_06_01 | BUY_YES | 105 | 5 | 212.89 | -50.08 | -23.5% | 24.8% | 0.493 | 0.177 |
| pre_2026_06_01 | BUY_NO | 306 | 14 | 935.13 | 38.33 | 4.1% | 66.0% | 0.856 | 0.231 |
| pre_2026_06_01 | BUY_YES | 124 | 14 | 276.15 | 60.73 | 22.0% | 41.1% | 0.491 | 0.182 |

model：
| period | model_version | fills | days | cost_usd | pnl_usd | roi | win_rate | avg_side_p_raw | avg_raw_edge | avg_abs_yes_divergence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | ecmwf | 165 | 5 | 438.09 | -119.33 | -27.2% | 36.4% | 0.689 | 0.184 | 0.177 |
| post_2026_06_01 | gfs | 112 | 5 | 300.43 | 30.27 | 10.1% | 54.5% | 0.719 | 0.220 | 0.209 |
| pre_2026_06_01 | ecmwf | 196 | 13 | 563.52 | 38.80 | 6.9% | 58.7% | 0.723 | 0.194 | 0.240 |
| pre_2026_06_01 | gfs | 234 | 14 | 647.75 | 60.26 | 9.3% | 59.0% | 0.774 | 0.235 | 0.310 |

6 月后最差 city-side-model-edge 组合（样本 >= 3）：
| period | city | side | model_version | raw_edge_bin | fills | pnl_usd | roi | win_rate | avg_side_p_raw |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | BuenosAires | BUY_YES | ecmwf | 0.10-0.15 | 5 | -15.00 | -100.0% | 0.0% | 0.401 |
| post_2026_06_01 | LA | BUY_YES | gfs | >0.25 | 5 | -12.14 | -100.0% | 0.0% | 0.653 |
| post_2026_06_01 | Ankara | BUY_NO | ecmwf | >0.25 | 7 | -11.96 | -61.6% | 14.3% | 0.964 |
| post_2026_06_01 | Warsaw | BUY_YES | ecmwf | 0.10-0.15 | 10 | -10.08 | -59.2% | 20.0% | 0.469 |
| post_2026_06_01 | Moscow | BUY_YES | ecmwf | 0.10-0.15 | 8 | -9.99 | -100.0% | 0.0% | 0.402 |
| post_2026_06_01 | BuenosAires | BUY_NO | ecmwf | 0.15-0.25 | 3 | -9.99 | -100.0% | 0.0% | 0.857 |
| post_2026_06_01 | Munich | BUY_NO | ecmwf | 0.10-0.15 | 5 | -9.91 | -100.0% | 0.0% | 0.673 |
| post_2026_06_01 | Jeddah | BUY_NO | ecmwf | 0.15-0.25 | 8 | -9.76 | -39.6% | 62.5% | 0.868 |
| post_2026_06_01 | NYC | BUY_YES | gfs | 0.15-0.25 | 7 | -9.71 | -100.0% | 0.0% | 0.483 |
| post_2026_06_01 | Munich | BUY_NO | ecmwf | 0.15-0.25 | 7 | -7.43 | -49.6% | 42.9% | 0.796 |
| post_2026_06_01 | Karachi | BUY_NO | ecmwf | 0.10-0.15 | 5 | -7.09 | -48.1% | 20.0% | 0.742 |
| post_2026_06_01 | Guangzhou | BUY_NO | gfs | 0.15-0.25 | 4 | -5.69 | -42.2% | 50.0% | 0.864 |
| post_2026_06_01 | Miami | BUY_NO | gfs | 0.15-0.25 | 4 | -5.29 | -35.5% | 25.0% | 0.787 |
| post_2026_06_01 | Ankara | BUY_YES | ecmwf | 0.10-0.15 | 4 | -5.00 | -100.0% | 0.0% | 0.371 |
| post_2026_06_01 | Jeddah | BUY_YES | ecmwf | 0.10-0.15 | 6 | -5.00 | -100.0% | 0.0% | 0.405 |
| post_2026_06_01 | Jeddah | BUY_YES | ecmwf | >0.25 | 3 | -5.00 | -100.0% | 0.0% | 0.889 |
| post_2026_06_01 | NYC | BUY_NO | gfs | 0.15-0.25 | 5 | -4.15 | -19.5% | 60.0% | 0.805 |
| post_2026_06_01 | NYC | BUY_YES | gfs | 0.10-0.15 | 7 | -3.84 | -27.6% | 14.3% | 0.459 |
| post_2026_06_01 | BuenosAires | BUY_NO | ecmwf | 0.10-0.15 | 6 | -3.60 | -18.0% | 33.3% | 0.798 |
| post_2026_06_01 | London | BUY_YES | ecmwf | 0.10-0.15 | 9 | -3.37 | -18.2% | 33.3% | 0.496 |
| post_2026_06_01 | Singapore | BUY_NO | gfs | 0.10-0.15 | 7 | -2.95 | -29.5% | 28.6% | 0.670 |
| post_2026_06_01 | Moscow | BUY_NO | ecmwf | >0.25 | 5 | -2.93 | -11.9% | 60.0% | 0.952 |
| post_2026_06_01 | Karachi | BUY_NO | ecmwf | 0.15-0.25 | 9 | -2.88 | -14.4% | 66.7% | 0.791 |
| post_2026_06_01 | Istanbul | BUY_YES | ecmwf | 0.10-0.15 | 4 | -2.76 | -27.6% | 25.0% | 0.499 |
| post_2026_06_01 | Istanbul | BUY_NO | ecmwf | 0.10-0.15 | 3 | -2.13 | -22.2% | 33.3% | 0.736 |

## 5. 候选机会宇宙 sanity check

候选表不是实盘 PnL，但可检查 raw edge 在全 eligible 机会里是否也退化。过滤：`eligible=1 AND final_yes IS NOT NULL AND decision_window_missing=0 AND decision_entry_price in [0.25,0.75] AND raw_edge_proxy>=0.10`。

| period | raw_edge_bin | eligible | ordered | live_filled | win_rate | cf_pnl | cf_roi_proxy | avg_side_p_raw | avg_raw_edge_proxy |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | 0.10-0.15 | 33 | 33 | 32 | 60.6% | 25.75 | 147.8% | 0.654 | 0.126 |
| post_2026_06_01 | 0.15-0.25 | 37 | 37 | 33 | 54.1% | -22.55 | -101.3% | 0.801 | 0.199 |
| post_2026_06_01 | >0.25 | 35 | 35 | 28 | 54.3% | -6.94 | -35.2% | 0.891 | 0.328 |
| pre_2026_06_01 | 0.10-0.15 | 60 | 60 | 35 | 51.7% | -25.55 | -76.1% | 0.685 | 0.126 |
| pre_2026_06_01 | 0.15-0.25 | 106 | 102 | 70 | 58.5% | 12.73 | 21.0% | 0.777 | 0.204 |
| pre_2026_06_01 | >0.25 | 89 | 87 | 57 | 59.6% | 38.80 | 79.0% | 0.904 | 0.352 |

## 不停实盘的优化建议

1. **主 live 规则先升 raw edge 门槛**：`raw_edge<=0.25` 转 shadow，`raw_edge>0.25` 保留小 size live。理由：真实成交亏损集中在 `0.10-0.25`，高 raw edge 仍略正；候选表不完全同向，所以先作为风控，不作为永久 alpha 规则。
2. **做 city-side-model 组合黑名单/降权，而不是单变量黑名单**：先降权报告中 6 月后最差的组合；城市单独弱但组合样本不足的只 shadow。理由：避免 Simpson paradox，也避免把可能的实例污染误判为城市 alpha。
3. **ECMWF 不直接全停，但降 size 并要求更高 raw edge**：例如 ECMWF live 要 `raw_edge>0.30`，GFS 可先沿用 `>0.25`。理由：6 月后 ECMWF 是主要拖累，但可能和 city/timing/实例默认参数 mix 纠缠。
4. **保留 blended edge 作为二级风控**：`blended_edge<0.10` 时 shadow；但不要把它当 alpha。理由：它能拦近期边际 raw 信号，但 6 月前会误伤。
5. **立即加一个代码安全审计任务**：对 5/29-5/31 后的 raw live signal JSON 抽样，验证 `model_p_yes`、`market_price`、`edge`、`side`、`strategy_instance`、`entry_price_window` 在 signal、plan、order、fact 四层一致。理由：当前 fact 切片不像简单代码断裂，但 per-side defaults/实例启动脚本确实是可疑变更点。

## 结论级别

- 强机制：真实成交样本里 raw edge 低/中段排序失效。
- 中等机制：ECMWF 与部分 city-side-model 组合在 6 月后显著拖累，但仍需控制 timing 子任务结果。
- 弱/混合证据：candidate universe 只部分支持 raw edge drift，说明执行选择、timing 或实例归属仍可能是共同原因。
- 代码改坏：目前证据不足，但存在明确嫌疑点。fact 中目标实例 `strategy_id`/配置没有 6/1 当天断裂；`127b1a0`/`5dd7fc3` 的 per-side defaults 与 explicit instances 需要 raw live 四层审计才能最终排除。
- 偶然/样本不足：单个城市日和低样本城市不能单独当黑名单理由。
