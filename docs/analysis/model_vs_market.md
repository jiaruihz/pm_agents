# Model Vs Market

> Living doc for module [1]: whether the weather probability model has alpha beyond market prices.
> Current status: `inconclusive` for residual model use, negative for global probability alpha and unconfirmed for rank alpha.
> Last updated: 2026-06-09.

Quant lineage anchor: model outputs enter the chain through Signal / candidate fields such as `model_p_yes`, `model_side_prob`, and model-derived edge. This document evaluates whether those fields should influence Signal, TradePlan, or sizing; it does not redefine fill PnL or account cashflow.

## Current Conclusion

The current evidence does **not** support treating the global weather model probability as a standalone alpha source.
Existing calibration snapshots show raw `model_p_yes` losing to market-implied probability on out-of-sample Brier,
while the 0.3 model / 0.7 market blend improvement is too small to treat as confirmed alpha without uncertainty bands.

This does not prove the model has no remaining use, but the first rank-IC pass also failed the live-action gates:

1. **Ranking / IC**: `model_edge_at_decision` did not show significant rank power versus realized ROI or counterfactual PnL; the date-based forward top-rank test also failed.
2. **Conditional subpools**: model value could still exist in specific city, forecast source, season, or lead-time slices, but any such pool must pass train/holdout validation before live use.

Important nuance: `model_side_prob` has positive IC against raw side win and decision ROI, but that mostly says high-probability sides win more often. It is not enough to prove tradable edge because the actionable score is excess over entry price / market price, and that score is not significant.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/analysis/2026-06/2026-06-05-probability-calibration.md` | through 2026-06-05 snapshot | Raw model vs market calibration and ensemble baseline | snapshot |
| `docs/analysis/2026-06/2026-06-07-mid-price-core-v1-raw-degradation.md` | 2026-06 degradation review | raw / mid-price v1 degradation after June 1 | snapshot |
| `docs/analysis/2026-06/2026-06-07-mid-price-core-v1-raw-calibration-drift.md` | 2026-06 drift review | calibration drift and market divergence | snapshot |
| `docs/analysis/2026-06/2026-06-07-mid-price-core-v1-forecast-timing-degradation-lineage.md` | 2026-06 timing review | forecast timing, side flip, market adverse move lineage | snapshot |
| `docs/analysis/2026-06/2026-06-07-mid-price-core-v1-city-model-downgrade.md` | 2026-06 city/model review | weak city/model slices and downgrade candidates | snapshot |
| `docs/analysis/2026-06/2026-06-08-blender-signal-value-research.md` | 2026-06 blender research | blender hard-gate and sizing signal value | snapshot |
| `docs/analysis/2026-06/2026-06-09-model-rank-ic.md` | 2026-05-12 to 2026-06-06 candidate rows | Ring3 rank/IC test; model edge ranking inconclusive, forward test failed | snapshot |

## Required Gates Before Live Use

Any claim that the model should affect live gates, city pools, or sizing must report:

| Gate | Required Evidence |
|---|---|
| Significance | Bootstrap 95% CI for excess ROI or Brier/log-loss delta |
| Baseline | Excess over market-implied probability or same-price dumb baseline |
| Forward | Train-selected rule holds in date-based holdout |

Any live decision change must be routed through `WEATHER_CITY_POOL_DECISIONS.md` or the deploy flow if it changes production behavior.

Conclusion labels:

- `confirmed`: all gates pass; live action may be proposed through deploy flow.
- `shadow_candidate`: signal looks useful but forward validation is missing; shadow/paper only.
- `inconclusive`: one or more gates fail or are unavailable.

## Open Work

1. Re-run IC after latest N100 sync + DB rebuild if the local snapshot is refreshed.
2. Extend rank-IC only as a shadow research path for pre-declared city, forecast source, and lead-time buckets.
3. Keep market-structure tests in `market_structure_edge.md`; do not conflate model alpha with BUY_NO base-rate.
4. Do not use `model_side_prob` alone as a live gate; any live proposal must prove excess over market/entry price.
