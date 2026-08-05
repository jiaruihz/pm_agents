# HeadA Source-Quality Score v1

Generated: 2026-07-08T09:55:46Z

## Question

Is a mechanism-based `source_quality_score` a reasonable observation feature for HeadA, beyond raw ABCD buckets?

## Score Definition

No fitted coefficients, no threshold search:

```text
source_quality_score_v1 =
  predictability_component(best model MAE bucket)
  + source_gap_component(active source MAE - best MAE)
  + hot_direction_component(actual > forecast rate / bias)
  + noise_component(active source MAE)
```

High is `>=3.0`, mid is `1.5..3.0`, neutral is `0..1.5`, low is `<0`.

## Bottom Line

Verdict: `shadow_telemetry_reasonable_not_live_selector`.

This is a **reasonable telemetry/confidence feature**, not a live selector yet. It has the right mechanism shape: low score rows are bad, and high/mid rows have better point estimates. But recent support is small and noisy, so the correct live use is forward observation and maybe future sizing input, not hard filtering.

Daily rank check: 31 eligible days, mean Spearman(score, win) 0.096, positive days 16, negative days 10.

## Selector A/B

| label | rows | dates | cities | win_rate | avg_entry | avg_source_quality_score | roi | roi_ci_low | roi_ci_high | top5_removed_roi | selected_share | delta_vs_complement | delta_vs_complement_ci_low | delta_vs_complement_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_hot_dist_gt0 | 333 | 53 | 47 | +15.0% | 0.10 | 0.78 | +41.8% | +10.0% | +77.2% | +28.3% |  |  |  |  |
| source_quality_high | 36 | 27 | 11 | +25.0% | 0.11 | 3.40 | +136.3% | +26.7% | +247.2% | +29.2% | +10.8% | +107.8% | -17.3% | +233.4% |
| source_quality_mid_or_high | 145 | 43 | 25 | +18.6% | 0.11 | 2.28 | +74.5% | +21.6% | +128.3% | +45.3% | +43.5% | +58.7% | -14.4% | +132.2% |
| source_quality_low | 50 | 30 | 14 | +8.0% | 0.11 | -2.06 | -26.7% | -86.0% | +44.5% | -100.0% | +15.0% | -81.3% | -153.0% | -2.4% |
| exclude_source_quality_low | 283 | 53 | 40 | +16.3% | 0.10 | 1.28 | +54.6% | +18.7% | +92.0% | +38.9% | +85.0% | +81.3% | +2.4% | +153.0% |
| source_quality_confidence_candidate | 145 | 43 | 25 | +18.6% | 0.11 | 2.28 | +74.5% | +21.6% | +128.3% | +45.3% | +43.5% | +58.7% | -14.4% | +132.2% |
| source_quality_downweight_candidate | 50 | 30 | 14 | +8.0% | 0.11 | -2.06 | -26.7% | -86.0% | +44.5% | -100.0% | +15.0% | -81.3% | -153.0% | -2.4% |
| exclude_downweight_candidate | 283 | 53 | 40 | +16.3% | 0.10 | 1.28 | +54.6% | +18.7% | +92.0% | +38.9% | +85.0% | +81.3% | +2.4% | +153.0% |

## Train / Recent

Train:

| label | rows | win_rate | avg_source_quality_score | roi | roi_ci_low | roi_ci_high | delta_vs_complement | delta_vs_complement_ci_low | delta_vs_complement_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_hot_dist_gt0 | 275 | +14.5% | 0.72 | +35.1% | -2.5% | +75.1% |  |  |  |
| source_quality_high | 28 | +21.4% | 3.42 | +104.2% | -19.2% | +233.7% | +77.7% | -58.6% | +222.6% |
| source_quality_mid_or_high | 111 | +16.2% | 2.30 | +52.1% | -7.7% | +113.5% | +28.7% | -49.3% | +108.5% |
| source_quality_low | 42 | +7.1% | -2.08 | -39.6% | -100.0% | +39.6% | -89.1% | -160.2% | -6.4% |
| exclude_source_quality_low | 233 | +15.9% | 1.22 | +49.5% | +8.3% | +93.2% | +89.1% | +6.4% | +160.2% |
| source_quality_confidence_candidate | 111 | +16.2% | 2.30 | +52.1% | -7.7% | +113.5% | +28.7% | -49.3% | +108.5% |
| source_quality_downweight_candidate | 42 | +7.1% | -2.08 | -39.6% | -100.0% | +39.6% | -89.1% | -160.2% | -6.4% |
| exclude_downweight_candidate | 233 | +15.9% | 1.22 | +49.5% | +8.3% | +93.2% | +89.1% | +6.4% | +160.2% |

Recent:

| label | rows | win_rate | avg_source_quality_score | roi | roi_ci_low | roi_ci_high | delta_vs_complement | delta_vs_complement_ci_low | delta_vs_complement_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_hot_dist_gt0 | 58 | +17.2% | 1.09 | +76.7% | +32.2% | +133.6% |  |  |  |
| source_quality_high | 8 | +37.5% | 3.31 | +234.3% | +26.7% | +378.0% | +194.3% | -152.5% | +395.9% |
| source_quality_mid_or_high | 34 | +26.5% | 2.22 | +151.8% | +67.7% | +243.9% | +196.7% | +55.9% | +334.8% |
| source_quality_low | 8 | +12.5% | -2.00 | +50.9% | -100.0% | +130.8% | -30.0% | -239.8% | +102.3% |
| exclude_source_quality_low | 50 | +18.0% | 1.58 | +80.9% | +19.4% | +148.0% | +30.0% | -102.3% | +239.8% |
| source_quality_confidence_candidate | 34 | +26.5% | 2.22 | +151.8% | +67.7% | +243.9% | +196.7% | +55.9% | +334.8% |
| source_quality_downweight_candidate | 8 | +12.5% | -2.00 | +50.9% | -100.0% | +130.8% | -30.0% | -239.8% | +102.3% |
| exclude_downweight_candidate | 50 | +18.0% | 1.58 | +80.9% | +19.4% | +148.0% | +30.0% | -102.3% | +239.8% |

## Tier View

| source_quality_tier_v1 | rows | dates | cities | win_rate | avg_source_quality_score | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| high | 36 | 27 | 11 | +25.0% | 3.40 | +136.3% | +26.7% | +247.2% | +29.2% |
| low | 50 | 30 | 14 | +8.0% | -2.06 | -26.7% | -86.0% | +44.5% | -100.0% |
| mid | 109 | 42 | 24 | +16.5% | 1.92 | +50.7% | -8.7% | +118.6% | +8.8% |
| neutral | 138 | 50 | 32 | +13.8% | 0.23 | +32.6% | -18.3% | +86.9% | -3.3% |

## Raw Score View

| source_quality_score_v1 | rows | dates | cities | win_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- |
| -4.0 | 6 | 6 | 3 | +0.0% | -100.0% | -100.0% | -100.0% |
| -3.0 | 1 | 1 | 1 | +0.0% | -100.0% |  |  |
| -2.5 | 10 | 9 | 3 | +0.0% | -100.0% | -100.0% | -100.0% |
| -2.0 | 20 | 18 | 4 | +10.0% | -8.0% | -100.0% | +109.0% |
| -1.5 | 1 | 1 | 1 | +0.0% | -100.0% |  |  |
| -1.0 | 6 | 6 | 4 | +16.7% | +119.9% | -100.0% | +664.2% |
| -0.75 | 4 | 4 | 2 | +25.0% | +90.3% | -100.0% | +422.9% |
| -0.5 | 1 | 1 | 1 | +0.0% | -100.0% |  |  |
| -0.25 | 1 | 1 | 1 | +0.0% | -100.0% |  |  |
| 0.0 | 84 | 39 | 28 | +19.0% | +71.6% | +4.0% | +143.6% |
| 0.25 | 30 | 24 | 5 | +3.3% | -69.6% | -100.0% | +5.4% |
| 0.5 | 2 | 2 | 2 | +0.0% | -100.0% |  |  |
| 0.75 | 3 | 3 | 2 | +0.0% | -100.0% | -100.0% | -100.0% |
| 1.0 | 9 | 6 | 5 | +11.1% | +48.1% | -100.0% | +243.5% |
| 1.25 | 10 | 7 | 4 | +10.0% | -11.3% | -100.0% | +290.5% |
| 1.5 | 55 | 30 | 17 | +12.7% | +28.2% | -59.5% | +124.5% |
| 1.75 | 11 | 10 | 5 | +18.2% | +96.9% | -100.0% | +333.4% |
| 2.0 | 1 | 1 | 1 | +0.0% | -100.0% |  |  |
| 2.25 | 1 | 1 | 1 | +0.0% | -100.0% |  |  |
| 2.5 | 40 | 29 | 12 | +22.5% | +66.0% | -26.3% | +182.1% |
| 2.75 | 1 | 1 | 1 | +0.0% | -100.0% |  |  |
| 3.0 | 4 | 4 | 2 | +0.0% | -100.0% | -100.0% | -100.0% |
| 3.25 | 11 | 10 | 3 | +36.4% | +214.1% | +3.6% | +372.8% |
| 3.5 | 17 | 15 | 7 | +29.4% | +177.7% | +16.6% | +333.7% |
| 3.75 | 4 | 3 | 3 | +0.0% | -100.0% | -100.0% | -100.0% |

## Component Interaction

| sq_predictability_component | sq_hot_direction_component | sq_noise_component | rows | cities | win_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| -1.0 | -1.0 | -1.0 | 29 | 6 | +3.4% | -66.5% | -100.0% | +0.5% |
| -1.0 | 0.0 | -1.0 | 2 | 2 | +50.0% | +607.8% |  |  |
| -1.0 | 1.25 | -1.0 | 1 | 1 | +0.0% | -100.0% |  |  |
| 0.0 | -1.0 | -1.0 | 2 | 2 | +0.0% | -100.0% |  |  |
| 0.0 | -1.0 | 0.0 | 4 | 3 | +0.0% | -100.0% | -100.0% | -100.0% |
| 0.0 | 0.0 | -1.0 | 1 | 1 | +100.0% | +1025.0% |  |  |
| 0.0 | 0.0 | 0.0 | 80 | 25 | +17.5% | +59.9% | -8.1% | +132.7% |
| 0.0 | 1.0 | -1.0 | 2 | 1 | +0.0% | -100.0% |  |  |
| 0.0 | 1.0 | 0.0 | 1 | 1 | +0.0% | -100.0% |  |  |
| 0.0 | 1.25 | -1.0 | 8 | 4 | +12.5% | -1.6% | -100.0% | +219.4% |
| 0.0 | 1.25 | 0.0 | 1 | 1 | +0.0% | -100.0% |  |  |
| 1.0 | -1.0 | -1.0 | 8 | 2 | +12.5% | +32.6% | -100.0% | +272.1% |
| 1.0 | -1.0 | 0.0 | 5 | 3 | +0.0% | -100.0% | -100.0% | -100.0% |
| 1.0 | -1.0 | 0.5 | 56 | 16 | +14.3% | +43.1% | -43.8% | +138.1% |
| 1.0 | 0.0 | -1.0 | 1 | 1 | +0.0% | -100.0% |  |  |
| 1.0 | 0.0 | 0.5 | 38 | 11 | +23.7% | +73.1% | -22.2% | +198.0% |
| 1.0 | 1.0 | -1.0 | 4 | 2 | +25.0% | +237.8% | -100.0% | +1060.2% |
| 1.0 | 1.0 | 0.0 | 4 | 3 | +0.0% | -100.0% | -100.0% | -100.0% |
| 1.0 | 1.0 | 0.5 | 20 | 8 | +25.0% | +127.3% | -9.6% | +271.5% |
| 1.0 | 1.25 | -1.0 | 38 | 7 | +7.9% | -11.9% | -100.0% | +102.1% |
| 1.0 | 1.25 | 0.0 | 11 | 5 | +9.1% | -14.8% | -100.0% | +268.6% |
| 1.0 | 1.25 | 0.5 | 17 | 5 | +23.5% | +134.8% | -32.1% | +290.6% |

## Decision

Keep initial HeadA live unchanged. Add/keep these fields as telemetry. The first practical use should be `no size-up when source_quality_score_v1 < 0` and `allow normal/high confidence only when score >= 1.5`, but that should wait for fresh forward rows.
