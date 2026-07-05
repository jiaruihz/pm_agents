# Temperature Path Mechanism Decomposition v1

Generated: 2026-07-05T05:45:08+00:00

Scope: research foundation only.  No live config, runner, order, or sizing behavior changed.

## Verdict

The clean way to carry yesterday's `trend3h_positive` finding forward is to promote it into a shared path feature family, not to freeze it as a trading filter.

Use these names going forward:

```text
trend3h_bucket: cooling_lt_neg0_5 / flat_abs_lt0_5 / warming_0_5_to_2 / strong_warming_ge2
trend3h_warming_ge0_5: clean sustained warming
sustained_warming_1h3h: 1h uptick confirmed by 3h path
one_hour_warm_without_3h: noisy/spiky reheat warning
runway_sustained_warming: sustained warming + open/marginal forecast space
late_reheat_after_dip: HeadB-style false-fade/reheat conflict
exclude_trend3h_flat: route-specific tmax/NO-side selector variant, not a universal mechanism
```

Read: `trend3h_positive` is real enough to keep as shared telemetry/context, especially for HeadB reheat confirmation, but it is not a standalone live gate.  Mechanism labels must feed probability heads or shadow telemetry first; expression selection still needs ask/depth/fresh-forward proof.

## Data Snapshot

- `fact_signal_candidates`: 46007 rows, 2026-05-05..2026-07-06, built 2026-07-05T05:35:54.929962+00:00.
- `fact_trades`: 4483 rows, 2026-05-06..2026-07-05, built 2026-07-05T05:35:22.871183+00:00.
- CLOB fill coverage gate: `True`; fail_reasons=[].
- Atlas state rows: 13725 rows, 2026-05-19..2026-07-03, 36 cities.
- Labelled mechanism rows: 12972 rows, 2026-05-19..2026-07-02, 41 dates.

Note: the latest canonical fact layer is fresher than the intraday atlas.  The current atlas feature layer has state rows through 2026-07-03; rows after that need a refreshed observed-path feature factory before they should enter this mechanism report.

## Mechanism Dictionary

| mechanism | definition | recommended_use |
| --- | --- | --- |
| trend3h_cooling_lt_neg0_5 | temp_trend_3h_f < -0.5F | shared_context_feature |
| trend3h_flat_abs_lt0_5 | -0.5F <= temp_trend_3h_f < +0.5F | shared_context_feature |
| trend3h_warming_ge0_5 | temp_trend_3h_f >= +0.5F | shared_context_feature |
| sustained_warming_1h3h | temp_trend_1h_f >= +0.5F AND temp_trend_3h_f >= +0.5F | shared_context_feature_headb_telemetry |
| one_hour_warm_without_3h | temp_trend_1h_f >= +0.5F AND temp_trend_3h_f < +0.5F | diagnostic_context_feature |
| runway_sustained_warming | temp_trend_3h_f >= +0.5F AND day_regime in {day_open_runway, day_marginal_runway} | shared_context_feature |
| solar_runway_sustained_warming | runway_sustained_warming AND solar_window in {late_morning, solar_peak_window} | shared_context_feature_shadow_only_if_used_for_selection |
| late_reheat_after_dip | temp_trend_3h_f >= +0.5F AND intraday_state in {false_fade_risk, reheating_after_dip} | headb_specific_shadow_telemetry |
| humid_or_cloud_warming | temp_trend_3h_f >= +0.5F AND moisture_cloud_regime in humid/cloud suppression families | diagnostic_context_feature |
| mature_cooling_or_fade | temp_trend_3h_f < -0.5F AND intraday_state in {mature_fade, flat_or_cooling} | shared_context_feature |
| plateau_flat_path | abs(temp_trend_3h_f) < 0.5F AND intraday_state in {fresh_high, plateau_near_high, pullback_uncertain} | diagnostic_context_feature |

## Physical Outcome

This table asks whether the path label maps to the actual temperature path, before any trading expression.

| mechanism | rows | dates | support | future_break_rate | current_hold_rate | d1_hit_rate | d2_hit_rate | future_break_delta_vs_complement | d1_hit_delta_vs_complement | current_hold_delta_vs_complement |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all_labeled_states | 12972 | 41 | support_ok | 41.3% | 56.4% | 15.2% | 10.1% |  |  |  |
| trend3h_cooling_lt_neg0_5 | 3487 | 41 | support_ok | 2.2% | 87.9% | 1.8% | 0.9% | -53.5% | -18.3% | +43.0% |
| trend3h_flat_abs_lt0_5 | 1793 | 41 | support_ok | 14.3% | 82.5% | 9.7% | 2.8% | -31.3% | -6.4% | +30.2% |
| trend3h_warming_ge0_5 | 7670 | 41 | support_ok | 65.5% | 36.1% | 22.6% | 16.0% | +59.1% | +18.1% | -49.8% |
| trend3h_strong_warming_ge2 | 5833 | 41 | support_ok | 74.9% | 27.4% | 22.8% | 18.8% | +61.0% | +13.8% | -52.7% |
| legacy_trend3h_positive_gt0 | 7674 | 41 | support_ok | 65.5% | 36.1% | 22.6% | 16.0% | +59.2% | +18.2% | -49.9% |
| sustained_warming_1h3h | 4813 | 41 | support_ok | 77.7% | 24.6% | 22.8% | 18.7% | +57.9% | +12.1% | -50.7% |
| one_hour_warm_without_3h | 340 | 41 | support_ok | 17.1% | 78.8% | 9.4% | 5.0% | -24.9% | -5.9% | +23.0% |
| runway_sustained_warming | 4107 | 40 | support_ok | 81.3% | 19.4% | 19.6% | 20.7% | +58.6% | +6.4% | -54.2% |
| solar_runway_sustained_warming | 3503 | 39 | support_ok | 89.4% | 11.7% | 18.7% | 22.9% | +65.8% | +4.8% | -61.3% |
| late_reheat_after_dip | 751 | 41 | support_ok | 50.2% | 48.6% | 20.8% | 10.5% | +9.4% | +5.9% | -8.3% |
| humid_or_cloud_warming | 1662 | 40 | support_ok | 67.9% | 32.4% | 21.5% | 16.7% | +30.5% | +7.3% | -27.5% |
| mature_cooling_or_fade | 2560 | 41 | support_ok | 1.5% | 86.5% | 1.6% | 0.6% | -49.6% | -16.9% | +37.4% |
| plateau_flat_path | 953 | 40 | support_ok | 17.3% | 80.8% | 13.1% | 2.7% | -25.9% | -2.3% | +26.3% |

## Expression Sanity

Atlas expression rows are broad same-snapshot diagnostics.  They are not live fills and do not include fresh-book fill feasibility.

| mechanism | expression | rows | dates | avg_ask | win_rate | roi | roi_ci_low | roi_ci_high | roi_delta_vs_complement | recent_rows | recent_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all_labeled_states | current_bracket_no | 11071 | 41 | 0.362 | 33.9% | -43.1% | -48.8% | -36.7% |  | 1875 | -46.7% |
| all_labeled_states | current_yes | 10744 | 41 | 0.621 | 60.6% | -4.0% | -19.3% | +14.8% |  | 1837 | +6.1% |
| all_labeled_states | d1_no | 10489 | 41 | 0.827 | 81.2% | -3.6% | -4.7% | -2.5% |  | 1809 | -4.2% |
| all_labeled_states | d2_no | 8923 | 41 | 0.873 | 85.3% | -3.0% | -4.5% | -1.1% |  | 1482 | -4.4% |
| late_reheat_after_dip | current_bracket_no | 640 | 41 | 0.457 | 43.0% | -18.1% | -32.4% | -1.2% | +26.5% | 101 | -32.8% |
| late_reheat_after_dip | current_yes | 655 | 41 | 0.572 | 54.5% | +57.0% | -27.6% | +208.0% | +64.9% | 103 | +20.8% |
| late_reheat_after_dip | d1_no | 658 | 41 | 0.807 | 76.3% | -7.7% | -12.2% | -3.1% | -4.4% | 106 | -8.7% |
| late_reheat_after_dip | d2_no | 604 | 41 | 0.878 | 86.9% | -0.5% | -5.1% | +4.6% | +2.7% | 97 | -6.1% |
| mature_cooling_or_fade | current_bracket_no | 2251 | 41 | 0.022 | 1.6% | -88.2% | -95.7% | -76.8% | -56.6% | 339 | -85.3% |
| mature_cooling_or_fade | current_yes | 1709 | 41 | 0.981 | 97.8% | -0.7% | -1.8% | +0.3% | +3.8% | 287 | -1.5% |
| mature_cooling_or_fade | d1_no | 1375 | 41 | 0.972 | 96.9% | -1.7% | -3.1% | -0.5% | +2.2% | 235 | -0.2% |
| mature_cooling_or_fade | d2_no | 674 | 40 | 0.977 | 97.6% | -1.2% | -2.9% | +0.3% | +1.9% | 75 | -3.9% |
| runway_sustained_warming | current_bracket_no | 2958 | 40 | 0.746 | 73.0% | -5.1% | -13.3% | +5.6% | +51.9% | 288 | +7.4% |
| runway_sustained_warming | current_yes | 3272 | 40 | 0.260 | 23.5% | -9.7% | -51.1% | +43.4% | -8.2% | 303 | -53.7% |
| runway_sustained_warming | d1_no | 3490 | 40 | 0.782 | 77.0% | -2.6% | -5.2% | +0.0% | +1.4% | 329 | +1.8% |
| runway_sustained_warming | d2_no | 3628 | 40 | 0.795 | 76.5% | -4.1% | -7.3% | -0.1% | -2.0% | 334 | -11.0% |
| sustained_warming_1h3h | current_bracket_no | 3560 | 41 | 0.687 | 66.8% | -8.8% | -14.5% | -1.8% | +50.6% | 608 | -5.7% |
| sustained_warming_1h3h | current_yes | 3980 | 41 | 0.315 | 28.8% | -16.6% | -44.3% | +21.3% | -20.1% | 645 | -45.0% |
| sustained_warming_1h3h | d1_no | 4158 | 41 | 0.750 | 73.6% | -4.1% | -6.3% | -1.7% | -0.8% | 697 | -5.1% |
| sustained_warming_1h3h | d2_no | 4185 | 41 | 0.816 | 78.5% | -4.0% | -6.5% | -1.0% | -2.1% | 713 | -4.7% |
| trend3h_flat_abs_lt0_5 | current_bracket_no | 1694 | 40 | 0.158 | 12.8% | -61.8% | -77.1% | -38.5% | -22.1% | 298 | -81.1% |
| trend3h_flat_abs_lt0_5 | current_yes | 1614 | 40 | 0.869 | 86.4% | +28.3% | -6.3% | +90.0% | +38.0% | 289 | +159.4% |
| trend3h_flat_abs_lt0_5 | d1_no | 1545 | 41 | 0.901 | 88.7% | -3.6% | -7.0% | -0.4% | +0.0% | 271 | -2.9% |
| trend3h_flat_abs_lt0_5 | d2_no | 1154 | 40 | 0.958 | 95.6% | -0.8% | -2.7% | +0.8% | +2.4% | 191 | -0.4% |
| trend3h_warming_ge0_5 | current_bracket_no | 6230 | 41 | 0.586 | 55.6% | -16.0% | -22.2% | -8.5% | +62.1% | 1066 | -18.2% |
| trend3h_warming_ge0_5 | current_yes | 6638 | 41 | 0.428 | 40.6% | -13.0% | -32.8% | +13.1% | -23.5% | 1104 | -30.9% |
| trend3h_warming_ge0_5 | d1_no | 6853 | 41 | 0.767 | 74.7% | -4.3% | -6.1% | -2.6% | -2.1% | 1164 | -5.7% |
| trend3h_warming_ge0_5 | d2_no | 6664 | 41 | 0.841 | 81.6% | -3.6% | -5.4% | -1.3% | -2.4% | 1135 | -5.3% |

## HeadB Check

HeadB remains the METAR rich-current / runway d1 YES reversal family.  This section only checks whether the path expressions improve its shadow denominator; it does not import HeadA maker-first or TP20 assumptions.

| strategy | mechanism | rows | dates | support | win_rate | avg_entry_stress | roi | roi_ci_low | roi_ci_high | roi_delta_vs_complement | recent_rows | recent_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| false_fade_reheat_conflict_d1_yes | all_labeled_states | 47 | 25 | support_ok | 29.8% | 0.155 | +45.5% | -34.5% | +146.9% |  | 1 | -100.0% |
| false_fade_reheat_conflict_d1_yes | humid_or_cloud_warming | 8 | 7 | thin_do_not_select | 50.0% | 0.216 | +118.0% | -55.5% | +296.4% | +87.3% | 0 |  |
| false_fade_reheat_conflict_d1_yes | late_reheat_after_dip | 7 | 5 | thin_do_not_select | 71.4% | 0.220 | +201.7% | +6.6% | +362.2% | +183.5% | 0 |  |
| false_fade_reheat_conflict_d1_yes | legacy_trend3h_positive_gt0 | 33 | 22 | support_ok | 39.4% | 0.168 | +95.6% | -16.3% | +220.0% | +168.0% | 0 |  |
| false_fade_reheat_conflict_d1_yes | runway_sustained_warming | 33 | 22 | support_ok | 39.4% | 0.168 | +95.6% | -16.3% | +220.0% | +168.0% | 0 |  |
| false_fade_reheat_conflict_d1_yes | solar_runway_sustained_warming | 20 | 16 | support_ok | 50.0% | 0.202 | +134.7% | +0.3% | +262.9% | +155.3% | 0 |  |
| false_fade_reheat_conflict_d1_yes | sustained_warming_1h3h | 33 | 22 | support_ok | 39.4% | 0.168 | +95.6% | -16.3% | +220.0% | +168.0% | 0 |  |
| false_fade_reheat_conflict_d1_yes | trend3h_warming_ge0_5 | 33 | 22 | support_ok | 39.4% | 0.168 | +95.6% | -16.3% | +220.0% | +168.0% | 0 |  |
| rich_current_b4_d1_yes | all_labeled_states | 70 | 29 | support_ok | 32.9% | 0.227 | +26.0% | -34.1% | +96.7% |  | 0 |  |
| rich_current_b4_d1_yes | humid_or_cloud_warming | 13 | 11 | thin_do_not_select | 53.8% | 0.294 | +87.4% | -39.2% | +220.6% | +75.4% | 0 |  |
| rich_current_b4_d1_yes | late_reheat_after_dip | 10 | 7 | thin_do_not_select | 60.0% | 0.225 | +110.9% | -17.9% | +279.2% | +99.0% | 0 |  |
| rich_current_b4_d1_yes | legacy_trend3h_positive_gt0 | 51 | 27 | support_ok | 37.3% | 0.258 | +35.3% | -28.6% | +115.0% | +34.3% | 0 |  |
| rich_current_b4_d1_yes | runway_sustained_warming | 48 | 25 | support_ok | 39.6% | 0.261 | +43.8% | -22.5% | +130.0% | +56.5% | 0 |  |
| rich_current_b4_d1_yes | solar_runway_sustained_warming | 31 | 20 | support_ok | 38.7% | 0.286 | +34.0% | -34.9% | +110.5% | +14.2% | 0 |  |
| rich_current_b4_d1_yes | sustained_warming_1h3h | 51 | 27 | support_ok | 37.3% | 0.258 | +35.3% | -28.6% | +115.0% | +34.3% | 0 |  |
| rich_current_b4_d1_yes | trend3h_warming_ge0_5 | 51 | 27 | support_ok | 37.3% | 0.258 | +35.3% | -28.6% | +115.0% | +34.3% | 0 |  |

## Clean Expressions To Reuse

1. `trend3h_bucket` is the canonical foundation field.  It is a context label with four states; it should be recorded on every strategy-head row.
2. `trend3h_warming_ge0_5` is the clean replacement for loose `trend3h_positive` when the intended mechanism is sustained warming.
3. `sustained_warming_1h3h` is the professional HeadB confirmation: HeadB already asks for a 1h uptick, and this prevents a single METAR jump from masquerading as real reheat.
4. `one_hour_warm_without_3h` should be carried as a warning/diagnostic label, not a selector.
5. `runway_sustained_warming` is the shared regime-compatible expression: observed warming plus forecast space.  It belongs in tmax/regime probability heads, not as a hard gate.
6. `late_reheat_after_dip` is the HeadB-specific conflict label.  It is mechanism-clear but thinner, so it stays shadow telemetry until fresh-forward rows accumulate.
7. `exclude_trend3h_flat` is only a route-specific selector variant for the tmax clean-edge work.  Do not call it `no_trend3h_flat` without spelling out that it means removing flat 3h rows.

## Contract Read

```text
significance=NA for mechanism foundation labels
baseline=NA for context labels; expression rows include same-snapshot sanity only
forward=FAIL/NA for live action because atlas coverage after 2026-07-03 is not yet refreshed and HeadB fresh-forward rows remain sparse
conclusion=inconclusive_for_live, promote_as_shared_context_feature_and_shadow_telemetry
```

8-ring coverage: [1] descriptive slices covered, [2] date-block CIs included for expression/physical summaries, [3] signal discrimination partially covered by physical outcome rates, [4] probability calibration not covered, [5] execution microstructure not covered here, [6] capacity not covered, [7] date clustering handled by block bootstrap, [8] market-expression sanity included but not sufficient for live.

## Artifacts

- Script: `scripts/analysis/reheat_risk/research_temperature_path_mechanism_decomposition_v1.py`
- JSON: `docs/analysis/2026-07/2026-07-05-temperature-path-mechanism-decomposition-v1.json`
- Mechanism dictionary: `docs/analysis/2026-07/generated/temperature_path_mechanism_decomposition_v1/mechanism_dictionary.csv`
- Physical summary: `docs/analysis/2026-07/generated/temperature_path_mechanism_decomposition_v1/mechanism_physics_summary.csv`
- Expression summary: `docs/analysis/2026-07/generated/temperature_path_mechanism_decomposition_v1/mechanism_expression_summary.csv`
- HeadB summary: `docs/analysis/2026-07/generated/temperature_path_mechanism_decomposition_v1/headb_mechanism_summary.csv`
- Trend bucket x regime: `docs/analysis/2026-07/generated/temperature_path_mechanism_decomposition_v1/trend3h_bucket_by_day_regime.csv`
