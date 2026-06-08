# Entry Timing

> Living doc for module [3]: how target-date lead time, forecast checkpoint, and decision window affect accepted plans and live outcomes.
> Current status: `shadow_candidate` for some timing restrictions, not confirmed for broad live automation.
> Last updated: 2026-06-09.

## Current Conclusion

Entry timing appears important, but timing rules must stay pre-registered and forward-tested. Existing snapshots suggest some windows may be weaker or stronger, yet the data contains decision-window missingness and forecast-checkpoint gaps that can create false timing conclusions.

Any live change to T-window or forecast checkpoint must be routed through `WEATHER_CITY_POOL_DECISIONS.md` or the relevant strategy entrypoint after holdout validation.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/analysis/2026-06/2026-06-08-entry-timing-rigorous-research-plan.md` | design | target metric, denominator, matched timing plan | design-draft |
| `docs/analysis/2026-06/2026-06-08-entry-timing-effect-baseline.md` | 2026-06 | L0-L4 timing baseline across candidates, submitted, live_real | snapshot |
| `docs/analysis/2026-06/2026-06-08-city-x-entry-timing-research.md` | 2026-06 | city x timing slice and preliminary weak/strong windows | snapshot |
| `docs/analysis/2026-06/2026-06-08-side-band-entry-timing-impact.md` | 2026-06 | side-band timing impact and limits of applying v1 timing rule | snapshot |

## Required Gates Before Live Use

| Gate | Required Evidence |
|---|---|
| Denominator | State whether the denominator is all candidates, accepted plans, submitted orders, fills, or city-days |
| Matched comparison | Same city/side/price bucket where possible |
| Forward | Date-forward holdout for selected timing rule |
| Data gap | Report decision-window missingness and forecast checkpoint availability |

## Open Work

1. Resolve whether timing effect comes from forecast quality, market liquidity, or selection into fills.
2. Keep timing rules out of live config unless the exact target metric and denominator are fixed first.
