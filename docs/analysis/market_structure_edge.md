# Market Structure Edge

> Living doc for module [2]: whether weather markets contain model-free structural edge such as favorite-longshot bias, side base-rate, or price-bucket mispricing.
> Current status: `mixed`: broad structure inconclusive; all-YES underround is offline-confirmed but retail-live blocked; forecast-bounded Range RV remains below live standard after orderbook-native hardening and is limited to zero-notional shadow telemetry.
> Last updated: 2026-06-25 Range RV timing/stability v2 shadow optimization.

## Current Conclusion

There is not yet enough evidence to promote a broad model-free market-structure rule into live trading. The first H_B structural-edge pass is useful because it separates market structure from model alpha, but it remains a research signal until it passes date-forward validation and multiple-test controls.

`all-YES underround` / no-arb basket tests are offline-confirmed but should not be treated as the leading retail live path. The 2026-06-15 basket fact refresh now provides the canonical denominator: one row per `strategy_id + snapshot_ts_utc + event_date + city + event_slug`, with leg rows keyed by `basket_id + condition_id`, and settlement resolved first by `settlements.condition_id` then by DB `settlement_outcomes` at `city + target_date + bracket` source grain. On 1,338 orderbook snapshots from 2026-05-19 through 2026-06-16, the 0.02 underround rule produced 297 strategy-candidate observations, 270 settled exactly-one-winner observations, and +3.16% settled unit ROI. The retail execution gap remains too large for the current goal: median per-leg buffer is only about 0.30 cents, no candidate survives +1 cent per leg extra cost, every YES leg must fill at the observed ask, partial fills create directional exposure, and the low-latency all-leg-or-none executor would be competing on speed and queue quality. Keep this family as market-structure evidence and an engineering sandbox, not as a tiny-live candidate for a small account.

The narrower forecast-bounded Range RV branch has now been rerun with the source-aware forecast-quality base and then hardened with orderbook-native entry selection. The closest generic candidate is `forecast_bounded_w3_cheaper`, `default_wu/no_filter`, orderbook edge >= 0.02: it passes the basic three statistical gates in the replay, but still fails live-standard support and robustness because train active dates are only 8, train top5-removed ROI is negative, and some holdout rows do not show 5 shares at top ask on every leg. Forward shadow now shows that `first_trigger` is structurally wrong, while `latest_before_local_18` is the cleanest current timing policy: one row per `city + event_date + forecast_source + model_version`, latest valid signal no later than local 18:59, with 165 settled groups / 11 event dates / +9.2% shadow ROI and date-block CI about +2.1% to +16.1%. This is still shadow-only because there is no live fill evidence, no executed basket capacity proof, and the baseline/live-standard gates are not complete. METAR/regime labels from the intraday atlas should be treated as explanatory overlays only; stability and mid-cost filters reduced support and did not improve the forward sample.

Most other Range RV variants remain `inconclusive`: adjacent2/3 forecast-first, market-shape anomalies, center/shoulder/butterfly, tail-fade/uncertainty, temporal reversion, regime-conditioned scanners, and walk-forward selectors did not pass the three-gate standard.

Important distinction: if BUY_NO or a price bucket works because of market structure, that is not evidence that the weather probability model is good. It belongs here, not in `model_vs_market.md`.

## Absorbed Historical Claims

1. `2026-06-08-market-structural-edge.md` tested a model-free H_B structural hypothesis; selected BUY_NO buckets had positive point estimates but failed significance and baseline gates.
2. Early Range RV scanner files (`v0`, `v0-1`, `positive-v0-2`, `variant-lab-v0-3`) are useful as search history, but their final verdicts remain `inconclusive`; they should not drive live action.
3. `noarb_all_yes_underround` became the first durable offline family: v0.9 showed the signal in proxy, `range-rv-underround-robust-v1-0.md` confirmed both proxy and executable orderbook thresholds, and `2026-06-15-all-yes-underround-basket-facts-v0.md` is now the canonical all-YES basket denominator. Current/live-prep scans and paper ledgers must be interpreted as forward evidence layers on top of that denominator, not as replacements for it. The family remains retail-live blocked unless a later executor can prove low-latency all-leg fill quality and enough TTL-valid settled forward edge.
4. Forecast-quality overlays should be treated as soft stratification. Strict forecast-quality hard filters did not reliably improve the no-filter adjacent3 baseline, and city/model samples are still thin.
5. Market-shape, temporal-reversion, regime, center/shoulder/butterfly, and tail-fade variants are negative or sample-limited evidence. Positive point estimates in those files are not enough because holdout, baseline, or top5-removed gates failed.
6. All Range RV reports are opportunity/counterfactual research unless a later shadow/paper/live run produces actual orders and fills. They must not be mixed with live_real PnL or CLOB account reconciliation.
7. Source-aware Range RV consumers must keep the decision grain at `city + event_date + forecast_source/model_version + decision_snapshot_ts_utc`; older scanner paths that omitted source/model could mix ECMWF/GFS rows and should be treated as pre-fix evidence.

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
| `docs/analysis/2026-06/2026-06-15-all-yes-underround-basket-facts-v0.md` | 2026-05-19 to 2026-06-16 orderbook snapshots | Canonical basket-grain denominator: 101,530 basket rows, 827,189 leg rows, 297 all-YES candidates at 0.02 underround, 270 settled exactly-one-winner, +3.16% settled unit ROI; settlement now resolves via `settlements.condition_id` then DB `settlement_outcomes` source grain | current-denominator |
| `docs/analysis/2026-06/2026-06-14-all-yes-underround-live-prep-v0.md` | 2026-06 current live-prep scan | 02:30 Beijing orderbook had 2 equal-share 5-share paper/shadow candidates, led by Busan and MexicoCity; full 03:00 snapshot has Denver +2.8% underround but local fresh runner skipped it as `snapshot_too_old` (age 1274.191s > 180s) and paper guard rejected it; local all-leg paper executor recorded 2 baskets / 20 leg orders but TTL audit marks both observation-only (`ttl_equivalent_baskets=0`, max record age 1353.209s > 180s); gate is not ready because live-equivalent forward sample is 0 and signed live execution is missing | paper-shadow-engineering |
| `docs/analysis/2026-06/2026-06-14-all-yes-underround-persistence-v0.md` | 2026-06 snapshot persistence scan | 7 snapshots scanned; 3 had guard-passing candidates; Busan persisted across 2 snapshots, MexicoCity across 1, Denver across latest 03:00; opportunity is real but sparse, so live executor must be low-latency and stale baskets must fail closed | paper-shadow-engineering |
| `docs/analysis/2026-06/2026-06-14-all-yes-underround-low-latency-paper-design.md` | 2026-06 low-latency paper design | fresh paper loop and N100 snapshot-source deployment shape; no orders placed; live review requires >=20 TTL-valid settled baskets, positive ROI/rate, no settlement anomaly, and separate signed executor review | design-draft |
| `docs/analysis/2026-06/2026-06-15-retail-live-strategy-direction-v0.md` | 2026-06 goal consolidation | Demotes all-YES from retail live path despite offline confirmation; freezes next branch as forecast-bounded Range RV shadow with compact 2-4 leg baskets and strict orderbook/forward gates | current-handoff |
| `docs/analysis/2026-06/2026-06-15-forecast-bounded-range-rv-source-aware-v0.md` | 2026-06 source-aware forecast-bounded Range RV | Reuses forecast-quality/source base at source/model decision-set grain; proxy default-WU width-3 looks positive but generic orderbook gates fail, so verdict remains inconclusive/no live action | active-evidence |
| `docs/analysis/2026-06/2026-06-15-forecast-bounded-range-rv-live-standard-v1.md` | 2026-06 orderbook-native live-standard hardening | Uses time-aligned orderbook costs for the entry decision itself; closest default-WU width-3 cheaper rule passes basic replay gates but fails live-standard support, top5, and 5-share capacity checks | active-evidence |
| `docs/analysis/2026-06/2026-06-16-range-rv-shadow-handoff-v0.md` | 2026-06 Range RV shadow handoff | Single entrypoint for current forecast-bounded Range RV shadow: strategy id, file inventory, runtime data paths, commands, gates, and do-not-do rules | current-handoff |
| `docs/analysis/2026-06/2026-06-16-range-rv-shadow-status-v0.md` | 2026-06 Range RV shadow status | Evaluates N100 zero-notional shadow journal against pm_history; current settled evidence remains only Miami 2026-06-14, so verdict is keep collecting shadow data | shadow-telemetry |
| `docs/analysis/2026-06/2026-06-17-range-rv-forward-diagnostic-v0.md` | 2026-06 first forward drawdown diagnostic | Compares historical replay vs settled forward shadow; finds outside-NO mix, very-low-cost rows, and hit-rate/payoff asymmetry as failure modes, but does not prove a live fix | active-evidence |
| `docs/analysis/2026-06/2026-06-17-range-rv-shadow-native-ablation-v1.md` | 2026-06 shadow-native fixed ablation | Tests current/inside/cost-band/persistence variants on historical train/holdout and forward shadow; simple expression and persistence fixes remain negative forward, so verdict stays shadow-only | active-evidence |
| `docs/analysis/2026-06/2026-06-25-range-rv-timing-stability-v2.md` | 2026-06 Range RV timing/stability optimization | Tests first/latest, local-hour cutoffs, lead-time cutoffs, range stability, cost/model-mass calibration, and intraday-regime overlays; current shadow candidate is `latest_before_local_18`, not live-ready | active-evidence |
| `scripts/ops/range_rv_shadow_v0.py` | 2026-06 N100 zero-notional telemetry | Reads latest weather-predict snapshot at `city + event_date + forecast_source/model_version + decision_snapshot_ts_utc`, keeps only `default_wu`, and appends selected `forecast_bounded_w3_cheaper` shadow plans without submitting orders; schema v2 rows add counterfactual inside-YES edge and cost-band flags for later ablations | shadow-telemetry |
| `scripts/analysis/market_structure_edge/evaluate_range_rv_shadow_v0.py` | 2026-06 Range RV shadow evaluator | Durable evaluator for synced shadow journal + pm_history; reports raw rows, dedup latest/first, event-date funnel, and settled city/expression splits | active-tool |
| `scripts/analysis/market_structure_edge/research_range_rv_shadow_native_ablation_v1.py` | 2026-06 Range RV shadow ablation tool | Durable script for first/latest/persistence, cost-band, current-cheaper, and inside-YES counterfactual comparisons using the source-aware default-WU grain | active-tool |
| `scripts/analysis/market_structure_edge/research_range_rv_timing_stability_v2.py` | 2026-06 Range RV timing/stability tool | Durable script that evaluates local-hour cutoffs, minimum lead-time policies, stability gates, calibration buckets, and regime overlays from zero-notional shadow telemetry | active-tool |
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
3. Keep `all-YES underround` as research/engineering sandbox unless a later run proves low-latency all-leg retail execution; all future all-YES research should read the basket fact output or extend its builder instead of creating a new denominator.
4. Promote only `confirmed`, `confirmed_offline`, or `shadow_candidate` labels; otherwise leave as `inconclusive`.
5. If Range RV continues, do not broaden threshold search or fit city-specific filters. Keep the default-WU shadow runner on and track `latest_before_local_18` as the current timing policy. Do not promote to paper/live until the rule has more settled forward dates, explicit baseline/excess checks, and executable capacity proof.
