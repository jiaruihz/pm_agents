# Side Band Mechanism Attribution v1

> generated_at_utc: `2026-06-10T01:36:01.270369+00:00`
> target_metric: `side_band_mechanism_attribution_v1`
> Scope: 本地 counterfactual research only；未改 N100/live 配置。

## 数据快照

- 数据源: `runtime/weather.db.fact_signal_candidates` 主实验；`fact_trades` 只用于自检和 CLOB gate。
- DB last_modified_utc: `2026-06-09T17:38:04.156943+00:00`
- MAX(fact_built_at_utc): `2026-06-09T17:37:40.513320+00:00`
- CLOB gate: `gate_pass=True`; `db_fill_cost_minus_fact_cost=0.0`
- train: `2026-05-06` -> `2026-05-28` (21 event_dates)
- holdout: `2026-05-29` -> `2026-06-07` (9 event_dates)

### 强制 5 行 SQL 自检

```json
{
  "max_fact_built_at_utc": "2026-06-09T17:37:40.513320+00:00",
  "trade_class_distribution": [
    {
      "trade_class": "paper",
      "n": 2285
    },
    {
      "trade_class": "live_real",
      "n": 1405
    },
    {
      "trade_class": "live_simulated",
      "n": 1147
    },
    {
      "trade_class": "snapshot_replay",
      "n": 636
    }
  ],
  "settlement_status_distribution": [
    {
      "settlement_status": "settled",
      "n": 5241
    },
    {
      "settlement_status": null,
      "n": 232
    }
  ],
  "candidate_coverage": [
    {
      "rows": 25117,
      "eligible": 8306,
      "paper_ordered": 3139,
      "live_filled": 554
    }
  ],
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

## 目标指标与设计

`side_band_mechanism_attribution_v1` 固定旧 side-band 形态和几个机制 selector，不再做大规模调参。每个 selector 相对同 side、同 T-18-24、同 5c entry price bucket 的 full-opportunity baseline 算 excess ROI，并按 event_date cluster bootstrap。

- 主分母: `fact_signal_candidates` full opportunity；不把旧 `eligible` 当硬门。
- 价格: `decision_entry_price`。
- PnL: `counterfactual_pnl`，不是成交样本 PnL。
- 重点: 分清 entry band、低价 BUY_YES、BUY_NO base-rate、forecast regime、样本日期运气。

## Selector Results

| direction | idea | train | holdout | top5 | worst_day | gates | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| old_side_band_proxy | 近似旧 side_band：BUY_YES 买 0.20-0.45 且 abs_edge>=0.20，BUY_NO 买 0.35-0.65 且 abs_edge>=0.10。 | rows 149, excess +33.5%, CI [-44.1%, +110.9%] | rows 52, excess +7.4%, CI [-83.1%, +186.3%] | -461.5% | -9.94 | FAIL/FAIL/FAIL | inconclusive |
| old_side_band_no_low_yes | 旧 side_band 但剔除 BUY_YES<0.25 彩票腿，看旧利润是否只是低价 YES 驱动。 | rows 137, excess +38.6%, CI [-44.1%, +120.2%] | rows 46, excess +70.9%, CI [-55.9%, +312.8%] | -300.2% | -17.80 | FAIL/FAIL/FAIL | inconclusive |
| low_price_yes_lottery_leg | 只看 BUY_YES 0.20-0.25 彩票腿，判断旧 side_band 的高回报是否来自少数命中。 | rows 13, excess -320.2%, CI [-1195.1%, +669.1%] | rows 6, excess -603.3%, CI [-2000.3%, +757.8%] | NA | -2.15 | FAIL/FAIL/FAIL | inconclusive |
| yes_mid_leg | BUY_YES 0.25-0.45 中低价腿，剔除最彩票区后看 YES 是否仍有 alpha。 | rows 17, excess +396.0%, CI [-25.4%, +760.8%] | rows 9, excess +155.6%, CI [-231.1%, +842.4%] | NA | -5.90 | FAIL/FAIL/FAIL | inconclusive |
| no_mid_high_leg | BUY_NO 0.35-0.65 中高价腿，检查收益是否只是 BUY_NO base-rate。 | rows 120, excess +36.4%, CI [-36.0%, +100.6%] | rows 37, excess +89.5%, CI [+24.2%, +222.1%] | -229.3% | -11.90 | FAIL/PASS/FAIL | inconclusive |
| entry_25_75_control | 传统 0.25-0.75 入场控制组，帮助分离 entry band 本身。 | rows 287, excess +22.8%, CI [-33.7%, +84.0%] | rows 98, excess +2.3%, CI [-82.0%, +75.9%] | -140.2% | -18.45 | FAIL/FAIL/FAIL | inconclusive |
| old_side_band_low_uncertainty | 旧 side_band 只保留 low_uncertainty_allowed，看 forecast regime 是否提供额外筛选。 | rows 16, excess +296.1%, CI [-502.6%, +1050.6%] | rows 14, excess +372.1%, CI [-531.1%, +647.8%] | NA | -7.94 | FAIL/FAIL/FAIL | inconclusive |
| old_side_band_medium_uncertainty | 旧 side_band 只保留 medium_uncertainty_price_sensitive，复核 clean-test 里 train 好看的区域。 | rows 9, excess +26.1%, CI [-971.1%, +745.8%] | rows 1, excess +0.0%, CI [+0.0%, +0.0%] | NA | -4.35 | FAIL/FAIL/FAIL | inconclusive |
| old_side_band_tail_blocked | 旧 side_band 挡掉 tail_risk_block，测试尾部风险过滤是否能降低样本运气依赖。 | rows 0, excess NA, CI [NA, NA] | rows 0, excess NA, CI [NA, NA] | NA | NA | FAIL/FAIL/FAIL | inconclusive |

## old_side_band_proxy 日期归因

| bucket | event_date | rows | pnl | roi |
| --- | --- | --- | --- | --- |
| worst | 2026-05-28 | 19 | -30.20 | -301.4% |
| worst | 2026-05-12 | 7 | -12.73 | -389.0% |
| worst | 2026-05-25 | 3 | -10.90 | -1000.0% |
| worst | 2026-06-04 | 7 | -9.94 | -332.0% |
| worst | 2026-05-09 | 8 | -7.75 | -205.3% |
| best | 2026-05-29 | 24 | +34.50 | +274.9% |
| best | 2026-05-26 | 6 | +20.85 | +1088.8% |
| best | 2026-05-11 | 13 | +19.90 | +283.9% |
| best | 2026-05-13 | 10 | +14.55 | +262.4% |
| best | 2026-05-14 | 16 | +14.35 | +167.5% |

## 结论

这个方向目前的意思是：旧 side-band 的正收益更像是 price/side 形态叠加少数日期命中的结果，而不是已经可复制的稳定 alpha。它赚/亏主要来自两个不稳定来源：低价 BUY_YES 的凸性会放大少数命中但相对同价位 baseline 不稳，BUY_NO 中高价腿在 holdout 有正 excess 但 train 和 top5 stress 过不了。最大问题是 holdout excess CI 与 top5 removed 后的 ROI 不能同时站住。如果放宽或重训 forecast regime，点估计可能变化，但那必须作为新预注册实验，不能回填本次 holdout。当前动作：仅研究，不允许 live。

## 总表

| direction | human-readable idea | sample size | holdout result | top5 removed | gates | verdict | next step |
| --- | --- | --- | --- | --- | --- | --- | --- |
| old_side_band_proxy | 近似旧 side_band：BUY_YES 买 0.20-0.45 且 abs_edge>=0.20，BUY_NO 买 0.35-0.65 且 abs_edge>=0.10。 | train 149 / holdout 52 | ROI +114.3%, excess +7.4% | -461.5% | FAIL/FAIL/FAIL | inconclusive | 仅研究；需要更长 forward 或新预注册规则。 |
| old_side_band_no_low_yes | 旧 side_band 但剔除 BUY_YES<0.25 彩票腿，看旧利润是否只是低价 YES 驱动。 | train 137 / holdout 46 | ROI +48.7%, excess +70.9% | -300.2% | FAIL/FAIL/FAIL | inconclusive | 仅研究；需要更长 forward 或新预注册规则。 |
| low_price_yes_lottery_leg | 只看 BUY_YES 0.20-0.25 彩票腿，判断旧 side_band 的高回报是否来自少数命中。 | train 13 / holdout 6 | ROI +1325.6%, excess -603.3% | NA | FAIL/FAIL/FAIL | inconclusive | 仅研究；需要更长 forward 或新预注册规则。 |
| yes_mid_leg | BUY_YES 0.25-0.45 中低价腿，剔除最彩票区后看 YES 是否仍有 alpha。 | train 17 / holdout 9 | ROI +452.4%, excess +155.6% | NA | FAIL/FAIL/FAIL | inconclusive | 仅研究；需要更长 forward 或新预注册规则。 |
| no_mid_high_leg | BUY_NO 0.35-0.65 中高价腿，检查收益是否只是 BUY_NO base-rate。 | train 120 / holdout 37 | ROI -4.0%, excess +89.5% | -229.3% | FAIL/PASS/FAIL | inconclusive | 仅研究；需要更长 forward 或新预注册规则。 |
| entry_25_75_control | 传统 0.25-0.75 入场控制组，帮助分离 entry band 本身。 | train 287 / holdout 98 | ROI -14.4%, excess +2.3% | -140.2% | FAIL/FAIL/FAIL | inconclusive | 仅研究；需要更长 forward 或新预注册规则。 |
| old_side_band_low_uncertainty | 旧 side_band 只保留 low_uncertainty_allowed，看 forecast regime 是否提供额外筛选。 | train 16 / holdout 14 | ROI +505.3%, excess +372.1% | NA | FAIL/FAIL/FAIL | inconclusive | 仅研究；需要更长 forward 或新预注册规则。 |
| old_side_band_medium_uncertainty | 旧 side_band 只保留 medium_uncertainty_price_sensitive，复核 clean-test 里 train 好看的区域。 | train 9 / holdout 1 | ROI -1000.0%, excess +0.0% | NA | FAIL/FAIL/FAIL | inconclusive | 仅研究；需要更长 forward 或新预注册规则。 |
| old_side_band_tail_blocked | 旧 side_band 挡掉 tail_risk_block，测试尾部风险过滤是否能降低样本运气依赖。 | train 0 / holdout 0 | ROI NA, excess NA | NA | FAIL/FAIL/FAIL | inconclusive | 仅研究；需要更长 forward 或新预注册规则。 |

## 8 环覆盖自检

- 1 描述性绩效切片: covered，机会层 selector + 旧 side-band 日期归因。
- 2 统计推断: covered，event_date cluster bootstrap。
- 3 信号判别: covered，固定 selector 相对 matched baseline。
- 4 概率分布评估: partial，复用 clean-test forecast regime。
- 5 执行微结构: partial，仅用 fact 表 decision price/spread 派生字段，不发布 executable PnL。
- 6 容量: NA。
- 7 组合相关性: covered by event_date cluster。
- 8 基准/反事实: covered，同 side/hour/price bucket baseline。

## 产物

- JSON: `/home/rui/projects/pm_agent/docs/analysis/2026-06/2026-06-10-side-band-mechanism-attribution-v1.json`
- Markdown: `/home/rui/projects/pm_agent/docs/analysis/2026-06/2026-06-10-side-band-mechanism-attribution-v1.md`
