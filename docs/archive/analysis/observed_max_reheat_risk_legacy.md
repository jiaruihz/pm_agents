# Observed Max reheat-risk

> Living doc for modules [0]-[2]: observed running max / late-day residual research for a possible reheat-risk weather strategy.
> Current status: `source_registry_ready_but_trading_blocked`; not a live, paper, or shadow trading rule yet.
> Last updated: 2026-06-14 settlement source registry v0

Quant lineage anchor: reheat-risk changes the information source before signal generation. Instead of forecasting tomorrow's final high at T-22 to T-28, it asks whether the current target day's observed running max is already effectively locked by local evening. That places reheat-risk first in [0] data and [1] model/physical-feature validation before it can become a [2] signal.

## Current Conclusion

reheat-risk is a valid physical research direction, but it is not approved for live, paper, or shadow trading.

The refreshed physical-layer residual experiment supports the core hypothesis: in the current WU/IEM cache, local 19:00-21:00 running max usually equals the final daily max, with low cross-bucket residual risk. Core 9 also passes the refreshed v1 physical gate.

reheat-risk reached historical orderbook best-ask testing, but the first apparent positive result did not survive settlement alignment. Using WU/IEM observed max as payout truth made `below_running_max_buy_no` look strongly positive; using `pm_history` official winner labels on the same v1 trades made it negative.

The current blocker is upstream of execution:

1. WU/IEM observed max must be reconciled to Polymarket settlement brackets and station rules.
2. Celsius market source/rounding must be identified before lower-bracket NO can be treated as logically impossible.
3. Historical best ask can only be revisited after official-source observed running max aligns with `pm_history`.
4. If revived later, capacity, matched baselines, queue/latency, and deploy governance still remain required.

The tail-NO retail diagnosis v0 (2026-06-11) sharpened all of this:

1. Settlement mismatch is city-concentrated station misalignment, not rounding: 36 cities align 100% (the whitelist), while Shenzhen/Jakarta/Milan/London/KL/HongKong/Paris/PanamaCity/Chicago are misaligned with per-city systematic but month-drifting offsets. Constant-offset correction fails; only official-source identification can fix them.
2. Every below-running-max cheap NO in the orderbook window sat in a misaligned city; the whitelist had zero. The apparent edge was settlement basis sold to us by official-source watchers.
3. The physical actuarial table supports the theta premise (whitelist P(bucket jump) after 20:00 local is 0.60% overall, ~0% in SaoPaulo/Chengdu/Wuhan/Beijing/TelAviv, but 2.6-3.6% in NYC/Amsterdam/Helsinki), yet at 20/21h the market already prices it: tail NO above running max returns -27.8% official ROI on the few executable asks, and whitelist top-of-book theta capacity over 22 days totals ~$211 notional / ~$11 max profit.
4. Open directions that remain data-supported: identify official resolution sources, move the study window to 14:00-17:00 local, evaluate the maker (not taker) expression in zero-jump cities, or repurpose the jump table as a stop-loss/exit module for existing strategies.

The settlement source registry v0 (2026-06-14) converts that blocker into a usable city-level source map:

1. `pm_history` / `final_yes` remains the market payout truth. The registry is about whether our observed/forecast feature source is aligned with the station/feed/rule that Polymarket settles against.
2. Confirmed official station-diff cities: Chicago KORD, KualaLumpur WMKK, London EGLC, Milan LIMC, PanamaCity MPMG, Paris LFPB, and Jakarta WIHH. These can only be studied with official-source features and per-market rules recheck.
3. HongKong is a confirmed special-source city: HKO Daily Extract absolute daily max, decimal precision, floor-to-integer bracket mapping. VHHH/IEM/WU must not be used as the payout feature source for HK.
4. Moscow, Seoul, and Shenzhen remain blocked unresolved settlement-basis cities. Do not include them in reheat-risk, station-basis, or source-sensitive forecast-quality conclusions until their mismatch is explained.
5. This makes source-sensitive research possible again, but not trading-approved: the next gate is a reusable official-source fact layer plus time-aligned orderbook retests, not a live config change.

## Absorbed Historical Claims

1. The reheat-risk handoff is useful as strategy framing, but external/deep-research numbers are not repo-verified until reproduced in local scripts and fact/cache outputs.
2. The reheat-risk strategy plan correctly freezes the boundary: do not modify N100 live config, do not replace current production strategy, and do not introduce market price/PnL before the observed-max fact layer is validated.
3. The residual v0 report is the first local evidence: 49 cached cities and current core 9 pass the 19:00+ physical gate, but 18:00 is weaker for Paris/Amsterdam/Helsinki/Madrid and core 9 tail cases include Boston/NYC/London.
4. reheat-risk should start with 20:00/21:00 local as the cleaner research window; 18:00 is observation-only until secondary-warming filters are validated.
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
| `docs/analysis/2026-06/2026-06-11-m3-tail-no-retail-diagnosis-v0.md` | alignment 1,403 city-days + residual 2024-04-30..2026-06-10 + 20/21h quotes | 36-city alignment whitelist; bucket-jump actuarial table; tail NO above running max is -27.8% official ROI with ~zero retail capacity at 20/21h | active-evidence |
| `docs/analysis/2026-06/2026-06-14-settlement-source-registry-v0.md` | current generated source registry | city-level official source classes: station-diff confirmed, HK/Jakarta fixes, Moscow/Seoul/Shenzhen blocked | active-evidence |

## Required Gates Before Trading Use

| Gate | Required Evidence |
|---|---|
| Fact layer | `observed_running_max_c`, station id, decision local time, and source timestamp stored with reproducible lineage |
| Settlement alignment | Observed final max from the official station/feed and Polymarket `pm_history` final bracket agree under the documented bracket/rounding rule |
| Physical forward | Train-selected city/hour windows hold in date-forward validation with low residual and low bucket-crossing risk |
| Bad-case filter | Late secondary-warming risk is filtered using only decision-time-visible weather features |
| Market baseline | Compare observed-max bracket/NO expression to same city/date/hour/price matched baselines |
| Executability | Time-aligned orderbook snapshots, spread/depth/capacity, and complete fill feasibility before paper/live |
| Governance | Any N100 data, paper, or live change goes through the weather deploy flow and is recorded in source-of-truth docs |

## Open Work

1. Promote `settlement_source_registry_v0` into a generated sidecar joined by city/date for reheat-risk, station-basis, forecast-quality, and basket research.
2. Build live-capable official-source fetchers for HKO and WIHH, and official-station running-max fetchers for the station-diff cities.
3. Diagnose Moscow/Seoul/Shenzhen mismatch dates against official rendered page values, METAR/WU minute history, and rounding/precision rules.
4. Recompute orderbook best-ask only after official-source feature alignment passes for the target city set.
5. Then add capacity caps, matched baselines, queue/latency, and one-trade-per-city-day controls.
6. If all gates pass, create a shadow journal first; do not directly modify live strategies.
