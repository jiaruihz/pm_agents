# HeadA Source-Risk Monotonicity v1

Generated: 2026-07-08T12:29:59Z

## Question

Can we explain HeadA source-quality with a cleaner monotonic risk score, instead of relying on the prior hand-built high/mid/low formula?

## Data Snapshot

- Input: `docs/analysis/2026-07/generated/low_price_yes_source_quality_score_v2/candidate_rows.csv`
- Denominator: 370 settled hot-tail rows, 60 dates, 47 cities, 2026-05-06..2026-07-07
- Known calibration rows for clean risk: 291; unknown rows are kept as `unknown`, not dropped.

## Clean Risk Definition

No fitted coefficients:

```text
source_risk_clean_v1 = mean(
  predictability risk: best_model_mae 1.25F -> 2.5F,
  active source noise risk: source_mae 2F -> 3F,
  source gap risk: active_source_gap 0.25F -> 1F,
  hot direction risk: underforecast_ge_1F 55% -> 30%
)
```

Higher is worse. Bands are fixed: `low<=0.25`, `mid<=0.50`, `high<=0.75`, `extreme>0.75`.

## Verdict

`source_risk_clean_v1` gives a more interpretable story, but the monotonic evidence is still weak-to-moderate.

- Combined bands are directionally sensible: extreme risk is bad, low/mid risk are better, unknown is not automatically bad.
- The clean risk is not perfectly monotonic by ROI because lottery winners dominate small buckets.
- Same-date permutation says the observed bad-risk penalty is suggestive, not decisive: bad-vs-rest delta -106.1%, permutation p 0.147.
- Forward 7/1..7/7 is too thin for a live rule.

Conclusion: keep source risk as a shadow diagnostic. Use it to explain and pre-register no-size-up candidates, not to filter live entries.

## Risk Bands

Combined:

| source_risk_band_v1 | rows | dates | cities | wins | win_rate | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| low_0_25 | 113 | 49 | 20 | 24 | +21.2% | +89.8% | +30.9% | +152.6% | +56.1% |
| mid_25_50 | 95 | 43 | 25 | 13 | +13.7% | +34.1% | -38.3% | +116.7% | -22.3% |
| high_50_75 | 60 | 34 | 12 | 7 | +11.7% | +6.8% | -67.7% | +96.6% | -72.5% |
| extreme_75_100 | 23 | 18 | 7 | 1 | +4.3% | -51.1% | -100.0% | +37.1% | -100.0% |
| unknown | 79 | 38 | 25 | 14 | +17.7% | +58.9% | -10.3% | +131.3% | +4.8% |

Forward:

| source_risk_band_v1 | rows | dates | cities | wins | win_rate | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| low_0_25 | 11 | 7 | 8 | 3 | +27.3% | +116.4% | -22.8% | +353.5% | -100.0% |
| mid_25_50 | 18 | 6 | 12 | 4 | +22.2% | +117.2% | -61.1% | +377.5% | -100.0% |
| high_50_75 | 5 | 3 | 4 | 2 | +40.0% | +280.2% | -100.0% | +755.3% |  |
| extreme_75_100 | 2 | 2 | 1 | 0 | +0.0% | -100.0% |  |  |  |
| unknown | 1 | 1 | 1 | 0 | +0.0% | -100.0% |  |  |  |

## Selector Comparisons

Combined:

| label | rows | dates | cities | win_rate | roi | roi_ci_low | roi_ci_high | selected_share | delta_vs_complement | delta_vs_complement_ci_low | delta_vs_complement_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| clean_risk_safe_le_0p50 | 208 | 53 | 35 | +17.8% | +68.1% | +26.7% | +114.8% | 0.5621621621621622 | +41.9% | -24.0% | +107.1% |
| clean_risk_not_extreme | 347 | 60 | 45 | +16.7% | +56.0% | +20.7% | +93.9% | 0.9378378378378378 | +107.1% | +14.2% | +185.6% |
| clean_risk_bad_gt_0p75 | 23 | 18 | 7 | +4.3% | -51.1% | -100.0% | +37.1% | 0.062162162162162166 | -107.1% | -185.6% | -14.2% |
| source_quality_mid_or_high | 168 | 50 | 28 | +20.2% | +85.1% | +34.3% | +139.1% | 0.4540540540540541 | +67.3% | -1.2% | +135.4% |
| source_quality_not_low | 315 | 60 | 41 | +17.1% | +61.3% | +25.4% | +100.3% | 0.8513513513513513 | +79.8% | +3.0% | +146.9% |

Forward:

| label | rows | dates | cities | win_rate | roi | roi_ci_low | roi_ci_high | selected_share | delta_vs_complement | delta_vs_complement_ci_low | delta_vs_complement_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| clean_risk_safe_le_0p50 | 29 | 7 | 18 | +24.1% | +116.9% | -9.5% | +324.3% | 0.7837837837837838 | -18.5% | -442.4% | +335.9% |
| clean_risk_not_extreme | 35 | 7 | 22 | +25.7% | +126.0% | -16.5% | +310.6% | 0.9459459459459459 | +226.0% | +93.5% | +412.1% |
| clean_risk_bad_gt_0p75 | 2 | 2 | 1 | +0.0% | -100.0% |  |  | 0.05405405405405406 | -226.0% | -412.1% | -93.5% |
| source_quality_mid_or_high | 23 | 7 | 14 | +30.4% | +148.9% | +0.9% | +401.9% | 0.6216216216216216 | +96.0% | -94.1% | +407.9% |
| source_quality_not_low | 32 | 7 | 20 | +25.0% | +120.1% | -14.8% | +315.5% | 0.8648648648648649 | -3.7% | -419.2% | +302.7% |

## Daily Rank / Permutation

```text
daily_rank_days = 30
mean_spearman(risk, win) = -0.144
median_spearman(risk, win) = -0.203
positive_days = 10
negative_days = 17

observed_global_spearman(risk, win) = -0.139
perm_p(spearman <= observed) = 0.024
observed_bad_delta = -106.1%
perm_p(bad_delta <= observed) = 0.147
```

## Component Risk Bands

| component | band | rows | wins | win_rate | roi |
| --- | --- | --- | --- | --- | --- |
| predictability | extreme | 42 | 5 | +11.9% | -1.5% |
| predictability | high | 43 | 7 | +16.3% | +43.9% |
| predictability | low | 146 | 23 | +15.8% | +52.6% |
| predictability | mid | 60 | 10 | +16.7% | +73.7% |
| predictability | unknown | 79 | 14 | +17.7% | +58.9% |
| active_noise | extreme | 83 | 7 | +8.4% | -23.5% |
| active_noise | high | 23 | 3 | +13.0% | +40.2% |
| active_noise | low | 172 | 33 | +19.2% | +76.6% |
| active_noise | mid | 13 | 2 | +15.4% | +92.8% |
| active_noise | unknown | 79 | 14 | +17.7% | +58.9% |
| source_gap | extreme | 97 | 10 | +10.3% | +10.1% |
| source_gap | high | 16 | 1 | +6.2% | -36.4% |
| source_gap | low | 155 | 30 | +19.4% | +74.6% |
| source_gap | mid | 23 | 4 | +17.4% | +57.7% |
| source_gap | unknown | 79 | 14 | +17.7% | +58.9% |
| hot_direction | extreme | 92 | 10 | +10.9% | +1.2% |
| hot_direction | high | 22 | 4 | +18.2% | +57.1% |
| hot_direction | low | 146 | 21 | +14.4% | +38.4% |
| hot_direction | mid | 31 | 10 | +32.3% | +187.5% |
| hot_direction | unknown | 79 | 14 | +17.7% | +58.9% |

## Decision

Verdict: `shadow_diagnostic_cleaner_mechanism_not_live_selector`.

This improves explainability: bad source-quality is mostly a source-risk problem, not an arbitrary city list. It does not yet improve reliability enough to change live. The right forward experiment is to log `source_risk_clean_v1` and require 2-3 weeks of settled forward evidence before any sizing change.
