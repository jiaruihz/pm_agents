# Tmax Distribution P5 Walk-Forward Execution Replay v1

> generated_at_utc: `2026-07-09T03:41:43+00:00`
> Scope: research-only walk-forward execution replay; no live runner/order behavior changed.

## 结论

- P5 固定 P3/P4 的分布模型，不新增天气 gate；交易表达由 `P(win) - ask` 决定，每个 city-date-hour 只保留最高 edge 的一个表达。
- 这一步把问题从“模型 logloss 是否赢盘口”推进到“真实 ask 下，概率优势能不能稳定落地”。
- verified 6/21-6/26 最好的一组是 `market_recal_blend`：303 rows，ROI +5.0%，CI [-5.7%, +14.8%]。
- extension 6/27-6/29 最好的一组是 `market_recal_blend`：6 rows，ROI +14.6%，CI [n/a, n/a]。
- 结论：`inconclusive_positive_signal`。概率模型方向仍有 edge 痕迹，但 extension 只有 3 天，EV CI 宽，不能 live。
- 旧样本 dev-CV 最偏好的 `edge>=0.10` 在 verified forward 仍很强，但在 6/27-6/29 extension 明显变薄；这说明高 edge 排序有信号，但 recent 压力测试还没过。

## Funnel / Evidence

- P4 scored rows: `8376`; date range `2026-05-19`..`2026-07-08`; cities `36`。
- raw label sources: `{'settlement_outcomes': 10105, 'missing': 4231, 'observed_max_derived': 32}`。
- skipped observed-derived before 6/27: `0`。
- scopes: `dev_cv` = 6/21 前训练窗内 expanding-CV；`verified_forward` = forward rows backed by `settlement_outcomes`；`extension_forward` = forward rows still using observed-max-derived labels。
- DB inventory: `{'fact_signal_candidates': {'rows': 48857, 'min_date': '2026-05-05', 'max_date': '2026-07-09'}, 'fact_trades': {'rows': 4533, 'min_date': '2026-05-06', 'max_date': '2026-07-07'}, 'settlement_outcomes': {'rows': 29715, 'min_date': '2026-05-04', 'max_date': '2026-07-07'}}`。

## Primary Replay, Edge >= 0.02

| scope | method | selected_rows | dates | cities | avg_rows_per_date | avg_ask | avg_edge | win_rate | cost | pnl | roi | roi_ci_low | roi_ci_high | daily_win_rate | losing_days | worst_day_pnl | worst_day_roi | current_yes_rows | current_no_rows | d1_no_rows | d2_no_rows |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dev_cv | market_local_norm | 59 | 16 | 30 | 3.69 | 0.274 | +0.056 | 35.6% | 16.14 | +4.86 | +30.1% | -6.7% | +63.1% | 50.0% | 8 | -0.75 | -100.0% | 3 | 31 | 22 | 3 |
| dev_cv | loo_no_regime_blend | 936 | 19 | 36 | 49.26 | 0.551 | +0.047 | 59.7% | 515.44 | +43.56 | +8.5% | +1.8% | +14.6% | 68.4% | 6 | -6.99 | -26.9% | 228 | 207 | 309 | 192 |
| dev_cv | loo_no_city_source_blend | 1068 | 19 | 36 | 56.21 | 0.537 | +0.047 | 57.5% | 573.52 | +40.48 | +7.1% | +1.6% | +12.1% | 73.7% | 5 | -6.09 | -22.5% | 256 | 248 | 359 | 205 |
| dev_cv | mkt_regime_blend | 1068 | 19 | 36 | 56.21 | 0.537 | +0.047 | 57.5% | 573.52 | +40.48 | +7.1% | +1.6% | +12.1% | 73.7% | 5 | -6.09 | -22.5% | 256 | 248 | 359 | 205 |
| dev_cv | mkt_city_source_blend | 1098 | 19 | 36 | 57.79 | 0.541 | +0.048 | 57.8% | 594.23 | +40.77 | +6.9% | +0.6% | +12.5% | 68.4% | 6 | -8.62 | -30.1% | 253 | 261 | 362 | 222 |
| dev_cv | market_recal_blend | 616 | 19 | 36 | 32.42 | 0.579 | +0.043 | 56.7% | 356.95 | -7.95 | -2.2% | -8.6% | +3.7% | 47.4% | 10 | -6.70 | -23.3% | 112 | 58 | 242 | 204 |
| extension_forward | market_recal_blend | 6 | 2 | 5 | 3.00 | 0.582 | +0.034 | 66.7% | 3.49 | +0.51 | +14.6% | n/a | n/a | 100.0% | 0 | +0.12 | +13.8% | 1 | 1 | 0 | 4 |
| extension_forward | loo_no_city_source_blend | 10 | 2 | 5 | 5.00 | 0.591 | +0.034 | 60.0% | 5.91 | +0.09 | +1.5% | n/a | n/a | 50.0% | 1 | -0.79 | -100.0% | 1 | 2 | 3 | 4 |
| extension_forward | mkt_regime_blend | 10 | 2 | 5 | 5.00 | 0.591 | +0.034 | 60.0% | 5.91 | +0.09 | +1.5% | n/a | n/a | 50.0% | 1 | -0.79 | -100.0% | 1 | 2 | 3 | 4 |
| extension_forward | loo_no_regime_blend | 3 | 2 | 3 | 1.50 | 0.354 | +0.038 | 33.3% | 1.06 | -0.06 | -5.9% | n/a | n/a | 0.0% | 2 | -0.05 | -4.8% | 1 | 1 | 0 | 1 |
| extension_forward | mkt_city_source_blend | 7 | 2 | 6 | 3.50 | 0.609 | +0.040 | 42.9% | 4.26 | -1.26 | -29.6% | n/a | n/a | 0.0% | 2 | -0.79 | -100.0% | 0 | 2 | 1 | 4 |
| extension_forward | market_local_norm | 1 | 1 | 1 | 1.00 | 0.170 | +0.040 | 0.0% | 0.17 | -0.17 | -100.0% | n/a | n/a | 0.0% | 1 | -0.17 | -100.0% | 0 | 1 | 0 | 0 |
| verified_forward | market_recal_blend | 303 | 15 | 36 | 20.20 | 0.497 | +0.061 | 52.1% | 150.53 | +7.47 | +5.0% | -5.7% | +14.8% | 46.7% | 8 | -2.54 | -16.3% | 54 | 53 | 96 | 100 |
| verified_forward | loo_no_city_source_blend | 511 | 15 | 36 | 34.07 | 0.453 | +0.060 | 47.0% | 231.48 | +8.52 | +3.7% | -10.2% | +16.3% | 60.0% | 6 | -9.00 | -37.5% | 102 | 173 | 146 | 90 |
| verified_forward | mkt_regime_blend | 511 | 15 | 36 | 34.07 | 0.453 | +0.060 | 47.0% | 231.48 | +8.52 | +3.7% | -10.2% | +16.3% | 60.0% | 6 | -9.00 | -37.5% | 102 | 173 | 146 | 90 |
| verified_forward | mkt_city_source_blend | 541 | 15 | 36 | 36.07 | 0.468 | +0.060 | 48.1% | 253.00 | +7.00 | +2.8% | -9.3% | +15.3% | 46.7% | 8 | -7.61 | -36.9% | 116 | 157 | 170 | 98 |
| verified_forward | loo_no_regime_blend | 494 | 15 | 36 | 32.93 | 0.460 | +0.059 | 47.2% | 227.42 | +5.58 | +2.5% | -9.6% | +15.6% | 53.3% | 7 | -8.18 | -42.6% | 106 | 132 | 159 | 97 |
| verified_forward | market_local_norm | 87 | 14 | 32 | 6.21 | 0.232 | +0.113 | 21.8% | 20.15 | -1.15 | -5.7% | -39.0% | +43.0% | 57.1% | 6 | -2.44 | -70.9% | 1 | 32 | 51 | 3 |

## Dev-CV Policy Ranking

这个表只用 6/21 前 expanding-CV 排序，目的是看如果先在旧样本里选方法/threshold，forward 是否同号。不是 live 选择器。

| method | edge_threshold | selected_rows | dates | cities | avg_ask | avg_edge | win_rate | roi | roi_ci_low | roi_ci_high | daily_win_rate | losing_days |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mkt_city_source_blend | 0.05 | 378 | 19 | 36 | 0.492 | +0.078 | 56.6% | +15.0% | +3.0% | +25.3% | 73.7% | 5 |
| loo_no_regime_blend | 0.02 | 936 | 19 | 36 | 0.551 | +0.047 | 59.7% | +8.5% | +1.8% | +14.6% | 68.4% | 6 |
| loo_no_city_source_blend | 0.02 | 1068 | 19 | 36 | 0.537 | +0.047 | 57.5% | +7.1% | +1.6% | +12.1% | 73.7% | 5 |
| mkt_regime_blend | 0.02 | 1068 | 19 | 36 | 0.537 | +0.047 | 57.5% | +7.1% | +1.6% | +12.1% | 73.7% | 5 |
| loo_no_regime_blend | 0.05 | 305 | 19 | 36 | 0.489 | +0.077 | 55.1% | +12.8% | +1.0% | +24.1% | 63.2% | 7 |
| mkt_city_source_blend | 0.02 | 1098 | 19 | 36 | 0.541 | +0.048 | 57.8% | +6.9% | +0.6% | +12.5% | 68.4% | 6 |
| mkt_city_source_blend | 0.0 | 2570 | 19 | 36 | 0.517 | +0.025 | 53.2% | +3.0% | +0.4% | +5.5% | 68.4% | 6 |
| loo_no_regime_blend | 0.0 | 2466 | 19 | 36 | 0.500 | +0.022 | 51.9% | +3.8% | +0.3% | +7.0% | 73.7% | 5 |

## Dev-Selected Threshold Forward Check

dev-CV 排名前几名偏向 `edge>=0.10`。如果把这个阈值冻结到未来，verified 仍强，但 extension 变薄，所以不能把旧样本最优阈值直接当 live 参数。

| scope | method | edge_threshold | selected_rows | dates | cities | avg_ask | avg_edge | win_rate | cost | pnl | roi | roi_ci_low | roi_ci_high | daily_win_rate | losing_days | worst_day_pnl | worst_day_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| extension_forward | loo_no_city_source_blend | 0.05 | 1 | 1 | 1 | 0.170 | +0.072 | 0.0% | 0.17 | -0.17 | -100.0% | n/a | n/a | 0.0% | 1 | -0.17 | -100.0% |
| extension_forward | loo_no_regime_blend | 0.05 | 1 | 1 | 1 | 0.170 | +0.063 | 0.0% | 0.17 | -0.17 | -100.0% | n/a | n/a | 0.0% | 1 | -0.17 | -100.0% |
| extension_forward | mkt_city_source_blend | 0.05 | 1 | 1 | 1 | 0.170 | +0.076 | 0.0% | 0.17 | -0.17 | -100.0% | n/a | n/a | 0.0% | 1 | -0.17 | -100.0% |
| extension_forward | mkt_regime_blend | 0.05 | 1 | 1 | 1 | 0.170 | +0.072 | 0.0% | 0.17 | -0.17 | -100.0% | n/a | n/a | 0.0% | 1 | -0.17 | -100.0% |
| verified_forward | mkt_city_source_blend | 0.05 | 219 | 15 | 36 | 0.409 | +0.099 | 47.0% | 89.66 | +13.34 | +14.9% | -6.2% | +33.3% | 66.7% | 5 | -4.21 | -51.3% |
| verified_forward | loo_no_city_source_blend | 0.05 | 205 | 15 | 35 | 0.415 | +0.100 | 46.3% | 85.13 | +9.87 | +11.6% | -6.5% | +27.8% | 66.7% | 5 | -3.56 | -37.3% |
| verified_forward | mkt_regime_blend | 0.05 | 205 | 15 | 35 | 0.415 | +0.100 | 46.3% | 85.13 | +9.87 | +11.6% | -6.5% | +27.8% | 66.7% | 5 | -3.56 | -37.3% |
| verified_forward | loo_no_regime_blend | 0.05 | 189 | 15 | 35 | 0.387 | +0.103 | 40.7% | 73.17 | +3.83 | +5.2% | -17.3% | +28.4% | 60.0% | 6 | -4.49 | -59.9% |
| verified_forward | loo_no_regime_blend | 0.1 | 48 | 13 | 24 | 0.273 | +0.202 | 35.4% | 13.11 | +3.89 | +29.7% | -20.4% | +74.1% | 53.8% | 6 | -1.10 | -100.0% |
| verified_forward | loo_no_city_source_blend | 0.1 | 50 | 13 | 22 | 0.260 | +0.201 | 30.0% | 13.01 | +1.99 | +15.3% | -26.3% | +69.1% | 69.2% | 4 | -1.40 | -100.0% |
| verified_forward | mkt_regime_blend | 0.1 | 50 | 13 | 22 | 0.260 | +0.201 | 30.0% | 13.01 | +1.99 | +15.3% | -26.3% | +69.1% | 69.2% | 4 | -1.40 | -100.0% |
| verified_forward | mkt_city_source_blend | 0.1 | 51 | 13 | 23 | 0.252 | +0.198 | 27.5% | 12.85 | +1.15 | +9.0% | -29.7% | +54.6% | 53.8% | 6 | -1.42 | -100.0% |

## Worst Forward Days

| scope | method | target_date | rows | cities | cost | pnl | roi | current_yes_rows | current_no_rows | d1_no_rows | d2_no_rows |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| verified_forward | mkt_regime_blend | 2026-07-05 | 52 | 23 | 24.00 | -9.00 | -37.5% | 9 | 21 | 15 | 7 |
| verified_forward | loo_no_city_source_blend | 2026-07-05 | 52 | 23 | 24.00 | -9.00 | -37.5% | 9 | 21 | 15 | 7 |
| verified_forward | mkt_city_source_blend | 2026-07-05 | 42 | 22 | 20.61 | -7.61 | -36.9% | 11 | 12 | 13 | 6 |
| verified_forward | mkt_city_source_blend | 2026-06-26 | 48 | 28 | 21.54 | -5.54 | -25.7% | 12 | 11 | 18 | 7 |
| verified_forward | mkt_regime_blend | 2026-06-26 | 41 | 29 | 17.79 | -3.79 | -21.3% | 10 | 10 | 13 | 8 |
| verified_forward | loo_no_city_source_blend | 2026-06-26 | 41 | 29 | 17.79 | -3.79 | -21.3% | 10 | 10 | 13 | 8 |
| verified_forward | mkt_city_source_blend | 2026-06-29 | 23 | 10 | 9.29 | -2.29 | -24.7% | 1 | 11 | 4 | 7 |
| verified_forward | mkt_regime_blend | 2026-06-29 | 20 | 10 | 7.81 | -1.81 | -23.2% | 1 | 12 | 2 | 5 |
| verified_forward | loo_no_city_source_blend | 2026-06-29 | 20 | 10 | 7.81 | -1.81 | -23.2% | 1 | 12 | 2 | 5 |
| verified_forward | mkt_regime_blend | 2026-06-28 | 10 | 6 | 5.76 | -1.76 | -30.6% | 4 | 2 | 1 | 3 |
| verified_forward | loo_no_city_source_blend | 2026-06-28 | 10 | 6 | 5.76 | -1.76 | -30.6% | 4 | 2 | 1 | 3 |
| verified_forward | mkt_city_source_blend | 2026-07-01 | 33 | 18 | 19.42 | -1.42 | -7.3% | 3 | 10 | 12 | 8 |

## Diagnostic Slices

以下是诊断切片，不是 hard gate。用于看 EV 来自哪里、坏在哪里。

| scope | method | slice | value | rows | dates | cost | pnl | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| extension_forward | loo_no_city_source_blend | ask_bucket | 0-0.25 | 3 | 1 | 0.58 | +0.42 | +72.4% | n/a | n/a |
| extension_forward | loo_no_city_source_blend | ask_bucket | 0.50-0.75 | 1 | 1 | 0.75 | +0.25 | +33.3% | n/a | n/a |
| extension_forward | loo_no_city_source_blend | ask_bucket | 0.75-1.00 | 5 | 2 | 4.10 | -0.10 | -2.4% | n/a | n/a |
| extension_forward | loo_no_city_source_blend | ask_bucket | 0.25-0.50 | 1 | 1 | 0.48 | -0.48 | -100.0% | n/a | n/a |
| extension_forward | loo_no_city_source_blend | edge_bucket | 0.02-0.05 | 9 | 2 | 5.74 | +0.26 | +4.5% | n/a | n/a |
| extension_forward | loo_no_city_source_blend | edge_bucket | 0.05-0.10 | 1 | 1 | 0.17 | -0.17 | -100.0% | n/a | n/a |
| extension_forward | loo_no_city_source_blend | expression | current_yes | 1 | 1 | 0.22 | +0.78 | +354.5% | n/a | n/a |
| extension_forward | loo_no_city_source_blend | expression | current_no | 2 | 1 | 0.95 | +0.05 | +5.3% | n/a | n/a |
| extension_forward | loo_no_city_source_blend | expression | d2_no | 4 | 2 | 3.23 | -0.23 | -7.1% | n/a | n/a |
| extension_forward | loo_no_city_source_blend | expression | d1_no | 3 | 1 | 1.51 | -0.51 | -33.8% | n/a | n/a |
| extension_forward | loo_no_city_source_blend | intraday_state | active_warming | 5 | 1 | 2.94 | +1.06 | +36.1% | n/a | n/a |
| extension_forward | loo_no_city_source_blend | intraday_state | false_fade_risk | 2 | 1 | 1.26 | -0.26 | -20.6% | n/a | n/a |
| extension_forward | loo_no_city_source_blend | intraday_state | fresh_high | 2 | 2 | 1.54 | -0.54 | -35.1% | n/a | n/a |
| extension_forward | loo_no_city_source_blend | intraday_state | pullback_uncertain | 1 | 1 | 0.17 | -0.17 | -100.0% | n/a | n/a |
| extension_forward | loo_no_city_source_blend | running_max_state | near_high_plateau | 5 | 1 | 2.94 | +1.06 | +36.1% | n/a | n/a |
| extension_forward | loo_no_city_source_blend | running_max_state | pullback_from_high | 3 | 1 | 1.43 | -0.43 | -30.1% | n/a | n/a |
| extension_forward | loo_no_city_source_blend | running_max_state | fresh_running_high | 2 | 2 | 1.54 | -0.54 | -35.1% | n/a | n/a |
| extension_forward | mkt_city_source_blend | ask_bucket | 0.50-0.75 | 1 | 1 | 0.75 | +0.25 | +33.3% | n/a | n/a |
| extension_forward | mkt_city_source_blend | ask_bucket | 0.75-1.00 | 3 | 2 | 2.45 | -0.45 | -18.4% | n/a | n/a |
| extension_forward | mkt_city_source_blend | ask_bucket | 0-0.25 | 1 | 1 | 0.17 | -0.17 | -100.0% | n/a | n/a |
| extension_forward | mkt_city_source_blend | ask_bucket | 0.25-0.50 | 2 | 1 | 0.89 | -0.89 | -100.0% | n/a | n/a |
| extension_forward | mkt_city_source_blend | edge_bucket | 0.02-0.05 | 6 | 2 | 4.09 | -1.09 | -26.7% | n/a | n/a |
| extension_forward | mkt_city_source_blend | edge_bucket | 0.05-0.10 | 1 | 1 | 0.17 | -0.17 | -100.0% | n/a | n/a |
| extension_forward | mkt_city_source_blend | expression | current_no | 2 | 1 | 0.95 | +0.05 | +5.3% | n/a | n/a |
| extension_forward | mkt_city_source_blend | expression | d2_no | 4 | 2 | 2.83 | -0.83 | -29.3% | n/a | n/a |
| extension_forward | mkt_city_source_blend | expression | d1_no | 1 | 1 | 0.48 | -0.48 | -100.0% | n/a | n/a |
| extension_forward | mkt_city_source_blend | intraday_state | active_warming | 1 | 1 | 0.88 | +0.12 | +13.6% | n/a | n/a |
| extension_forward | mkt_city_source_blend | intraday_state | fresh_high | 2 | 2 | 1.54 | -0.54 | -35.1% | n/a | n/a |
| extension_forward | mkt_city_source_blend | intraday_state | false_fade_risk | 3 | 1 | 1.67 | -0.67 | -40.1% | n/a | n/a |
| extension_forward | mkt_city_source_blend | intraday_state | pullback_uncertain | 1 | 1 | 0.17 | -0.17 | -100.0% | n/a | n/a |
| extension_forward | mkt_city_source_blend | running_max_state | near_high_plateau | 1 | 1 | 0.88 | +0.12 | +13.6% | n/a | n/a |
| extension_forward | mkt_city_source_blend | running_max_state | fresh_running_high | 2 | 2 | 1.54 | -0.54 | -35.1% | n/a | n/a |
| extension_forward | mkt_city_source_blend | running_max_state | pullback_from_high | 4 | 1 | 1.84 | -0.84 | -45.7% | n/a | n/a |
| extension_forward | mkt_regime_blend | ask_bucket | 0-0.25 | 3 | 1 | 0.58 | +0.42 | +72.4% | n/a | n/a |
| extension_forward | mkt_regime_blend | ask_bucket | 0.50-0.75 | 1 | 1 | 0.75 | +0.25 | +33.3% | n/a | n/a |
| extension_forward | mkt_regime_blend | ask_bucket | 0.75-1.00 | 5 | 2 | 4.10 | -0.10 | -2.4% | n/a | n/a |
| extension_forward | mkt_regime_blend | ask_bucket | 0.25-0.50 | 1 | 1 | 0.48 | -0.48 | -100.0% | n/a | n/a |
| extension_forward | mkt_regime_blend | edge_bucket | 0.02-0.05 | 9 | 2 | 5.74 | +0.26 | +4.5% | n/a | n/a |
| extension_forward | mkt_regime_blend | edge_bucket | 0.05-0.10 | 1 | 1 | 0.17 | -0.17 | -100.0% | n/a | n/a |
| extension_forward | mkt_regime_blend | expression | current_yes | 1 | 1 | 0.22 | +0.78 | +354.5% | n/a | n/a |
| extension_forward | mkt_regime_blend | expression | current_no | 2 | 1 | 0.95 | +0.05 | +5.3% | n/a | n/a |
| extension_forward | mkt_regime_blend | expression | d2_no | 4 | 2 | 3.23 | -0.23 | -7.1% | n/a | n/a |
| extension_forward | mkt_regime_blend | expression | d1_no | 3 | 1 | 1.51 | -0.51 | -33.8% | n/a | n/a |
| extension_forward | mkt_regime_blend | intraday_state | active_warming | 5 | 1 | 2.94 | +1.06 | +36.1% | n/a | n/a |
| extension_forward | mkt_regime_blend | intraday_state | false_fade_risk | 2 | 1 | 1.26 | -0.26 | -20.6% | n/a | n/a |
| extension_forward | mkt_regime_blend | intraday_state | fresh_high | 2 | 2 | 1.54 | -0.54 | -35.1% | n/a | n/a |
| extension_forward | mkt_regime_blend | intraday_state | pullback_uncertain | 1 | 1 | 0.17 | -0.17 | -100.0% | n/a | n/a |
| extension_forward | mkt_regime_blend | running_max_state | near_high_plateau | 5 | 1 | 2.94 | +1.06 | +36.1% | n/a | n/a |
| extension_forward | mkt_regime_blend | running_max_state | pullback_from_high | 3 | 1 | 1.43 | -0.43 | -30.1% | n/a | n/a |
| extension_forward | mkt_regime_blend | running_max_state | fresh_running_high | 2 | 2 | 1.54 | -0.54 | -35.1% | n/a | n/a |
| verified_forward | loo_no_city_source_blend | ask_bucket | 0-0.25 | 153 | 14 | 14.52 | +1.48 | +10.2% | -33.6% | +63.6% |
| verified_forward | loo_no_city_source_blend | ask_bucket | 0.50-0.75 | 156 | 15 | 100.50 | +5.50 | +5.5% | -7.1% | +18.1% |
| verified_forward | loo_no_city_source_blend | ask_bucket | 0.75-1.00 | 91 | 15 | 74.97 | +4.03 | +5.4% | -5.7% | +16.3% |
| verified_forward | loo_no_city_source_blend | ask_bucket | 0.25-0.50 | 111 | 15 | 41.49 | -2.49 | -6.0% | -38.8% | +30.2% |
| verified_forward | loo_no_city_source_blend | edge_bucket | 0.10+ | 50 | 13 | 13.01 | +1.99 | +15.3% | -26.3% | +69.1% |
| verified_forward | loo_no_city_source_blend | edge_bucket | 0.05-0.10 | 155 | 15 | 72.12 | +7.88 | +10.9% | -7.0% | +28.6% |
| verified_forward | loo_no_city_source_blend | edge_bucket | 0.02-0.05 | 306 | 15 | 146.35 | -1.35 | -0.9% | -13.7% | +12.5% |
| verified_forward | loo_no_city_source_blend | expression | current_yes | 102 | 14 | 38.21 | +4.79 | +12.5% | -16.7% | +38.9% |
| verified_forward | loo_no_city_source_blend | expression | d1_no | 146 | 15 | 72.92 | +5.08 | +7.0% | -9.0% | +23.2% |
| verified_forward | loo_no_city_source_blend | expression | d2_no | 90 | 15 | 58.33 | +0.67 | +1.2% | -12.5% | +15.0% |
| verified_forward | loo_no_city_source_blend | expression | current_no | 173 | 15 | 62.02 | -2.02 | -3.3% | -25.4% | +19.9% |
| verified_forward | loo_no_city_source_blend | intraday_state | fresh_high | 132 | 14 | 57.91 | +6.09 | +10.5% | -17.1% | +32.2% |
| verified_forward | loo_no_city_source_blend | intraday_state | false_fade_risk | 31 | 14 | 13.62 | +1.38 | +10.1% | -21.1% | +54.9% |
| verified_forward | loo_no_city_source_blend | intraday_state | pullback_uncertain | 18 | 9 | 7.56 | +0.44 | +5.8% | -32.2% | +64.5% |
| verified_forward | loo_no_city_source_blend | intraday_state | mature_fade | 24 | 9 | 13.50 | +0.50 | +3.7% | -67.2% | +36.1% |
| verified_forward | loo_no_city_source_blend | intraday_state | active_warming | 279 | 15 | 126.37 | +4.64 | +3.7% | -6.3% | +12.8% |
| verified_forward | loo_no_city_source_blend | intraday_state | plateau_near_high | 18 | 7 | 8.24 | -1.24 | -15.0% | -70.1% | +36.2% |
| verified_forward | loo_no_city_source_blend | intraday_state | reheating_after_dip | 9 | 6 | 4.28 | -3.28 | -76.6% | -100.0% | -28.2% |
| verified_forward | loo_no_city_source_blend | running_max_state | fresh_running_high | 296 | 15 | 123.97 | +11.03 | +8.9% | -8.7% | +21.6% |
| verified_forward | loo_no_city_source_blend | running_max_state | mature_fade | 44 | 11 | 23.11 | -0.11 | -0.5% | -47.2% | +33.5% |
| verified_forward | loo_no_city_source_blend | running_max_state | near_high_plateau | 128 | 8 | 66.35 | -1.35 | -2.0% | -17.9% | +17.4% |
| verified_forward | loo_no_city_source_blend | running_max_state | pullback_from_high | 43 | 13 | 18.05 | -1.05 | -5.8% | -31.4% | +24.0% |
| verified_forward | mkt_city_source_blend | ask_bucket | 0.50-0.75 | 165 | 15 | 106.17 | +8.83 | +8.3% | -5.7% | +22.1% |
| verified_forward | mkt_city_source_blend | ask_bucket | 0.75-1.00 | 106 | 15 | 87.08 | +1.92 | +2.2% | -7.8% | +12.9% |
| verified_forward | mkt_city_source_blend | ask_bucket | 0-0.25 | 150 | 13 | 14.77 | -0.77 | -5.2% | -46.0% | +38.6% |
| verified_forward | mkt_city_source_blend | ask_bucket | 0.25-0.50 | 120 | 14 | 44.98 | -2.98 | -6.6% | -38.2% | +22.1% |
| verified_forward | mkt_city_source_blend | edge_bucket | 0.05-0.10 | 168 | 15 | 76.81 | +12.19 | +15.9% | -5.9% | +35.8% |
| verified_forward | mkt_city_source_blend | edge_bucket | 0.10+ | 51 | 13 | 12.85 | +1.15 | +9.0% | -29.7% | +54.6% |
| verified_forward | mkt_city_source_blend | edge_bucket | 0.02-0.05 | 322 | 15 | 163.34 | -6.34 | -3.9% | -16.3% | +9.7% |
| verified_forward | mkt_city_source_blend | expression | d2_no | 98 | 15 | 62.75 | +6.25 | +10.0% | -4.7% | +23.9% |

## Model Selection

| spec | c | cv_rows | cv_dates | cv_market_logloss | cv_blend_alpha | cv_blend_logloss |
| --- | --- | --- | --- | --- | --- | --- |
| loo_no_boundary | 0.03 | 3743 | 19 | 0.6529527525647217 | 0.5 | 0.615946123406354 |
| loo_no_city_source | 0.03 | 3743 | 19 | 0.6529527525647217 | 0.5 | 0.617995638071871 |
| loo_no_meteo | 0.03 | 3743 | 19 | 0.6529527525647217 | 0.5 | 0.6184068495611225 |
| loo_no_regime | 0.03 | 3743 | 19 | 0.6529527525647217 | 0.5 | 0.6179845671038509 |
| market_recal | 0.3 | 3743 | 19 | 0.6529527525647217 | 0.75 | 0.6189221904120805 |
| mkt_boundary | 0.03 | 3743 | 19 | 0.6529527525647217 | 0.5 | 0.6207393023505728 |
| mkt_city_source | 0.03 | 3743 | 19 | 0.6529527525647217 | 0.5 | 0.6176524469655148 |
| mkt_meteo | 0.03 | 3743 | 19 | 0.6529527525647217 | 0.5 | 0.6185291274112892 |
| mkt_path_core | 0.1 | 3743 | 19 | 0.6529527525647217 | 0.5 | 0.6180812837094944 |
| mkt_regime | 0.03 | 3743 | 19 | 0.6529527525647217 | 0.5 | 0.617995638071871 |

## Verdict

significance=FAIL/THIN; baseline=PARTIAL_PASS; forward=FAIL/THIN; conclusion=`inconclusive_positive_signal`.

下一步需要两件事：第一，补完整 6/27+ official settlement/orderbook depth 后重跑；第二，把 P5 输出接成 zero-notional shadow telemetry，而不是 live 下单。

## Artifacts

- `docs/analysis/2026-07/generated/tmax_distribution_p5_walk_forward_execution_replay_v1/policy_summary.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p5_walk_forward_execution_replay_v1/daily.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p5_walk_forward_execution_replay_v1/diagnostic_slices.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p5_walk_forward_execution_replay_v1/opportunities.csv`
- `docs/analysis/2026-07/2026-07-03-tmax-distribution-p5-walk-forward-execution-replay-v1.json`
