# Data Integrity

> Living doc for module [0]: snapshot health, side flips, candidate/fill linkage, fact-table coverage, and known data gaps.
> Current status: `current-reference` for analysis preflight; current source docs remain `WEATHER_DATA_CANONICAL_SOURCES.md` and `WEATHER_ANALYSIS_CONTRACT.md`.
> Last updated: 2026-06-09.

## Current Conclusion

Data integrity checks are mandatory before weather analysis. The main failure mode is mistaking an incomplete derived DB or historical snapshot for current truth. Start from source/mirror/derived boundaries, then run the five SQL checks and CLOB coverage gate when live_real is involved.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/WEATHER_DATA_CANONICAL_SOURCES.md` | current | source/mirror/derived/legacy and required SQL checks | current-source |
| `docs/WEATHER_DATA_PIPELINE.md` | current | N100 -> local mirror -> DB -> API flow | current-source |
| `docs/analysis/2026-06/2026-06-03-signal-side-flip-check.md` | 2026-06 | side flip and bracket evolution investigation | snapshot |
| `docs/analysis/2026-05/2026-05-29-performance-candidates-vs-fills-link.md` | 2026-05 | candidate vs fill linkage context | snapshot |

## Required Preflight

| Gate | Required Evidence |
|---|---|
| Freshness | `SELECT MAX(fact_built_at_utc) FROM fact_trades` |
| Class coverage | `trade_class` counts in `fact_trades` |
| Settlement | `settlement_status` counts in `fact_trades` |
| Candidate coverage | `fact_signal_candidates` eligible/paper/live coverage |
| Order/fill linkage | CLOB `orders` joined to `fills` by execution_id |

## Open Work

1. Keep side-flip and snapshot-health analysis here, not in live-performance conclusions.
2. If raw live files are newer than DB, report DB lag instead of forcing a PnL conclusion.
