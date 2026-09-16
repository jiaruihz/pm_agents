# City-Day Distribution Quality Research — 2026-06-06

> generated_at_utc: `2026-06-05T18:27:12+00:00`
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
| full | uniform | 453 | 0.9009 | 0.5371 | 43.0% | 0.4629 | 1.00 | 0.861 |
| full | raw_norm | 453 | 1.1562 | 0.6088 | 49.7% | 0.4885 | 1.77 | 0.665 |
| full | market_norm | 453 | 0.8000 | 0.4852 | 56.3% | 0.5200 | 1.59 | 0.762 |
| full | blend_norm | 453 | 0.7949 | 0.4839 | 56.5% | 0.5135 | 1.58 | 0.787 |
| full | dist_blend_norm | 453 | 0.7960 | 0.4850 | 57.2% | 0.5134 | 1.57 | 0.786 |
| train_pre_2026_05_26 | uniform | 293 | 0.9455 | 0.5607 | 41.0% | 0.4393 | 1.00 | 0.894 |
| train_pre_2026_05_26 | raw_norm | 293 | 1.1380 | 0.6069 | 50.5% | 0.4796 | 1.76 | 0.692 |
| train_pre_2026_05_26 | market_norm | 293 | 0.8505 | 0.5090 | 53.6% | 0.4968 | 1.66 | 0.794 |
| train_pre_2026_05_26 | blend_norm | 293 | 0.8292 | 0.5009 | 53.2% | 0.4946 | 1.62 | 0.816 |
| train_pre_2026_05_26 | dist_blend_norm | 293 | 0.8280 | 0.5001 | 54.6% | 0.4953 | 1.61 | 0.815 |
| holdout_from_2026_05_26 | uniform | 160 | 0.8192 | 0.4940 | 46.9% | 0.5060 | 1.00 | 0.800 |
| holdout_from_2026_05_26 | raw_norm | 160 | 1.1897 | 0.6123 | 48.1% | 0.5049 | 1.79 | 0.615 |
| holdout_from_2026_05_26 | market_norm | 160 | 0.7075 | 0.4417 | 61.3% | 0.5623 | 1.46 | 0.702 |
| holdout_from_2026_05_26 | blend_norm | 160 | 0.7321 | 0.4528 | 62.5% | 0.5483 | 1.50 | 0.733 |
| holdout_from_2026_05_26 | dist_blend_norm | 160 | 0.7375 | 0.4574 | 61.9% | 0.5467 | 1.51 | 0.731 |
| recent_from_2026_06_01 | uniform | 44 | 0.6028 | 0.4091 | 50.0% | 0.5909 | 1.00 | 0.750 |
| recent_from_2026_06_01 | raw_norm | 44 | 0.9363 | 0.5767 | 59.1% | 0.5655 | 1.50 | 0.558 |
| recent_from_2026_06_01 | market_norm | 44 | 0.6203 | 0.4241 | 56.8% | 0.5940 | 1.50 | 0.720 |
| recent_from_2026_06_01 | blend_norm | 44 | 0.6279 | 0.4265 | 59.1% | 0.5907 | 1.50 | 0.726 |
| recent_from_2026_06_01 | dist_blend_norm | 44 | 0.6455 | 0.4413 | 59.1% | 0.5854 | 1.50 | 0.719 |
| live_filled_only | uniform | 110 | 0.4575 | 0.3152 | 69.1% | 0.6848 | 1.00 | 0.591 |
| live_filled_only | raw_norm | 110 | 0.7226 | 0.4022 | 68.2% | 0.6923 | 1.35 | 0.429 |
| live_filled_only | market_norm | 110 | 0.4521 | 0.3095 | 72.7% | 0.6962 | 1.30 | 0.566 |
| live_filled_only | blend_norm | 110 | 0.4534 | 0.3084 | 75.5% | 0.6957 | 1.29 | 0.569 |
| live_filled_only | dist_blend_norm | 110 | 0.4585 | 0.3124 | 76.4% | 0.6948 | 1.28 | 0.566 |

## Quant Read

- If market-normalized distributions dominate holdout, basket objective should stay market-anchored.
- If blend distributions improve only full/train but not holdout/recent, using them in an optimizer can amplify overfit.
- Distribution quality should be checked before adding more basket objective complexity.

Production remains unchanged.
