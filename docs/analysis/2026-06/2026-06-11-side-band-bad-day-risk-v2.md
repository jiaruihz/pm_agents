# Side Band Bad-Day Risk Filter v2

> generated_at_utc: `2026-06-11T08:33:34.944852+00:00`
> target_metric: `side_band_bad_day_risk_v2`
> Scope: 本地 counterfactual research only；未改 N100/live 配置。

## 数据快照

- 数据源: `runtime/weather.db.fact_signal_candidates` 主实验；`fact_trades` 只用于自检和 CLOB gate。
- DB last_modified_utc: `2026-06-11T05:19:43.132771+00:00`
- MAX(fact_built_at_utc): `2026-06-11T05:19:34.417822+00:00`
- CLOB gate: `gate_pass=False`; fail_reasons=`['cache_fills_missing_or_mismatched_order_id:/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/clob_fills.jsonl', 'cache_fills_exceed_order_cap:/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/clob_fills.jsonl', 'db_cache_fill_id_mismatch', 'db_cache_cost_mismatch', 'db_fills_exceed_order_cap']`
- 因 gate 未通过，本报告禁止发布新的 live_real PnL/ROI；下列表格均为机会层 counterfactual。
- train: `2026-05-06` -> `2026-05-28` (21 event_dates)
- holdout: `2026-05-29` -> `2026-06-09` (9 event_dates)

### 强制 5 行 SQL 自检

```json
{
  "max_fact_built_at_utc": "2026-06-11T05:19:34.417822+00:00",
  "trade_class_distribution": [
    {
      "trade_class": "paper",
      "n": 2285
    },
    {
      "trade_class": "live_real",
      "n": 853
    },
    {
      "trade_class": "snapshot_replay",
      "n": 636
    },
    {
      "trade_class": "live_simulated",
      "n": 624
    }
  ],
  "settlement_status_distribution": [
    {
      "settlement_status": "settled",
      "n": 4182
    },
    {
      "settlement_status": null,
      "n": 216
    }
  ],
  "candidate_coverage": [
    {
      "rows": 26572,
      "eligible": 8903,
      "paper_ordered": 3339,
      "live_filled": 345
    }
  ],
  "order_fill_coverage": [
    {
      "status": "error",
      "orders": 33,
      "with_fill": 0
    },
    {
      "status": "submitted",
      "orders": 962,
      "with_fill": 853
    }
  ]
}
```

### Filter Funnel

| step | rows | active_dates | drop | note |
| --- | --- | --- | --- | --- |
| fact_signal_candidates rows | 26572 | 39 | NA | 全机会候选表，不按 eligible 硬过滤。 |
| settled + decision_window present rows | 2245 | 33 | -91.6% | 只保留可评价 counterfactual 的 settled 决策窗。 |
| 可识别 market/model/price/side rows | 1064 | 30 | -52.6% | 要求 condition/market/model/side/decision_entry_price/counterfactual_pnl 可用。 |
| side_band candidate rows | 590 | 30 | -44.5% | 预注册 grid 任一 price band/hour bucket 的 union。 |
| forecast regime 分层后 rows | 590 | 30 | -0.0% | 已成功 merge distribution features 并分配 regime。 |
| executable orderbook matched rows | 312 | 18 | -47.1% | raw orderbook latest snapshot_ts <= decision_snapshot_ts_utc 且有 best ask。 |
| train rows | 449 | 21 | NA | 按 event_date chronological 70% split；不是上一行的过滤子集。 |
| holdout rows | 141 | 9 | NA | holdout 不参与调参；不是上一行的过滤子集。 |

掉数 >70% 说明:

- `settled + decision_window present rows` drop >70%: 只保留可评价 counterfactual 的 settled 决策窗。

### Orderbook Coverage

```json
{
  "status": "ok",
  "source": "raw orderbook snapshots, latest snapshot_ts_utc <= decision_snapshot_ts_utc",
  "orderbook_glob": "/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/market_data/orderbook_snapshots/*/*.jsonl.gz",
  "orderbook_files_found": 1124,
  "candidate_rows": 1064,
  "matched_candidate_rows": 551,
  "matched_candidate_rate": 0.5178571428571429,
  "scanned_files": 1010,
  "scanned_rows": 1262081,
  "matched_book_rows_seen": 41663
}
```

## 目标指标与设计

`side_band_bad_day_risk_v2` 固定旧 side-band、去低价 YES、BUY_NO 0.35-0.65、BUY_YES 0.25-0.45 四条 selector，不做新参数搜索；只比较预注册 forecast risk filters 是否改善 holdout excess、worst-day 和 top5-removed ROI。

- Baseline: 同 side + T-18-24 + 5c `decision_entry_price` bucket 的 full opportunity。
- Bootstrap: event_date cluster。
- 风控过滤: block high uncertainty、low/medium quality、tail blocked、历史 adjacent3 miss、model-market L1 gap、entropy。

## 结果总表 Top20

| selector | filter | train_excess | train_ci | holdout_rows | holdout_excess | holdout_ci | top5_removed | worst_day | gates | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| buy_no_035_065 | no_filter | +34.0% | [-41.2%, +101.3%] | 36 | +70.9% | [-13.3%, +198.2%] | -214.9% | -5.90 | FAIL/FAIL/FAIL | inconclusive |
| old_side_band_no_low_yes | no_filter | +37.5% | [-42.1%, +119.2%] | 45 | +55.1% | [-73.3%, +245.0%] | -292.4% | -11.80 | FAIL/FAIL/FAIL | inconclusive |
| old_side_band_no_low_yes | entropy_not_top_quartile | +71.3% | [-75.7%, +221.2%] | 33 | +71.7% | [-197.1%, +259.9%] | -316.8% | -12.49 | FAIL/FAIL/FAIL | inconclusive |
| old_side_band_no_low_yes | market_model_l1_not_top_quartile | +25.6% | [-80.8%, +137.9%] | 34 | +26.4% | [-122.9%, +246.6%] | -359.9% | -9.50 | FAIL/FAIL/FAIL | inconclusive |
| old_side_band_proxy | no_filter | +32.6% | [-43.3%, +106.5%] | 52 | +1.9% | [-136.7%, +132.6%] | -547.9% | -9.94 | FAIL/FAIL/FAIL | inconclusive |
| old_side_band_proxy | market_model_l1_not_top_quartile | +22.4% | [-78.8%, +133.8%] | 41 | -6.3% | [-156.7%, +192.9%] | -547.9% | -9.94 | FAIL/FAIL/FAIL | inconclusive |
| buy_no_035_065 | entropy_not_top_quartile | +34.2% | [-138.0%, +194.9%] | 24 | +65.9% | [-262.6%, +247.2%] | -550.6% | -7.20 | FAIL/FAIL/FAIL | inconclusive |
| old_side_band_proxy | entropy_not_top_quartile | +51.4% | [-95.0%, +184.1%] | 40 | +38.2% | [-340.4%, +205.6%] | -742.2% | -14.49 | FAIL/FAIL/FAIL | inconclusive |
| old_side_band_no_low_yes | hist_miss_below_train_median | +90.3% | [-54.9%, +255.6%] | 34 | +249.9% | [-72.3%, +418.9%] | -1000.0% | -5.05 | FAIL/FAIL/FAIL | inconclusive |
| old_side_band_proxy | hist_miss_below_train_median | +85.8% | [-42.4%, +243.3%] | 38 | +189.6% | [-253.3%, +335.3%] | -1000.0% | -7.10 | FAIL/FAIL/FAIL | inconclusive |
| buy_no_035_065 | market_model_l1_not_top_quartile | +4.7% | [-96.2%, +108.1%] | 26 | +62.6% | [-58.2%, +236.5%] | -1000.0% | -5.05 | FAIL/FAIL/FAIL | inconclusive |
| buy_no_035_065 | block_high_uncertainty | +127.9% | [-545.9%, +650.3%] | 6 | +516.0% | [-556.7%, +980.5%] | NA | -5.25 | FAIL/FAIL/FAIL | inconclusive |
| buy_no_035_065 | low_or_medium_quality | +127.9% | [-489.2%, +667.4%] | 6 | +516.0% | [-423.9%, +947.8%] | NA | -5.25 | FAIL/FAIL/FAIL | inconclusive |
| old_side_band_proxy | block_high_uncertainty | +195.4% | [-336.3%, +616.7%] | 14 | +403.7% | [-766.3%, +842.5%] | NA | -7.94 | FAIL/FAIL/FAIL | inconclusive |
| old_side_band_proxy | low_or_medium_quality | +195.4% | [-394.5%, +596.1%] | 14 | +403.7% | [-669.1%, +844.8%] | NA | -7.94 | FAIL/FAIL/FAIL | inconclusive |
| old_side_band_no_low_yes | block_high_uncertainty | +173.9% | [-333.4%, +601.7%] | 13 | +397.0% | [-456.3%, +838.6%] | NA | -7.94 | FAIL/FAIL/FAIL | inconclusive |
| old_side_band_no_low_yes | low_or_medium_quality | +173.9% | [-356.2%, +596.9%] | 13 | +397.0% | [-428.0%, +839.7%] | NA | -7.94 | FAIL/FAIL/FAIL | inconclusive |
| buy_yes_025_045 | hist_miss_below_train_median | +227.9% | [-445.2%, +695.9%] | 8 | +262.6% | [-34.6%, +783.9%] | NA | -5.90 | FAIL/FAIL/FAIL | inconclusive |
| buy_no_035_065 | hist_miss_below_train_median | +101.8% | [-30.6%, +270.8%] | 26 | +260.3% | [-17.1%, +455.8%] | NA | -5.05 | FAIL/FAIL/FAIL | inconclusive |
| buy_yes_025_045 | no_filter | +401.0% | [-15.5%, +750.2%] | 9 | +111.2% | [-203.3%, +716.0%] | NA | -5.90 | FAIL/FAIL/FAIL | inconclusive |

## BUY_NO 0.35-0.65 日期归因

| bucket | event_date | rows | pnl | roi |
| --- | --- | --- | --- | --- |
| worst | 2026-05-28 | 15 | -29.20 | -327.4% |
| worst | 2026-05-09 | 6 | -11.80 | -371.1% |
| worst | 2026-05-12 | 6 | -10.05 | -334.4% |
| worst | 2026-05-10 | 3 | -6.05 | -376.9% |
| worst | 2026-05-25 | 1 | -6.05 | -1000.0% |
| worst | 2026-05-30 | 8 | -5.90 | -128.5% |
| worst | 2026-05-21 | 1 | -5.30 | -1000.0% |
| worst | 2026-06-05 | 1 | -5.05 | -1000.0% |
| best | 2026-05-11 | 11 | +24.40 | +372.0% |
| best | 2026-05-14 | 15 | +16.65 | +199.8% |
| best | 2026-05-29 | 19 | +12.05 | +111.6% |
| best | 2026-05-26 | 2 | +11.05 | +1234.6% |
| best | 2026-05-13 | 9 | +9.00 | +176.5% |
| best | 2026-06-03 | 2 | +8.50 | +739.1% |
| best | 2026-05-20 | 12 | +5.95 | +92.9% |
| best | 2026-05-22 | 1 | +3.95 | +652.9% |

## 结论

这个方向目前的意思是：固定 side-band / BUY_NO 腿确实还能在部分 filter 下留下正点估计，但没有一个风控过滤同时通过 significance、baseline、forward 三门。它赚/亏主要来自 event_date 集中和 BUY_NO 中高价腿的日期暴露；forecast regime 能改变样本和尾部，但目前更多是在减少机会而不是稳定地产生 alpha。最大问题是 top5 removed 或 holdout CI 仍然不能站稳。当前动作：继续 shadow/paper 记录，不允许 live。 BUY_NO 最好的 holdout top5_removed 版本是 `no_filter`，holdout excess=+70.9%，top5_removed=-214.9%，gates=FAIL/FAIL/FAIL.

## 总表

| direction | human-readable idea | sample size | holdout result | top5 removed | gates | verdict | next step |
| --- | --- | --- | --- | --- | --- | --- | --- |
| buy_no_035_065:no_filter | 固定 selector 加 forecast risk filter，检查坏日子能否被提前挡掉。 | train 119 / holdout 36 | excess +70.9%, CI [-13.3%, +198.2%] | -214.9% | FAIL/FAIL/FAIL | inconclusive | 继续 shadow/paper；不允许 live。 |
| old_side_band_no_low_yes:no_filter | 固定 selector 加 forecast risk filter，检查坏日子能否被提前挡掉。 | train 136 / holdout 45 | excess +55.1%, CI [-73.3%, +245.0%] | -292.4% | FAIL/FAIL/FAIL | inconclusive | 继续 shadow/paper；不允许 live。 |
| old_side_band_no_low_yes:entropy_not_top_quartile | 固定 selector 加 forecast risk filter，检查坏日子能否被提前挡掉。 | train 89 / holdout 33 | excess +71.7%, CI [-197.1%, +259.9%] | -316.8% | FAIL/FAIL/FAIL | inconclusive | 继续 shadow/paper；不允许 live。 |
| old_side_band_no_low_yes:market_model_l1_not_top_quartile | 固定 selector 加 forecast risk filter，检查坏日子能否被提前挡掉。 | train 106 / holdout 34 | excess +26.4%, CI [-122.9%, +246.6%] | -359.9% | FAIL/FAIL/FAIL | inconclusive | 继续 shadow/paper；不允许 live。 |
| old_side_band_proxy:no_filter | 固定 selector 加 forecast risk filter，检查坏日子能否被提前挡掉。 | train 148 / holdout 52 | excess +1.9%, CI [-136.7%, +132.6%] | -547.9% | FAIL/FAIL/FAIL | inconclusive | 继续 shadow/paper；不允许 live。 |
| old_side_band_proxy:market_model_l1_not_top_quartile | 固定 selector 加 forecast risk filter，检查坏日子能否被提前挡掉。 | train 116 / holdout 41 | excess -6.3%, CI [-156.7%, +192.9%] | -547.9% | FAIL/FAIL/FAIL | inconclusive | 继续 shadow/paper；不允许 live。 |
| buy_no_035_065:entropy_not_top_quartile | 固定 selector 加 forecast risk filter，检查坏日子能否被提前挡掉。 | train 73 / holdout 24 | excess +65.9%, CI [-262.6%, +247.2%] | -550.6% | FAIL/FAIL/FAIL | inconclusive | 继续 shadow/paper；不允许 live。 |
| old_side_band_proxy:entropy_not_top_quartile | 固定 selector 加 forecast risk filter，检查坏日子能否被提前挡掉。 | train 101 / holdout 40 | excess +38.2%, CI [-340.4%, +205.6%] | -742.2% | FAIL/FAIL/FAIL | inconclusive | 继续 shadow/paper；不允许 live。 |
| old_side_band_no_low_yes:hist_miss_below_train_median | 固定 selector 加 forecast risk filter，检查坏日子能否被提前挡掉。 | train 93 / holdout 34 | excess +249.9%, CI [-72.3%, +418.9%] | -1000.0% | FAIL/FAIL/FAIL | inconclusive | 继续 shadow/paper；不允许 live。 |
| old_side_band_proxy:hist_miss_below_train_median | 固定 selector 加 forecast risk filter，检查坏日子能否被提前挡掉。 | train 102 / holdout 38 | excess +189.6%, CI [-253.3%, +335.3%] | -1000.0% | FAIL/FAIL/FAIL | inconclusive | 继续 shadow/paper；不允许 live。 |

## 8 环覆盖自检

- 1 描述性绩效切片: covered，机会层 selector + 日期归因。
- 2 统计推断: covered，event_date cluster bootstrap。
- 3 信号判别: covered，固定 selector + risk filter 相对 matched baseline。
- 4 概率分布评估: partial，复用 forecast regime 分布特征。
- 5 执行微结构: partial，只用 decision price/spread 派生字段；gate fail 抑制 live_real。
- 6 容量: NA。
- 7 组合相关性: covered by event_date cluster。
- 8 基准/反事实: covered，同 side/hour/price bucket baseline。

## 产物

- JSON: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-06/2026-06-11-side-band-bad-day-risk-v2.json`
- Markdown: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-06/2026-06-11-side-band-bad-day-risk-v2.md`
