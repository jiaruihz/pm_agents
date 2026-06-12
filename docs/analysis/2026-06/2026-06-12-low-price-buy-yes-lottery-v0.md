# Low-Price BUY_YES Lottery Sleeve v0

> generated_at_utc: `2026-06-12T18:40:18.085786+00:00`
> target_metric: `low_price_buy_yes_lottery_v0`
> Scope: 本地 counterfactual research only；未改 N100/live 配置。

## 数据快照

- 数据源: `runtime/weather.db.fact_signal_candidates` 主实验；`fact_trades` 只用于强制自检和 CLOB gate 状态。
- DB last_modified_utc: `2026-06-11T16:32:59.272888+00:00`
- MAX(fact_built_at_utc): `2026-06-11T16:32:50.519182+00:00`
- CLOB gate: `gate_pass=False`; fail_reasons=`['cache_fills_missing_or_mismatched_order_id:/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/clob_fills.jsonl', 'cache_fills_exceed_order_cap:/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/clob_fills.jsonl', 'db_cache_fill_id_mismatch', 'db_cache_cost_mismatch', 'db_fills_exceed_order_cap']`
- 因 gate 未通过，本报告不发布 live_real PnL/ROI；所有收益数字均为机会层 `counterfactual_pnl`。
- train: `2026-05-06` -> `2026-05-30` (22 event_dates)
- holdout: `2026-06-01` -> `2026-06-10` (10 event_dates)

### 强制 5 行 SQL 自检

```json
{
  "max_fact_built_at_utc": "2026-06-11T16:32:50.519182+00:00",
  "trade_class_distribution": [
    {
      "trade_class": "paper",
      "n": 2285
    },
    {
      "trade_class": "live_real",
      "n": 856
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
      "n": 4233
    },
    {
      "settlement_status": null,
      "n": 168
    }
  ],
  "candidate_coverage": [
    {
      "rows": 26817,
      "eligible": 9011,
      "paper_ordered": 3387,
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
      "orders": 962,
      "with_fill": 856
    }
  ]
}
```

### Filter Funnel

| step | rows | active_dates | cities | drop | retained_from_full | eligible_rows | note |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fact_signal_candidates rows | 26817 | 39 | 49 | NA | +100.0% | 9011 | full opportunity table |
| BUY_YES rows | 11845 | 39 | 49 | -14972 (-55.8%) | +44.2% | 3948 | side only; no eligible hard gate |
| BUY_YES with decision entry price | 6351 | 38 | 49 | -5494 (-46.4%) | +23.7% | 2091 | decision window price available |
| low-price BUY_YES <0.25 | 5544 | 38 | 49 | -807 (-12.7%) | +20.7% | 1783 | natural lottery universe before settlement |
| settled low-price BUY_YES | 497 | 32 | 48 | -5047 (-91.0%) | +1.9% | 151 | realized outcome available for counterfactual |
| decision-window present | 497 | 32 | 48 | 0 (+0.0%) | +1.9% | 151 | exclude missing decision window |
| counterfactual evaluable | 497 | 32 | 48 | 0 (+0.0%) | +1.9% | 151 | has authorized counterfactual_pnl and cost proxy |

## 目标指标与设计

`low_price_buy_yes_lottery_v0` = 在 full opportunity 分母上，只看 `BUY_YES` 且 `decision_entry_price < 0.25` 的可评价机会，用授权字段 `counterfactual_pnl` / `decision_entry_price` 计算持有到结算的机会层 ROI。`eligible=1` 只作为对照，不作为主实验硬门。

- Baseline: 同 `BUY_YES`、同 `hour_bucket`、同 5c `decision_entry_price` bucket 的 full opportunity。
- Train/holdout: 按 `event_date` chronological 70/30 split。
- Bootstrap: `event_date` cluster bootstrap。
- Raw orderbook: 本轮未额外 join raw orderbook；使用 fact 表中的 decision window price，因此没有新增 `snapshot_ts <= decision_snapshot_ts` join 风险。

## 主结果

| selector | rows | days | cities | cost | pnl | ROI | excess | excess CI | top day removed top1/top3/top5 ROI | worst_day_pnl | gates | verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | --- | --- |
| price_lt_010 | 267 | 18 | 46 | 10.37 | +26.34 | +254.0% | +0.0% | [+0.0%, +0.0%] | +124.2% / -150.9% / -384.2% | -9.27 | NA/NA/FAIL | inconclusive |
| price_010_020 | 148 | 24 | 46 | 21.58 | +4.17 | +19.3% | +0.0% | [+0.0%, +0.0%] | -87.9% / -366.6% / -545.9% | -12.36 | NA/NA/FAIL | inconclusive |
| price_020_025 | 82 | 30 | 31 | 18.19 | -21.86 | -120.2% | +0.0% | [+0.0%, +0.0%] | -298.1% / -429.7% / -543.1% | -11.31 | NA/NA/FAIL | inconclusive |
| old_side_band_lottery_leg | 25 | 15 | 12 | 5.50 | +4.99 | +90.7% | +210.9% | [-378.5%, +874.6%] | -172.9% / -544.5% / -1000.0% | -4.50 | FAIL/FAIL/FAIL | inconclusive |

说明：前三个 price-only selector 的 matched baseline 与 selector 本身同分母，因此 `excess=0`、baseline/significance 标 `NA`；它们用于观察低价 YES 自身的偏度、坏日和集中度。`old_side_band_lottery_leg` 才是相对同价位 full opportunity 的筛选检验。

## Train / Holdout

| selector | train rows | train ROI | train excess CI | holdout rows | holdout ROI | holdout excess CI | holdout worst day |
| --- | ---: | ---: | --- | ---: | ---: | --- | ---: |
| price_lt_010 | 267 | +254.0% | [+0.0%, +0.0%] | 0 | NA | [NA, NA] | NA |
| price_010_020 | 143 | +60.9% | [+0.0%, +0.0%] | 5 | -1000.0% | [+0.0%, +0.0%] | -3.70 |
| price_020_025 | 69 | -221.1% | [+0.0%, +0.0%] | 13 | +438.8% | [+0.0%, +0.0%] | -4.30 |
| old_side_band_lottery_leg | 17 | +46.8% | [-450.1%, +1052.1%] | 8 | +190.5% | [-1657.0%, +1033.5%] | -4.30 |

## Top Removed / Worst Day / Concentration

### price_lt_010

- top day removed ROI: top1 `+124.2%`, top3 `-150.9%`, top5 `-384.2%`.
- top candidate removed ROI: top1 `+158.6%`, top3 `-28.4%`, top5 `-216.7%`.
- worst_day: `2026-05-30` pnl `-9.27`.
- positive PnL concentration: top1 city `+15.6%`, top3 city `+40.7%`, top1 date `+24.0%`, top3 date `+65.7%`, top1 city-date `+8.1%`.

| top city | rows | pnl | ROI |
| --- | ---: | ---: | ---: |
| Shanghai | 10 | +15.39 | +3338.4% |
| Miami | 11 | +15.04 | +3028.2% |
| Singapore | 1 | +9.66 | +27985.5% |
| Guangzhou | 3 | +9.52 | +19833.3% |
| Wellington | 3 | +8.50 | +5644.5% |

| top city-date | rows | pnl | ROI |
| --- | ---: | ---: | ---: |
| Miami 2026-05-13 | 1 | +9.91 | +104263.2% |
| Tokyo 2026-05-15 | 1 | +9.70 | +31786.9% |
| Singapore 2026-05-29 | 1 | +9.66 | +27985.5% |
| LA 2026-05-11 | 2 | +9.53 | +20505.4% |
| Madrid 2026-05-07 | 1 | +9.53 | +20052.6% |

### price_010_020

- top day removed ROI: top1 `-87.9%`, top3 `-366.6%`, top5 `-545.9%`.
- top candidate removed ROI: top1 `-21.3%`, top3 `-103.5%`, top5 `-187.1%`.
- worst_day: `2026-05-15` pnl `-12.36`.
- positive PnL concentration: top1 city `+13.3%`, top3 city `+36.8%`, top1 date `+23.1%`, top3 date `+63.3%`, top1 city-date `+4.8%`.

| top city | rows | pnl | ROI |
| --- | ---: | ---: | ---: |
| Chengdu | 3 | +15.55 | +3494.4% |
| Busan | 3 | +15.02 | +3016.1% |
| Moscow | 5 | +12.60 | +1702.7% |
| Madrid | 4 | +12.55 | +1684.6% |
| NYC | 8 | +9.59 | +921.2% |

| top city-date | rows | pnl | ROI |
| --- | ---: | ---: | ---: |
| Jeddah 2026-05-14 | 1 | +8.75 | +7000.0% |
| Ankara 2026-05-20 | 1 | +8.70 | +6692.3% |
| Helsinki 2026-05-13 | 1 | +8.67 | +6518.8% |
| Chengdu 2026-05-30 | 1 | +8.60 | +6142.9% |
| NYC 2026-05-13 | 1 | +8.59 | +6092.2% |

### price_020_025

- top day removed ROI: top1 `-298.1%`, top3 `-429.7%`, top5 `-543.1%`.
- top candidate removed ROI: top1 `-166.0%`, top3 `-260.4%`, top5 `-358.6%`.
- worst_day: `2026-05-27` pnl `-11.31`.
- positive PnL concentration: top1 city `+21.2%`, top3 city `+49.1%`, top1 date `+36.4%`, top3 date `+57.5%`, top1 city-date `+6.4%`.

| top city | rows | pnl | ROI |
| --- | ---: | ---: | ---: |
| London | 7 | +14.60 | +948.1% |
| Atlanta | 4 | +10.95 | +1209.9% |
| Miami | 10 | +8.35 | +385.7% |
| Wuhan | 1 | +7.70 | +3347.8% |
| Warsaw | 1 | +7.65 | +3255.3% |

| top city-date | rows | pnl | ROI |
| --- | ---: | ---: | ---: |
| Jeddah 2026-05-30 | 1 | +8.00 | +4000.0% |
| Miami 2026-05-15 | 1 | +7.95 | +3878.0% |
| Madrid 2026-06-04 | 1 | +7.95 | +3878.0% |
| London 2026-06-01 | 1 | +7.90 | +3761.9% |
| Atlanta 2026-05-16 | 1 | +7.85 | +3651.2% |

### old_side_band_lottery_leg

- top day removed ROI: top1 `-172.9%`, top3 `-544.5%`, top5 `-1000.0%`.
- top candidate removed ROI: top1 `-55.9%`, top3 `-382.8%`, top5 `-772.5%`.
- worst_day: `2026-05-11` pnl `-4.50`.
- positive PnL concentration: top1 city `+25.2%`, top3 city `+71.1%`, top1 date `+35.1%`, top3 date `+76.0%`, top1 city-date `+17.1%`.

| top city | rows | pnl | ROI |
| --- | ---: | ---: | ---: |
| Madrid | 1 | +7.95 | +3878.0% |
| Warsaw | 1 | +7.65 | +3255.3% |
| Miami | 6 | +6.80 | +515.2% |
| Houston | 2 | +5.30 | +1127.7% |
| London | 3 | +3.80 | +612.9% |

| top city-date | rows | pnl | ROI |
| --- | ---: | ---: | ---: |
| Madrid 2026-06-04 | 1 | +7.95 | +3878.0% |
| London 2026-06-01 | 1 | +7.90 | +3761.9% |
| Miami 2026-05-30 | 1 | +7.75 | +3444.4% |
| Miami 2026-05-26 | 1 | +7.70 | +3347.8% |
| Warsaw 2026-05-30 | 1 | +7.65 | +3255.3% |

## Eligible=1 Control

这段只作为旧 eligible 口径对照；主实验不使用它做硬门。

| selector | eligible rows | ROI | excess | excess CI |
| --- | ---: | ---: | ---: | --- |
| price_lt_010 | 67 | +1070.0% | +0.0% | [+0.0%, +0.0%] |
| price_010_020 | 48 | -12.8% | +0.0% | [+0.0%, +0.0%] |
| price_020_025 | 36 | +399.3% | +0.0% | [+0.0%, +0.0%] |
| old_side_band_lottery_leg | 15 | +540.4% | +141.0% | [-514.7%, +727.8%] |

## 三门判定

| selector | significance | baseline | forward | conclusion |
| --- | --- | --- | --- | --- |
| price_lt_010 | NA | NA | FAIL | inconclusive |
| price_010_020 | NA | NA | FAIL | inconclusive |
| price_020_025 | NA | NA | FAIL | inconclusive |
| old_side_band_lottery_leg | FAIL | FAIL | FAIL | inconclusive |

## 8 环覆盖自检

- 1 描述性绩效切片: covered，机会层 low-price BUY_YES selector。
- 2 统计推断: covered，event_date cluster bootstrap。
- 3 信号判别: partial，仅检验 price/old side-band edge gate，不重训模型。
- 4 概率分布评估: NA，本轮不评估概率校准。
- 5 执行微结构: partial，使用 fact decision price/spread 派生字段；未额外 join raw orderbook。
- 6 容量: NA。
- 7 组合相关性: covered by event_date cluster and city/date concentration。
- 8 基准/反事实: covered，同 side/window/price bucket full opportunity baseline。

## 结论

这个方向目前的意思是：低价 BUY_YES 更像高偏度、日期和城市集中度很强的机会层彩票暴露，而不是已经能从旧 side-band 规则里单独提炼出的可复制 sleeve。<0.10 / 0.10-0.20 / 0.20-0.25 三档 ROI 分别为 `+254.0%`、`+19.3%`、`-120.2%`，但 price-only selector 的 matched baseline 是自身；旧 side-band lottery leg 相对同价位同窗口 baseline 的 overall excess 为 `+210.9%`，95% CI `[-378.5%, +874.6%]`，holdout excess 为 `-248.4%`。

它赚/亏主要来自少数 event_date 和 city-date 命中；top day / top candidate removed 后 ROI 明显回落，其中 `0.20-0.25` 桶 top5 day removed ROI 为 `-543.1%`，old side-band lottery leg top5 day removed ROI 为 `-1000.0%`。

最大问题是样本有效分母从全机会到可评价低价 BUY_YES 掉数很大，且 old side-band lottery leg 的筛选样本更薄，excess CI 跨 0，holdout 也不能把筛选效果和同价位 base-rate 分开。

如果放宽/修正 decision window 覆盖、延长样本、或加入真实可成交 orderbook sleeve 成本，点估计有可能反转；但当前 evidence 更支持把它当作待观测的凸性风险/收益来源，而不是 live 规则。当前动作：仅研究，不允许 live。

## 产物

- JSON: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-06/2026-06-12-low-price-buy-yes-lottery-v0.json`
- Markdown: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-06/2026-06-12-low-price-buy-yes-lottery-v0.md`
