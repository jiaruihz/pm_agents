# Side Alpha

> Living doc for module [2]: whether BUY_NO, BUY_YES, or side-band rules have persistent excess edge.
> Current status: `inconclusive` for live expansion; historical BUY_NO advantage requires baseline and forward validation.
> Last updated: 2026-06-09.

## Current Conclusion

Historical BUY_NO strength and side-band differences are important, but they must not be treated as confirmed alpha without price-bucket, market-structure, and forward controls. A side can win more often while still being fairly priced or unprofitable after execution.

Side conclusions must report both win-rate and ROI/PnL, and should separate realized fills from missed opportunities.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/analysis/2026-06/2026-06-08-performance-side-band-alpha-summary.md` | 2026-06 | side-band alpha summary | snapshot |
| `docs/analysis/2026-06/2026-06-08-side-band-entry-timing-impact.md` | 2026-06 | side-band timing impact | snapshot |
| `docs/analysis/2026-06/2026-06-04-performance-side-band-entry-analysis.md` | 2026-06 | early side-band entry analysis; pre-near-binary caution applies | snapshot |

## Required Gates Before Live Use

| Gate | Required Evidence |
|---|---|
| Baseline | Same price bucket and city/date controls; compare to market-structure baseline |
| Significance | Bootstrap or cluster CI by city-day |
| Forward | Date-forward validation for any selected side rule |
| Execution | If applied live, route through `execution_quality.md` for fill selection and spread cost |

## Open Work

1. Separate side base-rate, market mispricing, and model-driven side selection.
2. Avoid using win-rate alone as a live gate.
