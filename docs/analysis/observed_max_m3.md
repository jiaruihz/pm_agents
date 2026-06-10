# Observed Max M3

> Living doc for modules [0]-[2]: observed running max / late-day residual research for a possible M3 weather strategy.
> Current status: `orderbook_candidate`; not a live, paper, or shadow trading rule yet.
> Last updated: 2026-06-10 WU cache refresh + M3 orderbook best-ask v0

Quant lineage anchor: M3 changes the information source before signal generation. Instead of forecasting tomorrow's final high at T-22 to T-28, it asks whether the current target day's observed running max is already effectively locked by local evening. That places M3 first in [0] data and [1] model/physical-feature validation before it can become a [2] signal.

## Current Conclusion

M3 is a valid new research direction, but it is not approved for live trading.

The refreshed physical-layer residual experiment supports the core hypothesis: in the current WU/IEM cache, local 19:00-21:00 running max usually equals the final daily max, with low cross-bucket residual risk. Core 9 also passes the refreshed v1 physical gate.

M3 has now reached historical orderbook best-ask testing. The strongest current expression is `below_running_max_buy_no`: when observed running max has already exceeded a lower Celsius bracket by local 20:00/21:00, buy that lower bracket's NO token at orderbook best ask. In the first corrected best-ask v0, this expression is strongly positive on a small 2026-05-20 to 2026-06-09 window.

That is still **not** live PnL or fill-confirmed ROI. The current gaps are large enough that M3 must stay research/shadow-only:

1. WU/IEM observed max must be reconciled to Polymarket settlement brackets and station rules.
2. Historical best ask must be converted to capped executable capacity with queue/latency constraints.
3. Matched baselines must separate stale lower-bracket NO edge from generic low-price NO behavior.
4. Bad-case filters for late-day secondary warming must be built from decision-time-visible features only.
5. Any later shadow/live implementation must be separate from existing `mid_price_core_v1_*` instances and routed through deploy governance.

## Absorbed Historical Claims

1. The M3 handoff is useful as strategy framing, but external/deep-research numbers are not repo-verified until reproduced in local scripts and fact/cache outputs.
2. The M3 strategy plan correctly freezes the boundary: do not modify N100 live config, do not replace current production strategy, and do not introduce market price/PnL before the observed-max fact layer is validated.
3. The residual v0 report is the first local evidence: 49 cached cities and current core 9 pass the 19:00+ physical gate, but 18:00 is weaker for Paris/Amsterdam/Helsinki/Madrid and core 9 tail cases include Boston/NYC/London.
4. M3 should start with 20:00/21:00 local as the cleaner research window; 18:00 is observation-only until secondary-warming filters are validated.
5. Proxy paper-snapshot joins in `2026-06-10-m3-paper-snapshot-proxy-backtest-v0.md` produce highly concentrated 2- to 14-trade windows (core cities limited to Moscow/Madrid), and are `inconclusive` for ROI; they are blocked on dataset overlap and lack executable best-ask.
6. WU/IEM cache was refreshed on N100 and synced locally through 2026-06-10. The refreshed v1 residual outputs preserve the 20:00/21:00 physical case.
7. Corrected orderbook best-ask v0 interprets Polymarket bracket labels as Celsius and uses token-side `raw.asks` best ask. `below_running_max_buy_no` is strongly positive on the first overlap window, but still requires settlement alignment, capacity, and shadow journal gates.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/analysis/2026-06/2026-06-10-m3-observed-max-strategy-handoff.md` | 2026-06 handoff | strategy intuition and research discipline for observed max | snapshot |
| `docs/analysis/2026-06/2026-06-10-m3-observed-max-strategy-plan.md` | 2026-06 design | P0-P4 plan, repo ownership, no-live boundary | design-draft |
| `docs/analysis/2026-06/2026-06-10-m3-observed-max-residual-v0.md` | 2024-04-30 to 2026-05 cache window | physical residual experiment; 19:00+ passes v0 physical gate | active-evidence |
| `docs/analysis/2026-06/2026-06-10-m3-paper-snapshot-proxy-backtest-v0.md` | 2026-05-05 to 2026-05-12 | local paper snapshot proxy with `market_yes_price`, only 4/14 joined trades, no statistical gates | snapshot-inconclusive |
| `docs/analysis/2026-06/2026-06-10-m3-orderbook-best-ask-backtest-v0.md` | 2026-05-20 to 2026-06-09 trade window | token-side orderbook best-ask v0 after WU cache refresh; lower-bracket BUY_NO candidate positive but not fill-confirmed | active-evidence |

## Required Gates Before Trading Use

| Gate | Required Evidence |
|---|---|
| Fact layer | `observed_running_max_c`, station id, decision local time, and source timestamp stored with reproducible lineage |
| Settlement alignment | Observed final max and Polymarket `pm_history` final bracket agree under the documented bracket/rounding rule |
| Physical forward | Train-selected city/hour windows hold in date-forward validation with low residual and low bucket-crossing risk |
| Bad-case filter | Late secondary-warming risk is filtered using only decision-time-visible weather features |
| Market baseline | Compare observed-max bracket/NO expression to same city/date/hour/price matched baselines |
| Executability | Time-aligned orderbook snapshots, spread/depth/capacity, and complete fill feasibility before paper/live |
| Governance | Any N100 data, paper, or live change goes through the weather deploy flow and is recorded in source-of-truth docs |

## Open Work

1. Reconcile WU/IEM daily observed max to `pm_history` settlement brackets by city/station/date.
2. Recompute orderbook v0 after settlement alignment, with official payout labels.
3. Add capacity caps using best ask size, max notional, and one-trade-per-city-day controls.
4. Build matched baselines for lower-bracket NO vs same price/hour/side alternatives.
5. Build bad-case feature study for `residual_c >= 1C` and `bucket_delta >= 1`.
6. Add an explicit M3 fact or research table before any paper/live execution.
7. If gates pass, create a shadow journal first; do not directly modify live strategies.
