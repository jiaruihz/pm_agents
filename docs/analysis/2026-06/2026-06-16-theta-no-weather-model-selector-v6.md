# Theta NO Weather Model Selector v6

Status: snapshot
Generated: 2026-06-15T17:14:01.550458+00:00
Target metric: `d1_no_loses` = d1 高 ask NO carry 中，最终官方 winner 刚好落到 d1 bracket，导致 NO 亏满。

## 数据完整性自检

- Evidence layer: orderbook replay / opportunity research；不是 live_real fill PnL。
- d1 quote rows: 2204; train rows: 1052; holdout rows: 1152; active dates: 26.
- IEM ext cache: fetched=0, cached=36；研究目录，不覆盖生产 cache。
- fact_built_at_utc: `2026-06-15T16:31:04.483297+00:00`。
- fact_trades trade_class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`。
- fact_trades settlement_status: `[{'settlement_status': '', 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]`。
- fact_signal_candidates coverage: `{'rows': 30140, 'eligible': 10366, 'paper_ordered': 3968, 'live_filled': 348}`。
- CLOB orders/fills join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`。
- CLOB coverage gate: gate_pass=True, missing_order_rows=0, over_order_keys=0, db_fill_cost_minus_fact_cost=0.0.

## 人话结论

我把 dewpoint/RH/wind/sky 加进来后，模型确实能学到一些 exact d1 hit 风险；但一到交易层，仍然没能变成 live 策略。原因还是同一个：市场 ask 和 current YES 已经吃掉了大部分 no-reheat 信息，模型筛出来的正样本无法同时通过显著性、基准和前瞻。

一句话结论：在 expanded replay 中，weather-enhanced d1 theta-NO selector 相对 current YES 的前瞻超额 ROI 没有稳定显著大于 0，结论等级 `inconclusive`，不允许 live。

## 模型判别力

| model | train/OOS Brier | train/OOS AUC | train base | holdout base |
|---|---:|---:|---:|---:|
| `weather_only` | 0.2094 / 0.2014 | 0.610 / 0.639 | 29.8% | 29.4% |
| `weather_plus_price` | 0.1728 / 0.1640 | 0.752 / 0.775 | 29.8% | 29.4% |

## Train 选出的规则在 holdout 的结果

| model | rank | holdout rows | dates | NO ROI | YES ROI | NO-YES | NO CI95 | Delta CI95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `weather_only` | 1 | 22 | 11 | +12.5% | +17.8% | -5.3% | [+2.3%, +20.5%] | [-10.6%, -0.8%] |
| `weather_only` | 2 | 16 | 11 | +8.5% | +13.6% | -5.1% | [-5.5%, +18.2%] | [-13.7%, -0.7%] |
| `weather_only` | 3 | 24 | 11 | +13.9% | +19.1% | -5.2% | [+3.8%, +21.0%] | [-10.3%, -0.5%] |
| `weather_only` | 4 | 20 | 12 | +7.8% | +11.9% | -4.1% | [-4.3%, +15.9%] | [-10.6%, -0.6%] |
| `weather_only` | 5 | 21 | 12 | +2.5% | +5.7% | -3.2% | [-11.5%, +16.1%] | [-9.7%, +1.4%] |
| `weather_only` | 6 | 15 | 11 | -4.6% | -1.0% | -3.5% | [-22.4%, +12.8%] | [-12.7%, +4.3%] |
| `weather_only` | 7 | 28 | 12 | +12.5% | +16.9% | -4.4% | [+3.3%, +19.2%] | [-8.8%, -0.5%] |
| `weather_only` | 8 | 31 | 12 | +3.4% | +5.6% | -2.2% | [-5.8%, +12.3%] | [-6.0%, +0.7%] |
| `weather_only` | 9 | 29 | 12 | +8.8% | +12.9% | -4.1% | [-3.2%, +19.6%] | [-8.8%, +0.4%] |
| `weather_only` | 10 | 23 | 11 | +13.2% | +18.3% | -5.1% | [+2.5%, +21.1%] | [-9.5%, -0.8%] |
| `weather_plus_price` | 1 | 27 | 12 | +3.0% | +5.6% | -2.5% | [-6.7%, +11.8%] | [-7.9%, +1.0%] |
| `weather_plus_price` | 2 | 30 | 12 | +3.7% | +6.1% | -2.4% | [-4.9%, +11.5%] | [-7.4%, +0.7%] |

## Prefix walk-forward

| model | rows | dates | NO ROI | YES ROI | NO-YES | NO CI95 | Delta CI95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `weather_only` | 24 | 10 | +10.1% | +11.1% | -1.0% | [-0.3%, +18.4%] | [-5.7%, +3.3%] |
| `weather_plus_price` | 28 | 9 | -0.2% | +1.8% | -1.9% | [-11.9%, +11.0%] | [-3.8%, +0.6%] |

## 三道门

- significance=FAIL：最佳候选的 NO ROI / NO-over-YES bootstrap CI 仍跨 0或样本太小。
- baseline=FAIL：current YES sibling baseline 没被稳定打穿。
- forward=FAIL：train/OOS 与 prefix walk-forward 未同时同号过门。
- conclusion=inconclusive：不允许 live；若继续，只能保留 shadow-only telemetry。

## 产物

- CSV: `docs/analysis/2026-06/generated/theta_no_weather_model_selector_v6/feature_rows.csv`
- CSV: `docs/analysis/2026-06/generated/theta_no_weather_model_selector_v6/model_metrics.csv`
- CSV: `docs/analysis/2026-06/generated/theta_no_weather_model_selector_v6/fixed_grid.csv`
- CSV: `docs/analysis/2026-06/generated/theta_no_weather_model_selector_v6/chosen_holdout.csv`
- CSV: `docs/analysis/2026-06/generated/theta_no_weather_model_selector_v6/walkforward_summary.csv`
- JSON: `docs/analysis/2026-06/2026-06-16-theta-no-weather-model-selector-v6.json`
