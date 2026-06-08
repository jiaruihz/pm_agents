# Market Structure Edge

> Living doc for module [2]: whether weather markets contain model-free structural edge such as favorite-longshot bias, side base-rate, or price-bucket mispricing.
> Current status: `inconclusive`.
> Last updated: 2026-06-09.

## Current Conclusion

There is not yet enough evidence to promote a model-free market-structure rule into live trading. The first structural-edge pass is useful because it separates H_B from model alpha, but it remains a research signal until it passes date-forward validation and multiple-test controls.

Important distinction: if BUY_NO or a price bucket works because of market structure, that is not evidence that the weather probability model is good. It belongs here, not in `model_vs_market.md`.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/analysis/2026-06/2026-06-08-market-structural-edge.md` | 2026-06 first pass | H_B structural edge test with forward/date and cluster checks | snapshot |

## Required Gates Before Live Use

| Gate | Required Evidence |
|---|---|
| Significance | Cluster or block bootstrap CI for excess ROI / PnL; report multiple-test correction |
| Baseline | Compare against same-price dumb side baseline and market-implied probability |
| Forward | Rule selected on one window holds in a date-based holdout |
| Capacity | If rule survives, route to `execution_quality.md` and later capacity analysis before size-up |

## Open Work

1. Keep H_B separate from H_A model alpha.
2. Add explicit null baselines: always-buy-NO by price bucket, random same-price side, and market-implied outcome.
3. Promote only `confirmed` or `shadow_candidate` labels; otherwise leave as `inconclusive`.
