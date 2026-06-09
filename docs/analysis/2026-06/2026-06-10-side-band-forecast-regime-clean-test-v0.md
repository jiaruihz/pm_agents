# Side Band + Forecast Regime Clean Test v0

> generated_at_utc: `2026-06-09T17:47:15.416573+00:00`
> target_metric: `side_band_forecast_regime_alpha`
> Scope: 本地 counterfactual research only；未改 N100/live 配置，未改 city_pools / paper_policy / execution_policy。

## 数据快照

- 数据源: `runtime/weather.db.fact_signal_candidates` 主实验；`fact_trades` 仅历史诊断。
- DB last_modified_utc: `2026-06-09T17:38:04.156943+00:00`
- MAX(fact_built_at_utc): `2026-06-09T17:37:40.513320+00:00`
- CLOB gate: `gate_pass=True`; `missing_order_rows=0`; `over_order_keys=0`; `db_fill_cost_minus_fact_cost=0.0`
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

## Target Metric / 分母

`side_band_forecast_regime_alpha` = 在 `city + event_date + decision_snapshot_ts_utc` 的机会粒度上，使用 `fact_signal_candidates.counterfactual_pnl`，检查预注册 side/price/hour/edge/liquidity/eligible-control grid 加 forecast regime 后，相对同 side、同价位、同窗口 baseline 的 excess ROI。

- 主分母: full opportunity，不把旧单腿 `eligible` 当硬门；`eligible_control` 只作为 grid 对照。
- 价格: `decision_entry_price`，即所选 side 的决策窗入场价，用来识别低价 BUY_YES 彩票票。
- PnL: `counterfactual_pnl`，不是成交 PnL；`counterfactual_pnl_best` 未用于主实验。
- Baseline: 同 side + price band + hour bucket + edge threshold + liquidity + eligible filter，但不加 forecast regime overlay。
- Bootstrap: event_date cluster bootstrap。

## Filter Funnel

| step | rows | active_dates | drop | note |
| --- | --- | --- | --- | --- |
| fact_signal_candidates rows | 25117 | 37 | NA | 全机会候选表，不按 eligible 硬过滤。 |
| settled + decision_window present rows | 2277 | 33 | 90.9% | 只保留可评价 counterfactual 的 settled 决策窗。 |
| 可识别 market/model/price/side rows | 1092 | 30 | 52.0% | 要求 condition/market/model/side/decision_entry_price/counterfactual_pnl 可用。 |
| side_band candidate rows | 612 | 30 | 44.0% | 预注册 grid 任一 price band/hour bucket 的 union。 |
| forecast regime 分层后 rows | 612 | 30 | 0.0% | 已成功 merge distribution features 并分配 regime。 |
| executable orderbook matched rows | 334 | 18 | 45.4% | raw orderbook latest snapshot_ts <= decision_snapshot_ts_utc 且有 best ask。 |
| train rows | 456 | 21 | NA | 按 event_date chronological 70% split；不是上一行的过滤子集。 |
| holdout rows | 156 | 9 | NA | holdout 不参与调参；不是上一行的过滤子集。 |

任何一步掉超过 70% 的解释：

- `settled + decision_window present rows` drop >70%: 只保留可评价 counterfactual 的 settled 决策窗。

## 旧策略复现诊断

这段只用 `fact_trades live_real` 做历史诊断；因为主问题是机会 alpha，不能用 filled sample 替代主实验。

| strategy_instance | fills | settled | dates | roi | pnl | open_cost |
| --- | --- | --- | --- | --- | --- | --- |
| legacy_mid_price_core_v1_25_75 | 462 | 437 | 16 | +9.0% | +110.82 | +81.60 |
| legacy_mid_price_core_v1_side_band_window | 4 | 4 | 1 | +72.5% | +7.24 | +0.00 |
| legacy_mid_price_core_v2_25_75 | 89 | 89 | 4 | -10.7% | -22.52 | +0.00 |
| mid_price_core_v1_25_75 | 482 | 442 | 10 | -10.2% | -116.39 | +113.40 |
| mid_price_core_v1_side_band | 145 | 138 | 10 | +3.2% | +10.77 | +23.74 |
| mid_price_core_v2_25_75 | 88 | 88 | 6 | -24.2% | -53.62 | +0.00 |

拆解结论：

- mid quote 公式效果：当前 fact opportunity 表没有 materialized 的 quote formula variant，不能单独归因；只能在 live_real instance 里做历史诊断。
- entry price band 效果：用 `decision_entry_price` grid 单独评估。
- side mix 效果：grid 同时列 BUY_YES、BUY_NO、both。
- 过滤低价 BUY_YES 彩票票效果：`low_price_buy_yes_rows` 和 direction summary 单独列。
- city/date regime 效果：通过 event_date cluster bootstrap、worst_day_pnl、top5 removed ROI 控制样本运气。

## Train 选出的候选规则，Holdout 冻结复核

- grid_rules_tested: `5760`
- eligible_rules_for_selection: `336`
- multiple testing: No Bonferroni/FDR correction applied to the displayed top train rules; this is why passing train alone is not a live action.

| idea | train_roi | train_excess | train_ci | train_dates | holdout_roi | holdout_excess | holdout_ci | holdout_dates | top5_removed | worst_day_pnl | ob_rows | gates | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 全机会里，买双边，入场价0.20-0.80，T-18-24，abs_edge>=0.08，不过滤流动性，只要中等不确定性且价格敏感区。 | +268.5% | +243.4% | [-209.3%, +617.8%] | 14 | -107.1% | -154.0% | [-862.6%, +732.6%] | 3 | NA | -11.40 | 8 | FAIL/FAIL/FAIL | inconclusive |
| 全机会里，买双边，入场价0.20-0.80，T-18-24，abs_edge>=0.03，不过滤流动性，只要低不确定性 forecast。 | +161.9% | +146.4% | [-113.8%, +500.2%] | 10 | +177.5% | +160.1% | [-278.8%, +465.4%] | 4 | NA | -5.95 | 24 | FAIL/FAIL/PASS | inconclusive |
| 全机会里，买双边，入场价0.20-0.80，T-18-24，abs_edge>=0.05，不过滤流动性，只要低不确定性 forecast。 | +157.0% | +134.3% | [-152.5%, +475.1%] | 10 | +177.5% | +148.2% | [-317.5%, +440.7%] | 4 | NA | -5.95 | 24 | FAIL/FAIL/PASS | inconclusive |
| 全机会里，买双边，入场价0.20-0.80，T-18-24，abs_edge>=0.08，不过滤流动性，只要低不确定性 forecast。 | +157.0% | +131.9% | [-143.9%, +511.3%] | 10 | +177.5% | +130.6% | [-244.8%, +456.5%] | 4 | NA | -5.95 | 24 | FAIL/FAIL/PASS | inconclusive |
| 全机会里，买双边，入场价0.20-0.80，T-18-24，abs_edge>=0.10，不过滤流动性，只要低不确定性 forecast。 | +157.0% | +127.4% | [-152.7%, +495.7%] | 10 | +177.5% | +125.7% | [-269.0%, +463.4%] | 4 | NA | -5.95 | 24 | FAIL/FAIL/PASS | inconclusive |
| 全机会里，买双边，入场价0.20-0.80，T-18-24，abs_edge>=0.05，不过滤流动性，只要中等不确定性且价格敏感区。 | +145.6% | +122.8% | [-261.1%, +499.3%] | 14 | -55.9% | -85.1% | [-913.5%, +734.4%] | 3 | NA | -14.40 | 11 | FAIL/FAIL/FAIL | inconclusive |
| 全机会里，买双边，入场价0.20-0.80，T-18-24，abs_edge>=0.03，不过滤流动性，只要中等不确定性且价格敏感区。 | +73.6% | +58.1% | [-285.6%, +401.9%] | 15 | -21.0% | -38.3% | [-502.5%, +732.0%] | 3 | NA | -14.40 | 12 | FAIL/FAIL/FAIL | inconclusive |
| 旧 eligible 对照里，买双边，入场价0.30-0.70，T-18-24，abs_edge>=0.03，不过滤流动性，反向观察高不确定性区。 | +32.0% | +46.3% | [-84.0%, +195.0%] | 12 | -265.5% | -117.0% | [-212.3%, +69.1%] | 9 | -336.5% | -35.30 | 45 | FAIL/FAIL/FAIL | inconclusive |
| 旧 eligible 对照里，买双边，入场价0.25-0.75，T-18-24，abs_edge>=0.03，不过滤流动性，反向观察高不确定性区。 | +152.4% | +39.2% | [-60.9%, +155.7%] | 12 | -149.9% | -73.9% | [-234.9%, +154.6%] | 9 | -209.9% | -24.70 | 62 | FAIL/FAIL/FAIL | inconclusive |
| 旧 eligible 对照里，买双边，入场价0.35-0.65，T-18-24，abs_edge>=0.03，不过滤流动性，反向观察高不确定性区。 | +49.3% | +23.4% | [-139.0%, +231.6%] | 12 | -300.9% | -104.9% | [-214.4%, +143.2%] | 8 | -405.5% | -27.75 | 28 | FAIL/FAIL/FAIL | inconclusive |
| 旧 eligible 对照里，买双边，入场价0.30-0.70，T-18-24，abs_edge>=0.05，不过滤流动性，反向观察高不确定性区。 | +5.2% | +15.1% | [-108.1%, +184.1%] | 12 | -242.4% | -119.9% | [-228.4%, +72.3%] | 9 | -314.0% | -25.40 | 41 | FAIL/FAIL/FAIL | inconclusive |
| 旧 eligible 对照里，买双边，入场价0.25-0.75，T-18-24，abs_edge>=0.05，不过滤流动性，反向观察高不确定性区。 | +130.2% | +14.2% | [-79.2%, +155.4%] | 12 | -125.7% | -70.5% | [-254.9%, +161.4%] | 9 | -181.3% | -14.80 | 58 | FAIL/FAIL/FAIL | inconclusive |
| 全机会里，买BUY_NO，入场价0.35-0.65，T-18-24，abs_edge>=0.03，不过滤流动性，反向观察高不确定性区。 | +25.9% | +6.0% | [-56.3%, +58.0%] | 21 | -177.1% | -64.7% | [-158.1%, +54.5%] | 8 | -510.1% | -29.95 | 40 | FAIL/FAIL/FAIL | inconclusive |
| 旧 eligible 对照里，买BUY_NO，入场价0.25-0.75，T-18-24，abs_edge>=0.03，不过滤流动性，反向观察高不确定性区。 | +91.2% | +5.4% | [-99.4%, +97.5%] | 12 | -113.1% | -51.3% | [-132.0%, +129.8%] | 9 | -191.9% | -15.00 | 53 | FAIL/FAIL/FAIL | inconclusive |
| 全机会里，买BUY_NO，入场价0.25-0.75，T-18-24，abs_edge>=0.03，不过滤流动性，反向观察高不确定性区。 | -5.8% | +5.0% | [-18.3%, +23.9%] | 21 | -104.3% | -29.0% | [-79.6%, +99.9%] | 9 | -149.8% | -22.15 | 79 | FAIL/FAIL/FAIL | inconclusive |
| 全机会里，买BUY_NO，入场价0.20-0.80，T-18-24，abs_edge>=0.05，温和点差过滤，反向观察高不确定性区。 | -38.9% | +4.6% | [-33.2%, +46.2%] | 8 | -77.9% | -14.5% | [-55.5%, +87.4%] | 9 | -93.7% | -18.60 | 87 | FAIL/FAIL/FAIL | inconclusive |
| 全机会里，买BUY_NO，入场价0.20-0.80，T-18-24，abs_edge>=0.03，温和点差过滤，反向观察高不确定性区。 | -26.5% | +3.5% | [-28.5%, +37.6%] | 8 | -88.1% | -19.5% | [-55.7%, +57.7%] | 9 | -105.3% | -24.45 | 90 | FAIL/FAIL/FAIL | inconclusive |
| 旧 eligible 对照里，买BUY_NO，入场价0.30-0.70，T-18-24，abs_edge>=0.03，不过滤流动性，反向观察高不确定性区。 | -22.3% | +3.3% | [-143.8%, +156.2%] | 11 | -244.4% | -108.4% | [-158.8%, +32.4%] | 9 | -311.7% | -27.70 | 43 | FAIL/FAIL/FAIL | inconclusive |
| 全机会里，买BUY_NO，入场价0.25-0.75，T-18-24，abs_edge>=0.05，不过滤流动性，反向观察高不确定性区。 | +3.5% | +3.1% | [-19.6%, +22.3%] | 21 | -92.7% | -27.4% | [-83.0%, +116.6%] | 9 | -137.5% | -19.75 | 76 | FAIL/FAIL/FAIL | inconclusive |
| 全机会里，买双边，入场价0.25-0.75，T-18-24，abs_edge>=0.03，温和点差过滤，反向观察高不确定性区。 | -3.8% | +2.0% | [-80.9%, +83.2%] | 9 | -93.1% | -52.8% | [-160.1%, +151.6%] | 9 | -238.9% | -29.65 | 88 | FAIL/FAIL/FAIL | inconclusive |

## Forecast Regime Feature 口径

- `model_entropy`, `model_mode_probability`, `adjacent2_mass`, `adjacent3_mass`, `model_tail_mass_outside_adjacent3`, `model_market_l1_gap` 来自同一 decision set 的 bracket 分布。
- `city_source_expanding_adjacent3_miss_rate` 只用过去 event_date，避免未来泄漏。
- Regime 阈值只从 train event_dates 的 quantile 学出，holdout 不参与调参。

## 三门结论

三门定义：significance=train excess ROI CI 下界 > 0；baseline=holdout excess ROI CI 下界 > 0；forward=holdout excess ROI > 0 且 holdout rows/dates 达最低样本门。任一门失败则 `inconclusive`，禁止 live 动作。

这个方向目前的意思是：side_band/entry band 与 forecast regime 的某些组合在 train 上可以筛出正 excess，但 holdout 与 top-day stress 后还不能稳定证明是可复制 latent edge。它赚/亏主要来自 side、入场价区间和日期集中度的共同作用，而不是单一“side_band 参数正确”。最大问题是 holdout excess CI 和可成交 orderbook 覆盖不能同时把不确定性压下去。如果放宽/修正 forecast regime 阈值，有可能改变点估计，但那会变成新实验，不能回填到本次 holdout 调参。当前动作：仅研究，不允许 live。

## 总表

| direction | human-readable idea | sample size | holdout result | top5 removed | gates | verdict | next step |
| --- | --- | --- | --- | --- | --- | --- | --- |
| entry_band_25_75_all | 旧 25-75 入场价带本身是否有帮助。 | train 364 / holdout 127 | ROI -28.8%, pnl -21.04, dates 9 | -198.2% | FAIL/FAIL/FAIL | inconclusive | 仅研究；若要继续，必须预注册下一版规则后重跑。 |
| old_side_band_proxy | 近似旧 side_band：YES 低中价、NO 中高价，按 opportunity 复现。 | train 257 / holdout 84 | ROI +108.2%, pnl +41.01, dates 9 | -245.5% | FAIL/FAIL/FAIL | inconclusive | 仅研究；若要继续，必须预注册下一版规则后重跑。 |
| low_price_buy_yes_lottery | 低价 BUY_YES 彩票票是否拖累。 | train 259 / holdout 89 | ROI +469.1%, pnl +38.32, dates 7 | -450.9% | FAIL/FAIL/FAIL | inconclusive | 仅研究；若要继续，必须预注册下一版规则后重跑。 |
| buy_no_only_25_75 | BUY_NO base-rate 是否解释了收益。 | train 280 / holdout 102 | ROI -49.7%, pnl -32.45, dates 9 | -117.3% | FAIL/FAIL/FAIL | inconclusive | 仅研究；若要继续，必须预注册下一版规则后重跑。 |
| low_uncertainty_overlay | forecast 低不确定性是否提供额外筛选。 | train 52 / holdout 29 | ROI +161.8%, pnl +23.68, dates 4 | NA | FAIL/FAIL/FAIL | inconclusive | 仅研究；若要继续，必须预注册下一版规则后重跑。 |

## 8 环覆盖自检

- 1 描述性绩效切片: covered as live_real diagnostic only。
- 2 统计推断: covered，event_date cluster bootstrap。
- 3 信号判别: covered，side/price/edge/regime grid。
- 4 概率分布评估: partial，使用 fact 表可安全构造的 forecast quality features。
- 5 执行微结构: partial，raw orderbook `snapshot_ts <= decision_snapshot_ts` matched rows 和 taker ROI 只作复核。
- 6 容量: partial，仅 ask best price，不做 size-depth 容量曲线。
- 7 组合相关性: covered by event_date cluster。
- 8 基准/反事实: covered，baseline 是同 side/price/window 的 no-regime baseline。

## 产物

- JSON: `/home/rui/projects/pm_agent/docs/analysis/2026-06/2026-06-10-side-band-forecast-regime-clean-test-v0.json`
- Markdown: `/home/rui/projects/pm_agent/docs/analysis/2026-06/2026-06-10-side-band-forecast-regime-clean-test-v0.md`
