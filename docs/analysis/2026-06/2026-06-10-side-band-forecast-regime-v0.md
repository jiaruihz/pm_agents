# Side Band + Forecast Regime Clean Test v0

> generated_at_utc: `2026-06-09T17:54:29.683771+00:00`
> target_metric: `side_band_forecast_regime_alpha`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: local counterfactual research only; no N100/live config changed; no live action.

## 数据快照

- 数据源：`runtime/weather.db.fact_signal_candidates` 是主实验唯一机会粒度来源；`fact_trades` 只用于强制自检和旧 live 实例诊断。
- DB last_modified：`2026-06-09T17:38:04.156943+00:00`。
- fact_signal_candidates rows：`25117`；fact built：`2026-06-09T17:38:03.570994+00:00`。
- fact_trades rows：`5473`；unsettled/null：`232`；missing_bracket：`0`。
- train：`2026-05-06` -> `2026-05-29`，active dates `23`。
- holdout：`2026-05-30` -> `2026-06-08`，active dates `10`。
- CLOB coverage gate：`gate_ran=True`；`gate_pass=True`。主实验不发布 live_real PnL/ROI。

### 强制 5 行 SQL 自检

```json
{
  "max_fact_built_at_utc": "2026-06-09T17:37:40.513320+00:00",
  "trade_class_distribution": [
    {
      "trade_class": "live_real",
      "n": 1405
    },
    {
      "trade_class": "live_simulated",
      "n": 1147
    },
    {
      "trade_class": "paper",
      "n": 2285
    },
    {
      "trade_class": "snapshot_replay",
      "n": 636
    }
  ],
  "settlement_status_distribution": [
    {
      "settlement_status": null,
      "n": 232
    },
    {
      "settlement_status": "settled",
      "n": 5241
    }
  ],
  "candidate_coverage": {
    "rows": 25117,
    "eligible": 8306,
    "paper_ordered": 3139,
    "live_filled": 554
  },
  "order_fill_coverage": [
    {
      "status": "error",
      "orders": 151,
      "with_fill": 0
    },
    {
      "status": "submitted",
      "orders": 1635,
      "with_fill": 1405
    }
  ]
}
```

## 目标指标与分母

`side_band_forecast_regime_alpha` = 在 `city + event_date + decision_snapshot_ts_utc` 的机会粒度上，先用 full opportunity 复现/推广 side-band 入场价带，再用 forecast quality regime 分层；每个候选规则的 ROI 都减去同 side、同 price band、同 decision-hour bucket、同 liquidity/eligible 档的 baseline ROI。

- 主分母：`fact_signal_candidates` 中 `settlement_status='settled'`、`decision_window_missing=0`、有 `decision_snapshot_ts_utc/model_p_yes/market_yes_price/decision_entry_price/counterfactual_pnl/final_yes` 的机会行。
- `eligible` 不作为硬门；grid 同时报告 `no_filter` 和 `eligible_control`。
- ROI 口径：从 `counterfactual_pnl=(payoff-entry)*shares` 反推 decision shares 后，用 `SUM(counterfactual_pnl) / SUM(decision_entry_price * inferred_shares)`；这是机会层 cost-proxy ROI，不是钱包 PnL。
- baseline：同价位/同窗口/同 side universe，不加 edge threshold 和 forecast regime。

## Filter Funnel

| step | rows | drop vs previous |
| --- | --- | --- |
| fact_signal_candidates rows | 25117 | NA |
| settled + decision_window present rows | 2277 | +90.9% |
| recognizable market/model/price/side rows | 2276 | +0.0% |
| side_band candidate rows | 1587 | +30.3% |
| forecast regime layered side_band rows | 1587 | +0.0% |
| executable/spread-available side_band rows | 1082 | +31.8% |
| train rows from recognizable analysis rows | 1813 (23 active dates) | date split, not a filter |
| holdout rows from recognizable analysis rows | 463 (10 active dates) | date split, not a filter |

任何一步掉超过 70% 的解释：本轮最大掉点写在 JSON `filter_funnel_drop_notes`；主要来自 settlement + decision-window 可用性，不是因为旧 eligible gate。

## Forecast Regime Features

只用 `fact_signal_candidates` 同一 decision snapshot 内的模型/市场分布构造：`model_entropy`、`model_mode_probability`、`adjacent2_mass`、`adjacent3_mass`、`model_tail_mass_outside_adjacent3`、`model_market_l1_gap`、`forecast_source/model_version`，以及只看过去 event_date 的 `city_source_expanding_adjacent3_miss_rate`。

| regime | rows |
| --- | --- |
| low_uncertainty_allowed | 643 |
| medium_uncertainty_price_sensitive | 279 |
| high_uncertainty_no_trade | 783 |
| tail_risk_block | 571 |

## 结论先行

- train 选出的最强规则是：只买 YES，entry price 在 0.20-0.80，T-18-24，模型 edge 至少 0.10，只在中等不确定性且价位合适时交易，不加盘口过滤，不使用旧 eligible 硬门。train excess ROI `+33.2%`，95% CI `[-23.7%, +104.5%]`；holdout excess ROI `-48.6%`，95% CI `[-84.3%, +18.7%]`。
- 三门：significance=`FAIL`，baseline=`FAIL`，forward=`FAIL`，verdict=`inconclusive`。
- 人话结论：旧 side-band 形态在历史上确实有赚过的片段，机制上也像是在过滤低价 YES 彩票票和部分高不确定性 NO，但按 cost-proxy 复核后这个 clean test 仍没确认三门。它现在只能说明“有值得继续观察的 latent edge 线索”，不能说已经确认可复制，更不能推出 live 动作。

## 旧策略复现诊断

这里用 full opportunity 复现旧规则形状，不把它当 live 绩效。旧 live 实例若 gate 不过会抑制 PnL/ROI。

| selector | rows | dates | cost | pnl | ROI | top5 removed ROI | avg entry | avg abs edge |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mid_price_core_v1_25_75 proxy | 976 | 33 | 5625.04 | +154.96 | +2.8% | -2.3% | 0.576 | 0.222 |
| mid_price_core_v1_side_band proxy | 489 | 32 | 2566.43 | +163.56 | +6.4% | -0.0% | 0.525 | 0.252 |

- 旧 side-band proxy 定义：BUY_YES 0.20-0.45 且 abs_edge>=0.20；BUY_NO 0.35-0.65 且 abs_edge>=0.10。
- 旧 25-75 proxy 定义：BUY_YES/BUY_NO 都用 0.25-0.75 且 abs_edge>=0.10。
- 这能复现 mid quote / entry band / side mix 的形状，但不能证明旧参数正确；真实 strategy_instance 归因优先看 `producer_run_id`，不能只用 price window。

## 候选规则详情

### Candidate 1

- 规则解释：只买 YES，entry price 在 0.20-0.80，T-18-24，模型 edge 至少 0.10，只在中等不确定性且价位合适时交易，不加盘口过滤，不使用旧 eligible 硬门。
- baseline：同 side、同 price band、同 decision-hour bucket、同 liquidity/eligible 档，不加 edge threshold 和 forecast regime。

| split | rows | dates | cost | pnl | ROI | ROI CI | baseline ROI | excess ROI | excess CI | top5 removed ROI | worst_day_pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| train | 32 | 16 | 105.05 | +44.95 | +42.8% | [-18.2%, +115.6%] | +9.6% | +33.2% | [-23.7%, +104.5%] | -21.7% | -11.25 |
| holdout | 10 | 8 | 30.60 | -20.60 | -67.3% | [-100.0%, +24.2%] | -18.7% | -48.6% | [-84.3%, +18.7%] | -100.0% | -9.25 |

- 三门：significance=`FAIL`；baseline=`FAIL`；forward=`FAIL`；final verdict=`inconclusive`。

### Candidate 2

- 规则解释：YES/NO 都允许，entry price 在 0.25-0.75，T-18-24，模型 edge 至少 0.05，只在中等不确定性且价位合适时交易，点差 <=8c，只看旧 eligible 对照。
- baseline：同 side、同 price band、同 decision-hour bucket、同 liquidity/eligible 档，不加 edge threshold 和 forecast regime。

| split | rows | dates | cost | pnl | ROI | ROI CI | baseline ROI | excess ROI | excess CI | top5 removed ROI | worst_day_pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| train | 38 | 9 | 200.80 | +69.20 | +34.5% | [+5.3%, +60.8%] | +8.0% | +26.5% | [-6.0%, +52.8%] | -8.9% | -6.15 |
| holdout | 31 | 9 | 162.80 | -52.80 | -32.4% | [-66.3%, +4.7%] | -7.2% | -25.3% | [-58.5%, +11.0%] | -53.6% | -17.05 |

- 三门：significance=`PASS`；baseline=`FAIL`；forward=`FAIL`；final verdict=`inconclusive`。

### Candidate 3

- 规则解释：YES/NO 都允许，entry price 在 0.25-0.75，T-18-24，模型 edge 至少 0.05，只在中等不确定性且价位合适时交易，点差 <=15c，只看旧 eligible 对照。
- baseline：同 side、同 price band、同 decision-hour bucket、同 liquidity/eligible 档，不加 edge threshold 和 forecast regime。

| split | rows | dates | cost | pnl | ROI | ROI CI | baseline ROI | excess ROI | excess CI | top5 removed ROI | worst_day_pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| train | 38 | 9 | 200.80 | +69.20 | +34.5% | [+5.3%, +61.0%] | +8.6% | +25.8% | [-5.3%, +52.6%] | -8.9% | -6.15 |
| holdout | 31 | 9 | 162.80 | -52.80 | -32.4% | [-62.4%, +7.7%] | -7.1% | -25.3% | [-55.3%, +10.0%] | -53.6% | -17.05 |

- 三门：significance=`PASS`；baseline=`FAIL`；forward=`FAIL`；final verdict=`inconclusive`。

### Candidate 4

- 规则解释：只买 YES，entry price 在 0.20-0.80，T-18-24，模型 edge 至少 0.05，只在中等不确定性且价位合适时交易，不加盘口过滤，不使用旧 eligible 硬门。
- baseline：同 side、同 price band、同 decision-hour bucket、同 liquidity/eligible 档，不加 edge threshold 和 forecast regime。

| split | rows | dates | cost | pnl | ROI | ROI CI | baseline ROI | excess ROI | excess CI | top5 removed ROI | worst_day_pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| train | 47 | 21 | 155.56 | +54.44 | +35.0% | [-8.8%, +80.7%] | +9.6% | +25.4% | [-16.9%, +69.0%] | -7.0% | -11.25 |
| holdout | 13 | 9 | 44.80 | -34.80 | -77.7% | [-100.0%, -15.2%] | -18.7% | -59.0% | [-83.3%, -14.6%] | -100.0% | -13.10 |

- 三门：significance=`FAIL`；baseline=`FAIL`；forward=`FAIL`；final verdict=`inconclusive`。

### Candidate 5

- 规则解释：YES/NO 都允许，entry price 在 0.20-0.80，T-18-24，模型 edge 至少 0.05，只在中等不确定性且价位合适时交易，点差 <=15c，只看旧 eligible 对照。
- baseline：同 side、同 price band、同 decision-hour bucket、同 liquidity/eligible 档，不加 edge threshold 和 forecast regime。

| split | rows | dates | cost | pnl | ROI | ROI CI | baseline ROI | excess ROI | excess CI | top5 removed ROI | worst_day_pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| train | 41 | 9 | 224.10 | +75.90 | +33.9% | [+7.9%, +57.6%] | +9.1% | +24.8% | [+0.6%, +46.2%] | -5.7% | -6.15 |
| holdout | 38 | 9 | 205.50 | -55.50 | -27.0% | [-52.7%, +5.7%] | -3.9% | -23.1% | [-50.5%, +9.0%] | -42.2% | -16.85 |

- 三门：significance=`PASS`；baseline=`PASS`；forward=`FAIL`；final verdict=`inconclusive`。

## 总表

| direction | human-readable idea | sample size | holdout result | top5 removed | gates | verdict | next step |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BUY_YES | 只买 YES，entry price 在 0.20-0.80，T-18-24，模型 edge 至少 0.10，只在中等不确定性且价位合适时交易，不加盘口过滤，不使用旧 eligible 硬门 | train 32 / holdout 10 | ROI -67.3%, excess -48.6% | -100.0% | FAIL/FAIL/FAIL | inconclusive | 只保留为 offline/shadow 研究线索；补更长 forward 样本和可成交性复核 |
| both | YES/NO 都允许，entry price 在 0.25-0.75，T-18-24，模型 edge 至少 0.05，只在中等不确定性且价位合适时交易，点差 <=8c，只看旧 eligible 对照 | train 38 / holdout 31 | ROI -32.4%, excess -25.3% | -53.6% | PASS/FAIL/FAIL | inconclusive | 只保留为 offline/shadow 研究线索；补更长 forward 样本和可成交性复核 |
| both | YES/NO 都允许，entry price 在 0.25-0.75，T-18-24，模型 edge 至少 0.05，只在中等不确定性且价位合适时交易，点差 <=15c，只看旧 eligible 对照 | train 38 / holdout 31 | ROI -32.4%, excess -25.3% | -53.6% | PASS/FAIL/FAIL | inconclusive | 只保留为 offline/shadow 研究线索；补更长 forward 样本和可成交性复核 |
| BUY_YES | 只买 YES，entry price 在 0.20-0.80，T-18-24，模型 edge 至少 0.05，只在中等不确定性且价位合适时交易，不加盘口过滤，不使用旧 eligible 硬门 | train 47 / holdout 13 | ROI -77.7%, excess -59.0% | -100.0% | FAIL/FAIL/FAIL | inconclusive | 只保留为 offline/shadow 研究线索；补更长 forward 样本和可成交性复核 |
| both | YES/NO 都允许，entry price 在 0.20-0.80，T-18-24，模型 edge 至少 0.05，只在中等不确定性且价位合适时交易，点差 <=15c，只看旧 eligible 对照 | train 41 / holdout 38 | ROI -27.0%, excess -23.1% | -42.2% | PASS/PASS/FAIL | inconclusive | 只保留为 offline/shadow 研究线索；补更长 forward 样本和可成交性复核 |
| both | YES/NO 都允许，entry price 在 0.20-0.80，T-18-24，模型 edge 至少 0.05，只在中等不确定性且价位合适时交易，点差 <=8c，只看旧 eligible 对照 | train 41 / holdout 38 | ROI -27.0%, excess -23.5% | -42.2% | PASS/FAIL/FAIL | inconclusive | 只保留为 offline/shadow 研究线索；补更长 forward 样本和可成交性复核 |
| both | YES/NO 都允许，entry price 在 0.25-0.75，T-18-24，模型 edge 至少 0.03，只在中等不确定性且价位合适时交易，点差 <=8c，只看旧 eligible 对照 | train 41 / holdout 33 | ROI -35.8%, excess -28.6% | -57.0% | PASS/FAIL/FAIL | inconclusive | 只保留为 offline/shadow 研究线索；补更长 forward 样本和可成交性复核 |
| both | YES/NO 都允许，entry price 在 0.25-0.75，T-18-24，模型 edge 至少 0.03，只在中等不确定性且价位合适时交易，点差 <=15c，只看旧 eligible 对照 | train 41 / holdout 33 | ROI -35.8%, excess -28.7% | -57.0% | PASS/PASS/FAIL | inconclusive | 只保留为 offline/shadow 研究线索；补更长 forward 样本和可成交性复核 |
| both | YES/NO 都允许，entry price 在 0.25-0.75，T-18-24，模型 edge 至少 0.10，只在中等不确定性且价位合适时交易，点差 <=8c，只看旧 eligible 对照 | train 30 / holdout 27 | ROI -35.8%, excess -28.6% | -64.8% | PASS/FAIL/FAIL | inconclusive | 只保留为 offline/shadow 研究线索；补更长 forward 样本和可成交性复核 |
| both | YES/NO 都允许，entry price 在 0.20-0.80，T-18-24，模型 edge 至少 0.03，只在中等不确定性且价位合适时交易，点差 <=15c，只看旧 eligible 对照 | train 44 / holdout 41 | ROI -27.9%, excess -24.0% | -43.8% | PASS/PASS/FAIL | inconclusive | 只保留为 offline/shadow 研究线索；补更长 forward 样本和可成交性复核 |

## 三门与 live 结论

- significance：看 train ROI CI 是否全大于 0。
- baseline：看 train excess ROI CI 是否全大于 0。
- forward：holdout 至少 30 行、5 个 event_date、ROI CI 和 excess CI 都全大于 0，且 top5 removed ROI 仍大于 0。
- 本报告没有任何规则三门全过，最终只能 `inconclusive`。
- live：不能改 live，不能调 size，不能改城市池；主实验是本地 counterfactual research。

## 8 环覆盖自检

- 1 描述性绩效切片：covered，但只作为机会层和旧实例诊断。
- 2 统计推断：covered，按 event_date cluster bootstrap。
- 3 信号判别：covered，side/price/edge/regime 相对 baseline。
- 4 概率分布评估：partial，regime 用模型分布形状和 expanding historical miss。
- 5 执行微结构：partial，仅用 fact 表里的 spread 字段做 none/mild/strict；未读取 raw orderbook。
- 6 容量：NA。
- 7 组合相关性：partial，bootstrap 按 event_date 聚类。
- 8 基准/反事实：covered，同价位/同窗口/同 side baseline。
