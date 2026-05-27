# 绩效分析：live full research

> 时间窗：2026-05-16 — 2026-05-26（北京时间）  
> 策略：all live / weather_edge_v1  
> 城市池：all（live 实际为 t1_trading，早期缺 city_pool 的 raw order 标为 unknown）  
> 数据源：DB + live raw mirror

## 数据快照

| 项目 | 值 |
|---|---|
| 数据源路径 | runtime/weather.db；runtime/weather_edge_v1/remote_pm_agent/live |
| 数据快照时间 | 2026-05-27T22:09:30；sync 于 2026-05-27 22:08:51 +08:00 |
| fills 行数 | live=494 / paper=1145 / snapshot_replay=636 |
| unsettled 占比 | 191 / 494（38.7%） |
| missing_bracket 数 | 65 |
| live raw submitted orders | 275 submitted；76 not matched to DB fills |

## 总览

| 指标 | 已结算（fill 口径） | 已结算（plan 口径） | 含未结算（mid 估值）[UNSETTLED] |
|---|---:|---:|---:|
| 总 PnL (USD) | 318.32 | 317.18 | N/A（未拉盘口 mid） |
| ROI | 23.6% | N/A | N/A |
| Win rate（by count） | 63.7% | N/A | N/A |
| Win rate（by notional） | 63.5% | N/A | N/A |
| 总 fills 数 | 303 / 494 | 303 / 494 | 494 |
| 总 cost (USD) | 1,349.69 | 1,349.69 | N/A |
| 总 fill_qty (shares) | 2804.37 | 2804.37 | N/A |
| Sharpe-like（daily） | 0.633 | N/A | N/A |

## 切片：by_date

| 日期（北京时间） | fills | wins | win_rate | cost_usd | pnl_usd (fill) | pnl_usd (plan) | roi |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2026-05-16 | 18 | 10 | 55.6% | 77.02 | -7.20 | -7.32 | -9.3% |
| 2026-05-17 | 13 | 11 | 84.6% | 56.04 | 25.01 | 24.93 | 44.6% |
| 2026-05-20 | 39 | 34 | 87.2% | 183.97 | 173.13 | 173.05 | 94.1% |
| 2026-05-21 | 30 | 14 | 46.7% | 136.67 | -15.43 | -15.58 | -11.3% |
| 2026-05-22 | 34 | 26 | 76.5% | 154.52 | 65.36 | 65.11 | 42.3% |
| 2026-05-23 | 30 | 21 | 70.0% | 135.78 | 28.50 | 28.42 | 21.0% |
| 2026-05-24 | 40 | 22 | 55.0% | 166.27 | 28.11 | 28.15 | 16.9% |
| 2026-05-25 | 55 | 33 | 60.0% | 239.19 | 44.98 | 44.86 | 18.8% |
| 2026-05-26 | 44 | 22 | 50.0% | 200.23 | -24.14 | -24.44 | -12.1% |

## 切片：by_city

| city | fills | wins | win_rate | cost_usd | pnl_usd (fill) | pnl_usd (plan) | roi |
|---|---:|---:|---:|---:|---:|---:|---:|
| Madrid | 15 | 3 | 20.0% | 74.05 | -44.59 | -44.74 | -60.2% |
| Beijing | 29 | 17 | 58.6% | 134.80 | -10.20 | -10.27 | -7.6% |
| Chicago | 8 | 5 | 62.5% | 35.76 | -6.62 | -6.66 | -18.5% |
| Paris | 30 | 18 | 60.0% | 143.44 | -1.99 | -2.03 | -1.4% |
| LA | 40 | 20 | 50.0% | 177.34 | -0.55 | -0.89 | -0.3% |
| Austin | 26 | 16 | 61.5% | 115.63 | 0.92 | 0.84 | 0.8% |
| Shanghai | 11 | 10 | 90.9% | 54.66 | 18.53 | 18.53 | 33.9% |
| Miami | 33 | 19 | 57.6% | 138.34 | 29.95 | 29.94 | 21.7% |
| Warsaw | 25 | 18 | 72.0% | 106.10 | 41.30 | 41.12 | 38.9% |
| NYC | 26 | 18 | 69.2% | 107.69 | 57.81 | 57.71 | 53.7% |
| London | 28 | 21 | 75.0% | 126.82 | 66.12 | 66.09 | 52.1% |
| Tokyo | 32 | 28 | 87.5% | 135.06 | 167.64 | 167.55 | 124.1% |

## 切片：by_model

| model | fills | wins | win_rate | cost_usd | pnl_usd (fill) | pnl_usd (plan) | roi |
|---|---:|---:|---:|---:|---:|---:|---:|
| open_meteo_live_ecmwf | 97 | 59 | 60.8% | 441.77 | 52.64 | 52.20 | 11.9% |
| open_meteo_live_gfs | 206 | 134 | 65.0% | 907.92 | 265.69 | 264.98 | 29.3% |

## 切片：by_side

| side | fills | wins | win_rate | cost_usd | avg_fill_price | pnl_usd (fill) | pnl_usd (plan) | roi |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BUY_NO | 219 | 152 | 69.4% | 997.93 | 0.604 | 107.91 | 106.87 | 10.8% |
| BUY_YES | 84 | 41 | 48.8% | 351.75 | 0.305 | 210.41 | 210.31 | 59.8% |

## 切片：by_pool

| pool | fills | wins | win_rate | cost_usd | pnl_usd (fill) | pnl_usd (plan) | roi |
|---|---:|---:|---:|---:|---:|---:|---:|
| t1_trading | 303 | 193 | 63.7% | 1,349.69 | 318.32 | 317.18 | 23.6% |

## Top Winners / Top Losers

**Top 8 winners（by pnl_usd_at_fill）：**

| city | target_date | side | bracket | fill_price | plan_price | qty | settle | pnl_fill | edge |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| Tokyo | 2026-05-20 | BUY_YES | 27+ | 0.255 | 0.255 | 19.61 | 1.000 | 14.61 | 0.509 |
| Tokyo | 2026-05-20 | BUY_YES | 27+ | 0.255 | 0.255 | 19.61 | 1.000 | 14.61 | 0.582 |
| Tokyo | 2026-05-20 | BUY_YES | 27+ | 0.255 | 0.255 | 19.61 | 1.000 | 14.61 | 0.582 |
| Tokyo | 2026-05-20 | BUY_YES | 27+ | 0.255 | 0.255 | 19.61 | 1.000 | 14.61 | 0.582 |
| Tokyo | 2026-05-20 | BUY_YES | 27+ | 0.255 | 0.255 | 19.61 | 1.000 | 14.61 | 0.582 |
| Tokyo | 2026-05-20 | BUY_YES | 27+ | 0.255 | 0.255 | 19.61 | 1.000 | 14.61 | 0.582 |
| Tokyo | 2026-05-20 | BUY_YES | 27+ | 0.255 | 0.255 | 19.61 | 1.000 | 14.61 | 0.582 |
| Tokyo | 2026-05-20 | BUY_YES | 27+ | 0.255 | 0.255 | 19.61 | 1.000 | 14.61 | 0.582 |

**Top 8 losers：**

| city | target_date | side | bracket | fill_price | plan_price | qty | settle | pnl_fill | edge |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| Warsaw | 2026-05-26 | BUY_NO | 27 | 0.710 | 0.710 | 7.04 | 1.000 | -5.00 | 0.133 |
| Austin | 2026-05-23 | BUY_NO | 82-83 | 0.695 | 0.695 | 7.19 | 1.000 | -5.00 | 0.289 |
| Beijing | 2026-05-17 | BUY_NO | 19 | 0.725 | 0.725 | 6.90 | 1.000 | -5.00 | 0.238 |
| Warsaw | 2026-05-20 | BUY_NO | 23 | 0.490 | 0.490 | 10.20 | 1.000 | -5.00 | 0.389 |
| Miami | 2026-05-24 | BUY_NO | 88-89 | 0.490 | 0.490 | 10.20 | 1.000 | -5.00 | 0.417 |
| LA | 2026-05-24 | BUY_YES | 66-67 | 0.490 | 0.490 | 10.20 | 0.000 | -5.00 | 0.161 |
| Tokyo | 2026-05-25 | BUY_NO | 25 | 0.740 | 0.740 | 6.76 | 1.000 | -5.00 | 0.130 |
| LA | 2026-05-25 | BUY_NO | 68-69 | 0.740 | 0.740 | 6.76 | 1.000 | -5.00 | 0.231 |

**集中度 / 重复市场 Top 12（city × target_date × side × bracket）：**

| city | target_date | side | bracket | fills | cost_usd | pnl_usd | avg_price |
|---|---|---|---|---:|---:|---:|---:|
| Tokyo | 2026-05-20 | BUY_YES | 27+ | 8 | 40.00 | 116.86 | 0.255 |
| Miami | 2026-05-25 | BUY_YES | 86-87 | 3 | 15.00 | 42.69 | 0.260 |
| NYC | 2026-05-24 | BUY_YES | 56-57 | 4 | 12.73 | 30.47 | 0.295 |
| LA | 2026-05-25 | BUY_NO | 66-67 | 3 | 15.00 | 24.30 | 0.382 |
| London | 2026-05-26 | BUY_YES | 34 | 2 | 10.00 | 22.00 | 0.312 |
| Madrid | 2026-05-26 | BUY_NO | 32 | 4 | 19.21 | -19.21 | 0.487 |
| Warsaw | 2026-05-26 | BUY_NO | 27 | 4 | 19.02 | -19.02 | 0.702 |
| London | 2026-05-20 | BUY_YES | 20 | 2 | 9.71 | 18.85 | 0.340 |
| LA | 2026-05-25 | BUY_NO | 68-69 | 4 | 18.33 | -18.33 | 0.728 |
| Beijing | 2026-05-25 | BUY_NO | 22 | 4 | 17.83 | -17.83 | 0.641 |
| London | 2026-05-24 | BUY_YES | 30 | 2 | 10.00 | 17.02 | 0.370 |
| Warsaw | 2026-05-22 | BUY_YES | 23 | 2 | 6.70 | 16.92 | 0.284 |

## 数据完整性自检

- [x] fill_row_count 与 DB 匹配：live fills=494。
- [ ] unsettled_pct < 20%：38.7%。
- [ ] missing_bracket：DB 当前 total=65，本报告未逐城市列全量 missing_bracket。
- [x] by_date 行按 target_date 展示；无交易日期不会补空行。

## Paper / Snapshot 预期对比

| baseline | fills | win_rate | cost_usd | pnl_usd | ROI | 说明 |
|---|---:|---:|---:|---:|---:|---|
| live realized | 303 | 63.7% | 1,349.69 | 318.32 | 23.6% | 真实 CLOB matched fills |
| paper overlap t1 | 146 | 61.6% | 800.45 | 99.55 | 12.4% | 同 target_date 窗口，全 T1 paper ledger |
| paper same live cities | 146 | 61.6% | 800.45 | 99.55 | 12.4% | 同窗口，仅 live 已结算城市 |
| snapshot replay overlap | 0 | N/A | 0.00 | 0.00 | N/A | 同窗口 snapshot replay |

## 用户指定诊断：edge / 赔率（非 contract 官方切片）

正式 PnL 归因按 `docs/WEATHER_ANALYSIS_CONTRACT.md` §5 白名单展示。下表用于回答本次问题里的 edge / 赔率形态，不作为 contract 标准绩效切片。PnL 计算采用 `weather_dashboard.metrics.calc._trade_pnl` 与 `settle_t24_paper.py` 的 token-cost 口径：BUY_NO payout 为 `1-final_yes`。

**Live submitted order distribution：**

| edge_bucket | orders | submitted | submit_rate | avg_posted_price | avg_quote_edge |
|---|---:|---:|---:|---:|---:|
| unknown | 145 | 117 | 80.7% | 0.555 | N/A |
| >=20% | 70 | 69 | 98.6% | 0.556 | 0.286 |
| 10-15% | 54 | 41 | 75.9% | 0.546 | 0.123 |
| 15-20% | 46 | 44 | 95.7% | 0.623 | 0.169 |
| 5-10% | 2 | 2 | 100.0% | 0.495 | 0.085 |
| <5% | 2 | 2 | 100.0% | 0.460 | 0.048 |

| price_bucket | orders | submitted | submit_rate | avg_posted_price | avg_quote_edge |
|---|---:|---:|---:|---:|---:|
| 0.55-0.70 | 99 | 99 | 100.0% | 0.638 | 0.195 |
| 0.70-0.75 | 64 | 64 | 100.0% | 0.722 | 0.203 |
| 0.40-0.55 | 52 | 52 | 100.0% | 0.480 | 0.249 |
| 0.25-0.40 | 51 | 51 | 100.0% | 0.299 | 0.186 |
| <0.25 | 47 | 3 | 6.4% | 0.236 | 0.155 |
| >0.75 | 6 | 6 | 100.0% | 0.782 | 0.163 |

**Paper 同窗口 edge / 赔率诊断：**

| edge_bucket | fills | wins | win_rate | cost_usd | pnl_usd | roi |
|---|---:|---:|---:|---:|---:|---:|
| 15-20% | 30 | 14 | 46.7% | 158.80 | -18.80 | -11.8% |
| >=20% | 64 | 41 | 64.1% | 367.70 | 42.30 | 11.5% |
| 10-15% | 31 | 21 | 67.7% | 150.55 | 59.45 | 39.5% |

| price_bucket | fills | wins | win_rate | cost_usd | pnl_usd | roi |
|---|---:|---:|---:|---:|---:|---:|
| 0.70-0.75 | 29 | 21 | 72.4% | 209.75 | 0.25 | 0.1% |
| 0.40-0.55 | 19 | 11 | 57.9% | 92.05 | 17.95 | 19.5% |
| 0.25-0.40 | 34 | 13 | 38.2% | 103.60 | 26.40 | 25.5% |
| 0.55-0.70 | 43 | 31 | 72.1% | 271.65 | 38.35 | 14.1% |

## 观察与建议

1. 交易动作：当前 live 已结算样本 ROI=23.6%，高于同窗口 paper T1 ROI=12.4%，但 live 样本明显小且选择性成交强，不能按比例外推。短期建议保留 live 主路径，但把新增城市按城市级阈值分层，不再只用全池统一阈值。
2. 收益来源：live 当前美元 PnL 主要来自 BUY_YES 的少数高赔率命中；BUY_NO 的胜率更高、交易更多，但 token 成本高时单笔盈利较薄。Top winners/losers 和集中度表显示，Tokyo 2026-05-20 的重复 YES 命中贡献了很大一块收益，因此不能只看总 ROI。
3. Paper 预期：同窗口 paper 是正收益，但 paper 覆盖更多候选和假设成交；live 真实收益受 maker 排队、部分成交和重复去重影响。`still_open_or_unfilled` 较多时，paper 预期应打折，优先用 matched fills 做决策。
4. 城市池：已结算 live 样本数不足 5 的城市不应升降级；样本 >=5 且 ROI>10% 的城市可以维持/加权，样本 >=5 且 ROI<-5% 的城市先降 size 或 shadow，接近零的城市先不扩 size。
5. 城市独立策略：需要。至少应有 city-level 参数层：min_edge、price band、方向开关、max_notional。全池统一策略会把高噪声城市和稳定城市混在一起，paper 已显示城市差异足够大。

**候选分级（基于 live 已结算样本，样本不足只作观察）：**

| tier | cities | rule |
|---|---|---|
| Keep / scale cautiously | Shanghai, Miami, Warsaw, NYC, London, Tokyo | settled>=5 且 ROI>=10% |
| Watch / no scale | Paris, LA, Austin | settled>=5 且 -5%<=ROI<10% |
| Reduce / shadow | Madrid, Beijing, Chicago | settled>=5 且 ROI<-5% |
| Need more data | Ankara, Guangzhou, Istanbul, Jeddah, Karachi, Lucknow, Moscow, Seattle | raw live orders 存在但尚无已结算 fills |
