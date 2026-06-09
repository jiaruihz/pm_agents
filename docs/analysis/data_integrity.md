# Data Integrity

> Living doc for module [0]: snapshot health, side flips, candidate/fill linkage, fact-table coverage, and known data gaps.
> Current status: `current-reference` for analysis preflight; current source docs remain `WEATHER_DATA_CANONICAL_SOURCES.md` and `WEATHER_ANALYSIS_CONTRACT.md`.
> Last updated: 2026-06-09 Phase 4D pilot.

## Current Conclusion

Data integrity checks are mandatory before weather analysis. The main failure mode is mistaking an incomplete derived DB or historical snapshot for current truth. Start from source/mirror/derived boundaries, then run the five SQL checks and CLOB coverage gate when live_real is involved.

Phase 4D absorbed the side-flip, candidate-link, decision-window, and handoff audit reports into the current rule:

1. **Raw snapshot evidence can be fresher than DB facts.** If DB `signals` or `fact_*` lag raw mirrored files, report the lag instead of forcing a DB-only conclusion.
2. **Side flips are not automatically side bugs.** The 2026-06-03 check showed flips were mostly forecast/probability version instability amplified by narrow brackets; monitor them as stability/size factors unless formula or token inversion evidence appears.
3. **`fact_signal_candidates` and `fact_trades` are different grains.** Candidate counterfactuals measure opportunity/capture; fill facts measure executed PnL. Joining them is valid only after orphan checks and denominator labels.
4. **Decision-window coverage is an analysis-quality gate.** Backfilled rows must be marked by `decision_window_source`; high `decision_window_missing` means over-fine slices should be downgraded.
5. **Handoff and experiment reports are method evidence unless their scripts have run under current gates.** Do not cite a handoff manifest as proof that a strategy edge is confirmed.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/WEATHER_DATA_CANONICAL_SOURCES.md` | current | source/mirror/derived/legacy and required SQL checks | current-source |
| `docs/WEATHER_DATA_PIPELINE.md` | current | N100 -> local mirror -> DB -> API flow | current-source |
| `docs/analysis/2026-06/2026-06-03-signal-side-flip-check.md` | 2026-06 | side flip and bracket evolution investigation | snapshot |
| `docs/analysis/2026-05/2026-05-29-performance-candidates-vs-fills-link.md` | 2026-05 | candidate vs fill linkage context | snapshot |
| `docs/analysis/2026-06/2026-06-09-decision-window-backfill.md` | 2026-06 | analysis DB repair for decision-window coverage | snapshot |
| `docs/analysis/2026-06/2026-06-08-HANDOFF-LANDING-VALIDATION.md` | 2026-06 | handoff package landing and schema validation | design-plan |
| `docs/analysis/2026-06/2026-06-08-HANDOFF-REVIEW-AND-IMPROVEMENT-PLAN.md` | 2026-06 | handoff package execution gaps and three-source microstructure correction | design-plan |
| `docs/analysis/2026-06/2026-06-08-decisive-experiment-scripts-audit-and-handoff.md` | 2026-06 | final audit: use three-gate verdicts and avoid drifting live_real counts | snapshot |

## Absorbed Historical Claims

| Claim | Current handling |
|---|---|
| Signal side flips can come from forecast max changes of 1-3F and narrow bracket probability jumps | Monitor as probability/forecast stability risk; do not treat as side inversion bug by default |
| Candidate/fill linkage had zero paper/live orphans in the 2026-05-29 snapshot | Keep as bridge evidence, but rerun current orphan checks before fresh analysis |
| Candidate counterfactuals can show missed opportunity but `paper_ordered` is not live execution intent | Never call paper-not-live rows "missed live orders" without live intent/order evidence |
| Decision-window backfill reduced missing rows but used `orderbook_backfill` sources | Treat as analysis DB repair only; it does not change N100 live behavior |
| Handoff manifests initially mixed present files, missing scripts, and future plans | Use landing validation and final audit as method/history; current source remains contract plus live docs |

## Required Preflight

| Gate | Required Evidence |
|---|---|
| Freshness | `SELECT MAX(fact_built_at_utc) FROM fact_trades` |
| Class coverage | `trade_class` counts in `fact_trades` |
| Settlement | `settlement_status` counts in `fact_trades` |
| Candidate coverage | `fact_signal_candidates` eligible/paper/live coverage |
| Order/fill linkage | CLOB `orders` joined to `fills` by execution_id |
| Decision window | Coverage and `decision_window_source` distribution before counterfactual or timing claims |
| Grain labeling | State whether each table uses candidate, fill, order, city-day, or account-equity grain |

## Open Work

1. Keep side-flip and snapshot-health analysis here, not in live-performance conclusions.
2. If raw live files are newer than DB, report DB lag instead of forcing a PnL conclusion.
3. Add a small reusable orphan/coverage table for future candidate-vs-fill reports.
