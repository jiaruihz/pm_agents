# Entry Timing

> Living doc for module [3]: how target-date lead time, forecast checkpoint, and decision window affect accepted plans and live outcomes.
> Current status: `shadow_candidate` for some timing restrictions, not confirmed for broad live automation.
> Last updated: 2026-06-09 Phase 4D timing/sizing pilot.

## Current Conclusion

Entry timing appears important, but timing rules must stay pre-registered and forward-tested. Existing snapshots suggest some windows may be weaker or stronger, yet the data contains decision-window missingness and forecast-checkpoint gaps that can create false timing conclusions.

Any live change to T-window or forecast checkpoint must be routed through `WEATHER_CITY_POOL_DECISIONS.md` or the relevant strategy entrypoint after holdout validation.

Phase 4D absorbed the 2026-05 timing reports and 2026-06 timing baseline into the current rule:

1. **Use the 2026-06 gated timing reports as the current evidence family.** The 2026-05 strict-vs-wide and `<T-22` reports are useful hypotheses only; they used older replay/fill mixtures and cannot define current live rules.
2. **`T-22-24` is the current strongest main-window candidate, not a confirmed universal rule.** It looked best in the 2026-06 baseline and v1 timing lineage, but still needs opportunity-layer coverage outside the current decision window and forecast checkpoint materialization.
3. **`T-24-26` is not safe to merge blindly into the main window.** It mixes forecast-update timing, city/side composition, and live fill selection; treat as `shadow_candidate` until forecast checkpoint features are available.
4. **`<T-22` and `>T-28` need different explanations.** `<T-22` lacks full opportunity facts and can be catch-up/dedup/liquidity selected; `>T-28` was weak for v1 after June 1 and has already been handled as a risk filter candidate.
5. **Side-band timing does not simply inherit v1 timing rules.** The side-band snapshot shows timing matters, but bad days and city/side effects remain entangled; use it as side-band-specific evidence.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/analysis/2026-06/2026-06-08-entry-timing-rigorous-research-plan.md` | design | target metric, denominator, matched timing plan | design-draft |
| `docs/analysis/2026-06/2026-06-08-entry-timing-effect-baseline.md` | 2026-06 | L0-L4 timing baseline across candidates, submitted, live_real | snapshot |
| `docs/analysis/2026-06/2026-06-08-city-x-entry-timing-research.md` | 2026-06 | city x timing slice and preliminary weak/strong windows | snapshot |
| `docs/analysis/2026-06/2026-06-08-side-band-entry-timing-impact.md` | 2026-06 | side-band timing impact and limits of applying v1 timing rule | snapshot |
| `docs/analysis/2026-06/2026-06-07-mid-price-core-v1-forecast-timing-degradation-lineage.md` | 2026-06 | v1 post-June timing lineage and forecast/market adverse-move mechanisms | snapshot |
| `docs/analysis/2026-05/2026-05-27-compare-strict-t24-vs-wide-window.md` | 2026-05 | early strict vs 22-28h replay hypothesis | historical-hypothesis |
| `docs/analysis/2026-05/2026-05-29-entry-timing-edge.md` | 2026-05 | early timing edge and `<T-22` fill-rate discussion | historical-hypothesis |

## Absorbed Historical Claims

| Claim | Current handling |
|---|---|
| 2026-05 wide 22-28h replay added candidates with similar ROI | Keep as early hypothesis only; old replay source and current facts supersede it |
| 2026-05 `<T-22` BUY_NO looked strong in filled samples but fill rate was tiny | Treat as selection/liquidity clue; do not restore `<T-22` live from filled rows alone |
| 2026-06 baseline showed `fact_signal_candidates` only covers T-22-24 decision-window opportunities | Timing conclusions outside T-22-24 need raw/signal/order lineage before opportunity-layer claims |
| 2026-06 v1 timing lineage supports `>T-28` risk filtering and `T-26-28` shadow/drop caution | Keep as v1-specific risk evidence; not a global timing law |
| City x timing cells are often small and mixed | Use for review priority and risk labels, not direct city/timing whitelists |
| Forecast checkpoint metadata exists in raw snapshots but is not fully materialized in facts | Required before turning timing bins into forecast-update rules |

## Required Gates Before Live Use

| Gate | Required Evidence |
|---|---|
| Denominator | State whether the denominator is all candidates, accepted plans, submitted orders, fills, or city-days |
| Matched comparison | Same city/side/price bucket where possible |
| Forward | Date-forward holdout for selected timing rule |
| Data gap | Report decision-window missingness and forecast checkpoint availability |
| City-day | Report city-day portfolio impact, not only fill-level ROI |
| Scope | Label rule as global, strategy-instance-specific, side-band-specific, or city-specific |

## Open Work

1. Resolve whether timing effect comes from forecast quality, market liquidity, or selection into fills.
2. Keep timing rules out of live config unless the exact target metric and denominator are fixed first.
3. Materialize forecast checkpoint/run-age features before making `T-24-26` or `<T-22` decisions.
