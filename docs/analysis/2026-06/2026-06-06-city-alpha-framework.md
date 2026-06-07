# 城市 Alpha 评价体系研究：forecast × market × live 兑现

> 2026-06-06 口径勘误：本文生成于 near-binary settlement 修复前，自检里的 `missing_bracket=734` 已过时；涉及 settled live PnL、ROI、win rate、city/side rank 的结论必须重算后再用于交易决策。见 [2026-06-06-live-account-reconcile-near-binary-fix.md](2026-06-06-live-account-reconcile-near-binary-fix.md)。

## 数据快照

| 字段 | 值 |
| --- | --- |
| 数据源 | DB `runtime/weather.db` + `fact_trades` / `fact_signal_candidates` |
| 生成时间 | 2026-06-06T02:39:27+08:00 |
| DB mtime | 2026-06-06T02:31:19+08:00 |
| fact_trades MAX(fact_built_at_utc) | 2026-06-05T18:31:00.166369+00:00 |
| fact_trades 行数 | 4786 |
| fact_signal_candidates 行数 | 21797 |
| target_date 范围 | 2026-05-06..2026-06-06 |
| event_date 范围 | 2026-05-05..2026-06-06 |
| missing_bracket 行数 | 734 |
| unsettled/null 行数 | 299 |
| 同步/刷新 | 2026-06-06 02:30 +0800 已跑 full `sync_weather_remote.sh`；随后 `weather_dashboard_refresh.sh --no-sync` 成功 |

## 数据完整性自检

强制 5 行 SQL 结果：

```text
max_fact_built_at: [('2026-06-05T18:31:00.166369+00:00',)]
trade_class_distribution: [('live_real', 844), ('live_simulated', 1021), ('paper', 2285), ('snapshot_replay', 636)]
settlement_status_distribution: [(None, 299), ('missing_bracket', 734), ('settled', 3753)]
signal_candidate_coverage: [(21797, 6963, 2570, 455)]
clob_order_fill_join: [('error', 151, 0), ('submitted', 953, 844)]
```

CLOB fill sync 日志本轮 `data_incomplete=false`，但仍有大量 `submitted` 订单无 fill；这是未成交/仍 open/待外部确认的订单，不应和已成交 PnL 混算。

## 目标指标

本报告研究的不是单纯 `city ROI rank`，而是 `city_true_alpha_evidence`：一个城市或 city×side 是否同时满足：

- **机会层正 alpha**：`fact_signal_candidates` 中 `eligible=1 AND final_yes IS NOT NULL AND decision_window_missing=0` 的反事实 `counterfactual_pnl` 为正。
- **模型相对市场有信息量**：`brier_delta = market_brier - model_brier` 为正，表示模型概率比盘口隐含概率更接近最终结算。
- **live 可兑现**：`fact_trades.trade_class='live_real' AND settlement_status='settled'` 的 PnL/ROI 不和机会层明显背离。
- **按日稳定**：看 city+target_date 聚合后的正收益天比例、最差日、去掉最佳日后的 PnL，而不是只看 fill 级总 PnL。
- **执行覆盖合理**：live 覆盖率、paper_order_rate、spread/depth 不显示这个城市只能在 paper 里赚钱、live 吃不到。

## 当前事实摘要

### live_real 已结算城市赢家

| city | cohort | fills | days | pnl | ROI | win | pos_day | drop_best_day |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LA | core9 | 53 | 11 | 56.07 | 35.2% | 64.2% | 63.6% | 25.42 |
| Miami | core9 | 52 | 14 | 37.06 | 27.9% | 55.8% | 50.0% | -4.57 |
| London | core9 | 49 | 14 | 33.38 | 18.6% | 61.2% | 64.3% | 14.13 |
| Warsaw | core9 | 42 | 14 | 33.33 | 25.3% | 64.3% | 71.4% | 16.07 |
| Chengdu | new_t1_2026_05_27 | 12 | 6 | 15.91 | 37.5% | 91.7% | 83.3% | 10.76 |
| Shanghai | core9 | 31 | 11 | 12.51 | 11.0% | 61.3% | 54.5% | -13.19 |
| Istanbul | new_t1_2026_05_26 | 16 | 6 | 11.17 | 20.9% | 37.5% | 50.0% | -24.21 |
| Lucknow | new_t1_2026_05_26 | 12 | 2 | 8.05 | 26.2% | 66.7% | 100.0% | 2.29 |
| Tokyo | core9 | 38 | 14 | 7.65 | 6.1% | 68.4% | 57.1% | -0.74 |
| Paris | t2_or_demoted_watch | 25 | 9 | 4.74 | 5.0% | 64.0% | 44.4% | -10.09 |
| Chicago | t2_or_demoted_watch | 3 | 2 | -2.84 | -26.4% | 66.7% | 50.0% | -4.59 |
| Singapore | new_t1_2026_05_27 | 7 | 3 | -4.03 | -14.3% | 57.1% | 33.3% | -11.05 |

### live_real 已结算城市输家

| city | cohort | fills | days | pnl | ROI | win | pos_day | drop_best_day |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| NYC | core9 | 48 | 12 | -32.24 | -24.0% | 39.6% | 41.7% | -43.56 |
| Manila | new_t1_2026_05_27 | 16 | 5 | -26.34 | -50.4% | 31.2% | 20.0% | -26.66 |
| BuenosAires | new_t1_2026_05_27 | 19 | 6 | -25.41 | -38.5% | 31.6% | 50.0% | -33.57 |
| Amsterdam | new_t1_2026_05_27 | 13 | 3 | -20.01 | -46.2% | 30.8% | 33.3% | -33.32 |
| Ankara | new_t1_2026_05_26 | 31 | 8 | -14.81 | -14.5% | 48.4% | 37.5% | -22.64 |
| Moscow | new_t1_2026_05_26 | 26 | 8 | -12.07 | -13.0% | 53.8% | 37.5% | -21.46 |
| Jeddah | new_t1_2026_05_26 | 16 | 8 | -11.27 | -21.7% | 50.0% | 50.0% | -15.16 |
| Beijing | t2_or_demoted_watch | 10 | 6 | -11.11 | -27.9% | 40.0% | 33.3% | -15.00 |
| Guangzhou | new_t1_2026_05_26 | 21 | 8 | -9.91 | -11.0% | 57.1% | 50.0% | -14.83 |
| Seattle | new_t1_2026_05_26 | 5 | 2 | -6.97 | -58.2% | 20.0% | 0.0% | -3.92 |
| Karachi | new_t1_2026_05_26 | 35 | 9 | -6.90 | -5.5% | 51.4% | 44.4% | -32.20 |
| Munich | new_t1_2026_05_27 | 15 | 6 | -6.57 | -11.4% | 46.7% | 16.7% | -21.57 |

### 全机会反事实正 alpha 城市

| city | cohort | n | days | cf_pnl | win | brier_delta | miss_win_rate | live_cov |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Madrid | reentry_side_only | 33 | 17 | 41.85 | 60.6% | 0.02 | 43.8% | 42.4% |
| Warsaw | core9 | 41 | 21 | 40.67 | 61.0% | 0.01 | 42.2% | 48.8% |
| Tokyo | core9 | 36 | 18 | 33.18 | 52.8% | -0.03 | 48.4% | 50.0% |
| NYC | core9 | 56 | 19 | 31.35 | 57.1% | 0.00 | 42.7% | 30.4% |
| London | core9 | 53 | 19 | 31.06 | 56.6% | -0.02 | 40.9% | 45.3% |
| Chengdu | new_t1_2026_05_27 | 31 | 7 | 28.50 | 64.5% | 0.02 | 38.1% | 19.4% |
| Chicago | t2_or_demoted_watch | 14 | 5 | 9.89 | 57.1% | 0.01 | 50.4% | 21.4% |
| Istanbul | new_t1_2026_05_26 | 17 | 8 | 8.00 | 64.7% | -0.07 | 29.7% | 47.1% |
| Miami | core9 | 55 | 19 | 7.37 | 49.1% | -0.04 | 43.5% | 34.5% |
| Singapore | new_t1_2026_05_27 | 10 | 4 | 4.86 | 50.0% | 0.04 | 50.6% | 40.0% |
| Shanghai | core9 | 35 | 15 | 3.50 | 54.3% | -0.03 | 45.9% | 34.3% |
| Austin | t2_or_demoted_watch | 17 | 6 | 0.45 | 52.9% | -0.05 | 51.1% | 58.8% |

### 全机会反事实负 alpha 城市

| city | cohort | n | days | cf_pnl | win | brier_delta | miss_win_rate | live_cov |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Jeddah | new_t1_2026_05_26 | 14 | 9 | -38.10 | 42.9% | -0.09 | 32.0% | 71.4% |
| Paris | t2_or_demoted_watch | 33 | 15 | -21.46 | 45.5% | -0.05 | 40.7% | 45.5% |
| Ankara | new_t1_2026_05_26 | 22 | 9 | -18.53 | 54.5% | -0.08 | 35.5% | 72.7% |
| Manila | new_t1_2026_05_27 | 15 | 7 | -17.55 | 40.0% | -0.05 | 35.6% | 73.3% |
| Beijing | t2_or_demoted_watch | 28 | 10 | -16.64 | 28.6% | -0.14 | 40.5% | 21.4% |
| BuenosAires | new_t1_2026_05_27 | 14 | 6 | -16.10 | 42.9% | -0.07 | 43.1% | 64.3% |
| Moscow | new_t1_2026_05_26 | 25 | 9 | -15.30 | 52.0% | -0.04 | 35.2% | 60.0% |
| Guangzhou | new_t1_2026_05_26 | 11 | 8 | -15.05 | 54.5% | -0.07 | 45.1% | 90.9% |
| Lucknow | new_t1_2026_05_26 | 8 | 5 | -10.96 | 25.0% | -0.31 | 45.2% | 37.5% |
| Karachi | new_t1_2026_05_26 | 25 | 10 | -10.45 | 52.0% | -0.10 | 34.3% | 76.0% |
| Amsterdam | new_t1_2026_05_27 | 11 | 6 | -6.46 | 45.5% | -0.07 | 44.7% | 45.5% |
| Seattle | new_t1_2026_05_26 | 3 | 2 | -4.15 | 33.3% | -0.34 | 47.7% | 100.0% |

### city×side：结构性黑洞和可保留侧

机会层正 alpha 的 city×side：

| city | side | cohort | n | days | cf_pnl | win | brier_delta | live_cov |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| NYC | BUY_NO | core9 | 33 | 17 | 38.80 | 81.8% | -0.00 | 30.3% |
| Warsaw | BUY_YES | core9 | 18 | 16 | 27.83 | 44.4% | 0.01 | 44.4% |
| Madrid | BUY_YES | reentry_side_only | 12 | 10 | 24.05 | 50.0% | 0.01 | 33.3% |
| Tokyo | BUY_NO | core9 | 22 | 17 | 20.45 | 72.7% | -0.06 | 77.3% |
| Chengdu | BUY_YES | new_t1_2026_05_27 | 12 | 3 | 19.32 | 25.0% | 0.04 | 0.0% |
| Shanghai | BUY_NO | core9 | 22 | 13 | 17.84 | 81.8% | -0.01 | 40.9% |
| Madrid | BUY_NO | reentry_side_only | 21 | 15 | 17.80 | 66.7% | 0.03 | 47.6% |
| London | BUY_NO | core9 | 29 | 17 | 16.98 | 75.9% | -0.03 | 55.2% |
| London | BUY_YES | core9 | 24 | 14 | 14.07 | 33.3% | -0.00 | 33.3% |
| Warsaw | BUY_NO | core9 | 23 | 18 | 12.85 | 73.9% | 0.02 | 52.2% |
| Tokyo | BUY_YES | core9 | 14 | 9 | 12.73 | 21.4% | 0.01 | 7.1% |
| Istanbul | BUY_YES | new_t1_2026_05_26 | 6 | 6 | 12.30 | 66.7% | 0.03 | 50.0% |
| Chicago | BUY_NO | t2_or_demoted_watch | 9 | 4 | 11.90 | 77.8% | -0.02 | 33.3% |
| Miami | BUY_YES | core9 | 25 | 16 | 11.72 | 32.0% | -0.02 | 28.0% |

机会层负 alpha 的 city×side：

| city | side | cohort | n | days | cf_pnl | win | brier_delta | live_cov |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Jeddah | BUY_NO | new_t1_2026_05_26 | 13 | 9 | -35.45 | 46.2% | -0.09 | 69.2% |
| Moscow | BUY_YES | new_t1_2026_05_26 | 7 | 5 | -17.30 | 0.0% | -0.09 | 42.9% |
| Guangzhou | BUY_NO | new_t1_2026_05_26 | 11 | 8 | -15.05 | 54.5% | -0.07 | 90.9% |
| Paris | BUY_NO | t2_or_demoted_watch | 27 | 15 | -14.60 | 55.6% | -0.05 | 55.6% |
| Shanghai | BUY_YES | core9 | 13 | 9 | -14.34 | 7.7% | -0.06 | 23.1% |
| Beijing | BUY_NO | t2_or_demoted_watch | 15 | 9 | -14.25 | 46.7% | -0.23 | 33.3% |
| Karachi | BUY_NO | new_t1_2026_05_26 | 19 | 10 | -13.60 | 57.9% | -0.05 | 84.2% |
| Munich | BUY_NO | new_t1_2026_05_27 | 12 | 8 | -11.80 | 50.0% | -0.10 | 66.7% |
| BuenosAires | BUY_NO | new_t1_2026_05_27 | 9 | 6 | -10.60 | 55.6% | -0.06 | 66.7% |
| Manila | BUY_YES | new_t1_2026_05_27 | 5 | 5 | -10.35 | 0.0% | -0.07 | 60.0% |
| Ankara | BUY_NO | new_t1_2026_05_26 | 19 | 9 | -9.53 | 63.2% | -0.09 | 73.7% |
| Singapore | BUY_NO | new_t1_2026_05_27 | 6 | 4 | -8.60 | 50.0% | 0.01 | 66.7% |
| Amsterdam | BUY_NO | new_t1_2026_05_27 | 7 | 5 | -7.46 | 57.1% | -0.07 | 42.9% |
| NYC | BUY_YES | core9 | 23 | 16 | -7.46 | 21.7% | 0.00 | 30.4% |

live_real 已兑现最差 city×side：

| city | side | cohort | fills | days | pnl | ROI | win |
| --- | --- | --- | --- | --- | --- | --- | --- |
| NYC | BUY_YES | core9 | 29 | 9 | -47.72 | -70.6% | 10.3% |
| Manila | BUY_YES | new_t1_2026_05_27 | 6 | 3 | -16.89 | -100.0% | 0.0% |
| BuenosAires | BUY_YES | new_t1_2026_05_27 | 8 | 2 | -14.96 | -59.9% | 25.0% |
| Istanbul | BUY_NO | new_t1_2026_05_26 | 10 | 5 | -12.14 | -41.2% | 30.0% |
| Jeddah | BUY_NO | new_t1_2026_05_26 | 15 | 8 | -11.22 | -21.6% | 53.3% |
| Karachi | BUY_NO | new_t1_2026_05_26 | 29 | 9 | -11.06 | -10.6% | 55.2% |
| Amsterdam | BUY_NO | new_t1_2026_05_27 | 6 | 3 | -10.95 | -58.8% | 33.3% |
| BuenosAires | BUY_NO | new_t1_2026_05_27 | 11 | 6 | -10.45 | -25.4% | 36.4% |
| Guangzhou | BUY_NO | new_t1_2026_05_26 | 21 | 8 | -9.91 | -11.0% | 57.1% |
| Manila | BUY_NO | new_t1_2026_05_27 | 10 | 5 | -9.45 | -26.7% | 50.0% |
| Ankara | BUY_NO | new_t1_2026_05_26 | 26 | 8 | -9.17 | -9.5% | 57.7% |
| Amsterdam | BUY_YES | new_t1_2026_05_27 | 7 | 3 | -9.06 | -36.7% | 28.6% |
| Beijing | BUY_NO | t2_or_demoted_watch | 9 | 5 | -8.24 | -22.3% | 44.4% |
| Seattle | BUY_NO | new_t1_2026_05_26 | 5 | 2 | -6.97 | -58.2% | 20.0% |

## Cohort：为什么 paper 好城市进 live 后会均值回归

| cohort | paper pnl/ROI | live all pnl/ROI | live since 06-01 pnl/ROI |
| --- | --- | --- | --- |
| core9 | 159.73 / 8.9% | 147.75 / 15.1% | 101.34 / 22.3% |
| new_t1_2026_05_26 | 154.59 / 9.2% | -42.71 / -7.6% | -7.79 / -4.0% |
| new_t1_2026_05_27 | 234.64 / 24.3% | -66.44 / -22.9% | -52.57 / -35.7% |
| other | 3.38 / 0.1% | -5.22 / -6.2% | -8.65 / -28.0% |
| t2_or_demoted_watch | -51.51 / -8.3% | -14.40 / -7.4% |  /  |

Promotion 前后对比：

| cohort | promote | paper prior | live after | opp after |
| --- | --- | --- | --- | --- |
| new_t1_2026_05_26 | 2026-05-26 | 185 fills / 230.48 / 23.1% | 162 fills / -42.71 / -7.6% | 125 opp / -104.54 / brier -0.10 |
| new_t1_2026_05_27 | 2026-05-27 | 87 fills / 187.66 / 41.5% | 82 fills / -66.44 / -22.9% | 100 opp / -10.89 / brier -0.03 |

解读：paper 是厚样本先验，但不是 live 可兑现性的证明。新增城市最容易出现三类偏差：

1. **选择偏差**：paper ledger 覆盖全池，live 只成交其中一小片，且成交片段可能偏向更容易 fill、但不一定更有 EV 的腿。
2. **城市/方向混合偏差**：城市总 ROI 为正时，可能只是一侧赚钱；另一侧进入 live 后把 alpha 吃掉。Madrid/Shanghai 的历史说明 city×side 比 city 更可靠。
3. **tail 依赖**：若去掉最佳日/最佳单后 PnL 翻负，说明不是城市稳定 alpha，而是一次温度落点带来的高赔率收益。

## 当前 T1 城市证据矩阵

| city | cohort | live pnl/ROI | opp cf | brier_delta | paper ROI | stability | action |
| --- | --- | --- | --- | --- | --- | --- | --- |
| LA | core9 | 56.07 / 35.2% | 0.23 | -0.05 | -3.9% | pos 63.6%, dropBest 25.42 | keep no-add-size: robust live, weak prior/brier |
| Miami | core9 | 37.06 / 27.9% | 7.37 | -0.04 | 2.4% | pos 50.0%, dropBest -4.57 | shadow until evidence aligns |
| London | core9 | 33.38 / 18.6% | 31.06 | -0.02 | -3.2% | pos 64.3%, dropBest 14.13 | keep no-add-size: robust live, weak prior/brier |
| Warsaw | core9 | 33.33 / 25.3% | 40.67 | 0.01 | 46.7% | pos 71.4%, dropBest 16.07 | keep normal-size: live, opportunity, brier align |
| Chengdu | new_t1_2026_05_27 | 15.91 / 37.5% | 28.50 | 0.02 | 36.1% | pos 83.3%, dropBest 10.76 | keep normal-size: live, opportunity, brier align |
| Shanghai | core9 | 12.51 / 11.0% | 3.50 | -0.03 | -12.6% | pos 54.5%, dropBest -13.19 | shadow until evidence aligns |
| Istanbul | new_t1_2026_05_26 | 11.17 / 20.9% | 8.00 | -0.07 | 16.6% | pos 50.0%, dropBest -24.21 | shadow until evidence aligns |
| Lucknow | new_t1_2026_05_26 | 8.05 / 26.2% | -10.96 | -0.31 | 4.9% | pos 100.0%, dropBest 2.29 | shadow/T2: opportunity and brier negative |
| Tokyo | core9 | 7.65 / 6.1% | 33.18 | -0.03 | 4.2% | pos 57.1%, dropBest -0.74 | shadow until evidence aligns |
| Singapore | new_t1_2026_05_27 | -4.03 / -14.3% | 4.86 | 0.04 | 26.6% | pos 33.3%, dropBest -11.05 | low-size/shadow: good prior, thin live |
| Madrid | reentry_side_only | -5.22 / -6.2% | 41.85 | 0.02 | 3.6% | pos 54.5%, dropBest -20.77 | watch execution: opportunity positive, live negative |
| Munich | new_t1_2026_05_27 | -6.57 / -11.4% | -4.15 | -0.08 | -9.9% | pos 16.7%, dropBest -21.57 | shadow/T2: opportunity and brier negative |
| Karachi | new_t1_2026_05_26 | -6.90 / -5.5% | -10.45 | -0.10 | 1.7% | pos 44.4%, dropBest -32.20 | shadow/T2: opportunity and brier negative |
| Seattle | new_t1_2026_05_26 | -6.97 / -58.2% | -4.15 | -0.34 | 26.2% | pos 0.0%, dropBest -3.92 | shadow until evidence aligns |
| Guangzhou | new_t1_2026_05_26 | -9.91 / -11.0% | -15.05 | -0.07 | 10.8% | pos 50.0%, dropBest -14.83 | shadow/T2: opportunity and brier negative |
| Jeddah | new_t1_2026_05_26 | -11.27 / -21.7% | -38.10 | -0.09 | 5.0% | pos 50.0%, dropBest -15.16 | T2/demote: live and opportunity both negative |
| Moscow | new_t1_2026_05_26 | -12.07 / -13.0% | -15.30 | -0.04 | 5.1% | pos 37.5%, dropBest -21.46 | T2/demote: live and opportunity both negative |
| Ankara | new_t1_2026_05_26 | -14.81 / -14.5% | -18.53 | -0.08 | 13.6% | pos 37.5%, dropBest -22.64 | T2/demote: live and opportunity both negative |
| Manila | new_t1_2026_05_27 | -26.34 / -50.4% | -17.55 | -0.05 | 10.1% | pos 20.0%, dropBest -26.66 | T2/demote: live and opportunity both negative |
| NYC | core9 | -32.24 / -24.0% | 31.35 | 0.00 | 17.1% | pos 41.7%, dropBest -43.56 | watch execution: opportunity positive, live negative |

## T2 / 已降级观察池

| city | cohort | live pnl/ROI | opp cf | brier_delta | paper ROI | action |
| --- | --- | --- | --- | --- | --- | --- |
| Chicago | t2_or_demoted_watch | -2.84 / -26.4% | 9.89 | 0.01 | 10.0% | low-size/shadow: good prior, thin live |
| Austin | t2_or_demoted_watch | -5.18 / -10.2% | 0.45 | -0.05 | -12.5% | watch execution: opportunity positive, live negative |
| Amsterdam | new_t1_2026_05_27 | -20.01 / -46.2% | -6.46 | -0.07 | 50.0% | shadow/T2: opportunity and brier negative |
| BuenosAires | new_t1_2026_05_27 | -25.41 / -38.5% | -16.10 | -0.07 | 27.2% | T2/demote: live and opportunity both negative |
| Beijing | t2_or_demoted_watch | -11.11 / -27.9% | -16.64 | -0.14 | -15.8% | T2/demote: live and opportunity both negative |
| Paris | t2_or_demoted_watch | 4.74 / 5.0% | -21.46 | -0.05 | -9.1% | shadow/T2: opportunity and brier negative |

## 建议的城市评价体系

### Gate 0：数据资格

- city×side 的机会层 `eligible_settled_decision_n >= 8` 且 `active_days >= 4` 才能做方向判断；否则只能 shadow。
- live 层 `settled fills >= 8` 且 `active_days >= 4` 才能推翻 paper 先验；少于这个阈值只作为告警。
- `decision_window_missing_rate_all_seen > 60%` 的城市不允许直接晋升，只能先补 snapshot/盘口窗口。

### Gate 1：先验必须是 city×side，不是 city

- 用 `fact_signal_candidates` 按 `city, side` 排序，先拦掉结构性黑洞侧。
- 整城正、单侧负时，用 `CITY_ALLOWED_SIDES` 处理，不要整城进出。
- 整城负但一侧明显正时，允许 NO-only/YES-only 小 size 观察。

### Gate 2：模型是否真的适用这个城市

- `brier_delta = market_brier - model_brier > 0` 才说明 forecast/model 在该城市相对盘口有信息量。
- `cf_pnl > 0` 但 `brier_delta < 0` 的城市，多半是赔率/尾部收益，不是稳定 forecast alpha，要降权。
- `brier_delta > 0` 但 `cf_pnl < 0` 的城市，说明模型方向有信息但入场价/side/basket 处理错了，适合研究而不是直接 live。

### Gate 3：live 兑现和稳健性

- 先看 `positive_day_rate` 和 `drop_best_day_pnl`；去最佳日后仍为正的城市才算强 alpha。
- 新增城市进入 live 后前 7-14 天只允许低 size 或 shadow；不能因为 paper prior 高就直接同权。
- promotion 后 live 和机会层背离时，先查成交覆盖、价位桶、side mix、forecast jump，而不是立刻判定城市坏。

### Gate 3.5：城市特征进入研究，但不能替代交易证据

城市特征应作为解释变量和分层变量，而不是直接作为晋升规则。建议落成以下可量化特征：

- **天气/预报适配**：`region`、`unit`、主模型 `forecast_source/model_version`、历史 `brier_delta`、未来 PR3 shadow 中的 `forecast_jump_f` 与 `side_flip_count_today`。
- **市场结构**：`avg_yes_spread/no_spread`、`*_depth_ask_5c`、live coverage、价位桶分布；有 alpha 但盘口薄的城市只能 low size。
- **数据完整性**：IEM/WU/pm_history 覆盖、`decision_window_missing_rate`、settlement missing bracket；数据坏的城市先补数，不做交易结论。
- **组合结构**：同一 city-day 多腿是否互相抵消，是否依赖单个高赔率 bracket；这部分应交给 basket shadow，不用单腿 ROI 硬判。

### Gate 4：动作分级

- **Keep / normal size**：机会层正、brier 正、live 正且去最佳日后不翻负。
- **Keep / low size**：机会层和 paper 正，但 live 样本薄或去最佳日后翻负。
- **Side-only**：一侧两层以上为负，另一侧正；用 `CITY_ALLOWED_SIDES`。
- **Shadow only**：paper 正但 live after promotion 负，或 brier 负，或 decision window 缺失高。
- **T2 / demote**：paper、机会层、live 三层至少两层显著负，且不是 1-2 天噪声。

## 结论

当前最应该改变的是治理方法，而不是再按城市总 ROI 扩池：

1. 城市评价粒度必须从 `city` 升级到 `city×side×strategy_instance`，城市总榜只能做索引，不能做 live allowlist。
2. 新城市晋升必须有 paper prior + opportunity alpha + model-vs-market brier 三层证据；live 前 7-14 天按低 size/shadow 验证兑现率。
3. 对均值回归最敏感的不是 ROI，而是 `drop_best_day_pnl`、promotion 后 live PnL、以及 `brier_delta` 是否持续为正。
4. Basket/optimizer 的方向是对的，但现有研究已显示 tail dependence；城市选择应先用 market-anchored probability 和 shadow 双写复核，不应直接 canary。

## 残余风险

- 机会层只覆盖 `decision_window_missing=0` 的样本，本轮总体缺失率仍在 43.8% 左右。
- `paper_ordered` 不是 live 意图，不能把 paper 覆盖率当 live 成交率。
- 本报告以当前本机 DB 为准；生产 city_pools.py 已通过 N100 SSH 只读核实，当前 commit 为 `2458696`。
- 当前工作区已有多份未提交文档/脚本变更，本报告不判断这些变更是否应一起发布。
