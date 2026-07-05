# Tmax Distribution P5 Walk-Forward Execution Replay v1

> generated_at_utc: `2026-07-05T14:16:37+00:00`
> Scope: research-only walk-forward execution replay; no live runner/order behavior changed.

## 结论

- P5 固定 P3/P4 的分布模型，不新增天气 gate；交易表达由 `P(win) - ask` 决定，每个 city-date-hour 只保留最高 edge 的一个表达。
- 这一步把问题从“模型 logloss 是否赢盘口”推进到“真实 ask 下，概率优势能不能稳定落地”。
- verified 6/21-6/26 最好的一组是 `loo_no_city_source_blend`：412 rows，ROI +8.1%，CI [-5.0%, +19.4%]。
- extension 6/27-6/29 最好的一组是 `n/a`：0 rows，ROI n/a，CI [n/a, n/a]。
- 结论：`inconclusive_positive_signal`。概率模型方向仍有 edge 痕迹，但 extension 只有 3 天，EV CI 宽，不能 live。
- 旧样本 dev-CV 最偏好的 `edge>=0.10` 在 verified forward 仍很强，但在 6/27-6/29 extension 明显变薄；这说明高 edge 排序有信号，但 recent 压力测试还没过。

## Funnel / Evidence

- P4 scored rows: `8028`; date range `2026-05-19`..`2026-07-03`; cities `36`。
- raw label sources: `{'settlement_outcomes': 9744, 'missing': 4116}`。
- skipped observed-derived before 6/27: `0`。
- scopes: `dev_cv` = 6/21 前训练窗内 expanding-CV；`verified_forward` = forward rows backed by `settlement_outcomes`；`extension_forward` = forward rows still using observed-max-derived labels。
- DB inventory: `{'fact_signal_candidates': {'rows': 46253, 'min_date': '2026-05-05', 'max_date': '2026-07-07'}, 'fact_trades': {'rows': 4484, 'min_date': '2026-05-06', 'max_date': '2026-07-05'}, 'settlement_outcomes': {'rows': 28164, 'min_date': '2026-05-04', 'max_date': '2026-07-04'}}`。

## Primary Replay, Edge >= 0.02

| scope | method | selected_rows | dates | cities | avg_rows_per_date | avg_ask | avg_edge | win_rate | cost | pnl | roi | roi_ci_low | roi_ci_high | daily_win_rate | losing_days | worst_day_pnl | worst_day_roi | current_yes_rows | current_no_rows | d1_no_rows | d2_no_rows |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dev_cv | market_local_norm | 59 | 16 | 30 | 3.69 | 0.274 | +0.056 | 35.6% | 16.14 | +4.86 | +30.1% | -6.7% | +63.1% | 50.0% | 8 | -0.75 | -100.0% | 3 | 31 | 22 | 3 |
| dev_cv | loo_no_regime_blend | 936 | 19 | 36 | 49.26 | 0.551 | +0.047 | 59.7% | 515.44 | +43.56 | +8.5% | +1.8% | +14.6% | 68.4% | 6 | -6.99 | -26.9% | 228 | 207 | 309 | 192 |
| dev_cv | loo_no_city_source_blend | 1068 | 19 | 36 | 56.21 | 0.537 | +0.047 | 57.5% | 573.52 | +40.48 | +7.1% | +1.6% | +12.1% | 73.7% | 5 | -6.09 | -22.5% | 256 | 248 | 359 | 205 |
| dev_cv | mkt_regime_blend | 1068 | 19 | 36 | 56.21 | 0.537 | +0.047 | 57.5% | 573.52 | +40.48 | +7.1% | +1.6% | +12.1% | 73.7% | 5 | -6.09 | -22.5% | 256 | 248 | 359 | 205 |
| dev_cv | mkt_city_source_blend | 1098 | 19 | 36 | 57.79 | 0.541 | +0.048 | 57.8% | 594.23 | +40.77 | +6.9% | +0.6% | +12.5% | 68.4% | 6 | -8.62 | -30.1% | 253 | 261 | 362 | 222 |
| dev_cv | market_recal_blend | 616 | 19 | 36 | 32.42 | 0.579 | +0.043 | 56.7% | 356.95 | -7.95 | -2.2% | -8.6% | +3.7% | 47.4% | 10 | -6.70 | -23.3% | 112 | 58 | 242 | 204 |
| verified_forward | loo_no_city_source_blend | 412 | 12 | 36 | 34.33 | 0.438 | +0.059 | 47.3% | 180.40 | +14.60 | +8.1% | -5.0% | +19.4% | 58.3% | 5 | -3.79 | -21.3% | 89 | 136 | 113 | 74 |
| verified_forward | mkt_regime_blend | 412 | 12 | 36 | 34.33 | 0.438 | +0.059 | 47.3% | 180.40 | +14.60 | +8.1% | -5.0% | +19.4% | 58.3% | 5 | -3.79 | -21.3% | 89 | 136 | 113 | 74 |
| verified_forward | market_recal_blend | 258 | 12 | 36 | 21.50 | 0.487 | +0.061 | 51.9% | 125.58 | +8.41 | +6.7% | -5.8% | +17.2% | 50.0% | 6 | -2.54 | -16.3% | 48 | 44 | 83 | 83 |
| verified_forward | mkt_city_source_blend | 451 | 12 | 36 | 37.58 | 0.454 | +0.059 | 48.3% | 204.61 | +13.39 | +6.5% | -7.2% | +19.9% | 50.0% | 6 | -5.54 | -25.7% | 98 | 133 | 137 | 83 |
| verified_forward | loo_no_regime_blend | 415 | 12 | 36 | 34.58 | 0.443 | +0.058 | 46.0% | 183.68 | +7.32 | +4.0% | -11.9% | +17.7% | 50.0% | 6 | -8.18 | -42.6% | 96 | 108 | 127 | 84 |
| verified_forward | market_local_norm | 74 | 11 | 30 | 6.73 | 0.219 | +0.113 | 20.3% | 16.21 | -1.21 | -7.5% | -48.0% | +48.0% | 54.5% | 5 | -2.44 | -70.9% | 1 | 30 | 41 | 2 |

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
| verified_forward | mkt_city_source_blend | 0.05 | 172 | 12 | 35 | 0.389 | +0.101 | 47.1% | 66.94 | +14.05 | +21.0% | -4.7% | +40.7% | 66.7% | 4 | -3.62 | -78.4% |
| verified_forward | loo_no_city_source_blend | 0.05 | 163 | 12 | 34 | 0.395 | +0.100 | 46.6% | 64.34 | +11.66 | +18.1% | -1.0% | +33.4% | 66.7% | 4 | -1.18 | -28.3% |
| verified_forward | mkt_regime_blend | 0.05 | 163 | 12 | 34 | 0.395 | +0.100 | 46.6% | 64.34 | +11.66 | +18.1% | -1.0% | +33.4% | 66.7% | 4 | -1.18 | -28.3% |
| verified_forward | loo_no_regime_blend | 0.05 | 151 | 12 | 34 | 0.367 | +0.105 | 39.7% | 55.35 | +4.65 | +8.4% | -17.9% | +31.1% | 58.3% | 5 | -3.58 | -64.2% |
| verified_forward | loo_no_regime_blend | 0.1 | 38 | 10 | 21 | 0.252 | +0.214 | 34.2% | 9.58 | +3.42 | +35.7% | -34.9% | +93.5% | 60.0% | 4 | -1.10 | -100.0% |
| verified_forward | loo_no_city_source_blend | 0.1 | 38 | 10 | 18 | 0.241 | +0.212 | 31.6% | 9.16 | +2.84 | +31.0% | -23.9% | +85.2% | 80.0% | 2 | -1.40 | -100.0% |
| verified_forward | mkt_regime_blend | 0.1 | 38 | 10 | 18 | 0.241 | +0.212 | 31.6% | 9.16 | +2.84 | +31.0% | -23.9% | +85.2% | 80.0% | 2 | -1.40 | -100.0% |
| verified_forward | mkt_city_source_blend | 0.1 | 39 | 10 | 19 | 0.220 | +0.211 | 25.6% | 8.57 | +1.43 | +16.6% | -34.7% | +78.5% | 60.0% | 4 | -1.42 | -100.0% |

## Worst Forward Days

| scope | method | target_date | rows | cities | cost | pnl | roi | current_yes_rows | current_no_rows | d1_no_rows | d2_no_rows |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| verified_forward | mkt_city_source_blend | 2026-06-26 | 48 | 28 | 21.54 | -5.54 | -25.7% | 12 | 11 | 18 | 7 |
| verified_forward | loo_no_city_source_blend | 2026-06-26 | 41 | 29 | 17.79 | -3.79 | -21.3% | 10 | 10 | 13 | 8 |
| verified_forward | mkt_regime_blend | 2026-06-26 | 41 | 29 | 17.79 | -3.79 | -21.3% | 10 | 10 | 13 | 8 |
| verified_forward | mkt_city_source_blend | 2026-06-29 | 23 | 10 | 9.29 | -2.29 | -24.7% | 1 | 11 | 4 | 7 |
| verified_forward | mkt_regime_blend | 2026-06-29 | 20 | 10 | 7.81 | -1.81 | -23.2% | 1 | 12 | 2 | 5 |
| verified_forward | loo_no_city_source_blend | 2026-06-29 | 20 | 10 | 7.81 | -1.81 | -23.2% | 1 | 12 | 2 | 5 |
| verified_forward | mkt_regime_blend | 2026-06-28 | 10 | 6 | 5.76 | -1.76 | -30.6% | 4 | 2 | 1 | 3 |
| verified_forward | loo_no_city_source_blend | 2026-06-28 | 10 | 6 | 5.76 | -1.76 | -30.6% | 4 | 2 | 1 | 3 |
| verified_forward | mkt_city_source_blend | 2026-07-01 | 33 | 18 | 19.42 | -1.42 | -7.3% | 3 | 10 | 12 | 8 |
| verified_forward | mkt_city_source_blend | 2026-06-21 | 49 | 28 | 21.38 | -1.38 | -6.5% | 10 | 18 | 15 | 6 |
| verified_forward | loo_no_city_source_blend | 2026-07-01 | 28 | 18 | 15.30 | -1.31 | -8.5% | 4 | 10 | 5 | 9 |
| verified_forward | mkt_regime_blend | 2026-07-01 | 28 | 18 | 15.30 | -1.31 | -8.5% | 4 | 10 | 5 | 9 |

## Diagnostic Slices

以下是诊断切片，不是 hard gate。用于看 EV 来自哪里、坏在哪里。

| scope | method | slice | value | rows | dates | cost | pnl | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| verified_forward | loo_no_city_source_blend | ask_bucket | 0-0.25 | 132 | 11 | 12.38 | +1.62 | +13.0% | -34.6% | +71.0% |
| verified_forward | loo_no_city_source_blend | ask_bucket | 0.75-1.00 | 70 | 12 | 57.41 | +4.59 | +8.0% | -2.7% | +18.6% |
| verified_forward | loo_no_city_source_blend | ask_bucket | 0.50-0.75 | 120 | 12 | 76.93 | +6.07 | +7.9% | -7.6% | +22.0% |
| verified_forward | loo_no_city_source_blend | ask_bucket | 0.25-0.50 | 90 | 12 | 33.67 | +2.33 | +6.9% | -28.1% | +39.5% |
| verified_forward | loo_no_city_source_blend | edge_bucket | 0.10+ | 38 | 10 | 9.16 | +2.84 | +31.0% | -23.9% | +85.2% |
| verified_forward | loo_no_city_source_blend | edge_bucket | 0.05-0.10 | 125 | 12 | 55.17 | +8.83 | +16.0% | -5.3% | +34.3% |
| verified_forward | loo_no_city_source_blend | edge_bucket | 0.02-0.05 | 249 | 12 | 116.06 | +2.94 | +2.5% | -12.3% | +16.1% |
| verified_forward | loo_no_city_source_blend | expression | current_yes | 89 | 11 | 33.09 | +5.91 | +17.9% | -15.2% | +43.9% |
| verified_forward | loo_no_city_source_blend | expression | d1_no | 113 | 12 | 53.23 | +8.77 | +16.5% | -0.0% | +30.9% |
| verified_forward | loo_no_city_source_blend | expression | d2_no | 74 | 12 | 47.92 | +1.08 | +2.3% | -11.6% | +15.9% |
| verified_forward | loo_no_city_source_blend | expression | current_no | 136 | 12 | 46.16 | -1.16 | -2.5% | -28.9% | +25.2% |
| verified_forward | loo_no_city_source_blend | intraday_state | pullback_uncertain | 13 | 7 | 5.62 | +1.38 | +24.6% | -9.7% | +96.6% |
| verified_forward | loo_no_city_source_blend | intraday_state | fresh_high | 107 | 11 | 43.73 | +9.27 | +21.2% | -7.8% | +41.8% |
| verified_forward | loo_no_city_source_blend | intraday_state | mature_fade | 21 | 8 | 11.92 | +2.08 | +17.4% | -46.0% | +41.0% |
| verified_forward | loo_no_city_source_blend | intraday_state | false_fade_risk | 28 | 11 | 11.56 | +1.44 | +12.4% | -20.3% | +63.1% |
| verified_forward | loo_no_city_source_blend | intraday_state | active_warming | 227 | 12 | 99.98 | +4.02 | +4.0% | -10.2% | +14.6% |
| verified_forward | loo_no_city_source_blend | intraday_state | plateau_near_high | 9 | 4 | 3.59 | -0.59 | -16.5% | -100.0% | +29.6% |
| verified_forward | loo_no_city_source_blend | intraday_state | reheating_after_dip | 7 | 5 | 3.99 | -2.99 | -74.9% | -100.0% | -21.1% |
| verified_forward | loo_no_city_source_blend | running_max_state | fresh_running_high | 287 | 12 | 120.46 | +12.54 | +10.4% | -8.5% | +22.3% |
| verified_forward | loo_no_city_source_blend | running_max_state | mature_fade | 41 | 10 | 21.53 | +1.47 | +6.8% | -36.1% | +37.1% |
| verified_forward | loo_no_city_source_blend | running_max_state | near_high_plateau | 52 | 5 | 25.33 | +0.67 | +2.7% | -15.4% | +21.3% |
| verified_forward | loo_no_city_source_blend | running_max_state | pullback_from_high | 32 | 10 | 13.08 | -0.08 | -0.6% | -25.9% | +32.5% |
| verified_forward | mkt_city_source_blend | ask_bucket | 0.50-0.75 | 129 | 12 | 82.77 | +9.23 | +11.2% | -5.4% | +25.3% |
| verified_forward | mkt_city_source_blend | ask_bucket | 0.25-0.50 | 101 | 11 | 37.95 | +2.05 | +5.4% | -27.1% | +34.8% |
| verified_forward | mkt_city_source_blend | ask_bucket | 0.75-1.00 | 86 | 12 | 70.47 | +2.53 | +3.6% | -6.4% | +13.5% |
| verified_forward | mkt_city_source_blend | ask_bucket | 0-0.25 | 135 | 10 | 13.43 | -0.43 | -3.2% | -51.8% | +44.9% |
| verified_forward | mkt_city_source_blend | edge_bucket | 0.05-0.10 | 133 | 12 | 58.37 | +12.63 | +21.6% | -3.0% | +43.5% |
| verified_forward | mkt_city_source_blend | edge_bucket | 0.10+ | 39 | 10 | 8.57 | +1.43 | +16.6% | -34.7% | +78.5% |
| verified_forward | mkt_city_source_blend | edge_bucket | 0.02-0.05 | 279 | 12 | 137.66 | -0.66 | -0.5% | -14.0% | +14.4% |
| verified_forward | mkt_city_source_blend | expression | d2_no | 83 | 12 | 52.91 | +6.09 | +11.5% | -2.6% | +25.7% |
| verified_forward | mkt_city_source_blend | expression | current_yes | 98 | 11 | 39.67 | +4.33 | +10.9% | -17.8% | +37.7% |
| verified_forward | mkt_city_source_blend | expression | d1_no | 137 | 12 | 68.31 | +5.69 | +8.3% | -11.9% | +28.5% |
| verified_forward | mkt_city_source_blend | expression | current_no | 133 | 11 | 43.71 | -2.71 | -6.2% | -32.9% | +22.1% |
| verified_forward | mkt_city_source_blend | intraday_state | pullback_uncertain | 15 | 7 | 6.80 | +2.20 | +32.3% | -1.9% | +98.8% |
| verified_forward | mkt_city_source_blend | intraday_state | false_fade_risk | 22 | 11 | 9.35 | +2.65 | +28.3% | -5.1% | +78.7% |
| verified_forward | mkt_city_source_blend | intraday_state | fresh_high | 125 | 12 | 52.06 | +5.94 | +11.4% | -10.9% | +28.2% |
| verified_forward | mkt_city_source_blend | intraday_state | active_warming | 250 | 12 | 115.69 | +5.31 | +4.6% | -11.4% | +19.5% |
| verified_forward | mkt_city_source_blend | intraday_state | mature_fade | 21 | 8 | 12.49 | +0.51 | +4.0% | -65.6% | +42.1% |
| verified_forward | mkt_city_source_blend | intraday_state | plateau_near_high | 10 | 3 | 4.20 | -0.20 | -4.7% | -100.0% | +42.9% |
| verified_forward | mkt_city_source_blend | intraday_state | reheating_after_dip | 8 | 5 | 4.01 | -3.01 | -75.1% | -100.0% | -22.4% |
| verified_forward | mkt_city_source_blend | running_max_state | pullback_from_high | 29 | 10 | 12.90 | +1.10 | +8.5% | -12.4% | +34.9% |
| verified_forward | mkt_city_source_blend | running_max_state | fresh_running_high | 322 | 12 | 140.99 | +10.01 | +7.1% | -11.2% | +21.8% |
| verified_forward | mkt_city_source_blend | running_max_state | near_high_plateau | 58 | 5 | 29.21 | +1.79 | +6.1% | -13.2% | +32.3% |
| verified_forward | mkt_city_source_blend | running_max_state | mature_fade | 42 | 11 | 21.51 | +0.49 | +2.3% | -43.1% | +38.0% |
| verified_forward | mkt_regime_blend | ask_bucket | 0-0.25 | 132 | 11 | 12.38 | +1.62 | +13.0% | -34.6% | +71.0% |
| verified_forward | mkt_regime_blend | ask_bucket | 0.75-1.00 | 70 | 12 | 57.41 | +4.59 | +8.0% | -2.7% | +18.6% |
| verified_forward | mkt_regime_blend | ask_bucket | 0.50-0.75 | 120 | 12 | 76.93 | +6.07 | +7.9% | -7.6% | +22.0% |
| verified_forward | mkt_regime_blend | ask_bucket | 0.25-0.50 | 90 | 12 | 33.67 | +2.33 | +6.9% | -28.1% | +39.5% |
| verified_forward | mkt_regime_blend | edge_bucket | 0.10+ | 38 | 10 | 9.16 | +2.84 | +31.0% | -23.9% | +85.2% |
| verified_forward | mkt_regime_blend | edge_bucket | 0.05-0.10 | 125 | 12 | 55.17 | +8.83 | +16.0% | -5.3% | +34.3% |
| verified_forward | mkt_regime_blend | edge_bucket | 0.02-0.05 | 249 | 12 | 116.06 | +2.94 | +2.5% | -12.3% | +16.1% |
| verified_forward | mkt_regime_blend | expression | current_yes | 89 | 11 | 33.09 | +5.91 | +17.9% | -15.2% | +43.9% |
| verified_forward | mkt_regime_blend | expression | d1_no | 113 | 12 | 53.23 | +8.77 | +16.5% | -0.0% | +30.9% |
| verified_forward | mkt_regime_blend | expression | d2_no | 74 | 12 | 47.92 | +1.08 | +2.3% | -11.6% | +15.9% |
| verified_forward | mkt_regime_blend | expression | current_no | 136 | 12 | 46.16 | -1.16 | -2.5% | -28.9% | +25.2% |
| verified_forward | mkt_regime_blend | intraday_state | pullback_uncertain | 13 | 7 | 5.62 | +1.38 | +24.6% | -9.7% | +96.6% |
| verified_forward | mkt_regime_blend | intraday_state | fresh_high | 107 | 11 | 43.73 | +9.27 | +21.2% | -7.8% | +41.8% |
| verified_forward | mkt_regime_blend | intraday_state | mature_fade | 21 | 8 | 11.92 | +2.08 | +17.4% | -46.0% | +41.0% |
| verified_forward | mkt_regime_blend | intraday_state | false_fade_risk | 28 | 11 | 11.56 | +1.44 | +12.4% | -20.3% | +63.1% |
| verified_forward | mkt_regime_blend | intraday_state | active_warming | 227 | 12 | 99.98 | +4.02 | +4.0% | -10.2% | +14.6% |
| verified_forward | mkt_regime_blend | intraday_state | plateau_near_high | 9 | 4 | 3.59 | -0.59 | -16.5% | -100.0% | +29.6% |
| verified_forward | mkt_regime_blend | intraday_state | reheating_after_dip | 7 | 5 | 3.99 | -2.99 | -74.9% | -100.0% | -21.1% |
| verified_forward | mkt_regime_blend | running_max_state | fresh_running_high | 287 | 12 | 120.46 | +12.54 | +10.4% | -8.5% | +22.3% |
| verified_forward | mkt_regime_blend | running_max_state | mature_fade | 41 | 10 | 21.53 | +1.47 | +6.8% | -36.1% | +37.1% |
| verified_forward | mkt_regime_blend | running_max_state | near_high_plateau | 52 | 5 | 25.33 | +0.67 | +2.7% | -15.4% | +21.3% |
| verified_forward | mkt_regime_blend | running_max_state | pullback_from_high | 32 | 10 | 13.08 | -0.08 | -0.6% | -25.9% | +32.5% |

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
