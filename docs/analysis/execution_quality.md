# Execution Quality

> Living doc for module [4]: whether the strategy still has edge after maker-only execution, spread, queueing, fill selection, and adverse selection.
> Current status: `inconclusive`.
> Last updated: 2026-06-14 CLOB cache gate repair.

## Current Conclusion

Execution quality is one of the highest-priority open questions. PnL slices alone cannot prove the live strategy works because maker-only fills are selected by the market: the orders that fill may be systematically worse than the orders that did not fill.

The best current conclusion is still `inconclusive` for new live action. The fill-recovery layer is now trustworthy only when the CLOB coverage gate passes, but executable-edge research has not passed the significance/baseline/forward gates.

Batch 4C/4D status update:

1. **Fill truth gate comes first.** Old local fills can be internally consistent but still wrong if the fill recovery path over-allocates public activity or misses partial fills. Use `weather_clob_fill_coverage_gate.py`; publish `live_real` PnL only when `missing_order_rows=0`, `over_order_keys=0`, and DB/cache/fact cost deltas are zero. As of 2026-06-14, the local gate passes after repairing `runtime/weather_edge_v1/clob_fills.jsonl` to 855 current-order fills; the removed over-cap row was a duplicate Chengdu fill that exceeded its $5 order cap.
2. **Maker-based execution is now a retired mechanism.** Across independent reports, `maker_queue` always shows lower/poorer capture (`fill rate ~55%-62%` vs mid around `69%-79%`) with adverse selection concentration in long-latency fills. Its micro-price gains are tiny (`~4-7 cents per 100 shares` scale), so Maker is not currently a standalone live policy.
3. **Execution edge is still not confirmed.** 2026-06-08 `executable-edge.md` remains `significance=FAIL`, `baseline=FAIL`, `forward=NA`, `verdict=inconclusive`. The current action should be driven by risk controls and process hardening, not fresh live policy expansion.
4. **Stale-fill loss is the strongest concrete risk.** For maker-style behavior, fills with age `>2h`/`>4h` are materially worse (`WR ~33-40%` in sampled windows), supporting the 4-hour stale-fill cancel hypothesis in `maker-queue-cancel-backtest.md` as mechanism test, not live rule yet.
5. **The old blended-paper profile is dormant, not a current shadow candidate.** A 2026-08-13 replay on 45,009 settled-complete candidates found superficially positive full-history ROI (`+1.75%` for 25-75 and `+1.41%` for side-band), but both profiles were negative from 2026-05-26 onward and again from 2026-06-01 onward. Removing the five largest winners also reduced full-history ROI to `+1.20%/+0.33%`; the live-filled overlap was not better than the raw profile. Submitted-order fill-rate scaling is counterfactual, not fill evidence. Keep the mechanism only as historical evidence; do not restart its paper runners or infer current alpha from the June reports.

Do not cite old maker_queue or May entry-band numbers as current PnL. They are mechanism evidence, not current performance evidence.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/analysis/2026-06/2026-06-08-executable-edge.md` | 2026-06 Step2 | live_real fill audit, decision-entry proxy, time-aligned raw orderbook checks | `primary` |
| `docs/analysis/2026-06/2026-06-07-fill-recovery-and-performance-recalc.md` | 2026-06 fill recovery | CLOB/fact recovery and performance recalculation context | `primary` |
| `scripts/ops/repair_clob_fill_cache_for_gate.py` | 2026-06-14 | cap-safe local CLOB cache repair and backup flow; current repaired cache passes DB/cache/fact reconciliation | `active-tool` |
| `scripts/analysis/execution_quality/backtest_blended_paper_fill_estimate.py` | 2026-08-13 replay | full-history point estimates were positive, but holdout/recent and tail-robustness checks failed; machine output belongs in the JRS research store | `dormant-mechanism evaluator` |
| `docs/archive/analysis/2026-05/2026-05-29-strategy-entry-band-and-execution-quality.md` | 2026-05 | entry-price-window mechanism + maker/mid A/B logic | `historical-mechanism` |
| `docs/archive/analysis/2026-05/2026-05-27-lineage-execution-UNKNOWN-vs-mid_price_core_v1.md` | 2026-05 | signal filtering, not quote formula, explains UNKNOWN vs mid delta | `historical-mechanism` |
| `docs/archive/analysis/2026-05/2026-05-26-compare-UNKNOWN-vs-mid_price_core_v1.md` | 2026-05 | early UNKNOWN vs mid comparison context; 5/20 lift driven by eligible=False t2_research exposure | `historical-mechanism` |
| `docs/archive/analysis/2026-05/2026-05-27-compare-execution-algorithm-window-filter.md` | 2026-05 | entry_price_window as signal filter (`25-75`) and filled vs unfilled candidate mismatch | `historical-mechanism + sizing-boundary` |
| `docs/archive/analysis/2026-05/2026-05-27-compare-mid-price-vs-maker-queue.md` | 2026-05 | maker_queue A/B: lower fill rate + adverse selection, micro price gain | `retired-mechanism` |
| `docs/archive/analysis/2026-05/2026-05-27-maker-queue-baseline-and-optimization.md` | 2026-05 | maker_queue baseline evidence, stale-fill adverse selection pattern | `retired-mechanism` |
| `docs/archive/analysis/2026-05/2026-05-27-maker-queue-cancel-backtest.md` | 2026-05 | stale-fill sensitivity and 4h cancel hypothesis (sample-limited) | `retired-mechanism` |
| `docs/archive/analysis/2026-05/2026-05-28-performance-makerqueue-v3-city-pool.md` | 2026-05-28 | city-v3 open exposure and open-mark explanation for UI drawdown perception | `retired-mechanism` |

## Absorbed Historical Claims

| Claim | Current handling |
|---|---|
| UNKNOWN vs mid_price_core_v1 differed mostly by signal filtering, especially removing very low-priced BUY_YES lottery rows | Keep as historical lineage lesson; not current execution PnL |
| maker_queue gained only tiny price improvement while losing fill rate and filling a worse subset | Keep as retired maker_queue mechanism evidence; default policy remains mid-price execution |
| `entry_price_window` is pre-trade filter rather than quote formula change | Keep for boundary between sizing and execution: move bucket-ROI work to `sizing_entry_band.md` |
| 0.25-0.75 sub-bucket filled-sample vs candidate-sample mismatch remains unresolved selection-bias | Keep as active work item in `Open Work` |
| public Polymarket activity can over-allocate fills to child orders | Use authenticated/order-level fill data and coverage gate; public activity is fallback only |
| A later recovered fill can push an order over its local cap if previous cache rows are imported first | `clob_fill_sync.py` now caps authenticated order-level totals to submitted order shares/limit price and skips public fallback rows that would exceed existing order totals |
| maker-style stale fills older than ~2h are a repeated adverse-selection risk | Keep as execution hardening hypothesis: stale cancel controls remain on the test list |
| 2026-06-08 executable-edge Step2 did not prove tradable edge after spread/queue | Maintain `inconclusive`; no live expansion based on this evidence |
| 2026-05-28 city-pool UI drawdown perception included large open-mark component and legacy exposure overlap | Route to `account_reconcile.md`/`live_performance.md` for attribution, not execution edge |

## Required Gates Before Live Use

| Gate | Required Evidence |
|---|---|
| Coverage | `weather_clob_fill_coverage_gate.py gate_pass=true` before publishing live_real PnL/ROI |
| Fill selection | Compare filled vs unfilled candidates from `fact_signal_candidates`, not only filled rows |
| Cost | Report entry price, fill price, spread/queue proxy, and adverse-selection proxy separately |
| Baseline | Compare to same candidate set with same price/side constraints |
| Verdict | Label as `confirmed`, `shadow_candidate`, or `inconclusive`; failed gates cannot drive live behavior |

## Open Work

1. Keep `fact_trades` as realized fill source and `fact_signal_candidates` as opportunity/counterfactual source.
2. Add a standard table for fill rate, adverse selection, and executable edge by price bucket, city, side, and strategy_instance.
3. Do not use public Polymarket activity as authoritative order-level fill truth.
4. Archive retired maker_queue reports only after the above absorbed claims are linked from the archive note.
5. For next batch: add one standard table for stale-fill by execution age (`fill_age_bucket`) and city-level adverse-selection impact.
