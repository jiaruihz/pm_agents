# Theta Current YES Selector v7

Status: snapshot
Generated: 2026-06-15T17:19:06.963419+00:00
Target metric: `current_yes_no_reheat` = 在 source-aligned city-day 中，买当前 running-max bracket 的 YES，赌最终最高温仍落在当前档。

## 数据完整性自检

- Evidence layer: orderbook replay / opportunity research；不是 live_real fill PnL。
- current YES hour rows: 2204; strategy unique city-date-current rows: 1237; active dates: 26.
- fact_built_at_utc: `2026-06-15T16:31:04.483297+00:00`。
- fact_trades trade_class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`。
- fact_trades settlement_status: `[{'settlement_status': '', 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]`。
- fact_signal_candidates coverage: `{'rows': 30140, 'eligible': 10366, 'paper_ordered': 3968, 'live_filled': 348}`。
- CLOB orders/fills join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`。
- CLOB coverage gate: gate_pass=True, missing_order_rows=0, over_order_keys=0, db_fill_cost_minus_fact_cost=0.0.

## 人话结论

这轮把表达正式切到 current YES。结论是：current YES 确实比 d1 NO 更贴合 no-reheat thesis，但在当前 26 个 replay 日期里，仍没有通过 live 三道门。最主要的卡点是 train/holdout 不稳定和 prefix walk-forward 样本薄。

所以今天还不能说找到了 live 策略；但我们已经把方向从 NO carry 修正到了更合理的 current YES sibling expression。

## 模型判别力

| model | train/OOS Brier | train/OOS AUC | train base | holdout base |
|---|---:|---:|---:|---:|
| `weather_only` | 0.1962 / 0.1858 | 0.766 / 0.785 | 56.5% | 57.6% |
| `weather_plus_price` | 0.1462 / 0.1358 | 0.862 / 0.876 | 56.5% | 57.6% |

## Train 选出的规则在 holdout 的结果

| model | rank | rows | dates | YES ROI | d1 NO ROI | YES-NO | YES CI95 | Delta CI95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `weather_only` | 1 | 27 | 12 | +16.9% | +10.6% | +6.3% | [-0.1%, +32.0%] | [+2.9%, +9.3%] |
| `weather_only` | 2 | 40 | 13 | +3.6% | -0.8% | +4.4% | [-14.2%, +19.3%] | [+2.2%, +6.8%] |
| `weather_only` | 3 | 34 | 14 | +14.6% | +8.9% | +5.7% | [-1.0%, +31.4%] | [+2.7%, +8.7%] |
| `weather_only` | 4 | 34 | 14 | +14.6% | +8.9% | +5.7% | [-1.0%, +31.4%] | [+2.7%, +8.7%] |
| `weather_only` | 5 | 34 | 14 | +14.6% | +8.9% | +5.7% | [-1.0%, +31.4%] | [+2.7%, +8.7%] |
| `weather_only` | 6 | 46 | 14 | +3.1% | -1.0% | +4.2% | [-13.9%, +19.5%] | [+2.0%, +6.4%] |
| `weather_only` | 7 | 46 | 14 | +3.1% | -1.0% | +4.2% | [-13.9%, +19.5%] | [+2.0%, +6.4%] |
| `weather_only` | 8 | 46 | 14 | +3.1% | -1.0% | +4.2% | [-13.9%, +19.5%] | [+2.0%, +6.4%] |
| `weather_only` | 9 | 25 | 13 | -13.7% | -16.2% | +2.4% | [-38.9%, +10.1%] | [+1.2%, +4.1%] |
| `weather_only` | 10 | 30 | 14 | -13.7% | -15.8% | +2.2% | [-36.5%, +9.6%] | [+1.3%, +3.6%] |
| `weather_plus_price` | 1 | 18 | 10 | +22.7% | +19.8% | +3.0% | [+20.0%, +25.8%] | [-0.4%, +6.4%] |
| `weather_plus_price` | 2 | 18 | 10 | +22.7% | +19.8% | +3.0% | [+20.0%, +25.8%] | [-0.4%, +6.4%] |
| `weather_plus_price` | 3 | 18 | 10 | +22.7% | +19.8% | +3.0% | [+20.0%, +25.8%] | [-0.4%, +6.4%] |
| `weather_plus_price` | 4 | 17 | 10 | +21.9% | +19.4% | +2.5% | [+19.4%, +25.0%] | [-0.8%, +6.1%] |

## Prefix walk-forward

| model | rows | dates | YES ROI | d1 NO ROI | YES-NO | YES CI95 | Delta CI95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `weather_only` | 36 | 14 | +7.7% | +4.0% | +3.7% | [-7.8%, +21.0%] | [+2.2%, +5.6%] |
| `weather_plus_price` | 38 | 9 | -0.7% | -2.4% | +1.6% | [-9.9%, +8.6%] | [+0.1%, +3.0%] |

## 三道门

- significance=FAIL：候选规则和 walk-forward 的日期 bootstrap CI 未稳定支持 YES ROI 与 YES-over-NO 同时大于 0。
- baseline=FAIL/partial：相对 d1 NO sibling 通常改善，但不够稳定；相对 0 EV 也未稳过。
- forward=FAIL：train 选出的规则在 holdout/walk-forward 没有稳定复现。
- conclusion=inconclusive：不允许 live；可作为 shadow-only current YES sibling selector 继续积累。

## 产物

该入口及下列派生 CSV 已在 2026-08-05 完成 SHA-256 精确重放后退场；复现代码固定为
`a1349192df452d88cecd4c2731653c5bda9d8a5e`，删除证明见 JRS artifact manifest
`pm_agents_replay_prune_theta_yes_current_selector_v7_20260805.json`。本报告保留结论与历史路径，不把这些路径当作当前工作树输入。

- CSV: `docs/analysis/2026-06/generated/theta_yes_current_selector_v7/model_metrics.csv`
- CSV: `docs/analysis/2026-06/generated/theta_yes_current_selector_v7/fixed_grid.csv`
- CSV: `docs/analysis/2026-06/generated/theta_yes_current_selector_v7/chosen_holdout.csv`
- CSV: `docs/analysis/2026-06/generated/theta_yes_current_selector_v7/walkforward_summary.csv`
- JSON: `docs/analysis/2026-06/2026-06-16-theta-yes-current-selector-v7.json`
