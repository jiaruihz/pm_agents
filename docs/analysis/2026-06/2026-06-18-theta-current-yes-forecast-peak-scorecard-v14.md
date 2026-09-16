# Theta Current YES Forecast Peak Scorecard v14

Status: research_scorecard / not_live_ready_upgrade
Generated: 2026-06-17T16:49:18+00:00

Target metric: `forecast_peak_clock_current_yes_scorecard` = 在同一 current-YES replay 上，用共享 forecast peak clock 表评估固定 v9、after-peak 过滤、peak-forming 早入场的成功率和收益率。

## 数据完整性自检

- fact_built_at_utc: `2026-06-17T16:09:29.241520+00:00`
- fact_trades trade_class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`
- settlement_status: `[{'settlement_status': '', 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]`
- fact_signal_candidates coverage: `{'rows': 31496, 'eligible': 10961, 'paper_ordered': 4274, 'live_filled': 348}`
- CLOB orders/fills join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`
- CLOB gate: `{'gate_pass': True, 'fail_reasons': [], 'missing_order_rows': 0, 'over_order_keys': 0, 'db_fill_cost_minus_fact_cost': 0.0}`

## 人话结论

forecast peak clock 有价值，但它目前更像“解释和分层风险”的特征，不是一个能直接替换 v9 的上线规则。

最稳的仍然是原 v9：看到温度已经从 running max 回落，再买当前最高温 YES。给 v9 额外加 forecast peak 过滤，样本会变少，收益没有变得更可靠；而更早的 peak-forming 规则虽然试图在价格还没完全收敛前入场，但 holdout 仍然不够稳。

所以当前推进路线不是扩大 live，而是：继续保留 v9 tiny-live/telemetry，生产原生落盘 forecast peak fields，用 forward would-order 样本验证 forecast-clock 是否能提前入场或过滤坏单。

## Holdout 核心结果（$5/order, taker +2c）

| rule | orders | days | win | YES ROI | CI95 | d1 NO ROI | YES-NO | YES-NO CI95 | avg ask | avg GFS delta | avg ECMWF delta |
|---|---:|---:|---:|---:|---|---:|---:|---|---:|---:|---:|
| v9_fixed_fade_confirmed | 31 | 11 | +93.5% | +16.2% | [+2.7%, +27.6%] | +12.1% | +4.1% | [+1.3%, +7.2%] | 0.794 | 0.5 | -0.0 |
| v9_fixed_fade_models_agree | 14 | 9 | +92.9% | +12.5% | [-4.6%, +23.1%] | +9.2% | +3.4% | [+0.3%, +6.0%] | 0.807 | 0.0 | 0.1 |
| v9_fixed_fade_both_peaks_passed | 14 | 9 | +100.0% | +17.1% | [+14.8%, +20.8%] | +12.4% | +4.8% | [+2.7%, +8.7%] | 0.837 | 1.7 | 1.2 |
| v9_fixed_fade_after_peak_agree | 7 | 6 | +100.0% | +17.2% | [+12.9%, +20.5%] | +11.4% | +5.8% | [+2.2%, +10.0%] | 0.838 | 1.6 | 1.6 |
| forecast_fade_no_fixed_hour | 6 | 4 | +100.0% | +10.7% | [+8.8%, +11.7%] | +8.0% | +2.7% | [+0.5%, +4.0%] | 0.884 | 1.8 | 1.2 |
| forecast_peak_forming_agree | 23 | 11 | +87.0% | +10.0% | [-12.2%, +27.5%] | +6.2% | +3.9% | [+2.0%, +6.0%] | 0.770 | 0.0 | -0.2 |
| gfs_peak_forming_shadow | 40 | 12 | +85.0% | +9.1% | [-9.6%, +27.7%] | +5.0% | +4.1% | [+2.0%, +6.5%] | 0.772 | -0.2 | 0.2 |

## Forecast Delta Diagnostic

下面这张表不是参数选择器，只看满足基础 price/model gate 后，GFS 预报峰值相对决策时间的粗分桶。它回答的是：是不是越接近/越过预报峰值，current YES 越安全。

| period | GFS delta bucket | orders | days | win | YES ROI | avg ask |
|---|---|---:|---:|---:|---:|---:|
| holdout | <=-2h | 14 | 8 | +57.1% | -27.8% | 0.787 |
| holdout | -2..-1h | 16 | 10 | +81.2% | +7.8% | 0.760 |
| holdout | -1..0h | 29 | 13 | +75.9% | -3.0% | 0.783 |
| holdout | 0..1h | 36 | 12 | +83.3% | +5.7% | 0.777 |
| holdout | 1..2h | 30 | 12 | +90.0% | +12.0% | 0.784 |
| holdout | 2..4h | 28 | 13 | +92.9% | +9.8% | 0.823 |
| holdout | >4h | 8 | 6 | +75.0% | -6.9% | 0.797 |

## Funnel

- raw current-YES replay rows: `3239`
- joined forecast peak rows: `3239`
- rows with both forecast models: `3239`
- live-style dedupe grain: first `target_date + city + current_bracket`, max 2 current brackets per city-day

## Verdict

- significance=FAIL for forecast-clock upgrade: after-peak/agreement variants do not beat v9 with stronger support.
- baseline=FAIL: v9 remains the stronger simple baseline; forecast-clock filters mostly shrink samples.
- forward=FAIL/NA: these are historical backfilled forecast fields, not production native forward fields.
- conclusion=`inconclusive` for live upgrade; `telemetry_required` for forward sample collection.

## Outputs

- JSON: `docs/analysis/2026-06/2026-06-18-theta-current-yes-forecast-peak-scorecard-v14.json`
- rule summary CSV: `docs/analysis/2026-06/generated/theta_current_yes_forecast_peak_scorecard_v14/rule_summary.csv`
- forecast bin CSV: `docs/analysis/2026-06/generated/theta_current_yes_forecast_peak_scorecard_v14/forecast_delta_bins.csv`
- selected rows CSV: `docs/analysis/2026-06/generated/theta_current_yes_forecast_peak_scorecard_v14/selected_rule_rows.csv`
- Script: `scripts/analysis/reheat_risk/research_theta_current_yes_forecast_peak_scorecard_v14.py`
