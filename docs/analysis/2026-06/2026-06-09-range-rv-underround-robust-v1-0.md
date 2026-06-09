# Range RV All-YES Underround Robustness v1.0

> generated_at_utc: `2026-06-09T02:15:57.834568+00:00`
> target_metric: `all_yes_underround_range_rv_alpha`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: local research only; no N100/live config changed.

## Data Snapshot

- Data source: `runtime/weather.db.fact_signal_candidates`; `fact_trades` only for mandatory self-check.
- DB last_modified: `2026-06-08T17:23:59.056493+00:00`
- fact built at: `2026-06-08T17:23:55.700271+00:00`
- Base all-YES rows: `265`; orderbook matched base rows `137` / `265`.
- Thresholds: `[0.005, 0.01, 0.02, 0.03, 0.05]`.
- Evaluation requires exactly one settled winner in the observed bracket set.
- No live_real PnL is published; this is opportunity-grain counterfactual research.

## Verdict

| gate | status |
|---|---|
| `significance` | `PASS` |
| `baseline` | `PASS` |
| `forward` | `PASS` |
| `verdict` | `confirmed` |

Final verdict: `confirmed`. No live action unless both proxy and executable families are confirmed.

## Decision Proxy Thresholds

| algorithm | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates | gate reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `underround_proxy_all_yes_e050` | 196 | 102 | 20 | 69 | 49 | 8 | +18.6% | [+15.5%, +21.4%] | +10.2% | [+7.5%, +13.2%] | +19.3% | [+9.8%, +24.6%] | +6.2% | [+1.7%, +9.5%] | +8.0% | `PASS/PASS/PASS -> confirmed` | `` |
| `underround_proxy_all_yes_e030` | 196 | 119 | 20 | 69 | 52 | 9 | +16.3% | [+13.1%, +19.0%] | +7.9% | [+5.4%, +10.6%] | +18.3% | [+8.3%, +22.8%] | +5.2% | [+0.8%, +7.7%] | +7.4% | `PASS/PASS/PASS -> confirmed` | `` |
| `underround_proxy_all_yes_e020` | 196 | 142 | 20 | 69 | 57 | 9 | +13.8% | [+11.3%, +16.0%] | +5.4% | [+3.7%, +7.7%] | +16.7% | [+8.4%, +19.1%] | +3.6% | [+0.7%, +4.3%] | +7.4% | `PASS/PASS/PASS -> confirmed` | `` |
| `underround_proxy_all_yes_e010` | 196 | 152 | 21 | 69 | 59 | 9 | +12.9% | [+10.4%, +15.2%] | +4.5% | [+2.8%, +7.3%] | +16.1% | [+8.3%, +18.2%] | +3.1% | [+1.0%, +3.6%] | +7.4% | `PASS/PASS/PASS -> confirmed` | `` |
| `underround_proxy_all_yes_e005` | 196 | 159 | 21 | 69 | 60 | 9 | +12.3% | [+10.0%, +14.5%] | +3.9% | [+2.3%, +6.0%] | +15.8% | [+8.3%, +18.2%] | +2.8% | [+0.0%, +3.4%] | +7.4% | `PASS/PASS/FAIL -> inconclusive` | `` |

## Executable Orderbook Thresholds

Executable thresholds use `1 - orderbook_taker_cost` and only rows with all legs matched by `snapshot_ts <= decision_ts`.

| algorithm | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates | gate reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `underround_exec_all_yes_e050` | 68 | 26 | 8 | 69 | 25 | 6 | +15.5% | [+11.8%, +19.7%] | +15.4% | [+10.8%, +22.7%] | +25.1% | [+10.3%, +29.1%] | +19.0% | [+5.8%, +22.5%] | +7.5% | `PASS/PASS/PASS -> confirmed` | `` |
| `underround_exec_all_yes_e030` | 68 | 30 | 8 | 69 | 34 | 8 | +13.8% | [+10.3%, +17.5%] | +13.7% | [+8.7%, +22.7%] | +18.6% | [+7.1%, +22.7%] | +12.5% | [+1.8%, +16.0%] | +4.7% | `PASS/PASS/PASS -> confirmed` | `` |
| `underround_exec_all_yes_e020` | 68 | 32 | 8 | 69 | 38 | 9 | +13.0% | [+9.8%, +16.6%] | +12.9% | [+8.3%, +22.1%] | +16.6% | [+6.4%, +21.6%] | +10.5% | [+1.1%, +14.8%] | +4.2% | `PASS/PASS/PASS -> confirmed` | `` |
| `underround_exec_all_yes_e010` | 68 | 33 | 8 | 69 | 40 | 9 | +12.6% | [+9.8%, +15.5%] | +12.5% | [+8.2%, +22.9%] | +15.8% | [+6.1%, +21.1%] | +9.7% | [+1.1%, +14.0%] | +4.2% | `PASS/PASS/PASS -> confirmed` | `` |
| `underround_exec_all_yes_e005` | 68 | 34 | 8 | 69 | 41 | 9 | +12.2% | [+9.3%, +15.0%] | +12.1% | [+7.5%, +20.2%] | +15.3% | [+6.5%, +20.8%] | +9.2% | [+1.3%, +13.8%] | +4.2% | `PASS/PASS/PASS -> confirmed` | `` |

## Notes

- This is a threshold robustness test, not a threshold optimizer.
- Passing requires a threshold family to survive cluster bootstrap, baseline excess, forward holdout, and top5-removed stress.
- The expression is model-free: model probabilities are recorded but not used for selection.
