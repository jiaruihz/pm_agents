# Range RV Positive Profiles v0.2

> generated_at_utc: `2026-06-08T17:18:19.357448+00:00`
> target_metric: `eligible_adjacent3_inside_range_yes_relative_value_alpha`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: local research only; no N100/live config changed.

## Data Snapshot

- Data source: `runtime/weather.db.fact_signal_candidates`; `fact_trades` only for mandatory self-check.
- DB last_modified: `2026-06-08T17:00:43.284789+00:00`
- fact built at: `2026-06-08T01:32:49.788100+00:00`
- Scanner rows: candidates `2488`, ranges `6245`, eligible adjacent_3 long ranges `75`.
- No live_real PnL is published; CLOB coverage gate is not required for this counterfactual opportunity test.

## Pre-Registered Thesis

`eligible-only adjacent_3 long/inside_range_yes` is tested as a positive Range RV profile. The baseline is the full eligible adjacent_3 long universe in the same train/holdout split. Profiles are structural filters only: no city/date/model selector and no post-hoc threshold search.

## Gates

| gate | status |
|---|---|
| `significance` | `FAIL` |
| `baseline` | `FAIL` |
| `forward` | `FAIL` |
| `verdict` | `inconclusive` |

Final verdict: `inconclusive`. No live action.

## Train / Holdout

- Split field: `event_date`.
- Train dates: `2026-05-06` to `2026-05-28`.
- Holdout dates: `2026-05-29` to `2026-06-06`.
- Profiles tested: `7`; no multiple-comparison winner is promoted unless all three gates pass.

## Decision Proxy Profiles

| profile | train rows | train dates | holdout rows | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `base_e015` | 27 | 7 | 20 | 2 | +24.8% | [+9.5%, +38.9%] | +14.0% | [+0.6%, +31.3%] | +29.0% | [+1.4%, +52.7%] | +3.5% | [-9.5%, +20.0%] | NA | `PASS/PASS/FAIL -> inconclusive` |
| `model_cover_070` | 23 | 7 | 13 | 2 | +20.9% | [+8.0%, +38.7%] | +10.1% | [+0.3%, +25.4%] | +8.2% | [-36.7%, +41.6%] | -17.3% | [-47.5%, +8.8%] | NA | `PASS/PASS/FAIL -> inconclusive` |
| `cheap_cost_075` | 22 | 6 | 20 | 2 | +29.8% | [+10.8%, +44.4%] | +18.9% | [+1.4%, +38.7%] | +29.0% | [+1.4%, +52.7%] | +3.5% | [-9.5%, +20.0%] | NA | `PASS/PASS/FAIL -> inconclusive` |
| `model_cover_and_cost` | 18 | 6 | 13 | 2 | +24.6% | [-2.1%, +45.6%] | +13.8% | [-9.0%, +32.8%] | +8.2% | [-36.7%, +41.6%] | -17.3% | [-47.5%, +8.8%] | NA | `FAIL/FAIL/FAIL -> inconclusive` |
| `low_loss_prob_035` | 21 | 7 | 14 | 2 | +22.9% | [+6.6%, +37.2%] | +12.1% | [-3.7%, +30.8%] | +9.0% | [-37.2%, +54.4%] | -16.4% | [-48.0%, +21.6%] | NA | `PASS/FAIL/FAIL -> inconclusive` |
| `high_edge_020` | 21 | 7 | 15 | 2 | +15.9% | [-15.4%, +38.4%] | +5.1% | [-26.9%, +30.7%] | +23.8% | [-17.7%, +55.1%] | -1.7% | [-28.6%, +22.3%] | NA | `FAIL/FAIL/FAIL -> inconclusive` |
| `positive_model_ev` | 27 | 7 | 20 | 2 | +24.8% | [+10.4%, +39.1%] | +14.0% | [+1.1%, +32.2%] | +29.0% | [+1.4%, +52.7%] | +3.5% | [-9.5%, +20.0%] | NA | `PASS/PASS/FAIL -> inconclusive` |

## Orderbook Executable Subset

Orderbook prices use latest `snapshot_ts_utc <= decision_snapshot_ts_utc`. This is an execution reality check, not the primary profile selector.

- Fully matched ranges: `3548` / `6245`.

| profile | train rows | train dates | holdout rows | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `base_e015` | 14 | 4 | 18 | 2 | +14.5% | [+2.5%, +37.2%] | +14.8% | [-1.7%, +44.9%] | +20.0% | [-6.9%, +43.8%] | +2.6% | [-10.0%, +18.2%] | NA | `PASS/FAIL/FAIL -> inconclusive` |
| `model_cover_070` | 13 | 4 | 13 | 2 | +6.9% | [+1.5%, +23.9%] | +7.1% | [-2.2%, +33.4%] | +0.5% | [-42.3%, +33.7%] | -16.9% | [-45.5%, +8.1%] | NA | `PASS/FAIL/FAIL -> inconclusive` |
| `cheap_cost_075` | 9 | 3 | 18 | 2 | +19.1% | [-5.0%, +44.2%] | +19.4% | [-9.8%, +50.7%] | +20.0% | [-6.9%, +43.8%] | +2.6% | [-10.0%, +18.2%] | NA | `FAIL/FAIL/FAIL -> inconclusive` |
| `model_cover_and_cost` | 8 | 3 | 13 | 2 | +2.9% | [-7.1%, +41.8%] | +3.2% | [-10.1%, +48.4%] | +0.5% | [-42.3%, +33.7%] | -16.9% | [-45.5%, +8.1%] | NA | `FAIL/FAIL/FAIL -> inconclusive` |
| `low_loss_prob_035` | 10 | 4 | 13 | 2 | +15.4% | [-2.3%, +38.5%] | +15.7% | [-6.5%, +46.1%] | +2.0% | [-42.3%, +47.4%] | -15.5% | [-45.5%, +21.8%] | NA | `FAIL/FAIL/FAIL -> inconclusive` |
| `high_edge_020` | 10 | 4 | 13 | 2 | +6.3% | [-28.6%, +43.7%] | +6.5% | [-33.2%, +50.7%] | +14.9% | [-27.0%, +49.2%] | -2.6% | [-30.2%, +23.6%] | NA | `FAIL/FAIL/FAIL -> inconclusive` |
| `positive_model_ev` | 14 | 4 | 18 | 2 | +14.5% | [+2.5%, +36.8%] | +14.8% | [-1.7%, +44.9%] | +20.0% | [-6.9%, +43.8%] | +2.6% | [-10.0%, +18.2%] | NA | `PASS/FAIL/FAIL -> inconclusive` |

## Interpretation

- A profile must pass significance, baseline, and forward gates before it can be called confirmed.
- Positive holdout point estimates are not enough when the bootstrap CI crosses zero or excess CI crosses zero.
- `top5 removed ROI` is reported as a tail-dependency diagnostic and blocks escalation when it flips materially negative.
