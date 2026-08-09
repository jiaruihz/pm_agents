# Market Structure Edge

> Living doc for module [2]: whether weather markets contain model-free structural edge such as favorite-longshot bias, side base-rate, or price-bucket mispricing.
> Current status: `mixed`: broad structure inconclusive; all-YES underround is offline-confirmed but retail-live blocked; forecast-bounded Range RV remains below live standard after orderbook-native hardening and is limited to zero-notional shadow telemetry; forecast/update-time "hourly repricing" has only shadow-research evidence; METAR C->F and AMOS half-degree boundary traps are real source-basis risks; five-city full-ladder microstructure supports city/time-aware execution routing but not a maker EV claim; exact-ladder kink is `BRANCH_EXHAUSTED`; time-aligned ladder mass transport passes the probability gate but is `REJECTED_FOR_EXPRESSION`.
> Last updated: 2026-08-09 ladder mass transport time-aligned replay.

## Current Conclusion

There is not yet enough evidence to promote a broad model-free market-structure rule into live trading. The first H_B structural-edge pass is useful because it separates market structure from model alpha, but it remains a research signal until it passes date-forward validation and multiple-test controls.

`market_ladder_kink_v1` is `BRANCH_EXHAUSTED` for the two registered expressions, without changing its frozen broad-v1 result. First, the side-aware settlement baseline adds market probability, signed mode distance, cold/hot side, lifecycle and native lattice; its candidate adds only continuous `kink × cold_distance`. On the same 41,124 OOF rungs / 4,135 snapshots / 9 target dates / 47 cities, candidate-minus-baseline Brier is `+0.000060` CI `[-0.000092,+0.000249]`, logloss `+0.000089` CI `[-0.000243,+0.000471]`, and AUC declines `0.87759→0.87756`: failure is probability/baseline. Second, the dynamic head finds a descriptive convergence signal at 30m and 60m—Brier deltas `-0.001795` CI `[-0.002598,-0.000993]` and `-0.003979` CI `[-0.004802,-0.003255]`—but current direct ask entry, future direct bid exit, size≥1 and official fee on both legs loses `-14.48%` CI `[-15.72%,-13.82%]` and `-18.01%` CI `[-19.15%,-16.88%]`; all 9 dates lose at both horizons. The 15m layer has zero target-plus-neighbors direct-book exits under the fixed canonical capture contract, so it fails execution coverage rather than being silently dropped. No selector, `SignalCandidate`, `TradeIntent`, runner or shadow instance was deployed. The most valuable next mechanism is queue-aware passive entry on the proven 30/60m convergence signal, but it requires real trade prints, queue position and order lifecycle; future touch remains insufficient.

`ladder_mass_transport_v1` was corrected after the old July-only denominator was found invalid. Historical training now uses 2026-05-19..07-10 (874,281 fixed rungs / 53 dates), development uses all 2026-07-11..21 (15,729 rungs / 11 dates), and the one-open frozen historical validation remains 2026-07-22..28 (23,887 rungs / 3,174 snapshots / 7 dates / 47 cities). Development freezes `M2 ladder transition`, Ridge alpha 100. Its primary 60m relative-markout MSE is `0.00115401`; paired deltas are `-0.00003554` vs M0, `-0.00000772` vs M1, and `-0.00003470` vs static kink, with all target-date CIs below zero. Direction and settlement proper scores also improve. The sign is stable on 7/7 dates, 47/47 cities, every leave-one-city-out sensitivity, and all nine spread×depth regimes. This confirms a ladder-state-transition residual, not a weather-event-response-lag result: M3 was not selected and 893,321 / 913,897 fixed rows lack intraday observation.

The fixed 60m pair expression is nevertheless `REJECTED_FOR_EXPRESSION`. A positive max-minus-min M2 prediction buys one YES share on the max rung and one NO share on the min rung, first non-overlapping signal per city-date-event. On 1,872 all-leg one-share-depth rows, taker entry+taker exit loses `$53.1412`, ROI `-2.8035%`, target-date CI `[-$81.5042,-$29.3361]`, with 7/7 dates and 46/47 cities losing. Maker-entry+taker-exit is only `+$5.4733`, CI crossing zero, and needs median 105.7% break-even fill; double-maker is `+$56.5550` only under unknown two-sided fills and turns negative at a 1c adverse-selection penalty per maker leg. Future touch is not a fill, M4 queue/trade evidence is absent, and generic ladder-maker is also negative under maker-entry+taker-exit. No selector runtime, `SignalCandidate`, `TradeIntent`, shadow, or live instance was created. Artifact: `/Volumes/jrs-archive/pm_agents/research/artifact_store/market_structure_edge/ladder_mass_transport_time_aligned_20260809`; frozen report: `docs/analysis/2026-08/2026-08-09-ladder-mass-transport-time-aligned-replay-v1.md`.

`all-YES underround` / no-arb basket tests are offline-confirmed but should not be treated as the leading retail live path. The 2026-06-15 basket fact refresh now provides the canonical denominator: one row per `strategy_id + snapshot_ts_utc + event_date + city + event_slug`, with leg rows keyed by `basket_id + condition_id`, and settlement resolved first by `settlements.condition_id` then by DB `settlement_outcomes` at `city + target_date + bracket` source grain. On 1,338 orderbook snapshots from 2026-05-19 through 2026-06-16, the 0.02 underround rule produced 297 strategy-candidate observations, 270 settled exactly-one-winner observations, and +3.16% settled unit ROI. The retail execution gap remains too large for the current goal: median per-leg buffer is only about 0.30 cents, no candidate survives +1 cent per leg extra cost, every YES leg must fill at the observed ask, partial fills create directional exposure, and the low-latency all-leg-or-none executor would be competing on speed and queue quality. Keep this family as market-structure evidence and an engineering sandbox, not as a tiny-live candidate for a small account.

The narrower forecast-bounded Range RV branch has now been rerun with the source-aware forecast-quality base and then hardened with orderbook-native entry selection. The closest generic candidate is `forecast_bounded_w3_cheaper`, `default_wu/no_filter`, orderbook edge >= 0.02: it passes the basic three statistical gates in the replay, but still fails live-standard support and robustness because train active dates are only 8, train top5-removed ROI is negative, and some holdout rows do not show 5 shares at top ask on every leg.

The 2026-06-26 forecast-update cadence audit corrects the previous timing framing: `local hour` is not a forecast information state. Range RV timing must be analyzed at `city + event_date + forecast_source + model_init/run_age/hash + snapshot_ts_utc`. The earlier `latest_before_local_18` result should be treated only as a coarse diagnostic, not as the candidate label. Re-reading the shadow journal against reconstructed snapshot metadata shows `prev_day_latest` at 159 settled rows / 11 dates / +2.6% ROI and `d0_morning_latest` at 69 settled rows / 6 dates / +7.9% ROI. This keeps D-1 Range RV weak and shadow-only, while opening a separate `forecast-update Range RV` research branch for D0 morning. That branch still needs measured state-arrival/repricing lag, forward support, baselines, and executable capacity proof before paper/live consideration. METAR/regime labels from the intraday atlas should be treated as explanatory overlays or sizing inputs only; they are not a substitute for forecast-state lineage.

The 2026-06-26 forecast/update-time repricing v0 pass creates a separate "hourly trade" research branch from N100 source/book timing logs. In the 2026-06-24 onward sample for Busan, BuenosAires, Manila, Singapore, Shanghai, Tokyo, Chicago, Miami, Austin, Denver, LA, and Philadelphia, 41.5% of book-change rows landed within local :00/:30 +/-5m. The stronger evidence is observation/update-window driven, not a clean forecast-hash trigger: observation-linked large reaction rows were 142 versus 19 forecast/hash-linked rows, and forecast events were reconstructed from 30-minute paper snapshots rather than native first-seen cadence. Keep this branch zero-notional shadow/research only; next proof needs `forecast_state_first_seen_utc`, real NO ask/depth, same-price baselines, and forward settlement.

The 2026-06-26 METAR C->F boundary-trap pass isolates a distinct source-basis microstructure failure mode. In N100 raw timing logs from 2026-06-24 onward, 81 F-market crossing events yielded 59 raw-METAR matched events, 22 main-vs-RMK source-basis risk events, and 5 true boundary mismatches where the METAR main integer-C value crossed a whole-F bracket but the RMK `Txxxx` tenth-C value did not. SanFrancisco 2026-06-25 `68-69°F` is the clearest trap: 5-minute MADIS/HFMETAR rows around 19:30-19:55 and 20:00-20:25 carried `T02100130` / 21.0C, which looks like 70F under naive whole-F rounding, while the 19:56 routine METAR carried `T02060128` / 20.6C and WU historical hourly reports 69F. The market first moved toward NO, then repriced back toward YES around 20:13Z. This proves the old crossed-NO live logic is unsafe on F/WU cities; the research branch is now whether 5-minute-vs-routine/WU disagreements create a shadow-only reverse YES setup.

Most other Range RV variants remain `inconclusive`: adjacent2/3 forecast-first, market-shape anomalies, center/shoulder/butterfly, tail-fade/uncertainty, temporal reversion, regime-conditioned scanners, and walk-forward selectors did not pass the three-gate standard.

New 2026-06-25 research branch: `post_cross_repricing` studies how nearby
temperature brackets reprice after a fresh running-max crossing. This is
market-structure/probability redistribution, not the latency bot's crossed-NO
free-money claim. Initial N100 timing logs show crossed brackets are repriced
fastest, while current/new-high and tail brackets move unevenly. Keep it
`research_only` until it has real-book baselines, settlement labels, and
forward-date validation. Living entry: `docs/analysis/post_cross_repricing.md`;
tool: `scripts/analysis/market_structure_edge/research_post_cross_repricing_v0.py`.

The 2026-07-21 scheduled-report liquidity study validates a separate execution
overlay. Across 41 cities / 13 target dates, active exact-bracket books widened
by 0.237 cents in the eight minutes before nominal observation report time
versus the preceding window (date-block 95% CI +0.110 to +0.373 cents), with
lower top depth and a small decline in two-sided quoting; spreads then narrowed
by 0.522 cents in minutes +8 to +15. Ankara's :20/:50 cadence shows the same
shape, especially depth withdrawal, but its city-only spread CI crosses zero.
The trade expression is not taker entry: fee-adjusted taker markout was -1.56
cents/share, while bid+one-tick maker markout was only a +4.05 cents/share
no-fill upper bound. Keep this as `execution_shadow_candidate`; collect trade
tape and queue-ahead against frozen existing direction signals before any live
claim.

The 2026-07-30 full-ladder microstructure atlas and five-city deep dive now
provide the canonical descriptive execution layer. The broad atlas standardizes
47,508 `event × archived snapshot` states across 47 cities; the focused Tokyo,
Busan, Seoul, Amsterdam, and Helsinki replay uses 1,024 target-day 06-18
complete-ladder states after excluding 324 incomplete snapshots. Asian active
repricing concentrates around local 10-14, while Amsterdam/Helsinki shift later
to 12-16. Visible maker price improvement is about 1.9-2.9 cents, but quotes
whose next archived ask moves through the hypothetical maker price have
conditional median markout of -3.1 to -7.6 cents; this is a toxicity proxy, not
a fill-rate estimate. Complete-ladder static all-YES underround appears in only
1.4%-2.9% of snapshots and remains non-atomic.

The same replay adds an important source-basis negative control. Busan
2026-07-27 reached AMOS 35.7C while the market ultimately concentrated on the
35 bracket; Seoul 2026-07-29 similarly showed AMOS 30.5C versus the market's 30
bracket. A fast-source-implied feasible strip below 1 is therefore not an
arbitrage label unless the source has been proven equivalent to settlement.
Use AMOS/JMA/FMI/KNMI as path and cancel/reprice evidence; do not delete legs
from their raw rounded maximum alone.

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
| `docs/analysis/post_cross_repricing.md` | 2026-06 METAR-cross microstructure fork | Defines the new post-cross repricing research task: crossed bracket collapse vs current/new-high and tail bracket probability redistribution after a fresh running-max update | current-reference |
| `docs/analysis/2026-06/2026-06-26-forecast-update-time-repricing-v0.md` | 2026-06-24 onward N100 source/book timing + paper snapshots | First forecast/update-time "hourly trade" denominator: 154,518 book rows, 1,373 observation events, 421 reconstructed forecast-state events; book changes cluster around local :00/:30, observation/update-window evidence dominates forecast/hash evidence; shadow/research only | snapshot |
| `docs/analysis/2026-06/2026-06-26-metar-cf-boundary-trap-v0.md` | 2026-06-24 onward N100 METAR/source/book timing + WU/IEM follow-up | METAR main integer-C vs RMK tenth-C boundary mismatch research: 5 true F-market boundary events, led by SFO 68-69F where 5-minute MADIS/HFMETAR looked like 70F but routine METAR/WU hourly stayed 69F and market later repriced to YES; research-only source-basis branch | snapshot |
| `docs/analysis/2026-07/2026-07-21-scheduled-report-liquidity-gap-v1.md` | 2026-06 dense timing journal + 2026-07 JRS sanity | Nominal report windows show statistically significant pre-report spread widening/depth withdrawal and post-report recovery; taker markout is negative, passive maker remains a no-fill upper bound pending queue/trade-tape forward | execution-shadow-candidate |
| `docs/analysis/2026-07/2026-07-30-weather-book-microstructure-atlas-v1.md` | 2026-07-15..29, 47 cities / 17 target dates | Canonical event-snapshot spread/depth/favorite-repricing atlas; warming/plateau is wider, thinner, and faster, but market-wide trades and queue are absent | current-execution-reference |
| `docs/analysis/2026-07/2026-07-30-five-city-weather-microstructure-v1.md` | 2026-07-15..29, Tokyo/Busan/Seoul/Amsterdam/Helsinki | Complete-ladder city/time routing, maker quote-cross toxicity, rare static underround, and AMOS settlement-basis negative controls; no live change | current-execution-reference |
| `docs/analysis/2026-08/2026-08-09-market-ladder-kink-exact-bracket-mispricing-v1.md` + `scripts/analysis/market_structure_edge/market_ladder_kink_challengers.py` | 2026-07-11..28 fixed same-snapshot ladders; 9 OOF target dates / 47 cities | Broad v1 stays frozen; side-aware settlement residual fails baseline, while 30/60m convergence proper score passes but taker ask→bid execution loses significantly; 15m direct three-rung exit coverage is zero | current-evidence / BRANCH_EXHAUSTED / no-deploy |
| `docs/analysis/2026-08/2026-08-09-ladder-mass-transport-time-aligned-replay-v1.md` + `weather_model_evaluation/ladder_mass_transport.py` | train 2026-05-19..07-10; development 07-11..21; frozen historical validation 07-22..28 | M2 ladder transition beats M0/M1/static kink on 60m proper score across 7 dates/47 cities, but taker pair ROI is -2.80% with CI below zero; maker-only upside lacks fill evidence | current-evidence / REJECTED_FOR_EXPRESSION / no-shadow |
| `docs/analysis/2026-06/2026-06-15-forecast-bounded-range-rv-source-aware-v0.md` | 2026-06 source-aware forecast-bounded Range RV | Reuses forecast-quality/source base at source/model decision-set grain; proxy default-WU width-3 looks positive but generic orderbook gates fail, so verdict remains inconclusive/no live action | active-evidence |
| `docs/analysis/2026-06/2026-06-15-forecast-bounded-range-rv-live-standard-v1.md` | 2026-06 orderbook-native live-standard hardening | Uses time-aligned orderbook costs for the entry decision itself; closest default-WU width-3 cheaper rule passes basic replay gates but fails live-standard support, top5, and 5-share capacity checks | active-evidence |
| `docs/analysis/2026-06/2026-06-16-range-rv-shadow-status-v0.md` | 2026-06 Range RV shadow status | Evaluates N100 zero-notional shadow journal against pm_history; current settled evidence remains only Miami 2026-06-14, so verdict is keep collecting shadow data | shadow-telemetry |
| `docs/analysis/2026-06/2026-06-17-range-rv-forward-diagnostic-v0.md` | 2026-06 first forward drawdown diagnostic | Compares historical replay vs settled forward shadow; finds outside-NO mix, very-low-cost rows, and hit-rate/payoff asymmetry as failure modes, but does not prove a live fix | active-evidence |
| `docs/analysis/2026-06/2026-06-17-range-rv-shadow-native-ablation-v1.md` | 2026-06 shadow-native fixed ablation | Tests current/inside/cost-band/persistence variants on historical train/holdout and forward shadow; simple expression and persistence fixes remain negative forward, so verdict stays shadow-only | active-evidence |
| `docs/analysis/2026-06/2026-06-25-range-rv-timing-stability-v2.md` | 2026-06 Range RV timing/stability optimization | Tests first/latest, local-hour cutoffs, lead-time cutoffs, range stability, cost/model-mass calibration, and intraday-regime overlays; current shadow candidate is `latest_before_local_18`, not live-ready | active-evidence |
| `docs/analysis/2026-06/2026-06-26-range-rv-forecast-update-cadence-v1.md` | 2026-05-12 to 2026-06-25 paper snapshots + Range RV shadow | Reconstructs city/source forecast-state cadence and corrects Range RV timing research from local-hour labels to `model_init/run_age/hash/state_age`; D-1 remains weak (+2.6% ROI), D0 morning is a separate forecast-update branch (+7.9% ROI on 6 settled dates), diagnostic only | current-evidence |
| `scripts/ops/range_rv_shadow_v0.py` | 2026-06 N100 zero-notional telemetry | Reads latest weather-predict snapshot at `city + event_date + forecast_source/model_version + decision_snapshot_ts_utc`, keeps only `default_wu`, and appends selected `forecast_bounded_w3_cheaper` shadow plans without submitting orders; schema v2 rows add counterfactual inside-YES edge and cost-band flags, and current rows persist forecast-state lineage fields for later forecast-update ablations | shadow-telemetry |
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
5. If Range RV continues, do not broaden threshold search or fit city-specific filters. Keep the default-WU shadow runner on, but evaluate timing with forecast-state fields (`model_init`, run age, hash change, first-seen age) instead of `latest_before_local_18`. Do not promote to paper/live until the rule has more settled forward dates, explicit baseline/excess checks, measured repricing-lag evidence, and executable capacity proof.
6. For maker/taker/skip research, collect market-wide trade prints and complete
   order lifecycle on the same `signal_id × target shares` denominator. Future
   quote touch/cross must not be counted as a maker fill, and incomplete baskets
   must remain in the denominator.
7. `market_ladder_kink_v1` settlement and taker-repricing expressions are exhausted; do not search city/price/spread/depth thresholds and do not deploy a strategy runner. If this family resumes, the only justified fork is queue-aware passive convergence capture with real prints, queue position and complete order lifecycle—not future-touch maker replay.
