# Observed Max M3

> Living doc for modules [0]-[2]: observed running max / late-day residual research for a possible M3 weather strategy.
> Current status: `physical_candidate`; not a live, paper, or shadow trading rule yet.
> Last updated: 2026-06-10 Phase 4D M3 absorption.

Quant lineage anchor: M3 changes the information source before signal generation. Instead of forecasting tomorrow's final high at T-22 to T-28, it asks whether the current target day's observed running max is already effectively locked by local evening. That places M3 first in [0] data and [1] model/physical-feature validation before it can become a [2] signal.

## Current Conclusion

M3 is a valid new research direction, but it is not approved for live or paper trading.

The first physical-layer residual experiment supports the core hypothesis: in the current WU/IEM cache, local 19:00-21:00 running max usually equals the final daily max, with low cross-bucket residual risk. Core 9 also passes the v0 physical gate.

That is only a `physical_candidate` result. It does **not** prove market edge, executable edge, or fill feasibility. The current gaps are large enough that M3 must stay research-only:

1. WU/IEM observed max must be reconciled to Polymarket settlement brackets and station rules.
2. Latest June observations need refreshed coverage before current live relevance is claimed.
3. Bad-case filters for late-day secondary warming must be built from decision-time-visible features only.
4. Price/orderbook tests must use time-aligned snapshots after physical and bad-case gates pass.
5. Any later shadow/live implementation must be separate from existing `mid_price_core_v1_*` instances and routed through deploy governance.

## Absorbed Historical Claims

1. The M3 handoff is useful as strategy framing, but external/deep-research numbers are not repo-verified until reproduced in local scripts and fact/cache outputs.
2. The M3 strategy plan correctly freezes the boundary: do not modify N100 live config, do not replace current production strategy, and do not introduce market price/PnL before the observed-max fact layer is validated.
3. The residual v0 report is the first local evidence: 49 cached cities and current core 9 pass the 19:00+ physical gate, but 18:00 is weaker for Paris/Amsterdam/Helsinki/Madrid and core 9 tail cases include Boston/NYC/London.
4. M3 should start with 20:00/21:00 local as the cleaner research window; 18:00 is observation-only until secondary-warming filters are validated.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/analysis/2026-06/2026-06-10-m3-observed-max-strategy-handoff.md` | 2026-06 handoff | strategy intuition and research discipline for observed max | snapshot |
| `docs/analysis/2026-06/2026-06-10-m3-observed-max-strategy-plan.md` | 2026-06 design | P0-P4 plan, repo ownership, no-live boundary | design-draft |
| `docs/analysis/2026-06/2026-06-10-m3-observed-max-residual-v0.md` | 2024-04-30 to 2026-05 cache window | physical residual experiment; 19:00+ passes v0 physical gate | active-evidence |

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
2. Refresh latest WU/IEM cache through current June for core 9 and any proposed M3 cities.
3. Build bad-case feature study for `residual_c >= 1C` and `bucket_delta >= 1`.
4. Add an explicit M3 fact or research table before connecting to market prices.
5. Only after P2/P3 pass, run time-aligned orderbook backtests for 20:00/21:00 local windows.
