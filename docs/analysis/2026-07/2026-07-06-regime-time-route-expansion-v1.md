# Regime Time/Route Expansion V1

## Conclusion

Verdict: `do_not_promote_time_or_shadow_route_expansion_from_current_evidence`.

This is a research/shadow replay. It evaluates route-price-disciplined entries with row-risk sizing, plus a current live-like execution gate (`base_notional_usd=9`, `min_order_shares=5`, `row_risk_soft_v1 / ask >= 1.0`). It does not change live policy.

## Forward Summary

| policy | sizing | rows | dates | cities | win_rate | avg_ask | avg_hour | cost_usd | pnl_usd | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_fresh_capped_10_14 | exec_gate_current | 4 | 3 | 4 | +50.0% | 0.375 | 12.750 | $+18.47 | $+3.15 | +17.1% | -100.0% | +112.8% |
| cheap_stale_only_10_14 | exec_gate_current | 1 | 1 | 1 | +100.0% | 0.210 | 10.000 | $+2.38 | $+8.94 | +376.2% | NA | NA |
| cheap_stale_only_10_17 | exec_gate_current | 1 | 1 | 1 | +100.0% | 0.210 | 10.000 | $+2.38 | $+8.94 | +376.2% | NA | NA |
| false_fade_only_10_14 | exec_gate_current | 0 | 0 | 0 | NA | NA | NA | $+0.00 | $+0.00 | NA | NA | NA |
| false_fade_only_10_17 | exec_gate_current | 0 | 0 | 0 | NA | NA | NA | $+0.00 | $+0.00 | NA | NA | NA |
| fresh_capped_extend_10_17 | exec_gate_current | 7 | 3 | 6 | +42.9% | 0.347 | 13.857 | $+29.98 | $+1.89 | +6.3% | -100.0% | +112.8% |
| incremental_fresh_capped_added_by_15_17 | exec_gate_current | 3 | 2 | 2 | +33.3% | 0.310 | 15.333 | $+11.52 | $-1.26 | -11.0% | NA | NA |
| no_pullback_yes_10_17 | exec_gate_current | 8 | 4 | 6 | +50.0% | 0.330 | 13.375 | $+32.36 | $+10.83 | +33.5% | -64.2% | +202.2% |
| shadow_tail_false_10_17 | exec_gate_current | 1 | 1 | 1 | +100.0% | 0.210 | 10.000 | $+2.38 | $+8.94 | +376.2% | NA | NA |
| baseline_fresh_capped_10_14 | research_row_soft | 9 | 4 | 7 | +33.3% | 0.464 | 12.333 | $+34.55 | $-8.85 | -25.6% | -71.0% | +6.3% |
| cheap_stale_only_10_14 | research_row_soft | 2 | 2 | 2 | +50.0% | 0.215 | 12.000 | $+2.85 | $+8.47 | +297.2% | NA | NA |
| cheap_stale_only_10_17 | research_row_soft | 2 | 2 | 2 | +50.0% | 0.215 | 12.000 | $+2.85 | $+8.47 | +297.2% | NA | NA |
| false_fade_only_10_14 | research_row_soft | 2 | 2 | 2 | +50.0% | 0.629 | 12.000 | $+2.65 | $+0.65 | +24.6% | NA | NA |
| false_fade_only_10_17 | research_row_soft | 2 | 2 | 2 | +50.0% | 0.629 | 12.000 | $+2.65 | $+0.65 | +24.6% | NA | NA |
| fresh_capped_extend_10_17 | research_row_soft | 14 | 4 | 11 | +35.7% | 0.432 | 13.500 | $+50.53 | $-9.47 | -18.7% | -80.5% | +25.2% |
| incremental_fresh_capped_added_by_15_17 | research_row_soft | 5 | 3 | 4 | +40.0% | 0.375 | 15.600 | $+15.98 | $-0.62 | -3.9% | -100.0% | +81.8% |
| no_pullback_yes_10_17 | research_row_soft | 18 | 5 | 13 | +38.9% | 0.430 | 13.167 | $+56.04 | $-0.35 | -0.6% | -56.6% | +43.1% |
| shadow_tail_false_10_17 | research_row_soft | 4 | 3 | 3 | +50.0% | 0.422 | 12.000 | $+5.50 | $+9.12 | +165.7% | -100.0% | +376.2% |

## All Settled Summary

| policy | sizing | rows | dates | cities | win_rate | avg_ask | avg_hour | cost_usd | pnl_usd | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_fresh_capped_10_14 | exec_gate_current | 51 | 28 | 23 | +41.2% | 0.371 | 12.980 | $+253.76 | $+30.82 | +12.1% | -27.5% | +52.1% |
| cheap_stale_only_10_14 | exec_gate_current | 1 | 1 | 1 | +100.0% | 0.210 | 10.000 | $+2.38 | $+8.94 | +376.2% | NA | NA |
| cheap_stale_only_10_17 | exec_gate_current | 1 | 1 | 1 | +100.0% | 0.210 | 10.000 | $+2.38 | $+8.94 | +376.2% | NA | NA |
| false_fade_only_10_14 | exec_gate_current | 10 | 9 | 9 | +50.0% | 0.354 | 12.100 | $+41.06 | $+23.87 | +58.1% | -28.6% | +137.3% |
| false_fade_only_10_17 | exec_gate_current | 12 | 11 | 10 | +41.7% | 0.321 | 12.583 | $+50.42 | $+14.51 | +28.8% | -51.1% | +104.4% |
| fresh_capped_extend_10_17 | exec_gate_current | 81 | 30 | 28 | +37.0% | 0.333 | 13.926 | $+382.62 | $+25.91 | +6.8% | -26.9% | +39.7% |
| incremental_fresh_capped_added_by_15_17 | exec_gate_current | 30 | 17 | 15 | +30.0% | 0.269 | 15.533 | $+128.86 | $-4.91 | -3.8% | -49.1% | +39.4% |
| no_pullback_yes_10_17 | exec_gate_current | 88 | 31 | 28 | +37.5% | 0.329 | 13.693 | $+407.68 | $+30.01 | +7.4% | -21.6% | +38.0% |
| shadow_tail_false_10_17 | exec_gate_current | 13 | 12 | 11 | +46.2% | 0.312 | 12.385 | $+52.80 | $+23.45 | +44.4% | -33.6% | +127.2% |
| baseline_fresh_capped_10_14 | research_row_soft | 107 | 38 | 32 | +42.1% | 0.447 | 12.542 | $+426.78 | $-5.17 | -1.2% | -27.6% | +28.0% |
| cheap_stale_only_10_14 | research_row_soft | 9 | 9 | 8 | +55.6% | 0.266 | 12.111 | $+13.71 | $+23.67 | +172.7% | +25.7% | +285.0% |
| cheap_stale_only_10_17 | research_row_soft | 11 | 11 | 9 | +45.5% | 0.267 | 12.727 | $+15.06 | $+22.32 | +148.2% | -1.0% | +256.5% |
| false_fade_only_10_14 | research_row_soft | 32 | 18 | 18 | +46.9% | 0.476 | 12.062 | $+86.50 | $+17.36 | +20.1% | -28.1% | +68.5% |
| false_fade_only_10_17 | research_row_soft | 34 | 19 | 19 | +44.1% | 0.457 | 12.235 | $+95.86 | $+8.00 | +8.3% | -38.6% | +57.7% |
| fresh_capped_extend_10_17 | research_row_soft | 163 | 41 | 35 | +39.9% | 0.411 | 13.595 | $+616.63 | $-10.97 | -1.8% | -24.4% | +22.3% |
| incremental_fresh_capped_added_by_15_17 | research_row_soft | 56 | 29 | 25 | +35.7% | 0.343 | 15.607 | $+189.85 | $-5.80 | -3.1% | -37.4% | +30.0% |
| no_pullback_yes_10_17 | research_row_soft | 193 | 42 | 36 | +41.5% | 0.415 | 13.259 | $+684.69 | $+7.91 | +1.2% | -16.8% | +19.9% |
| shadow_tail_false_10_17 | research_row_soft | 43 | 22 | 22 | +44.2% | 0.418 | 12.256 | $+108.96 | $+28.14 | +25.8% | -17.2% | +71.6% |

## Forward Daily

| policy | sizing | target_date | rows | win_rate | avg_ask | avg_hour | pnl_usd | roi | route_mix | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_fresh_capped_10_14 | exec_gate_current | 2026-06-21 | 2 | +50.0% | 0.370 | 12.500 | $+2.43 | +26.0% | capped_d2_no:1,fresh_runway_current_no:1 | Beijing |
| baseline_fresh_capped_10_14 | exec_gate_current | 2026-06-25 | 1 | +100.0% | 0.470 | 14.000 | $+5.22 | +112.8% | fresh_runway_current_no:1 |  |
| baseline_fresh_capped_10_14 | exec_gate_current | 2026-06-26 | 1 | +0.0% | 0.290 | 12.000 | $-4.49 | -100.0% | fresh_runway_current_no:1 | Chongqing |
| cheap_stale_only_10_14 | exec_gate_current | 2026-06-23 | 1 | +100.0% | 0.210 | 10.000 | $+8.94 | +376.2% | cheap_stale_tail_current_no:1 |  |
| cheap_stale_only_10_17 | exec_gate_current | 2026-06-23 | 1 | +100.0% | 0.210 | 10.000 | $+8.94 | +376.2% | cheap_stale_tail_current_no:1 |  |
| fresh_capped_extend_10_17 | exec_gate_current | 2026-06-21 | 4 | +50.0% | 0.295 | 13.750 | $+6.42 | +41.1% | capped_d2_no:1,fresh_runway_current_no:3 | Beijing,CapeTown |
| fresh_capped_extend_10_17 | exec_gate_current | 2026-06-25 | 1 | +100.0% | 0.470 | 14.000 | $+5.22 | +112.8% | fresh_runway_current_no:1 |  |
| fresh_capped_extend_10_17 | exec_gate_current | 2026-06-26 | 2 | +0.0% | 0.390 | 14.000 | $-9.75 | -100.0% | fresh_runway_current_no:2 | Chongqing,NYC |
| incremental_fresh_capped_added_by_15_17 | exec_gate_current | 2026-06-21 | 2 | +50.0% | 0.220 | 15.000 | $+3.99 | +63.7% | fresh_runway_current_no:2 | CapeTown |
| incremental_fresh_capped_added_by_15_17 | exec_gate_current | 2026-06-26 | 1 | +0.0% | 0.490 | 16.000 | $-5.25 | -100.0% | fresh_runway_current_no:1 | NYC |
| no_pullback_yes_10_17 | exec_gate_current | 2026-06-21 | 4 | +50.0% | 0.295 | 13.750 | $+6.42 | +41.1% | capped_d2_no:1,fresh_runway_current_no:3 | Beijing,CapeTown |
| no_pullback_yes_10_17 | exec_gate_current | 2026-06-23 | 1 | +100.0% | 0.210 | 10.000 | $+8.94 | +376.2% | cheap_stale_tail_current_no:1 |  |
| no_pullback_yes_10_17 | exec_gate_current | 2026-06-25 | 1 | +100.0% | 0.470 | 14.000 | $+5.22 | +112.8% | fresh_runway_current_no:1 |  |
| no_pullback_yes_10_17 | exec_gate_current | 2026-06-26 | 2 | +0.0% | 0.390 | 14.000 | $-9.75 | -100.0% | fresh_runway_current_no:2 | Chongqing,NYC |
| shadow_tail_false_10_17 | exec_gate_current | 2026-06-23 | 1 | +100.0% | 0.210 | 10.000 | $+8.94 | +376.2% | cheap_stale_tail_current_no:1 |  |
| baseline_fresh_capped_10_14 | research_row_soft | 2026-06-21 | 3 | +33.3% | 0.417 | 12.000 | $-0.53 | -4.3% | capped_d2_no:2,fresh_runway_current_no:1 | Beijing,Singapore |
| baseline_fresh_capped_10_14 | research_row_soft | 2026-06-23 | 2 | +50.0% | 0.575 | 11.500 | $-3.07 | -42.9% | capped_d2_no:1,fresh_runway_current_no:1 | Chongqing |
| baseline_fresh_capped_10_14 | research_row_soft | 2026-06-25 | 2 | +50.0% | 0.485 | 13.500 | $+1.69 | +20.7% | fresh_runway_current_no:2 | Karachi |
| baseline_fresh_capped_10_14 | research_row_soft | 2026-06-26 | 2 | +0.0% | 0.404 | 12.500 | $-6.94 | -100.0% | fresh_runway_current_no:2 | Chongqing,TelAviv |
| cheap_stale_only_10_14 | research_row_soft | 2026-06-22 | 1 | +0.0% | 0.220 | 14.000 | $-0.47 | -100.0% | cheap_stale_tail_current_no:1 | Shanghai |
| cheap_stale_only_10_14 | research_row_soft | 2026-06-23 | 1 | +100.0% | 0.210 | 10.000 | $+8.94 | +376.2% | cheap_stale_tail_current_no:1 |  |
| cheap_stale_only_10_17 | research_row_soft | 2026-06-22 | 1 | +0.0% | 0.220 | 14.000 | $-0.47 | -100.0% | cheap_stale_tail_current_no:1 | Shanghai |
| cheap_stale_only_10_17 | research_row_soft | 2026-06-23 | 1 | +100.0% | 0.210 | 10.000 | $+8.94 | +376.2% | cheap_stale_tail_current_no:1 |  |
| false_fade_only_10_14 | research_row_soft | 2026-06-22 | 1 | +0.0% | 0.638 | 13.000 | $-0.60 | -100.0% | false_fade_reheat_current_no:1 | NYC |
| false_fade_only_10_14 | research_row_soft | 2026-06-26 | 1 | +100.0% | 0.620 | 11.000 | $+1.26 | +61.3% | false_fade_reheat_current_no:1 |  |
| false_fade_only_10_17 | research_row_soft | 2026-06-22 | 1 | +0.0% | 0.638 | 13.000 | $-0.60 | -100.0% | false_fade_reheat_current_no:1 | NYC |
| false_fade_only_10_17 | research_row_soft | 2026-06-26 | 1 | +100.0% | 0.620 | 11.000 | $+1.26 | +61.3% | false_fade_reheat_current_no:1 |  |
| fresh_capped_extend_10_17 | research_row_soft | 2026-06-21 | 5 | +40.0% | 0.338 | 13.200 | $+3.46 | +18.7% | capped_d2_no:2,fresh_runway_current_no:3 | Beijing,CapeTown,Singapore |
| fresh_capped_extend_10_17 | research_row_soft | 2026-06-23 | 2 | +50.0% | 0.575 | 11.500 | $-3.07 | -42.9% | capped_d2_no:1,fresh_runway_current_no:1 | Chongqing |
| fresh_capped_extend_10_17 | research_row_soft | 2026-06-25 | 3 | +66.7% | 0.507 | 14.333 | $+3.99 | +36.4% | fresh_runway_current_no:3 | Karachi |
| fresh_capped_extend_10_17 | research_row_soft | 2026-06-26 | 4 | +0.0% | 0.423 | 14.250 | $-13.84 | -100.0% | fresh_runway_current_no:4 | Chongqing,Jeddah,NYC,TelAviv |
| incremental_fresh_capped_added_by_15_17 | research_row_soft | 2026-06-21 | 2 | +50.0% | 0.220 | 15.000 | $+3.99 | +63.7% | fresh_runway_current_no:2 | CapeTown |
| incremental_fresh_capped_added_by_15_17 | research_row_soft | 2026-06-25 | 1 | +100.0% | 0.550 | 16.000 | $+2.30 | +81.8% | fresh_runway_current_no:1 |  |
| incremental_fresh_capped_added_by_15_17 | research_row_soft | 2026-06-26 | 2 | +0.0% | 0.442 | 16.000 | $-6.91 | -100.0% | fresh_runway_current_no:2 | Jeddah,NYC |
| no_pullback_yes_10_17 | research_row_soft | 2026-06-21 | 5 | +40.0% | 0.338 | 13.200 | $+3.46 | +18.7% | capped_d2_no:2,fresh_runway_current_no:3 | Beijing,CapeTown,Singapore |
| no_pullback_yes_10_17 | research_row_soft | 2026-06-22 | 2 | +0.0% | 0.429 | 13.500 | $-1.08 | -100.0% | cheap_stale_tail_current_no:1,false_fade_reheat_current_no:1 | NYC,Shanghai |
| no_pullback_yes_10_17 | research_row_soft | 2026-06-23 | 3 | +66.7% | 0.453 | 11.000 | $+5.86 | +61.5% | capped_d2_no:1,cheap_stale_tail_current_no:1,fresh_runway_current_no:1 | Chongqing |
| no_pullback_yes_10_17 | research_row_soft | 2026-06-25 | 3 | +66.7% | 0.507 | 14.333 | $+3.99 | +36.4% | fresh_runway_current_no:3 | Karachi |
| no_pullback_yes_10_17 | research_row_soft | 2026-06-26 | 5 | +20.0% | 0.462 | 13.600 | $-12.59 | -79.2% | false_fade_reheat_current_no:1,fresh_runway_current_no:4 | Chongqing,Jeddah,NYC,TelAviv |
| shadow_tail_false_10_17 | research_row_soft | 2026-06-22 | 2 | +0.0% | 0.429 | 13.500 | $-1.08 | -100.0% | cheap_stale_tail_current_no:1,false_fade_reheat_current_no:1 | NYC,Shanghai |
| shadow_tail_false_10_17 | research_row_soft | 2026-06-23 | 1 | +100.0% | 0.210 | 10.000 | $+8.94 | +376.2% | cheap_stale_tail_current_no:1 |  |
| shadow_tail_false_10_17 | research_row_soft | 2026-06-26 | 1 | +100.0% | 0.620 | 11.000 | $+1.26 | +61.3% | false_fade_reheat_current_no:1 |  |

## Data Notes

- Generated at `2026-07-06T04:52:55+00:00`.
- Historical atlas rows through `2026-07-04`; settled payoff window used here ends at `2026-06-26`.
- `research_row_soft` is opportunity sizing and can be below current minimum executable size.
- `exec_gate_current` applies the current tiny-live order-size and price-quality gates, but still uses historical top-of-book snapshots, not queue simulation.
