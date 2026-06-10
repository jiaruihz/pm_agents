# Observed Max M3

> Living doc for modules [0]-[2]: observed running max / late-day residual research for a possible M3 weather strategy.
> Current status: `settlement_blocked`; not a live, paper, or shadow trading rule yet.
> Last updated: 2026-06-11 settlement alignment v1

Quant lineage anchor: M3 changes the information source before signal generation. Instead of forecasting tomorrow's final high at T-22 to T-28, it asks whether the current target day's observed running max is already effectively locked by local evening. That places M3 first in [0] data and [1] model/physical-feature validation before it can become a [2] signal.

## Current Conclusion

M3 is a valid physical research direction, but it is not approved for live, paper, or shadow trading.

The refreshed physical-layer residual experiment supports the core hypothesis: in the current WU/IEM cache, local 19:00-21:00 running max usually equals the final daily max, with low cross-bucket residual risk. Core 9 also passes the refreshed v1 physical gate.

M3 reached historical orderbook best-ask testing, but the first apparent positive result did not survive settlement alignment. Using WU/IEM observed max as payout truth made `below_running_max_buy_no` look strongly positive; using `pm_history` official winner labels on the same v1 trades made it negative.

The current blocker is upstream of execution:

1. WU/IEM observed max must be reconciled to Polymarket settlement brackets and station rules.
2. Celsius market source/rounding must be identified before lower-bracket NO can be treated as logically impossible.
3. Historical best ask can only be revisited after official-source observed running max aligns with `pm_history`.
4. If revived later, capacity, matched baselines, queue/latency, and deploy governance still remain required.

## Absorbed Historical Claims

1. The M3 handoff is useful as strategy framing, but external/deep-research numbers are not repo-verified until reproduced in local scripts and fact/cache outputs.
2. The M3 strategy plan correctly freezes the boundary: do not modify N100 live config, do not replace current production strategy, and do not introduce market price/PnL before the observed-max fact layer is validated.
3. The residual v0 report is the first local evidence: 49 cached cities and current core 9 pass the 19:00+ physical gate, but 18:00 is weaker for Paris/Amsterdam/Helsinki/Madrid and core 9 tail cases include Boston/NYC/London.
4. M3 should start with 20:00/21:00 local as the cleaner research window; 18:00 is observation-only until secondary-warming filters are validated.
5. Proxy paper-snapshot joins in `2026-06-10-m3-paper-snapshot-proxy-backtest-v0.md` produce highly concentrated 2- to 14-trade windows (core cities limited to Moscow/Madrid), and are `inconclusive` for ROI; they are blocked on dataset overlap and lack executable best-ask.
6. WU/IEM cache was refreshed on N100 and synced locally through 2026-06-10. The refreshed v1 residual outputs preserve the 20:00/21:00 physical case.
7. The 2026-06-10 orderbook best-ask v0/v1 observed-payout results are superseded. Settlement alignment v1 shows official `pm_history` winner labels disagree with WU/IEM observed payout often enough to flip the result negative.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/analysis/2026-06/2026-06-10-m3-observed-max-strategy-handoff.md` | 2026-06 handoff | strategy intuition and research discipline for observed max | snapshot |
| `docs/analysis/2026-06/2026-06-10-m3-observed-max-strategy-plan.md` | 2026-06 design | P0-P4 plan, repo ownership, no-live boundary | design-draft |
| `docs/analysis/2026-06/2026-06-10-m3-observed-max-residual-v0.md` | 2024-04-30 to 2026-05 cache window | physical residual experiment; 19:00+ passes v0 physical gate | active-evidence |
| `docs/analysis/2026-06/2026-06-10-m3-paper-snapshot-proxy-backtest-v0.md` | 2026-05-05 to 2026-05-12 | local paper snapshot proxy with `market_yes_price`, only 4/14 joined trades, no statistical gates | snapshot-inconclusive |
| `docs/analysis/2026-06/2026-06-10-m3-orderbook-best-ask-backtest-v0.md` | 2026-05-20 to 2026-06-09 trade window | superseded observed-payout best-ask result; do not cite ROI | superseded |
| `docs/analysis/2026-06/2026-06-11-m3-settlement-alignment-v1.md` | 2026-05-20 to 2026-06-09 trade window | official `pm_history` settlement alignment; observed-payout edge flips negative under official winners | active-evidence |

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

1. Identify Polymarket's official weather source / station / rounding rule for each city.
2. Rebuild observed running max from the official source, not WU/IEM proxy, or prove a robust transformation.
3. Require near-100% city-date alignment to `pm_history` winner labels before any market backtest.
4. Only after source alignment passes, recompute orderbook best-ask with official payout labels.
5. Then add capacity caps, matched baselines, queue/latency, and one-trade-per-city-day controls.
6. If all gates pass, create a shadow journal first; do not directly modify live strategies.
