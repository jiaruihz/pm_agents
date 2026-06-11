# Range RV Temporal Reversion Scanner v0.6

> generated_at_utc: `2026-06-09T01:57:41.039780+00:00`
> target_metric: `temporal_range_rv_reversion_alpha`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: local research only; no N100/live config changed.

## Data Snapshot

- Data source: `runtime/weather.db.fact_signal_candidates`; `fact_trades` only for mandatory self-check.
- DB last_modified: `2026-06-08T17:23:59.056493+00:00`
- fact built at: `2026-06-08T17:23:55.700271+00:00`
- Decision sets: `647`; generated temporal rows `20`.
- Algorithms tested: `6`.
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

- `temporal_single_reversion_e012`: largest bracket residual; BUY_NO if market over-moved up, BUY_YES if it over-moved down.
- `temporal_adjacent_spread_reversion_e015`: adjacent residual spread; BUY_YES cheap residual side and BUY_NO rich residual side.
- `temporal_adj2_range_reversion_e120` / `temporal_adj3_range_reversion_e150`: residual over/under-move across contiguous ranges.
- `temporal_below_tail_reversion_e010` / `temporal_above_tail_reversion_e010`: tail residual vs inner neighbor residual.

Forward gate requires holdout active_dates >= 5, rows >= 10, and top5-removed ROI > 0.

## Decision Proxy

| algorithm | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates | gate reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `temporal_adj3_range_reversion_e150` | 2 | 0 | 0 | 2 | 1 | 1 | NA | NA | NA | NA | +132.6% | [+132.6%, +132.6%] | +24.1% | [+0.0%, +24.1%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `temporal_above_tail_reversion_e010` | 0 | 0 | 0 | 2 | 2 | 2 | NA | NA | NA | NA | +60.9% | [-4.3%, +143.9%] | +0.0% | [+0.0%, +0.0%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `temporal_adj2_range_reversion_e120` | 2 | 2 | 2 | 2 | 2 | 2 | -38.5% | [-100.0%, -18.0%] | +0.0% | [+0.0%, +0.0%] | +51.1% | [+23.8%, +170.3%] | +0.0% | [+0.0%, +0.0%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `temporal_adjacent_spread_reversion_e015` | 2 | 2 | 2 | 2 | 2 | 2 | +14.9% | [-1.5%, +37.9%] | +0.0% | [+0.0%, +0.0%] | +52.7% | [-12.7%, +143.9%] | +0.0% | [+0.0%, +0.0%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `temporal_single_reversion_e012` | 2 | 2 | 2 | 2 | 2 | 2 | +7.5% | [-100.0%, +31.6%] | +0.0% | [+0.0%, +0.0%] | +177.8% | [-100.0%, +589.7%] | +0.0% | [+0.0%, +0.0%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `temporal_below_tail_reversion_e010` | 2 | 2 | 2 | 0 | 0 | 0 | +14.9% | [-1.5%, +37.9%] | +0.0% | [+0.0%, +0.0%] | NA | NA | NA | NA | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |

## Orderbook Executable Subset

Orderbook prices use latest `snapshot_ts_utc <= decision_snapshot_ts_utc`; rows require all legs matched.

- Fully matched strategy rows: `15` / `20`.

| algorithm | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates | gate reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `temporal_adj3_range_reversion_e150` | 1 | 0 | 0 | 2 | 1 | 1 | NA | NA | NA | NA | +117.4% | [+117.4%, +117.4%] | +21.7% | [+0.0%, +21.7%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `temporal_above_tail_reversion_e010` | 0 | 0 | 0 | 2 | 2 | 2 | NA | NA | NA | NA | +42.2% | [-5.6%, +90.5%] | +0.0% | [+0.0%, +0.0%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `temporal_adj2_range_reversion_e120` | 1 | 1 | 1 | 2 | 2 | 2 | -100.0% | [-100.0%, -100.0%] | +0.0% | [+0.0%, +0.0%] | +40.3% | [+14.4%, +156.4%] | +0.0% | [+0.0%, +0.0%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `temporal_adjacent_spread_reversion_e015` | 1 | 1 | 1 | 2 | 2 | 2 | -1.0% | [-1.0%, -1.0%] | +0.0% | [+0.0%, +0.0%] | +34.5% | [-15.3%, +90.5%] | +0.0% | [+0.0%, +0.0%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `temporal_single_reversion_e012` | 1 | 1 | 1 | 2 | 2 | 2 | +29.9% | [+29.9%, +29.9%] | +0.0% | [+0.0%, +0.0%] | +108.3% | [-100.0%, +300.0%] | +0.0% | [+0.0%, +0.0%] | NA | `PASS/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `temporal_below_tail_reversion_e010` | 1 | 1 | 1 | 0 | 0 | 0 | -1.0% | [-1.0%, -1.0%] | +0.0% | [+0.0%, +0.0%] | NA | NA | NA | NA | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |

## Notes

- This experiment uses only the previous snapshot of the same city-day plus the current decision snapshot.
- Baseline is each algorithm's own top unfiltered temporal candidate per city-day decision snapshot.
- The result is evaluated with the same train/holdout split, cluster bootstrap, and executable orderbook subset as the prior Range RV scripts.
