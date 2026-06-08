# Execution Quality

> Living doc for module [4]: whether the strategy still has edge after maker-only execution, spread, queueing, fill selection, and adverse selection.
> Current status: `inconclusive`.
> Last updated: 2026-06-09.

## Current Conclusion

Execution quality is one of the highest-priority open questions. PnL slices alone cannot prove the live strategy works because maker-only fills are selected by the market: the orders that fill may be systematically worse than the orders that did not fill.

Current evidence has started the right direction, but no live action should be based on execution analysis until CLOB fill coverage passes and fill-vs-unfill counterfactuals are separated from realized fill PnL.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/analysis/2026-06/2026-06-08-executable-edge.md` | 2026-06 Step2 | live_real fill audit, decision-entry proxy, time-aligned raw orderbook checks | snapshot |
| `docs/analysis/2026-06/2026-06-07-fill-recovery-and-performance-recalc.md` | 2026-06 fill recovery | CLOB/fact recovery and performance recalculation context | snapshot |
| `docs/analysis/2026-05/2026-05-29-strategy-entry-band-and-execution-quality.md` | 2026-05 | early entry band and execution-quality context | snapshot |

## Required Gates Before Live Use

| Gate | Required Evidence |
|---|---|
| Coverage | `weather_clob_fill_coverage_gate.py gate_pass=true` before publishing live_real PnL/ROI |
| Fill selection | Compare filled vs unfilled candidates from `fact_signal_candidates`, not only filled rows |
| Cost | Report entry price, fill price, spread/queue proxy, and adverse-selection proxy separately |
| Baseline | Compare to same candidate set with same price/side constraints |

## Open Work

1. Keep `fact_trades` as realized fill source and `fact_signal_candidates` as opportunity/counterfactual source.
2. Add a standard table for fill rate, adverse selection, and executable edge by price bucket, city, side, and strategy_instance.
3. Do not use public Polymarket activity as authoritative order-level fill truth.
