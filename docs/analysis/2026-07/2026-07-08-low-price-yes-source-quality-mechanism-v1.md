# HeadA Source-Quality Mechanism Falsification v1

Generated: 2026-07-08T12:18:44Z

## Question

Is `source_quality_score_v1` an interpretable mechanism signal for HeadA hot-tail, or mostly coincidence / city memory / a few winners?

## Data Snapshot

- Input: `docs/analysis/2026-07/generated/low_price_yes_source_quality_score_v2/candidate_rows.csv`
- Denominator: 370 settled hot-tail rows, 60 target dates, 47 cities, 2026-05-06..2026-07-07
- Execution replay inherited from source-quality v2: price-tier `6/8/10` shares, official Weather taker fee, hold to settlement.

## Verdict

`source_quality_score_v1` has a **plausible but messy mechanism**, not a clean confirmed alpha.

What supports mechanism:

- The low bucket is not only a city cut. It is mostly produced by source/forecast features: weak hot-underforecast history, D reliability, and high active-source MAE.
- Full score keeps positive low-avoidance after same-date permutation sanity: observed low-vs-not-low delta -79.8%; date-block score shuffle is at or below this 7.5% of the time.
- Ablation does **not** prove the exact four-part formula is uniquely right. Some variants rank similarly or better on point estimate. That means the current score should be treated as an interpretable diagnostic ensemble, not a fitted law.

What blocks promotion:

- Forward 7/1..7/7 is still only 37 rows and low bucket only 5 rows.
- Mid/high separation is positive but only borderline under permutation (`p≈0.062`), and low avoidance is also only suggestive (`p≈0.075`), not decisive.
- Leave-one-city remains positive in most removals, but several cities fully define their own low bucket, so city/source confounding is not gone.

Action: keep `source_quality` as shadow sizing confidence / `do_not_size_up_low`. Do not hard-filter low rows and do not size up solely from this score. The useful next step is forward validation plus a cleaner monotonic score, not more threshold cuts.

## Score Variants / Ablation

Each row uses the same thresholds: mid/high = score >= 1.5; low = score < 0.

Read this table conservatively: if removing a component improves a point estimate, that does not mean we should deploy the ablation. It means the present score is a rough mechanism bundle and needs forward validation before becoming a real sizing rule.

| score_col | mid_high_rows | mid_high_win_rate | mid_high_roi | mid_high_delta_vs_complement | low_rows | low_win_rate | low_roi | low_delta_vs_not_low |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| score_full | 168 | 0.20238095238095238 | 0.8511313541609519 | +67.3% | 55 | +9.1% | -18.5% | -79.8% |
| score_only_hot_direction | 0 | nan | nan |  | 123 | +12.2% | +14.0% | -50.2% |
| score_only_noise | 0 | nan | nan |  | 106 | +9.4% | -11.1% | -82.6% |
| score_only_predictability | 0 | nan | nan |  | 37 | +8.1% | -35.0% | -94.1% |
| score_only_source_gap | 0 | nan | nan |  | 71 | +7.0% | -28.9% | -92.7% |
| score_predictability_plus_hot_direction | 102 | 0.14705882352941177 | 0.5568024777839582 | +8.6% | 43 | +9.3% | -22.5% | -81.5% |
| score_source_gap_plus_noise | 130 | 0.2 | 0.7720295709675751 | +43.2% | 102 | +6.9% | -31.5% | -106.7% |
| score_without_hot_direction | 158 | 0.2088607594936709 | 0.8846100359084917 | +70.4% | 89 | +7.9% | -30.2% | -103.1% |
| score_without_noise | 109 | 0.22018348623853212 | 0.8905244659712637 | +61.0% | 47 | +6.4% | -39.8% | -102.7% |
| score_without_predictability | 88 | 0.22727272727272727 | 0.894035518361653 | +56.1% | 89 | +6.7% | -37.2% | -111.6% |
| score_without_source_gap | 100 | 0.21 | 0.8187877225099216 | +47.1% | 53 | +11.3% | +1.2% | -56.3% |

## Daily Rank

| score_col | days | mean_spearman | median_spearman | positive_days | negative_days |
| --- | --- | --- | --- | --- | --- |
| score_source_gap_plus_noise | 36 | 0.154 | 0.204 | 23 | 9 |
| score_without_hot_direction | 36 | 0.142 | 0.225 | 23 | 10 |
| score_without_predictability | 36 | 0.141 | 0.209 | 20 | 10 |
| score_only_noise | 35 | 0.139 | 0.310 | 22 | 10 |
| score_without_noise | 36 | 0.139 | 0.187 | 21 | 10 |
| score_full | 36 | 0.126 | 0.176 | 20 | 10 |
| score_only_source_gap | 34 | 0.101 | 0.192 | 20 | 11 |
| score_only_predictability | 32 | 0.092 | 0.200 | 21 | 9 |
| score_without_source_gap | 36 | 0.071 | 0.026 | 18 | 11 |
| score_predictability_plus_hot_direction | 36 | 0.027 | 0.000 | 15 | 16 |
| score_only_hot_direction | 36 | -0.009 | 0.000 | 13 | 15 |

## Date-Block Permutation Sanity

Scores were shuffled within each target date, preserving daily opportunity count and market/weather day structure.

```text
observed_mid_high_delta = +67.3%
perm_p(delta >= observed) = 6.2%
perm_mid_high_delta_p50/p95 = +19.0%, +72.0%

observed_low_delta = -79.8%
perm_p(delta <= observed) = 7.5%
perm_low_delta_p05/p50 = -89.2%, -19.2%
```

## What Makes Low Low

Component values inside low bucket:

| component | value | rows | wins | win_rate | pnl | roi |
| --- | --- | --- | --- | --- | --- | --- |
| predictability | -1.0 | 37 | 3 | +8.1% | -11.85 | -35.0% |
| predictability | 1.0 | 12 | 1 | +8.3% | 0.58 | +6.1% |
| predictability | 0.0 | 6 | 1 | +16.7% | 2.21 | +38.1% |
| source_gap | -1.0 | 22 | 2 | +9.1% | -0.90 | -4.7% |
| source_gap | 1.0 | 19 | 3 | +15.8% | 4.37 | +24.8% |
| source_gap | 0.5 | 14 | 0 | +0.0% | -12.55 | -100.0% |
| hot_direction | -1.0 | 48 | 3 | +6.2% | -17.53 | -40.3% |
| hot_direction | 1.25 | 5 | 1 | +20.0% | 3.31 | +70.6% |
| hot_direction | 0.0 | 2 | 1 | +50.0% | 5.15 | +607.8% |
| noise | -1.0 | 51 | 5 | +9.8% | -7.19 | -15.2% |
| noise | 0.0 | 4 | 0 | +0.0% | -1.88 | -100.0% |

Source/model shapes inside low bucket:

| group | value | rows | wins | win_rate | pnl | roi |
| --- | --- | --- | --- | --- | --- | --- |
| forecast_model | gfs | 33 | 3 | +9.1% | -9.55 | -30.3% |
| forecast_model | ecmwf | 22 | 2 | +9.1% | 0.49 | +2.8% |
| best_reliability_bucket | D_>2.5F | 37 | 3 | +8.1% | -11.85 | -35.0% |
| best_reliability_bucket | A_<=1.25F | 11 | 1 | +9.1% | 0.92 | +10.2% |
| best_reliability_bucket | C_2-2.5F | 6 | 1 | +16.7% | 2.21 | +38.1% |
| best_reliability_bucket | B_1.25-2F | 1 | 0 | +0.0% | -0.35 | -100.0% |
| source_underforecast_bucket | <40% | 42 | 2 | +4.8% | -20.41 | -50.5% |
| source_underforecast_bucket | >=70% | 5 | 1 | +20.0% | 3.31 | +70.6% |
| source_underforecast_bucket | nan | 5 | 1 | +20.0% | 3.32 | +123.8% |
| source_underforecast_bucket | 40-55% | 3 | 1 | +33.3% | 4.72 | +367.2% |
| source_mae_bucket | >3F | 37 | 3 | +8.1% | -10.65 | -27.6% |
| source_mae_bucket | 2.5-3F | 9 | 1 | +11.1% | 0.15 | +2.5% |
| source_mae_bucket | nan | 5 | 1 | +20.0% | 3.32 | +123.8% |
| source_mae_bucket | 2-2.5F | 4 | 0 | +0.0% | -1.88 | -100.0% |
| source_gap_bucket | >1F | 22 | 2 | +9.1% | -0.90 | -4.7% |
| source_gap_bucket | 0.5-1F | 13 | 0 | +0.0% | -12.11 | -100.0% |
| source_gap_bucket | <=0.25F | 12 | 1 | +8.3% | -6.48 | -51.9% |
| source_gap_bucket | nan | 5 | 1 | +20.0% | 3.32 | +123.8% |
| source_gap_bucket | 0.25-0.5F | 3 | 1 | +33.3% | 7.09 | +243.8% |

## City Robustness

Leave-one-city stress, weakest remaining low-avoidance cases first:

| city | remaining_rows | low_rows | low_delta_vs_not_low | mid_high_rows | mid_high_delta_vs_complement |
| --- | --- | --- | --- | --- | --- |
| Chicago | 359 | 45 | -64.0% | 168 | +60.1% |
| Seattle | 365 | 51 | -67.5% | 168 | +67.8% |
| Amsterdam | 353 | 55 | -71.2% | 152 | +65.0% |
| Austin | 359 | 44 | -72.6% | 168 | +63.3% |
| Madrid | 366 | 55 | -72.8% | 164 | +55.4% |
| Shanghai | 353 | 55 | -73.1% | 153 | +71.1% |
| Dallas | 363 | 53 | -73.5% | 167 | +64.4% |
| Moscow | 358 | 55 | -75.4% | 168 | +78.8% |
| BuenosAires | 362 | 55 | -76.0% | 161 | +71.4% |
| Busan | 365 | 55 | -76.4% | 165 | +79.8% |
| Paris | 362 | 55 | -77.3% | 161 | +62.6% |
| Wuhan | 361 | 55 | -77.3% | 160 | +62.8% |
| Chongqing | 364 | 55 | -77.3% | 167 | +63.9% |
| Helsinki | 354 | 54 | -77.5% | 164 | +59.6% |
| Istanbul | 366 | 55 | -78.1% | 165 | +62.8% |
| Manila | 355 | 55 | -78.4% | 153 | +66.4% |
| SaoPaulo | 367 | 55 | -78.5% | 165 | +65.3% |
| Jeddah | 369 | 54 | -78.6% | 168 | +66.9% |
| SanFrancisco | 366 | 53 | -78.6% | 168 | +65.4% |
| Miami | 367 | 55 | -78.8% | 165 | +65.8% |
| Tokyo | 364 | 55 | -79.0% | 163 | +65.7% |
| Jakarta | 369 | 54 | -79.1% | 168 | +67.0% |
| MexicoCity | 369 | 54 | -79.2% | 168 | +67.1% |
| Wellington | 366 | 55 | -79.3% | 167 | +69.7% |
| Warsaw | 359 | 55 | -79.7% | 159 | +76.8% |

Within-city low vs not-low where both exist:

| city | rows | low_rows | not_low_rows | low_win_rate | not_low_win_rate | low_roi | not_low_roi | roi_gap_not_low_minus_low |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Ankara | 17 | 3 | 14 | +0.0% | +0.0% | -100.0% | -100.0% | +0.0% |
| Helsinki | 16 | 1 | 15 | +0.0% | +20.0% | -100.0% | +96.6% | +196.6% |
| Chicago | 11 | 10 | 1 | +0.0% | +0.0% | -100.0% | -100.0% | +0.0% |
| Karachi | 11 | 10 | 1 | +10.0% | +0.0% | +15.8% | -100.0% | -115.8% |
| Dallas | 7 | 2 | 5 | +0.0% | +40.0% | -100.0% | +274.1% | +374.1% |
| Seattle | 5 | 4 | 1 | +0.0% | +100.0% | -100.0% | +856.9% | +956.9% |
| SanFrancisco | 4 | 2 | 2 | +0.0% | +0.0% | -100.0% | -100.0% | +0.0% |
| KualaLumpur | 4 | 1 | 3 | +100.0% | +33.3% | +584.8% | +179.1% | -405.7% |

## Forward Slice

7/1..7/7 only:

| score_col | mid_high_rows | mid_high_win_rate | mid_high_roi | mid_high_delta_vs_complement | low_rows | low_win_rate | low_roi | low_delta_vs_not_low |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| score_full | 23 | 0.30434782608695654 | 1.4893313739944378 | +96.0% | 5 | +20.0% | +123.8% | +3.7% |
| score_without_predictability | 9 | 0.2222222222222222 | 0.5785057576984076 | -87.8% | 10 | +10.0% | -0.4% | -150.1% |
| score_without_hot_direction | 24 | 0.2916666666666667 | 1.4398947764201235 | +83.4% | 9 | +11.1% | +5.7% | -140.6% |

## Decision

Conclusion: `shadow_telemetry_mechanism_plausible_not_confirmed`.

This is useful enough to keep journaling and to mark low-score tickets as no-size-up candidates. It is not enough to remove low-score tickets from live, because the fresh forward denominator is too small and low-score tickets can still be winners.
