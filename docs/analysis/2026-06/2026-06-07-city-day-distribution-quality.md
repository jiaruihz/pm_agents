# City-Day Distribution Quality Research — 2026-06-06

> generated_at_utc: `2026-06-07T10:01:24+00:00`
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
| full | uniform | 526 | 0.9360 | 0.5526 | 41.6% | 0.4474 | 1.00 | 0.873 |
| full | raw_norm | 526 | 1.2505 | 0.6263 | 48.5% | 0.4732 | 1.80 | 0.673 |
| full | market_norm | 526 | 0.8148 | 0.4926 | 55.9% | 0.5096 | 1.59 | 0.771 |
| full | blend_norm | 526 | 0.8157 | 0.4943 | 55.1% | 0.5012 | 1.59 | 0.797 |
| full | dist_blend_norm | 526 | 0.8162 | 0.4950 | 55.7% | 0.5013 | 1.58 | 0.796 |
| train_pre_2026_05_26 | uniform | 303 | 0.9461 | 0.5602 | 41.6% | 0.4398 | 1.00 | 0.891 |
| train_pre_2026_05_26 | raw_norm | 303 | 1.1335 | 0.6098 | 49.8% | 0.4782 | 1.77 | 0.689 |
| train_pre_2026_05_26 | market_norm | 303 | 0.8467 | 0.5070 | 53.8% | 0.4977 | 1.65 | 0.793 |
| train_pre_2026_05_26 | blend_norm | 303 | 0.8276 | 0.5003 | 53.1% | 0.4949 | 1.62 | 0.814 |
| train_pre_2026_05_26 | dist_blend_norm | 303 | 0.8269 | 0.5000 | 54.5% | 0.4954 | 1.61 | 0.813 |
| holdout_from_2026_05_26 | uniform | 223 | 0.9222 | 0.5422 | 41.7% | 0.4578 | 1.00 | 0.848 |
| holdout_from_2026_05_26 | raw_norm | 223 | 1.4094 | 0.6486 | 46.6% | 0.4664 | 1.85 | 0.651 |
| holdout_from_2026_05_26 | market_norm | 223 | 0.7714 | 0.4731 | 58.7% | 0.5257 | 1.51 | 0.741 |
| holdout_from_2026_05_26 | blend_norm | 223 | 0.7994 | 0.4861 | 57.8% | 0.5099 | 1.54 | 0.773 |
| holdout_from_2026_05_26 | dist_blend_norm | 223 | 0.8017 | 0.4882 | 57.4% | 0.5093 | 1.54 | 0.772 |
| recent_from_2026_06_01 | uniform | 57 | 0.6011 | 0.4094 | 52.6% | 0.5906 | 1.00 | 0.754 |
| recent_from_2026_06_01 | raw_norm | 57 | 0.9253 | 0.5908 | 56.1% | 0.5577 | 1.53 | 0.565 |
| recent_from_2026_06_01 | market_norm | 57 | 0.5885 | 0.3984 | 61.4% | 0.6071 | 1.44 | 0.723 |
| recent_from_2026_06_01 | blend_norm | 57 | 0.6145 | 0.4183 | 61.4% | 0.5954 | 1.46 | 0.728 |
| recent_from_2026_06_01 | dist_blend_norm | 57 | 0.6266 | 0.4284 | 61.4% | 0.5923 | 1.46 | 0.722 |
| live_filled_only | uniform | 133 | 0.4939 | 0.3421 | 67.7% | 0.6579 | 1.00 | 0.647 |
| live_filled_only | raw_norm | 133 | 0.8958 | 0.4678 | 63.2% | 0.6510 | 1.40 | 0.468 |
| live_filled_only | market_norm | 133 | 0.4905 | 0.3400 | 70.7% | 0.6698 | 1.32 | 0.613 |
| live_filled_only | blend_norm | 133 | 0.4976 | 0.3437 | 71.4% | 0.6657 | 1.32 | 0.621 |
| live_filled_only | dist_blend_norm | 133 | 0.5041 | 0.3490 | 72.2% | 0.6639 | 1.32 | 0.619 |

## Quant Read

- If market-normalized distributions dominate holdout, basket objective should stay market-anchored.
- If blend distributions improve only full/train but not holdout/recent, using them in an optimizer can amplify overfit.
- Distribution quality should be checked before adding more basket objective complexity.

Production remains unchanged.
