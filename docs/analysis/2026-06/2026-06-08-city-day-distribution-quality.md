# City-Day Distribution Quality Research — 2026-06-08

> generated_at_utc: `2026-06-07T16:32:33+00:00`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: offline distribution diagnostic only; no N100/live behavior changed.

## Question

Before improving basket objectives, check whether the city-day temperature distribution is reliable enough.

Distributions:

- `uniform`: no-information baseline over listed brackets.
- `raw_norm`: normalize raw model bracket probabilities.
- `market_norm`: normalize market yes prices.
- `blend_norm`: per-bracket blend then normalize.
- `dist_blend_norm`: normalize raw and market first, then blend distributions.

Lower log loss / Brier is better; higher top1 accuracy / winner probability is better.

## Results

| slice | dist | n | logloss | brier | top1 | winner p | winner rank | entropy |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| full | uniform | 535 | 0.9319 | 0.5517 | 41.7% | 0.4483 | 1.00 | 0.875 |
| full | raw_norm | 535 | 1.2472 | 0.6283 | 48.4% | 0.4727 | 1.80 | 0.675 |
| full | market_norm | 535 | 0.8112 | 0.4912 | 56.1% | 0.5104 | 1.59 | 0.774 |
| full | blend_norm | 535 | 0.8128 | 0.4937 | 55.1% | 0.5018 | 1.59 | 0.799 |
| full | dist_blend_norm | 535 | 0.8139 | 0.4949 | 55.5% | 0.5017 | 1.58 | 0.798 |
| train_pre_2026_05_26 | uniform | 303 | 0.9461 | 0.5602 | 41.6% | 0.4398 | 1.00 | 0.891 |
| train_pre_2026_05_26 | raw_norm | 303 | 1.1335 | 0.6098 | 49.8% | 0.4782 | 1.77 | 0.689 |
| train_pre_2026_05_26 | market_norm | 303 | 0.8467 | 0.5070 | 53.8% | 0.4977 | 1.65 | 0.793 |
| train_pre_2026_05_26 | blend_norm | 303 | 0.8276 | 0.5003 | 53.1% | 0.4949 | 1.62 | 0.814 |
| train_pre_2026_05_26 | dist_blend_norm | 303 | 0.8269 | 0.5000 | 54.5% | 0.4954 | 1.61 | 0.813 |
| holdout_from_2026_05_26 | uniform | 232 | 0.9134 | 0.5405 | 41.8% | 0.4595 | 1.00 | 0.853 |
| holdout_from_2026_05_26 | raw_norm | 232 | 1.3957 | 0.6525 | 46.6% | 0.4655 | 1.84 | 0.656 |
| holdout_from_2026_05_26 | market_norm | 232 | 0.7647 | 0.4707 | 59.1% | 0.5269 | 1.50 | 0.749 |
| holdout_from_2026_05_26 | blend_norm | 232 | 0.7935 | 0.4850 | 57.8% | 0.5109 | 1.53 | 0.780 |
| holdout_from_2026_05_26 | dist_blend_norm | 232 | 0.7970 | 0.4883 | 56.9% | 0.5099 | 1.54 | 0.779 |
| recent_from_2026_06_01 | uniform | 66 | 0.6137 | 0.4217 | 51.5% | 0.5783 | 1.00 | 0.788 |
| recent_from_2026_06_01 | raw_norm | 66 | 0.9430 | 0.6121 | 54.5% | 0.5422 | 1.53 | 0.595 |
| recent_from_2026_06_01 | market_norm | 66 | 0.5899 | 0.4000 | 62.1% | 0.6003 | 1.42 | 0.755 |
| recent_from_2026_06_01 | blend_norm | 66 | 0.6189 | 0.4236 | 60.6% | 0.5872 | 1.45 | 0.759 |
| recent_from_2026_06_01 | dist_blend_norm | 66 | 0.6337 | 0.4366 | 59.1% | 0.5829 | 1.47 | 0.753 |
| live_filled_only | uniform | 139 | 0.4975 | 0.3453 | 67.6% | 0.6547 | 1.00 | 0.655 |
| live_filled_only | raw_norm | 139 | 0.8869 | 0.4692 | 63.3% | 0.6488 | 1.40 | 0.476 |
| live_filled_only | market_norm | 139 | 0.4895 | 0.3387 | 71.2% | 0.6688 | 1.31 | 0.622 |
| live_filled_only | blend_norm | 139 | 0.4967 | 0.3427 | 71.9% | 0.6647 | 1.32 | 0.629 |
| live_filled_only | dist_blend_norm | 139 | 0.5040 | 0.3489 | 71.9% | 0.6626 | 1.32 | 0.626 |

## Quant Read

- If market-normalized distributions dominate holdout, basket objective should stay market-anchored.
- If blend distributions improve only full/train but not holdout/recent, using them in an optimizer can amplify overfit.
- Distribution quality should be checked before adding more basket objective complexity.

Production remains unchanged.
