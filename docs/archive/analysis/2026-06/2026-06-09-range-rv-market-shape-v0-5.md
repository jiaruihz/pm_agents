# Range RV Market Shape Scanner v0.5

> generated_at_utc: `2026-06-09T01:54:19.124249+00:00`
> target_metric: `market_shape_range_rv_alpha`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: local research only; no N100/live config changed.

## Data Snapshot

- Data source: `runtime/weather.db.fact_signal_candidates`; `fact_trades` only for mandatory self-check.
- DB last_modified: `2026-06-08T17:23:59.056493+00:00`
- fact built at: `2026-06-08T17:23:55.700271+00:00`
- Decision sets: `647`; generated strategy rows `792`.
- Algorithms tested: `7`.
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

- `shape_trough_center_yes_pair_e012`: center bracket is cheap vs shoulders; buy YES center and BUY_NO shoulders.
- `shape_peak_center_no_pair_e012`: center bracket is rich vs shoulders; buy NO center and BUY_YES shoulders.
- `shape_adjacent_inversion_pair_e010`: adjacent market slope disagrees with model slope; buy YES cheap side and BUY_NO rich side.
- `shape_below_tail_inversion_pair_e010` / `shape_above_tail_inversion_pair_e010`: tail richer than inner neighbor while model prefers inner; buy NO tail and YES inner.
- `shape_single_trough_yes_e010` / `shape_single_peak_no_e010`: single-leg version of local trough/peak when expression as one leg is cleaner.

Forward gate requires holdout active_dates >= 5, rows >= 10, and top5-removed ROI > 0.

## Decision Proxy

| algorithm | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates | gate reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `shape_single_peak_no_e010` | 91 | 61 | 13 | 15 | 8 | 4 | +4.7% | [-13.6%, +23.9%] | -0.7% | [-14.4%, +11.2%] | +8.7% | [-64.8%, +67.8%] | -5.0% | [-72.8%, +43.3%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `shape_adjacent_inversion_pair_e010` | 172 | 162 | 17 | 101 | 91 | 8 | -1.5% | [-17.0%, +12.9%] | -1.5% | [-3.9%, +1.8%] | -18.5% | [-40.7%, +7.0%] | -6.2% | [-14.5%, +0.4%] | -50.6% | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_top5_removed_roi<=0_or_not_available` |
| `shape_peak_center_no_pair_e012` | 86 | 55 | 12 | 15 | 7 | 4 | +15.0% | [-13.6%, +44.2%] | -0.1% | [-18.0%, +13.1%] | +6.0% | [-49.9%, +77.0%] | -15.4% | [-72.0%, +46.2%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `shape_above_tail_inversion_pair_e010` | 50 | 24 | 10 | 11 | 6 | 6 | +11.7% | [-11.7%, +38.2%] | +9.1% | [-6.6%, +32.6%] | -54.4% | [-100.0%, +2.4%] | -18.2% | [-41.7%, +13.8%] | -100.0% | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `shape_trough_center_yes_pair_e012` | 86 | 40 | 12 | 15 | 1 | 1 | +13.2% | [-4.9%, +28.7%] | +12.3% | [+1.0%, +23.5%] | -48.3% | [-48.3%, -48.3%] | -35.9% | [-48.7%, -15.2%] | NA | `FAIL/PASS/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `shape_below_tail_inversion_pair_e010` | 40 | 20 | 12 | 4 | 2 | 2 | +15.0% | [-20.2%, +42.7%] | +10.0% | [-21.7%, +31.9%] | -53.6% | [-100.0%, -4.8%] | -63.7% | [-130.0%, +0.0%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `shape_single_trough_yes_e010` | 91 | 44 | 13 | 15 | 1 | 1 | +51.3% | [-14.6%, +106.5%] | +39.8% | [-4.4%, +72.4%] | -100.0% | [-100.0%, -100.0%] | -80.6% | [-112.3%, -22.3%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |

## Orderbook Executable Subset

Orderbook prices use latest `snapshot_ts_utc <= decision_snapshot_ts_utc`; rows require all legs matched.

- Fully matched strategy rows: `617` / `792`.

| algorithm | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates | gate reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `shape_single_peak_no_e010` | 65 | 44 | 8 | 15 | 8 | 4 | +2.7% | [-26.0%, +31.7%] | +2.7% | [-14.3%, +19.5%] | +5.3% | [-42.5%, +68.9%] | -5.1% | [-55.3%, +43.6%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `shape_adjacent_inversion_pair_e010` | 122 | 117 | 11 | 101 | 91 | 8 | -2.4% | [-21.8%, +15.2%] | -2.5% | [-5.5%, +0.8%] | -21.6% | [-41.8%, +3.0%] | -6.1% | [-13.2%, +0.4%] | -52.3% | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_top5_removed_roi<=0_or_not_available` |
| `shape_peak_center_no_pair_e012` | 63 | 42 | 7 | 15 | 7 | 4 | +16.6% | [-23.9%, +54.7%] | +6.6% | [-13.4%, +25.9%] | +1.3% | [-50.2%, +78.6%] | -16.1% | [-68.2%, +50.4%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `shape_above_tail_inversion_pair_e010` | 32 | 14 | 6 | 11 | 6 | 6 | +27.5% | [+0.8%, +83.0%] | +16.6% | [-5.3%, +78.4%] | -55.6% | [-100.0%, +5.6%] | -18.1% | [-41.2%, +14.8%] | -100.0% | `PASS/FAIL/FAIL -> inconclusive` | `holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `shape_trough_center_yes_pair_e012` | 63 | 25 | 9 | 15 | 1 | 1 | +20.1% | [-1.5%, +36.4%] | +19.1% | [+4.9%, +29.1%] | -50.2% | [-50.2%, -50.2%] | -36.1% | [-50.6%, -14.7%] | NA | `FAIL/PASS/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `shape_below_tail_inversion_pair_e010` | 31 | 15 | 9 | 4 | 2 | 2 | +16.2% | [-24.3%, +43.1%] | +13.9% | [-22.8%, +37.4%] | -52.8% | [-100.0%, -7.4%] | -62.5% | [-129.3%, +0.0%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `shape_single_trough_yes_e010` | 65 | 28 | 9 | 15 | 1 | 1 | +61.6% | [-21.4%, +124.2%] | +53.3% | [-0.7%, +89.0%] | -100.0% | [-100.0%, -100.0%] | -79.2% | [-112.4%, -28.3%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |

## Notes

- Baseline is each algorithm's own top unfiltered shape candidate per city-day decision snapshot.
- The selected rule is fixed by anomaly thresholds; no city/date/model post-selection is used.
- This is intentionally different from v0.3/v0.4: market shape anomaly first, model confirmation second.
