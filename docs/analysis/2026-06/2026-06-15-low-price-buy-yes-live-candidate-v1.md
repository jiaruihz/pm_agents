# Low-Price BUY_YES Live Candidate Search v1

> generated_at_utc: `2026-06-14T17:32:05.489206+00:00`
> target_metric: `low_price_buy_yes_live_candidate_v1`
> Scope: 本地 counterfactual research only；未改 N100/live 配置。

## 数据快照

- 数据源: `runtime/weather.db.fact_signal_candidates` 主实验；`fact_trades` 只用于强制自检和 CLOB gate 状态。
- DB last_modified_utc: `2026-06-14T17:21:22.141278+00:00`
- MAX(fact_built_at_utc): `2026-06-14T17:21:10.519431+00:00`
- CLOB gate: `gate_pass=True`; fail_reasons=`[]`
- 收益数字为机会层 `counterfactual_pnl`；`eligible` / `paper_ordered` / `live_filled` 只作覆盖控制。
- train: `2026-05-06` -> `2026-05-30` (22 event_dates)
- holdout: `2026-06-01` -> `2026-06-10` (10 event_dates)

### 强制 5 行 SQL 自检

```json
{
  "max_fact_built_at_utc": "2026-06-14T17:21:10.519431+00:00",
  "trade_class_distribution": [
    {
      "trade_class": "paper",
      "n": 2285
    },
    {
      "trade_class": "live_real",
      "n": 855
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
      "n": 4250
    },
    {
      "settlement_status": null,
      "n": 150
    }
  ],
  "candidate_coverage": [
    {
      "rows": 29317,
      "eligible": 10032,
      "paper_ordered": 3840,
      "live_filled": 348
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
      "orders": 961,
      "with_fill": 855
    }
  ]
}
```

### Filter Funnel

| step | rows | active_dates | cities | drop | retained_from_full | eligible_rows | note |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fact_signal_candidates rows | 29317 | 42 | 49 | NA | +100.0% | 10032 | full opportunity table |
| BUY_YES rows | 13047 | 42 | 49 | -16270 (-55.5%) | +44.5% | 4418 | side only; no eligible hard gate |
| BUY_YES with decision entry price | 7043 | 41 | 49 | -6004 (-46.0%) | +24.0% | 2364 | decision window price present |
| low-price BUY_YES <0.25 | 6165 | 41 | 49 | -878 (-12.5%) | +21.0% | 2025 | natural low-price universe |
| settled/evaluable low-price BUY_YES | 497 | 32 | 48 | -5668 (-91.9%) | +1.7% | 151 | authorized counterfactual pnl available |

## 目标指标与搜索设计

`low_price_buy_yes_live_candidate_v1` = 在 full opportunity 分母上，从 `BUY_YES` 且 `decision_entry_price < 0.25` 的可评价机会中，寻找可解释、可执行、每 city-date 最多 1 条的 lottery sleeve。主问题不是“低价 YES 是否曾经赚钱”，而是“能否在 train 上选出规则，并在 holdout 仍相对同 side/hour/price bucket baseline 有正超额”。

- 规则网格: `2880` 个预声明组合；train deterministic excess > 0 的规则数 `973`；其中 holdout rows>=20、active_dates>=5 且 excess>0 的 forward screen 规则数 `0`。
- 规则维度: price band、positive edge / edge_mean、yes spread、yes depth、n_snapshots、rank mode；不使用事后赢家城市名单。
- Baseline: 同 `BUY_YES`、同 `hour_bucket`、同 5c `decision_entry_price` bucket 的 full opportunity。
- Train/holdout: 按 `event_date` chronological 70/30 split；搜索只看 train，表中 holdout 是后段日期验证。
- Bootstrap: `event_date` cluster bootstrap；候选数量 K 已报告，未做 Bonferroni 后仍需保守看待。
- 执行现实: 使用 fact 表 decision-window price/spread/depth 字段；没有额外 raw orderbook join。

## 候选结果

| selector | rows | days | cities | cost | pnl | ROI | baseline ROI | excess | excess CI | holdout rows | holdout ROI | holdout excess | top3 day removed ROI | gates sig/base/fwd/stress/exec | verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- | --- |
| `p00_10_e20_em5_sprna_depna_nna_edge_per_price` | 84 | 15 | 37 | 3.76 | +52.41 | +1393.9% | +262.0% | +1131.9% | [+467.4%, +2105.5%] | 0 | NA | NA | +599.2% | PASS/PASS/FAIL/PASS/PASS | inconclusive |
| `p00_10_e20_em0_sprna_depna_nna_cheapest` | 85 | 15 | 37 | 3.77 | +52.35 | +1390.4% | +262.0% | +1128.4% | [+463.0%, +2144.0%] | 0 | NA | NA | +596.4% | PASS/PASS/FAIL/PASS/PASS | inconclusive |
| `p00_10_e20_emna_sprna_depna_n3_cheapest` | 85 | 15 | 37 | 3.77 | +52.35 | +1390.4% | +262.0% | +1128.4% | [+457.1%, +2165.7%] | 0 | NA | NA | +596.4% | PASS/PASS/FAIL/PASS/PASS | inconclusive |
| `p00_10_e20_em0_sprna_depna_n3_edge_per_price` | 85 | 15 | 37 | 3.76 | +52.35 | +1390.4% | +262.0% | +1128.4% | [+454.6%, +2164.4%] | 0 | NA | NA | +596.4% | PASS/PASS/FAIL/PASS/PASS | inconclusive |
| `p00_10_e20_emna_sprna_depna_n3_edge_per_price` | 85 | 15 | 37 | 3.76 | +52.35 | +1390.4% | +262.0% | +1128.4% | [+446.2%, +2131.0%] | 0 | NA | NA | +596.4% | PASS/PASS/FAIL/PASS/PASS | inconclusive |
| `p00_10_e20_em0_sprna_depna_nna_edge_per_price` | 85 | 15 | 37 | 3.76 | +52.35 | +1390.4% | +262.0% | +1128.4% | [+445.7%, +2169.9%] | 0 | NA | NA | +596.4% | PASS/PASS/FAIL/PASS/PASS | inconclusive |
| `p00_10_e20_emna_sprna_depna_nna_edge_per_price` | 85 | 15 | 37 | 3.76 | +52.35 | +1390.4% | +262.0% | +1128.4% | [+442.7%, +2188.8%] | 0 | NA | NA | +596.4% | PASS/PASS/FAIL/PASS/PASS | inconclusive |
| `p00_10_e20_em5_sprna_depna_nna_cheapest` | 84 | 15 | 37 | 3.76 | +52.41 | +1393.9% | +262.0% | +1131.9% | [+439.1%, +2094.3%] | 0 | NA | NA | +599.2% | PASS/PASS/FAIL/PASS/PASS | inconclusive |
| `p00_10_e20_em5_sprna_depna_n3_cheapest` | 84 | 15 | 37 | 3.76 | +52.41 | +1393.9% | +262.0% | +1131.9% | [+438.7%, +2170.0%] | 0 | NA | NA | +599.2% | PASS/PASS/FAIL/PASS/PASS | inconclusive |
| `p00_10_e20_emna_sprna_depna_nna_cheapest` | 85 | 15 | 37 | 3.77 | +52.35 | +1390.4% | +262.0% | +1128.4% | [+436.7%, +2066.4%] | 0 | NA | NA | +596.4% | PASS/PASS/FAIL/PASS/PASS | inconclusive |
| `p00_10_e20_em0_sprna_depna_n3_cheapest` | 85 | 15 | 37 | 3.77 | +52.35 | +1390.4% | +262.0% | +1128.4% | [+432.0%, +2021.6%] | 0 | NA | NA | +596.4% | PASS/PASS/FAIL/PASS/PASS | inconclusive |
| `p00_10_e20_em5_sprna_depna_n3_edge_per_price` | 84 | 15 | 37 | 3.76 | +52.41 | +1393.9% | +262.0% | +1131.9% | [+410.6%, +2258.6%] | 0 | NA | NA | +599.2% | PASS/PASS/FAIL/PASS/PASS | inconclusive |

## 最优候选详情

- selector: `p00_10_e20_em5_sprna_depna_nna_edge_per_price`
- rule: 0.00<=price<0.10, rank=edge_per_price, edge>=0.20, edge_mean>=0.05, one_per_city_date
- train: rows `84`, ROI `+1393.9%`, excess `+1131.9%`, CI `[+445.6%, +2110.0%]`
- holdout: rows `0`, ROI `NA`, excess `NA`, CI `[NA, NA]`
- stress: top1/top3/top5 day removed ROI `+983.8%` / `+599.2%` / `+235.1%`
- execution fields: avg yes_spread `0.0132`, median yes_depth_ask_5c `315.1`

| top city | rows | pnl | ROI |
| --- | ---: | ---: | ---: |
| Shanghai | 4 | +17.69 | +7639.3% |
| Singapore | 1 | +9.66 | +27985.5% |
| Wellington | 1 | +9.35 | +14384.6% |
| Madrid | 2 | +9.08 | +9810.8% |
| Busan | 2 | +8.98 | +8756.1% |

| worst city | rows | pnl | ROI |
| --- | ---: | ---: | ---: |
| Tokyo | 7 | -3.25 | -1000.0% |
| Paris | 5 | -2.86 | -1000.0% |
| Seoul | 5 | -2.32 | -1000.0% |
| Beijing | 5 | -2.18 | -1000.0% |
| London | 7 | -1.96 | -1000.0% |

## 三门判定

| item | significance | baseline | forward | stress | execution | conclusion |
| --- | --- | --- | --- | --- | --- | --- |
| best_candidate | PASS | PASS | FAIL | PASS | PASS | inconclusive |

## 8 环覆盖自检

- 1 描述性绩效切片: covered，机会层 low-price BUY_YES selector。
- 2 统计推断: covered，event_date cluster bootstrap；候选数量 K 已报告但未做严格多重检验修正。
- 3 信号判别: partial，用 edge / edge_mean / market microstructure 标签做筛选，不重训模型。
- 4 概率分布评估: NA，本轮不评估概率校准。
- 5 执行微结构: partial，使用 fact decision-window price/spread/depth；未额外 join raw orderbook。
- 6 容量: partial，要求每 city-date 最多 1 条并报告 depth；未做真实下单容量仿真。
- 7 组合相关性: covered by event_date cluster and top-day stress。
- 8 基准/反事实: covered，同 side/hour/price bucket full opportunity baseline。

## 结论

在 `2026-05-06` 到 `2026-06-10`，低价 BUY_YES 最优入围候选 `p00_10_e20_em5_sprna_depna_nna_edge_per_price` 相对同价位/同窗口 baseline 的超额 ROI 为 `+1131.9%`（95% CI `[+467.4%, +2105.5%]`），前瞻 `FAIL`，结论等级 `inconclusive`。

当前动作：不允许 live。若继续这条线，下一步不是再扩大同类网格，而是补更长 forward shadow 或把低价 YES 作为 convexity tag 叠到更强的上游 forecast/source-quality 规则上。

## 产物

- JSON: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-06/2026-06-15-low-price-buy-yes-live-candidate-v1.json`
- Markdown: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-06/2026-06-15-low-price-buy-yes-live-candidate-v1.md`
