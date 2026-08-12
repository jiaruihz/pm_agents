# Side Alpha

> Living doc for module [2]: whether BUY_NO, BUY_YES, or side-band rules have persistent excess edge.
> Current status: `inconclusive` for live expansion; historical BUY_NO advantage requires baseline and forward validation.
> Last updated: 2026-06-10 Phase 4D side absorption.

## Current Conclusion

Historical BUY_NO strength and side-band differences are important, but they must not be treated as confirmed alpha without price-bucket, market-structure, and forward controls. A side can win more often while still being fairly priced or unprofitable after execution.

The current side-band evidence is now split:

1. The early live side-band instance produced positive settled PnL in a short window, and the gate shape is economically plausible.
2. The cleaner 2026-06-10 full-opportunity and mechanism tests did **not** pass significance, baseline, and forward gates. The best-looking variants were unstable after holdout and top-winner stress.
3. Side-band should remain a feature/tag and small-sample research line, not an independent live expansion rule.

Side conclusions must report win-rate, ROI/PnL, top-winner dependence, matched baseline excess, and whether the number comes from realized fills or opportunity-level counterfactuals.

## Absorbed Historical Claims

1. The 2026-06-04 entry analysis is an early snapshot; it is useful for entry-band shape but pre-near-binary caveats and strict attribution issues make it secondary.
2. The 2026-06-08 side-band alpha summary shows real early live PnL was positive but small and concentrated by date/city; it supports continued observation, not size expansion.
3. The 2026-06-08 timing impact shows side-band timing differs from v1 25-75. Do not mechanically apply the v1 `T>28` cut to side-band without side-specific evidence.
4. The 2026-06-07 blocked-ECMWF overlay shows side-band would have reduced losses in weak ECMWF cities, but it remains an overlay on historical fills and still preserves losing combinations.
5. The 2026-06-10 clean test and mechanism attribution both end at `inconclusive`: old side-band shape, low-price YES, BUY_NO mid-high, and forecast-regime filters all fail at least one required gate.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/analysis/2026-06/2026-06-08-performance-side-band-alpha-summary.md` | 2026-06 | side-band alpha summary | snapshot |
| `docs/analysis/2026-06/2026-06-08-side-band-entry-timing-impact.md` | 2026-06 | side-band timing impact | snapshot |
| `docs/archive/analysis/2026-06/2026-06-04-performance-side-band-entry-analysis.md` | 2026-06 | early side-band entry analysis; pre-near-binary caution applies | snapshot |
| `docs/analysis/2026-06/2026-06-07-v1-ecmwf-blocked-side-band-overlay.md` | 2026-06 | side-band overlay on blocked ECMWF cities; improves losses but shadow only | snapshot |
| `docs/analysis/2026-06/2026-06-10-side-band-forecast-regime-v0.md` | 2026-06 | clean full-opportunity side-band + forecast regime test; gates failed | active-evidence |
| `docs/analysis/2026-06/2026-06-10-side-band-mechanism-attribution-v1.md` | 2026-06 | mechanism attribution for side-band, low YES, BUY_NO, forecast regime; gates failed | active-evidence |
| `docs/analysis/2026-06/2026-06-10-weather-strategy-live-test-selection.md` | 2026-06 | synthesis says side-band is not a live-test candidate | active-evidence |

## Required Gates Before Live Use

| Gate | Required Evidence |
|---|---|
| Baseline | Same price bucket and city/date controls; compare to market-structure baseline |
| Significance | Bootstrap or cluster CI by city-day |
| Forward | Date-forward validation for any selected side rule |
| Stress | Top5-removed ROI and worst-day PnL must not invalidate the headline result |
| Execution | If applied live, route through `execution_quality.md` for fill selection and spread cost |

## Open Work

1. Separate side base-rate, market mispricing, and model-driven side selection.
2. Avoid using win-rate alone as a live gate.
3. Keep `side_band_mechanism_shadow_tags_v1` as a tag-only shadow line until a pre-registered rerun passes all gates.
