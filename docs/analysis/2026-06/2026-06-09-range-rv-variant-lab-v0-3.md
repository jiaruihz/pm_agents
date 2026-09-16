# Range RV Variant Lab v0.3

> generated_at_utc: `2026-06-09T01:37:39.147289+00:00`
> target_metric: `range_rv_variant_alpha`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: local research only; no N100/live config changed.

## Data Snapshot

- Data source: `runtime/weather.db.fact_signal_candidates`; `fact_trades` only for mandatory self-check.
- DB last_modified: `2026-06-08T17:23:59.056493+00:00`
- fact built at: `2026-06-08T17:23:55.700271+00:00`
- Decision sets: `647`; generated strategy rows `1382`.
- Algorithms tested: `9`; each emits at most one candidate per city-day decision snapshot.
- No live_real PnL is published; this is opportunity-grain counterfactual research.

## Verdict

| gate | status |
|---|---|
| `significance` | `FAIL` |
| `baseline` | `FAIL` |
| `forward` | `FAIL` |
| `verdict` | `inconclusive` |

Final verdict: `inconclusive`. No live action.

## Algorithm Definitions

- `adj3_raw_mass_long_e015`: buy YES on best adjacent 3-bracket raw model-minus-market mass if score >= 0.15.
- `adj3_norm_mass_long_e010`: same, but score uses model/market probabilities normalized within the observed city-day bracket set.
- `adj2_norm_mass_long_e008`: normalized adjacent 2-bracket range YES.
- `pair_spread_adjacent_e010`: buy YES on the higher-edge adjacent bracket and BUY_NO on the lower-edge adjacent bracket.
- `center_over_shoulders_e010`: buy YES center bracket and BUY_NO adjacent shoulders.
- `shoulders_over_center_e010`: buy YES shoulders and BUY_NO center bracket.
- `range_vs_neighbors_e010`: buy YES adjacent 3 range and BUY_NO immediate outside neighbors.
- `tail_fade_overpriced_e015`: buy NO on the most overpriced below/above tail.
- `mode_cluster_cheap_060_075`: buy YES on the 3-bracket model-mode cluster only if model mass >= 0.60 and cost <= 0.75.

## Decision Proxy

| algorithm | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `adj2_norm_mass_long_e008` | 175 | 81 | 17 | 101 | 14 | 7 | +33.0% | [+7.1%, +55.1%] | +11.0% | [-9.5%, +27.9%] | -22.8% | [-52.8%, -2.7%] | -18.1% | [-40.7%, -0.3%] | -100.0% | `PASS/FAIL/FAIL -> inconclusive` |
| `adj3_norm_mass_long_e010` | 87 | 40 | 9 | 15 | 0 | 0 | +34.0% | [+14.5%, +51.5%] | +16.2% | [+1.5%, +28.3%] | NA | NA | NA | NA | NA | `PASS/PASS/FAIL -> inconclusive` |
| `adj3_raw_mass_long_e015` | 87 | 33 | 8 | 15 | 0 | 0 | +42.7% | [+13.4%, +64.6%] | +24.9% | [+1.3%, +42.3%] | NA | NA | NA | NA | NA | `PASS/PASS/FAIL -> inconclusive` |
| `center_over_shoulders_e010` | 87 | 42 | 12 | 15 | 1 | 1 | +9.6% | [-3.5%, +23.0%] | +10.5% | [+2.7%, +19.3%] | -48.3% | [-48.3%, -48.3%] | -35.9% | [-50.0%, -12.6%] | NA | `FAIL/PASS/FAIL -> inconclusive` |
| `mode_cluster_cheap_060_075` | 87 | 29 | 8 | 15 | 0 | 0 | +14.1% | [-15.1%, +36.3%] | +4.1% | [-19.7%, +23.1%] | NA | NA | NA | NA | NA | `FAIL/FAIL/FAIL -> inconclusive` |
| `pair_spread_adjacent_e010` | 175 | 145 | 17 | 101 | 71 | 8 | -1.3% | [-18.0%, +15.1%] | -2.7% | [-8.5%, +3.9%] | -14.2% | [-38.3%, +14.4%] | -8.9% | [-19.4%, +1.7%] | -43.0% | `FAIL/FAIL/FAIL -> inconclusive` |
| `range_vs_neighbors_e010` | 44 | 40 | 9 | 0 | 0 | 0 | +25.9% | [+13.0%, +42.3%] | +0.8% | [-0.6%, +3.6%] | NA | NA | NA | NA | NA | `PASS/FAIL/FAIL -> inconclusive` |
| `shoulders_over_center_e010` | 87 | 57 | 13 | 15 | 8 | 4 | +14.3% | [-13.7%, +44.4%] | -0.3% | [-15.9%, +14.6%] | +16.4% | [-50.1%, +82.3%] | -5.0% | [-77.6%, +50.1%] | NA | `FAIL/FAIL/FAIL -> inconclusive` |
| `tail_fade_overpriced_e015` | 175 | 130 | 17 | 101 | 66 | 8 | +0.6% | [-6.0%, +6.4%] | +1.8% | [-1.4%, +6.5%] | -2.7% | [-22.6%, +15.6%] | +1.9% | [-4.9%, +9.1%] | -29.1% | `FAIL/FAIL/FAIL -> inconclusive` |

## Orderbook Executable Subset

Orderbook prices use latest `snapshot_ts_utc <= decision_snapshot_ts_utc`; rows require all legs matched.

- Fully matched strategy rows: `1100` / `1382`.

| algorithm | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `adj2_norm_mass_long_e008` | 125 | 58 | 11 | 101 | 14 | 7 | +25.3% | [-1.0%, +48.3%] | +7.2% | [-11.9%, +23.9%] | -24.2% | [-50.8%, -0.2%] | -14.7% | [-36.0%, +4.4%] | -42.4% | `FAIL/FAIL/FAIL -> inconclusive` |
| `adj3_norm_mass_long_e010` | 64 | 25 | 5 | 15 | 0 | 0 | +23.8% | [+0.9%, +52.7%] | +12.9% | [-5.5%, +32.1%] | NA | NA | NA | NA | NA | `PASS/FAIL/FAIL -> inconclusive` |
| `adj3_raw_mass_long_e015` | 64 | 22 | 5 | 15 | 0 | 0 | +29.3% | [-7.7%, +57.0%] | +18.5% | [-12.8%, +40.6%] | NA | NA | NA | NA | NA | `FAIL/FAIL/FAIL -> inconclusive` |
| `center_over_shoulders_e010` | 64 | 27 | 9 | 15 | 1 | 1 | +13.6% | [-3.3%, +31.1%] | +13.8% | [+3.8%, +21.2%] | -50.2% | [-50.2%, -50.2%] | -36.1% | [-49.5%, -11.0%] | NA | `FAIL/PASS/FAIL -> inconclusive` |
| `mode_cluster_cheap_060_075` | 64 | 20 | 5 | 15 | 0 | 0 | +3.3% | [-31.6%, +30.4%] | -1.1% | [-32.4%, +21.8%] | NA | NA | NA | NA | NA | `FAIL/FAIL/FAIL -> inconclusive` |
| `pair_spread_adjacent_e010` | 125 | 105 | 11 | 101 | 71 | 8 | -1.4% | [-21.4%, +17.0%] | -3.8% | [-10.1%, +4.0%] | -17.9% | [-41.4%, +9.0%] | -8.7% | [-17.7%, +1.2%] | -45.4% | `FAIL/FAIL/FAIL -> inconclusive` |
| `range_vs_neighbors_e010` | 28 | 24 | 5 | 0 | 0 | 0 | +18.0% | [+5.8%, +42.7%] | +0.8% | [-0.9%, +6.8%] | NA | NA | NA | NA | NA | `PASS/FAIL/FAIL -> inconclusive` |
| `shoulders_over_center_e010` | 64 | 43 | 8 | 15 | 8 | 4 | +13.8% | [-23.4%, +54.4%] | +2.6% | [-16.1%, +22.8%] | +12.2% | [-39.4%, +83.5%] | -5.3% | [-56.9%, +57.9%] | NA | `FAIL/FAIL/FAIL -> inconclusive` |
| `tail_fade_overpriced_e015` | 124 | 95 | 11 | 101 | 66 | 8 | -5.1% | [-12.4%, +0.4%] | -1.3% | [-4.2%, +1.6%] | -5.3% | [-23.1%, +13.6%] | +1.7% | [-4.8%, +8.7%] | -30.5% | `FAIL/FAIL/FAIL -> inconclusive` |

## Notes

- Baseline is each algorithm's own unfiltered top candidate per city-day decision snapshot.
- Passing point estimates are not enough; ROI CI, excess CI, and forward gates must all pass.
- This lab is broader than v0.2 but still pre-registered; it does not choose cities, dates, or models after seeing results.
