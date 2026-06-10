# Weather Markdown Consolidation Plan

Status: current-reference
Updated: 2026-06-10 Phase 4D side/city/M3 absorption
Source of truth: no
Superseded by / Used by: WEATHER_DOCS_INDEX.md; docs/analysis living docs

This is the Phase 4A/4B plan for Markdown cleanup. It does not move files. It records the first content audit so later archive work is based on claims, data quality, and overlap rather than filename/date alone.

## Scope

Reviewed set:

- `docs/analysis/2026-05/*.md`
- `docs/analysis/2026-06/*.md`
- Existing living docs under `docs/analysis/*.md`

Not done in this phase:

- No Markdown files moved.
- No JSON files moved or removed.
- No historical report bodies rewritten.
- No live strategy behavior changed.

## Action Labels

| Label | Meaning | Next step |
|---|---|---|
| `active-evidence` | Still useful as evidence if cited with its data window and caveats | Extract into owner living doc before archive |
| `superseded-evidence` | Directional or historical value, but newer report has better data/contract | Keep until newer report is summarized, then archive |
| `invalidated` | A key numeric conclusion is known wrong or unsafe due to near-binary, fill recovery, stale DB, old attribution, or bad denominator | Do not cite for current decisions except as failure history |
| `duplicate` | Same or near-same conclusion as a newer/current report | Collapse into newest owner doc; archive duplicate after link check |
| `design-plan` | Plan/handoff/research plan, not a factual result | Keep only if it still guides execution; otherwise archive after plan is absorbed |
| `non-weather` | Copy-trade, OpenAI hardware, or other non-weather research | Move to separate non-weather/copy-trade area in a later phase |

## Phase 4B Findings

1. **May reports are mostly historical.** They include useful early lineage and design thinking, but most numeric conclusions are stale because current facts changed after near-binary settlement fixes, CLOB fill recovery, strategy-instance attribution fixes, and later June reruns.
2. **June 3-6 reports split into two groups.** Reports that already carry near-binary/fill caveats can stay as failure-history evidence; reports with `missing_bracket`-dependent PnL, city rank, win-rate, or strategy rank should be treated as `invalidated`.
3. **June 7-9 reports are the main active evidence pool.** They generally use CLOB coverage gates, rebuilt facts, and explicit verdict labels. Many still end at `inconclusive` or `shadow_candidate`, so they are evidence, not live action.
4. **There is heavy duplication in city-day basket, live-period slices, blender, and range-RV reports.** The archive unit should be the older duplicate after its useful claim is merged into the living doc.
5. **Non-weather research is now mixed into weather analysis.** `copy_trade_*` and OpenAI hardware branch research should leave weather analysis in a later phase.

## Owner Map

| Owner living doc | Pulls from |
|---|---|
| `model_vs_market.md` | probability calibration, raw degradation, calibration drift, forecast timing lineage, city model downgrade, model rank IC |
| `market_structure_edge.md` | market structural edge, range-RV scanner, range-RV positive profiles |
| `execution_quality.md` | executable edge, fill recovery, maker-queue comparisons, candidates-vs-fills, execution window filters |
| `entry_timing.md` | entry timing edge, strict vs wide window, city x entry timing, timing baseline, timing plan |
| `side_alpha.md` | side-band entry analysis, side-band alpha summary, side-band timing impact |
| `city_selection.md` | city pool contribution, city pool side strategy, city-day basket family, city/model conditional edge |
| `observed_max_m3.md` | M3 observed running max handoff, plan, residual physical-layer experiment |
| `sizing_entry_band.md` | entry band research, sizing and band distribution |
| `blender_shadow.md` | blender backtests, overlays, edge v2 shadow, filtered operational base |
| `live_performance.md` | live full research, live period slices, PnL curves, recent loss attribution, three strategy instances |
| `account_reconcile.md` | account equity replay, UI account loss reconciliation, live account reconcile near-binary fix |
| `data_integrity.md` | signal side flip, decision-window backfill, candidates-vs-fills linkage |

## Detailed Audit Table

### 2026-05

| File | Owner | Audit status | Read finding | Next action |
|---|---|---|---|---|
| `2026-05-26-compare-UNKNOWN-vs-mid_price_core_v1.md` | `execution_quality.md` | `superseded-evidence` | Early UNKNOWN vs mid comparison; warns non-overlapping windows and has `missing_bracket` exposure. | Extract lineage lesson only; archive after execution living doc absorbs. |
| `2026-05-27-compare-execution-algorithm-window-filter.md` | `execution_quality.md` | `superseded-evidence` | Useful explanation that entry window filter explains much of UNKNOWN delta; old paper/snapshot era. | Keep as historical mechanism evidence, not current PnL. |
| `2026-05-27-compare-mid-price-vs-maker-queue.md` | `execution_quality.md` | `superseded-evidence` | Direction says maker_queue fill loss outweighed price improvement; sample tiny and pre-current execution. | Extract maker_queue retirement rationale. |
| `2026-05-27-compare-strict-t24-vs-wide-window.md` | `entry_timing.md` | `superseded-evidence` | Early strict T24 vs wider window selector; old settlement and city pool. | Extract only timing hypothesis. |
| `2026-05-27-lineage-execution-UNKNOWN-vs-mid_price_core_v1.md` | `execution_quality.md` | `active-evidence` | Useful qualitative lineage: signal filtering, not quote formula, drove key difference. | Summarize mechanism in execution living doc before archive. |
| `2026-05-27-maker-queue-baseline-and-optimization.md` | `execution_quality.md` | `superseded-evidence` | Maker_queue baseline and cancel hypothesis; strategy is retired. | Move to retired/maker-queue archive later. |
| `2026-05-27-maker-queue-cancel-backtest.md` | `execution_quality.md` | `superseded-evidence` | Cancel-after-4h direction but tiny sample; retired strategy. | Archive with maker_queue group after extracting caution. |
| `2026-05-27-performance-city-pool-contribution.md` | `city_selection.md` | `superseded-evidence` | Old T1/T2 contribution replay with `missing_bracket` exposure. | Do not use for current city pool; compare against June city docs. |
| `2026-05-27-performance-live-full-research.md` | `live_performance.md` | `invalidated` | Report itself says old live denominator mixed `live_real` and `live_simulated`. | Keep only as failure-history example. |
| `2026-05-28-compare-orig-pool-vs-new-pool.md` | `city_selection.md` | `superseded-evidence` | Useful distinction between realized and unsettled/MTM; old city-pool comparison. | Extract accounting caution; archive after city living doc update. |
| `2026-05-28-performance-makerqueue-v3-city-pool.md` | `execution_quality.md` | `superseded-evidence` | Old maker_queue/v3 city pool live slice; includes unsettled MTM caveats. | Archive with maker_queue group. |
| `2026-05-29-entry-timing-edge.md` | `entry_timing.md` | `superseded-evidence` | Early timing edge hypothesis; old denominator and missing-bracket exposure. | Extract hypothesis only; current timing docs supersede numbers. |
| `2026-05-29-performance-candidates-vs-fills-link.md` | `data_integrity.md` | `active-evidence` | Important bridge between `fact_signal_candidates` and `fact_trades`; explicitly frames execution selection bias. | Keep as active evidence until data-integrity doc absorbs. |
| `2026-05-29-performance-city-pool-side-strategy.md` | `city_selection.md` | `superseded-evidence` | Early city x side whitelist logic; acknowledges DB changed and decision-window missingness. | Extract side whitelist design lesson, not current city actions. |
| `2026-05-29-strategy-entry-band-and-execution-quality.md` | `execution_quality.md` | `active-evidence` | Strong early A/B mechanism: mid_price beat maker_queue; entry band concepts. | Extract into execution and sizing docs. |
| `2026-05-30-performance-entry-band-research.md` | `sizing_entry_band.md` | `superseded-evidence` | Warns no latest sync; useful side x price-band framing. | Extract framework; do not cite numbers for live action. |
| `2026-05-30-performance-sizing-and-band-distribution.md` | `sizing_entry_band.md` | `active-evidence` | Useful anti-overfit sizing/band reasoning and size inconsistency observation. | Extract methodology and caveats into sizing doc. |

### 2026-06: Data / Account / Live Performance

| File | Owner | Audit status | Read finding | Next action |
|---|---|---|---|---|
| `2026-06-03-performance-three-strategy-instances.md` | `live_performance.md` | `invalidated` | Explicitly uses old settlement rule with `missing_bracket=725`; side-band mapping incomplete. | Keep as evidence for why V2 was questioned, but do not cite PnL/rank. |
| `2026-06-06-account-equity-replay.md` | `account_reconcile.md` | `superseded-evidence` | Correctly identifies DB fill recovery gap, but later account reconcile/fill recovery improved authority. | Extract failure mode; current command remains account reconcile script. |
| `2026-06-06-live-account-reconcile-near-binary-fix.md` | `account_reconcile.md` | `active-evidence` | Key near-binary and account reconciliation snapshot; current contract supersedes procedure details. | Keep until account living doc fully summarizes. |
| `2026-06-06-live-strategy-period-slice.md` | `live_performance.md` | `superseded-evidence` | Good distinction between target-date strategy PnL and UI account equity; older than fill fix. | Extract wording; newer 06-07 slices supersede numbers. |
| `2026-06-06-polymarket-ui-account-loss-reconciliation.md` | `account_reconcile.md` | `superseded-evidence` | It self-corrects public activity / UI interpretation; useful but not final authority. | Extract caveat about UI vs strategy PnL. |
| `2026-06-06-recent-live-loss-attribution.md` | `live_performance.md` | `invalidated` | Contains old `missing_bracket=734` distribution; later target loss attribution supersedes. | Do not cite current loss numbers. |
| `2026-06-06-three-strategy-instances-near-binary-reanalysis.md` | `live_performance.md` | `active-evidence` | Recomputed after near-binary; supports V2 stopped/shadow decision. | Extract current still-valid action history. |
| `2026-06-07-fill-recovery-and-performance-recalc.md` | `execution_quality.md` | `active-evidence` | Core CLOB fill recovery/gate evidence; explains row counts drift with new fills. | Keep as primary fill-recovery evidence. |
| `2026-06-07-live-pnl-15d-curve-after-history-rebuild.md` | `live_performance.md` | `duplicate` | Same title/content family as 15d curve/history rebuild. | Merge into live performance; archive duplicate later. |
| `2026-06-07-live-pnl-curve-0524-0606.md` | `live_performance.md` | `active-evidence` | PnL curve snapshot after fill recovery period. | Extract only if still needed for curve history. |
| `2026-06-07-live-strategy-period-slice-after-fill-fix.md` | `live_performance.md` | `duplicate` | Same numbers as history rebuild with smaller open-cost differences. | Prefer `current`; archive older duplicate later. |
| `2026-06-07-live-strategy-period-slice-after-history-rebuild.md` | `live_performance.md` | `duplicate` | Near-duplicate of after-fill-fix/current. | Prefer `current`; archive duplicate later. |
| `2026-06-07-live-strategy-period-slice-current.md` | `live_performance.md` | `active-evidence` | Best landing snapshot for current 06-07 period-slice wording. | Extract into live performance. |
| `2026-06-07-recent-live-loss-attribution-target-0531-0606.md` | `live_performance.md` | `active-evidence` | Newer loss attribution with `missing_bracket` fixed to settled/null. | Extract into live performance. |

### 2026-06: Model / Blender / Edge Engine

| File | Owner | Audit status | Read finding | Next action |
|---|---|---|---|---|
| `2026-06-05-probability-calibration.md` | `model_vs_market.md` | `active-evidence` | Foundational calibration snapshot. | Already referenced; keep until model doc has full summary. |
| `2026-06-05-city-day-basket-eval.md` | `blender_shadow.md` | `superseded-evidence` | Step2 gate did not pass; early blended/basket comparison. | Extract early gate failure; later 06-08 supersedes. |
| `2026-06-06-blended-entry-band-backtest.md` | `blender_shadow.md` | `invalidated` | Pre near-binary caveat; settlement rows include `missing_bracket`. | Do not cite numbers; keep only as old experiment. |
| `2026-06-06-blended-paper-fill-estimate.md` | `execution_quality.md` | `superseded-evidence` | Paper fill estimate; not live-real authority. | Keep as execution background if needed. |
| `2026-06-06-blended-single-v0-backtest.md` | `blender_shadow.md` | `invalidated` | Pre near-binary; settled/live conclusions need newer run. | Superseded by 06-07/08 blender docs. |
| `2026-06-07-blended-live-instance-overlay.md` | `blender_shadow.md` | `active-evidence` | Supports blender as secondary filter/shadow, not hard live gate. | Extract into blender living doc. |
| `2026-06-07-blended-paper-fill-estimate.md` | `execution_quality.md` | `superseded-evidence` | Newer than 06-06 paper fill estimate but still paper/shadow only. | Archive after execution doc captures limits. |
| `2026-06-07-mid-price-core-v1-city-model-downgrade.md` | `model_vs_market.md` | `active-evidence` | City x model downgrade evidence; settled live sample. | Extract to model/city docs, but no direct config without gates. |
| `2026-06-07-mid-price-core-v1-forecast-timing-degradation-lineage.md` | `model_vs_market.md` | `active-evidence` | Gate passed; explains forecast timing degradation lineage. | Extract into model and entry-timing docs. |
| `2026-06-07-mid-price-core-v1-raw-calibration-drift.md` | `model_vs_market.md` | `active-evidence` | Checks raw probability drift and code breakpoint suspicion. | Extract diagnostic conclusion. |
| `2026-06-07-mid-price-core-v1-raw-degradation.md` | `model_vs_market.md` | `active-evidence` | Gate passed; core raw/v1 degradation evidence. | Extract into model living doc. |
| `2026-06-07-v1-ecmwf-blocked-side-band-overlay.md` | `entry_timing.md` | `active-evidence` | Says blocked cities should not directly move to side-band live; shadow first. | Extract into entry/side docs. |
| `2026-06-07-v1-raw-regime-filter-walkforward.md` | `model_vs_market.md` | `active-evidence` | Walk-forward regime filter; useful but still control-variable evidence. | Extract into model doc with gate status. |
| `2026-06-08-blender-research-state-and-next-plan.md` | `blender_shadow.md` | `active-evidence` | Best narrative handoff for blender: shadow/sizing, not hard gate. | Keep as primary blender evidence until absorbed. |
| `2026-06-08-blender-signal-value-research.md` | `blender_shadow.md` | `active-evidence` | Current strict base; confirms blender not live hard gate. | Extract tables/conclusion. |
| `2026-06-08-v1-removed-ecmwf-t28-blender-overlay.md` | `blender_shadow.md` | `active-evidence` | Blender marginal value negative after removing weak cities/T28. | Extract to blender and entry timing. |
| `2026-06-08-weather-edge-v2-filtered-operational-base-research.md` | `blender_shadow.md` | `active-evidence` | Current operational-base research, many gates fail. | Keep as active but `inconclusive` evidence. |
| `2026-06-08-weather-edge-v2-shadow-lineage.md` | `blender_shadow.md` | `active-evidence` | Shadow lineage, not live performance. | Extract lineage scope into blender doc. |
| `2026-06-09-model-rank-ic.md` | `model_vs_market.md` | `active-evidence` | Explicit `verdict=inconclusive`; rank/sizing research only. | Already summarized; keep as active evidence. |

### 2026-06: City / Timing / Side / Market Structure

| File | Owner | Audit status | Read finding | Next action |
|---|---|---|---|---|
| `2026-06-03-signal-side-flip-check.md` | `data_integrity.md` | `active-evidence` | Confirms side flip can be normal snapshot drift, not immediate bug. | Extract into data integrity. |
| `2026-06-04-performance-side-band-entry-analysis.md` | `side_alpha.md` | `superseded-evidence` | It says side-band has no strict live_real under producer_run_id; later side-band alpha supersedes. | Extract caution only. |
| `2026-06-06-city-alpha-framework.md` | `city_selection.md` | `invalidated` | Explicit `missing_bracket=734` pre-fix; city/side rank must be recomputed. | Do not cite current ranks. |
| `2026-06-06-near-binary-city-reanalysis.md` | `city_selection.md` | `active-evidence` | Post near-binary city reanalysis. | Extract active city evidence. |
| `2026-06-06-city-day-basket-optimizer-research.md` | `city_selection.md` | `superseded-evidence` | Early optimizer; failed missed-profit / top5 gates. | Later 06-07/08 supersedes. |
| `2026-06-06-city-day-basket-pr2b-robustness.md` | `city_selection.md` | `superseded-evidence` | PR2b robustness; research-only. | Archive after city doc summarizes overfit risk. |
| `2026-06-06-city-day-basket-pr2b-sweep.md` | `city_selection.md` | `superseded-evidence` | Offline sweep; explicitly not production approval. | Archive after extracting warning. |
| `2026-06-06-city-day-basket-vs-legacy-baselines.md` | `city_selection.md` | `superseded-evidence` | Older basket vs baseline. | Prefer 06-08 refresh. |
| `2026-06-06-city-day-basket-walkforward.md` | `city_selection.md` | `superseded-evidence` | Older walk-forward. | Prefer 06-08 refresh. |
| `2026-06-06-city-day-distribution-quality.md` | `city_selection.md` | `superseded-evidence` | Older distribution-quality run. | Prefer 06-08 refresh. |
| `2026-06-07-basket-latest-protocol-experiment-plan.md` | `city_selection.md` | `design-plan` | Research plan with gate pass and forward criteria; no final action. | Keep until plan absorbed into city doc. |
| `2026-06-07-city-day-basket-optimizer-research.md` | `city_selection.md` | `superseded-evidence` | Newer optimizer but still fails key gates. | Prefer 06-08 state/plan plus latest refresh. |
| `2026-06-07-city-day-basket-vs-legacy-baselines.md` | `city_selection.md` | `superseded-evidence` | Intermediate refresh. | Prefer 06-08. |
| `2026-06-07-city-day-basket-walkforward.md` | `city_selection.md` | `superseded-evidence` | Intermediate refresh. | Prefer 06-08. |
| `2026-06-07-city-day-distribution-quality.md` | `city_selection.md` | `superseded-evidence` | Intermediate refresh. | Prefer 06-08. |
| `2026-06-08-city-day-basket-research-state-and-plan.md` | `city_selection.md` | `active-evidence` | Best city-day basket state handoff; says research snapshot. | Extract as city-selection anchor. |
| `2026-06-08-city-day-basket-vs-legacy-baselines.md` | `city_selection.md` | `active-evidence` | Latest baseline refresh; recent slice still not live approval. | Extract to city doc. |
| `2026-06-08-city-day-basket-walkforward.md` | `city_selection.md` | `active-evidence` | Latest walk-forward refresh. | Extract to city doc. |
| `2026-06-08-city-day-distribution-quality.md` | `city_selection.md` | `active-evidence` | Latest distribution quality. | Extract to city doc. |
| `2026-06-08-city-model-conditional-edge.md` | `city_selection.md` | `active-evidence` | `verdict=inconclusive`; no live action. | Extract as negative/holdout evidence. |
| `2026-06-08-city-x-entry-timing-research.md` | `entry_timing.md` | `active-evidence` | Gate passed; city x timing conclusions but not broad live automation. | Extract to timing doc. |
| `2026-06-08-entry-timing-effect-baseline.md` | `entry_timing.md` | `active-evidence` | First rigorous timing baseline after gate. | Extract to timing doc. |
| `2026-06-08-entry-timing-rigorous-research-plan.md` | `entry_timing.md` | `design-plan` | Research plan; warns not to infer live rule from old samples. | Keep as timing method plan. |
| `2026-06-08-performance-side-band-alpha-summary.md` | `side_alpha.md` | `active-evidence` | Current side-band alpha snapshot with CLOB gate. | Extract into side-alpha doc with sample caveats. |
| `2026-06-08-side-band-entry-timing-impact.md` | `side_alpha.md` | `active-evidence` | Side-band timing shape differs; no simple v1 timing transplant. | Extract into side-alpha and entry-timing. |
| `2026-06-08-market-structural-edge.md` | `market_structure_edge.md` | `active-evidence` | Three gates, `verdict=inconclusive`. | Already referenced; keep active. |
| `2026-06-08-executable-edge.md` | `execution_quality.md` | `active-evidence` | Three gates, `verdict=inconclusive`; core execution reality test. | Already referenced; keep active. |
| `2026-06-09-range-rv-scanner-v0.md` | `market_structure_edge.md` | `duplicate` | Range RV v0, inconclusive. | Prefer v0-1 or v0-2. |
| `2026-06-09-range-rv-scanner-v0-1.md` | `market_structure_edge.md` | `duplicate` | Range RV v0 duplicate/variant, inconclusive. | Prefer positive v0.2 if retained. |
| `2026-06-09-range-rv-positive-v0-2.md` | `market_structure_edge.md` | `active-evidence` | Positive profile test; no live PnL, no live action, `inconclusive`. | Extract as current range-RV evidence. |
| `2026-06-09-range-rv-variant-lab-v0-3.md` | `market_structure_edge.md` | `active-evidence` | Broader pre-registered range-RV lab; decision proxy and orderbook subsets all fail final verdict, `inconclusive`. | Treat as newest range-RV negative evidence; no live action. |
| `2026-06-09-range-rv-walkforward-v0-4.md` | `market_structure_edge.md` | `active-evidence` | Expanding-window selector failed significance/baseline/forward gates. | Extract as walk-forward negative evidence. |
| `2026-06-09-range-rv-market-shape-v0-5.md` | `market_structure_edge.md` | `active-evidence` | Market-shape anomaly variants did not hold in holdout. | Extract as shape negative evidence. |
| `2026-06-09-range-rv-temporal-reversion-v0-6.md` | `market_structure_edge.md` | `active-evidence` | Temporal reversion generated only 20 rows and failed gates. | Extract sample-thin negative evidence. |
| `2026-06-09-range-rv-market-shape-fullop-v0-7.md` | `market_structure_edge.md` | `active-evidence` | Full-opportunity market-shape pass remained inconclusive. | Extract as latest shape evidence. |
| `2026-06-09-range-rv-regime-v0-8.md` | `market_structure_edge.md` | `active-evidence` | Regime-conditioned variants failed all gates. | Extract regime negative evidence. |
| `2026-06-09-range-rv-noarb-v0-9.md` | `market_structure_edge.md` | `superseded-evidence` | All-YES underround emerged as strongest family, but executable proof was incomplete in this pass. | Prefer robust v1.0 for current conclusion. |
| `2026-06-09-range-rv-underround-robust-v1-0.md` | `market_structure_edge.md` | `active-evidence` | All-YES underround confirmed offline in proxy and executable orderbook thresholds. | Keep as primary market-structure candidate evidence. |
| `2026-06-09-forecast-quality-range-rv-overlay.md` | `market_structure_edge.md` | `active-evidence` | Forecast-quality hard filters did not stably improve no-filter range baseline. | Extract as overlay caveat. |
| `2026-06-09-forecast-first-adjacent-range-rv-v0-1.md` | `market_structure_edge.md` | `active-evidence` | Forecast-first adjacent2/3 range failed gates in proxy and orderbook. | Extract as adjacent-range negative evidence. |
| `2026-06-09-center-shoulders-butterfly-range-rv.md` | `market_structure_edge.md` | `active-evidence` | Center/shoulder/butterfly structures were sample-limited and inconclusive. | Extract as shape-family negative evidence. |
| `2026-06-09-range-rv-tail-fade-uncertainty-v1-1.md` | `market_structure_edge.md` | `active-evidence` | Tail-fade/uncertainty baskets failed all gates. | Extract as tail-family negative evidence. |

### Handoff / Non-Weather / Misc

| File | Owner | Audit status | Read finding | Next action |
|---|---|---|---|---|
| `2026-06-08-HANDOFF-LANDING-VALIDATION.md` | `data_integrity.md` | `design-plan` | Verifies handoff package/script landing; not strategy evidence. | Archive after handoff method absorbed. |
| `2026-06-08-HANDOFF-REVIEW-AND-IMPROVEMENT-PLAN.md` | `data_integrity.md` | `design-plan` | Useful review of method and missing executable pieces. | Keep until migration done. |
| `2026-06-08-RELIABILITY-AUDIT-AND-HANDOFF.md` | `data_integrity.md` | `active-evidence` | Captures reliability thesis: model weak, execution/stat gates required. | Keep as meta-evidence. |
| `2026-06-08-decisive-experiment-scripts-audit-and-handoff-draft.md` | `data_integrity.md` | `duplicate` | Draft version; newer audit exists. | Archive draft after final is linked. |
| `2026-06-08-decisive-experiment-scripts-audit-and-handoff.md` | `data_integrity.md` | `active-evidence` | Final audit version; separates usable/unusable claims. | Keep until summarized in coverage map. |
| `2026-06-09-weather-strategy-research-window-handoff.md` | `data_integrity.md` | `active-evidence` | Window handoff with current verdict table. | Keep as active handoff until next window supersedes. |
| `2026-06-09-decision-window-backfill.md` | `data_integrity.md` | `active-evidence` | Decision-window backfill report. | Extract data-integrity caveat. |
| `2026-06-09-copy-trade-rule-edge-wallet-research.md` | `non-weather` | `non-weather` | Copy-trade wallet/rule-edge research, not weather spine. | Move to copy-trade docs area later. |
| `2026-06-09-openai-hardware-branch-edge-research.md` | `non-weather` | `non-weather` | OpenAI hardware branch market research, not weather spine. | Move out of weather analysis later. |

## Recommended Batch Order For Phase 4C/4D

1. `execution_quality + account_reconcile`: highest risk of wrong PnL/account claims; absorb fill recovery and executable-edge caveats first.
2. `model_vs_market + blender_shadow`: model alpha and blender conclusions are often conflated; consolidate negative/inconclusive gates.
3. `city_selection`: largest duplicate cluster; process only 5-8 docs per batch.
4. `entry_timing + side_alpha`: timing and side-band overlap; keep denominators explicit.
5. `live_performance`: merge live period-slice duplicates after account/fill gates are stable.
6. `non-weather`: move copy-trade/OpenAI hardware docs to their own area.
7. `2026-05 archive`: after lessons are extracted, move most May reports together.

## Phase 4C Pilot Absorption Log

### `execution_quality.md` absorbed on 2026-06-09

| File | Absorbed claim | Archive readiness |
|---|---|---|
| `2026-05-27-lineage-execution-UNKNOWN-vs-mid_price_core_v1.md` | UNKNOWN vs mid mechanism was signal filtering, not quote formula; paper-era only. | `archive-ready-after-link-check` |
| `2026-05-27-compare-mid-price-vs-maker-queue.md` | maker_queue price improvement was too small relative to fill-rate loss; retired-strategy evidence. | `archive-ready-after-link-check` |
| `2026-05-29-strategy-entry-band-and-execution-quality.md` | mid_price beat maker_queue in early A/B; filled-only price-band results conflict with opportunity universe. | `archive-ready-after-link-check` for execution claims; sizing claims still need `sizing_entry_band.md` |
| `2026-06-07-fill-recovery-and-performance-recalc.md` | CLOB coverage gate, partial-fill recovery, and public fallback over-allocation failure mode. | keep active evidence until next fill-recovery audit supersedes |
| `2026-06-08-executable-edge.md` | Step2 executable-edge gates failed; current verdict remains `inconclusive`. | keep active evidence |

### `account_reconcile.md` absorbed on 2026-06-09

| File | Absorbed claim | Archive readiness |
|---|---|---|
| `2026-06-06-account-equity-replay.md` | UI account loss is account-equity/cashflow reconstruction, not settled strategy PnL; public activity was diagnostic for magnitude. | `archive-ready-after-link-check` after account-equity replay successor exists |
| `2026-06-06-live-account-reconcile-near-binary-fix.md` | near-binary normalization and fill-date cashflow split are required. | `archive-ready-after-link-check` |
| `2026-06-06-polymarket-ui-account-loss-reconciliation.md` | settled strategy PnL cannot refute UI one-week loss; report already self-corrects. | `archive-ready-after-link-check` |
| `2026-06-07-fill-recovery-and-performance-recalc.md` | external account activity gap is separate from internal DB/fact coverage gate. | keep active evidence |
| `2026-06-07-live-strategy-period-slice-current.md` | fill-date cashflow and target-date strategy attribution must be presented separately. | keep for `live_performance.md` absorption later |

## Phase 4D Pilot Absorption Log

### `live_performance.md` absorbed on 2026-06-09

| File | Absorbed claim | Archive readiness |
|---|---|---|
| `2026-06-03-performance-three-strategy-instances.md` | Old near-binary and side-band mapping make numeric PnL invalid; keep only V2 action-history context. | `archive-ready-after-link-check` as invalidated evidence |
| `2026-06-06-three-strategy-instances-near-binary-reanalysis.md` | Post-fix instance actions: V2 stopped/shadow, V1 retained but not expanded, side-band small-size only. | keep active action-history evidence until entrypoint supersedes |
| `2026-06-06-live-strategy-period-slice.md` | Correct distinction between UI equity, target-date attribution, fill-date cashflow, open cost, and MTM. | `archive-ready-after-link-check`; numbers superseded |
| `2026-06-06-recent-live-loss-attribution.md` | Loss-attribution structure useful, but numeric output pre near-binary fix is invalidated. | `archive-ready-after-link-check` as invalidated evidence |
| `2026-06-07-live-strategy-period-slice-current.md` | Best 2026-06-07 target-date vs fill-date live period snapshot; row counts timestamped. | keep active evidence until newer live-performance snapshot |
| `2026-06-07-live-pnl-curve-0524-0606.md` | 15d curve with settled-only PnL and open-cost separation through 2026-06-06. | duplicate-family; keep newest after link check |
| `2026-06-07-live-pnl-15d-curve-after-history-rebuild.md` | Extended 15d curve through 2026-06-07; open rows and MTM missingness explicit. | keep as primary curve snapshot for now |
| `2026-06-07-recent-live-loss-attribution-target-0531-0606.md` | Post-fix loss attribution by cohort/city/side/instance; no global model proof. | keep active evidence |
| `2026-06-08-decisive-experiment-scripts-audit-and-handoff.md` | Current risk posture: gated DB usable, 6月 live turned negative, no add-size / no confirmed edge. | keep active meta-evidence |

### `data_integrity.md` absorbed on 2026-06-09

| File | Absorbed claim | Archive readiness |
|---|---|---|
| `2026-06-03-signal-side-flip-check.md` | Side flips are mostly forecast/probability instability, not side inversion by default. | `archive-ready-after-link-check` after monitor exists |
| `2026-05-29-performance-candidates-vs-fills-link.md` | Candidate/fill bridge reached zero orphan in snapshot; counterfactual and fill grains must stay separated. | keep active evidence until reusable orphan table exists |
| `2026-06-09-decision-window-backfill.md` | Backfill repaired analysis DB coverage only and marks `decision_window_source=orderbook_backfill`. | keep active evidence |
| `2026-06-08-HANDOFF-LANDING-VALIDATION.md` | Handoff landing/schema validation is method evidence, not strategy proof. | `archive-ready-after-link-check` |
| `2026-06-08-HANDOFF-REVIEW-AND-IMPROVEMENT-PLAN.md` | Corrected fact table/schema assumptions and staged reorg advice. | `archive-ready-after-link-check` after current plan supersedes |
| `2026-06-08-decisive-experiment-scripts-audit-and-handoff.md` | Report only three-gate verdicts; avoid drifting live_real counts and hard-coded rows. | keep active meta-evidence |

### `entry_timing.md` absorbed on 2026-06-09

| File | Absorbed claim | Archive readiness |
|---|---|---|
| `2026-05-27-compare-strict-t24-vs-wide-window.md` | Strict-vs-wide replay is early timing hypothesis only; old replay source and current facts supersede numbers. | `archive-ready-after-link-check` |
| `2026-05-29-entry-timing-edge.md` | `<T-22` filled samples looked strong but fill-rate/selection made it unsuitable for live restoration. | `archive-ready-after-link-check` |
| `2026-06-07-mid-price-core-v1-forecast-timing-degradation-lineage.md` | v1 `>T-28` risk filter and `T-26-28` shadow/drop caution; forecast/adverse-move mechanisms overlap. | keep active v1 timing evidence |
| `2026-06-08-entry-timing-rigorous-research-plan.md` | Timing research must use L0-L4 denominators, matched analysis, forecast checkpoint, and city-day portfolio. | keep design-plan until implementation |
| `2026-06-08-entry-timing-effect-baseline.md` | Current gated timing baseline: T-22-24 strongest; other bins need opportunity/checkpoint coverage. | keep active evidence |
| `2026-06-08-city-x-entry-timing-research.md` | City x timing cells guide review priority but are too sparse for broad live automation. | keep active evidence |
| `2026-06-08-side-band-entry-timing-impact.md` | Side-band timing differs from v1; no simple transplant of v1 timing cuts. | keep for side-alpha absorption too |

### `sizing_entry_band.md` absorbed on 2026-06-09

| File | Absorbed claim | Archive readiness |
|---|---|---|
| `2026-05-27-compare-execution-algorithm-window-filter.md` | `entry_price_window` is pre-trade signal filter; 0.25-0.75 removed many low-quality lottery/over-expensive signals. | `archive-ready-after-link-check` |
| `2026-05-29-strategy-entry-band-and-execution-quality.md` | Filled-only bands and opportunity bands can disagree; execution selection bias must be separated. | `archive-ready-after-link-check` for sizing claims |
| `2026-05-30-performance-entry-band-research.md` | Side x price bucket framework is useful; stale DB and high decision-window missingness block live use. | `archive-ready-after-link-check` after fresh rerun exists |
| `2026-05-30-performance-sizing-and-band-distribution.md` | EV-tier is risk leverage, Kelly overconcentrates, actual fill size inconsistency is first-order. | keep active methodology evidence until sizing report standard exists |
| `2026-06-07-v1-ecmwf-blocked-side-band-overlay.md` | Blocked ECMWF side-band overlay supports shadow research, not direct live restoration. | keep for side-alpha/city absorption too |

### `model_vs_market.md` absorbed on 2026-06-10

| File | Absorbed claim | Archive readiness |
|---|---|---|
| `2026-06-05-probability-calibration.md` | Raw model probability loses to market out of sample; small blend improvement is not confirmed alpha. | keep active calibration baseline |
| `2026-06-07-mid-price-core-v1-raw-degradation.md` | Post-June raw edge degraded, especially low/mid edge buckets and ECMWF-heavy paths. | keep active degradation evidence |
| `2026-06-07-mid-price-core-v1-raw-calibration-drift.md` | No clean code-version breakpoint was proven; breakpoint suspicion remains diagnostic only. | `archive-ready-after-link-check` after lineage rerun exists |
| `2026-06-07-mid-price-core-v1-forecast-timing-degradation-lineage.md` | Timing, adverse market move, and forecast/version lineage overlap with model degradation. | keep active evidence; also owned by `entry_timing.md` |
| `2026-06-08-city-model-conditional-edge.md` | City/model selected pockets have high point estimates but failed significance/baseline/forward gates. | keep active city/model caution |
| `2026-06-09-model-rank-ic.md` | `model_edge_at_decision` rank alpha remains inconclusive; `model_side_prob` is not tradable edge by itself. | keep active rank-IC evidence |

### `market_structure_edge.md` absorbed on 2026-06-10

| File | Absorbed claim | Archive readiness |
|---|---|---|
| `2026-06-08-market-structural-edge.md` | H_B structural BUY_NO/price-bucket edge is useful separation from model alpha but remains inconclusive. | keep active baseline evidence |
| `2026-06-09-range-rv-positive-v0-2.md` | Adjacent positive profiles had encouraging point estimates but failed required gates. | `archive-ready-after-link-check` after v0.3/v1.0 links verified |
| `2026-06-09-range-rv-variant-lab-v0-3.md` | Broad Range RV lab found no general adjacent/range/pair/center/tail rule ready for live. | keep active negative evidence |
| `2026-06-09-range-rv-walkforward-v0-4.md` | Prior-date walk-forward selector did not pass the three-gate standard. | `archive-ready-after-link-check` |
| `2026-06-09-range-rv-market-shape-v0-5.md` | Market-shape anomaly first pass failed holdout/forward gates. | `archive-ready-after-link-check` after fullop v0.7 retained |
| `2026-06-09-range-rv-temporal-reversion-v0-6.md` | Temporal reversion sample was too thin and failed gates. | `archive-ready-after-link-check` |
| `2026-06-09-range-rv-market-shape-fullop-v0-7.md` | Full-opportunity market-shape variants remained inconclusive. | keep active latest shape evidence |
| `2026-06-09-range-rv-regime-v0-8.md` | Regime-conditioned Range RV variants failed gates. | `archive-ready-after-link-check` |
| `2026-06-09-range-rv-noarb-v0-9.md` | All-YES underround emerged as strongest no-arb family, but robust v1.0 supersedes the current proof. | `archive-ready-after-link-check` after v1.0 retained |
| `2026-06-09-range-rv-underround-robust-v1-0.md` | All-YES underround is `confirmed_offline` in both proxy and executable orderbook tests. | keep primary active evidence |
| `2026-06-09-forecast-quality-range-rv-overlay.md` | Forecast-quality filters are soft stratification, not a stable hard filter. | keep active overlay caveat |
| `2026-06-09-forecast-first-adjacent-range-rv-v0-1.md` | Forecast-first adjacent2/3 range tests failed proxy/orderbook gates. | `archive-ready-after-link-check` |
| `2026-06-09-center-shoulders-butterfly-range-rv.md` | Center/shoulder/butterfly structures are sample-limited and inconclusive. | `archive-ready-after-link-check` |
| `2026-06-09-range-rv-tail-fade-uncertainty-v1-1.md` | Tail-fade/uncertainty baskets failed all gates. | `archive-ready-after-link-check` |

### `blender_shadow.md` absorbed on 2026-06-10

| File | Absorbed claim | Archive readiness |
|---|---|---|
| `2026-06-06-blended-single-v0-backtest.md` | Early blended opportunity backtest is search history only; pre near-binary correction and not final live evidence. | `archive-ready-after-link-check` as superseded evidence |
| `2026-06-06-blended-entry-band-backtest.md` | Early entry-band blended result is superseded by current gated fact-table runs. | `archive-ready-after-link-check` as superseded evidence |
| `2026-06-06-blended-paper-fill-estimate.md` | Paper fill estimate is not live-real authority; use only as early execution hypothesis. | `archive-ready-after-link-check` after execution doc link check |
| `2026-06-07-blended-live-instance-overlay.md` | Blender helped recent June fills but hurt pre-June fills; it is a drift/risk filter, not stable hard alpha. | keep active overlay evidence |
| `2026-06-07-blended-paper-fill-estimate.md` | Newer paper estimate remains shadow/paper only, not live fill proof. | `archive-ready-after-link-check` |
| `2026-06-08-blender-research-state-and-next-plan.md` | Best narrative handoff: blender should be lineage/risk/sizing shadow, not live hard gate. | keep active state handoff until next edge-engine doc |
| `2026-06-08-blender-signal-value-research.md` | Hard gates reduce PnL despite ROI optics; size curves and negative blended-edge alerts remain research candidates. | keep active signal-value evidence |
| `2026-06-08-v1-removed-ecmwf-t28-blender-overlay.md` | City/time controls explain more June degradation; blender is marginally negative after new operational base. | keep active control evidence |
| `2026-06-08-weather-edge-v2-shadow-lineage.md` | Edge v2 lineage is shadow/offline artifact; do not read it as live trading result. | keep active lineage evidence |
| `2026-06-08-weather-edge-v2-filtered-operational-base-research.md` | Edge v2/basket candidates need separate top5, forward, capacity, and execution gates; do not merge with blender hard-gate approval. | keep active edge-engine evidence |

### `side_alpha.md` absorbed on 2026-06-10

| File | Absorbed claim | Archive readiness |
|---|---|---|
| `2026-06-04-performance-side-band-entry-analysis.md` | Early side-band entry-band shape is useful, but pre-near-binary caveats and attribution issues make it secondary. | `archive-ready-after-link-check` as superseded evidence |
| `2026-06-07-v1-ecmwf-blocked-side-band-overlay.md` | Side-band would reduce losses in blocked ECMWF city fills, but it is an overlay and still shadow-only. | keep active cross-owner evidence |
| `2026-06-08-performance-side-band-alpha-summary.md` | Early side-band live PnL was positive but small, concentrated, and not enough for size expansion. | keep active side-band live snapshot |
| `2026-06-08-side-band-entry-timing-impact.md` | Side-band timing differs from v1; do not transplant v1 timing cuts mechanically. | keep active timing/side evidence |
| `2026-06-10-side-band-forecast-regime-v0.md` | Full-opportunity clean side-band + forecast-regime test failed significance/baseline/forward gates. | keep active current side-band evidence |
| `2026-06-10-side-band-mechanism-attribution-v1.md` | Side-band gains look like side/price/date concentration rather than stable reusable alpha; all selectors remain inconclusive. | keep active mechanism evidence |
| `2026-06-10-weather-strategy-live-test-selection.md` | Side-band is not a real live-test candidate in the current scoreboard. | keep active synthesis evidence |
| `2026-06-10-shadow-paper-queue-v0.md` | Side-band remains tag-only shadow, not paper/live queue. | keep active queue evidence |

### `city_selection.md` absorbed on 2026-06-10

| File | Absorbed claim | Archive readiness |
|---|---|---|
| `2026-06-06-city-alpha-framework.md` | Numeric city ranks are invalidated by old near-binary `missing_bracket`, but the target metric framework remains useful. | `archive-ready-after-link-check` as invalidated numbers |
| `2026-06-06-near-binary-city-reanalysis.md` | Post-fix city evidence points to city x side x strategy_instance, not whole-city ROI. | keep active city correction evidence |
| `2026-06-08-city-day-basket-research-state-and-plan.md` | Basket remains research/shadow; first rerun on filtered operational base before canary. | keep active basket handoff |
| `2026-06-08-city-day-basket-vs-legacy-baselines.md` | Basket/combos can show headline gains, but recent/top5 stress still blocks production. | keep active basket baseline evidence |
| `2026-06-08-city-day-basket-walkforward.md` | Train-selected basket rules do not yet beat simple always-on baselines in unseen windows. | keep active anti-overfit evidence |
| `2026-06-08-city-day-distribution-quality.md` | Market-normalized distribution remains the safer basket objective anchor in holdout/recent slices. | keep active distribution evidence |
| `2026-06-10-weather-strategy-live-test-selection.md` | No new real live-test candidate; adjacent3 only enters shadow/paper observation. | keep active synthesis evidence |
| `2026-06-10-live-test-readiness-scoreboard-v0.md` | Readiness scoreboard freezes adjacent3 shadow and defers all live scheduling. | keep active readiness evidence |

### `observed_max_m3.md` absorbed on 2026-06-10

| File | Absorbed claim | Archive readiness |
|---|---|---|
| `2026-06-10-m3-observed-max-strategy-handoff.md` | M3 is a new observed-running-max information structure; external claims need local reproduction. | keep active framing evidence |
| `2026-06-10-m3-observed-max-strategy-plan.md` | M3 must start with fact layer and physical residual gates; no live/paper config change. | keep active design evidence |
| `2026-06-10-m3-observed-max-residual-v0.md` | 19:00+ local observed-max residual passes the first physical candidate gate, but market/execution gates are untouched. | keep active physical evidence |
| `2026-06-10-m3-paper-snapshot-proxy-backtest-v0.md` | Proxy paper-market backtest confirms no reliable edge signal in 4/14 sparse trades; sample concentrated in Moscow/Madrid and not executable-best-ask. | keep active-snapshot; do not cite for live/paper claims |

## Hard Rules For Later Moves

- A historical file can move only after its useful claim appears in the owner living doc or is explicitly marked `invalidated`.
- Any file containing `missing_bracket` or pre-2026-06-07 live PnL must be checked against near-binary/fill-recovery notes before being cited.
- Duplicates should keep the newest gated report as the primary evidence.
- Current production decisions still live in `WEATHER_STRATEGY_ENTRYPOINT.md`, `WEATHER_CITY_POOL_DECISIONS.md`, and `WEATHER_ANALYSIS_CONTRACT.md`, not in old snapshots.
