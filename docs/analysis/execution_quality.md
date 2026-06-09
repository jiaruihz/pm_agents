# Execution Quality

> Living doc for module [4]: whether the strategy still has edge after maker-only execution, spread, queueing, fill selection, and adverse selection.
> Current status: `inconclusive`.
> Last updated: 2026-06-09 Phase 4C pilot.

## Current Conclusion

Execution quality is one of the highest-priority open questions. PnL slices alone cannot prove the live strategy works because maker-only fills are selected by the market: the orders that fill may be systematically worse than the orders that did not fill.

The best current conclusion is still `inconclusive` for new live action. The fill-recovery layer is now trustworthy only when the CLOB coverage gate passes, but executable-edge research has not passed the significance/baseline/forward gates.

Phase 4C absorbed the older execution snapshots into three durable conclusions:

1. **Fill truth gate comes first.** Old local fills can be internally consistent but still wrong if the fill recovery path over-allocates public activity or misses partial fills. Use `weather_clob_fill_coverage_gate.py`; publish `live_real` PnL only when `missing_order_rows=0`, `over_order_keys=0`, and DB/cache/fact cost deltas are zero.
2. **Maker queue underperformed mid-price in the early A/B evidence.** The May evidence is stale for current PnL, but the mechanism remains useful: maker_queue lost too much fill rate and appeared to fill a worse subset. The benefit from small price improvement did not compensate for lower capture and adverse selection.
3. **Executable edge is not confirmed.** The 2026-06-08 Step2 report showed `significance=FAIL`, `baseline=FAIL`, `forward=NA`, `verdict=inconclusive`. High model edge did not reliably map to higher realized ROI, and raw orderbook taker/maker-proxy checks were diagnostic only.

Do not cite old maker_queue or May entry-band numbers as current PnL. They are mechanism evidence, not current performance evidence.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/analysis/2026-06/2026-06-08-executable-edge.md` | 2026-06 Step2 | live_real fill audit, decision-entry proxy, time-aligned raw orderbook checks | snapshot |
| `docs/analysis/2026-06/2026-06-07-fill-recovery-and-performance-recalc.md` | 2026-06 fill recovery | CLOB/fact recovery and performance recalculation context | snapshot |
| `docs/analysis/2026-05/2026-05-29-strategy-entry-band-and-execution-quality.md` | 2026-05 | early entry band and execution-quality context | snapshot |
| `docs/analysis/2026-05/2026-05-27-lineage-execution-UNKNOWN-vs-mid_price_core_v1.md` | 2026-05 | paper-era lineage: signal filtering, not quote formula, explained much of UNKNOWN vs mid delta | snapshot |
| `docs/analysis/2026-05/2026-05-27-compare-mid-price-vs-maker-queue.md` | 2026-05 | early maker_queue vs mid comparison; useful only as retired-strategy mechanism evidence | snapshot |

## Absorbed Historical Claims

| Claim | Current handling |
|---|---|
| UNKNOWN vs mid_price_core_v1 differed mostly by signal filtering, especially removing very low-priced BUY_YES lottery rows | Keep as historical lineage lesson; not current execution PnL |
| maker_queue gained only tiny price improvement while losing fill rate and filling a worse subset | Keep as retired maker_queue mechanism evidence |
| 0.25-0.75 entry-band sub-buckets showed conflicting filled-sample vs full-opportunity results | Route price-band decisions to `sizing_entry_band.md`; do not use filled-only tables as execution proof |
| public Polymarket activity can over-allocate fills to child orders | Use authenticated/order-level fill data and coverage gate; public activity is fallback only |
| 2026-06-08 executable-edge Step2 did not prove tradable edge after spread/queue | Maintain `inconclusive`; no live expansion based on this evidence |

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
