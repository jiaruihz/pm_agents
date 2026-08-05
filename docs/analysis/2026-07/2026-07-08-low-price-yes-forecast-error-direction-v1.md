# HeadA Forecast Error Direction v1

Generated: 2026-07-08T09:49:43Z

## Question

Does forecast inaccuracy hurt HeadA, or can some inaccurate cities/sources be the source of the alpha?

## Bottom Line

Verdict: `shadow_research_source_quality_score_not_raw_D_hard_block`.

Forecast error is not one thing, but the current evidence does **not** support the naive idea that "very inaccurate but hot-biased" is enough. High active-source MAE remains weak. The better shape is: source quality is acceptable, the active source is close to the city best model, and the remaining bias is directionally hot-underforecast.

Baseline: 333 rows / 53 dates / 47 cities, win 15.0%, ROI +41.8% CI [+10.0%, +77.2%].

Mechanism read:

- `best_D_all` is weak: 32 rows, win 6.2%, ROI negative. Recent is too small to hard-block from this alone, but D is not a positive sleeve.
- `noisy_hot_underforecast` does **not** rescue high MAE: active source MAE >2.5F plus hot direction is still roughly flat/full negative and failed recent.
- `noisy_not_hot_underforecast` is worse, as expected.
- `mechanism_good_source` is the positive confidence state: A/B city predictability, active source within 1F of best, and hot-underforecast direction. This is the cleanest candidate for confidence sizing, not raw D filtering.

## Selector A/B

| label | rows | dates | cities | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high | top5_removed_roi | selected_share | delta_vs_complement | delta_vs_complement_ci_low | delta_vs_complement_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_hot_dist_gt0 | 333 | 53.0 | 47.0 | +15.0% | 0.10 | +41.8% | +10.0% | +77.2% | +28.3% |  |  |  |  |
| best_D_all | 32 | 20.0 | 8.0 | +6.2% | 0.11 | -48.7% | -100.0% | +24.8% | -100.0% | +9.6% | -101.2% | -174.1% | -17.0% |
| best_D_hot_direction | 1 | 1.0 | 1.0 | +0.0% | 0.08 | -100.0% |  |  |  | +0.3% | -142.1% | -175.6% | -109.2% |
| best_D_not_hot_direction | 31 | 20.0 | 7.0 | +6.5% | 0.11 | -47.9% | -100.0% | +26.1% | -100.0% | +9.3% | -100.1% | -173.8% | -16.1% |
| active_source_high_mae | 96 | 39.0 | 18.0 | +9.4% | 0.10 | -11.8% | -65.7% | +48.5% | -65.2% | +28.8% | -74.3% | -142.4% | +3.0% |
| noisy_hot_underforecast | 53 | 34.0 | 8.0 | +9.4% | 0.09 | -3.0% | -76.3% | +90.0% | -100.0% | +15.9% | -51.7% | -132.4% | +43.3% |
| noisy_not_hot_underforecast | 43 | 26.0 | 11.0 | +9.3% | 0.11 | -20.0% | -100.0% | +73.2% | -100.0% | +12.9% | -72.3% | -165.6% | +34.6% |
| hot_direction_55 | 107 | 43.0 | 18.0 | +14.0% | 0.10 | +48.5% | -9.4% | +110.1% | +0.8% | +32.1% | +9.4% | -64.2% | +82.0% |
| hot_direction_65 | 76 | 38.0 | 11.0 | +11.8% | 0.10 | +28.5% | -39.3% | +105.3% | -46.3% | +22.8% | -16.7% | -102.3% | +70.5% |
| mechanism_good_source | 50 | 31.0 | 13.0 | +22.0% | 0.11 | +118.8% | +26.8% | +208.0% | +37.4% | +15.0% | +91.8% | -13.4% | +192.9% |
| mechanism_bad_noise | 6 | 6.0 | 3.0 | +0.0% | 0.10 | -100.0% | -100.0% | -100.0% | -100.0% | +1.8% | -144.2% | -179.4% | -112.2% |
| exclude_mechanism_bad_noise | 327 | 53.0 | 46.0 | +15.3% | 0.10 | +44.2% | +12.2% | +79.5% | +30.5% | +98.2% | +144.2% | +112.2% | +179.4% |

## Train / Recent

Train:

| label | rows | win_rate | roi | roi_ci_low | roi_ci_high | delta_vs_complement | delta_vs_complement_ci_low | delta_vs_complement_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_hot_dist_gt0 | 275 | +14.5% | +35.1% | -2.5% | +75.1% |  |  |  |
| best_D_all | 27 | +3.7% | -76.9% | -100.0% | -18.6% | -125.2% | -182.0% | -59.8% |
| best_D_hot_direction | 1 | +0.0% | -100.0% |  |  | -135.4% | -173.3% | -96.2% |
| best_D_not_hot_direction | 26 | +3.8% | -76.5% | -100.0% | -17.8% | -124.4% | -181.6% | -58.4% |
| active_source_high_mae | 82 | +9.8% | -10.4% | -68.8% | +58.8% | -63.2% | -135.9% | +17.5% |
| noisy_hot_underforecast | 44 | +11.4% | +19.4% | -65.4% | +125.8% | -18.1% | -109.4% | +89.7% |
| noisy_not_hot_underforecast | 38 | +7.9% | -35.7% | -100.0% | +65.8% | -83.4% | -173.3% | +31.1% |
| hot_direction_55 | 87 | +13.8% | +42.6% | -24.1% | +111.6% | +10.5% | -68.0% | +88.3% |
| hot_direction_65 | 59 | +10.2% | +4.7% | -64.7% | +90.6% | -37.3% | -120.2% | +52.9% |
| mechanism_good_source | 40 | +20.0% | +107.0% | +1.7% | +217.7% | +84.0% | -36.4% | +203.5% |
| mechanism_bad_noise | 5 | +0.0% | -100.0% | -100.0% | -100.0% | -137.7% | -177.7% | -99.5% |
| exclude_mechanism_bad_noise | 270 | +14.8% | +37.7% | -0.3% | +77.8% | +137.7% | +99.5% | +177.7% |

Recent:

| label | rows | win_rate | roi | roi_ci_low | roi_ci_high | delta_vs_complement | delta_vs_complement_ci_low | delta_vs_complement_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_hot_dist_gt0 | 58 | +17.2% | +76.7% | +32.2% | +133.6% |  |  |  |
| best_D_all | 5 | +20.0% | +92.9% | -100.0% | +197.5% | +18.2% | -237.0% | +178.1% |
| best_D_hot_direction | 0 |  |  |  |  |  |  |  |
| best_D_not_hot_direction | 5 | +20.0% | +92.9% | -100.0% | +197.5% | +18.2% | -237.0% | +178.1% |
| active_source_high_mae | 14 | +7.1% | -20.1% | -100.0% | +86.5% | -131.3% | -274.0% | +18.0% |
| noisy_hot_underforecast | 9 | +0.0% | -100.0% | -100.0% | -100.0% | -208.9% | -267.2% | -171.2% |
| noisy_not_hot_underforecast | 5 | +20.0% | +92.9% | -100.0% | +197.5% | +18.2% | -237.0% | +178.1% |
| hot_direction_55 | 20 | +15.0% | +71.8% | -54.3% | +211.3% | -7.7% | -255.0% | +201.0% |
| hot_direction_65 | 17 | +17.6% | +102.1% | -46.2% | +246.3% | +36.9% | -219.8% | +229.4% |
| mechanism_good_source | 10 | +30.0% | +154.7% | -17.2% | +304.1% | +103.7% | -180.1% | +297.9% |
| mechanism_bad_noise | 1 | +0.0% | -100.0% |  |  | -178.1% | -231.8% | -134.3% |
| exclude_mechanism_bad_noise | 57 | +17.5% | +78.1% | +33.1% | +135.4% | +178.1% | +134.3% | +231.8% |

## Error Direction Buckets

| direction_bucket | rows | dates | cities | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cold_or_random | 104 | 40 | 30 | +9.6% | 0.10 | -8.8% | -58.4% | +48.6% | -58.2% |
| hot_underforecast_55 | 31 | 21 | 12 | +19.4% | 0.10 | +93.4% | -31.6% | +207.3% | -69.5% |
| hot_underforecast_65 | 76 | 38 | 11 | +11.8% | 0.10 | +28.5% | -39.3% | +105.3% | -46.3% |
| neutral_or_missing | 122 | 45 | 30 | +20.5% | 0.11 | +71.3% | +16.7% | +131.6% | +40.0% |

## Noise x Direction Buckets

| noise_direction_bucket | rows | dates | cities | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| high_mae_hot_direction | 53 | 34 | 8 | +9.4% | 0.09 | -3.0% | -76.3% | +90.0% | -100.0% |
| high_mae_not_hot | 43 | 26 | 11 | +9.3% | 0.11 | -20.0% | -100.0% | +73.2% | -100.0% |
| low_mae_hot_direction | 54 | 33 | 14 | +18.5% | 0.10 | +90.1% | -2.2% | +180.9% | +5.3% |
| low_mae_not_hot_or_missing | 183 | 52 | 37 | +16.9% | 0.11 | +54.3% | +10.3% | +102.9% | +30.3% |

## Reliability x Direction

| best_reliability_bucket | direction_bucket | rows | cities | win_rate | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A_<=1.25F | cold_or_random | 43 | 11 | +11.6% | +23.6% | -69.7% | +130.8% | -100.0% |
| A_<=1.25F | hot_underforecast_55 | 10 | 5 | +20.0% | +90.4% | -100.0% | +320.7% | -100.0% |
| A_<=1.25F | hot_underforecast_65 | 30 | 6 | +16.7% | +72.3% | -43.1% | +186.7% | -100.0% |
| A_<=1.25F | neutral_or_missing | 8 | 3 | +25.0% | +58.8% | -100.0% | +313.8% | -100.0% |
| B_1.25-2F | cold_or_random | 26 | 12 | +15.4% | +53.2% | -61.2% | +175.7% | -100.0% |
| B_1.25-2F | hot_underforecast_55 | 18 | 7 | +22.2% | +123.8% | -38.3% | +282.0% | -100.0% |
| B_1.25-2F | hot_underforecast_65 | 36 | 7 | +8.3% | -2.5% | -100.0% | +108.2% | -100.0% |
| B_1.25-2F | neutral_or_missing | 31 | 9 | +22.6% | +67.9% | -34.4% | +216.0% | -50.5% |
| C_2-2.5F | cold_or_random | 6 | 5 | +0.0% | -100.0% | -100.0% | -100.0% | -100.0% |
| C_2-2.5F | hot_underforecast_55 | 3 | 2 | +0.0% | -100.0% | -100.0% | -100.0% |  |
| C_2-2.5F | hot_underforecast_65 | 9 | 5 | +11.1% | -9.9% | -100.0% | +184.6% | -100.0% |
| C_2-2.5F | neutral_or_missing | 3 | 2 | +33.3% | +348.0% | -100.0% | +1025.0% |  |
| D_>2.5F | cold_or_random | 29 | 6 | +3.4% | -66.5% | -100.0% | +0.5% | -100.0% |
| D_>2.5F | hot_underforecast_65 | 1 | 1 | +0.0% | -100.0% |  |  |  |
| D_>2.5F | neutral_or_missing | 2 | 2 | +50.0% | +607.8% |  |  |  |
| missing | neutral_or_missing | 78 | 25 | +17.9% | +62.1% | -9.7% | +138.4% | +7.1% |

## Implication

Do not hard block raw `D` yet. A cleaner next policy is a shadow source-quality score:

```text
forecast_source_quality =
  predictability(best_mae)
  + active_source_gap(source_gap_to_best)
  + direction(actual_above_forecast_rate, bias)
```

For live, the conservative version is not a new selector: keep current HeadA entries, record these fields forward, allow normal/extra confidence only for `mechanism_good_source`, and consider **no size-up / downweight** for high-MAE non-hot states. Hard block needs fresh forward evidence because the recent window is too small.
