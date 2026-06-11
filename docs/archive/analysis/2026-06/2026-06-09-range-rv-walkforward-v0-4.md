# Range RV Walk-Forward Selector v0.4

> generated_at_utc: `2026-06-09T01:46:57.766893+00:00`
> target_metric: `range_rv_walkforward_selector_alpha`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: local research only; no N100/live config changed.

## Data Snapshot

- Data source: `runtime/weather.db.fact_signal_candidates`; `fact_trades` only for mandatory self-check.
- DB last_modified: `2026-06-08T17:23:59.056493+00:00`
- fact built at: `2026-06-08T17:23:55.700271+00:00`
- Warmup dates: `10`; walk-forward dates after warmup: `15`.
- Variant rows: `1382`; orderbook matched rows `1100` / `1382`.

## Selector Rules

- `balanced`: before each date, pick the algorithm with prior active_dates >= 5, rows >= 10, prior ROI > 0, and prior excess ROI > 0; rank by prior excess ROI.
- `strict_tail`: same as balanced, but prior top5-removed ROI must also be > 0.
- Only prior dates are used to choose the algorithm for the current date.
- Forward gate additionally requires active_dates >= 5, rows >= 10, and top5-removed ROI > 0.

## Verdict

| gate | status |
|---|---|
| `significance` | `FAIL` |
| `baseline` | `FAIL` |
| `forward` | `FAIL` |
| `verdict` | `inconclusive` |

Final verdict: `inconclusive`. No live action.

## Decision Proxy Walk-Forward

| selector | active dates | no-trade dates | rows | ROI | ROI CI | baseline ROI | excess ROI | excess CI | top5 removed ROI | gates | algorithm counts |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `balanced` | 7 | 8 | 25 | +42.1% | [+7.2%, +73.2%] | +18.7% | +23.5% | [-6.2%, +49.5%] | -4.6% | `PASS/FAIL/FAIL -> inconclusive` | `{'adj2_norm_mass_long_e008': 3, 'adj3_norm_mass_long_e010': 1, 'adj3_raw_mass_long_e015': 3}` |
| `strict_tail` | 6 | 9 | 38 | +21.0% | [-22.9%, +67.5%] | +17.6% | +3.4% | [-33.0%, +39.8%] | -45.8% | `FAIL/FAIL/FAIL -> inconclusive` | `{'adj2_norm_mass_long_e008': 6}` |

## Orderbook Walk-Forward

| selector | active dates | no-trade dates | rows | ROI | ROI CI | baseline ROI | excess ROI | excess CI | top5 removed ROI | gates | algorithm counts |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `balanced` | 1 | 8 | 8 | +11.4% | [+11.4%, +11.4%] | -0.2% | +11.6% | [+10.7%, +13.4%] | NA | `PASS/PASS/FAIL -> inconclusive` | `{'center_over_shoulders_e010': 1}` |
| `strict_tail` | 1 | 8 | 2 | -24.2% | [-24.2%, -24.2%] | -28.4% | +4.2% | [+4.2%, +4.2%] | NA | `FAIL/PASS/FAIL -> inconclusive` | `{'adj2_norm_mass_long_e008': 1}` |

## Notes

- This is an expanding-window test over fixed v0.3 algorithm definitions.
- It can trade different algorithms over time, but it cannot inspect the current/future date outcome before selecting.
- Passing requires both proxy and executable orderbook versions to pass significance, baseline, and forward gates.
