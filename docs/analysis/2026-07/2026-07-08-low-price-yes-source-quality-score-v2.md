# HeadA Source-Quality Score v2

Generated: 2026-07-08T12:04:51Z

## Question

Update the HeadA source-quality conclusion after adding settled forward opportunity rows through 2026-07-07.

## Data Snapshot

- DB: `runtime/weather.db`
- `fact_signal_candidates` event dates: 2026-05-05..2026-07-09; `fact_built_at_utc` 2026-07-08T09:18:35.918613+00:00
- `settlement_outcomes` target dates: 2026-05-04..2026-07-07; settled rows 29642
- Historical source-quality rows: 333 rows, 2026-05-06..2026-06-30
- Forward raw rows 2026-07-01..2026-07-07: 59; after `dist>0`: 37

Execution replay uses current HeadA probe geometry: price-tier `6/8/10` shares, official Weather taker fee `shares * 0.05 * price * (1-price)`, hold to settlement.

## Bottom Line

Verdict: `shadow_telemetry_reasonable_not_live_selector`.

Adding 7/1..7/7 does **not** weaken source-quality as a telemetry feature. Combined through 7/7, mid/high score rows are still materially better than baseline on point estimate, and low score rows remain worse over the full window.

But the new forward low bucket is only 5 rows and includes one winner, so `source_quality_low` should **not** be a hard block. The clean use is still: keep collecting, use it for shadow sizing diagnostics, and at most treat low score as `do_not_size_up` until more fresh rows settle.

Daily rank check after extension: 36 eligible days, mean Spearman(score, win) 0.126, positive days 20, negative days 10.

## Combined Through 7/7

| label | rows | dates | cities | win_rate | avg_entry | avg_source_quality_score | roi | roi_ci_low | roi_ci_high | top5_removed_roi | selected_share | delta_vs_complement | delta_vs_complement_ci_low | delta_vs_complement_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_hot_dist_gt0 | 370 | 60 | 47 | +15.9% | 0.10 | 0.81 | +49.3% | +15.4% | +86.1% | +37.2% |  |  |  |  |
| source_quality_high | 38 | 29 | 12 | +26.3% | 0.11 | 3.39 | +136.7% | +28.0% | +242.7% | +38.4% | +10.3% | +99.2% | -23.7% | +220.2% |
| source_quality_mid_or_high | 168 | 50 | 28 | +20.2% | 0.11 | 2.25 | +85.1% | +34.3% | +139.1% | +60.9% | +45.4% | +67.3% | -1.2% | +135.4% |
| source_quality_low | 55 | 34 | 14 | +9.1% | 0.10 | -2.07 | -18.5% | -80.2% | +52.9% | -100.0% | +14.9% | -79.8% | -146.9% | -3.0% |
| exclude_source_quality_low | 315 | 60 | 41 | +17.1% | 0.10 | 1.31 | +61.3% | +25.4% | +100.3% | +47.4% | +85.1% | +79.8% | +3.0% | +146.9% |

## Forward 7/1-7/7

| label | rows | dates | cities | win_rate | avg_entry | avg_source_quality_score | roi | roi_ci_low | roi_ci_high | top5_removed_roi | selected_share | delta_vs_complement | delta_vs_complement_ci_low | delta_vs_complement_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_hot_dist_gt0 | 37 | 7 | 23 | +24.3% | 0.10 | 1.07 | +120.4% | -17.0% | +292.6% | -2.2% |  |  |  |  |
| source_quality_high | 2 | 2 | 2 | +50.0% | 0.13 | 3.38 | +142.2% |  |  |  | +5.4% | +23.7% | -307.2% | +1213.7% |
| source_quality_mid_or_high | 23 | 7 | 14 | +30.4% | 0.11 | 2.02 | +148.9% | +0.9% | +401.9% | -24.1% | +62.2% | +96.0% | -94.1% | +407.9% |
| source_quality_low | 5 | 4 | 3 | +20.0% | 0.08 | -2.10 | +123.8% | -100.0% | +533.4% |  | +13.5% | +3.7% | -302.7% | +419.2% |
| exclude_source_quality_low | 32 | 7 | 20 | +25.0% | 0.10 | 1.57 | +120.1% | -14.8% | +315.5% | -17.6% | +86.5% | -3.7% | -419.2% | +302.7% |

## Historical 5/6-6/30

| label | rows | dates | cities | win_rate | avg_entry | avg_source_quality_score | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_hot_dist_gt0 | 333 | 53 | 47 | +15.0% | 0.10 | 0.78 | +41.8% | +10.0% | +77.2% | +28.3% |
| source_quality_high | 36 | 27 | 11 | +25.0% | 0.11 | 3.40 | +136.3% | +26.7% | +247.2% | +29.2% |
| source_quality_mid_or_high | 145 | 43 | 25 | +18.6% | 0.11 | 2.28 | +74.5% | +21.6% | +128.3% | +45.3% |
| source_quality_low | 50 | 30 | 14 | +8.0% | 0.11 | -2.06 | -26.7% | -86.0% | +44.5% | -100.0% |
| exclude_source_quality_low | 283 | 53 | 40 | +16.3% | 0.10 | 1.28 | +54.6% | +18.7% | +92.0% | +38.9% |

## Tier View

Combined:

| source_quality_tier_v1 | rows | dates | cities | win_rate | avg_source_quality_score | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| high | 38 | 29 | 12 | +26.3% | 3.39 | +136.7% | +28.0% | +242.7% | +38.4% |
| low | 55 | 34 | 14 | +9.1% | -2.07 | -18.5% | -80.2% | +52.9% | -100.0% |
| mid | 130 | 49 | 27 | +18.5% | 1.91 | +67.5% | +8.1% | +131.2% | +33.7% |
| neutral | 147 | 53 | 34 | +13.6% | 0.24 | +32.1% | -15.3% | +87.5% | -1.8% |

Forward:

| source_quality_tier_v1 | rows | dates | cities | win_rate | avg_source_quality_score | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| high | 2 | 2 | 2 | +50.0% | 3.38 | +142.2% |  |  |  |
| low | 5 | 4 | 3 | +20.0% | -2.10 | +123.8% | -100.0% | +533.4% |  |
| mid | 21 | 7 | 12 | +28.6% | 1.89 | +149.8% | -1.9% | +376.4% | -55.0% |
| neutral | 9 | 3 | 9 | +11.1% | 0.42 | +23.6% | -100.0% | +184.5% | -100.0% |

## Forward Low-Score Rows

| target_date | city | bracket | entry | payoff | source_quality_score_v1 | pnl |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-07-01 | Chicago | 98-99 | 0.07 | 0.0 | -2.5 | -0.44 |
| 2026-07-02 | Chicago | 98-99 | 0.05 | 0.0 | -2.0 | -0.31 |
| 2026-07-05 | Austin | 100-101 | 0.135 | 0.0 | -2.0 | -1.13 |
| 2026-07-06 | Austin | 98-99 | 0.06 | 1.0 | -2.0 | 5.62 |
| 2026-07-06 | Denver | 98-99 | 0.0675 | 0.0 | -2.0 | -0.42 |

## What Exclude-Low Removes

`exclude_source_quality_low` removes 55 / 370 rows (14.9%). This is not a broad volume cut; it removes about one ticket per active day when low rows appear. Historical low rows were 50 rows / 30 dates / 14 cities, win 8.0%, ROI -26.7%. Forward low rows are only 5 rows / 4 dates / 3 cities, and one Austin winner makes the forward-only point estimate positive, which is why low remains a downweight/no-size-up observation rather than a hard block.

Daily low rows:

| target_date | all_rows | all_wins | low_rows | low_wins | selected_share | low_pnl | roi |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-20 | 5 | 1 | 1.0 | 0.0 | +20.0% | -0.87759 | -100.0% |
| 2026-05-21 | 6 | 0 | 4.0 | 0.0 | +66.7% | -3.077665325 | -100.0% |
| 2026-05-22 | 9 | 2 | 1.0 | 0.0 | +11.1% | -1.5119875 | -100.0% |
| 2026-05-23 | 7 | 0 | 2.0 | 0.0 | +28.6% | -2.8369239 | -100.0% |
| 2026-05-24 | 5 | 3 | 1.0 | 1.0 | +20.0% | 6.83184 | +584.8% |
| 2026-05-25 | 6 | 0 | 1.0 | 0.0 | +16.7% | -0.35812530000000004 | -100.0% |
| 2026-05-26 | 4 | 1 | 1.0 | 0.0 | +25.0% | -0.43953000000000003 | -100.0% |
| 2026-05-27 | 4 | 1 | 2.0 | 0.0 | +50.0% | -2.04595 | -100.0% |
| 2026-05-28 | 12 | 1 | 2.0 | 0.0 | +16.7% | -1.7343875 | -100.0% |
| 2026-05-29 | 11 | 0 | 1.0 | 0.0 | +9.1% | -0.71111 | -100.0% |
| 2026-05-31 | 7 | 2 | 2.0 | 0.0 | +28.6% | -1.0567025 | -100.0% |
| 2026-06-01 | 6 | 1 | 1.0 | 0.0 | +16.7% | -2.0284875 | -100.0% |
| 2026-06-02 | 6 | 1 | 1.0 | 1.0 | +16.7% | 8.3328 | +499.8% |
| 2026-06-04 | 5 | 0 | 2.0 | 0.0 | +40.0% | -2.35814 | -100.0% |
| 2026-06-06 | 9 | 0 | 2.0 | 0.0 | +22.2% | -1.1390753249999999 | -100.0% |
| 2026-06-07 | 8 | 0 | 2.0 | 0.0 | +25.0% | -2.17048 | -100.0% |
| 2026-06-08 | 7 | 1 | 3.0 | 0.0 | +42.9% | -3.4104970999999997 | -100.0% |
| 2026-06-10 | 4 | 0 | 1.0 | 0.0 | +25.0% | -1.0644975 | -100.0% |
| 2026-06-11 | 15 | 1 | 3.0 | 0.0 | +20.0% | -2.632358925 | -100.0% |
| 2026-06-13 | 6 | 2 | 1.0 | 0.0 | +16.7% | -1.9253875 | -100.0% |
| 2026-06-15 | 10 | 2 | 2.0 | 1.0 | +20.0% | 4.70392 | +362.9% |
| 2026-06-17 | 10 | 0 | 1.0 | 0.0 | +10.0% | -0.4082325 | -100.0% |
| 2026-06-18 | 7 | 1 | 1.0 | 0.0 | +14.3% | -0.4082325 | -100.0% |
| 2026-06-19 | 6 | 0 | 3.0 | 0.0 | +50.0% | -1.5588725 | -100.0% |
| 2026-06-20 | 6 | 1 | 1.0 | 0.0 | +16.7% | -1.8738 | -100.0% |
| 2026-06-21 | 10 | 1 | 1.0 | 0.0 | +10.0% | -0.37692 | -100.0% |
| 2026-06-22 | 7 | 1 | 2.0 | 0.0 | +28.6% | -0.669242325 | -100.0% |
| 2026-06-23 | 11 | 2 | 1.0 | 0.0 | +9.1% | -0.9939355999999999 | -100.0% |
| 2026-06-24 | 9 | 2 | 1.0 | 0.0 | +11.1% | -0.71111 | -100.0% |
| 2026-06-26 | 6 | 1 | 3.0 | 1.0 | +50.0% | 6.123489999999999 | +158.0% |
| 2026-07-01 | 3 | 1 | 1.0 | 0.0 | +33.3% | -0.43953000000000003 | -100.0% |
| 2026-07-02 | 5 | 2 | 1.0 | 0.0 | +20.0% | -0.31425000000000003 | -100.0% |
| 2026-07-05 | 3 | 1 | 1.0 | 0.0 | +33.3% | -1.12671 | -100.0% |
| 2026-07-06 | 9 | 4 | 2.0 | 1.0 | +22.2% | 5.199196875 | +649.2% |

Cities most affected:

| city | all_rows | all_wins | low_rows | low_wins | selected_share | low_pnl | roi |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Austin | 11 | 1 | 11.0 | 1.0 | +100.0% | -4.735430325 | -44.1% |
| Chicago | 11 | 0 | 10.0 | 0.0 | +90.9% | -8.361672325 | -100.0% |
| Karachi | 11 | 1 | 10.0 | 1.0 | +90.9% | 1.3608250000000004 | +15.8% |
| Denver | 7 | 1 | 7.0 | 1.0 | +100.0% | 3.2250942749999996 | +47.6% |
| Seattle | 5 | 1 | 4.0 | 0.0 | +80.0% | -5.3058609 | -100.0% |
| Ankara | 17 | 0 | 3.0 | 0.0 | +17.6% | -3.0367575 | -100.0% |
| Dallas | 7 | 2 | 2.0 | 0.0 | +28.6% | -1.4605956 | -100.0% |
| SanFrancisco | 4 | 0 | 2.0 | 0.0 | +50.0% | -1.2231825 | -100.0% |
| Jeddah | 1 | 0 | 1.0 | 0.0 | +100.0% | -0.71111 | -100.0% |
| Helsinki | 16 | 3 | 1.0 | 0.0 | +6.2% | -0.48332129999999995 | -100.0% |
| Jakarta | 1 | 0 | 1.0 | 0.0 | +100.0% | -0.43640092500000005 | -100.0% |
| MexicoCity | 1 | 0 | 1.0 | 0.0 | +100.0% | -0.354992325 | -100.0% |
| Guangzhou | 1 | 1 | 1.0 | 1.0 | +100.0% | 5.62308 | +1491.8% |
| KualaLumpur | 4 | 2 | 1.0 | 1.0 | +25.0% | 6.83184 | +584.8% |

Low-score source shapes:

By active source:

| forecast_model | rows | wins | win_rate | pnl | roi |
| --- | --- | --- | --- | --- | --- |
| gfs | 33 | 3 | +9.1% | -9.55 | -30.3% |
| ecmwf | 22 | 2 | +9.1% | 0.49 | +2.8% |

By best-model reliability:

| best_reliability_bucket | rows | wins | win_rate | pnl | roi |
| --- | --- | --- | --- | --- | --- |
| D_>2.5F | 37 | 3 | +8.1% | -11.85 | -35.0% |
| A_<=1.25F | 11 | 1 | +9.1% | 0.92 | +10.2% |
| C_2-2.5F | 6 | 1 | +16.7% | 2.21 | +38.1% |
| B_1.25-2F | 1 | 0 | +0.0% | -0.35 | -100.0% |

By hot-underforecast history:

| source_underforecast_bucket | rows | wins | win_rate | pnl | roi |
| --- | --- | --- | --- | --- | --- |
| <40% | 42 | 2 | +4.8% | -20.41 | -50.5% |
| >=70% | 5 | 1 | +20.0% | 3.31 | +70.6% |
| nan | 5 | 1 | +20.0% | 3.32 | +123.8% |
| 40-55% | 3 | 1 | +33.3% | 4.72 | +367.2% |

By active-source MAE:

| source_mae_bucket | rows | wins | win_rate | pnl | roi |
| --- | --- | --- | --- | --- | --- |
| >3F | 37 | 3 | +8.1% | -10.65 | -27.6% |
| 2.5-3F | 9 | 1 | +11.1% | 0.15 | +2.5% |
| nan | 5 | 1 | +20.0% | 3.32 | +123.8% |
| 2-2.5F | 4 | 0 | +0.0% | -1.88 | -100.0% |

## Decision

No live selector change. `source_quality_score_v1` remains `shadow_telemetry_reasonable_not_live_selector`.

The 7/7 extension supports a conservative future rule: do not increase size on low-score rows. It does not support excluding low rows outright, because the forward low bucket is too thin and already contains a winner.
