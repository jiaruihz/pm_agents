# Model Vs Market

> Living doc for module [1]: whether the weather probability model has alpha beyond market prices.
> Current status: `inconclusive` for residual model use, negative for global probability alpha and unconfirmed for rank alpha.
> Last updated: 2026-06-15 source-adjusted forecast quality v0.

Quant lineage anchor: model outputs enter the chain through Signal / candidate fields such as `model_p_yes`, `model_side_prob`, and model-derived edge. This document evaluates whether those fields should influence Signal, TradePlan, or sizing; it does not redefine fill PnL or account cashflow.

## Current Conclusion

The current evidence does **not** support treating the global weather model probability as a standalone alpha source.
Existing calibration snapshots show raw `model_p_yes` losing to market-implied probability on out-of-sample Brier,
while the 0.3 model / 0.7 market blend improvement is too small to treat as confirmed alpha without uncertainty bands.

This does not prove the model has no remaining use, but the first rank-IC pass also failed the live-action gates:

1. **Ranking / IC**: `model_edge_at_decision` did not show significant rank power versus realized ROI or counterfactual PnL. After decision-window backfill, the forward top-rank test is same-sign but still not significant, so it remains non-actionable.
2. **Conditional subpools**: model value could still exist in specific city, forecast source, season, or lead-time slices, but any such pool must pass train/holdout validation before live use.

Important nuance: `model_side_prob` has positive IC against raw side win and decision ROI, but that mostly says high-probability sides win more often. It is not enough to prove tradable edge because the actionable score is excess over entry price / market price, and that score is not significant.

### Forecast Quality / Reliability Base

The 2026-06-13 forecast-quality-base v0 pass changes how follow-up research should use model information. The current conclusion is:

- Forecast quality has value as a **shared reliability layer**, not as a standalone live strategy.
- `forecast_quality_medium_plus` and `city_model_reliable` are the most useful soft allow / risk tags so far, especially for BUY_NO single-leg and side-band proxy overlays.
- `forecast_quality_low`, `tail_risk_high`, and `model_market_disagreement_high` are diagnostic weak-quality or risk tags. They are not hard no-trade gates yet.
- Cross-family evidence is directionally useful but still fails live gates: holdout samples are thin, top-date stress is unstable, and this v0 uses decision-price proxy rather than time-aligned executable pricing.

Any future Range RV, adjacent3, side-band, single-leg, or basket study that uses forecast reliability should consume the same base labels at `city + event_date + forecast_source/model_version + decision_snapshot_ts_utc` grain, then compare against that family’s no-quality-filter baseline. Do not copy the v0 thresholds into live config; rederive thresholds on the train window for each rerun.

### Source-Adjusted Reliability Rule

The 2026-06-15 source-adjusted rerun adds one required caveat: forecast-quality labels must carry settlement-source context. The generic denominator should default to `default_wu_station_by_rules` cities, with any watchlist/default exceptions shown explicitly.

Current source-aware evidence:

- `default_wu` is the main generic denominator: 188 decision sets / 34 cities, holdout adjacent3 hit 95.7%, tail miss 4.3%.
- `source_sensitive_confirmed` is real but different: 45 decision sets / 8 cities, including HongKong HKO and official-station-diff cities such as Jakarta WIHH. These rows require official-source feature adapters before making source-sensitive forecast claims.
- `blocked_unresolved` is small but should not train or validate generic source-sensitive claims: 19 decision sets / 3 cities.
- HK/Jakarta should not be treated as generic VHHH/WIII configured-station rows. HongKong uses HKO Daily Extract floor semantics; Jakarta uses WIHH/Halim.

Consumer rule: Range RV, adjacent3, side-band, BUY_NO single-leg, and basket studies must report source bucket alongside no-quality-filter and forecast-quality-filter baselines. If a tag only works in source-sensitive cities, it is not a generic forecast-quality base.

## Absorbed Historical Claims

1. The 2026-06-05 calibration run is the current baseline warning: raw model probability lost to market probability out of sample. Blends can be useful for shrinkage or guardrails, but their observed edge is too small to promote without uncertainty and forward tests.
2. The 2026-06-07 mid-price-core v1 degradation reports show a real post-June deterioration in the raw edge path, especially low/mid raw-edge buckets and ECMWF-heavy slices. They do **not** prove a single code-break root cause; timing, execution selection, city/model mix, and market drift all remain plausible contributors.
3. The calibration-drift audit did not establish a clean `code_version` breakpoint in fact rows. Treat breakpoint hypotheses as diagnostics to rerun with raw signal/plan/order lineage, not as a settled explanation.
4. The 2026-06-08 city/model conditional edge study had large positive point estimates in selected pockets but failed significance, baseline, and forward gates. It can guide shadow tags, not city/model live allowlists.
5. The 2026-06-09 rank-IC run keeps `model_edge_at_decision` non-actionable. `model_side_prob` can describe outcome likelihood, but it cannot by itself set live gates or sizes because the tradable claim is excess over market entry price.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/analysis/2026-06/2026-06-05-probability-calibration.md` | through 2026-06-05 snapshot | Raw model vs market calibration and ensemble baseline | active-evidence |
| `docs/analysis/2026-06/2026-06-07-mid-price-core-v1-raw-degradation.md` | 2026-06 degradation review | consolidated raw/mid-price degradation, calibration and code-break audit after June 1 | active-evidence |
| `docs/analysis/2026-06/2026-06-07-mid-price-core-v1-forecast-timing-degradation-lineage.md` | 2026-06 timing review | forecast timing, side flip, market adverse move lineage | active-evidence |
| `docs/analysis/2026-06/2026-06-07-mid-price-core-v1-city-model-downgrade.md` | 2026-06 city/model review | weak city/model slices and downgrade candidates | active-evidence |
| `docs/analysis/2026-06/2026-06-08-city-model-conditional-edge.md` | 2026-05-12 to 2026-06-06 candidate rows | city x model train-selected pockets; gates failed | active-evidence |
| `docs/analysis/2026-06/2026-06-08-blender-signal-value-research.md` | 2026-06 blender research | blender hard-gate and sizing signal value | cross-domain-reference |
| `docs/analysis/2026-06/2026-06-09-decision-window-backfill.md` | 2026-06-09 local DB repair | backfilled 2,186 candidate decision windows from raw orderbook with 0.005 wear | active-evidence |
| `docs/analysis/2026-06/2026-06-09-model-rank-ic.md` | 2026-05-12 to 2026-06-06 candidate rows | Ring3 rank/IC test after backfill; model edge ranking still inconclusive | active-evidence |
| `docs/analysis/2026-06/2026-06-13-forecast-quality-base-v0.md` | 2026-05-06 to 2026-06-10 settled decision sets | reusable forecast reliability labels and cross-family overlays; research/shadow only | active-evidence |
| `docs/analysis/2026-06/2026-06-15-forecast-quality-source-adjusted-v0.md` | 2026-05-06 to 2026-06-10 settled decision sets | forecast-quality labels stratified by settlement source class; HK/Jakarta/station-diff require source-aware treatment | active-evidence |

## Required Gates Before Live Use

Any claim that the model should affect live gates, city pools, or sizing must report:

| Gate | Required Evidence |
|---|---|
| Significance | Bootstrap 95% CI for excess ROI or Brier/log-loss delta |
| Baseline | Excess over market-implied probability or same-price dumb baseline |
| Forward | Train-selected rule holds in date-based holdout |
| Separation | Probability quality and tradable edge must be reported separately; do not use side win probability as price edge |

Any live decision change must be routed through `WEATHER_CITY_POOL_DECISIONS.md` or the deploy flow if it changes production behavior.

Conclusion labels:

- `confirmed`: all gates pass; live action may be proposed through deploy flow.
- `shadow_candidate`: signal looks useful but forward validation is missing; shadow/paper only.
- `inconclusive`: one or more gates fail or are unavailable.

## Open Work

1. Materialize `forecast_run_ts_utc`, forecast issuance/checkpoint age, and forecast-source run id into `fact_signal_candidates`.
2. Materialize same-checkpoint ECMWF/GFS paired distribution features: mode distance, L1 distribution gap, entropy gap, and paired confidence/mass features.
3. Promote forecast-quality labels into a reusable generated artifact or fact-table sidecar so strategy scripts consume the same reliability layer.
4. Add `settlement_source_class`, `official_station_or_feed`, and `mapping_rule` to that sidecar; do not pool blocked unresolved cities into generic source-sensitive reliability claims.
5. Build HK HKO and Jakarta WIHH source adapters before treating those cities as aligned feature-source rows.
6. Re-run IC and reliability labels after each latest N100 sync + DB rebuild when the local snapshot is refreshed.
7. Extend rank-IC and reliability tests only for pre-declared city, forecast source, lead-time, source bucket, and strategy-family buckets.
8. Keep market-structure tests in `market_structure_edge.md`; do not conflate model reliability with BUY_NO base-rate.
9. Do not use `model_side_prob` or forecast-quality labels alone as a live gate; any live proposal must prove excess over market/entry price and survive time-aligned orderbook checks.
