# City-Day Basket PR2b Robustness Check

> generated_at_utc: `2026-06-05T17:38:28+00:00`
> db: `/home/rui/projects/pm_agent/runtime/weather.db`
> config: `{'single_leg_notional_small': 3.0, 'single_leg_notional_normal': 8.0, 'city_day_notional_cap': 15.0, 'max_no_legs_per_city_day': 4, 'edge_small_threshold': 0.03, 'edge_normal_threshold': 0.06, 'prefer_no_over_yes': False}`

## Slice Summary

| slice | rows | range | raw ROI | blended ROI | basket ROI | basket top5 ROI | missed | avoided | gates |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| full | 1878 | 2026-05-06 -> 2026-06-04 | +6.48% | +14.48% | +28.16% | +3.44% | $784 | $825 | 4/4 |
| train_pre_2026_05_26 | 1282 | 2026-05-06 -> 2026-05-25 | +14.47% | +24.52% | +40.53% | +7.44% | $572 | $540 | 3/4 |
| holdout_from_2026_05_26 | 596 | 2026-05-26 -> 2026-06-04 | -10.76% | -5.91% | +4.23% | -29.31% | $212 | $285 | 2/4 |
| recent_from_2026_06_01 | 147 | 2026-06-01 -> 2026-06-04 | +1.54% | +4.27% | +8.04% | -33.60% | $99 | $75 | 1/4 |
| live_filled_only | 305 | 2026-05-16 -> 2026-06-04 | +2.52% | +5.56% | +11.67% | -15.35% | $254 | $240 | 1/4 |

## Quant Read

- Full-sample PR2b pass is not enough for production because the selected parameters were chosen on the same sample.
- Holdout and recent slices are the real overfit check. Positive raw ROI is not required, but tail-removed basket ROI should stay nonnegative before live promotion.
- `live_filled_only` is an opportunity-subset counterfactual, not actual wallet PnL and not authoritative live fills accounting.

Production remains unchanged.
