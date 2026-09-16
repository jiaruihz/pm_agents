# Regime Margin And Salvage V1

## Conclusion

Current `fresh_runway_current_no` already requires `current_no_escape_margin_native > 0`. Raising it would have blocked today's Chengdu at any threshold above `0.1` native units; `>0.25` blocks Chengdu only among current accepted live rows, while `>0.5` also blocks CapeTown's 0.5-margin row.

Historical replay is mixed: stricter margins improve the all-settled point estimate, but the small forward window gets worse because two low-margin winners are removed. This is not enough to promote a live hard filter; the cleaner action is shadow-tag `margin_bucket` and require more fresh forward evidence before changing live.

Residual/salvage exit is a separate overlay. Today's Beijing manual sell recovered cash, but the regime journal does not contain that sell, and historical proof needs PIT official-report trigger plus executable exit bid replay. Do not fold it into regime live until the residual-capture research supplies that exit model.

## All Settled Margin Sweep

| min_margin_native_strict_gt | rows | additional_removed_vs_current | removed_wins | removed_pnl_usd | pnl_usd | roi | delta_pnl_vs_current_usd | delta_roi_vs_current |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.000 | 48 | 0 | 0 | $+0.00 | $+48.82 | +20.7% | $+0.00 | 0.000 |
| 0.250 | 40 | 8 | 2 | $-10.28 | $+59.10 | +29.5% | $+10.28 | 0.088 |
| 0.500 | 36 | 12 | 4 | $-8.99 | $+57.81 | +32.0% | $+8.99 | 0.113 |
| 1.000 | 28 | 20 | 9 | $+9.47 | $+39.35 | +27.8% | $-9.47 | 0.071 |

## Forward Margin Sweep

| min_margin_native_strict_gt | rows | additional_removed_vs_current | removed_wins | removed_pnl_usd | pnl_usd | roi | delta_pnl_vs_current_usd | delta_roi_vs_current |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.000 | 4 | 0 | 0 | $+0.00 | $+3.15 | +17.1% | $+0.00 | 0.000 |
| 0.250 | 3 | 1 | 1 | $+5.22 | $-2.07 | -14.9% | $-5.22 | -0.320 |
| 0.500 | 2 | 2 | 2 | $+11.69 | $-8.54 | -100.0% | $-11.69 | -1.171 |
| 1.000 | 2 | 2 | 2 | $+11.69 | $-8.54 | -100.0% | $-11.69 | -1.171 |

## Current Live Accepted Rows

| city | target_date | bracket | route_leg | current_no_escape_margin_native | kept_gt_0.25 | kept_gt_0.5 | kept_gt_1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| CapeTown | 2026-07-05 | 18 | fresh_runway_current_no | 0.500 | True | False | False |
| Miami | 2026-07-05 | 90-91 | capped_d2_no | NA | True | True | True |
| Atlanta | 2026-07-05 | 92-93 | fresh_runway_current_no | 1.200 | True | True | True |
| Beijing | 2026-07-06 | 33 | fresh_runway_current_no | 1.600 | True | True | True |
| Chengdu | 2026-07-06 | 37 | fresh_runway_current_no | 0.100 | False | False | False |

## Data Notes

- Generated at `2026-07-06T09:55:56+00:00` after Mac market sync and `run_stack.sh` rebuild.
- Evidence layer: `regime_time_route_expansion_v1/selected_rows.csv` for historical live-like replay, plus `accepted_candidates.jsonl` for current live accepted rows.
- Margin is measured in market native units: C markets use °C, F markets use °F.
- `>` is strict because the current runner uses `margin.gt(min_current_no_escape_margin_native)`.
- Salvage overlay is intentionally not scored here because manual UI exits are outside the canonical regime order journal.
