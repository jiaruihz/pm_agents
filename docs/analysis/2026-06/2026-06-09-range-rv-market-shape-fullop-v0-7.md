# Range RV Market Shape Full-Opportunity Scanner v0.7

> generated_at_utc: `2026-06-09T02:01:23.390879+00:00`
> target_metric: `market_shape_fullop_range_rv_alpha`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: local research only; no N100/live config changed.

## Data Snapshot

- Data source: `runtime/weather.db.fact_signal_candidates`; `fact_trades` only for mandatory self-check.
- DB last_modified: `2026-06-08T17:23:59.056493+00:00`
- fact built at: `2026-06-08T17:23:55.700271+00:00`
- Decision sets: `647`; generated strategy rows `2129`.
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
| `shape_single_trough_yes_e010` | 225 | 135 | 17 | 73 | 30 | 3 | +32.2% | [+3.4%, +59.7%] | +11.7% | [-6.2%, +28.6%] | +92.0% | [-100.0%, +121.9%] | +73.0% | [-80.8%, +101.8%] | NA | `PASS/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_top5_removed_roi<=0_or_not_available` |
| `shape_trough_center_yes_pair_e012` | 225 | 128 | 17 | 73 | 27 | 3 | +1.3% | [-6.2%, +8.6%] | -2.0% | [-6.6%, +1.2%] | +18.7% | [-48.3%, +23.9%] | +16.1% | [-39.2%, +22.0%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_top5_removed_roi<=0_or_not_available` |
| `shape_below_tail_inversion_pair_e010` | 116 | 63 | 19 | 34 | 14 | 4 | +10.0% | [-4.8%, +26.0%] | +2.9% | [-7.1%, +13.1%] | +27.3% | [-63.2%, +50.0%] | +12.4% | [-70.7%, +29.9%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_top5_removed_roi<=0_or_not_available` |
| `shape_peak_center_no_pair_e012` | 225 | 137 | 21 | 73 | 47 | 6 | -2.2% | [-17.1%, +10.7%] | -2.7% | [-11.1%, +5.7%] | +14.7% | [-16.6%, +36.3%] | +3.1% | [-30.3%, +9.3%] | -12.3% | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_top5_removed_roi<=0_or_not_available` |
| `shape_single_peak_no_e010` | 225 | 152 | 21 | 73 | 49 | 6 | +0.5% | [-11.1%, +11.1%] | -1.0% | [-7.6%, +6.7%] | +4.1% | [-18.5%, +25.3%] | +1.3% | [-14.1%, +8.9%] | -15.3% | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_top5_removed_roi<=0_or_not_available` |
| `shape_adjacent_inversion_pair_e010` | 465 | 430 | 21 | 174 | 161 | 10 | -1.0% | [-10.8%, +7.0%] | +0.5% | [-1.6%, +2.5%] | -6.6% | [-30.8%, +13.0%] | -4.3% | [-10.3%, +0.2%] | -38.4% | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_top5_removed_roi<=0_or_not_available` |
| `shape_above_tail_inversion_pair_e010` | 109 | 53 | 17 | 39 | 15 | 8 | +20.6% | [+2.9%, +36.0%] | +10.4% | [-1.8%, +24.8%] | -8.5% | [-76.7%, +20.5%] | -4.6% | [-36.9%, +18.0%] | -100.0% | `PASS/FAIL/FAIL -> inconclusive` | `holdout_top5_removed_roi<=0_or_not_available` |

## Orderbook Executable Subset

Orderbook prices use latest `snapshot_ts_utc <= decision_snapshot_ts_utc`; rows require all legs matched.

- Fully matched strategy rows: `1160` / `2129`.

| algorithm | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates | gate reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `shape_single_trough_yes_e010` | 79 | 44 | 7 | 73 | 30 | 3 | +16.2% | [-13.8%, +62.9%] | +0.1% | [-27.7%, +19.5%] | +75.3% | [-100.0%, +100.6%] | +61.9% | [-85.6%, +89.1%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_top5_removed_roi<=0_or_not_available` |
| `shape_trough_center_yes_pair_e012` | 79 | 40 | 7 | 73 | 27 | 3 | -2.9% | [-14.8%, +17.5%] | -4.4% | [-13.1%, +2.8%] | +16.0% | [-50.2%, +21.3%] | +15.7% | [-37.6%, +21.3%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_top5_removed_roi<=0_or_not_available` |
| `shape_below_tail_inversion_pair_e010` | 44 | 21 | 8 | 33 | 14 | 4 | +5.5% | [-26.0%, +37.7%] | -1.2% | [-23.3%, +13.2%] | +24.9% | [-68.4%, +46.9%] | +12.5% | [-74.0%, +28.0%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_top5_removed_roi<=0_or_not_available` |
| `shape_peak_center_no_pair_e012` | 77 | 49 | 9 | 73 | 47 | 6 | -6.2% | [-44.3%, +21.2%] | -6.5% | [-19.6%, +6.8%] | +9.5% | [-22.2%, +33.3%] | +3.0% | [-28.2%, +9.3%] | -16.7% | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_top5_removed_roi<=0_or_not_available` |
| `shape_single_peak_no_e010` | 79 | 55 | 9 | 73 | 49 | 6 | -11.8% | [-35.7%, +6.1%] | -8.6% | [-16.7%, +1.9%] | +1.7% | [-21.4%, +28.3%] | +1.5% | [-18.7%, +13.3%] | -17.8% | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_top5_removed_roi<=0_or_not_available` |
| `shape_adjacent_inversion_pair_e010` | 232 | 214 | 9 | 174 | 161 | 10 | -6.5% | [-20.0%, +6.7%] | +0.5% | [-2.6%, +3.8%] | -10.3% | [-33.1%, +9.8%] | -4.3% | [-10.1%, -0.2%] | -33.1% | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_top5_removed_roi<=0_or_not_available` |
| `shape_above_tail_inversion_pair_e010` | 32 | 13 | 6 | 39 | 15 | 8 | +32.4% | [-4.5%, +76.7%] | +26.1% | [-10.0%, +66.7%] | -11.3% | [-79.8%, +17.1%] | -5.4% | [-36.4%, +14.2%] | -100.0% | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_top5_removed_roi<=0_or_not_available` |

## Notes

- Baseline is each algorithm's own top unfiltered shape candidate per city-day decision snapshot.
- The selected rule is fixed by anomaly thresholds; no city/date/model post-selection is used.
- This is intentionally different from v0.3/v0.4: market shape anomaly first, model confirmation second.
