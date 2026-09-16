# Temperature Path Mechanism Decomposition v1

Generated: 2026-07-05T06:21:47+00:00

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
- Atlas state rows: 13860 rows, 2026-05-19..2026-07-04, 36 cities.
- Labelled mechanism rows: 13852 rows, 2026-05-19..2026-07-04, 46 dates.
- Atlas freshness preflight: status `fresh`, target `2026-07-04`, label-required `2026-07-03`, state max `2026-07-04`, labeled max `2026-07-04`.

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
| all_labeled_states | 13852 | 46 | support_ok | 41.1% | 55.8% | 15.3% | 10.2% |  |  |  |
| trend3h_cooling_lt_neg0_5 | 3670 | 46 | support_ok | 2.3% | 87.4% | 2.0% | 0.9% | -52.7% | -18.1% | +43.1% |
| trend3h_flat_abs_lt0_5 | 1908 | 45 | support_ok | 14.2% | 82.3% | 9.9% | 3.0% | -31.2% | -6.3% | +30.8% |
| trend3h_warming_ge0_5 | 8142 | 46 | support_ok | 65.4% | 35.8% | 22.7% | 16.1% | +59.1% | +17.9% | -48.4% |
| trend3h_strong_warming_ge2 | 6211 | 46 | support_ok | 74.8% | 27.2% | 22.9% | 18.9% | +61.2% | +13.8% | -51.8% |
| legacy_trend3h_positive_gt0 | 8146 | 46 | support_ok | 65.4% | 35.8% | 22.7% | 16.1% | +59.1% | +17.9% | -48.5% |
| sustained_warming_1h3h | 5112 | 46 | support_ok | 77.8% | 24.2% | 22.9% | 18.8% | +58.3% | +12.0% | -50.0% |
| one_hour_warm_without_3h | 366 | 45 | support_ok | 16.9% | 78.7% | 9.6% | 5.2% | -24.8% | -5.9% | +23.6% |
| runway_sustained_warming | 4278 | 45 | support_ok | 81.3% | 19.2% | 19.5% | 20.7% | +58.2% | +6.0% | -52.8% |
| solar_runway_sustained_warming | 3650 | 44 | support_ok | 89.3% | 11.5% | 18.5% | 22.8% | +65.5% | +4.4% | -60.0% |
| late_reheat_after_dip | 791 | 45 | support_ok | 49.4% | 49.1% | 20.6% | 10.4% | +8.9% | +5.6% | -7.1% |
| humid_or_cloud_warming | 1745 | 45 | support_ok | 67.6% | 32.4% | 21.4% | 16.6% | +30.4% | +6.9% | -26.7% |
| mature_cooling_or_fade | 2674 | 45 | support_ok | 1.5% | 86.3% | 1.7% | 0.6% | -49.0% | -16.9% | +37.8% |
| plateau_flat_path | 1035 | 44 | support_ok | 17.3% | 80.3% | 13.5% | 3.1% | -25.7% | -1.9% | +26.5% |

## Expression Sanity

Atlas expression rows are broad same-snapshot diagnostics.  They are not live fills and do not include fresh-book fill feasibility.

| mechanism | expression | rows | dates | avg_ask | win_rate | roi | roi_ci_low | roi_ci_high | roi_delta_vs_complement | recent_rows | recent_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all_labeled_states | current_bracket_no | 11699 | 45 | 0.364 | 34.2% | -42.9% | -48.5% | -36.6% |  | 2503 | -44.6% |
| all_labeled_states | current_yes | 11398 | 46 | 0.620 | 60.4% | -5.6% | -20.5% | +12.5% |  | 2491 | -3.9% |
| all_labeled_states | d1_no | 11168 | 46 | 0.827 | 81.0% | -4.0% | -5.1% | -2.8% |  | 2488 | -5.7% |
| all_labeled_states | d2_no | 9517 | 46 | 0.871 | 85.1% | -2.9% | -4.4% | -1.1% |  | 2076 | -3.7% |
| late_reheat_after_dip | current_bracket_no | 671 | 45 | 0.451 | 42.2% | -19.4% | -33.6% | -3.5% | +24.9% | 132 | -35.9% |
| late_reheat_after_dip | current_yes | 686 | 45 | 0.577 | 55.2% | +54.1% | -26.5% | +201.3% | +63.5% | 134 | +14.2% |
| late_reheat_after_dip | d1_no | 692 | 45 | 0.806 | 76.4% | -7.6% | -12.0% | -3.1% | -3.9% | 140 | -7.8% |
| late_reheat_after_dip | d2_no | 631 | 45 | 0.879 | 87.0% | -0.4% | -4.9% | +4.7% | +2.7% | 124 | -4.3% |
| mature_cooling_or_fade | current_bracket_no | 2346 | 45 | 0.023 | 1.7% | -88.6% | -95.9% | -77.3% | -57.1% | 434 | -87.9% |
| mature_cooling_or_fade | current_yes | 1788 | 45 | 0.980 | 97.8% | -0.8% | -1.8% | +0.2% | +5.7% | 366 | -1.5% |
| mature_cooling_or_fade | d1_no | 1445 | 45 | 0.972 | 96.9% | -1.8% | -3.0% | -0.6% | +2.5% | 305 | -0.9% |
| mature_cooling_or_fade | d2_no | 709 | 44 | 0.976 | 97.6% | -1.2% | -2.9% | +0.3% | +1.9% | 110 | -3.1% |
| runway_sustained_warming | current_bracket_no | 3067 | 44 | 0.747 | 73.2% | -5.5% | -13.3% | +4.9% | +50.7% | 397 | +1.0% |
| runway_sustained_warming | current_yes | 3396 | 45 | 0.259 | 23.4% | -11.6% | -51.3% | +42.3% | -8.6% | 427 | -56.0% |
| runway_sustained_warming | d1_no | 3627 | 45 | 0.783 | 77.1% | -2.8% | -5.2% | -0.3% | +1.8% | 466 | -0.4% |
| runway_sustained_warming | d2_no | 3773 | 45 | 0.795 | 76.5% | -4.2% | -7.3% | -0.1% | -2.1% | 479 | -9.5% |
| sustained_warming_1h3h | current_bracket_no | 3774 | 45 | 0.688 | 67.3% | -8.1% | -13.7% | -1.6% | +51.3% | 822 | -3.4% |
| sustained_warming_1h3h | current_yes | 4218 | 46 | 0.314 | 28.4% | -19.2% | -45.8% | +16.2% | -21.7% | 883 | -49.9% |
| sustained_warming_1h3h | d1_no | 4411 | 46 | 0.752 | 73.5% | -4.5% | -6.7% | -2.2% | -0.9% | 950 | -6.9% |
| sustained_warming_1h3h | d2_no | 4442 | 46 | 0.816 | 78.4% | -3.9% | -6.3% | -0.7% | -1.8% | 970 | -3.7% |
| trend3h_flat_abs_lt0_5 | current_bracket_no | 1799 | 44 | 0.159 | 12.8% | -62.5% | -77.0% | -40.4% | -23.1% | 403 | -78.9% |
| trend3h_flat_abs_lt0_5 | current_yes | 1716 | 44 | 0.869 | 86.4% | +26.2% | -6.2% | +83.3% | +37.5% | 391 | +116.1% |
| trend3h_flat_abs_lt0_5 | d1_no | 1652 | 45 | 0.898 | 88.6% | -3.8% | -7.1% | -0.7% | +0.2% | 378 | -4.0% |
| trend3h_flat_abs_lt0_5 | d2_no | 1229 | 44 | 0.957 | 95.4% | -1.1% | -2.8% | +0.6% | +2.1% | 266 | -1.8% |
| trend3h_warming_ge0_5 | current_bracket_no | 6596 | 45 | 0.586 | 55.9% | -15.5% | -21.6% | -8.0% | +62.9% | 1432 | -15.2% |
| trend3h_warming_ge0_5 | current_yes | 7035 | 46 | 0.428 | 40.4% | -15.0% | -34.3% | +10.1% | -24.5% | 1501 | -35.5% |
| trend3h_warming_ge0_5 | d1_no | 7268 | 46 | 0.768 | 74.6% | -4.6% | -6.4% | -3.0% | -1.9% | 1579 | -6.8% |
| trend3h_warming_ge0_5 | d2_no | 7060 | 46 | 0.841 | 81.5% | -3.5% | -5.3% | -1.2% | -2.2% | 1531 | -4.5% |

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
