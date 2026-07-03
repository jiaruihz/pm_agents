# Tmax Distribution P5 Walk-Forward Execution Replay v1

> generated_at_utc: `2026-07-03T16:23:56+00:00`
> Scope: research-only walk-forward execution replay; no live runner/order behavior changed.

## 结论

- P5 固定 P3/P4 的分布模型，不新增天气 gate；交易表达由 `P(win) - ask` 决定，每个 city-date-hour 只保留最高 edge 的一个表达。
- 这一步把问题从“模型 logloss 是否赢盘口”推进到“真实 ask 下，概率优势能不能稳定落地”。
- verified 6/21-6/26 最好的一组是 `loo_no_city_source_blend`：475 rows，ROI +13.5%，CI [+3.4%, +23.4%]。
- extension 6/27-6/29 最好的一组是 `market_local_norm`：13 rows，ROI +21.9%，CI [-6.4%, +194.1%]。
- 结论：`inconclusive_positive_signal`。概率模型方向仍有 edge 痕迹，但 extension 只有 3 天，EV CI 宽，不能 live。
- 旧样本 dev-CV 最偏好的 `edge>=0.10` 在 verified forward 仍很强，但在 6/27-6/29 extension 明显变薄；这说明高 edge 排序有信号，但 recent 压力测试还没过。

## Funnel / Evidence

- P4 scored rows: `7005`; date range `2026-05-19`..`2026-07-02`; cities `36`。
- raw label sources: `{'settlement_outcomes': 8170, 'missing': 3960, 'observed_max_derived': 1436, 'unmapped': 9}`。
- skipped observed-derived before 6/27: `991`。
- scopes: `dev_cv` = 6/21 前训练窗内 expanding-CV；`verified_forward` = forward rows backed by `settlement_outcomes`；`extension_forward` = forward rows still using observed-max-derived labels。
- DB inventory: `{'fact_signal_candidates': {'rows': 44655, 'min_date': '2026-05-05', 'max_date': '2026-07-05'}, 'fact_trades': {'rows': 4414, 'min_date': '2026-05-06', 'max_date': '2026-07-01'}, 'settlement_outcomes': {'rows': 26107, 'min_date': '2026-05-04', 'max_date': '2026-07-02'}}`。

## Primary Replay, Edge >= 0.02

| scope | method | selected_rows | dates | cities | avg_rows_per_date | avg_ask | avg_edge | win_rate | cost | pnl | roi | roi_ci_low | roi_ci_high | daily_win_rate | losing_days | worst_day_pnl | worst_day_roi | current_yes_rows | current_no_rows | d1_no_rows | d2_no_rows |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dev_cv | market_local_norm | 50 | 15 | 26 | 3.33 | 0.283 | +0.049 | 34.0% | 14.15 | +2.85 | +20.1% | -24.5% | +58.3% | 40.0% | 9 | -0.98 | -100.0% | 1 | 27 | 19 | 3 |
| dev_cv | loo_no_city_source_blend | 1579 | 19 | 36 | 83.11 | 0.616 | +0.093 | 69.5% | 972.59 | +124.41 | +12.8% | +8.1% | +17.1% | 84.2% | 3 | -6.94 | -15.4% | 598 | 290 | 450 | 241 |
| dev_cv | mkt_regime_blend | 1579 | 19 | 36 | 83.11 | 0.616 | +0.093 | 69.5% | 972.59 | +124.41 | +12.8% | +8.1% | +17.1% | 84.2% | 3 | -6.94 | -15.4% | 598 | 290 | 450 | 241 |
| dev_cv | loo_no_regime_blend | 1735 | 19 | 36 | 91.32 | 0.623 | +0.104 | 69.2% | 1080.73 | +120.27 | +11.1% | +7.0% | +15.2% | 84.2% | 3 | -5.29 | -11.0% | 682 | 273 | 496 | 284 |
| dev_cv | mkt_city_source_blend | 1852 | 19 | 36 | 97.47 | 0.616 | +0.107 | 68.2% | 1141.05 | +121.95 | +10.7% | +6.8% | +14.0% | 84.2% | 3 | -7.61 | -15.3% | 702 | 321 | 515 | 314 |
| dev_cv | market_recal_blend | 1081 | 19 | 36 | 56.89 | 0.576 | +0.049 | 61.5% | 622.71 | +42.29 | +6.8% | +1.1% | +12.3% | 68.4% | 6 | -5.26 | -14.5% | 583 | 12 | 341 | 145 |
| extension_forward | market_local_norm | 13 | 3 | 10 | 4.33 | 0.315 | +0.060 | 38.5% | 4.10 | +0.90 | +21.9% | -6.4% | +194.1% | 66.7% | 1 | -0.21 | -6.4% | 0 | 6 | 7 | 0 |
| extension_forward | mkt_city_source_blend | 248 | 6 | 36 | 41.33 | 0.512 | +0.145 | 54.0% | 126.98 | +7.02 | +5.5% | -2.9% | +25.2% | 66.7% | 2 | -1.43 | -3.6% | 73 | 53 | 86 | 36 |
| extension_forward | loo_no_regime_blend | 250 | 6 | 36 | 41.67 | 0.495 | +0.143 | 52.0% | 123.69 | +6.31 | +5.1% | -5.6% | +26.6% | 83.3% | 1 | -3.18 | -26.1% | 84 | 49 | 83 | 34 |
| extension_forward | loo_no_city_source_blend | 208 | 6 | 36 | 34.67 | 0.537 | +0.128 | 55.3% | 111.64 | +3.36 | +3.0% | -17.6% | +29.0% | 66.7% | 2 | -4.94 | -49.7% | 58 | 38 | 75 | 37 |
| extension_forward | mkt_regime_blend | 208 | 6 | 36 | 34.67 | 0.537 | +0.128 | 55.3% | 111.64 | +3.36 | +3.0% | -17.6% | +29.0% | 66.7% | 2 | -4.94 | -49.7% | 58 | 38 | 75 | 37 |
| extension_forward | market_recal_blend | 135 | 5 | 34 | 27.00 | 0.545 | +0.048 | 55.6% | 73.63 | +1.37 | +1.9% | -7.4% | +18.1% | 80.0% | 1 | -1.81 | -23.2% | 51 | 2 | 52 | 30 |
| verified_forward | loo_no_city_source_blend | 475 | 8 | 36 | 59.38 | 0.530 | +0.095 | 60.2% | 251.89 | +34.11 | +13.5% | +3.4% | +23.4% | 87.5% | 1 | -2.40 | -5.9% | 174 | 118 | 138 | 45 |
| verified_forward | mkt_regime_blend | 475 | 8 | 36 | 59.38 | 0.530 | +0.095 | 60.2% | 251.89 | +34.11 | +13.5% | +3.4% | +23.4% | 87.5% | 1 | -2.40 | -5.9% | 174 | 118 | 138 | 45 |
| verified_forward | mkt_city_source_blend | 593 | 8 | 36 | 74.12 | 0.530 | +0.105 | 59.2% | 314.06 | +36.94 | +11.8% | +5.9% | +18.3% | 87.5% | 1 | -0.99 | -14.2% | 221 | 139 | 168 | 65 |
| verified_forward | loo_no_regime_blend | 563 | 8 | 36 | 70.38 | 0.509 | +0.103 | 56.5% | 286.64 | +31.36 | +10.9% | +6.4% | +15.9% | 87.5% | 1 | -1.58 | -28.3% | 243 | 108 | 158 | 54 |
| verified_forward | market_recal_blend | 303 | 8 | 35 | 37.88 | 0.514 | +0.066 | 53.1% | 155.62 | +5.38 | +3.5% | -8.2% | +17.0% | 62.5% | 3 | -3.93 | -13.1% | 141 | 9 | 107 | 46 |
| verified_forward | market_local_norm | 56 | 8 | 27 | 7.00 | 0.193 | +0.128 | 17.9% | 10.79 | -0.79 | -7.3% | -56.0% | +70.4% | 50.0% | 4 | -2.02 | -66.9% | 1 | 23 | 30 | 2 |

## Dev-CV Policy Ranking

这个表只用 6/21 前 expanding-CV 排序，目的是看如果先在旧样本里选方法/threshold，forward 是否同号。不是 live 选择器。

| method | edge_threshold | selected_rows | dates | cities | avg_ask | avg_edge | win_rate | roi | roi_ci_low | roi_ci_high | daily_win_rate | losing_days |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| loo_no_city_source_blend | 0.1 | 435 | 19 | 36 | 0.535 | +0.206 | 74.0% | +38.3% | +24.9% | +50.0% | 89.5% | 2 |
| mkt_regime_blend | 0.1 | 435 | 19 | 36 | 0.535 | +0.206 | 74.0% | +38.3% | +24.9% | +50.0% | 89.5% | 2 |
| loo_no_regime_blend | 0.1 | 554 | 19 | 36 | 0.582 | +0.218 | 76.9% | +32.1% | +23.1% | +42.4% | 84.2% | 3 |
| mkt_city_source_blend | 0.1 | 628 | 19 | 36 | 0.570 | +0.216 | 72.9% | +27.9% | +18.3% | +36.9% | 84.2% | 3 |
| loo_no_city_source_blend | 0.05 | 954 | 19 | 36 | 0.588 | +0.132 | 70.0% | +19.1% | +12.6% | +25.2% | 84.2% | 3 |
| mkt_regime_blend | 0.05 | 954 | 19 | 36 | 0.588 | +0.132 | 70.0% | +19.1% | +12.6% | +25.2% | 84.2% | 3 |
| loo_no_regime_blend | 0.05 | 1064 | 19 | 36 | 0.614 | +0.148 | 72.0% | +17.2% | +11.9% | +22.9% | 84.2% | 3 |
| mkt_city_source_blend | 0.05 | 1195 | 19 | 36 | 0.608 | +0.147 | 70.6% | +16.1% | +9.8% | +21.9% | 84.2% | 3 |

## Dev-Selected Threshold Forward Check

dev-CV 排名前几名偏向 `edge>=0.10`。如果把这个阈值冻结到未来，verified 仍强，但 extension 变薄，所以不能把旧样本最优阈值直接当 live 参数。

| scope | method | edge_threshold | selected_rows | dates | cities | avg_ask | avg_edge | win_rate | cost | pnl | roi | roi_ci_low | roi_ci_high | daily_win_rate | losing_days | worst_day_pnl | worst_day_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| extension_forward | loo_no_city_source_blend | 0.05 | 148 | 5 | 35 | 0.508 | +0.167 | 54.1% | 75.19 | +4.81 | +6.4% | -16.3% | +46.8% | 80.0% | 1 | -3.32 | -62.4% |
| extension_forward | mkt_regime_blend | 0.05 | 148 | 5 | 35 | 0.508 | +0.167 | 54.1% | 75.19 | +4.81 | +6.4% | -16.3% | +46.8% | 80.0% | 1 | -3.32 | -62.4% |
| extension_forward | mkt_city_source_blend | 0.05 | 187 | 5 | 36 | 0.501 | +0.182 | 52.9% | 93.75 | +5.25 | +5.6% | -6.6% | +40.7% | 60.0% | 2 | -2.42 | -8.0% |
| extension_forward | loo_no_regime_blend | 0.05 | 173 | 5 | 35 | 0.498 | +0.192 | 51.4% | 86.20 | +2.79 | +3.2% | -6.9% | +38.9% | 40.0% | 3 | -1.51 | -20.1% |
| verified_forward | loo_no_city_source_blend | 0.05 | 286 | 8 | 36 | 0.524 | +0.136 | 67.5% | 149.97 | +43.03 | +28.7% | +17.3% | +40.3% | 87.5% | 1 | -0.44 | -17.9% |
| verified_forward | mkt_regime_blend | 0.05 | 286 | 8 | 36 | 0.524 | +0.136 | 67.5% | 149.97 | +43.03 | +28.7% | +17.3% | +40.3% | 87.5% | 1 | -0.44 | -17.9% |
| verified_forward | mkt_city_source_blend | 0.05 | 386 | 8 | 36 | 0.527 | +0.143 | 61.9% | 203.36 | +35.64 | +17.5% | +9.7% | +26.7% | 87.5% | 1 | -1.87 | -38.4% |
| verified_forward | loo_no_regime_blend | 0.05 | 346 | 8 | 36 | 0.525 | +0.146 | 61.6% | 181.68 | +31.32 | +17.2% | +9.3% | +25.9% | 87.5% | 1 | -0.97 | -32.7% |
| extension_forward | mkt_city_source_blend | 0.1 | 117 | 5 | 30 | 0.474 | +0.247 | 51.3% | 55.43 | +4.57 | +8.2% | -6.1% | +55.7% | 60.0% | 2 | -1.84 | -6.4% |
| extension_forward | loo_no_city_source_blend | 0.1 | 89 | 5 | 24 | 0.431 | +0.229 | 46.1% | 38.38 | +2.62 | +6.8% | -21.8% | +63.9% | 60.0% | 2 | -2.02 | -100.0% |
| extension_forward | mkt_regime_blend | 0.1 | 89 | 5 | 24 | 0.431 | +0.229 | 46.1% | 38.38 | +2.62 | +6.8% | -21.8% | +63.9% | 60.0% | 2 | -2.02 | -100.0% |
| extension_forward | loo_no_regime_blend | 0.1 | 116 | 5 | 26 | 0.469 | +0.252 | 49.1% | 54.39 | +2.61 | +4.8% | -7.9% | +46.5% | 60.0% | 2 | -2.09 | -7.4% |
| verified_forward | loo_no_city_source_blend | 0.1 | 129 | 8 | 33 | 0.458 | +0.215 | 68.2% | 59.03 | +28.97 | +49.1% | +35.9% | +59.2% | 87.5% | 1 | -0.01 | -0.6% |
| verified_forward | mkt_regime_blend | 0.1 | 129 | 8 | 33 | 0.458 | +0.215 | 68.2% | 59.03 | +28.97 | +49.1% | +35.9% | +59.2% | 87.5% | 1 | -0.01 | -0.6% |
| verified_forward | loo_no_regime_blend | 0.1 | 166 | 8 | 35 | 0.498 | +0.227 | 65.7% | 82.71 | +26.29 | +31.8% | +15.2% | +49.7% | 87.5% | 1 | -0.88 | -100.0% |
| verified_forward | mkt_city_source_blend | 0.1 | 189 | 8 | 36 | 0.499 | +0.215 | 65.1% | 94.28 | +28.72 | +30.5% | +15.9% | +46.0% | 87.5% | 1 | -1.07 | -100.0% |

## Worst Forward Days

| scope | method | target_date | rows | cities | cost | pnl | roi | current_yes_rows | current_no_rows | d1_no_rows | d2_no_rows |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| extension_forward | mkt_regime_blend | 2026-06-28 | 16 | 7 | 9.94 | -4.94 | -49.7% | 7 | 1 | 4 | 4 |
| extension_forward | loo_no_city_source_blend | 2026-06-28 | 16 | 7 | 9.94 | -4.94 | -49.7% | 7 | 1 | 4 | 4 |
| verified_forward | loo_no_city_source_blend | 2026-06-21 | 75 | 30 | 40.40 | -2.40 | -5.9% | 29 | 18 | 21 | 7 |
| verified_forward | mkt_regime_blend | 2026-06-21 | 75 | 30 | 40.40 | -2.40 | -5.9% | 29 | 18 | 21 | 7 |
| extension_forward | mkt_city_source_blend | 2026-07-01 | 80 | 28 | 39.43 | -1.43 | -3.6% | 13 | 28 | 27 | 12 |
| extension_forward | loo_no_city_source_blend | 2026-07-01 | 60 | 25 | 33.24 | -1.24 | -3.7% | 10 | 19 | 19 | 12 |
| extension_forward | mkt_regime_blend | 2026-07-01 | 60 | 25 | 33.24 | -1.24 | -3.7% | 10 | 19 | 19 | 12 |
| verified_forward | mkt_city_source_blend | 2026-06-29 | 20 | 9 | 6.99 | -0.99 | -14.2% | 5 | 12 | 2 | 1 |
| extension_forward | mkt_city_source_blend | 2026-06-28 | 14 | 9 | 8.95 | -0.95 | -10.6% | 6 | 3 | 3 | 2 |
| extension_forward | mkt_city_source_blend | 2026-07-02 | 1 | 1 | 0.95 | +0.05 | +5.3% | 0 | 0 | 0 | 1 |
| extension_forward | loo_no_city_source_blend | 2026-07-02 | 1 | 1 | 0.95 | +0.05 | +5.3% | 0 | 0 | 0 | 1 |
| extension_forward | mkt_regime_blend | 2026-07-02 | 1 | 1 | 0.95 | +0.05 | +5.3% | 0 | 0 | 0 | 1 |

## Diagnostic Slices

以下是诊断切片，不是 hard gate。用于看 EV 来自哪里、坏在哪里。

| scope | method | slice | value | rows | dates | cost | pnl | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| extension_forward | loo_no_city_source_blend | ask_bucket | 0.25-0.50 | 41 | 5 | 15.78 | +3.22 | +20.4% | -48.9% | +100.9% |
| extension_forward | loo_no_city_source_blend | ask_bucket | 0.50-0.75 | 66 | 5 | 41.94 | +3.06 | +7.3% | -35.1% | +21.0% |
| extension_forward | loo_no_city_source_blend | ask_bucket | 0.75-1.00 | 60 | 6 | 50.78 | -1.78 | -3.5% | -6.0% | +6.2% |
| extension_forward | loo_no_city_source_blend | ask_bucket | 0-0.25 | 41 | 5 | 3.13 | -1.13 | -36.2% | -100.0% | -2.1% |
| extension_forward | loo_no_city_source_blend | edge_bucket | 0.10+ | 89 | 5 | 38.38 | +2.62 | +6.8% | -21.8% | +63.9% |
| extension_forward | loo_no_city_source_blend | edge_bucket | 0.05-0.10 | 59 | 4 | 36.80 | +2.20 | +6.0% | -13.5% | +27.1% |
| extension_forward | loo_no_city_source_blend | edge_bucket | 0.02-0.05 | 60 | 6 | 36.45 | -1.45 | -4.0% | -27.6% | +10.7% |
| extension_forward | loo_no_city_source_blend | expression | d1_no | 75 | 5 | 46.13 | +7.87 | +17.1% | -9.1% | +64.9% |
| extension_forward | loo_no_city_source_blend | expression | current_no | 38 | 4 | 17.90 | +2.10 | +11.7% | -24.3% | +37.0% |
| extension_forward | loo_no_city_source_blend | expression | d2_no | 37 | 6 | 23.85 | +2.15 | +9.0% | -9.9% | +29.6% |
| extension_forward | loo_no_city_source_blend | expression | current_yes | 58 | 5 | 23.76 | -8.76 | -36.9% | -74.0% | +15.3% |
| extension_forward | loo_no_city_source_blend | intraday_state | false_fade_risk | 15 | 4 | 7.62 | +2.38 | +31.2% | +4.1% | +92.1% |
| extension_forward | loo_no_city_source_blend | intraday_state | plateau_near_high | 4 | 2 | 2.38 | +0.62 | +26.1% | n/a | n/a |
| extension_forward | loo_no_city_source_blend | intraday_state | mature_fade | 7 | 4 | 4.56 | +0.44 | +9.6% | -18.9% | +56.7% |
| extension_forward | loo_no_city_source_blend | intraday_state | active_warming | 111 | 6 | 58.87 | +3.13 | +5.3% | -7.1% | +40.0% |
| extension_forward | loo_no_city_source_blend | intraday_state | pullback_uncertain | 4 | 2 | 3.04 | -0.04 | -1.3% | n/a | n/a |
| extension_forward | loo_no_city_source_blend | intraday_state | fresh_high | 62 | 4 | 32.88 | -1.88 | -5.7% | -53.4% | +23.9% |
| extension_forward | loo_no_city_source_blend | intraday_state | reheating_after_dip | 5 | 2 | 2.28 | -1.28 | -56.2% | n/a | n/a |
| extension_forward | loo_no_city_source_blend | running_max_state | pullback_from_high | 12 | 4 | 6.62 | +1.38 | +20.9% | -13.6% | +97.6% |
| extension_forward | loo_no_city_source_blend | running_max_state | near_high_plateau | 33 | 4 | 18.83 | +2.17 | +11.5% | -16.5% | +33.0% |
| extension_forward | loo_no_city_source_blend | running_max_state | mature_fade | 21 | 4 | 11.46 | +0.54 | +4.7% | -23.8% | +39.9% |
| extension_forward | loo_no_city_source_blend | running_max_state | fresh_running_high | 142 | 5 | 74.73 | -0.73 | -1.0% | -29.1% | +33.0% |
| extension_forward | mkt_city_source_blend | ask_bucket | 0.25-0.50 | 45 | 5 | 17.47 | +4.53 | +25.9% | -25.0% | +108.3% |
| extension_forward | mkt_city_source_blend | ask_bucket | 0.50-0.75 | 68 | 5 | 44.16 | +6.84 | +15.5% | +3.5% | +20.1% |
| extension_forward | mkt_city_source_blend | ask_bucket | 0-0.25 | 64 | 5 | 4.60 | +0.40 | +8.8% | -100.0% | +47.0% |
| extension_forward | mkt_city_source_blend | ask_bucket | 0.75-1.00 | 71 | 6 | 60.75 | -4.75 | -7.8% | -17.7% | +1.6% |
| extension_forward | mkt_city_source_blend | edge_bucket | 0.10+ | 117 | 5 | 55.43 | +4.57 | +8.2% | -6.1% | +55.7% |
| extension_forward | mkt_city_source_blend | edge_bucket | 0.02-0.05 | 61 | 5 | 33.22 | +1.78 | +5.3% | -22.2% | +11.2% |
| extension_forward | mkt_city_source_blend | edge_bucket | 0.05-0.10 | 70 | 5 | 38.32 | +0.68 | +1.8% | -18.3% | +22.2% |
| extension_forward | mkt_city_source_blend | expression | current_no | 53 | 4 | 20.50 | +4.50 | +21.9% | -5.2% | +49.9% |
| extension_forward | mkt_city_source_blend | expression | d1_no | 86 | 5 | 54.04 | +10.96 | +20.3% | +1.6% | +78.9% |
| extension_forward | mkt_city_source_blend | expression | d2_no | 36 | 6 | 24.45 | +1.55 | +6.3% | -13.2% | +35.0% |
| extension_forward | mkt_city_source_blend | expression | current_yes | 73 | 5 | 27.98 | -9.98 | -35.7% | -65.5% | +3.0% |
| extension_forward | mkt_city_source_blend | intraday_state | plateau_near_high | 3 | 1 | 2.10 | +0.90 | +42.9% | n/a | n/a |
| extension_forward | mkt_city_source_blend | intraday_state | false_fade_risk | 17 | 4 | 8.32 | +2.68 | +32.3% | +5.4% | +75.6% |
| extension_forward | mkt_city_source_blend | intraday_state | fresh_high | 72 | 4 | 36.40 | +5.60 | +15.4% | -12.2% | +43.0% |
| extension_forward | mkt_city_source_blend | intraday_state | pullback_uncertain | 7 | 2 | 4.43 | +0.57 | +12.9% | n/a | n/a |
| extension_forward | mkt_city_source_blend | intraday_state | mature_fade | 10 | 4 | 5.98 | +0.02 | +0.4% | -51.7% | +83.8% |
| extension_forward | mkt_city_source_blend | intraday_state | active_warming | 132 | 6 | 67.76 | -1.76 | -2.6% | -9.4% | +25.0% |
| extension_forward | mkt_city_source_blend | intraday_state | reheating_after_dip | 6 | 2 | 1.99 | -0.99 | -49.7% | n/a | n/a |
| extension_forward | mkt_city_source_blend | intraday_state | state_unknown | 1 | 1 | 0.01 | -0.01 | -100.0% | n/a | n/a |
| extension_forward | mkt_city_source_blend | running_max_state | mature_fade | 25 | 4 | 12.39 | +1.61 | +13.0% | -11.6% | +22.3% |
| extension_forward | mkt_city_source_blend | running_max_state | pullback_from_high | 17 | 4 | 8.90 | +1.10 | +12.4% | -39.1% | +66.1% |
| extension_forward | mkt_city_source_blend | running_max_state | near_high_plateau | 43 | 4 | 22.97 | +1.03 | +4.5% | -6.2% | +24.4% |
| extension_forward | mkt_city_source_blend | running_max_state | fresh_running_high | 162 | 5 | 82.71 | +3.29 | +4.0% | -2.1% | +32.5% |
| extension_forward | mkt_city_source_blend | running_max_state | stalled_high | 1 | 1 | 0.01 | -0.01 | -100.0% | n/a | n/a |
| extension_forward | mkt_regime_blend | ask_bucket | 0.25-0.50 | 41 | 5 | 15.78 | +3.22 | +20.4% | -48.9% | +100.9% |
| extension_forward | mkt_regime_blend | ask_bucket | 0.50-0.75 | 66 | 5 | 41.94 | +3.06 | +7.3% | -35.1% | +21.0% |
| extension_forward | mkt_regime_blend | ask_bucket | 0.75-1.00 | 60 | 6 | 50.78 | -1.78 | -3.5% | -6.0% | +6.2% |
| extension_forward | mkt_regime_blend | ask_bucket | 0-0.25 | 41 | 5 | 3.13 | -1.13 | -36.2% | -100.0% | -2.1% |
| extension_forward | mkt_regime_blend | edge_bucket | 0.10+ | 89 | 5 | 38.38 | +2.62 | +6.8% | -21.8% | +63.9% |
| extension_forward | mkt_regime_blend | edge_bucket | 0.05-0.10 | 59 | 4 | 36.80 | +2.20 | +6.0% | -13.5% | +27.1% |
| extension_forward | mkt_regime_blend | edge_bucket | 0.02-0.05 | 60 | 6 | 36.45 | -1.45 | -4.0% | -27.6% | +10.7% |
| extension_forward | mkt_regime_blend | expression | d1_no | 75 | 5 | 46.13 | +7.87 | +17.1% | -9.1% | +64.9% |
| extension_forward | mkt_regime_blend | expression | current_no | 38 | 4 | 17.90 | +2.10 | +11.7% | -24.3% | +37.0% |
| extension_forward | mkt_regime_blend | expression | d2_no | 37 | 6 | 23.85 | +2.15 | +9.0% | -9.9% | +29.6% |
| extension_forward | mkt_regime_blend | expression | current_yes | 58 | 5 | 23.76 | -8.76 | -36.9% | -74.0% | +15.3% |
| extension_forward | mkt_regime_blend | intraday_state | false_fade_risk | 15 | 4 | 7.62 | +2.38 | +31.2% | +4.1% | +92.1% |
| extension_forward | mkt_regime_blend | intraday_state | plateau_near_high | 4 | 2 | 2.38 | +0.62 | +26.1% | n/a | n/a |
| extension_forward | mkt_regime_blend | intraday_state | mature_fade | 7 | 4 | 4.56 | +0.44 | +9.6% | -18.9% | +56.7% |
| extension_forward | mkt_regime_blend | intraday_state | active_warming | 111 | 6 | 58.87 | +3.13 | +5.3% | -7.1% | +40.0% |
| extension_forward | mkt_regime_blend | intraday_state | pullback_uncertain | 4 | 2 | 3.04 | -0.04 | -1.3% | n/a | n/a |
| extension_forward | mkt_regime_blend | intraday_state | fresh_high | 62 | 4 | 32.88 | -1.88 | -5.7% | -53.4% | +23.9% |
| extension_forward | mkt_regime_blend | intraday_state | reheating_after_dip | 5 | 2 | 2.28 | -1.28 | -56.2% | n/a | n/a |
| extension_forward | mkt_regime_blend | running_max_state | pullback_from_high | 12 | 4 | 6.62 | +1.38 | +20.9% | -13.6% | +97.6% |
| extension_forward | mkt_regime_blend | running_max_state | near_high_plateau | 33 | 4 | 18.83 | +2.17 | +11.5% | -16.5% | +33.0% |
| extension_forward | mkt_regime_blend | running_max_state | mature_fade | 21 | 4 | 11.46 | +0.54 | +4.7% | -23.8% | +39.9% |
| extension_forward | mkt_regime_blend | running_max_state | fresh_running_high | 142 | 5 | 74.73 | -0.73 | -1.0% | -29.1% | +33.0% |
| verified_forward | loo_no_city_source_blend | ask_bucket | 0-0.25 | 122 | 8 | 9.81 | +4.19 | +42.7% | -21.8% | +110.2% |
| verified_forward | loo_no_city_source_blend | ask_bucket | 0.25-0.50 | 78 | 8 | 30.18 | +11.82 | +39.2% | +9.5% | +68.4% |
| verified_forward | loo_no_city_source_blend | ask_bucket | 0.75-1.00 | 159 | 8 | 137.46 | +12.54 | +9.1% | +6.6% | +11.6% |
| verified_forward | loo_no_city_source_blend | ask_bucket | 0.50-0.75 | 116 | 8 | 74.44 | +5.56 | +7.5% | -14.6% | +26.7% |
| verified_forward | loo_no_city_source_blend | edge_bucket | 0.10+ | 129 | 8 | 59.03 | +28.97 | +49.1% | +35.9% | +59.2% |
| verified_forward | loo_no_city_source_blend | edge_bucket | 0.05-0.10 | 157 | 8 | 90.94 | +14.06 | +15.5% | +4.1% | +28.5% |
| verified_forward | loo_no_city_source_blend | edge_bucket | 0.02-0.05 | 189 | 8 | 101.92 | -8.92 | -8.8% | -19.4% | +2.7% |
| verified_forward | loo_no_city_source_blend | expression | current_yes | 174 | 8 | 85.30 | +19.70 | +23.1% | +11.1% | +35.7% |
| verified_forward | loo_no_city_source_blend | expression | d1_no | 138 | 8 | 85.77 | +11.23 | +13.1% | -4.3% | +28.5% |
| verified_forward | loo_no_city_source_blend | expression | current_no | 118 | 8 | 51.36 | +3.64 | +7.1% | -4.5% | +22.6% |
| verified_forward | loo_no_city_source_blend | expression | d2_no | 45 | 8 | 29.46 | -0.46 | -1.6% | -28.6% | +15.0% |
| verified_forward | loo_no_city_source_blend | intraday_state | false_fade_risk | 39 | 8 | 20.40 | +4.60 | +22.5% | -5.8% | +78.5% |

## Model Selection

| spec | c | cv_rows | cv_dates | cv_market_logloss | cv_blend_alpha | cv_blend_logloss |
| --- | --- | --- | --- | --- | --- | --- |
| loo_no_boundary | 0.03 | 3217 | 19 | 0.5932488025858269 | 1.0 | 0.5189586407260325 |
| loo_no_city_source | 0.1 | 3217 | 19 | 0.5932488025858269 | 0.75 | 0.5201861032714495 |
| loo_no_meteo | 0.03 | 3217 | 19 | 0.5932488025858269 | 1.0 | 0.5199384692616691 |
| loo_no_regime | 0.03 | 3217 | 19 | 0.5932488025858269 | 1.0 | 0.518809622612911 |
| market_recal | 0.3 | 3217 | 19 | 0.5932488025858269 | 0.75 | 0.5567601088509119 |
| mkt_boundary | 0.1 | 3217 | 19 | 0.5932488025858269 | 1.0 | 0.5216395207977925 |
| mkt_city_source | 0.03 | 3217 | 19 | 0.5932488025858269 | 1.0 | 0.5193636305622239 |
| mkt_meteo | 0.1 | 3217 | 19 | 0.5932488025858269 | 1.0 | 0.5199818140673043 |
| mkt_path_core | 0.3 | 3217 | 19 | 0.5932488025858269 | 1.0 | 0.5195683583930546 |
| mkt_regime | 0.1 | 3217 | 19 | 0.5932488025858269 | 0.75 | 0.5201861032714495 |

## Verdict

significance=FAIL/THIN; baseline=PARTIAL_PASS; forward=FAIL/THIN; conclusion=`inconclusive_positive_signal`.

下一步需要两件事：第一，补完整 6/27+ official settlement/orderbook depth 后重跑；第二，把 P5 输出接成 zero-notional shadow telemetry，而不是 live 下单。

## Artifacts

- `docs/analysis/2026-07/generated/tmax_distribution_p5_walk_forward_execution_replay_v1/policy_summary.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p5_walk_forward_execution_replay_v1/daily.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p5_walk_forward_execution_replay_v1/diagnostic_slices.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p5_walk_forward_execution_replay_v1/opportunities.csv`
- `docs/analysis/2026-07/2026-07-03-tmax-distribution-p5-walk-forward-execution-replay-v1.json`
