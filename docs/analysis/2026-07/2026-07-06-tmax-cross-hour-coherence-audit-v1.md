# Tmax Cross-Hour Coherence Audit v1

> generated_at_utc: `2026-07-06T14:28:42+00:00`
> Scope: research-only P0e/P0c/P0b + execution replay; no tmax live runner/config/order behavior changed.

## 数据快照

- Mac market_data sync: `scripts/ops/sync_weather_remote.sh --market-source=mac-weather-data-feed --market-only` ran before this report.
- Materialized tmax rows used here: `8028` rows, `2026-05-19`..`2026-07-03`, `36` cities.
- Inventory: `{'fact_signal_candidates': {'rows': 47113, 'min_date': '2026-05-05', 'max_date': '2026-07-07'}, 'fact_trades': {'rows': 4529, 'min_date': '2026-05-06', 'max_date': '2026-07-06'}, 'settlement_outcomes': {'rows': 28241, 'min_date': '2026-05-04', 'max_date': '2026-07-05'}, 'atlas': {'rows': 13860, 'min_date': '2026-05-19', 'max_date': '2026-07-04'}, 'p5_opportunities': {'rows': 196992, 'min_date': '2026-06-02', 'max_date': '2026-07-03'}, 'p6_shadow_events': {'rows': 16416, 'min_date': '2026-06-02', 'max_date': '2026-07-03'}}`
- P4 counters: `{'raw_rows': 13860, 'missing_or_invalid_label': 4116, 'missing_interval': 0, 'missing_market_quote': 1716, 'observed_derived_before_extension_window': 0, 'scored_rows': 8028, 'label_sources_raw': {'settlement_outcomes': 9744, 'missing': 4116}}`
- Important limit: raw snapshots are newer than the materialized P0/P5/P6 research layer. This report evaluates the currently materialized atlas/P5 layer, not a 7/05+ fully rebuilt tmax layer.

## 验收口径冻结

- P0e: adjacent city-day-hour pairs; coherent posterior is previous four-bucket distribution conditioned on the newly reached bracket. `incoherence = refit P(current) - coherent P(current)`.
- P0c: one-vs-rest `p_current` reliability for `loo_no_city_source_blend`; key slice is `pre_or_at_peak × warming × ceiling >= +2C`.
- P0b: settlement-basis replay tries to add below-current settlement rows back as current-YES losers / NO winners. If this run reports zero below rows, the finding is a materializer gap, not evidence that basis risk is absent.
- Execution replay: first-lock vs target-book reconciliation on the same P5 opportunity denominator; no live behavior changed.

## 结论

- P0e supports the broader `path-coherence is model debt` thesis, but it does **not** cleanly confirm the narrow `new-high reanchor always overstates hold` story. In this materialized sample, `no_reanchor` has the largest positive incoherence; d1 reanchor is weak/unstable and d2 reanchor is negative.
- P0c does not show a robust high-ceiling warming overestimate in the current verified-forward materialized layer; the key slice is only 4 rows. This means Lucknow cannot be generalized from this slice yet.
- P0b is not answered by the current materialized layer: the replay found zero below-current rows. That means the E2 settlement-basis state is still missing upstream; current-YES remains shadow until below/current is represented as a real model target.
- Execution replay remains research-only. Target-book reconciliation should be implemented as a position ledger before any future live restart, but this report is not live approval.

## P0e Cross-Hour Coherence

| grouping | scope | reanchor_group | rows | dates | cities | incoherence_mean | incoherence_median | positive_rate | incoherence_ci_low | incoherence_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| reanchor_group | nan | d1_reanchor | 1292 | 31 | 36 | +0.9% | +0.4% | +53.9% | -0.2% | +1.6% |
| reanchor_group | nan | d2_reanchor | 246 | 31 | 36 | -5.2% | -3.3% | +28.5% | -7.0% | -3.8% |
| reanchor_group | nan | no_reanchor | 2941 | 31 | 36 | +7.1% | +3.6% | +78.3% | +6.6% | +7.8% |
| scope+reanchor_group | dev_cv | d1_reanchor | 870 | 19 | 36 | +1.8% | +0.6% | +56.3% | +0.6% | +2.8% |
| scope+reanchor_group | dev_cv | d2_reanchor | 170 | 19 | 36 | -4.9% | -3.0% | +32.9% | -6.6% | -2.3% |
| scope+reanchor_group | dev_cv | no_reanchor | 2039 | 19 | 36 | +7.2% | +3.8% | +79.0% | +6.4% | +7.9% |
| scope+reanchor_group | verified_forward | d1_reanchor | 422 | 12 | 36 | -1.0% | -0.1% | +48.8% | -1.9% | +0.6% |
| scope+reanchor_group | verified_forward | d2_reanchor | 76 | 12 | 28 | -5.9% | -4.8% | +18.4% | -9.2% | -4.3% |
| scope+reanchor_group | verified_forward | no_reanchor | 902 | 12 | 36 | +7.0% | +3.1% | +76.8% | +5.9% | +8.1% |

## P0c Key Calibration Slice

| scope | peak_phase | trend3h_bucket | ceiling_margin_bucket | rows | dates | cities | mean_p_current | actual_current_rate | calibration_error | brier_current |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dev_cv | pre_or_at_peak | warming | >=+2C | 450 | 19 | 33 | 11.4% | 11.1% | +0.3% | 0.058371533664906966 |
| verified_forward | pre_or_at_peak | warming | >=+2C | 4 | 1 | 1 | 29.8% | 50.0% | -20.2% | 0.09913480193029078 |

## P0c Reliability Deciles

| scope | p_decile | rows | mean_p_current | actual_current_rate | calibration_error | brier_current |
| --- | --- | --- | --- | --- | --- | --- |
| dev_cv | (0.00022999999999999995, 0.0138] | 369 | 0.8% | 0.5% | +0.3% | 0.005415299692638216 |
| dev_cv | (0.0138, 0.0423] | 345 | 2.6% | 0.3% | +2.3% | 0.003459607214549242 |
| dev_cv | (0.0423, 0.123] | 378 | 7.6% | 7.1% | +0.5% | 0.06573478797813309 |
| dev_cv | (0.123, 0.316] | 357 | 21.3% | 20.2% | +1.1% | 0.15063933595770243 |
| dev_cv | (0.316, 0.567] | 386 | 43.6% | 43.3% | +0.4% | 0.24214906931740823 |
| dev_cv | (0.567, 0.774] | 384 | 68.0% | 68.0% | +0.0% | 0.21175936685349225 |
| dev_cv | (0.774, 0.899] | 405 | 84.1% | 82.7% | +1.3% | 0.14012526320331645 |
| dev_cv | (0.899, 0.961] | 358 | 93.4% | 93.6% | -0.2% | 0.05934157945733374 |
| dev_cv | (0.961, 0.988] | 386 | 97.6% | 97.9% | -0.3% | 0.020187531294615167 |
| dev_cv | (0.988, 0.998] | 375 | 99.3% | 100.0% | -0.7% | 4.952618301595846e-05 |
| verified_forward | (0.00022999999999999995, 0.0138] | 179 | 0.8% | 0.0% | +0.8% | 7.107067250879691e-05 |
| verified_forward | (0.0138, 0.0423] | 202 | 2.4% | 0.5% | +1.9% | 0.005224636312853698 |
| verified_forward | (0.0423, 0.123] | 169 | 7.5% | 7.1% | +0.4% | 0.06656553376977341 |
| verified_forward | (0.123, 0.316] | 190 | 20.3% | 16.3% | +4.0% | 0.13881926275567988 |
| verified_forward | (0.316, 0.567] | 161 | 43.3% | 36.0% | +7.3% | 0.24347327289238047 |
| verified_forward | (0.567, 0.774] | 163 | 67.9% | 69.3% | -1.4% | 0.2014818040084011 |
| verified_forward | (0.774, 0.899] | 142 | 84.5% | 84.5% | +0.0% | 0.12917853520444397 |
| verified_forward | (0.899, 0.961] | 189 | 93.7% | 96.8% | -3.2% | 0.03191508179762025 |
| verified_forward | (0.961, 0.988] | 161 | 97.6% | 98.8% | -1.2% | 0.01247650970053615 |
| verified_forward | (0.988, 0.998] | 173 | 99.3% | 100.0% | -0.7% | 4.982522820871546e-05 |

## P0b Basis-Inclusive EV

This table is a sanity replay on the current materialized layer. `below_rows=0` means the known E2 below-current issue was not available to this materializer, so this table cannot clear current-YES.

| scope | expression | selected_rows | dates | below_rows | basis_below_rate | avg_ask | p_current_mean | win_rate | cost | pnl | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dev_cv | current_no | 262 | 19 | 0 | 0.0% | 0.439 | n/a | +50.4% | 114.913 | 17.087 | +14.9% | +3.9% | +25.5% |
| dev_cv | current_yes | 346 | 19 | 0 | 0.0% | 0.471 | 51.3% | +51.7% | 163.112 | 15.888 | +9.7% | -3.8% | +22.4% |
| dev_cv | d1_no | 448 | 19 | 0 | 0.0% | 0.587 | n/a | +60.3% | 263.127 | 6.873 | +2.6% | -3.5% | +8.4% |
| dev_cv | d2_no | 231 | 19 | 0 | 0.0% | 0.700 | n/a | +74.5% | 161.809 | 10.191 | +6.3% | -0.5% | +14.1% |
| verified_forward | current_no | 153 | 12 | 0 | 0.0% | 0.360 | n/a | +39.9% | 55.125 | 5.875 | +10.7% | -15.1% | +34.1% |
| verified_forward | current_yes | 102 | 11 | 0 | 0.0% | 0.390 | 43.6% | +49.0% | 39.760 | 10.240 | +25.8% | -2.1% | +47.7% |
| verified_forward | d1_no | 133 | 12 | 0 | 0.0% | 0.493 | n/a | +56.4% | 65.579 | 9.421 | +14.4% | -2.6% | +29.5% |
| verified_forward | d2_no | 93 | 12 | 0 | 0.0% | 0.678 | n/a | +71.0% | 63.079 | 2.921 | +4.6% | -8.7% | +16.7% |

## P1b Alpha Ablation

| scope | alpha | rows | dates | logloss | market_logloss | logloss_delta_vs_market | brier | market_brier | brier_delta_vs_market |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| verified_forward | 0.75 | 1729 | 12 | 0.5782593215961929 | 0.6436771335758387 | -6.5% | 0.32178096623251967 | 0.32714586691331976 | -0.5% |
| verified_forward | 0.5 | 1729 | 12 | 0.5791337523948005 | 0.6436771335758387 | -6.5% | 0.32180122142886686 | 0.32714586691331976 | -0.5% |
| verified_forward | 1.0 | 1729 | 12 | 0.581793410634486 | 0.6436771335758387 | -6.2% | 0.32352875606666165 | 0.32714586691331976 | -0.4% |
| verified_forward | 0.25 | 1729 | 12 | 0.5843187450335111 | 0.6436771335758387 | -5.9% | 0.32358952165570276 | 0.32714586691331976 | -0.4% |
| verified_forward | 0.0 | 1729 | 12 | 0.6436771335758387 | 0.6436771335758387 | +0.0% | 0.3271458669133198 | 0.32714586691331976 | +0.0% |

## Execution Replay

| scope | rows | dates | cities | later_candidate_rows | replace_triggers | avg_ask | avg_close_bid | first_cost | first_pnl | first_roi | reconcile_cost | reconcile_pnl | reconcile_roi | net_delta_pnl | net_delta_roi | turnover_cost |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dev_cv | 512 | 19 | 36 | 321 | 51 | 0.551 | 0.515 | 287.14296165 | 13.85703835 | +4.8% | 310.773571 | 11.936135100000001 | +3.8% | -1.921 | -0.7% | 1.080 |
| verified_forward | 226 | 12 | 36 | 112 | 18 | 0.465 | 0.424 | 106.96364715 | 9.03635285 | +8.4% | 113.67323784999999 | 7.12956215 | +6.3% | -1.907 | -1.8% | 0.651 |

## 三道门

significance=FAIL/PARTIAL; baseline=PARTIAL; forward=FAIL/THIN; conclusion=`inconclusive_research_only`.

No live action. `current_yes` remains shadow. The next model step is a real five-bucket or hazard materializer, not another execution gate.

## Artifacts

- `docs/analysis/2026-07/generated/tmax_cross_hour_coherence_audit_v1/coherence_pairs.csv`
- `docs/analysis/2026-07/generated/tmax_cross_hour_coherence_audit_v1/coherence_summary.csv`
- `docs/analysis/2026-07/generated/tmax_cross_hour_coherence_audit_v1/current_calibration_rows.csv`
- `docs/analysis/2026-07/generated/tmax_cross_hour_coherence_audit_v1/current_calibration_summary.csv`
- `docs/analysis/2026-07/generated/tmax_cross_hour_coherence_audit_v1/basis_ev_opportunities.csv`
- `docs/analysis/2026-07/generated/tmax_cross_hour_coherence_audit_v1/basis_ev_summary.csv`
- `docs/analysis/2026-07/generated/tmax_cross_hour_coherence_audit_v1/alpha_ablation.csv`
- `docs/analysis/2026-07/generated/tmax_cross_hour_coherence_audit_v1/reconciliation_decisions.csv`
- `docs/analysis/2026-07/generated/tmax_cross_hour_coherence_audit_v1/reconciliation_summary.csv`
- `docs/analysis/2026-07/2026-07-06-tmax-cross-hour-coherence-audit-v1.json`
