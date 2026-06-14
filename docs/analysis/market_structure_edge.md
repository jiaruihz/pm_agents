# Market Structure Edge

> Living doc for module [2]: whether weather markets contain model-free structural edge such as favorite-longshot bias, side base-rate, or price-bucket mispricing.
> Current status: `mixed`: broad structure inconclusive; all-YES underround is the leading paper/shadow engineering candidate but not live-approved.
> Last updated: 2026-06-10 Phase 4D absorption.

## Current Conclusion

There is not yet enough evidence to promote a broad model-free market-structure rule into live trading. The first H_B structural-edge pass is useful because it separates market structure from model alpha, but it remains a research signal until it passes date-forward validation and multiple-test controls.

The exception is narrower: `all-YES underround` / no-arb basket tests are now the strongest market-structure family. The 2026-06-09 robust run passed proxy and time-aligned executable orderbook gates across several thresholds. The 2026-06-14 live-prep scanner found two full-basket paper candidates at 02:30 Beijing and a Denver candidate at 03:00 Beijing. The persistence scan found candidates in 3 of 7 snapshots: Busan persisted for two snapshots while MexicoCity and Denver each appeared once, showing the opportunity is real but flickery. A local all-leg-or-none paper executor records baskets, evaluates settlement by `settlements.condition_id`, monitors the current scanner state, and shares a tested guard with the future live path. The guard now fails closed on stale baskets older than 180 seconds; the first two recorded paper baskets were recorded 1353.209 seconds after their snapshot, and the later Denver candidate was rejected with `snapshot_too_old`, so current observations do not count as live-equivalent forward paper evidence. A low-latency fresh paper loop is now specified for the snapshot source host but not deployed. That makes it a `paper_shadow_engineering_candidate`, not a live rule. Before live use it still needs fresh live-equivalent forward paper evidence from the snapshot capture side or N100 same-host loop, plus signed live order construction, partial-fill cancellation/unwind orchestration, per-basket notional caps, settlement consistency checks, and monitoring.

Most other Range RV variants remain `inconclusive`: adjacent2/3 forecast-first, market-shape anomalies, center/shoulder/butterfly, tail-fade/uncertainty, temporal reversion, regime-conditioned scanners, and walk-forward selectors did not pass the three-gate standard.

Important distinction: if BUY_NO or a price bucket works because of market structure, that is not evidence that the weather probability model is good. It belongs here, not in `model_vs_market.md`.

## Absorbed Historical Claims

1. `2026-06-08-market-structural-edge.md` tested a model-free H_B structural hypothesis; selected BUY_NO buckets had positive point estimates but failed significance and baseline gates.
2. Early Range RV scanner files (`v0`, `v0-1`, `positive-v0-2`, `variant-lab-v0-3`) are useful as search history, but their final verdicts remain `inconclusive`; they should not drive live action.
3. `noarb_all_yes_underround` became the first durable family: v0.9 showed the signal in proxy, `range-rv-underround-robust-v1-0.md` confirmed both proxy and executable orderbook thresholds, and `all-yes-underround-live-prep-v0.md` now scans current orderbook liquidity for equal-share baskets plus records all-leg paper fills through `all_yes_underround_guards.py`.
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
| `docs/analysis/2026-06/2026-06-14-all-yes-underround-live-prep-v0.md` | 2026-06 current live-prep scan | 02:30 Beijing orderbook had 2 equal-share 5-share paper/shadow candidates, led by Busan and MexicoCity; full 03:00 snapshot has Denver +2.8% underround but local fresh runner skipped it as `snapshot_too_old` (age 1274.191s > 180s) and paper guard rejected it; local all-leg paper executor recorded 2 baskets / 20 leg orders but TTL audit marks both observation-only (`ttl_equivalent_baskets=0`, max record age 1353.209s > 180s); gate is not ready because live-equivalent forward sample is 0 and signed live execution is missing | paper-shadow-engineering |
| `docs/analysis/2026-06/2026-06-14-all-yes-underround-persistence-v0.md` | 2026-06 snapshot persistence scan | 7 snapshots scanned; 3 had guard-passing candidates; Busan persisted across 2 snapshots, MexicoCity across 1, Denver across latest 03:00; opportunity is real but sparse, so live executor must be low-latency and stale baskets must fail closed | paper-shadow-engineering |
| `docs/analysis/2026-06/2026-06-14-all-yes-underround-low-latency-paper-design.md` | 2026-06 low-latency paper design | fresh paper loop and N100 snapshot-source deployment shape; no orders placed; live review requires >=20 TTL-valid settled baskets, positive ROI/rate, no settlement anomaly, and separate signed executor review | design-draft |
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
3. Deploy-review `all-YES underround` fresh paper loop on the snapshot source host; count only TTL-valid baskets toward live readiness.
4. Promote only `confirmed`, `confirmed_offline`, or `shadow_candidate` labels; otherwise leave as `inconclusive`.
