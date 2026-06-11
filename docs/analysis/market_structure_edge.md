# Market Structure Edge

> Living doc for module [2]: whether weather markets contain model-free structural edge such as favorite-longshot bias, side base-rate, or price-bucket mispricing.
> Current status: `mixed`: broad structure inconclusive; all-YES underround confirmed offline but not live-approved.
> Last updated: 2026-06-10 Phase 4D absorption.

## Current Conclusion

There is not yet enough evidence to promote a broad model-free market-structure rule into live trading. The first H_B structural-edge pass is useful because it separates market structure from model alpha, but it remains a research signal until it passes date-forward validation and multiple-test controls.

The exception is narrower: `all-YES underround` / no-arb basket tests are now the strongest confirmed offline market-structure family. The 2026-06-09 robust run passed proxy and time-aligned executable orderbook gates across several thresholds. That makes it a `confirmed_offline` research candidate, not a live rule. Before live use it still needs explicit order construction, all-leg availability checks, capacity/depth limits, settlement consistency checks, sizing, and monitoring.

Most other Range RV variants remain `inconclusive`: adjacent2/3 forecast-first, market-shape anomalies, center/shoulder/butterfly, tail-fade/uncertainty, temporal reversion, regime-conditioned scanners, and walk-forward selectors did not pass the three-gate standard.

Important distinction: if BUY_NO or a price bucket works because of market structure, that is not evidence that the weather probability model is good. It belongs here, not in `model_vs_market.md`.

## Absorbed Historical Claims

1. `2026-06-08-market-structural-edge.md` tested a model-free H_B structural hypothesis; selected BUY_NO buckets had positive point estimates but failed significance and baseline gates.
2. Early Range RV scanner files (`v0`, `v0-1`, `positive-v0-2`, `variant-lab-v0-3`) are useful as search history, but their final verdicts remain `inconclusive`; they should not drive live action.
3. `noarb_all_yes_underround` became the first durable family: v0.9 showed the signal in proxy, and `range-rv-underround-robust-v1-0.md` confirmed both proxy and executable orderbook thresholds.
4. Forecast-quality overlays should be treated as soft stratification. Strict forecast-quality hard filters did not reliably improve the no-filter adjacent3 baseline, and city/model samples are still thin.
5. Market-shape, temporal-reversion, regime, center/shoulder/butterfly, and tail-fade variants are negative or sample-limited evidence. Positive point estimates in those files are not enough because holdout, baseline, or top5-removed gates failed.
6. All Range RV reports are opportunity/counterfactual research unless a later shadow/paper/live run produces actual orders and fills. They must not be mixed with live_real PnL or CLOB account reconciliation.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/analysis/2026-06/2026-06-08-market-structural-edge.md` | 2026-06 first pass | H_B structural edge test with forward/date and cluster checks | snapshot |
| `docs/archive/analysis/2026-06/2026-06-09-range-rv-positive-v0-2.md` | 2026-06 Range RV positive profile pass | adjacent profile search; positive points but gates failed | superseded-evidence |
| `docs/analysis/2026-06/2026-06-09-range-rv-variant-lab-v0-3.md` | 2026-06 broad variant lab | pre-registered adjacent/range/pair/center/tail tests; no live action | active-evidence |
| `docs/archive/analysis/2026-06/2026-06-09-range-rv-walkforward-v0-4.md` | 2026-06 expanding-window selector | prior-date selector failed gates | active-evidence |
| `docs/archive/analysis/2026-06/2026-06-09-range-rv-market-shape-v0-5.md` | 2026-06 shape anomaly scanner | shape anomaly first, model confirmation second; inconclusive | active-evidence |
| `docs/archive/analysis/2026-06/2026-06-09-range-rv-temporal-reversion-v0-6.md` | 2026-06 temporal residual scanner | previous-snapshot reversion sample too thin; inconclusive | active-evidence |
| `docs/analysis/2026-06/2026-06-09-range-rv-market-shape-fullop-v0-7.md` | 2026-06 full opportunity shape scan | full-opportunity market-shape variants remained inconclusive | active-evidence |
| `docs/archive/analysis/2026-06/2026-06-09-range-rv-regime-v0-8.md` | 2026-06 regime scanner | distribution-regime filters failed gates | active-evidence |
| `docs/archive/analysis/2026-06/2026-06-09-range-rv-noarb-v0-9.md` | 2026-06 no-arb scanner | all-YES underround emerged; proxy stronger than executable in this pass | superseded-evidence |
| `docs/analysis/2026-06/2026-06-09-range-rv-underround-robust-v1-0.md` | 2026-06 robust underround run | all-YES underround confirmed offline in proxy and executable thresholds | confirmed-offline |
| `docs/analysis/2026-06/2026-06-09-forecast-quality-range-rv-overlay.md` | 2026-06 forecast-quality overlay | quality filters do not stably beat no-filter baseline | active-evidence |
| `docs/archive/analysis/2026-06/2026-06-09-forecast-first-adjacent-range-rv-v0-1.md` | 2026-06 forecast-first adjacent ranges | adjacent2/3 around forecast mode failed gates | active-evidence |
| `docs/archive/analysis/2026-06/2026-06-09-center-shoulders-butterfly-range-rv.md` | 2026-06 center/shoulder/butterfly | forecast-first butterfly structures sample-limited and inconclusive | active-evidence |
| `docs/archive/analysis/2026-06/2026-06-09-range-rv-tail-fade-uncertainty-v1-1.md` | 2026-06 tail fade / uncertainty | tail-fade baskets failed all gates | active-evidence |

## Required Gates Before Live Use

| Gate | Required Evidence |
|---|---|
| Significance | Cluster or block bootstrap CI for excess ROI / PnL; report multiple-test correction |
| Baseline | Compare against same-price dumb side baseline and market-implied probability |
| Forward | Rule selected on one window holds in a date-based holdout |
| Executability | For baskets, every leg must have `orderbook_snapshot_ts <= decision_snapshot_ts_utc`, matched asks, total cost, and settlement winner consistency |
| Capacity | If rule survives, route to `execution_quality.md` and later capacity analysis before size-up |

## Open Work

1. Keep H_B separate from H_A model alpha.
2. Add explicit null baselines: always-buy-NO by price bucket, random same-price side, and market-implied outcome.
3. Turn `all-YES underround` from `confirmed_offline` into a shadow/paper design only after order construction, all-leg liquidity, max cost, and monitoring are specified.
4. Promote only `confirmed`, `confirmed_offline`, or `shadow_candidate` labels; otherwise leave as `inconclusive`.
