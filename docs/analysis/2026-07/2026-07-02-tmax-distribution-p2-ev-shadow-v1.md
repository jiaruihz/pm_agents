# Tmax Distribution P2 EV Shadow v1

> generated_at_utc: `2026-07-02T15:00:34+00:00`
> Scope: offline EV / telemetry diagnostic only; no live runner/order behavior changed.

## 结论

- P2 没有重新训练模型，也没有在 forward 上挑模型；它只消费 P1 fixed/expanding predictions。
- 执行价格只用 atlas 里的真实 ask：`current_yes_ask`、`current_bracket_no_ask`、`d1_no_ask`、`d2_no_ask`。
- `market_local_norm` 在这里仅是 diagnostic；不能当成交概率或交易 approval，因为它是局部分布归一化 proxy。
- Expanding forward, threshold `model_edge >= 0.02`：`fusion_city_blend` 选出 869 expression rows，ROI +10.6%；`fusion_numeric_blend` 选出 740 rows，ROI +11.0%。
- 更接近 shadow 的一状态一表达 dedupe：`fusion_city_blend` 567 rows，ROI +12.3%。
- Verdict: `inconclusive_ev_shadow_only`。这只能说明哪些表达值得 telemetry，不支持 live。

## Why This Is Still Not Live

- Forward 只有 2026-06-21..2026-06-26 六天。
- P1 的概率增量主要来自 `current` bucket；P2 必须防止 current 表达掩盖 d1/d2 退化。
- Tail 概率仍来自 local residual proxy，不是完整 bracket ladder。
- 本报告没有模拟真实 fill、滑点、订单容量、重复 city-day 限额或资金 sizing。

## Expanding Forward, Edge >= 0.02

| method | expression | rows | dates | avg_ask | avg_edge | win | ROI | CI |
|---|---|---:|---:|---:|---:|---:|---:|---|
| fusion_city_blend | current_no | 134 | 6 | 0.428 | +0.082 | 53.0% | +23.9% | [+4.6%, +40.0%] |
| fusion_city_blend | current_yes | 315 | 6 | 0.522 | +0.121 | 59.4% | +13.7% | [-3.2%, +27.4%] |
| fusion_city_blend | d1_no | 299 | 6 | 0.680 | +0.120 | 74.9% | +10.2% | [+2.3%, +17.6%] |
| fusion_city_blend | d2_no | 121 | 6 | 0.782 | +0.066 | 76.9% | -1.7% | [-6.0%, +0.8%] |
| fusion_context_blend | current_no | 127 | 6 | 0.432 | +0.079 | 55.1% | +27.6% | [+6.8%, +46.1%] |
| fusion_context_blend | current_yes | 303 | 6 | 0.518 | +0.120 | 57.8% | +11.6% | [-6.7%, +26.2%] |
| fusion_context_blend | d1_no | 286 | 6 | 0.680 | +0.122 | 74.8% | +10.0% | [-0.1%, +18.0%] |
| fusion_context_blend | d2_no | 117 | 6 | 0.786 | +0.065 | 77.8% | -1.0% | [-6.7%, +2.7%] |
| fusion_numeric_blend | current_no | 95 | 6 | 0.497 | +0.078 | 60.0% | +20.8% | [+12.8%, +32.8%] |
| fusion_numeric_blend | current_yes | 284 | 6 | 0.558 | +0.136 | 62.0% | +11.0% | [-4.9%, +23.6%] |
| fusion_numeric_blend | d1_no | 260 | 6 | 0.683 | +0.136 | 78.1% | +14.4% | [+4.6%, +21.4%] |
| fusion_numeric_blend | d2_no | 101 | 6 | 0.817 | +0.069 | 80.2% | -1.9% | [-7.8%, +4.8%] |

## Fixed Forward, Edge >= 0.02

| method | expression | rows | dates | avg_ask | avg_edge | win | ROI | CI |
|---|---|---:|---:|---:|---:|---:|---:|---|
| fusion_city_blend | current_no | 112 | 6 | 0.414 | +0.080 | 57.1% | +38.0% | [+22.3%, +49.7%] |
| fusion_city_blend | current_yes | 343 | 6 | 0.497 | +0.116 | 56.3% | +13.1% | [+0.4%, +24.4%] |
| fusion_city_blend | d1_no | 285 | 6 | 0.682 | +0.121 | 75.1% | +10.2% | [+2.5%, +17.3%] |
| fusion_city_blend | d2_no | 131 | 6 | 0.762 | +0.067 | 75.6% | -0.9% | [-6.7%, +3.5%] |
| fusion_context_blend | current_no | 104 | 6 | 0.398 | +0.084 | 52.9% | +32.9% | [+18.9%, +44.0%] |
| fusion_context_blend | current_yes | 329 | 6 | 0.494 | +0.112 | 55.0% | +11.5% | [-3.4%, +24.4%] |
| fusion_context_blend | d1_no | 273 | 6 | 0.676 | +0.122 | 74.7% | +10.5% | [-1.1%, +19.6%] |
| fusion_context_blend | d2_no | 130 | 6 | 0.770 | +0.065 | 76.2% | -1.1% | [-7.6%, +3.4%] |
| fusion_numeric_blend | current_no | 106 | 6 | 0.489 | +0.077 | 58.5% | +19.7% | [+13.4%, +28.0%] |
| fusion_numeric_blend | current_yes | 284 | 6 | 0.544 | +0.126 | 60.2% | +10.6% | [-2.7%, +20.3%] |
| fusion_numeric_blend | d1_no | 238 | 6 | 0.671 | +0.137 | 75.2% | +12.1% | [+0.9%, +21.1%] |
| fusion_numeric_blend | d2_no | 95 | 6 | 0.795 | +0.064 | 83.2% | +4.6% | [+1.9%, +7.8%] |

## Dedupe: Best Expression Per State

| scope | method | rows | dates | cost | pnl | ROI | CI | expression mix |
|---|---|---:|---:|---:|---:|---:|---|---|
| expanding_forward | fusion_city_blend | 567 | 6 | 300.86 | +37.14 | +12.3% | [+6.8%, +19.9%] | YES 221 / currentNO 120 / d1NO 167 / d2NO 59 |
| expanding_forward | fusion_context_blend | 541 | 6 | 285.95 | +35.05 | +12.3% | [+4.7%, +19.7%] | YES 217 / currentNO 114 / d1NO 156 / d2NO 54 |
| expanding_forward | fusion_numeric_blend | 467 | 6 | 258.13 | +27.87 | +10.8% | [+1.7%, +20.7%] | YES 216 / currentNO 81 / d1NO 135 / d2NO 35 |
| fixed_forward | fusion_city_blend | 560 | 6 | 288.37 | +37.63 | +13.0% | [+7.0%, +20.6%] | YES 245 / currentNO 99 / d1NO 154 / d2NO 62 |
| fixed_forward | fusion_context_blend | 548 | 6 | 282.57 | +35.43 | +12.5% | [+4.9%, +19.5%] | YES 235 / currentNO 90 / d1NO 156 / d2NO 67 |
| fixed_forward | fusion_numeric_blend | 476 | 6 | 257.83 | +27.17 | +10.5% | [+2.9%, +18.7%] | YES 216 / currentNO 92 / d1NO 125 / d2NO 43 |

## Dedupe Daily Primary

| scope | date | rows | cities | cost | pnl | ROI |
|---|---|---:|---:|---:|---:|---:|
| expanding_forward | 2026-06-21 | 97 | 29 | 49.85 | +2.15 | +4.3% |
| expanding_forward | 2026-06-22 | 98 | 29 | 54.77 | +14.23 | +26.0% |
| expanding_forward | 2026-06-23 | 71 | 24 | 33.72 | +7.28 | +21.6% |
| expanding_forward | 2026-06-24 | 90 | 25 | 44.76 | +3.24 | +7.2% |
| expanding_forward | 2026-06-25 | 101 | 30 | 55.10 | +3.90 | +7.1% |
| expanding_forward | 2026-06-26 | 110 | 34 | 62.66 | +6.34 | +10.1% |
| fixed_forward | 2026-06-21 | 97 | 29 | 49.85 | +2.15 | +4.3% |
| fixed_forward | 2026-06-22 | 100 | 29 | 59.12 | +14.88 | +25.2% |
| fixed_forward | 2026-06-23 | 74 | 24 | 33.05 | +7.95 | +24.1% |
| fixed_forward | 2026-06-24 | 83 | 25 | 41.36 | +4.64 | +11.2% |
| fixed_forward | 2026-06-25 | 99 | 30 | 51.19 | +3.81 | +7.4% |
| fixed_forward | 2026-06-26 | 107 | 34 | 53.82 | +4.18 | +7.8% |

## Threshold Sweep, Expanding Forward

| method | expression | rows | dates | avg_ask | avg_edge | win | ROI | CI |
|---|---|---:|---:|---:|---:|---:|---:|---|
| fusion_city_blend | current_no | 255 | 6 | 0.341 | +0.046 | 40.0% | +17.1% | [+1.3%, +30.7%] |
| fusion_city_blend | current_yes | 517 | 6 | 0.507 | +0.077 | 55.5% | +9.5% | [-1.0%, +19.1%] |
| fusion_city_blend | d1_no | 417 | 6 | 0.746 | +0.088 | 80.6% | +8.0% | [+2.8%, +14.6%] |
| fusion_city_blend | d2_no | 434 | 6 | 0.921 | +0.021 | 90.6% | -1.6% | [-3.1%, -0.4%] |
| fusion_numeric_blend | current_no | 190 | 6 | 0.429 | +0.042 | 48.9% | +14.2% | [+7.6%, +21.6%] |
| fusion_numeric_blend | current_yes | 500 | 6 | 0.546 | +0.080 | 58.0% | +6.2% | [-3.8%, +13.4%] |
| fusion_numeric_blend | d1_no | 422 | 6 | 0.763 | +0.086 | 82.2% | +7.8% | [+1.2%, +14.0%] |
| fusion_numeric_blend | d2_no | 459 | 6 | 0.935 | +0.018 | 91.7% | -1.9% | [-3.5%, +0.0%] |

| method | expression | rows | dates | avg_ask | avg_edge | win | ROI | CI |
|---|---|---:|---:|---:|---:|---:|---:|---|
| fusion_city_blend | current_no | 79 | 6 | 0.426 | +0.117 | 60.8% | +42.8% | [+19.6%, +70.7%] |
| fusion_city_blend | current_yes | 209 | 6 | 0.541 | +0.165 | 62.7% | +15.8% | [-4.0%, +34.3%] |
| fusion_city_blend | d1_no | 211 | 6 | 0.648 | +0.155 | 73.9% | +14.1% | [+7.1%, +21.6%] |
| fusion_city_blend | d2_no | 61 | 6 | 0.741 | +0.098 | 78.7% | +6.2% | [-4.1%, +15.3%] |
| fusion_numeric_blend | current_no | 60 | 6 | 0.498 | +0.105 | 66.7% | +33.9% | [+20.3%, +49.7%] |
| fusion_numeric_blend | current_yes | 184 | 6 | 0.559 | +0.191 | 65.2% | +16.6% | [-5.9%, +33.9%] |
| fusion_numeric_blend | d1_no | 191 | 6 | 0.645 | +0.172 | 75.9% | +17.8% | [+9.3%, +24.9%] |
| fusion_numeric_blend | d2_no | 54 | 6 | 0.783 | +0.100 | 81.5% | +4.0% | [-1.4%, +11.7%] |

## Daily Primary

| scope | date | rows | cities | cost | pnl | ROI |
|---|---|---:|---:|---:|---:|---:|
| expanding_forward | 2026-06-21 | 144 | 29 | 80.45 | +4.55 | +5.7% |
| expanding_forward | 2026-06-22 | 163 | 29 | 109.41 | +24.59 | +22.5% |
| expanding_forward | 2026-06-23 | 105 | 24 | 57.70 | +9.30 | +16.1% |
| expanding_forward | 2026-06-24 | 137 | 25 | 75.75 | +2.25 | +3.0% |
| expanding_forward | 2026-06-25 | 164 | 30 | 99.78 | +4.21 | +4.2% |
| expanding_forward | 2026-06-26 | 156 | 34 | 96.56 | +10.44 | +10.8% |
| fixed_forward | 2026-06-21 | 144 | 29 | 80.45 | +4.55 | +5.7% |
| fixed_forward | 2026-06-22 | 171 | 29 | 117.01 | +25.99 | +22.2% |
| fixed_forward | 2026-06-23 | 111 | 24 | 59.18 | +8.82 | +14.9% |
| fixed_forward | 2026-06-24 | 124 | 25 | 68.16 | +4.84 | +7.1% |
| fixed_forward | 2026-06-25 | 165 | 30 | 98.56 | +6.44 | +6.5% |
| fixed_forward | 2026-06-26 | 156 | 34 | 87.69 | +8.31 | +9.5% |

## Bucket / Expression Breakdown

| scope | expression | actual_bucket | rows | cost | pnl | ROI |
|---|---|---|---:|---:|---:|---:|
| expanding_forward | current_no | current | 63 | 10.73 | -10.73 | -100.0% |
| expanding_forward | current_no | d1 | 53 | 31.97 | +21.03 | +65.8% |
| expanding_forward | current_no | d2 | 14 | 11.31 | +2.69 | +23.8% |
| expanding_forward | current_no | tail | 4 | 3.28 | +0.72 | +22.0% |
| expanding_forward | current_yes | current | 187 | 134.44 | +52.56 | +39.1% |
| expanding_forward | current_yes | d1 | 58 | 19.77 | -19.77 | -100.0% |
| expanding_forward | current_yes | d2 | 33 | 9.76 | -9.76 | -100.0% |
| expanding_forward | current_yes | tail | 37 | 0.52 | -0.52 | -100.0% |
| expanding_forward | d1_no | current | 161 | 127.70 | +33.30 | +26.1% |
| expanding_forward | d1_no | d1 | 75 | 32.43 | -32.43 | -100.0% |
| expanding_forward | d1_no | d2 | 49 | 31.50 | +17.50 | +55.6% |
| expanding_forward | d1_no | tail | 14 | 11.62 | +2.38 | +20.5% |
| expanding_forward | d2_no | current | 45 | 40.21 | +4.79 | +11.9% |
| expanding_forward | d2_no | d1 | 35 | 27.00 | +8.00 | +29.6% |
| expanding_forward | d2_no | d2 | 28 | 17.69 | -17.69 | -100.0% |
| expanding_forward | d2_no | tail | 13 | 9.73 | +3.27 | +33.6% |

## Artifacts

- `docs/analysis/2026-07/generated/tmax_distribution_p2_ev_shadow_v1/opportunities.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p2_ev_shadow_v1/selected_summary.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p2_ev_shadow_v1/deduped_best_expression_summary.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p2_ev_shadow_v1/daily_primary.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p2_ev_shadow_v1/bucket_breakdown.csv`
- `docs/analysis/2026-07/2026-07-02-tmax-distribution-p2-ev-shadow-v1.json`
