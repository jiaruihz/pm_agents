# HeadA Forecast-Source Calibration Overlay v1

Generated: 2026-07-08T09:25:29Z

## Question

Can the multi-model city/source calibration layer in `WEATHER_FORECAST_SOURCE_CALIBRATION.md` improve the original HeadA low-price YES hot-tail strategy by choosing better cities or forecast sources?

## Evidence Layer

- HeadA denominator: fixed `dist>0` hot-tail rows from `low_price_yes_integrated_tail_v2`, rebuilt against canonical `fact_signal_candidates`.
- Calibration source: `historical_forecast_enrichment_bias_v1` city/model artifacts, historical window `2026-05-04`..`2026-07-07`.
- Calibration labels are **as-of** per row: only city/model errors with `target_date < decision target_date` are used, with minimum 7 prior days per model. Full-window `city_best_model.csv` is used only for provenance.
- Execution model: `price_tier_6_8_10_shares`, official Weather taker fee `shares * 0.05 * price * (1-price)`, hold to settlement.
- Important boundary: alternate ICON/GEM/JMA/etc forecasts are not PIT-replayed into HeadA decisions here. They are used only as city/source reliability labels.

## Bottom Line

Verdict: `shadow_candidate_confidence_only_no_live_source_change`.

The calibration layer is useful as a **shadow confidence / diagnostics layer**, but this run does **not** justify a hard city/source selector or live source switch. The best practical signal is negative information: as-of D-bucket city/model quality is weak and may deserve future downweighting, while positive selectors such as `calibration_core_AB_and_gap_le_1F` improve hit-rate/ROI point estimates but still need fresh forward confirmation.

Baseline hot-tail: 333 rows / 53 dates / 47 cities, win 15.0%, avg ask 0.104, ROI +41.8% CI [+10.0%, +77.2%]. Recent `2026-06-21+`: 58 rows, win 17.2%, ROI +76.7%.

## Selector A/B

Same denominator selected-vs-complement. Positive delta means the calibration slice beat the rows it would exclude.

| label | rows | dates | cities | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high | top5_removed_roi | selected_share | delta_vs_complement | delta_vs_complement_ci_low | delta_vs_complement_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_hot_dist_gt0 | 333 | 53 | 47 | +15.0% | 0.10 | +41.8% | +10.0% | +77.2% | +28.3% |  |  |  |  |
| best_reliable_AB | 202 | 46 | 32 | +15.8% | 0.10 | +54.5% | +11.9% | +97.6% | +31.5% | +60.7% | +30.1% | -42.4% | +103.5% |
| exclude_best_D | 301 | 53 | 42 | +15.9% | 0.10 | +52.5% | +17.7% | +90.2% | +37.7% | +90.4% | +101.2% | +17.0% | +174.1% |
| source_gap_le_0p5F | 140 | 45 | 33 | +17.1% | 0.10 | +57.3% | +4.6% | +112.8% | +26.0% | +42.0% | +26.6% | -44.7% | +102.7% |
| source_gap_le_1F | 189 | 46 | 36 | +16.4% | 0.11 | +51.0% | +9.0% | +96.1% | +28.0% | +56.8% | +22.1% | -47.7% | +91.5% |
| source_matches_old_best | 179 | 46 | 35 | +13.4% | 0.10 | +26.8% | -17.1% | +75.9% | -0.5% | +53.8% | -30.9% | -100.7% | +40.3% |
| source_matches_enrichment_best | 88 | 41 | 22 | +17.0% | 0.11 | +42.5% | -18.4% | +112.2% | -6.5% | +26.4% | +0.9% | -73.4% | +80.9% |
| source_hot_underforecast_high | 107 | 43 | 18 | +14.0% | 0.10 | +48.5% | -9.4% | +110.1% | +0.8% | +32.1% | +9.4% | -64.2% | +82.0% |
| calibration_core_AB_and_gap_le_1F | 147 | 44 | 27 | +19.0% | 0.11 | +77.0% | +23.8% | +129.9% | +48.5% | +44.1% | +64.1% | -4.9% | +135.6% |
| calibration_strict_AB_and_gap_le_0p5F | 114 | 42 | 23 | +18.4% | 0.10 | +68.6% | +10.0% | +134.5% | +30.8% | +34.2% | +40.7% | -34.1% | +122.5% |
| regional_model_gain_ge_1F | 28 | 23 | 7 | +10.7% | 0.09 | +13.8% | -100.0% | +142.8% | -100.0% | +8.4% | -30.2% | -144.3% | +105.7% |

## Train / Recent Stability

Train:

| label | rows | win_rate | roi | roi_ci_low | roi_ci_high | delta_vs_complement | delta_vs_complement_ci_low | delta_vs_complement_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_hot_dist_gt0 | 275 | +14.5% | +35.1% | -2.5% | +75.1% |  |  |  |
| best_reliable_AB | 158 | +14.6% | +40.1% | -7.5% | +90.2% | +11.0% | -63.6% | +84.5% |
| exclude_best_D | 248 | +15.7% | +48.3% | +7.8% | +91.1% | +125.2% | +59.8% | +182.0% |
| source_gap_le_0p5F | 112 | +15.2% | +39.4% | -16.6% | +101.3% | +7.1% | -68.7% | +87.9% |
| source_gap_le_1F | 152 | +13.8% | +27.3% | -18.3% | +78.0% | -17.7% | -91.3% | +57.7% |
| source_matches_old_best | 141 | +12.8% | +20.2% | -27.1% | +75.8% | -29.8% | -103.3% | +48.6% |
| source_matches_enrichment_best | 70 | +14.3% | +18.4% | -43.7% | +94.7% | -22.6% | -104.9% | +69.5% |
| source_hot_underforecast_high | 87 | +13.8% | +42.6% | -24.1% | +111.6% | +10.5% | -68.0% | +88.3% |
| calibration_core_AB_and_gap_le_1F | 115 | +16.5% | +54.6% | -2.0% | +117.5% | +33.9% | -40.1% | +110.5% |
| calibration_strict_AB_and_gap_le_0p5F | 89 | +16.9% | +54.6% | -10.3% | +127.3% | +29.1% | -51.8% | +115.5% |
| regional_model_gain_ge_1F | 20 | +15.0% | +40.0% | -100.0% | +205.1% | +5.2% | -132.9% | +170.2% |

Recent:

| label | rows | win_rate | roi | roi_ci_low | roi_ci_high | delta_vs_complement | delta_vs_complement_ci_low | delta_vs_complement_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_hot_dist_gt0 | 58 | +17.2% | +76.7% | +32.2% | +133.6% |  |  |  |
| best_reliable_AB | 44 | +20.5% | +110.0% | +29.3% | +197.9% | +128.6% | -68.2% | +293.6% |
| exclude_best_D | 53 | +17.0% | +74.7% | +12.8% | +142.8% | -18.2% | -178.1% | +237.0% |
| source_gap_le_0p5F | 28 | +25.0% | +136.2% | +15.6% | +268.9% | +114.5% | -67.5% | +329.0% |
| source_gap_le_1F | 37 | +27.0% | +147.7% | +81.2% | +228.6% | +247.7% | +181.2% | +328.6% |
| source_matches_old_best | 38 | +15.8% | +55.7% | -47.6% | +173.3% | -51.8% | -216.9% | +147.6% |
| source_matches_enrichment_best | 18 | +27.8% | +150.1% | +19.7% | +288.7% | +105.2% | -40.3% | +267.2% |
| source_hot_underforecast_high | 20 | +15.0% | +71.8% | -54.3% | +211.3% | -7.7% | -255.0% | +201.0% |
| calibration_core_AB_and_gap_le_1F | 32 | +28.1% | +158.7% | +70.7% | +252.8% | +205.9% | +61.2% | +346.4% |
| calibration_strict_AB_and_gap_le_0p5F | 25 | +24.0% | +127.9% | -10.2% | +276.6% | +86.2% | -100.2% | +296.0% |
| regional_model_gain_ge_1F | 8 | +0.0% | -100.0% | -100.0% | -100.0% | -192.8% | -251.9% | -150.0% |

## Reliability Buckets

`best_reliability_bucket` uses the multi-model best MAE per city: A <=1.25F, B <=2.0F, C <=2.5F, D >2.5F.

| best_reliability_bucket | rows | dates | cities | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A_<=1.25F | 91 | 40 | 17 | +15.4% | 0.10 | +53.4% | -11.2% | +123.0% | +0.2% |
| B_1.25-2F | 111 | 44 | 23 | +16.2% | 0.10 | +55.4% | +0.6% | +110.9% | +12.5% |
| C_2-2.5F | 21 | 19 | 13 | +9.5% | 0.10 | -8.1% | -100.0% | +132.3% | -100.0% |
| D_>2.5F | 32 | 20 | 8 | +6.2% | 0.11 | -48.7% | -100.0% | +24.8% | -100.0% |
| missing | 78 | 37 | 25 | +17.9% | 0.11 | +62.1% | -9.7% | +138.4% | +7.1% |

## Active Source Gap Buckets

`source_gap_to_best_f` is the active HeadA source MAE minus the best available model MAE for that city. This is diagnostic, not a source switch replay.

| source_gap_bucket | rows | dates | cities | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.25-0.5F | 42 | 28 | 18 | +14.3% | 0.09 | +67.4% | -38.5% | +185.1% | -74.2% |
| 0.5-1F | 49 | 31 | 12 | +14.3% | 0.11 | +35.2% | -43.2% | +119.0% | -60.2% |
| <=0.25F | 98 | 43 | 26 | +18.4% | 0.11 | +54.1% | -4.0% | +116.2% | +11.9% |
| >1F | 66 | 34 | 14 | +7.6% | 0.09 | -23.9% | -80.3% | +44.2% | -100.0% |
| missing | 78 | 37 | 25 | +17.9% | 0.11 | +62.1% | -9.7% | +138.4% | +7.1% |

## City Contribution

Top contributors:

| label | rows | win_rate | roi | pnl | best_model_label | best_reliability_bucket | source_model_key | source_gap_to_best_f |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Amsterdam | 16 | +37.5% | +183.9% | 37.57 |  | missing |  | 0.67 |
| Madrid | 4 | +75.0% | +392.2% | 22.31 | GDPS | B_1.25-2F | ecmwf_ifs025 | 0.14 |
| Shanghai | 14 | +35.7% | +168.4% | 21.33 |  | missing |  | 0.00 |
| Moscow | 12 | +33.3% | +137.0% | 20.81 | missing | missing | missing |  |
| BuenosAires | 8 | +37.5% | +143.3% | 17.67 |  | missing |  | 0.01 |
| Busan | 3 | +66.7% | +412.7% | 14.49 |  | missing |  | 0.20 |
| KualaLumpur | 4 | +50.0% | +278.9% | 13.25 | ICON | C_2-2.5F | ecmwf_ifs025 | 1.14 |
| Manila | 13 | +23.1% | +124.0% | 11.07 | GFS Global | B_1.25-2F | gfs_global | 0.00 |
| Warsaw | 9 | +22.2% | +115.0% | 9.63 |  | missing |  | 0.00 |
| Istanbul | 3 | +33.3% | +229.9% | 6.97 |  | missing |  | 0.37 |

Worst contributors:

| label | rows | win_rate | roi | pnl | best_model_label | best_reliability_bucket | source_model_key | source_gap_to_best_f |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Shenzhen | 12 | +0.0% | -100.0% | -12.55 | missing | missing | missing |  |
| Ankara | 16 | +0.0% | -100.0% | -11.67 |  | missing |  | 2.25 |
| Munich | 15 | +0.0% | -100.0% | -10.46 | ICON-D2 | B_1.25-2F | ecmwf_ifs025 | 0.22 |
| Austin | 9 | +0.0% | -100.0% | -9.23 | GFS | D_>2.5F | gfs_seamless | 0.00 |
| Chicago | 9 | +0.0% | -100.0% | -9.22 |  | missing |  | 0.88 |
| Beijing | 15 | +0.0% | -100.0% | -8.85 |  | missing |  | 0.38 |
| TelAviv | 13 | +0.0% | -100.0% | -7.68 |  | missing |  | 0.27 |
| Seattle | 4 | +0.0% | -100.0% | -5.31 | GDPS | D_>2.5F | gfs_seamless | 0.73 |
| Lucknow | 5 | +0.0% | -100.0% | -5.17 | ICON | C_2-2.5F | ecmwf_ifs025 | 0.47 |
| CapeTown | 4 | +0.0% | -100.0% | -4.97 | ECMWF | B_1.25-2F | ecmwf_ifs025 | 0.00 |

## Interpretation

1. City/source matters, but not as a clean whitelist. Reliability buckets are heavily entangled with geography, market attention, and the original fixed city-source routing.
2. `source_gap_to_best` is the most actionable diagnostic: if the active source is much worse than the as-of city best model, HeadA is probably relying on a noisy forecast ceiling. That should feed shadow confidence and future PIT source enrichment.
3. A live source switch is not supported by this report because the alternate model forecast max was not available as a time-aligned decision feature for historical HeadA rows.
4. Recommended next step: materialize per-model PIT forecast max into canonical facts, then replay HeadA candidate generation under source policies `assigned_old`, `best_mae_city`, and `source_ensemble_quantile`. Until then, keep current live selector and record these calibration tags.
