# Low-Price YES Tail p_cal v1

Generated: 2026-07-02T14:32:07+00:00

## Verdict

`inconclusive` as a replacement selector.  The fabel5 direction is conceptually right, but this first calibrated-probability pass does not yet produce a clean live-valid alpha.

What did hold up:

- The v1 low-price tail universe still has real signal versus broad cheap YES.
- As-of station-basis features carry explanatory information and should be logged.
- Raw city fixed effects make the model look much stronger, which confirms the overfit risk rather than solving it.

What did not hold up:

- A no-city p_cal model trained on broad `ask 0.05..0.20` improves train but fails 6/21-6/26 holdout.
- A no-city p_cal model inside the current v1 denominator does not improve the v1 baseline; stricter EV thresholds cut out winners in holdout/forward.
- The only very attractive model includes city fixed effects, so it is not acceptable as a live rule without fresh forward proof.

Action: keep `$1` v1 live unchanged; add p_cal/station-bias telemetry; do not switch selector or size up.

Gate summary: `significance=FAIL`, `baseline=FAIL`, `forward=FAIL/NA`, `conclusion=inconclusive`.  The no-city p_cal filters do not beat the current v1 denominator out of sample; the city-fixed diagnostic is too proxy-heavy to count as confirmed alpha.

## Evidence Window

- Broad rows: `docs/analysis/2026-07/generated/reversal_lottery_lab_v2/details.csv` label `all_low_price_yes`, ask 0.05..0.20, settled through 2026-06-26.
- V1 rows: `docs/analysis/2026-07/generated/low_price_yes_lottery_selector_refinement_v1/details.csv` selector `no_dust_edge20_ask05_20`, historical through 2026-06-26 plus closed forward rows through 2026-06-30.
- As-of station bias: `docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/daily_error_rows.csv`; only errors with `date < target_date` enter each row.
- Bracket distance remains a known gap: old May rows lack forecast max fields, so this p_cal v1 does not use bracket distance yet.

## Model Quality

| dataset | model | city fixed | train rows | train AUC | holdout rows | holdout AUC | fwd rows | fwd AUC |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| broad_no_dust | broad_no_dust_no_city | False | 392 | 0.627 | 71 | 0.673 |  |  |
| broad_no_dust | broad_no_dust_city_diag | True | 392 | 0.749 | 71 | 0.670 |  |  |
| v1_edge20 | v1_edge20_no_city | False | 383 | 0.662 | 74 | 0.647 | 19.000 | 0.521 |
| v1_edge20 | v1_edge20_city_diag | True | 383 | 0.724 | 74 | 0.642 | 19.000 | 0.646 |

## Strategy Comparison

| strategy | train rows | train win | train ask | train ROI | train CI low | train CI high | holdout rows | holdout win | holdout ROI | fwd rows | fwd ROI | train top10 rm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| broad_no_dust_baseline_all | 392.000 | +8.9% | 0.094 | +1.6% | -35.4% | +45.4% | 71.000 | +7.0% | -24.3% |  |  | -38.2% |
| broad_no_dust_broad_no_dust_no_city_ev_ge_0.2 | 204.000 | +9.3% | 0.068 | +32.5% | -23.4% | +98.0% | 44.000 | +9.1% | +10.0% |  |  | -44.2% |
| broad_no_dust_broad_no_dust_no_city_ev_ge_0.5 | 90.000 | +10.0% | 0.063 | +52.4% | -38.6% | +156.7% | 17.000 | +5.9% | -25.5% |  |  | -100.0% |
| v1_edge20_baseline_all | 383.000 | +13.1% | 0.105 | +20.4% | -14.4% | +59.9% | 74.000 | +16.2% | +43.9% | 19.000 | +65.9% | -16.7% |
| v1_edge20_v1_edge20_no_city_ev_ge_0.2 | 314.000 | +13.7% | 0.106 | +22.8% | -14.0% | +62.7% | 63.000 | +14.3% | +32.7% | 15.000 | +21.2% | -21.6% |
| v1_edge20_v1_edge20_no_city_ev_ge_0.5 | 107.000 | +15.9% | 0.106 | +38.5% | -26.9% | +115.2% | 18.000 | +11.1% | +19.4% | 5.000 | -100.0% | -59.4% |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.2 | 257.000 | +16.3% | 0.105 | +51.7% | +8.5% | +99.6% | 55.000 | +16.4% | +52.0% | 14.000 | +84.4% | -2.4% |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | 149.000 | +22.1% | 0.106 | +105.0% | +40.3% | +180.1% | 28.000 | +21.4% | +87.2% | 8.000 | +127.3% | +15.2% |

## Daily Risk

| strategy | period | rows | dates | cities | ROI | losing days | <= -50% days | max daily loss | top5 rm | top10 rm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| broad_no_dust_baseline_all | holdout_2026_06_21_26 | 71 | 6 | 38 | -24.3% | 4 | 2 | $-13.00 | -100.0% | -100.0% |
| broad_no_dust_baseline_all | train_pre_2026_06_21 | 392 | 45 | 48 | +1.6% | 29 | 23 | $-18.00 | -19.6% | -38.2% |
| broad_no_dust_broad_no_dust_no_city_ev_ge_0.2 | holdout_2026_06_21_26 | 44 | 6 | 29 | +10.0% | 3 | 2 | $-10.00 | -100.0% | -100.0% |
| broad_no_dust_broad_no_dust_no_city_ev_ge_0.2 | train_pre_2026_06_21 | 204 | 44 | 46 | +32.5% | 29 | 29 | $-8.00 | -8.0% | -44.2% |
| broad_no_dust_broad_no_dust_no_city_ev_ge_0.5 | holdout_2026_06_21_26 | 17 | 6 | 13 | -25.5% | 5 | 5 | $-4.00 | -100.0% | -100.0% |
| broad_no_dust_broad_no_dust_no_city_ev_ge_0.5 | train_pre_2026_06_21 | 90 | 41 | 33 | +52.4% | 33 | 33 | $-5.00 | -39.5% | -100.0% |
| v1_edge20_baseline_all | forward_2026_06_27_30 | 19 | 3 | 17 | +65.9% | 1 | 0 | $-5.29 | -100.0% | -100.0% |
| v1_edge20_baseline_all | holdout_2026_06_21_26 | 74 | 6 | 32 | +43.9% | 3 | 3 | $-7.94 | -34.5% | -83.9% |
| v1_edge20_baseline_all | train_pre_2026_06_21 | 383 | 44 | 48 | +20.4% | 23 | 15 | $-13.00 | +1.2% | -16.7% |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.2 | forward_2026_06_27_30 | 14 | 3 | 12 | +84.4% | 1 | 1 | $-8.00 | -100.0% | -100.0% |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.2 | holdout_2026_06_21_26 | 55 | 6 | 26 | +52.0% | 4 | 0 | $-4.94 | -55.4% | -100.0% |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.2 | train_pre_2026_06_21 | 257 | 44 | 41 | +51.7% | 19 | 16 | $-9.00 | +23.4% | -2.4% |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | forward_2026_06_27_30 | 8 | 3 | 7 | +127.3% | 2 | 2 | $-6.00 | -100.0% |  |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | holdout_2026_06_21_26 | 28 | 6 | 15 | +87.2% | 2 | 1 | $-3.00 | -76.9% | -100.0% |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | train_pre_2026_06_21 | 149 | 41 | 29 | +105.0% | 17 | 17 | $-8.00 | +57.5% | +15.2% |
| v1_edge20_v1_edge20_no_city_ev_ge_0.2 | forward_2026_06_27_30 | 15 | 3 | 14 | +21.2% | 2 | 2 | $-9.00 | -100.0% | -100.0% |
| v1_edge20_v1_edge20_no_city_ev_ge_0.2 | holdout_2026_06_21_26 | 63 | 6 | 27 | +32.7% | 4 | 1 | $-7.07 | -61.6% | -100.0% |
| v1_edge20_v1_edge20_no_city_ev_ge_0.2 | train_pre_2026_06_21 | 314 | 44 | 46 | +22.8% | 22 | 16 | $-13.00 | -0.7% | -21.6% |
| v1_edge20_v1_edge20_no_city_ev_ge_0.5 | forward_2026_06_27_30 | 5 | 2 | 5 | -100.0% | 2 | 2 | $-4.00 |  |  |
| v1_edge20_v1_edge20_no_city_ev_ge_0.5 | holdout_2026_06_21_26 | 18 | 6 | 7 | +19.4% | 4 | 4 | $-4.00 | -100.0% | -100.0% |
| v1_edge20_v1_edge20_no_city_ev_ge_0.5 | train_pre_2026_06_21 | 107 | 39 | 22 | +38.5% | 26 | 26 | $-4.00 | -22.6% | -59.4% |

## Station-Basis Diagnostics

High station-basis buckets are informative, but not yet a standalone selector.  The best-looking slices still rely on the existing v1 edge universe and are not fresh-forward validated.

| feature | period | bucket | rows | dates | win | ask | ROI | top5 rm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bias_mean | forward_2026_06_27_30 | high | 7 | 3 | +28.6% | 0.099 | +268.8% | -100.0% |
| bias_mean | holdout_2026_06_21_26 | high | 18 | 6 | +5.6% | 0.078 | -24.4% | -100.0% |
| bias_mean | train_pre_2026_06_21 | high | 129 | 44 | +14.0% | 0.104 | +37.6% | -17.3% |
| bias_p90 | forward_2026_06_27_30 | high | 7 | 3 | +28.6% | 0.099 | +268.8% | -100.0% |
| bias_p90 | holdout_2026_06_21_26 | high | 20 | 6 | +10.0% | 0.076 | +27.9% | -100.0% |
| bias_p90 | train_pre_2026_06_21 | high | 130 | 42 | +15.4% | 0.100 | +65.3% | +9.0% |
| hot_tail_pct | forward_2026_06_27_30 | high | 8 | 3 | +25.0% | 0.094 | +222.7% | -100.0% |
| hot_tail_pct | holdout_2026_06_21_26 | high | 23 | 6 | +17.4% | 0.095 | +34.4% | -100.0% |
| hot_tail_pct | train_pre_2026_06_21 | high | 130 | 44 | +15.4% | 0.109 | +40.6% | -10.7% |

## Interpretation

- fabel5 was right that source-aware v3 should be downgraded as a causal story.  Source is mostly a city/station proxy on this denominator.
- But the first p_cal attempt also shows why we should not jump straight into a city/station selector.  Without raw city fixed effects, the calibrated EV filter is not better than v1 on holdout/forward.
- The valuable next step is telemetry and a better feature layer: bracket distance, as-of station bias, book freshness/depth, and true fresh 7/03+ forward settlement.
- If future p_cal needs raw city fixed effects to work, we should treat it as a city whitelist and reject it unless leave-one-city/leave-one-region tests survive.

## Next Step

Implement shadow columns on the running `$1` v1 journal: `p_cal_no_city`, `p_cal_city_diag`, `hot_tail_pct_asof`, `bias_p90_asof`, `bias_mean_asof`, `source_aware_v3`, and later `bracket_distance_f` once materialized.  The next promotion test should use only fresh rows after this telemetry exists.

## Artifacts

- Script: `scripts/analysis/forecast_quality/research_low_price_yes_tail_pcal_v1.py`
- JSON summary: `docs/analysis/2026-07/2026-07-02-low-price-yes-tail-pcal-v1.json`
- Details: `docs/analysis/2026-07/generated/low_price_yes_tail_pcal_v1/details.csv`
- Summary: `docs/analysis/2026-07/generated/low_price_yes_tail_pcal_v1/summary.csv`
- Daily: `docs/analysis/2026-07/generated/low_price_yes_tail_pcal_v1/daily.csv`
- Model metrics: `docs/analysis/2026-07/generated/low_price_yes_tail_pcal_v1/model_metrics.csv`
- Station slices: `docs/analysis/2026-07/generated/low_price_yes_tail_pcal_v1/station_slices.csv`
