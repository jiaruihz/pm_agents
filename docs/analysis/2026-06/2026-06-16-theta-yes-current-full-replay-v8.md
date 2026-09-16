# Theta Current YES Full Replay v8

Status: snapshot
Generated: 2026-06-15T17:32:08.887854+00:00
Target metric: `full_current_yes_no_reheat` = 直接从 raw orderbook 物化所有当前 running-max bracket YES，而不是只看 d1 NO sibling 分母。

## 数据完整性自检

- Evidence layer: orderbook replay / opportunity research；不是 live_real fill PnL。
- current YES hour rows: 3239; strategy rows: 1480; active dates: 27.
- d1 NO sibling pair rate: 90.2%.
- fact_built_at_utc: `2026-06-15T16:31:04.483297+00:00`。
- fact_trades trade_class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`。
- fact_trades settlement_status: `[{'settlement_status': '', 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]`。
- fact_signal_candidates coverage: `{'rows': 30140, 'eligible': 10366, 'paper_ordered': 3968, 'live_filled': 348}`。
- CLOB orders/fills join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`。
- CLOB coverage gate: gate_pass=True, missing_order_rows=0, over_order_keys=0, db_fill_cost_minus_fact_cost=0.0.

## 人话结论

全量 current YES 分母比 v7 更干净，而且把核心关系讲明白了：NO carry 不是一个已经独立证明的 alpha，它和 current YES 都在交易同一个 no-reheat thesis；区别只是 payoff 表达。

固定 train→holdout 切片里，current YES 的 best rule 已经同时跑赢自己和 paired d1 NO；但部署式 prefix walk-forward 没复现，YES ROI 和 YES-NO 增量 CI 都跨 0。所以当前结论是 shadow_candidate，不是 live。

## 模型判别力

| model | train/OOS Brier | train/OOS AUC | train base | holdout base |
|---|---:|---:|---:|---:|
| `weather_only` | 0.1445 / 0.1356 | 0.858 / 0.865 | 66.1% | 68.8% |
| `weather_plus_price` | 0.1025 / 0.0954 | 0.923 / 0.925 | 66.1% | 68.8% |

## Train 选出的规则在 holdout

| model | rank | rows | dates | YES ROI | YES CI95 | paired d1 NO ROI | YES-NO CI95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `weather_only` | 1 | 41 | 13 | +9.4% | [-4.2%, +21.0%] | +7.0% | [+3.1%, +7.2%] |
| `weather_only` | 2 | 62 | 13 | -0.8% | [-15.6%, +12.4%] | +1.3% | [-7.1%, +4.3%] |
| `weather_only` | 3 | 39 | 13 | +6.8% | [-7.9%, +19.2%] | +4.3% | [+3.1%, +7.2%] |
| `weather_only` | 4 | 72 | 14 | -1.2% | [-13.9%, +10.7%] | -0.1% | [-5.9%, +4.7%] |
| `weather_only` | 5 | 45 | 14 | +8.5% | [-3.8%, +21.3%] | +5.6% | [+3.2%, +7.5%] |
| `weather_only` | 6 | 45 | 14 | +8.5% | [-3.8%, +21.3%] | +5.6% | [+3.2%, +7.5%] |
| `weather_only` | 7 | 45 | 14 | +8.5% | [-3.8%, +21.3%] | +5.6% | [+3.2%, +7.5%] |
| `weather_only` | 8 | 61 | 13 | -2.4% | [-18.1%, +11.0%] | -0.3% | [-7.5%, +4.3%] |
| `weather_only` | 9 | 78 | 14 | +4.4% | [-5.6%, +14.2%] | +4.9% | [-4.5%, +5.2%] |
| `weather_only` | 10 | 57 | 14 | +15.9% | [+2.3%, +28.2%] | +11.2% | [+4.5%, +9.6%] |
| `weather_plus_price` | 1 | 32 | 11 | +17.7% | [+4.6%, +28.1%] | +13.5% | [+1.7%, +6.8%] |
| `weather_plus_price` | 2 | 32 | 11 | +17.7% | [+4.6%, +28.1%] | +13.5% | [+1.7%, +6.8%] |

## Prefix walk-forward

| model | rows | dates | YES ROI | YES CI95 | paired d1 NO ROI | YES-NO CI95 |
|---|---:|---:|---:|---:|---:|---:|
| `weather_only` | 138 | 17 | -1.7% | [-9.0%, +5.4%] | -1.7% | [-3.0%, +2.7%] |
| `weather_plus_price` | 152 | 14 | +1.2% | [-2.5%, +5.2%] | +0.5% | [-1.7%, +2.1%] |

## 三道门

- significance=PASS_FIXED：train 选出的 best fixed rule 在 holdout 的 YES ROI CI 大于 0。
- baseline=PASS_FIXED：同一 fixed rule 下 current YES 相对 paired d1 NO 的 YES-NO CI 大于 0。
- forward=FAIL_PREFIX：prefix walk-forward 未稳定复现正 ROI 或正增量。
- conclusion=shadow_candidate：不允许 live；继续 zero-notional / shadow-only current YES telemetry。

## 产物

- CSV: `docs/analysis/2026-06/generated/theta_yes_current_full_replay_v8/current_yes_rows.csv`
- CSV: `docs/analysis/2026-06/generated/theta_yes_current_full_replay_v8/feature_rows.csv`
- CSV: `docs/analysis/2026-06/generated/theta_yes_current_full_replay_v8/model_metrics.csv`
- CSV: `docs/analysis/2026-06/generated/theta_yes_current_full_replay_v8/chosen_holdout.csv`
- CSV: `docs/analysis/2026-06/generated/theta_yes_current_full_replay_v8/walkforward_summary.csv`
- JSON: `docs/analysis/2026-06/2026-06-16-theta-yes-current-full-replay-v8.json`
