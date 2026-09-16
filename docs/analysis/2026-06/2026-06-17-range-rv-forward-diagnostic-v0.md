# Range RV Forward Diagnostic v0

> generated_at_utc: `2026-06-17T15:32:38.399594+00:00`
> target_metric: `forecast_bounded_range_rv_forward_diagnostic_v0`
> strategy_id: `forecast_bounded_w3_cheaper_default_wu_edge002_shadow_v0`

## Question

Training/replay showed a positive default-WU `forecast_bounded_w3_cheaper` candidate, but the first two settled forward shadow days were negative. This report compares the historical replay rows and forward shadow rows on fixed, pre-existing dimensions instead of searching for a new winner.

## Cohort Summary

| cohort | rows | dates | cities | gross_cost | pnl | gross_roi | effective_roi | win_rate | avg_win | avg_loss | avg_eff_cost | avg_edge |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| historical_train | 38 | 8 | 25 | 32.524 | +4.476 | +13.8% | +15.7% | +86.8% | +0.209 | -0.487 | 0.751 | 0.206 |
| historical_holdout | 36 | 8 | 22 | 30.596 | +3.404 | +11.1% | +13.3% | +80.6% | +0.225 | -0.447 | 0.711 | 0.234 |
| historical_train_capacity5 | 36 | 8 | 24 | 29.920 | +4.080 | +13.6% | +15.2% | +86.1% | +0.210 | -0.487 | 0.748 | 0.208 |
| historical_holdout_capacity5 | 32 | 7 | 22 | 27.225 | +3.775 | +13.9% | +17.0% | +81.2% | +0.242 | -0.421 | 0.695 | 0.244 |
| forward_first_settled | 34 | 3 | 17 | 122.056 | -2.056 | -1.7% | -9.8% | +55.9% | +0.321 | -0.543 | 0.619 | 0.233 |
| forward_latest_settled | 34 | 3 | 17 | 53.751 | -1.751 | -3.3% | -7.7% | +61.8% | +0.202 | -0.460 | 0.669 | 0.230 |
| forward_latest_2026_06_15_16 | 33 | 2 | 17 | 50.855 | -1.855 | -3.6% | -8.5% | +60.6% | +0.206 | -0.460 | 0.662 | 0.234 |

## Expression Split

| cohort | expression | rows | dates | cities | gross_cost | pnl | gross_roi | effective_roi | win_rate | avg_win | avg_loss | avg_eff_cost | avg_edge |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| historical_train | inside_yes | 34 | 8 | 23 | 25.835 | +4.165 | +16.1% | +16.1% | +88.2% | +0.205 | -0.495 | 0.760 | 0.200 |
| historical_train | outside_no | 4 | 3 | 4 | 6.689 | +0.311 | +4.6% | +11.6% | +75.0% | +0.254 | -0.452 | 0.672 | 0.259 |
| historical_holdout | inside_yes | 31 | 8 | 19 | 22.375 | +3.625 | +16.2% | +16.2% | +83.9% | +0.218 | -0.407 | 0.722 | 0.229 |
| historical_holdout | outside_no | 5 | 2 | 5 | 8.221 | -0.221 | -2.7% | -6.9% | +60.0% | +0.292 | -0.548 | 0.644 | 0.267 |
| historical_train_capacity5 | inside_yes | 33 | 8 | 22 | 24.891 | +4.109 | +16.5% | +16.5% | +87.9% | +0.210 | -0.495 | 0.754 | 0.204 |
| historical_train_capacity5 | outside_no | 3 | 3 | 3 | 5.029 | -0.029 | -0.6% | -1.4% | +66.7% | +0.212 | -0.452 | 0.676 | 0.246 |
| historical_holdout_capacity5 | inside_yes | 27 | 7 | 19 | 19.004 | +3.996 | +21.0% | +21.0% | +85.2% | +0.236 | -0.358 | 0.704 | 0.240 |
| historical_holdout_capacity5 | outside_no | 5 | 2 | 5 | 8.221 | -0.221 | -2.7% | -6.9% | +60.0% | +0.292 | -0.548 | 0.644 | 0.267 |
| forward_first_settled | inside_yes | 14 | 3 | 11 | 9.082 | +2.918 | +32.1% | +32.1% | +85.7% | +0.339 | -0.573 | 0.649 | 0.223 |
| forward_first_settled | outside_no | 20 | 2 | 14 | 112.974 | -4.974 | -4.4% | -41.5% | +35.0% | +0.290 | -0.539 | 0.599 | 0.240 |
| forward_latest_settled | outside_no | 20 | 3 | 14 | 46.084 | -1.084 | -2.4% | -7.2% | +70.0% | +0.154 | -0.540 | 0.754 | 0.166 |
| forward_latest_settled | inside_yes | 14 | 2 | 12 | 7.667 | -0.667 | -8.7% | -8.7% | +50.0% | +0.297 | -0.392 | 0.548 | 0.321 |
| forward_latest_2026_06_15_16 | outside_no | 19 | 2 | 14 | 43.188 | -1.188 | -2.8% | -8.4% | +68.4% | +0.158 | -0.540 | 0.747 | 0.169 |
| forward_latest_2026_06_15_16 | inside_yes | 14 | 2 | 12 | 7.667 | -0.667 | -8.7% | -8.7% | +50.0% | +0.297 | -0.392 | 0.548 | 0.321 |

## Effective-Cost Buckets

| cohort | bucket | rows | dates | cities | gross_cost | pnl | gross_roi | effective_roi | win_rate | avg_win | avg_loss | avg_eff_cost | avg_edge |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| historical_train | (0.25, 0.50] | 3 | 1 | 3 | 2.247 | -0.247 | -11.0% | -19.8% | +33.3% | +0.503 | -0.375 | 0.416 | 0.424 |
| historical_train | (0.50, 0.75] | 12 | 3 | 9 | 9.937 | +3.063 | +30.8% | +38.6% | +91.7% | +0.326 | -0.518 | 0.661 | 0.285 |
| historical_train | (0.75, 0.90] | 14 | 6 | 10 | 12.571 | +2.429 | +19.3% | +21.0% | +100.0% | +0.173 | NA | 0.827 | 0.158 |
| historical_train | (0.90, 0.95] | 5 | 4 | 5 | 4.651 | -0.651 | -14.0% | -14.0% | +80.0% | +0.072 | -0.937 | 0.930 | 0.061 |
| historical_train | <= 0.25 | 1 | 1 | 1 | 0.228 | -0.228 | -100.0% | -100.0% | +0.0% | NA | -0.228 | 0.228 | 0.520 |
| historical_train | > 0.95 | 3 | 2 | 3 | 2.890 | +0.110 | +3.8% | +3.8% | +100.0% | +0.037 | NA | 0.963 | 0.037 |
| historical_holdout | (0.25, 0.50] | 5 | 2 | 5 | 2.090 | +0.910 | +43.5% | +43.5% | +60.0% | +0.530 | -0.341 | 0.418 | 0.410 |
| historical_holdout | (0.50, 0.75] | 15 | 3 | 12 | 13.789 | +1.211 | +8.8% | +12.4% | +73.3% | +0.322 | -0.583 | 0.653 | 0.286 |
| historical_holdout | (0.75, 0.90] | 6 | 3 | 4 | 5.990 | +1.010 | +16.9% | +20.2% | +100.0% | +0.168 | NA | 0.832 | 0.139 |
| historical_holdout | (0.90, 0.95] | 3 | 2 | 3 | 2.790 | +0.210 | +7.5% | +7.5% | +100.0% | +0.070 | NA | 0.930 | 0.066 |
| historical_holdout | <= 0.25 | 1 | 1 | 1 | 0.117 | -0.117 | -100.0% | -100.0% | +0.0% | NA | -0.117 | 0.117 | 0.867 |
| historical_holdout | > 0.95 | 6 | 6 | 5 | 5.820 | +0.180 | +3.1% | +3.1% | +100.0% | +0.030 | NA | 0.970 | 0.030 |
| historical_train_capacity5 | (0.25, 0.50] | 3 | 1 | 3 | 2.247 | -0.247 | -11.0% | -19.8% | +33.3% | +0.503 | -0.375 | 0.416 | 0.424 |
| historical_train_capacity5 | (0.50, 0.75] | 11 | 3 | 9 | 8.277 | +2.723 | +32.9% | +37.4% | +90.9% | +0.324 | -0.518 | 0.662 | 0.285 |
| historical_train_capacity5 | (0.75, 0.90] | 14 | 6 | 10 | 12.571 | +2.429 | +19.3% | +21.0% | +100.0% | +0.173 | NA | 0.827 | 0.158 |
| historical_train_capacity5 | (0.90, 0.95] | 4 | 4 | 4 | 3.707 | -0.707 | -19.1% | -19.1% | +75.0% | +0.077 | -0.937 | 0.927 | 0.062 |
| historical_train_capacity5 | <= 0.25 | 1 | 1 | 1 | 0.228 | -0.228 | -100.0% | -100.0% | +0.0% | NA | -0.228 | 0.228 | 0.520 |
| historical_train_capacity5 | > 0.95 | 3 | 2 | 3 | 2.890 | +0.110 | +3.8% | +3.8% | +100.0% | +0.037 | NA | 0.963 | 0.037 |
| historical_holdout_capacity5 | (0.25, 0.50] | 5 | 2 | 5 | 2.090 | +0.910 | +43.5% | +43.5% | +60.0% | +0.530 | -0.341 | 0.418 | 0.410 |
| historical_holdout_capacity5 | (0.50, 0.75] | 14 | 3 | 11 | 13.188 | +1.812 | +13.7% | +19.7% | +78.6% | +0.322 | -0.577 | 0.656 | 0.279 |
| historical_holdout_capacity5 | (0.75, 0.90] | 5 | 2 | 4 | 5.130 | +0.870 | +17.0% | +21.1% | +100.0% | +0.174 | NA | 0.826 | 0.139 |
| historical_holdout_capacity5 | (0.90, 0.95] | 2 | 1 | 2 | 1.850 | +0.150 | +8.1% | +8.1% | +100.0% | +0.075 | NA | 0.925 | 0.070 |
| historical_holdout_capacity5 | <= 0.25 | 1 | 1 | 1 | 0.117 | -0.117 | -100.0% | -100.0% | +0.0% | NA | -0.117 | 0.117 | 0.867 |
| historical_holdout_capacity5 | > 0.95 | 5 | 5 | 5 | 4.850 | +0.150 | +3.1% | +3.1% | +100.0% | +0.030 | NA | 0.970 | 0.030 |
| forward_first_settled | (0.25, 0.50] | 11 | 2 | 8 | 43.151 | -0.151 | -0.3% | -3.6% | +36.4% | +0.578 | -0.352 | 0.377 | 0.390 |
| forward_first_settled | (0.50, 0.75] | 11 | 2 | 11 | 35.059 | -0.059 | -0.2% | -0.8% | +63.6% | +0.358 | -0.641 | 0.642 | 0.245 |
| forward_first_settled | (0.75, 0.90] | 11 | 3 | 9 | 35.941 | -1.941 | -5.4% | -21.7% | +63.6% | +0.168 | -0.780 | 0.813 | 0.078 |
| forward_first_settled | (0.90, 0.95] | 1 | 1 | 1 | 7.905 | +0.095 | +1.2% | +10.5% | +100.0% | +0.095 | NA | 0.905 | 0.066 |
| forward_latest_settled | (0.50, 0.75] | 9 | 2 | 7 | 13.619 | +0.381 | +2.8% | +6.8% | +66.7% | +0.404 | -0.682 | 0.624 | 0.209 |
| forward_latest_settled | (0.75, 0.90] | 11 | 3 | 10 | 23.166 | -1.166 | -5.0% | -12.7% | +72.7% | +0.166 | -0.830 | 0.833 | 0.090 |
| forward_latest_settled | (0.90, 0.95] | 8 | 2 | 8 | 16.421 | -0.421 | -2.6% | -5.7% | +87.5% | +0.069 | -0.903 | 0.928 | 0.042 |
| forward_latest_settled | <= 0.25 | 6 | 2 | 5 | 0.545 | -0.545 | -100.0% | -100.0% | +0.0% | NA | -0.091 | 0.091 | 0.766 |
| forward_latest_2026_06_15_16 | (0.50, 0.75] | 9 | 2 | 7 | 13.619 | +0.381 | +2.8% | +6.8% | +66.7% | +0.404 | -0.682 | 0.624 | 0.209 |
| forward_latest_2026_06_15_16 | (0.75, 0.90] | 10 | 2 | 10 | 20.270 | -1.270 | -6.3% | -15.4% | +70.0% | +0.174 | -0.830 | 0.827 | 0.090 |
| forward_latest_2026_06_15_16 | (0.90, 0.95] | 8 | 2 | 8 | 16.421 | -0.421 | -2.6% | -5.7% | +87.5% | +0.069 | -0.903 | 0.928 | 0.042 |
| forward_latest_2026_06_15_16 | <= 0.25 | 6 | 2 | 5 | 0.545 | -0.545 | -100.0% | -100.0% | +0.0% | NA | -0.091 | 0.091 | 0.766 |

## Diagnosis

- Forward underperformance is not explained by one bad raw snapshot; it appears after deduping to `city + event_date + forecast_source + model_version`.
- The first two complete forward days are below the historical holdout expectation, but still too few for a final statistical verdict.
- The biggest process mismatch is expression mix. Historical holdout was mostly `inside_yes` (`31/36` rows), while forward latest is much more `outside_no` (`20/34` rows). Historical `outside_no` was already weak (`-2.7%` gross ROI in holdout), so letting `cheaper` route more forward rows into `outside_no` is a plausible failure mode.
- The very-low effective-cost bucket (`<=0.25`) is not a hidden gem: it was negative in historical train/holdout and is also negative in forward. This points to model overconfidence or stale/misaligned forecast distribution when the market prices the range as very unlikely.
- Historical winners depended on high model mass and high hit rate. Forward model mass is still high enough to pass edge filters, but hit rate and payoff asymmetry are worse. That is a calibration problem, not just an execution threshold problem.
- The most plausible improvement areas are probability calibration, timing/persistence, cost floors/caps, and expression selection. Do not tune city-specific filters from these two days.

## Fixed Next Tests

1. Compare first/latest/persistence>=2 under the same dedup grain.
2. Add pre-registered `effective_cost <= 0.75` and `<= 0.80` diagnostics.
3. Re-estimate model range mass calibration by bucket; if high mass buckets miss too often, fix the forecast distribution before changing basket expression.
4. Keep `inside_yes` and `outside_no` separate until both have enough forward dates.
