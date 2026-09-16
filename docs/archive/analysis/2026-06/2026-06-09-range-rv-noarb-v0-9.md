# Range RV Bracket No-Arb Scanner v0.9

> generated_at_utc: `2026-06-09T02:11:50.926222+00:00`
> target_metric: `bracket_noarb_range_rv_alpha`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: local research only; no N100/live config changed.

## Data Snapshot

- Data source: `runtime/weather.db.fact_signal_candidates`; `fact_trades` only for mandatory self-check.
- DB last_modified: `2026-06-08T17:23:59.056493+00:00`
- fact built at: `2026-06-08T17:23:55.700271+00:00`
- Decision sets: `647`; generated no-arb rows `658`.
- Algorithms tested: `4`.
- Evaluation requires exactly one settled winner in the observed bracket set.
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

- `noarb_all_yes_underround_e005`: buy YES on every observed bracket when sum(YES) < 1 by at least 0.005.
- `noarb_all_no_overround_e005`: buy NO on every observed bracket when sum(YES) > 1 by at least 0.005.
- `noarb_center_yes_tail_no_e010`: center band underpriced while tails are overpriced.
- `noarb_tail_yes_center_no_e010`: tails underpriced while center band is overpriced.

Forward gate requires holdout active_dates >= 5, rows >= 10, and top5-removed ROI > 0.

## Decision Proxy

| algorithm | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates | gate reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `noarb_all_no_overround_e005` | 196 | 32 | 16 | 69 | 8 | 3 | +1.8% | [+0.7%, +3.7%] | +4.3% | [+3.2%, +5.9%] | +0.9% | [+0.7%, +1.4%] | +4.7% | [+4.0%, +6.0%] | NA | `PASS/PASS/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `noarb_all_yes_underround_e005` | 196 | 159 | 21 | 69 | 60 | 9 | +12.3% | [+9.9%, +14.6%] | +3.9% | [+2.4%, +6.4%] | +15.8% | [+8.1%, +18.0%] | +2.8% | [+0.6%, +3.4%] | +7.4% | `PASS/PASS/PASS -> confirmed` | `` |
| `noarb_center_yes_tail_no_e010` | 50 | 20 | 9 | 14 | 3 | 2 | -5.0% | [-20.6%, +16.2%] | -4.2% | [-18.6%, +12.4%] | +0.3% | [-15.1%, +32.4%] | -1.4% | [-18.1%, +31.7%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `noarb_tail_yes_center_no_e010` | 50 | 21 | 9 | 14 | 7 | 2 | -1.4% | [-12.3%, +13.7%] | -2.2% | [-12.6%, +10.5%] | -5.2% | [-14.7%, +1.5%] | -3.5% | [-11.5%, +2.2%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |

## Orderbook Executable Subset

Orderbook prices use latest `snapshot_ts_utc <= decision_snapshot_ts_utc`; rows require all legs matched.

- Fully matched strategy rows: `338` / `658`.

| algorithm | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates | gate reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `noarb_all_no_overround_e005` | 68 | 10 | 6 | 67 | 8 | 3 | -1.9% | [-3.9%, -1.2%] | +2.8% | [+1.8%, +3.5%] | -1.2% | [-2.3%, -0.8%] | +4.7% | [+4.1%, +5.1%] | NA | `FAIL/PASS/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `noarb_all_yes_underround_e005` | 68 | 53 | 9 | 69 | 60 | 9 | +5.4% | [+2.4%, +7.4%] | +5.3% | [+2.1%, +13.0%] | +8.3% | [+5.8%, +10.3%] | +2.2% | [+0.0%, +2.7%] | +3.4% | `PASS/PASS/FAIL -> inconclusive` | `` |
| `noarb_tail_yes_center_no_e010` | 20 | 6 | 2 | 14 | 7 | 2 | -6.2% | [-18.8%, +6.1%] | -4.0% | [-22.8%, +14.3%] | -7.1% | [-16.2%, -0.6%] | -3.5% | [-11.6%, +2.2%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `noarb_center_yes_tail_no_e010` | 20 | 13 | 4 | 12 | 3 | 2 | -7.1% | [-24.4%, +11.9%] | -3.7% | [-15.4%, +5.6%] | -2.5% | [-17.6%, +28.8%] | -4.0% | [-18.7%, +27.0%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |

## Notes

- Full-set all-YES/all-NO baskets are model-free no-arb tests on the observed settled bracket set.
- Center/tail rows are model-confirmed relative-value range expressions, not pure no-arb.
- Baseline is each algorithm's own top unfiltered no-arb candidate per city-day decision snapshot.
